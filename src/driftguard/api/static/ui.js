/* DriftGuard story page — renders the JSON the API serves, nothing more.
   Two pages share this file: the deliveries list and one delivery's story.
   Both poll; a card animates in the first time it appears and stays put on
   later refreshes, so a live run reads as a timeline filling in. */
(() => {
  "use strict";

  const page = document.body.dataset.page;
  const $ = (selector) => document.querySelector(selector);

  const esc = (value) =>
    String(value ?? "").replace(/[&<>"']/g, (c) => (
      { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]
    ));

  // SQLite stamps are UTC without a zone ("2026-09-12 16:20:16"); GitHub's are ISO.
  const when = (stamp) => {
    if (!stamp) return "";
    const iso = stamp.includes("T") ? stamp : stamp.replace(" ", "T") + "Z";
    return new Date(iso).toLocaleString(undefined, { dateStyle: "medium", timeStyle: "short" });
  };

  const STAGES = [
    ["webhook", "Webhook"], ["facts", "Facts"], ["search", "Search"],
    ["judge", "Judge"], ["fix", "Fix & verify"], ["result", "Result"],
  ];
  const STATUS_LABEL = {
    received: "received", logged: "archived only (no token)", facts_extracted: "facts extracted",
    searching: "searching the docs", judging: "judging each section", asking: "asking the developer",
    fixing: "writing the fix", verifying: "verifying the fix", publishing: "opening the docs PR",
    no_drift: "no drift", docs_pr_opened: "docs PR opened", proposals_ready: "proposals stored",
    all_rejected: "all fixes rejected", questions_open: "waiting for an answer",
    answers_processed: "answer processed", no_answers_matched: "no matching answer",
    error: "GitHub error", agent_error: "agent error",
  };
  const VERDICT_ORDER = { drifted: 0, unsure: 1, historical: 2, accurate: 3 };

  const chip = (kind, text) => `<span class="chip ${esc(kind)}">${esc(text)}</span>`;
  const statusChip = (status) => {
    let cls = "acc";
    if (status === "docs_pr_opened" || status === "answers_processed") cls = "ok";
    else if (status === "no_drift" || status === "logged") cls = "mut";
    else if (status.includes("error") || status === "all_rejected") cls = "drift";
    else if (status === "questions_open") cls = "unsure";
    return chip(cls, STATUS_LABEL[status] || status);
  };
  const ranks = (row) =>
    (row.dense_rank ? `<span class="rank d" title="embedding search, rank ${row.dense_rank}">D${row.dense_rank}</span>` : "") +
    (row.sparse_rank ? `<span class="rank b" title="exact-word search, rank ${row.sparse_rank}">B${row.sparse_rank}</span>` : "");

  const describeFact = (f) => {
    const dot = f.symbol.lastIndexOf(".");
    const owner = dot >= 0 ? f.symbol.slice(0, dot) : f.symbol;
    const param = dot >= 0 ? f.symbol.slice(dot + 1) : f.symbol;
    switch (f.kind) {
      case "renamed":
        return `Parameter <code>${esc(f.old)}</code> of <code>${esc(owner)}()</code> was renamed to <code>${esc(f.new)}</code>. Docs still using the old name are wrong.`;
      case "default_changed":
        return `The default value of <code>${esc(param)}</code> in <code>${esc(owner)}()</code> changed. Docs quoting the old default are wrong.`;
      case "constant_changed":
        return `The module constant <code>${esc(f.symbol)}</code> changed value.`;
      case "removed":
        return `<code>${esc(f.old)}</code> was removed — it no longer exists. Docs presenting it as available API are wrong.`;
      default:
        return `The signature of <code>${esc(f.symbol)}()</code> changed.`;
    }
  };

  // ---- animation bookkeeping: new cards rise in, known cards stay still ----
  const seen = new Set();
  let order = 0;
  const block = (key, cls, html) => {
    const fresh = !seen.has(key);
    seen.add(key);
    return `<section class="card ${cls} ${fresh ? "fresh" : "settled"}" style="--i:${order++}">${html}</section>`;
  };
  const step = (n, title, subtitle, body, key) =>
    block(key, "step", `<div class="step-head"><span class="num">${n}</span><div><h2>${esc(title)}</h2><p class="sub">${esc(subtitle)}</p></div></div>${body}`);

  const stageClasses = (stage) => STAGES.map((_, i) => {
    if (stage.state === "error" && i === stage.index) return "error";
    if (i < stage.index || (i === stage.index && stage.state === "done")) return "done";
    if (i === stage.index) return "active";
    return "pending";
  });
  const stepper = (stage) => {
    const classes = stageClasses(stage);
    return `<div class="stepper">${STAGES.map(([, label], i) => {
      const link = i === 0 ? "" : `<span class="link ${classes[i] === "done" ? "done" : classes[i] === "active" ? "active" : ""}"></span>`;
      return `${link}<span class="stage ${classes[i]}"><span class="dot"></span><span class="label">${label}</span></span>`;
    }).join("")}</div>`;
  };
  const miniStepper = (stage) => `<span class="mini">${stageClasses(stage).map((c) => `<i class="${c}"></i>`).join("")}</span>`;

  const placeholder = (stage, index, workingText) => {
    if (stage.state === "error" && stage.index <= index) return `<p class="empty">Stopped by an error before this step. The delivery can be replayed.</p>`;
    if (stage.index < index) return `<p class="pending">Not reached yet.</p>`;
    if (stage.index === index && stage.state === "running") return `<p class="working"><span class="spin"></span> ${esc(workingText)}</p>`;
    return `<p class="empty">Nothing recorded for this step.</p>`;
  };

  const setLive = (cls, text) => {
    const live = $("#live");
    live.className = `live ${cls}`;
    live.innerHTML = `<span class="dot"></span> ${esc(text)}`;
  };

  // ---------------------------------------------------------------- story
  const factCard = (f) => `
    <div class="fact">
      <div class="fact-head">${chip("kind", f.kind.replace("_", " "))}<code class="sym">${esc(f.symbol)}</code>
        ${f.file_url ? `<a class="file" href="${esc(f.file_url)}" target="_blank" rel="noopener">${esc(f.file)} ↗</a>` : `<span class="file">${esc(f.file)}</span>`}
      </div>
      <p class="fact-text">${describeFact(f)}</p>
      ${f.old != null || f.new != null ? `<div class="change"><code class="old">${esc(f.old ?? "—")}</code><span class="arrow">→</span><code class="new">${esc(f.new ?? "—")}</code></div>` : ""}
    </div>`;

  const verdictRow = (v) => `
    <div class="row v-${esc(v.verdict)}">${chip(v.verdict, v.verdict)}
      <div class="vbody"><div><span class="doc">${esc(v.doc_path)}</span> <span class="sep">›</span> <span class="head">${esc(v.heading)}</span></div>
        <p class="reason">${esc(v.reason)}</p>${v.question ? `<p class="q">❓ ${esc(v.question)}</p>` : ""}</div>
      <span class="ranks">${ranks(v)}</span>
    </div>`;

  const diffBlock = (diff) => `<pre class="diff">${diff.map((l) =>
    `<span class="${l.type}">${l.type === "add" ? "+" : l.type === "del" ? "−" : " "} ${esc(l.text)}</span>`).join("\n")}</pre>`;

  const proposalCard = (p) => `
    <div class="proposal ${esc(p.status)}">
      <div class="p-head"><span class="doc">${esc(p.doc_path)}</span><span class="sep">›</span><span class="head">${esc(p.heading)}</span>
        ${chip(p.status === "rejected" || p.status === "stale" ? "drift" : "ok", p.status)}
        <span class="mut">${p.attempts} attempt${p.attempts === 1 ? "" : "s"}</span></div>
      ${p.reasons.length ? `<ul class="reasons">${p.reasons.map((r) => `<li>${esc(r)}</li>`).join("")}</ul>` : ""}
      ${p.feedback ? `<p class="feedback">Verifier: ${esc(p.feedback)}</p>` : ""}
      ${p.status === "stale" ? `<p class="feedback">The section no longer exists in today's docs — nothing was changed.</p>` : diffBlock(p.diff)}
    </div>`;

  const questionCard = (q) => {
    const cls = q.status === "open" ? "unsure" : q.status === "resolved_fixed" ? "ok" : q.status === "resolved_accurate" ? "acc" : "drift";
    return `
    <div class="question">
      <div class="p-head"><strong>${esc(q.label)}</strong><span class="doc">${esc(q.doc_path)}</span><span class="sep">›</span><span class="head">${esc(q.heading)}</span>${chip(cls, q.status.replace("_", " "))}</div>
      <div class="bubble bot"><span class="who">DriftGuard asked</span>${esc(q.question)}${q.comment_url ? ` <a href="${esc(q.comment_url)}" target="_blank" rel="noopener">view on GitHub ↗</a>` : ""}</div>
      ${q.answer
        ? `<div class="bubble human"><span class="who">@${esc(q.answered_by)} answered · ${esc(when(q.answered_at))}</span>${esc(q.answer)}</div>`
        : `<div class="bubble wait"><span class="who">waiting</span>Reply on the PR with <code>${esc(q.label)}: …</code></div>`}
    </div>`;
  };

  const followupCard = (f) => `
    <div class="followup">
      <div class="p-head"><strong>Answer received</strong><span class="mut">${esc(when(f.received_at))}</span>${statusChip(f.status)}
        ${f.docs_pr_url ? `<a class="btn ghost" href="${esc(f.docs_pr_url)}" target="_blank" rel="noopener">follow-up docs PR ↗</a>` : ""}</div>
      ${f.comment.body ? `<div class="bubble human"><span class="who">@${esc(f.comment.author)}</span>${esc(f.comment.body)}</div>` : ""}
      ${f.proposals.map(proposalCard).join("")}
    </div>`;

  function renderStory(s) {
    const { delivery: d, pull_request: pr, stage: st } = s;
    order = 0;
    setLive(st.final ? "done" : "on", st.final ? `finished · ${STATUS_LABEL[st.status] || st.status}` : `live · ${STATUS_LABEL[st.status] || st.status}`);
    document.title = `DriftGuard · ${d.repo}#${d.pr_number}`;

    const blocks = [];
    blocks.push(block("hero", "hero", `
      <div class="hero-top"><div>
        <div class="crumbs"><a href="/ui">deliveries</a> / ${esc(d.repo)} <span class="mut">#${d.pr_number}</span></div>
        <h1>${esc(pr.title || `Pull request #${d.pr_number}`)}</h1>
        <div class="meta">${pr.avatar_url ? `<img class="avatar" src="${esc(pr.avatar_url)}" alt="">` : ""}${pr.author ? `merged by <strong>@${esc(pr.author)}</strong> · ` : ""}received ${esc(when(d.received_at))}
          ${pr.html_url ? ` · <a href="${esc(pr.html_url)}" target="_blank" rel="noopener">pull request ↗</a>` : ""}${pr.compare_url ? ` · <a href="${esc(pr.compare_url)}" target="_blank" rel="noopener">code diff ↗</a>` : ""}</div>
      </div><div class="hero-status">${statusChip(d.status)}</div></div>${stepper(st)}`));

    // 1 — the change
    let factsBody;
    if (s.facts.length) factsBody = s.facts.map(factCard).join("");
    else if (st.status === "logged") factsBody = `<p class="empty">Archived only: GITHUB_TOKEN is not set, so the code was never fetched.</p>`;
    else if (st.index >= 1 && st.state !== "running") factsBody = `<p class="empty">No documentable change: only function bodies, tests or docs moved. Nothing to do.</p>`;
    else factsBody = `<p class="working"><span class="spin"></span> Comparing each changed Python file before and after the merge…</p>`;
    blocks.push(step(1, "The change", "What the AST comparison proved changed — never guessed by a model.", factsBody, "facts"));

    if (s.facts.length === 0 && st.final) {
      blocks.push(block("stop", "step", `<p class="empty">The pipeline stopped here: no facts, no documentation to check.</p>`));
      $("#story").innerHTML = blocks.join("");
      return;
    }

    // 2 — search
    const byFact = new Map();
    s.verdicts.forEach((v) => { if (!byFact.has(v.fact)) byFact.set(v.fact, []); byFact.get(v.fact).push(v); });
    const searchBody = s.verdicts.length
      ? [...byFact].map(([fact, rows]) => `
          <div class="group"><div class="group-head"><code>${esc(fact)}</code><span class="mut">${rows.length} candidate section${rows.length === 1 ? "" : "s"}</span></div>
          ${rows.map((v) => `<div class="row"><span class="doc">${esc(v.doc_path)}</span><span class="sep">›</span><span class="head">${esc(v.heading)}</span><span class="ranks">${ranks(v)}</span></div>`).join("")}</div>`).join("")
        + `<p class="legend"><span><span class="rank d">D</span> found by the embedding search (meaning)</span><span><span class="rank b">B</span> found by the exact-word search (BM25)</span></p>`
      : placeholder(st, 2, "Indexing the documentation at the merge commit and searching it for each fact…");
    blocks.push(step(2, "Search", "Docs fetched at the merge commit, hybrid retrieval per fact — this is the ceiling: what search misses, the judge never sees.", searchBody, "search"));

    // 3 — judge
    const sorted = [...s.verdicts].sort((a, b) => (VERDICT_ORDER[a.verdict] ?? 9) - (VERDICT_ORDER[b.verdict] ?? 9));
    const flagged = sorted.filter((v) => v.verdict !== "accurate");
    const quiet = sorted.filter((v) => v.verdict === "accurate");
    const judgeBody = s.verdicts.length
      ? flagged.map(verdictRow).join("")
        + (flagged.length ? "" : `<p class="empty">Every candidate section is still accurate.</p>`)
        + (quiet.length ? `<details class="quiet"><summary>${quiet.length} section${quiet.length === 1 ? "" : "s"} left alone — judged accurate</summary>${quiet.map(verdictRow).join("")}</details>` : "")
      : placeholder(st, 3, "Asking the judge what each fact means for each candidate section…");
    blocks.push(step(3, "Judge", "One verdict per section: drifted, accurate, historical (never edited), or unsure (becomes a question).", judgeBody, "judge"));

    // 4 — fix & verify
    const fixBody = s.proposals.length
      ? s.proposals.map(proposalCard).join("")
      : st.index >= 4 || st.final ? `<p class="empty">Nothing to rewrite${s.questions.length ? " — only questions came out of the judgment" : ""}.</p>` : placeholder(st, 4, "Rewriting the drifted sections and verifying each rewrite with code…");
    blocks.push(step(4, "Fix & verify", "The model rewrites; deterministic checks accept or reject. A fix that never passes is dropped, not published.", fixBody, "fix"));

    // 5 — result
    const resultParts = [];
    if (d.docs_pr_url) resultParts.push(`<div class="actions"><a class="btn" href="${esc(d.docs_pr_url)}" target="_blank" rel="noopener">docs pull request ↗</a><span class="mut">opened for human review — DriftGuard never merges.</span></div>`);
    if (s.questions.length) resultParts.push(s.questions.map(questionCard).join(""));
    if (s.followups.length) resultParts.push(s.followups.map(followupCard).join(""));
    const resultBody = resultParts.length ? resultParts.join("") : placeholder(st, 5, "Opening the docs pull request…");
    blocks.push(step(5, "Result", "Verified fixes become a PR; undecidable sections become questions; answers become follow-up PRs.", resultBody, "result"));

    $("#story").innerHTML = blocks.join("");
  }

  // ----------------------------------------------------------------- list
  function renderList(items) {
    order = 0;
    setLive("on", `watching · ${items.length} deliver${items.length === 1 ? "y" : "ies"}`);
    $("#list").innerHTML = items.length
      ? items.map((it) => block(`d:${it.delivery_id}`, "row-card", `
          <a class="row-link" href="/ui/deliveries/${encodeURIComponent(it.story_id)}">
            <span class="icon">${it.event === "issue_comment" ? "💬" : "⇄"}</span>
            <span><span class="title">${esc(it.title || "(untitled)")}</span>
              <span class="meta">${esc(it.repo)}#${it.pr_number} · ${it.event === "issue_comment" ? "answer from" : "merged by"} @${esc(it.author || "?")} · ${esc(when(it.received_at))}</span></span>
            ${miniStepper(it.stage)}
            ${statusChip(it.status)}
          </a>`)).join("")
      : `<p class="empty">No deliveries yet. Merge a pull request on a repository whose webhook points here.</p>`;
  }

  // ------------------------------------------------------------- polling
  let lastSignature = "";
  async function tick(url, render, fastMs, slowMs) {
    let delay = fastMs;
    try {
      const res = await fetch(url, { cache: "no-store" });
      if (res.status === 404) {
        setLive("err", "unknown delivery");
        $("#story").innerHTML = `<section class="card"><p class="empty">No delivery with this id. <a href="/ui">Back to the list.</a></p></section>`;
        return;
      }
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      const data = await res.json();
      const signature = JSON.stringify(data);
      if (signature !== lastSignature) {
        lastSignature = signature;
        render(data);
      }
      if (!Array.isArray(data) && data.stage.final) delay = slowMs;
    } catch (err) {
      setLive("err", "connection lost — retrying");
      delay = 4000;
    }
    setTimeout(() => tick(url, render, fastMs, slowMs), delay);
  }

  if (page === "story") {
    const id = decodeURIComponent(location.pathname.split("/").filter(Boolean).pop());
    tick(`/ui/api/deliveries/${encodeURIComponent(id)}`, renderStory, 2500, 15000);
  } else {
    tick("/ui/api/deliveries", renderList, 4000, 4000);
  }
})();
