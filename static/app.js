// Review desk. Shows only what is saved in Neon. Approve / Edit / Reject only RECORD a decision: nothing here can send an email.
const $ = s => document.querySelector(s);
const esc = s => String(s ?? "").replace(/[&<>"']/g, c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const link = (u, t) => /^https?:\/\//i.test(u || "") ? `<a href="${esc(u)}" target="_blank" rel="noopener noreferrer">${esc(t || u)}</a>` : esc(u || "");
const day = d => d ? String(d).slice(0, 10) : "–";
const STATE = { positive: "evidence found", missing: "no evidence found", explicit_negative: "evidence says no" };
const LABEL_TONE = { observed: "neutral", inferred: "warning", unknown: "neutral", conflicting: "danger" };
const PAGE_RESULT = { ok: "Read", blocked: "Blocked (not a poor-fit signal)", not_found: "Page not found", failed: "Could not read", robots_disallowed: "Not allowed by the site's robots.txt" };
const ROLE = { compliance_grc_leader: "compliance/GRC leader", security_leader_ciso: "security leader", cto_vp_engineering: "CTO / engineering head", technical_founder_ceo: "founder / CEO", other: "other role" };
const GATES = [["saas", "SaaS"], ["series", "Series A–C"], ["compliance", "Compliance workload"], ["score", null]];
const GATE_WORD = { passed: ["✓", "Passed"], failed: ["✕", "Failed"], unknown: ["?", "Unknown"] };

let CFG = { labels: {}, threshold: 65, contact_pass: 8, words: [80, 120] }, selected = null, mode = "company", editing = false, sig = "";

async function api(path, body) {
  const r = await fetch(path, body === undefined ? {} : { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) });
  let data = null; try { data = await r.json(); } catch (e) {}
  return { ok: r.ok, status: r.status, data };
}

// ------------------------------------------------------------------ top bar and list
async function refreshStatus() {
  const { data } = await api("/api/status"); if (!data) return;
  const h = $("#pill-hunter");
  h.textContent = data.hunter_remaining == null ? "Hunter credits: unknown" : `Hunter credits left: ${data.hunter_remaining}`;
  h.className = "pill" + (data.hunter_remaining != null && data.hunter_remaining < 5 ? " warning" : "");
  const g = $("#pill-gmail");
  if (data.gmail === "ok") { g.textContent = "Gmail Drafts connected"; g.className = "pill success"; g.title = ""; }
  else { g.textContent = "Gmail setup needed"; g.className = "pill danger"; g.title = data.gmail || ""; }
}

async function refreshList() {
  const { data } = await api("/api/companies");
  const el = $("#list");
  if (!data || !data.length) { el.innerHTML = `<div class="empty">No researched companies yet. Press Run to search Google News.</div>`; return []; }
  el.innerHTML = data.map(c => `
    <button class="item ${mode === "company" && c.id === selected ? "sel" : ""}" data-id="${c.id}" role="listitem">
      <div class="top"><span>${esc(c.name)}</span><span>${c.score == null ? "–" : c.score + "/100"}</span></div>
      <div class="dom">${esc(c.domain)}</div>
      <span class="badge tone-${esc(c.tone)}">${esc(c.status)}</span>
    </button>`).join("");
  el.querySelectorAll(".item").forEach(b => b.onclick = () => select(Number(b.dataset.id)));
  return data;
}

// ------------------------------------------------------------------ one company
async function select(id) { mode = "company"; selected = id; editing = false; sig = ""; $("#btn-news").classList.remove("sel"); await refreshList(); await showDetail(true); }

async function showDetail(force) {
  if (mode !== "company") return;
  if (!selected) { $("#detail").innerHTML = `<div class="empty">Select a company on the left, or press Run to find new ones.</div>`; return; }
  const { ok, data } = await api(`/api/company/${selected}`);
  if (!ok) { $("#detail").innerHTML = `<div class="empty">Could not load this company.</div>`; return; }
  const e = data.email || {};
  const newSig = [data.view.key, data.company.stage, e.review_decision, e.gmail_draft_error, (data.contacts.find(c => c.chosen) || {}).contact_correct].join("|");
  if (!force && (newSig === sig || editing)) return;
  sig = newSig; render(data);
}

function evidenceHtml(list) {
  return (list || []).map(e => `<blockquote>“${esc(e.excerpt)}”<span class="src"><span class="lab tone-${LABEL_TONE[e.label] || "neutral"}">${esc(e.label)}</span>${e.source_kind === "news" ? "Google News" : "Website"} · ${link(e.source_url)} · read ${esc(day(e.retrieved_on))}${e.published_on ? " · published " + esc(day(e.published_on)) : ""}</span></blockquote>`).join("");
}

function render(d) {
  const c = d.company, em = d.email, chosen = d.contacts.find(x => x.chosen), dec = em ? em.review_decision : null;
  const stage = c.funding_stage && c.funding_stage !== "unknown" ? "Funding: " + c.funding_stage.replace(/\b\w/g, m => m.toUpperCase()) : "Funding stage: unknown";
  const total = c.total_score == null ? null : Math.round(Number(c.total_score));
  let h = `<div class="head"><div><h2>${esc(c.name || c.domain)}</h2><div class="meta">${link("https://" + c.domain, c.domain)} · ${esc(stage)}${(c.flags || []).includes("funding_stage_unknown") ? " · flagged for you" : ""}</div></div>`;
  if (total != null) h += `<div class="score"><span class="num">${total}</span><span class="of"> / 100</span><div class="bar"><i style="width:${total}%"></i></div></div>`;
  h += `</div><div class="banner tone-${esc(d.view.tone)}"><b>${esc(d.view.label)}</b>${c.status === "DRAFT_READY" ? "" : esc(c.escalation_reason || c.reason || "")}</div>`;

  if (c.gates) {
    h += `<div class="chips" aria-label="Gates">` + GATES.map(([k, label]) => { const g = c.gates[k]; if (!g) return "";
      const [sym, word] = GATE_WORD[g.state]; return `<span class="chip ${g.state}" title="${esc(g.detail)}"><b>${sym}</b>${esc(label || "Score ≥ " + CFG.threshold)}: ${word}</span>`; }).join("") +
      `</div><div class="muted small">Hover a chip for the detail. All four must pass. "Unknown" means the evidence was not found, not that the answer is no.</div>`;
  }
  if (chosen) {
    const passing = d.contacts.filter(x => x.passed).length;
    h += `<div class="card"><h3>Why this person</h3><div class="who"><div>${esc(chosen.name)}, ${esc(chosen.position || "no title")}: contact score <b>${chosen.total}/10</b> (passes at ${CFG.contact_pass}). Picked as the ${esc(ROLE[chosen.role_category] || "best match")}; ${passing} of ${d.contacts.length} Hunter candidates passed.
      <div class="muted small">Email on record from Hunter (research data only, never a delivery address): ${esc(chosen.email || "none")}</div></div>
      <div class="small">Right person? <button class="btn small ${chosen.contact_correct === true ? "on" : ""}" data-fb="1">Yes</button> <button class="btn small ${chosen.contact_correct === false ? "on" : ""}" data-fb="0">No</button></div></div></div>`;
  }
  h += trail(d);
  if (em) h += draftPanel(d);
  else if (c.status !== "DRAFT_READY") h += `<div class="card muted">No draft was written for this company. The decision trail above shows why.</div>`;
  $("#detail").innerHTML = h; wire(d);
}

function trail(d) {
  const c = d.company, byDim = {}; d.evidence.forEach(e => (byDim[e.dimension] = byDim[e.dimension] || []).push(e));
  let h = `<details><summary>Decision trail: why it was found, judged and written this way</summary><div class="inner">`;
  h += `<details open><summary>1. Why it was found</summary><div class="inner">` + (d.found_via.length
    ? d.found_via.map(f => `<div class="newsrow"><div class="h">${link(f.source_url, f.headline)}</div><div class="muted small">${esc(f.source_name || "")} · ${esc(day(f.published_at))} · search: ${esc(f.query)}</div></div>`).join("")
    : `<div class="muted">This company was researched by a direct developer command, not found through a Google News result.</div>`) + `</div></details>`;
  h += `<details open><summary>2. Why it qualified (or not): score and evidence per question</summary><div class="inner">` +
    d.scores.map(s => `<div class="dim"><div class="t"><span>${esc(CFG.labels[s.dimension] || s.dimension)}</span><span>${Number(s.points)} pts · level ${s.level} · ${STATE[s.state] || s.state}</span></div>
      ${s.reasoning ? `<div class="muted small">AI's reasoning: ${esc(s.reasoning)}</div>` : ""}${evidenceHtml(byDim[s.dimension])}</div>`).join("") +
    `<div class="dim"><div class="t"><span>Funding stage</span><span>${esc(c.funding_stage || "unknown")}</span></div>${evidenceHtml(byDim.funding)}</div>` +
    (byDim.conflict ? `<div class="dim"><div class="t"><span>Sources that disagree</span></div>${evidenceHtml(byDim.conflict)}</div>` : "") +
    `<div class="muted small">Points are added up by code, not by the AI. Employee count, industry and cloud stack are information only.</div></div></details>`;
  h += `<details><summary>3. Research log: every round</summary><div class="inner">` + d.rounds.map(r => {
    const g = (r.gap && r.gap.gap) || []; const srcs = r.sources || [];
    return `<div class="dim"><div class="t"><span>Round ${r.round_no}</span><span>Decision after: ${esc(r.revised_decision)}</span></div>
      ${g.length ? `<div class="small"><b>Gap:</b> ${g.map(x => esc(x.gap) + (x.page_type ? ` (look on a ${esc(x.page_type)} page)` : "")).join("; ")}</div>` : ""}
      <div class="small"><b>Source:</b> ${srcs.map(s => s.url && s.url !== "Google News" ? `${link(s.url)} (${esc(PAGE_RESULT[s.result] || s.result)}${s.attempts > 1 ? ", " + s.attempts + " tries" : ""}${s.from_cache ? ", cached" : ""})` : `Google News (${esc(s.items ?? "")} headlines)`).join("; ") || "none"}</div>
      <div class="small"><b>New evidence:</b> ${(r.new_evidence || []).length || "none"}</div>${r.gap && r.gap.note ? `<div class="muted small">${esc(r.gap.note)}</div>` : ""}</div>`; }).join("") + `</div></details>`;
  if (d.contacts.length) h += `<details><summary>4. Contact scores (out of 10)</summary><div class="inner"><table><tr><th>Person</th><th>Role</th><th>Company link</th><th>Owns function</th><th>Drives action</th><th>Fits need</th><th>Identity</th><th>Total</th></tr>` +
    d.contacts.map(x => `<tr><td>${esc(x.name)}${x.chosen ? " ← chosen" : ""}</td><td>${esc(x.position || "no title")}</td><td>${x.scores.company_association}</td><td>${x.scores.functional_ownership}</td><td>${x.scores.ability_to_drive_action}</td><td>${x.scores.match_to_need}</td><td>${x.scores.identity_quality}</td><td><b>${x.total}</b> ${x.passed ? "✓" : ""}</td></tr>`).join("") +
    `</table><div class="muted small">Scores come from the AI and are checked and added up by code. Role order: compliance/GRC, security, CTO/engineering, founder.</div></div></details>`;
  if (d.pain.length) h += `<details><summary>5. Pain points and the hook</summary><div class="inner">` + d.pain.map(p => `<div class="dim"><div class="t"><span>${p.position === 1 ? "Used in the email" : "Backup"}: ${esc(p.hypothesis)}</span><span>${p.total}/10</span></div>
    <div class="small">Fact it rests on: ${esc(p.observed_fact)} · Sprinto capability: ${esc(p.capability)}</div>${p.role_relevance ? `<div class="small">Why it matters to this person: ${esc(p.role_relevance)}</div>` : ""}
    <blockquote>“${esc(p.evidence_excerpt)}”<span class="src">${link(p.evidence_url)}</span></blockquote></div>`).join("") + `</div></details>`;
  if (d.email_attempts.length) h += `<details><summary>6. Email checks</summary><div class="inner">` + d.email_attempts.map(a => `<div class="dim"><div class="t"><span>Attempt ${a.revision + 1}${a.passed ? " ✓ passed" : " ✕ revised"}</span><span>${a.qa_total}/10 · ${a.words} words</span></div>
    ${(a.qa.code_problems || []).length ? `<div class="small">Code checks flagged: ${a.qa.code_problems.map(esc).join("; ")}</div>` : ""}${a.qa.feedback ? `<div class="muted small">Reviewer note: ${esc(a.qa.feedback)}</div>` : ""}</div>`).join("") + `</div></details>`;
  h += `<div class="muted small" style="margin-top:8px">Run ${esc(String(d.run.id).slice(0, 8))} · rubric ${esc(d.run.version)} · ${c.seconds ?? "?"} s · ${(c.usage || {}).calls || 0} AI calls · ${(c.retry_counts || {}).page_reads || 0} pages read</div>`;
  return h + `</div></details>`;
}

function draftPanel(d) {
  const e = d.email, dec = e.review_decision, c = d.company;
  const note = e.gmail_draft_error
    ? `<div class="note tone-warning"><b>Not in Gmail Drafts yet.</b> ${esc(e.gmail_draft_error)}</div>`
    : e.gmail_draft_id ? `<div class="gmailnote">✓ This draft is also in your <a href="${esc(d.gmail_drafts_url)}" target="_blank" rel="noopener noreferrer">Gmail Drafts folder</a> (same subject and text). Send it from Gmail yourself.</div>` : "";
  return `<div class="card draft" id="draft"><h3>Draft email${dec !== "pending" ? " · you marked it: " + esc(dec) : ""}</h3>
    <label for="subj">Subject</label><input type="text" id="subj" value="${esc(e.subject)}" readonly>
    <label for="body">Email</label><textarea id="body" readonly>${esc(e.body)}</textarea><div class="wc" id="wc"></div>${note}
    <div class="actions" id="act-view"><button class="btn primary" id="btn-approve">Approve</button><button class="btn" id="btn-edit">Edit</button><button class="btn danger" id="btn-reject">Reject</button></div>
    <div class="actions" id="act-edit" hidden><button class="btn primary" id="btn-save">Save edit</button><button class="btn" id="btn-cancel">Cancel</button></div>
    <div class="muted small" style="margin-top:8px">These buttons only record your decision. They send nothing. ${c.status !== "DRAFT_READY" ? "" : ""}Saving an edit also updates the Gmail draft.</div></div>`;
}

const wc = t => (t.trim().match(/\S+/g) || []).length;
function wire(d) {
  document.querySelectorAll("[data-fb]").forEach(b => b.onclick = async () => { await api(`/api/company/${d.company.id}/contact-feedback`, { correct: b.dataset.fb === "1" }); await showDetail(true); });
  const dr = $("#draft"); if (!dr) return;
  const subj = $("#subj"), body = $("#body"), w = $("#wc");
  const upd = () => { const n = wc(body.value); w.textContent = `${n} words (target ${CFG.words[0]}–${CFG.words[1]})`; w.className = "wc" + (n < CFG.words[0] - 10 || n > CFG.words[1] + 10 ? " off" : ""); };
  const fit = () => { body.style.height = "auto"; body.style.height = (body.scrollHeight + 4) + "px"; };   // show the whole email, no scrolling
  upd(); fit(); body.oninput = () => { upd(); fit(); };
  const setEdit = on => { editing = on; subj.readOnly = body.readOnly = !on; dr.classList.toggle("editing", on); $("#act-view").hidden = on; $("#act-edit").hidden = !on; if (on) body.focus(); };
  $("#btn-edit").onclick = () => setEdit(true);
  $("#btn-cancel").onclick = () => { subj.value = d.email.subject; body.value = d.email.body; upd(); setEdit(false); };
  $("#btn-save").onclick = async () => { const r = await api(`/api/company/${d.company.id}/edit`, { subject: subj.value, body: body.value });
    if (!r.ok) return alert((r.data && r.data.error) || "Could not save"); editing = false; if (r.data.gmail_error) alert("Saved here, but Gmail could not be updated:\n" + r.data.gmail_error); await showDetail(true); refreshList(); };
  $("#btn-approve").onclick = async () => { const r = await api(`/api/company/${d.company.id}/approve`, {}); if (!r.ok) alert((r.data && r.data.error) || "Could not record"); await showDetail(true); refreshList(); };
  $("#btn-reject").onclick = async () => { const why = prompt("Why are you rejecting this draft? (optional)"); if (why === null) return;
    await api(`/api/company/${d.company.id}/reject`, { reason: why }); await showDetail(true); refreshList(); };
}

// ------------------------------------------------------------------ Google News results (why companies were found)
async function showNews() {
  mode = "news"; selected = null; editing = false; await refreshList(); $("#btn-news").classList.add("sel");
  const { data } = await api("/api/discovery");
  if (!data || !data.run) { $("#detail").innerHTML = `<div class="empty">No Google News search has been run yet. Press Run.</div>`; return; }
  const r = data.results, conf = r.filter(x => x.domain_status === "confirmed").length, named = r.filter(x => x.company_name).length;
  $("#detail").innerHTML = `<div class="head"><div><h2>Google News results</h2><div class="meta">Run ${esc(String(data.run.id).slice(0, 8))} · ${esc(day(data.run.started_at))}</div></div></div>
    <div class="note">${r.length} results saved. ${named} have a company name read from the headline. <b>${conf}</b> have a website address that a source states; the rest are <b>unknown</b>, because a website address is never guessed.
    <br>Next step, the shortlist, is <b>waiting for your OK</b>, so nothing has been researched from these yet.</div>
    <div class="card">` + r.map(x => `<div class="newsrow"><div class="h">${link(x.source_url, x.headline)}</div>
      <div class="muted small">${esc(x.source_name || "")} · ${esc(day(x.published_at))} · company: ${x.company_name ? esc(x.company_name) : "not clear from headline"} · website: ${x.domain_status === "confirmed" ? esc(x.company_domain) + " (stated in the source)" : "unknown"}</div></div>`).join("") + `</div>`;
}

// ------------------------------------------------------------------ run and scorecard
async function run() {
  const b = $("#btn-run"); b.disabled = true; b.textContent = "Searching Google News…"; $("#runmsg").textContent = "This takes about a minute. You can keep reviewing meanwhile.";
  const r = await api("/api/run", {});
  b.disabled = false; b.textContent = "Run";
  if (!r.ok) { $("#runmsg").textContent = (r.data && r.data.error) || "The run failed."; return; }
  $("#runmsg").textContent = `Found ${r.data.results} news results; ${r.data.domains_confirmed} have a confirmed website. The shortlist is waiting for your OK.`;
  await loadNewsCount(); await showNews();
}
async function loadNewsCount() { const { data } = await api("/api/discovery"); $("#news-count").textContent = data && data.run ? data.results.length : ""; }

async function scorecard() {
  const { data: m } = await api("/api/metrics"), k = m.kill_checks, t = m.targets;
  const n = (v, u = "") => v == null ? "not enough data" : v + u;
  const flag = (v, bad) => v === null ? `<span class="pill">not enough data yet (needs ${k.min_sample})</span>` : v ? `<span class="pill danger">${bad}</span>` : `<span class="pill success">OK</span>`;
  $("#score-dialog").innerHTML = `<h2 style="margin-top:0">Scorecard</h2><div class="muted small">From saved results only. ${m.drafts_reviewed} drafts reviewed, ${m.companies_processed} companies processed.</div>
    <div class="kv" style="margin-top:12px">
      <span class="k">Your review time per company (target under ${t.minutes_per_company} min)</span><b>${m.review_seconds_avg == null ? "none yet" : (m.review_seconds_avg / 60).toFixed(1) + " min"}</b>
      <span class="k">Pipeline run time per company (runs unattended)</span><b>${m.pipeline_seconds_avg == null ? "none yet" : (m.pipeline_seconds_avg / 60).toFixed(1) + " min"}</b>
      <span class="k">Drafts you edited or rejected (target under ${t.rewrite_rate_pct}%, stop above 50%)</span><b>${n(m.rewrite_rate_pct, "%")}</b>
      <span class="k">  of which heavy rewrites or rejections</span><b>${n(m.heavy_rewrite_rate_pct, "%")}</b>
      <span class="k">Contact accuracy, your Yes/No verdicts (stop below ${t.contact_accuracy_pct}%)</span><b>${n(m.contact_accuracy_pct, "%")} (${m.contact_labelled} judged)</b>
      <span class="k">Gemini tokens used / cost per company if on a paid plan</span><b>${Number(m.gemini_tokens_total).toLocaleString()} / ${m.gemini_cost_per_company_usd_if_paid == null ? "unknown" : "$" + m.gemini_cost_per_company_usd_if_paid}</b>
      <span class="k">Hunter credits spent (fresh lookups only)</span><b>${m.hunter_credits_spent}</b>
      <span class="k">Research limit reached before a decision</span><b>${n(m.research_limit_hit_rate_pct, "%")}</b>
      <span class="k">Hunter lookups paused by a limit</span><b>${m.hunter_limit_hits}</b></div>
    <h3 style="margin-bottom:4px">Shut-down checks</h3><div class="kv">
      <span class="k">You rewrite more than 50% of emails</span>${flag(k.rewrites_over_50pct, "Stop")}
      <span class="k">Contact accuracy below 60%</span>${flag(k.contact_accuracy_under_60pct, "Stop")}
      <span class="k">Review takes longer than the ${t.baseline_minutes}-minute manual baseline</span>${flag(k.takes_more_time_than_it_saves, "Stop")}</div>
    <div class="muted small" style="margin-top:10px">A rewrite means you edited the draft or rejected it. Review time runs from when you open a draft to your decision.</div>
    <div class="actions"><button class="btn" onclick="document.getElementById('score-dialog').close()">Close</button></div>`;
  $("#score-dialog").showModal();
}

$("#btn-run").onclick = run; $("#btn-news").onclick = showNews; $("#btn-score").onclick = scorecard;
(async function init() {
  const c = await api("/api/config"); if (c.data) CFG = c.data;
  await refreshStatus(); await loadNewsCount(); const rows = await refreshList();
  const first = rows.find(r => r.needs_review) || rows[0];
  if (first) await select(first.id); else await showDetail(true);
  setInterval(async () => { await refreshStatus(); await refreshList(); if (!editing) showDetail(false); }, 8000);
})();
