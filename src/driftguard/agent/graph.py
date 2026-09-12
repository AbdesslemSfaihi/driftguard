"""The DriftGuard agent: search -> judge -> ask -> fix -> verify -> publish, as a LangGraph.

Why a graph library for what is "almost a pipeline": it is not a pipeline.
verify can send work BACK to fix (bounded retries with feedback), judge can
short-circuit to END (no drift), and publish is conditional (all fixes may be
rejected). Encoding that as an explicit state machine makes the control flow
inspectable and extensible — the alternative is a while-loop whose real shape
lives in the programmer's head.

Division of labor stays the project's core rule: nodes are plain functions,
the LLM appears only behind the DriftModel protocol (judge + fixer), and
everything around it — retrieval, verification, git plumbing — is owned,
deterministic code.

Uncertainty is a first-class outcome, not a silent drop: a judge verdict of
"unsure" becomes a question posted to the developer on the merged PR (the ask
node), while clear findings continue to fix/verify/publish unblocked. Answers
arrive later as issue_comment webhooks and are handled by agent/answers.py —
this run simply ends; there is no in-process waiting to lose in a restart.
"""

import json
import logging
from dataclasses import dataclass, field
from typing import TypedDict

from langgraph.graph import END, START, StateGraph

from driftguard.agent.asking import PendingQuestion, format_digest
from driftguard.agent.model import DriftModel
from driftguard.agent.prompts import describe_fact
from driftguard.agent.verify import verify_fix
from driftguard.analysis.extractor import DriftFact
from driftguard.analysis.queries import queries_for_fact
from driftguard.config import Settings
from driftguard.github.client import GitHubClient
from driftguard.indexing.chunker import DocChunk, apply_chunk_edits, chunk_markdown
from driftguard.indexing.embedder import Embedder
from driftguard.indexing.index import HybridIndex, SearchResult
from driftguard.storage import db

log = logging.getLogger("driftguard.agent")

SEARCH_K = 5  # same k the benchmark measures retrieval recall at


@dataclass
class Evidence:
    """One fact and the doc sections retrieval surfaced for it."""

    fact: DriftFact
    results: list[SearchResult]


@dataclass
class Proposal:
    """One doc section to rewrite, with every fact that contradicts it.

    Grouped by section, not by fact: two facts hitting the same section (the
    classic rename + default change) must produce ONE rewrite, or the second
    edit would clobber the first.
    """

    chunk: DocChunk
    facts: list[DriftFact] = field(default_factory=list)
    reasons: list[str] = field(default_factory=list)
    new_text: str = ""
    status: str = "pending"  # pending -> verified | rejected
    feedback: str | None = None
    attempts: int = 0


class AgentState(TypedDict, total=False):
    docs: dict[str, tuple[str, str]]  # doc_path -> (text, blob sha) at head_ref
    evidence: list[Evidence]
    proposals: list[Proposal]
    questions: list[PendingQuestion]
    pr_url: str | None


@dataclass(frozen=True)
class AgentResult:
    status: str  # no_drift | published | proposals_ready | all_rejected | questions_open
    pr_url: str | None
    proposals: list[Proposal]
    questions: list[PendingQuestion] = field(default_factory=list)


def run_agent(
    settings: Settings,
    client: GitHubClient,
    model: DriftModel,
    embedder: Embedder,
    *,
    repo: str,
    pr_number: int,
    head_ref: str,
    facts: list[DriftFact],
    delivery_id: str,
) -> AgentResult:
    graph = _build_graph(
        settings, client, model, embedder, repo, pr_number, head_ref, facts, delivery_id
    )
    state: AgentState = graph.invoke({})

    proposals = state.get("proposals") or []
    questions = state.get("questions") or []
    pr_url = state.get("pr_url")
    if pr_url:
        status = "published"
    elif any(p.status == "verified" for p in proposals):
        status = "proposals_ready"
    elif proposals:
        status = "all_rejected"
    elif questions:
        status = "questions_open"
    else:
        status = "no_drift"
    return AgentResult(status=status, pr_url=pr_url, proposals=proposals, questions=questions)


