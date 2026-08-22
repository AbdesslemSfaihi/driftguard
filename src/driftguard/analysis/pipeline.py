"""PR-level analysis: changed files in, drift facts out.

Pure orchestration — no HTTP details (the client owns those), no AST details
(the extractor owns those). This is the function the agent graph will call.
"""

import logging

from driftguard.analysis.extractor import DriftFact, extract_drift_facts
from driftguard.github.client import GitHubClient

log = logging.getLogger("driftguard.analysis")


def analyze_pr(
    client: GitHubClient,
    *,
    repo: str,
    number: int,
    base_ref: str,
    head_ref: str,
) -> list[DriftFact]:
    """Extract drift facts from every relevant Python file the PR changed.

    base_ref is the commit the PR started from (the "old truth"); head_ref is
    the merge result (the "new truth"). Comparing full files at two commits is
    simpler and more reliable than parsing unified diffs — AST needs complete,
    valid source, which a patch fragment is not.
    """
    facts: list[DriftFact] = []
    for changed in client.pr_changed_files(repo, number):
        if not _is_relevant(changed.path, changed.status):
            continue

        old_path = changed.previous_path or changed.path
        old_source = client.file_content(repo, old_path, base_ref) or ""
        new_source = (
            ""
            if changed.status == "removed"
            else client.file_content(repo, changed.path, head_ref) or ""
        )
        file_facts = extract_drift_facts(old_source, new_source, changed.path)
        if file_facts:
            log.info("%s: %d drift fact(s)", changed.path, len(file_facts))
        facts.extend(file_facts)
    return facts


def _is_relevant(path: str, status: str) -> bool:
    if not path.endswith(".py"):
        return False
    # Brand-new code cannot contradict existing docs (v1); missing-docs
    # detection is explicitly out of the validated MVP perimeter.
    if status == "added":
        return False
    # Test code is not documented user-facing API.
    parts = path.replace("\\", "/").split("/")
    filename = parts[-1]
    in_test_dir = any(part in {"tests", "test"} for part in parts[:-1])
    return not (in_test_dir or filename.startswith("test_") or filename.endswith("_test.py"))
