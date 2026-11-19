// Funding Outreach desk. Shows only what is saved in Neon. Approve / Edit / Reject / Done only RECORD a decision: nothing here can send an email.
const $ = (s, r = document) => r.querySelector(s);
const esc = s => String(s ?? "").replace(/[&<>"']/g, c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const link = (u, t) => /^https?:\/\//i.test(u || "") ? `<a href="${esc(u)}" target="_blank" rel="noopener noreferrer">${esc(t || u)}</a>` : esc(u || "");
const day = d => d ? String(d).slice(0, 10) : "–";
const sleep = ms => new Promise(r => setTimeout(r, ms));
const ago = d => { if (!d) return ""; const s = (Date.now() - new Date(d).getTime()) / 1000; if (s < 90) return "just now"; if (s < 3600) return Math.round(s / 60) + " min ago"; if (s < 86400) return Math.round(s / 3600) + " h ago"; return Math.round(s / 86400) + " d ago"; };
const STATE_WORD = { positive: "evidence found", missing: "no evidence found", explicit_negative: "evidence says no" };
const LABEL_TONE = { observed: "neutral", inferred: "warning", unknown: "neutral", conflicting: "danger" };
const PAGE_RESULT = { ok: "Read", blocked: "Blocked (not a poor-fit signal)", not_found: "Page not found", failed: "Could not read", robots_disallowed: "Not allowed by the site's robots.txt" };
const ROLE = { compliance_grc_leader: "compliance/GRC leader", security_leader_ciso: "security leader", cto_vp_engineering: "CTO / engineering head", technical_founder_ceo: "founder / CEO", other: "other role" };
const GATES = [["saas", "SaaS"], ["series", "Series A–C"], ["compliance", "Compliance workload"], ["score", null]];
const GATE_WORD = { passed: ["✓", "Passed"], failed: ["✕", "Failed"], unknown: ["?", "Unknown"] };
const DIM = { business_fit: "Business and technology fit", customer_trust: "Customer trust exposure", compliance_workload: "Relevant compliance workload", reason_now: "Reason to engage now", delivery_complexity: "Delivery complexity" };
const T_ORDER = ['total_min','contact_pass_total','pain_pass_total','email_pass_total','email_words_target_min','email_words_target_max','reason_now_level1_months','reason_now_level2_months'], L_ORDER = ['shortlist_size','initial_extra_pages','max_extra_rounds','pages_per_round','max_read_attempts','hunter_calls_per_run','email_max_revisions'], P_ORDER = ['stage_points','signal_points_each','signal_points_max','max_age_months'];
const ordered = (obj, order) => order.filter(k => k in obj).map(k => [k, obj[k]]);
const STEPS = [["finding_website", "Website"], ["reading_website", "Research"], ["finding_contact", "Contact"], ["writing_email", "Email"]];

const S = { me: { name: "", can_edit: false, code_required: false }, cfg: { threshold: 65, contact_pass: 8, labels: {}, words: [80, 120] }, driving: false, sel: null, detail: null, editing: false, progress: [], open: [] };

async function api(path, body) {
  const r = await fetch(path, body === undefined ? {} : { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) });
  let data = null; try { data = await r.json(); } catch (e) {}
  return { ok: r.ok, status: r.status, data };
}
function toast(msg, ms = 4200) { const t = $("#toast"); t.textContent = msg; t.hidden = false; clearTimeout(toast.t); toast.t = setTimeout(() => t.hidden = true, ms); }
const needCode = () => S.me.can_edit ? "" : `disabled title="Needs the access code (Account page)"`;
const fail = r => toast((r.data && r.data.error) || "Something went wrong.");

// ------------------------------------------------------------------ router
function parseHash() { const h = location.hash.replace(/^#\/?/, ""); const [page, q] = h.split("?"); return { page: page || "today", params: Object.fromEntries(new URLSearchParams(q || "")) }; }
function go(page, params = {}) { const q = new URLSearchParams(params).toString(); location.hash = `#/${page}${q ? "?" + q : ""}`; }
const PAGES = { today: pageToday, drafts: pageDrafts, rejected: pageRejected, sources: pageSources, runs: pageRuns, settings: pageSettings, account: pageAccount };
async function route() {
  const { page, params } = parseHash(); S.editing = false;
  document.querySelectorAll("#nav a").forEach(a => a.classList.toggle("on", a.dataset.p === page));
  $("#main").innerHTML = `<div class="muted">Loading…</div>`;
  try { await (PAGES[page] || pageToday)(params); } catch (e) { $("#main").innerHTML = `<div class="note bad">Could not load this page: ${esc(e.message)}</div>`; }
}
window.addEventListener("hashchange", route);

// ------------------------------------------------------------------ status bar, theme
async function refreshChrome() {
  const [o, d] = await Promise.all([api("/api/overview"), api("/api/drafts?tab=review")]);
  if (d.ok) $("#n-drafts").textContent = d.data.counts.review;
  const st = $("#status");
  if (S.driving) st.innerHTML = `<span class="dot live"></span>Run in progress`;
  else if (o.ok && o.data.run) st.innerHTML = `<span class="dot ok"></span>Last run ${esc(ago(o.data.run.started_at))}`;
  else st.innerHTML = `<span class="dot"></span>No run yet`;
  return o.ok ? o.data : null;
}
function applyTheme() {
  $("#palette").value = document.documentElement.dataset.palette; $("#mode").textContent = document.documentElement.dataset.mode === "dark" ? "Light mode" : "Dark mode";
}
$("#palette").onchange = e => { document.documentElement.dataset.palette = e.target.value; try { localStorage.setItem("palette", e.target.value); } catch (x) {} };
$("#mode").onclick = () => { const m = document.documentElement.dataset.mode === "dark" ? "light" : "dark"; document.documentElement.dataset.mode = m; try { localStorage.setItem("mode", m); } catch (x) {} applyTheme(); };

// ------------------------------------------------------------------ the Run (the browser walks the queue; every step is a short request saved in Neon)
async function finishCompany(id) {
  let busy = 0;
  for (;;) {
    const r = await api("/api/advance", { company_id: id });
    if (!r.ok) { fail(r); return; }
    if (r.data.stage === "done") return;
    if (r.data.busy) { if (++busy > 45) return; await sleep(4000); continue; }
    if (!r.data.ran) return;
  }
}
async function drive(ids, workers = 3) {
  S.driving = true; refreshChrome();
  const queue = [...ids], poll = setInterval(async () => { const p = await api("/api/run/progress"); if (p.ok) { S.progress = p.data; if (parseHash().page === "today") renderProgress(); } refreshChrome(); }, 4000);
  try { await Promise.all(Array.from({ length: Math.min(workers, queue.length) }, async () => { while (queue.length) await finishCompany(queue.shift()); })); }
  finally { clearInterval(poll); S.driving = false; }
  toast("Run finished. New drafts are under Drafts."); S.open = []; await refreshChrome(); route();
}
async function startRun() {
  if (S.driving) return;
  if (!S.me.can_edit) { toast("Running needs the access code. Enter it on the Account page."); return go("account"); }
  const b = $("#run-btn"); if (b) { b.disabled = true; b.textContent = "Searching Google News…"; }
  $("#run-msg") && ($("#run-msg").textContent = "Searching Google News and building the shortlist. This takes about a minute.");
  const r = await api("/api/run", {});
  if (!r.ok) { fail(r); if (b) { b.disabled = false; b.textContent = "Run now"; } return; }
  toast(`Found ${r.data.saved} news results. ${r.data.shortlisted} companies shortlisted.`);
  S.open = r.data.company_ids; await route(); drive(r.data.company_ids);
}

// ------------------------------------------------------------------ Today
function renderProgress() {
  const el = $("#progress"); if (!el) return;
  const rows = S.progress; if (!rows.length || !(S.driving || S.open.length)) { el.innerHTML = ""; return; }
  const done = rows.filter(r => r.stage === "done").length;
  el.innerHTML = `<div class="card" style="margin-top:18px;text-align:left"><h3>Run progress · ${done} of ${rows.length} companies finished</h3>` + rows.map(r => {
    const idx = STEPS.findIndex(s => s[0] === r.stage), finished = r.stage === "done";
    return `<div class="runrow"><span><b>${esc(r.name)}</b><div class="tiny muted">${esc(r.domain || "website not found yet")}</div></span>
      <span class="steps" title="${esc(r.stage)}">${STEPS.map((s, i) => `<i class="${finished || i < idx ? "on" : i === idx ? "now" : ""}"></i>`).join("")}</span>
      <span>${finished ? badge(r.status) : `<span class="muted small">${esc((STEPS[idx] || ["", "Queued"])[1])}…</span>`}</span></div>`; }).join("") + `</div>`;
}
const STATUS_LABEL = { DRAFT_READY: ["Draft ready", "warning"], DISQUALIFIED: ["Not a fit", "neutral"], LOW_PRIORITY: ["Low priority", "neutral"], INSUFFICIENT_EVIDENCE: ["Not enough evidence", "warning"], NO_SUITABLE_CONTACT: ["No suitable contact", "warning"], CONTACT_LOOKUP_PAUSED: ["Contact lookup paused", "warning"], INSUFFICIENT_PAIN_EVIDENCE: ["Not enough to write", "warning"], EMAIL_QA_FAILED: ["Failed quality checks", "danger"], ERROR: ["Error", "danger"], QUALIFIED: ["Qualified", "neutral"] };
const badge = s => { const [l, t] = STATUS_LABEL[s] || [s, "neutral"]; return `<span class="badge t-${t}">${esc(l)}</span>`; };

async function pageToday() {
  const o = await refreshChrome(); const h = new Date().getHours(), greet = h < 12 ? "Good morning" : h < 18 ? "Good afternoon" : "Good evening";
  const run = o && o.run, date = new Date().toLocaleDateString("en-GB", { weekday: "long", day: "numeric", month: "long" });
  const o2 = await api("/api/run/open"); S.open = (o2.ok && o2.data.company_ids) || [];
  const p = await api("/api/run/progress"); S.progress = p.ok ? p.data : [];
  const sub = !run ? "No run yet. Press Run and I will search Google News, read the companies and write the drafts."
    : S.driving ? "A run is working through its shortlist right now."
    : S.open.length ? `A run is unfinished: ${S.open.length} companies left.` : "Your latest run is done. Here is what it found.";
  $("#main").innerHTML = `<section class="hero"><div class="date">${esc(date)}</div><h1>${esc(greet)}, ${esc(S.me.name)}.</h1><div class="muted">${esc(sub)}</div>
    <div class="stats"><div class="stat hot"><div class="num">${o ? o.to_review : 0}</div><div class="muted">drafts ready for you</div></div>
      <div class="stat"><div class="num">${run ? run.skipped : 0}</div><div class="muted">companies skipped (did not fit)</div></div>
      <div class="stat"><div class="num">${run ? run.articles : 0}</div><div class="muted">articles read from ${run ? run.sources : 0} searches</div></div></div>
    <div>${o && o.to_review ? `<a class="btn primary cta" href="#/drafts">Review ${o.to_review} draft${o.to_review > 1 ? "s" : ""} →</a> ` : ""}
      <button class="btn ${o && o.to_review ? "" : "primary"} cta" id="run-btn" ${S.driving ? "disabled" : ""}>${S.driving ? "Run in progress…" : run ? "Run again" : "Run now"}</button>
      ${S.open.length && !S.driving && S.me.can_edit ? `<button class="btn cta" id="resume-btn">Continue unfinished run (${S.open.length})</button>` : ""}</div>
    <p class="muted small" id="run-msg" style="margin-top:18px">Nothing is ever sent by this software. You send from Gmail. <a href="#/sources">See where the articles came from</a>${S.me.can_edit ? "" : " · <a href='#/account'>Enter the access code to run</a>"}</p>
    <div id="progress"></div></section>
    ${o && o.waiting.length ? `<div class="card"><h3>Waiting for you</h3>${o.waiting.map(w => `<a class="runrow" style="text-decoration:none;color:inherit" href="#/drafts?id=${w.id}"><b>${esc(w.name)}</b><span class="muted">${esc(w.contact || "")}</span><span><span class="badge t-warning">score ${w.score}</span></span></a>`).join("")}</div>` : ""}
    ${o && o.gmail !== "ok" ? `<div class="note bad"><b>Gmail Drafts is not connected.</b> ${esc(o.gmail || "")} Drafts cannot be created until this is fixed (Runs &amp; health).</div>` : ""}`;
  renderProgress();
  $("#run-btn").onclick = startRun; const rb = $("#resume-btn"); if (rb) rb.onclick = () => drive(S.open);
}

// ------------------------------------------------------------------ Drafts
async function pageDrafts(p) {
  const tab = p.tab || "review", kind = p.kind || "";
  const r = await api(`/api/drafts?tab=${tab}${kind ? "&kind=" + kind : ""}`); if (!r.ok) throw new Error("drafts");
  const d = r.data, T = [["review", "To review"], ["approved", "Approved"], ["done", "Done"], ["older", "Older than 24 h"]];
  S.sel = p.id ? Number(p.id) : (d.items[0] && d.items[0].id) || null;
  const KS = [["verified", "Verified"], ["unverified", "Unverified"], ["none", "No address"]];
  $("#main").innerHTML = `<h1 class="page-title">Drafts</h1><p class="page-sub">Every draft is also in your Gmail Drafts folder. Nothing is ever sent by this software.</p>
    <div class="tabs">${T.map(([k, l]) => `<a href="#/drafts?tab=${k}" class="${k === tab ? "on" : ""}">${l}<span class="n">${d.counts[k]}</span></a>`).join("")}</div>
    <div class="chips" style="margin-top:0">${KS.map(([k, l]) => `<a class="pillbtn ${kind === k ? "on" : ""}" href="#/drafts?tab=${tab}${kind === k ? "" : "&kind=" + k}" style="text-decoration:none;color:inherit">${l}<span class="n">${d.kinds[k]}</span></a>`).join("")}</div>
    <div class="small muted" style="margin:6px 0 16px">Hunter's check of the contact's address (research data only, never a place to send). Verified = Hunter says valid.</div>
    ${d.items.length ? `<div class="split"><div class="list">${d.items.map(i => `<button class="dcard ${i.id === S.sel ? "on" : ""}" data-id="${i.id}">
        <div class="r1"><span>${esc(i.name)}</span><span class="badge t-neutral">${i.score}/100</span></div>
        <div class="small muted">${esc(i.contact || "no contact")}${i.position ? " · " + esc(i.position) : ""}</div>
        <div class="sn">${esc(i.snippet)}</div>
        <div class="small">${decisionBadge(i.decision)} ${i.in_gmail ? `<span class="muted">✓ in Gmail</span>` : `<span class="badge t-warning">not in Gmail</span>`} <span class="muted tiny">${esc(ago(i.created_at))}</span></div></button>`).join("")}</div>
      <div class="detail" id="detail"></div></div>`
    : `<div class="card empty"><h3>No drafts here</h3><div class="muted">New drafts appear after the next run.</div><div style="margin-top:14px"><a class="btn" href="#/runs">See last run</a></div></div>`}`;
  document.querySelectorAll(".dcard").forEach(b => b.onclick = () => { S.sel = Number(b.dataset.id); document.querySelectorAll(".dcard").forEach(x => x.classList.toggle("on", x === b)); history.replaceState(null, "", `#/drafts?tab=${tab}${kind ? "&kind=" + kind : ""}&id=${S.sel}`); loadDetail(S.sel); });
  if (S.sel && d.items.length) loadDetail(S.sel);
}
const decisionBadge = d => ({ pending: `<span class="badge t-warning">to review</span>`, edited: `<span class="badge t-warning">edited</span>`, approved: `<span class="badge t-success">approved</span>`, done: `<span class="badge t-success">done</span>`, rejected: `<span class="badge t-danger">rejected</span>` }[d] || "");

async function loadDetail(id, into = "#detail") {
  const el = $(into); if (!el) return; el.innerHTML = `<div class="muted">Loading…</div>`;
  const r = await api(`/api/company/${id}`); if (!r.ok) { el.innerHTML = `<div class="note bad">Could not load this company.</div>`; return; }
  S.detail = r.data; el.innerHTML = renderDetail(r.data); wireDetail(r.data, into);
}
function evidenceHtml(list) {
  return (list || []).map(e => `<blockquote>“${esc(e.excerpt)}”<span class="src"><span class="lab t-${LABEL_TONE[e.label] || "neutral"}">${esc(e.label)}</span>${e.source_kind === "news" ? "Google News" : "Website"} · ${link(e.source_url)} · read ${esc(day(e.retrieved_on))}${e.published_on ? " · published " + esc(day(e.published_on)) : ""}</span></blockquote>`).join("");
}
function renderDetail(d) {
  const c = d.company, em = d.email, chosen = d.contacts.find(x => x.chosen);
  const stage = c.funding_stage && c.funding_stage !== "unknown" ? "Funding: " + c.funding_stage.replace(/\b\w/g, m => m.toUpperCase()) : "Funding stage: unknown";
  const total = c.total_score == null ? null : Math.round(Number(c.total_score));
  let h = `<div class="head"><div><h2>${esc(c.name || c.domain)}</h2><div class="muted small">${c.domain ? link("https://" + c.domain, c.domain) : "website not confirmed"} · ${esc(stage)}${(c.flags || []).includes("funding_stage_unknown") ? " · flagged for you" : ""}${c.overruled ? " · overruled by you" : ""}</div></div>`;
  if (total != null) h += `<div class="score"><span class="num">${total}</span><span class="of"> / 100</span><div class="bar"><i style="width:${total}%"></i></div></div>`;
  h += `</div><div class="banner t-${esc(d.view.tone)}"><b>${esc(d.view.label)}</b>${c.status === "DRAFT_READY" ? "" : esc(c.escalation_reason || c.reason || "")}</div>`;
  if (c.gates) h += `<div class="chips">` + GATES.map(([k, label]) => { const g = c.gates[k]; if (!g) return ""; const [sym, word] = GATE_WORD[g.state];
      return `<span class="chip ${g.state}" title="${esc(g.detail)}"><b>${sym}</b>${esc(label || "Score ≥ " + S.cfg.threshold)}: ${word}</span>`; }).join("") + `</div><div class="muted tiny">Hover a chip for the detail. All four must pass. "Unknown" means the evidence was not found, not that the answer is no.</div>`;
  if (chosen) { const passing = d.contacts.filter(x => x.passed).length;
    h += `<div class="card" style="margin-top:14px"><h3>Why this person</h3><div class="who"><div>${esc(chosen.name)}, ${esc(chosen.position || "no title")}: contact score <b>${chosen.total}/10</b> (passes at ${S.cfg.contact_pass}). Picked as the ${esc(ROLE[chosen.role_category] || "best match")}; ${passing} of ${d.contacts.length} Hunter candidates passed.
      <div class="muted tiny">Address on record from Hunter (research data only, never a delivery address): ${esc(chosen.email || "none")}${chosen.email_verification ? " · Hunter: " + esc(chosen.email_verification) : ""}</div></div>
      <div class="small">Right person? <button class="btn sm ${chosen.contact_correct === true ? "on" : ""}" data-fb="1" ${needCode()}>Yes</button> <button class="btn sm ${chosen.contact_correct === false ? "on" : ""}" data-fb="0" ${needCode()}>No</button></div></div></div>`; }
  h += trail(d);
  if (em) h += draftPanel(d);
  else if (d.overrule_allowed) h += `<div class="card"><div class="muted">No draft was written. If you disagree with this decision you can overrule it from the Rejected page.</div></div>`;
  return h;
}
function trail(d) {
  const c = d.company, byDim = {}; d.evidence.forEach(e => (byDim[e.dimension] = byDim[e.dimension] || []).push(e));
  let h = `<details><summary>Decision trail: why it was found, judged and written this way</summary><div class="inner">`;
  h += `<details open><summary>1. Why it was found</summary><div class="inner">` + (d.found_via.length ? d.found_via.map(f => `<div class="dim"><div>${link(f.source_url, f.headline)}</div><div class="muted small">${esc(f.source_name || "")} · ${esc(day(f.published_at))} · search: ${esc(f.query)}</div>${f.domain_evidence ? `<div class="small">Website: ${esc(f.domain_evidence)}</div>` : ""}${f.outcome_note ? `<div class="muted small">${esc(f.outcome_note)}</div>` : ""}</div>`).join("") : `<div class="muted">Researched by a direct developer command, not found through a news result.</div>`) + `</div></details>`;
  if (byDim.website) h += `<details open><summary>2. How its website was confirmed</summary><div class="inner">${evidenceHtml(byDim.website)}<div class="muted tiny">A website is never guessed: Gemini suggested it, then the company's own homepage had to name the company and a quoted line had to be really on that page.</div></div></details>`;
  h += `<details open><summary>3. Why it qualified (or not): score and evidence per question</summary><div class="inner">` + d.scores.map(s => `<div class="dim"><div class="t"><span>${esc(DIM[s.dimension] || s.dimension)}</span><span>${Number(s.points)} pts · level ${s.level} · ${STATE_WORD[s.state] || s.state}</span></div>${s.reasoning ? `<div class="muted small">AI's reasoning: ${esc(s.reasoning)}</div>` : ""}${evidenceHtml(byDim[s.dimension])}</div>`).join("") +
    `<div class="dim"><div class="t"><span>Funding stage</span><span>${esc(c.funding_stage || "unknown")}</span></div>${evidenceHtml(byDim.funding)}</div>${byDim.conflict ? `<div class="dim"><div class="t"><span>Sources that disagree</span></div>${evidenceHtml(byDim.conflict)}</div>` : ""}<div class="muted tiny">Points are added up by code, not by the AI.</div></div></details>`;
  h += `<details><summary>4. Research log: every round</summary><div class="inner">` + d.rounds.map(r => { const g = (r.gap && r.gap.gap) || [], srcs = r.sources || [];
    return `<div class="dim"><div class="t"><span>Round ${r.round_no}</span><span>Decision after: ${esc(r.revised_decision)}</span></div>${g.length ? `<div class="small"><b>Gap:</b> ${g.map(x => esc(x.gap) + (x.page_type ? ` (look on a ${esc(x.page_type)} page)` : "")).join("; ")}</div>` : ""}
      <div class="small"><b>Source:</b> ${srcs.map(s => s.url && s.url !== "Google News" ? `${link(s.url)} (${esc(PAGE_RESULT[s.result] || s.result)}${s.attempts > 1 ? ", " + s.attempts + " tries" : ""}${s.from_cache ? ", cached" : ""})` : `Google News (${esc(s.items ?? "")} headlines)`).join("; ") || "none"}</div><div class="small"><b>New evidence:</b> ${(r.new_evidence || []).length || "none"}</div></div>`; }).join("") + `</div></details>`;
  if (d.contacts.length) h += `<details><summary>5. Contact scores (out of 10)</summary><div class="inner"><table><tr><th>Person</th><th>Role</th><th>Link</th><th>Owns</th><th>Drives</th><th>Fits</th><th>Identity</th><th>Total</th></tr>` + d.contacts.map(x => `<tr><td>${esc(x.name)}${x.chosen ? " ← chosen" : ""}</td><td>${esc(x.position || "no title")}</td><td>${x.scores.company_association}</td><td>${x.scores.functional_ownership}</td><td>${x.scores.ability_to_drive_action}</td><td>${x.scores.match_to_need}</td><td>${x.scores.identity_quality}</td><td><b>${x.total}</b> ${x.passed ? "✓" : ""}</td></tr>`).join("") + `</table></div></details>`;
  if (d.pain.length) h += `<details><summary>6. Pain points and the hook</summary><div class="inner">` + d.pain.map(p => `<div class="dim"><div class="t"><span>${p.position === 1 ? "Used in the email" : "Backup"}: ${esc(p.hypothesis)}</span><span>${p.total}/10</span></div><div class="small">Fact it rests on: ${esc(p.observed_fact)} · Capability: ${esc(p.capability)}</div><blockquote>“${esc(p.evidence_excerpt)}”<span class="src">${link(p.evidence_url)}</span></blockquote></div>`).join("") + `</div></details>`;
  if (d.email_attempts.length) h += `<details><summary>7. Email checks</summary><div class="inner">` + d.email_attempts.map(a => `<div class="dim"><div class="t"><span>Attempt ${a.revision + 1}${a.passed ? " ✓ passed" : " ✕ revised"}</span><span>${a.qa_total}/10 · ${a.words} words</span></div>${(a.qa.code_problems || []).length ? `<div class="small">Code checks flagged: ${a.qa.code_problems.map(esc).join("; ")}</div>` : ""}${a.qa.feedback ? `<div class="muted small">Reviewer note: ${esc(a.qa.feedback)}</div>` : ""}</div>`).join("") + `</div></details>`;
  return h + `<div class="muted tiny">Run ${esc(String(d.run.id).slice(0, 8))} · rubric ${esc(d.run.version)} · ${c.seconds ?? "?"} s · ${(c.usage || {}).calls || 0} AI calls</div></div></details>`;
}
function draftPanel(d) {
  const e = d.email, dec = e.review_decision, off = needCode();
  const note = e.gmail_draft_error ? `<div class="note warn"><b>Not in Gmail Drafts.</b> ${esc(e.gmail_draft_error)} <button class="btn sm" id="b-restore" ${off}>Try again</button></div>`
    : e.gmail_draft_id ? `<div class="gmailnote">✓ Also in your <a href="${esc(e.gmail_open_url)}" target="_blank" rel="noopener noreferrer">Gmail draft</a> (same subject and text). Send it from Gmail yourself. <button class="btn sm" id="b-restore" ${off} title="Deleted it by accident? This puts it back.">Put it back in Gmail</button></div>`
    : `<div class="note warn">There is no Gmail draft for this one yet. <button class="btn sm" id="b-restore" ${off}>Create it</button></div>`;
  return `<div class="card draft" id="draft" style="margin-top:14px"><h3>Draft email · ${esc(({ pending: "to review", edited: "edited by you", approved: "approved", done: "done", rejected: "rejected" })[dec] || dec)}</h3>
    <label for="subj">Subject</label><input type="text" id="subj" value="${esc(e.subject)}" readonly>
    <label for="body">Email</label><textarea id="body" readonly>${esc(e.body)}</textarea><div class="tiny muted wc" id="wc"></div>${note}
    <div class="actions" id="act-view"><button class="btn primary" id="b-approve" ${off}>Approve</button><button class="btn" id="b-edit" ${off}>Edit</button><button class="btn danger" id="b-reject" ${off}>Reject</button>${dec === "approved" ? `<button class="btn" id="b-done" ${off}>Mark as done (I sent it)</button>` : ""}</div>
    <div class="actions" id="act-edit" hidden><button class="btn primary" id="b-save">Save edit</button><button class="btn" id="b-cancel">Cancel</button></div>
    <div class="muted tiny" style="margin-top:10px">These buttons only record your decision. They send nothing. Saving an edit also updates the Gmail draft.${S.me.can_edit ? "" : " Viewing only: enter the access code on the Account page to act."}</div></div>`;
}
const wcount = t => (t.trim().match(/\S+/g) || []).length;
function wireDetail(d, root) {
  const R = $(root), id = d.company.id;
  const act = async (path, body, ok) => { const r = await api(`/api/company/${id}/${path}`, body || {}); if (!r.ok) return fail(r); S.detail = r.data; if (ok || r.data.message) toast(r.data.message || ok);
    R.innerHTML = renderDetail(r.data); wireDetail(r.data, root); refreshChrome(); if (parseHash().page === "drafts") refreshCards(); };
  R.querySelectorAll("[data-fb]").forEach(b => b.onclick = () => act("contact-feedback", { correct: b.dataset.fb === "1" }, "Thanks, saved."));
  const dr = $("#draft", R); if (!dr) return;
  const subj = $("#subj", R), body = $("#body", R), w = $("#wc", R);
  const fit = () => { body.style.height = "auto"; body.style.height = (body.scrollHeight + 4) + "px"; };
  const upd = () => { const n = wcount(body.value); w.textContent = `${n} words (target ${S.cfg.words[0]}–${S.cfg.words[1]})`; w.classList.toggle("off", n < S.cfg.words[0] - 10 || n > S.cfg.words[1] + 10); };
  upd(); fit(); body.oninput = () => { upd(); fit(); };
  const setEdit = on => { S.editing = on; subj.readOnly = body.readOnly = !on; dr.classList.toggle("editing", on); $("#act-view", R).hidden = on; $("#act-edit", R).hidden = !on; if (on) { body.focus(); fit(); } };
  const q = id2 => $(id2, R);
  if (q("#b-edit")) q("#b-edit").onclick = () => setEdit(true);
  q("#b-cancel").onclick = () => { subj.value = d.email.subject; body.value = d.email.body; upd(); fit(); setEdit(false); };
  q("#b-save").onclick = async () => { const r = await api(`/api/company/${id}/edit`, { subject: subj.value, body: body.value }); if (!r.ok) return fail(r); S.editing = false;
    if (r.data.gmail_error) toast("Saved here, but Gmail could not be updated: " + r.data.gmail_error, 9000); else toast("Saved. The Gmail draft was updated to match.");
    R.innerHTML = renderDetail(r.data); wireDetail(r.data, root); refreshChrome(); refreshCards(); };
  if (q("#b-approve")) q("#b-approve").onclick = () => act("approve", {}, "Approved. Nothing was sent.");
  if (q("#b-reject")) q("#b-reject").onclick = () => { const why = prompt("Why are you rejecting this draft? (optional)"); if (why !== null) act("reject", { reason: why }, "Rejected."); };
  if (q("#b-done")) q("#b-done").onclick = () => act("done", {}, "Marked as done.");
  if (q("#b-restore")) q("#b-restore").onclick = () => act("restore", {});
}
async function refreshCards() { const { params } = parseHash(); if (parseHash().page !== "drafts") return; const keep = S.sel; await pageDrafts({ ...params, id: keep }); }

// ------------------------------------------------------------------ Rejected
async function pageRejected(p) {
  const qs = new URLSearchParams({ ...(p.status ? { status: p.status } : {}), window: p.window || "all" }).toString();
  const r = await api(`/api/rejected?${qs}`); if (!r.ok) throw new Error("rejected"); const d = r.data;
  const opts = Object.entries(d.counts).map(([k, n]) => `<option value="${k}" ${p.status === k ? "selected" : ""}>${esc((STATUS_LABEL[k] || [k])[0])} (${n})</option>`).join("");
  $("#main").innerHTML = `<h1 class="page-title">Rejected</h1><p class="page-sub">Companies that did not fit, with the reason. Overrule one if you disagree.</p>
    <div class="grid2" style="max-width:560px;margin-bottom:18px"><label class="small muted">Why<select class="f" id="f-status"><option value="">Any</option>${opts}</select></label>
      <label class="small muted">Announced<select class="f" id="f-window"><option value="all">Any</option><option value="24h" ${p.window === "24h" ? "selected" : ""}>Last 24 hours</option><option value="7d" ${p.window === "7d" ? "selected" : ""}>Last 7 days</option></select></label></div>
    ${d.items.length ? d.items.map(i => `<div class="card" data-id="${i.id}"><div class="head"><div><b style="font-size:17px">${esc(i.name)}</b> ${badge(i.status)} <span class="muted small">${esc(i.domain || "website not confirmed")}${i.funding_stage ? " · " + esc(i.funding_stage) : ""}${i.announced ? " · announced " + esc(day(i.announced)) : ""}</span>
        <div style="margin-top:6px">${esc(i.reason || "")}</div>${i.headline ? `<div class="muted small" style="margin-top:4px">${link(i.source_url, i.headline)}${i.source ? " · " + esc(i.source) : ""}</div>` : ""}</div>
      <div style="text-align:right">${i.score != null ? `<div class="badge t-neutral">${i.score}/100</div>` : ""}<div style="margin-top:8px"><button class="btn sm" data-open="${i.id}">See why</button> ${i.overrule_allowed ? `<button class="btn sm" data-over="${i.id}" ${needCode()}>Overrule</button>` : ""}</div></div></div><div class="over" hidden></div></div>`).join("")
      : `<div class="card empty"><h3>Nothing has been rejected yet</h3><div class="muted">Companies that do not fit appear here, with the reason.</div></div>`}`;
  const f = () => go("rejected", { ...($("#f-status").value ? { status: $("#f-status").value } : {}), window: $("#f-window").value });
  $("#f-status").onchange = f; $("#f-window").onchange = f;
  document.querySelectorAll("[data-open]").forEach(a => a.onclick = () => openCompanyDialog(Number(a.dataset.open)));
  document.querySelectorAll("[data-over]").forEach(b => b.onclick = () => { const card = b.closest(".card"), box = $(".over", card); box.hidden = false;
    box.innerHTML = `<div class="note"><b>Overrule moves this company to the review queue.</b> It uses one Hunter lookup and takes about two minutes. Why do you disagree? (optional)<textarea class="f" style="margin-top:8px;min-height:60px"></textarea><div class="actions"><button class="btn primary sm" id="go-over">Overrule and continue</button><button class="btn sm" id="no-over">Cancel</button></div></div>`;
    $("#no-over", box).onclick = () => box.hidden = true;
    $("#go-over", box).onclick = async () => { $("#go-over", box).disabled = true; const r2 = await api(`/api/company/${b.dataset.over}/overrule`, { note: $("textarea", box).value }); if (!r2.ok) { fail(r2); return route(); }
      toast("Overruled. Writing the draft now (about two minutes)…", 8000); await drive([Number(b.dataset.over)], 1); go("drafts", { id: b.dataset.over }); }; });
}
async function openCompanyDialog(id) { const dlg = $("#dlg"); dlg.innerHTML = `<div id="dlgbody"><div class="muted">Loading…</div></div><div class="actions"><button class="btn" onclick="document.getElementById('dlg').close()">Close</button></div>`; dlg.showModal(); await loadDetail(id, "#dlgbody"); }

// ------------------------------------------------------------------ Sources
async function pageSources(p) {
  const qs = new URLSearchParams({ ...(p.query ? { query: p.query } : {}), ...(p.state ? { state: p.state } : {}) }).toString();
  const r = await api(`/api/sources?${qs}`); if (!r.ok) throw new Error("sources"); const d = r.data;
  if (!d.run) { $("#main").innerHTML = `<h1 class="page-title">Sources</h1><div class="card empty"><h3>No articles yet</h3><div class="muted">Press Run on the Today page.</div></div>`; return; }
  const BT = { drafted: "success", rejected: "neutral", researching: "warning", error: "danger", no_website: "warning", shortlisted: "warning" };
  $("#main").innerHTML = `<h1 class="page-title">Sources</h1><p class="page-sub">Where the articles are read. Every article the latest run looked at, and what happened to it.</p>
    <div class="card"><table><tr><th>Search (Google News)</th><th>Articles read</th><th>Named a company</th><th>What happened</th></tr>${d.feeds.map(f => `<tr><td>${esc(f.query)}</td><td>${f.articles}</td><td>${f.named}</td><td>${Object.entries(f.states).map(([k, n]) => `<span class="badge t-${BT[k] || "neutral"}" style="margin:2px">${esc((d.outcomes[k] || {}).label || k)} ${n}</span>`).join("")}</td></tr>`).join("")}</table></div>
    <p class="muted small">Google News is read through its public feed, gently (one request every 2 seconds, cached for a day). The AI reads every headline whether or not it looks like funding news.</p>
    <div class="grid2" style="max-width:640px;margin:14px 0"><label class="small muted">Search<select class="f" id="s-query"><option value="">All</option>${d.feeds.map(f => `<option value="${esc(f.query)}" ${p.query === f.query ? "selected" : ""}>${esc(f.query.slice(0, 60))}</option>`).join("")}</select></label>
      <label class="small muted">What happened<select class="f" id="s-state"><option value="">All</option>${Object.entries(d.outcomes).map(([k, o]) => `<option value="${k}" ${p.state === k ? "selected" : ""}>${esc(o.label)} (${o.count})</option>`).join("")}</select></label></div>
    <div class="card" style="padding:6px 14px">${d.items.length ? `<table><tr><th>Article</th><th>Published</th><th>Company</th><th>Website</th><th>What happened</th></tr>${d.items.map(i => `<tr><td>${link(i.source_url, i.headline)}<div class="tiny muted">${esc(i.source_name || "")}</div></td><td>${esc(day(i.published_at))}</td><td>${esc(i.company_name || "–")}</td>
      <td>${i.domain_status === "confirmed" ? esc(i.company_domain) : "<span class='muted'>unknown</span>"}</td><td><span class="badge t-${BT[i.state] || "neutral"}">${esc(i.state_label)}</span>${i.outcome_note ? `<div class="tiny muted">${esc(i.outcome_note)}</div>` : ""}</td></tr>`).join("")}</table>` : `<div class="empty"><h3>No articles match these filters</h3><div class="muted">Change a filter to see more.</div></div>`}</div>
    <p class="muted small">Showing ${d.shown} of ${d.total} articles in this run.</p>`;
  const f = () => go("sources", { ...($("#s-query").value ? { query: $("#s-query").value } : {}), ...($("#s-state").value ? { state: $("#s-state").value } : {}) });
  $("#s-query").onchange = f; $("#s-state").onchange = f;
}

// ------------------------------------------------------------------ Runs and health
async function pageRuns() {
  const r = await api("/api/runs"); if (!r.ok) throw new Error("runs"); const d = r.data, h = d.health, m = d.metrics, k = m.kill_checks, t = m.targets;
  const hc = (title, ok, text) => `<div class="card"><h3>${esc(title)}</h3><div><span class="badge t-${ok === true ? "success" : ok === false ? "danger" : "neutral"}">${ok === true ? "OK" : ok === false ? "Needs attention" : "Info"}</span></div><div class="small muted" style="margin-top:8px">${esc(text)}</div></div>`;
  const flag = (v, bad) => v === null ? `<span class="badge t-neutral">not enough data (needs ${k.min_sample})</span>` : v ? `<span class="badge t-danger">${bad}</span>` : `<span class="badge t-success">OK</span>`;
  const n = (v, u = "") => v == null ? "not enough data" : v + u;
  $("#main").innerHTML = `<h1 class="page-title">Runs &amp; health</h1><p class="page-sub">What each run did, and whether everything it depends on is working.</p>
    <div class="grid2">${hc("Gmail Drafts", h.gmail === "ok", h.gmail === "ok" ? "The sign-in works. While the Google app is in Testing mode it expires after 7 days: run scripts/gmail_authorize.py again." : h.gmail)}
      ${hc("Hunter", h.hunter_left == null ? null : h.hunter_left > 4, h.hunter_left == null ? "Could not check." : `${h.hunter_left} lookups left this month.`)}
      ${hc("Gemini", h.gemini_key, h.gemini_key ? `Model ${h.model}.` : "No Gemini key set.")}${hc("Database (Neon)", h.database_ms < 3000, `${h.database_ms} ms to answer.`)}
      ${hc("Rules", true, `Rubric ${h.rubric}. Context file ${h.context || "missing"}.`)}${hc("Access code", h.access_code_set, h.access_code_set ? "Set. Only people with the code can run or change things." : "Not set: anyone with the link can run and edit.")}</div>
    <div class="card" style="margin-top:18px"><h3>Run history</h3>${d.runs.length ? `<table><tr><th>Started</th><th>Articles</th><th>Companies</th><th>Drafts</th><th>Errors</th><th>Hunter</th><th>Rules</th><th>Status</th></tr>${d.runs.map(x => `<tr><td>${esc(ago(x.started_at))}<div class="tiny muted">${esc(String(x.started_at).slice(0, 16).replace("T", " "))} · ${esc((x.params || {}).trigger || "")}</div></td><td>${x.articles}</td><td>${x.companies}${x.unfinished ? ` <span class="badge t-warning">${x.unfinished} unfinished</span>` : ""}</td><td>${x.drafts}</td><td>${x.errors || "–"}</td><td>${x.hunter_calls} calls${x.hunter_paused ? `<div class="tiny" style="color:var(--warning-fg)">${esc(x.hunter_paused)}</div>` : ""}</td><td class="tiny">${esc(x.version)}</td><td><span class="badge t-${x.unfinished ? "warning" : "neutral"}">${esc(x.status.toLowerCase())}</span></td></tr>`).join("")}</table>` : `<div class="muted">No runs yet.</div>`}</div>
    <div class="card"><h3>Scorecard</h3><div class="kv">
      <span class="k">Your review time per company (target under ${t.minutes_per_company} min)</span><b>${m.review_seconds_avg == null ? "none yet" : (m.review_seconds_avg / 60).toFixed(1) + " min"}</b>
      <span class="k">Pipeline time per company (runs unattended)</span><b>${m.pipeline_seconds_avg == null ? "none yet" : (m.pipeline_seconds_avg / 60).toFixed(1) + " min"}</b>
      <span class="k">Drafts you edited or rejected (target under ${t.rewrite_rate_pct}%, stop above 50%)</span><b>${n(m.rewrite_rate_pct, "%")}</b>
      <span class="k">Contact accuracy, your Yes/No verdicts (stop below ${t.contact_accuracy_pct}%)</span><b>${n(m.contact_accuracy_pct, "%")} (${m.contact_labelled} judged)</b>
      <span class="k">Gemini cost per company if on a paid plan</span><b>${m.gemini_cost_per_company_usd_if_paid == null ? "unknown" : "$" + m.gemini_cost_per_company_usd_if_paid}</b>
      <span class="k">Hunter credits spent</span><b>${m.hunter_credits_spent}</b><span class="k">Research limit reached before a decision</span><b>${n(m.research_limit_hit_rate_pct, "%")}</b><span class="k">Hunter lookups paused by a limit</span><b>${m.hunter_limit_hits}</b></div>
      <h3 style="margin-top:16px">Shut-down checks</h3><div class="kv"><span class="k">You rewrite more than 50% of emails</span>${flag(k.rewrites_over_50pct, "Stop")}<span class="k">Contact accuracy below 60%</span>${flag(k.contact_accuracy_under_60pct, "Stop")}<span class="k">Review takes longer than ${t.baseline_minutes} minutes (the manual baseline)</span>${flag(k.takes_more_time_than_it_saves, "Stop")}</div></div>
    <div class="card"><h3>Warnings and errors</h3>${d.problems.length ? `<table>${d.problems.map(x => `<tr><td class="tiny">${esc(ago(x.at))}</td><td><span class="badge t-${x.level === "error" ? "danger" : "warning"}">${esc(x.level)}</span></td><td>${esc(x.event)}</td><td class="tiny muted">${esc(JSON.stringify(x.detail || {}).slice(0, 160))}</td></tr>`).join("")}</table>` : `<div class="muted">Nothing to report.</div>`}</div>`;
}

// ------------------------------------------------------------------ Settings (rubric editor)
async function pageSettings() {
  const r = await api("/api/settings"); if (!r.ok) throw new Error("settings"); const d = r.data; let kw = [...d.shortlist.signal_keywords];
  const num = (grp, key, o) => `<div class="rowform"><label for="${grp}-${key}">${esc(o.label)}</label><input type="number" class="f" id="${grp}-${key}" data-g="${grp}" data-k="${key}" value="${o.value}" min="${o.min}" max="${o.max}" step="any" ${needCode()}></div>`;
  $("#main").innerHTML = `<h1 class="page-title">Settings</h1><p class="page-sub">How startups are scored and shortlisted. You own the rubric. Every save makes a new version, and it applies from the next run. Current: <b>${esc(d.version)}</b></p>
    ${S.me.can_edit ? "" : `<div class="note warn">Viewing only. Changing settings needs the access code (Account page).</div>`}
    <div class="card"><h3>Five checks. Each has a weight. Together they make the 100 points.</h3>${d.weights.map(w => `<div class="rowform"><label for="w-${w.key}">${esc(w.label)}</label><input type="number" class="f" id="w-${w.key}" data-w="${w.key}" value="${w.weight}" min="0" max="100" ${needCode()}></div>`).join("")}<div class="rowform"><b>Total</b><b id="wtotal"></b></div></div>
    <div class="card"><h3>Scores needed</h3><div class="small muted">A company at or above the score is researched further and drafted. Below it goes to Rejected, where you can still overrule it.</div>${ordered(d.thresholds, T_ORDER).map(([k, o]) => num("t", k, o)).join("")}</div>
    <div class="card"><h3>Limits</h3><div class="small muted">Keep each run fast and inside your Gemini and Hunter quotas. Hunter's free plan allows 50 a month.</div>${ordered(d.limits, L_ORDER).map(([k, o]) => num("l", k, o)).join("")}</div>
    <div class="card"><h3>Shortlist</h3><div class="small muted">Plain rules, no AI. A headline naming Series A–C scores points; a stage outside A–C is dropped; sector words add points. Remove a word with ×.</div>${ordered(d.shortlist.numbers, P_ORDER).map(([k, o]) => num("s", k, o)).join("")}
      <div style="margin-top:10px"><div class="small muted">Sector words</div><div id="kw"></div><div class="actions"><input type="text" class="f" id="kw-new" placeholder="Add a word" style="max-width:220px" ${needCode()}><button class="btn sm" id="kw-add" ${needCode()}>Add</button></div></div></div>
    <div class="card"><h3>Google News searches</h3><div class="small muted">One search per line. {year} becomes the current year. These are your "sources".</div><textarea class="f" id="queries" ${needCode()}>${esc(d.queries.join("\n"))}</textarea></div>
    <div class="card"><h3>Approved Sprinto capabilities</h3><div class="small muted">An email may name exactly one of these. One per line.</div><textarea class="f" id="caps" ${needCode()}>${esc(d.capabilities.join("\n"))}</textarea></div>
    <div class="actions"><button class="btn primary" id="save" ${needCode()}>Save as a new version</button><span class="muted small" id="save-msg" style="align-self:center"></span></div>
    <details style="margin-top:22px"><summary>Fixed by the rubric (shown, not editable here)</summary><div class="inner"><div class="dim"><b>Gates</b> (all required)${d.read_only.gates.map(g => `<div class="small">${esc(g.label)}: <span class="muted">${esc(JSON.stringify(g.rule))}</span></div>`).join("")}</div>
      <div class="dim"><b>Contact role order</b><div class="small">${d.read_only.role_order.map(x => esc(ROLE[x] || x)).join(" → ")}</div></div>
      <div class="dim"><b>Phrases an email may never contain</b><div class="small muted">${d.read_only.banned_phrases.map(esc).join(" · ")}</div></div>
      ${Object.entries(d.read_only.levels).map(([k, lv]) => `<div class="dim"><b>${esc(DIM[k] || k)}</b>${Object.entries(lv).map(([n2, txt]) => `<div class="small">Level ${n2}: <span class="muted">${esc(txt)}</span></div>`).join("")}</div>`).join("")}</div></details>
    <details><summary>Earlier versions</summary><div class="inner">${d.versions.map(v => `<div class="dim small"><b>${esc(v.version)}</b>${v.is_active ? ` <span class="badge t-success">active</span>` : ""}<div class="muted tiny">${esc(v.source)} · ${esc(day(v.created_at))}</div></div>`).join("")}</div></details>`;
  const drawKw = () => { $("#kw").innerHTML = kw.map((w, i) => `<span class="tag">${esc(w)}<button data-i="${i}" aria-label="Remove ${esc(w)}" ${needCode()}>×</button></span>`).join(""); document.querySelectorAll("#kw button").forEach(b => b.onclick = () => { kw.splice(Number(b.dataset.i), 1); drawKw(); }); };
  drawKw();
  $("#kw-add").onclick = () => { const v = $("#kw-new").value.trim().toLowerCase(); if (v && !kw.includes(v)) kw.push(v); $("#kw-new").value = ""; drawKw(); };
  const tot = () => { const s = [...document.querySelectorAll("[data-w]")].reduce((a, i) => a + Number(i.value || 0), 0); $("#wtotal").textContent = s + " / 100"; $("#wtotal").style.color = s === 100 ? "var(--success-fg)" : "var(--danger-fg)"; return s; };
  document.querySelectorAll("[data-w]").forEach(i => i.oninput = tot); tot();
  $("#save").onclick = async () => {
    if (tot() !== 100) return toast("The five weights must add up to 100.");
    const grp = g => Object.fromEntries([...document.querySelectorAll(`[data-g="${g}"]`)].map(i => [i.dataset.k, Number(i.value)]));
    const body = { weights: Object.fromEntries([...document.querySelectorAll("[data-w]")].map(i => [i.dataset.w, Number(i.value)])), thresholds: grp("t"), limits: grp("l"), shortlist_numbers: grp("s"), signal_keywords: kw,
                   queries: $("#queries").value.split("\n"), capabilities: $("#caps").value.split("\n") };
    const r2 = await api("/api/settings", body); if (!r2.ok) return fail(r2); toast(`Saved as ${r2.data.saved_as}. It applies from the next run.`, 7000); route();
  };
}

// ------------------------------------------------------------------ Account
async function pageAccount() {
  const me = await api("/api/me"); S.me = me.data;
  $("#main").innerHTML = `<h1 class="page-title">Account</h1><p class="page-sub">Who is using the desk, and what they can do.</p>
    <div class="card"><div class="who"><div><span class="avatar" style="width:38px;height:38px;font-size:18px">${esc((S.me.name || "?")[0].toUpperCase())}</span><b style="font-size:18px">${esc(S.me.name)}</b><div class="muted small" style="margin-left:46px;margin-top:-6px">Reviews the drafts. Sends nothing: you send from Gmail yourself.</div></div>
      <span class="badge t-${S.me.can_edit ? "success" : "neutral"}">${S.me.can_edit ? (S.me.code_required ? "Can run and edit" : "Open access (no code set)") : "Viewing only"}</span></div></div>
    <div class="card"><h3>Access</h3><p class="small muted" style="margin-top:0">Everyone with the link can look at the desk. Entering the access code lets you run, approve, edit, reject, overrule and save settings. Prospect email addresses are shown masked to viewers.</p>
      ${S.me.code_required ? (S.me.can_edit ? `<button class="btn" id="logout">Lock the desk again</button>` : `<div class="actions" style="margin-top:0"><input type="password" class="f" id="code" placeholder="Access code" style="max-width:260px"><button class="btn primary" id="login">Unlock</button></div><div class="small" id="acc-msg" style="color:var(--danger-fg);margin-top:8px"></div>`) : `<div class="note">No access code is set, so everyone can edit. Set ACCESS_CODE to protect the desk.</div>`}</div>`;
  const lg = $("#login"); if (lg) { const go2 = async () => { const r = await api("/api/login", { code: $("#code").value }); if (!r.ok) { $("#acc-msg").textContent = r.data.error; return; } S.me = r.data; toast("Unlocked."); pageAccount(); }; lg.onclick = go2; $("#code").onkeydown = e => e.key === "Enter" && go2(); }
  const lo = $("#logout"); if (lo) lo.onclick = async () => { await api("/api/logout", {}); const m = await api("/api/me"); S.me = m.data; toast("Locked."); pageAccount(); };
}

// ------------------------------------------------------------------ start
(async function init() {
  applyTheme();
  const [me, cfg] = await Promise.all([api("/api/me"), api("/api/config")]);
  if (me.ok) { S.me = me.data; $("#avatar").textContent = (S.me.name || "?")[0].toUpperCase(); }
  if (cfg.ok) S.cfg = cfg.data;
  await route(); refreshChrome();
  setInterval(() => { if (!S.driving && !S.editing && parseHash().page !== "settings") refreshChrome(); }, 30000);
})();