def _build_graph(
    settings: Settings,
    client: GitHubClient,
    model: DriftModel,
    embedder: Embedder,
    repo: str,
    pr_number: int,
    head_ref: str,
    facts: list[DriftFact],
    delivery_id: str,
):
    def search(_: AgentState) -> AgentState:
        """Fetch the repo's docs at the merge commit, index them, retrieve
        candidate sections per fact. The index is built per delivery: the docs
        being judged must be exactly the docs that exist AFTER the merge."""
        docs: dict[str, tuple[str, str]] = {}
        chunks: list[DocChunk] = []
        for path in client.markdown_paths(repo, head_ref):
            result = client.file_with_sha(repo, path, head_ref)
            if result is None:
                continue
            docs[path] = result
            chunks.extend(chunk_markdown(result[0], path))

        index = HybridIndex(embedder)
        index.build(chunks)

        evidence: list[Evidence] = []
        for fact in facts:
            seen: set[str] = set()
            results: list[SearchResult] = []
            for query in queries_for_fact(fact):
                for hit in index.search(query, k=SEARCH_K):
                    if hit.chunk.chunk_id not in seen:
                        seen.add(hit.chunk.chunk_id)
                        results.append(hit)
            evidence.append(Evidence(fact, results))
        log.info("search: %d docs, %d chunks, %d facts", len(docs), len(chunks), len(facts))
        return {"docs": docs, "evidence": evidence}

    def judge(state: AgentState) -> AgentState:
        by_chunk: dict[str, Proposal] = {}
        unsure: dict[str, PendingQuestion] = {}  # one question per section max
        for item in state["evidence"]:
            if not item.results:
                continue
            candidates = {r.chunk.chunk_id: r.chunk for r in item.results}
            for verdict in model.judge(item.fact, list(candidates.values())):
                if verdict.verdict == "drifted":
                    proposal = by_chunk.setdefault(
                        verdict.chunk_id, Proposal(chunk=candidates[verdict.chunk_id])
                    )
                    proposal.facts.append(item.fact)
                    proposal.reasons.append(verdict.reason)
                elif verdict.verdict == "unsure" and verdict.question.strip():
                    unsure.setdefault(
                        verdict.chunk_id,
                        PendingQuestion(
                            fact=item.fact,
                            chunk=candidates[verdict.chunk_id],
                            question=verdict.question.strip(),
                        ),
                    )
        # A section already being fixed needs no question about itself:
        # another fact proved it drifted, which settles the ambiguity.
        questions = [q for cid, q in unsure.items() if cid not in by_chunk]
        proposals = list(by_chunk.values())
        log.info("judge: %d drifted section(s), %d question(s)", len(proposals), len(questions))
        return {"proposals": proposals, "questions": questions}

    def ask(state: AgentState) -> AgentState:
        """Escalate undecidable judgments to the developer on the merged PR.

        Persist FIRST, comment SECOND: a comment referencing labels that are
        not in the database would make the eventual answers unmatchable, while
        stored-but-unposted questions are merely invisible (and a replay posts
        them). The comment-id guard keeps replays from duplicating the digest.
        """
        questions = state.get("questions") or []
        if not questions:
            return {}
        if len(questions) > settings.max_questions_per_run:
            dropped = questions[settings.max_questions_per_run :]
            log.warning(
                "ask: dropping %d question(s) over the per-run cap of %d: %s",
                len(dropped),
                settings.max_questions_per_run,
                [q.chunk.chunk_id for q in dropped],
            )
            questions = questions[: settings.max_questions_per_run]
        for number, pending in enumerate(questions, start=1):
            pending.label = f"Q{number}"

        db.save_questions(
            settings.database_path,
            delivery_id,
            repo=repo,
            pr_number=pr_number,
            questions=[
                (
                    q.label,
                    json.dumps(vars(q.fact)),
                    q.chunk.doc_path,
                    q.chunk.heading_path,
                    q.question,
                )
                for q in questions
            ],
        )
        if not settings.ask_developer:
            log.info("ASK_DEVELOPER is off — %d question(s) stored only", len(questions))
        elif db.questions_comment_posted(settings.database_path, delivery_id):
            log.info("ask: digest already posted for this delivery — not reposting")
        else:
            digest = format_digest(pr_number, questions, [describe_fact(q.fact) for q in questions])
            comment_id = client.create_issue_comment(repo, pr_number, digest)
            db.set_questions_comment(settings.database_path, delivery_id, comment_id)
            log.info("ask: posted %d question(s) on PR #%s", len(questions), pr_number)
        return {"questions": questions}

    def fix(state: AgentState) -> AgentState:
        for proposal in state["proposals"]:
            if proposal.status != "pending":
                continue
            proposal.new_text = model.propose_fix(
                proposal.chunk, proposal.facts, feedback=proposal.feedback
            )
            proposal.attempts += 1
        return {"proposals": state["proposals"]}

    def verify(state: AgentState) -> AgentState:
        for proposal in state["proposals"]:
            if proposal.status != "pending":
                continue
            feedback = verify_fix(proposal.facts, proposal.chunk.text, proposal.new_text)
            if feedback is None:
                proposal.status = "verified"
            elif proposal.attempts >= settings.max_fix_attempts:
                proposal.status = "rejected"
                proposal.feedback = feedback
                log.warning(
                    "rejected fix for %s (%s attempts): %s",
                    proposal.chunk.chunk_id,
                    proposal.attempts,
                    feedback,
                )
            else:
                proposal.feedback = feedback  # stays pending -> routed back to fix
        return {"proposals": state["proposals"]}

    def publish(state: AgentState) -> AgentState:
        verified = [p for p in state["proposals"] if p.status == "verified"]
        if not settings.open_docs_prs:
            log.info("OPEN_DOCS_PRS is off — %d verified proposal(s) stored only", len(verified))
            return {"pr_url": None}

        branch = f"driftguard/docs-pr-{pr_number}"
        client.create_branch(repo, branch, head_ref)

        by_doc: dict[str, list[Proposal]] = {}
        for proposal in verified:
            by_doc.setdefault(proposal.chunk.doc_path, []).append(proposal)
        for doc_path, doc_proposals in sorted(by_doc.items()):
            text, sha = state["docs"][doc_path]
            new_doc = apply_chunk_edits(text, [(p.chunk, p.new_text) for p in doc_proposals])
            client.put_file(
                repo,
                doc_path,
                message=f"docs: update {doc_path} for changes merged in #{pr_number}",
                text=new_doc,
                branch=branch,
                sha=sha,
            )

        url = client.open_pull_request(
            repo,
            title=f"docs: fix drift introduced by #{pr_number}",
            body=_pr_body(pr_number, verified),
            head=branch,
            base=client.default_branch(repo),
        )
        for proposal in verified:
            proposal.status = "published"
        log.info("opened docs PR: %s", url)
        return {"pr_url": url}

    def after_ask(state: AgentState) -> str:
        # Partial publishing: clear findings proceed regardless of open
        # questions — a slow answer must never block a verified fix.
        return "fix" if state["proposals"] else END

    def after_verify(state: AgentState) -> str:
        if any(p.status == "pending" for p in state["proposals"]):
            return "fix"
        if any(p.status == "verified" for p in state["proposals"]):
            return "publish"
        return END

    graph = StateGraph(AgentState)
    graph.add_node("search", search)
    graph.add_node("judge", judge)
    graph.add_node("ask", ask)
    graph.add_node("fix", fix)
    graph.add_node("verify", verify)
    graph.add_node("publish", publish)
    graph.add_edge(START, "search")
    graph.add_edge("search", "judge")
    graph.add_edge("judge", "ask")
    graph.add_conditional_edges("ask", after_ask, {"fix": "fix", END: END})
    graph.add_edge("fix", "verify")
    graph.add_conditional_edges(
        "verify", after_verify, {"fix": "fix", "publish": "publish", END: END}
    )
    graph.add_edge("publish", END)
    return graph.compile()


def _pr_body(pr_number: int, proposals: list[Proposal]) -> str:
    lines = [
        f"PR #{pr_number} changed code that this documentation still described"
        " the old way. Every edit below is backed by an AST-verified fact and"
        " passed deterministic verification.",
        "",
    ]
    for proposal in proposals:
        lines.append(f"### `{proposal.chunk.doc_path}` — {proposal.chunk.heading_path}")
        for fact, reason in zip(proposal.facts, proposal.reasons, strict=True):
            lines.append(f"- **{fact.kind}** `{fact.symbol}`: {reason}")
        lines.append("")
    lines.append("---")
    lines.append(f"*Opened automatically by DriftGuard after #{pr_number} was merged.*")
    return "\n".join(lines)
