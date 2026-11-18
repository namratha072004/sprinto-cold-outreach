// Review desk. Shows only what is saved in output/<domain>/state.json. No sample data anywhere.
const $ = s => document.querySelector(s);
const esc = s => String(s ?? "").replace(/[&<>"']/g, c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const link = (u, t) => /^https?:\/\//i.test(u || "") ? `<a href="${esc(u)}" target="_blank" rel="noopener noreferrer">${esc(t || u)}</a>` : esc(u || "");
const DIM = { business_fit: "SaaS / business fit", customer_trust: "Customer trust", compliance_workload: "Compliance workload",
              reason_now: "Reason to engage now", delivery_complexity: "Delivery complexity" };
const STATE = { positive: "evidence found", missing: "no evidence found", explicit_negative: "evidence says no" };
const PAGE_RESULT = { ok: "Read", blocked: "Blocked (not a poor-fit signal)", failed: "Could not read" };
const GATES = [["saas", "SaaS"], ["series", "Series A–C"], ["compliance", "Compliance workload"], ["score", null]];
const GATE_WORD = { passed: ["✓", "Passed"], failed: ["✕", "Failed"], unknown: ["?", "Unknown"] };

let CFG = {}, selected = null, editing = false, sig = "";

async function api(path, body) {
  const r = await fetch(path, body === undefined ? {} : { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) });
  let data = null; try { data = await r.json(); } catch (e) {}
  return { ok: r.ok, status: r.status, data };
}

// ------------------------------------------------------------------ top bar and list
async function refreshStatus() {
  const { data } = await api("/api/status");
  if (!data) return;
  const h = $("#pill-hunter");
  h.textContent = data.hunter_remaining == null ? "Hunter credits: unknown" : `Hunter credits left: ${data.hunter_remaining}`;
  h.className = "pill" + (data.hunter_remaining != null && data.hunter_remaining < 5 ? " warning" : "");
  const m = $("#pill-mail");
  if (!data.gemini_key_set) { m.textContent = "Gemini key missing in .env: cannot run"; m.className = "pill danger"; }
  else if (data.mail.ready) { m.textContent = "Gmail ready: sends only to the demo address"; m.className = "pill success"; }
  else { m.textContent = "Gmail not set up: approvals are saved, not sent"; m.className = "pill warning"; }
  const r = data.run;
  $("#runline").textContent = r.active ? `Working on ${r.current || "…"}${r.queued.length ? ` · ${r.queued.length} waiting` : ""}` : "";
}

async function refreshList() {
  const { data } = await api("/api/companies");
  const el = $("#list");
  if (!data || !data.length) {
    el.innerHTML = `<div class="empty">No companies yet. Paste domains above and press Run, or load domains.txt.</div>`;
    return data || [];
  }
  el.innerHTML = data.map(c => `
    <button class="item ${c.domain === selected ? "sel" : ""}" data-d="${esc(c.domain)}" role="listitem">
      <div class="top"><span>${esc(c.name)}</span><span>${c.score == null ? "–" : c.score + "/100"}</span></div>
      <div class="dom">${esc(c.domain)}</div>
      <span class="badge tone-${esc(c.tone)}">${esc(c.status)}</span>
    </button>`).join("");
  el.querySelectorAll(".item").forEach(b => b.onclick = () => select(b.dataset.d));
  return data;
}

// ------------------------------------------------------------------ one company
async function select(domain) {
  selected = domain; editing = false; sig = "";
  await refreshList();
  await showDetail(true);
}

async function showDetail(force) {
  if (!selected) { $("#detail").innerHTML = `<div class="empty">Select a company on the left.</div>`; return; }
  const { ok, data: st } = await api(`/api/company/${encodeURIComponent(selected)}`);
  if (!ok) { $("#detail").innerHTML = `<div class="empty">No saved result for ${esc(selected)}.</div>`; return; }
  const newSig = st.view.key + "|" + (st.stage || "") + "|" + ((st.review || {}).send_status || "") + "|" + ((st.review || {}).contact_correct);
  if (!force && (newSig === sig || editing)) return;
  sig = newSig;
  render(st);
}

function render(st) {
  const a = st.assessment, c = st.contact, rv = st.review || {};
  const name = (a && a.company_name) || st.domain;
  const stage = a ? (a.funding.stage === "unknown" ? "Funding stage: unknown" : "Funding: " + a.funding.stage.replace(/\b\w/g, m => m.toUpperCase())) : "";
  const total = "total" in st ? Math.round(st.total) : null;
  let h = `<div class="head"><div><h2>${esc(name)}</h2>
    <div class="meta">${link("https://" + st.domain, st.domain)}${stage ? " · " + esc(stage) : ""}${(st.flags || []).includes("funding_stage_unknown") ? " · flagged for you" : ""}</div></div>`;
  if (total != null) h += `<div class="score"><span class="num">${total}</span><span class="of"> / 100</span><div class="bar"><i style="width:${total}%"></i></div></div>`;
  h += `</div>`;

  h += `<div class="banner tone-${esc(st.view.tone)}"><b>${esc(st.view.label)}</b>${st.status === "DRAFT_READY" && !rv.decision ? "" : esc(st.escalation || st.reason || "")}</div>`;

  if (st.gates) {
    h += `<div class="chips" aria-label="Gates">` + GATES.map(([k, label]) => {
      const g = st.gates[k]; if (!g) return "";
      const [sym, word] = GATE_WORD[g.state];
      return `<span class="chip ${g.state}" title="${esc(g.detail)}"><b>${sym}</b>${esc(label || "Score ≥ " + CFG.threshold)}: ${word}</span>`;
    }).join("") + `</div><div class="muted small">Hover a chip for the detail. All four must pass; "unknown" means the evidence was not found, not that the answer is no.</div>`;
  }

  if (c) {
    const fb = rv.contact_correct;
    h += `<div class="card"><h3>Why this person</h3><div class="who"><div>${esc(c.why)}
      <div class="muted small">Contact email found by Hunter (research only, never emailed): ${esc(c.email)}${c.email_verified ? " · Hunter marks it valid" : ""}</div></div>
      <div class="small">Right person? <button class="btn small" data-fb="1" ${fb === true ? "disabled" : ""}>Yes</button> <button class="btn small" data-fb="0" ${fb === false ? "disabled" : ""}>No</button></div></div></div>`;
  }

  if (a) h += trail(st);

  if (st.status === "DRAFT_READY" && !rv.decision) {
    h += draftPanel(st);
  } else if (rv.decision) {
    h += resultPanel(st);
  } else if (st.finish_text) {
    h += `<div class="card muted">${esc(st.finish_text)}</div>`;
  }
  if (st.status === "CONTACT_LOOKUP_PAUSED") h += `<div class="actions"><button class="btn" id="btn-resume">Retry contact lookup</button></div>`;

  $("#detail").innerHTML = h;
  wire(st);
}

// ------------------------------------------------------------------ decision trail
function evidenceHtml(list) {
  return (list || []).map(e => `<blockquote>“${esc(e.excerpt)}”<span class="src">${link(e.source_url)}${e.dated ? " · dated " + esc(e.dated) : ""}</span></blockquote>`).join("");
}

function trail(st) {
  const a = st.assessment, pts = st.points || {};
  let h = `<details><summary>Decision trail: why it was judged this way (evidence, sources, every round)</summary><div class="inner">`;
  h += `<details open><summary>1. Why it qualified (or not): evidence per question</summary><div class="inner">` +
    Object.keys(DIM).map(k => { const d = a.dimensions[k];
      return `<div class="dim"><div class="t"><span>${DIM[k]}</span><span>${pts[k] ?? 0} / ${CFG.weights[k]} pts · level ${d.level} · ${STATE[d.state] || d.state}</span></div>
        ${d.reasoning ? `<div class="muted small">AI's reasoning: ${esc(d.reasoning)}</div>` : ""}${evidenceHtml(d.evidence)}</div>`; }).join("") +
    `<div class="dim"><div class="t"><span>Funding stage</span><span>${esc(a.funding.stage.replace(/\b\w/g, m => m.toUpperCase()))}${a.funding.date ? " · " + esc(a.funding.date) : ""}</span></div>${a.funding.evidence ? evidenceHtml([{ ...a.funding.evidence, dated: a.funding.date }]) : `<div class="muted small">No dated source names the funding round.</div>`}</div>` +
    (a.employee_count && a.employee_count.value ? `<div class="dim"><div class="t"><span>Employees (information only)</span><span>${esc(a.employee_count.value)}</span></div>${evidenceHtml([a.employee_count.evidence])}</div>` : "") +
    `<div class="muted small">Points are added up by code, not by the AI. Employee count, industry and cloud stack are information only.</div></div></details>`;

  h += `<details><summary>2. Research log: what was read, round by round</summary><div class="inner">` +
    (st.rounds || []).map(r => `<div class="dim"><div class="t"><span>Round ${r.round}</span><span>Decision after: ${esc(r.revised_decision)}</span></div>
      ${r.gap && r.gap.length ? `<div class="small"><b>Gap:</b> ${r.gap.map(g => esc(g.gap) + (g.page_type ? ` (look on ${esc(g.page_type)} page)` : "")).join("; ")}</div>` : ""}
      <div class="small"><b>Source:</b> ${(r.source || []).map(s => link(s)).join(", ") || "none"}</div>
      <div class="small"><b>New evidence:</b> ${(r.new_evidence || []).length ? "" : "none"}</div>${evidenceHtml(r.new_evidence)}${r.note ? `<div class="muted small">${esc(r.note)}</div>` : ""}</div>`).join("") +
    `<table><tr><th>Page</th><th>Result</th><th>Tries</th></tr>${(st.pages || []).map(p => `<tr><td>${link(p.url)}${p.guessed_path ? " (usual path, not linked)" : ""}</td><td>${esc(PAGE_RESULT[p.status] || p.status)}${p.from_cache ? " · cached" : ""}</td><td>${p.attempts}</td></tr>`).join("")}</table>` +
    ((a.verification_notes || []).length ? `<div class="small"><b>Claims the checker removed:</b><ul>${a.verification_notes.map(n => `<li>${esc(n)}</li>`).join("")}</ul></div>` : "") + `</div></details>`;

  if (st.contact_scores) {
    h += `<details><summary>3. Contact scores (out of 10, pass at ${CFG.contact_pass} with company link and role ownership above 0)</summary><div class="inner"><table>
      <tr><th>Person</th><th>Role</th><th>Company link</th><th>Owns function</th><th>Drives action</th><th>Fits need</th><th>Identity</th><th>Total</th></tr>` +
      st.contact_scores.map(x => `<tr><td>${esc(x.name)}${st.contact && st.contact.name === x.name ? " ← chosen" : ""}</td><td>${esc(x.position || "no title")}</td>
        <td>${x.scores.company_association}</td><td>${x.scores.functional_ownership}</td><td>${x.scores.ability_to_drive_action}</td><td>${x.scores.match_to_need}</td><td>${x.scores.identity_quality}</td><td><b>${x.total}</b> ${x.pass ? "✓" : ""}</td></tr>`).join("") +
      `</table><div class="muted small">Scores are the AI's, checked and added up by code. Role order: compliance/GRC, then security, then CTO/engineering, then founder.</div></div></details>`;
  }
  if (st.pain_points) {
    h += `<details><summary>4. Pain points and the hook</summary><div class="inner">` + st.pain_points.map((p, i) => `<div class="dim"><div class="t"><span>${i === 0 ? "Used in the email" : "Backup"}: ${esc(p.hypothesis)}</span><span>${p.total}/10</span></div>
      <div class="small">Fact it rests on: ${esc(p.observed_fact)} · Sprinto capability: ${esc(p.capability)}</div>${evidenceHtml([p.evidence])}</div>`).join("") + `</div></details>`;
  }
  if (st.email_attempts && st.email_attempts.length) {
    h += `<details><summary>5. Email checks</summary><div class="inner">` + st.email_attempts.map(x => x.error ? `<div class="dim">Attempt ${x.revision + 1}: ${esc(x.error)}</div>` :
      `<div class="dim"><div class="t"><span>Attempt ${x.revision + 1}${x.passed ? " ✓ passed" : " ✕ revised"}</span><span>${x.qa_total}/10 · ${x.words} words</span></div>
      ${x.code_problems.length ? `<div class="small">Code checks flagged: ${x.code_problems.map(esc).join("; ")}</div>` : ""}${x.feedback ? `<div class="muted small">Reviewer note: ${esc(x.feedback)}</div>` : ""}</div>`).join("") + `</div></details>`;
  }
  return h + `</div></details>`;
}

// ------------------------------------------------------------------ draft, decision
function draftPanel(st) {
  const d = st.draft, m = CFG.mail || {};
  return `<div class="card draft" id="draft"><h3>Draft email</h3>
    <label for="subj">Subject</label><input type="text" id="subj" value="${esc(d.subject)}" readonly>
    <label for="body">Email</label><textarea id="body" readonly>${esc(d.body)}</textarea>
    <div class="wc" id="wc"></div>
    ${m.ready ? "" : `<div class="muted small" style="margin-top:8px">Gmail is not set up, so Approve will save your approval but not send. Nothing goes to a prospect either way.</div>`}
    <div class="actions" id="act-view"><button class="btn primary" id="btn-approve">Approve and send to demo address</button><button class="btn" id="btn-edit">Edit</button><button class="btn danger" id="btn-reject">Reject</button></div>
    <div class="actions" id="act-edit" hidden><button class="btn primary" id="btn-save">Save edit</button><button class="btn" id="btn-cancel">Cancel</button></div></div>`;
}

function resultPanel(st) {
  const rv = st.review;
  if (rv.decision === "rejected") return `<div class="card"><h3>Rejected</h3>You rejected this draft${rv.reject_reason ? ": " + esc(rv.reject_reason) : ""}.<div class="muted small">Nothing was sent.</div></div>`;
  const sent = rv.send_status === "SENT";
  return `<div class="card"><h3>${sent ? "Sent to the demo address" : "Approved, not sent"}</h3>${sent ? `Sent ${esc(rv.sent_at || "")}. Only the demo address received it.` : esc(rv.send_error || "Not sent yet.")}
    ${rv.edited ? `<div class="muted small">You edited the draft before approving (kept ${Math.round((rv.edit_similarity || 0) * 100)}% of the original).</div>` : ""}
    ${sent ? "" : `<div class="actions"><button class="btn primary" id="btn-retry">Try sending again</button></div>`}
    <label class="small muted">Approved text</label><blockquote><b>${esc(rv.final_subject)}</b><br>${esc(rv.final_body).replace(/\n/g, "<br>")}</blockquote></div>`;
}

function wordCount(t) { return (t.trim().match(/\S+/g) || []).length; }

function wire(st) {
  const fb = document.querySelectorAll("[data-fb]");
  fb.forEach(b => b.onclick = async () => { await api(`/api/company/${st.domain}/contact-feedback`, { correct: b.dataset.fb === "1" }); await showDetail(true); });
  const rs = $("#btn-resume"); if (rs) rs.onclick = async () => { const r = await api(`/api/company/${st.domain}/resume`, {}); if (!r.ok) alert(r.data.error); };
  const rt = $("#btn-retry"); if (rt) rt.onclick = async () => { rt.disabled = true; await api(`/api/company/${st.domain}/retry-send`, {}); await showDetail(true); refreshList(); };
  const dr = $("#draft"); if (!dr) return;
  const subj = $("#subj"), body = $("#body"), wc = $("#wc");
  const upd = () => { const n = wordCount(body.value); wc.textContent = `${n} words (target ${CFG.words[0]}–${CFG.words[1]})`; wc.className = "wc" + (n < CFG.words[0] - 10 || n > CFG.words[1] + 10 ? " off" : ""); };
  upd(); body.oninput = upd;
  const setEdit = on => { editing = on; subj.readOnly = body.readOnly = !on; dr.classList.toggle("editing", on); $("#act-view").hidden = on; $("#act-edit").hidden = !on; if (on) body.focus(); };
  $("#btn-edit").onclick = () => setEdit(true);
  $("#btn-cancel").onclick = () => { subj.value = st.draft.subject; body.value = st.draft.body; upd(); setEdit(false); };
  $("#btn-save").onclick = async () => { const r = await api(`/api/company/${st.domain}/edit`, { subject: subj.value, body: body.value }); if (!r.ok) return alert(r.data.error || "Could not save"); editing = false; await showDetail(true); };
  $("#btn-approve").onclick = async e => { e.target.disabled = true; e.target.textContent = "Sending…";
    const r = await api(`/api/company/${st.domain}/approve`, { subject: subj.value, body: body.value });
    if (!r.ok) alert((r.data && r.data.error) || "Could not approve"); editing = false; await showDetail(true); refreshList(); };
  $("#btn-reject").onclick = async () => { const why = prompt("Why are you rejecting this draft? (optional)"); if (why === null) return;
    await api(`/api/company/${st.domain}/reject`, { subject: subj.value, body: body.value, reason: why }); editing = false; await showDetail(true); refreshList(); };
}

// ------------------------------------------------------------------ run, scorecard
async function run(fromFile, rerun) {
  const body = fromFile ? { from_file: true, rerun } : { domains: $("#domains").value, rerun };
  const r = await api("/api/run", body);
  if (r.status === 409 && confirm(`${r.data.message}\n\n${r.data.existing.join(", ")}\n\nRun again?`)) return run(fromFile, true);
  if (!r.ok && r.status !== 409) $("#runline").textContent = r.data.error || "Could not start";
  else if (r.ok) { $("#domains").value = ""; if (r.data.invalid.length) $("#runline").textContent = "Skipped (not domains): " + r.data.invalid.join(", "); }
  refreshStatus(); refreshList();
}

async function scorecard() {
  const { data: m } = await api("/api/metrics"), k = m.kill_checks, t = m.targets;
  const n = v => v == null ? "not enough data" : v;
  const flag = (v, bad) => v === null ? `<span class="pill">not enough data yet (needs ${k.min_sample})</span>` : v ? `<span class="pill danger">${bad}</span>` : `<span class="pill success">OK</span>`;
  $("#score-dialog").innerHTML = `<h2 style="margin-top:0">Scorecard</h2><div class="muted small">From saved runs only. ${m.drafts_reviewed} drafts reviewed, ${m.companies_processed} companies processed.</div>
    <div class="kv" style="margin-top:12px">
      <span class="k">Your review time per company (target under ${t.minutes_per_company} min)</span><b>${m.review_seconds_avg == null ? "none yet" : (m.review_seconds_avg / 60).toFixed(1) + " min"}</b>
      <span class="k">Pipeline run time per company (runs unattended)</span><b>${m.pipeline_seconds_avg == null ? "none yet" : (m.pipeline_seconds_avg / 60).toFixed(1) + " min"}</b>
      <span class="k">Drafts you rewrote or rejected (target under ${t.rewrite_rate_pct}%, stop above 50%)</span><b>${n(m.rewrite_rate_pct)}${m.rewrite_rate_pct == null ? "" : "%"}</b>
      <span class="k">  of which heavy rewrites or rejections</span><b>${n(m.heavy_rewrite_rate_pct)}${m.heavy_rewrite_rate_pct == null ? "" : "%"}</b>
      <span class="k">Contact accuracy, your Yes/No verdicts (stop below ${t.contact_accuracy_pct}%)</span><b>${n(m.contact_accuracy_pct)}${m.contact_accuracy_pct == null ? "" : "%"} (${m.contact_labelled} judged)</b>
      <span class="k">Gemini tokens used / cost per company if on a paid plan</span><b>${m.gemini_tokens_total.toLocaleString()} / ${m.gemini_cost_per_company_usd_if_paid == null ? "unknown" : "$" + m.gemini_cost_per_company_usd_if_paid}</b>
      <span class="k">Hunter credits spent (fresh lookups only)</span><b>${m.hunter_credits_spent}</b>
      <span class="k">Research limit reached before a decision</span><b>${n(m.research_limit_hit_rate_pct)}${m.research_limit_hit_rate_pct == null ? "" : "%"}</b>
      <span class="k">Hunter lookups paused by a limit</span><b>${m.hunter_limit_hits}</b></div>
    <h3 style="margin-bottom:4px">Shut-down checks</h3><div class="kv">
      <span class="k">You rewrite more than 50% of emails</span>${flag(k.rewrites_over_50pct, "Stop")}
      <span class="k">Contact accuracy below 60%</span>${flag(k.contact_accuracy_under_60pct, "Stop")}
      <span class="k">Review takes longer than the ${t.baseline_minutes}-minute manual baseline</span>${flag(k.takes_more_time_than_it_saves, "Stop")}</div>
    <div class="muted small" style="margin-top:10px">A rewrite means you edited the draft or rejected it. Review time runs from when you open a draft to your decision.</div>
    <div class="actions"><button class="btn" onclick="document.getElementById('score-dialog').close()">Close</button></div>`;
  $("#score-dialog").showModal();
}

// ------------------------------------------------------------------ start
$("#btn-run").onclick = () => run(false);
$("#btn-file").onclick = () => run(true);
$("#btn-score").onclick = scorecard;
(async function init() {
  const c = await api("/api/config"); CFG = c.data || { weights: {}, threshold: 65, words: [80, 120], contact_pass: 8, mail: {} };
  await refreshStatus(); const rows = await refreshList();
  const first = rows.find(r => r.needs_review) || rows[0];
  if (first) await select(first.domain); else await showDetail(true);
  setInterval(async () => { await refreshStatus(); await refreshList(); if (!editing) showDetail(false); }, 2500);
})();
