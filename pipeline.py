#!/usr/bin/env python3
"""Sprinto cold-outreach pipeline (draft-first).

    python3 pipeline.py domains.txt                 # research + draft, saves everything, sends nothing
    
Rules (scores, gates, thresholds) are in rubric.py. Code adds the scores and applies the gates.
The AI only reads and writes. Every company's evidence, scores, retries, status and escalation reason
are saved in output/<domain>/state.json. One company failing never stops the batch.
LEGACY first draft (file-based). The live flow is research.py + outreach.py on Neon. This software never sends mail."""
import os, sys, re, json, time, hashlib, argparse, csv, datetime as dt
from email.utils import parsedate_to_datetime
from urllib.parse import urljoin, urlparse
import xml.etree.ElementTree as ET
import requests
from bs4 import BeautifulSoup
import rubric as R
import settings as S

env, today, OUT = S.env, S.today, S.OUT

def log(*a):
    print(*a, flush=True)

def norm(s):
    return re.sub(r"\W+", " ", (s or "").lower()).strip()

def ukey(u):
    return (u or "").split("#")[0].rstrip("/")

# ====================================================================== fetching (free sources first)
UA = {"User-Agent": "Mozilla/5.0 (compatible; SprintoResearchDemo/0.2)"}
TYPE_KEYS = [("security", ["security", "trust", "compliance", "soc2", "soc-2", "iso27001", "privacy"]),
             ("customers", ["customer", "case-stud", "casestud", "stories", "success"]),
             ("product", ["product", "platform", "solution", "feature", "how-it-works", "docs", "documentation"]),
             ("careers", ["career", "jobs", "join-us", "join"]),
             ("news", ["news", "press", "blog", "announce"]),
             ("about", ["about", "company", "team", "leadership"])]
GUESS_PATHS = {"security": ["/security", "/trust", "/compliance", "/trust-center"], "customers": ["/customers", "/case-studies", "/customer-stories"],
               "product": ["/product", "/platform", "/solutions", "/features"], "careers": ["/careers", "/jobs"], "about": ["/about", "/company", "/about-us"]}
JOB_HOSTS = ("greenhouse.io", "lever.co", "ashbyhq.com", "workable.com")
NEVER_FETCH_HOSTS = ("linkedin.com",)                       # brief: never scrape LinkedIn
LOGIN_HINT = re.compile(r"/(login|log-in|signin|sign-in|sign_in|signup|sign-up|auth|sso|account|dashboard|app)(/|$)", re.I)

def host_matches(host, domain):
    host = host.lower().split(":")[0]
    return host == domain or host.endswith("." + domain)

def fetch_allowed(url):
    u = urlparse(url)
    if u.scheme not in ("http", "https"):
        return False
    if any(host_matches(u.netloc, h) for h in NEVER_FETCH_HOSTS):
        return False
    if LOGIN_HINT.search(u.path):                           # never touch login-protected areas
        return False
    return True

def http_get(url, timeout=15):
    """Returns (label, response, attempts). label: ok | blocked | failed. Max 3 attempts, only for transient errors."""
    for attempt in range(1, R.MAX_READ_ATTEMPTS + 1):
        try:
            r = requests.get(url, headers=UA, timeout=timeout, allow_redirects=True)
            if r.status_code in (401, 403):
                return "blocked", None, attempt            # retrieval failure, not a poor fit; retrying will not help
            if r.status_code == 429 or r.status_code >= 500:
                time.sleep(attempt)                          # transient: short back-off, then try again
                continue
            if r.status_code >= 400:
                return "failed", None, attempt               # 404 etc.: do not retry unchanged
            if "html" not in r.headers.get("content-type", "html").lower() and "xml" not in r.headers.get("content-type", "").lower():
                return "failed", None, attempt
            return "ok", r, attempt
        except requests.RequestException:
            time.sleep(attempt)
    return "failed", None, R.MAX_READ_ATTEMPTS

def extract_text(html):
    """Clean page text. Keeps title + meta description (they carry the product pitch)."""
    soup = BeautifulSoup(html, "html.parser")
    head = []
    if soup.title and soup.title.string:
        head.append(soup.title.string.strip())
    for m in soup.find_all("meta"):
        if (m.get("name") or m.get("property") or "").lower() in ("description", "og:description") and m.get("content"):
            head.append(m["content"].strip())
    site = soup.find("meta", attrs={"property": "og:site_name"})
    site_name = site.get("content", "").strip() if site else ""
    for t in soup(["script", "style", "noscript", "svg", "nav", "footer", "form"]):
        t.decompose()
    body = re.sub(r"\s+", " ", soup.get_text(" ", strip=True))
    text = (" | ".join(head) + " | " if head else "") + body
    return text[: R.MAX_CHARS_PER_SOURCE], site_name

def fetch_page(url):
    """Cached for S.PAGE_CACHE_DAYS days. A page is never refetched inside that window without a reason."""
    ck = "page_" + hashlib.md5(url.encode()).hexdigest()
    res = S.kv_get("cache", ck)
    if res:
        try:
            if (dt.date.today() - dt.date.fromisoformat(res["retrieved_at"])).days <= S.PAGE_CACHE_DAYS:
                res["from_cache"] = True
                return res
        except (KeyError, ValueError):
            pass
    label, r, attempts = http_get(url)
    res = {"url": url, "status": label, "text": "", "links": [], "attempts": attempts,
           "retrieved_at": today(), "site_name": "", "from_cache": False}
    if r is not None:
        res["url"] = r.url
        soup = BeautifulSoup(r.text, "html.parser")
        res["links"] = [[urljoin(r.url, a["href"]), a.get_text(" ", strip=True)] for a in soup.find_all("a", href=True)][:300]
        res["text"], res["site_name"] = extract_text(r.text)
        S.kv_put("cache", ck, res)
    return res

def fetch_news(domain, brand):
    """Free source: Google News RSS about THIS company's funding. One source entry per headline,
    keyed by the headline's own link so every excerpt has a clickable source."""
    q = requests.utils.quote(f'("{brand}" OR "{domain}") (funding OR raises OR "Series A" OR "Series B" OR "Series C")')
    label, r, _ = http_get(f"https://news.google.com/rss/search?q={q}&hl=en-US&gl=US&ceid=US:en")
    if r is None:
        return []
    try:
        items = ET.fromstring(r.content).findall("./channel/item")[:8]
    except ET.ParseError:
        return []
    out = []
    for i in items:
        link, title = i.findtext("link"), i.findtext("title")
        if not link or not title:
            continue
        try:
            date = parsedate_to_datetime(i.findtext("pubDate")).date().isoformat()
        except (TypeError, ValueError):
            date = "date unknown"
        out.append({"url": link, "text": f"{date} | {title} | publisher: {i.findtext('source') or 'unknown'}"})
    return out

def classify(href, anchor):
    s = (urlparse(href).path + " " + (anchor or "")).lower()
    for t, keys in TYPE_KEYS:
        if any(k in s for k in keys):
            return t
    return None

def candidates_from(domain, links):
    out, seen = [], set()
    for href, anchor in links:
        u = urlparse(href)
        if re.search(r"\.(pdf|png|jpg|jpeg|svg|zip|gif)$", u.path.lower()) or not fetch_allowed(href):
            continue
        same = host_matches(u.netloc, domain)
        t = classify(href, anchor)
        if not t or not (same or (t == "careers" and any(host_matches(u.netloc, h) for h in JOB_HOSTS))):
            continue
        key = (u.netloc.lower(), u.path.rstrip("/"))
        if key in seen or u.path in ("", "/"):
            continue
        seen.add(key)
        out.append({"url": href.split("#")[0], "type": t})
    # if the homepage links to no page of some type, add the usual path as a fallback (a 404 costs one read, no retry)
    have = {c["type"] for c in out}
    for t, paths in GUESS_PATHS.items():
        if t not in have:
            out.extend({"url": f"https://{domain}{p}", "type": t, "guessed": True} for p in paths)     # the next-best address is tried if one is a 404
    return out

def pick_pages(cands, fetched, wanted_types, n):
    chosen = []
    taken = lambda u: u in fetched or u in [x["url"] for x in chosen]
    for t in wanted_types:
        for c in cands:
            if c["type"] == t and not taken(c["url"]):
                chosen.append(c)
                break
        if len(chosen) >= n:
            return chosen
    for c in cands:   # fill remaining slots with any new page
        if len(chosen) >= n:
            break
        if not taken(c["url"]):
            chosen.append(c)
    return chosen[:n]

# ====================================================================== Gemini (AI reads and writes only)
def gemini_json(prompt, usage):
    """Returns parsed JSON. usage is the per-company counter dict (calls, retries, tokens)."""
    key, model = env("GEMINI_API_KEY"), env("GEMINI_MODEL", "gemini-2.5-flash")
    if not key:
        raise RuntimeError("GEMINI_API_KEY is empty in .env")
    url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"
    text, last = prompt, None
    level = env("GEMINI_THINKING_LEVEL", "low")                 # measured on a real company read: 35 s -> 11 s, identical scores
    for attempt in range(1, 4):
        body = {"contents": [{"parts": [{"text": text}]}],
                "generationConfig": {"temperature": 0, "responseMimeType": "application/json", "maxOutputTokens": 8192}}
        if level and level != "default":
            body["generationConfig"]["thinkingConfig"] = {"thinkingLevel": level}
        usage["calls"] = usage.get("calls", 0) + 1
        if attempt > 1:
            usage["retries"] = usage.get("retries", 0) + 1
        try:
            r = requests.post(url, headers={"x-goog-api-key": key}, json=body, timeout=120)
            if r.status_code in (429, 500, 502, 503, 504):
                last = f"HTTP {r.status_code}"
                time.sleep(5 * attempt * attempt)
                continue
            if r.status_code == 400 and level and "thinking" in r.text.lower():
                level = None                                    # this model does not support that setting: ask again without it
                usage["calls"] -= 1
                continue
            if r.status_code in (400, 401, 403, 404):
                raise RuntimeError(f"Gemini error {r.status_code}: {r.json().get('error', {}).get('message', '')[:160]} "
                                   "(check GEMINI_API_KEY / GEMINI_MODEL)")
            j = r.json()
            um = j.get("usageMetadata", {})
            usage["prompt_tokens"] = usage.get("prompt_tokens", 0) + um.get("promptTokenCount", 0)
            usage["output_tokens"] = usage.get("output_tokens", 0) + um.get("candidatesTokenCount", 0)
            usage["thought_tokens"] = usage.get("thought_tokens", 0) + um.get("thoughtsTokenCount", 0)
            usage["model"] = model
            if j.get("promptFeedback", {}).get("blockReason"):
                raise RuntimeError(f"Gemini blocked the request: {j['promptFeedback']['blockReason']}")
            cand = j["candidates"][0]
            txt = "".join(p.get("text", "") for p in cand["content"]["parts"] if not p.get("thought")).strip()
            txt = re.sub(r"^```(?:json)?|```$", "", txt).strip()
            if cand.get("finishReason") == "MAX_TOKENS":
                raise ValueError("answer cut off at the token limit")
            return json.loads(txt)
        except (json.JSONDecodeError, KeyError, IndexError, ValueError) as e:
            last = f"bad answer: {type(e).__name__}"
            text = prompt + "\nReturn ONE complete, valid JSON object only. Keep reasoning fields short."
        except requests.RequestException as e:
            last = type(e).__name__
            time.sleep(attempt)
    raise RuntimeError(f"Gemini failed after 3 attempts: {last}")

# ====================================================================== verifying what the AI says
class Verifier:
    """An excerpt is real only if it appears word for word (ignoring punctuation/case) in the page it cites."""
    def __init__(self, sources):
        self.n = {ukey(u): norm(t) for u, t in sources.items()}

    def ok(self, url, excerpt):
        n = norm(excerpt)
        src = self.n.get(ukey(url))
        return len(n) >= 12 and src is not None and n in src

def parse_date(s):
    if not s:
        return None
    s = str(s).strip()
    for fmt in ("%Y-%m-%d", "%Y-%m", "%d %b %Y", "%d %B %Y", "%B %d, %Y", "%b %d, %Y"):
        try:
            return dt.datetime.strptime(s, fmt).date()
        except ValueError:
            continue
    try:
        return parsedate_to_datetime(s).date()
    except (TypeError, ValueError):
        return None

def clamp(v, lo, hi):
    try:
        return max(lo, min(hi, int(v)))
    except (TypeError, ValueError):
        return lo

def normalise_stage(s):
    s = (s or "unknown").lower().strip()
    if s in ("series a", "series b", "series c", "pre-seed", "seed", "public"):
        return s
    if re.match(r"series [d-z]", s) or s in ("series d+", "late stage"):
        return "series d+"
    if "ipo" in s or "publicly" in s or "public" in s:
        return "public"
    return "unknown"

def assess(domain, sources, usage):
    """One AI read of everything gathered so far, then plain-code verification of what it claimed."""
    a = gemini_json(R.assess_prompt(domain, sources, today()), usage)
    ver, notes = Verifier(sources), []
    dims = {}
    for k in R.WEIGHTS:
        raw = (a.get("dimensions") or {}).get(k) or {}
        level = clamp(raw.get("level"), 0, 2)
        ev = []
        for e in raw.get("evidence") or []:
            if isinstance(e, dict) and ver.ok(e.get("source_url"), e.get("excerpt", "")):
                ev.append({"source_url": e["source_url"], "excerpt": e["excerpt"].strip(), "dated": e.get("dated"),
                           "label": e.get("label") if e.get("label") in ("observed", "inferred") else "inferred"})   # unlabelled = the cautious label
        state = raw.get("state") if raw.get("state") in ("positive", "missing", "explicit_negative") else "missing"
        if level > 0 and not ev:
            notes.append(f"{k}: AI claimed level {level} but gave no excerpt I could find in the pages, so it counts as 0")
            level, state = 0, "missing"
        if level > 0:
            state = "positive"
        if level == 0 and state == "positive":
            state = "missing"
        if level == 0 and state == "explicit_negative" and not ev:
            state = "missing"          # "no evidence found" is not "evidence says no"
        if k == "reason_now" and level > 0:
            ages = [abs((dt.date.today() - d).days) for d in (parse_date(e.get("dated")) for e in ev) if d]
            new = level
            if level == 2 and not any(x <= R.REASON_NOW_L2_MONTHS * 30.5 for x in ages):
                new = 1
            if new == 1 and not any(x <= R.REASON_NOW_L1_MONTHS * 30.5 for x in ages):
                new = 0
            if new != level:
                notes.append(f"reason_now: dated evidence is undated or outside the window (6 months for level 2, 12 for level 1); level {level} -> {new}")
                level = new
                if level == 0:
                    state = "missing"
        if level == 0 and state != "explicit_negative" and ev:
            notes.append(f"{k}: scored 0, so its {len(ev)} attached excerpt(s) are not kept as supporting evidence")
            ev = []                                  # evidence that did not earn a point must not look like support
        dims[k] = {"level": level, "state": state, "reasoning": str(raw.get("reasoning", ""))[:400], "evidence": ev}
    a["dimensions"] = dims

    f = a.get("funding") or {}
    stage = normalise_stage(f.get("stage"))
    fe = f.get("evidence") or {}
    if stage != "unknown" and not ver.ok(fe.get("source_url"), fe.get("excerpt", "")):
        notes.append("funding stage claimed without an excerpt I could find in the pages, so it is unknown")
        stage = "unknown"
    a["funding"] = {"stage": stage, "date": f.get("date") if stage != "unknown" else None,
                    "evidence": fe if stage != "unknown" else None}

    ec = a.get("employee_count") or {}
    ee = ec.get("evidence") or {}
    a["employee_count"] = {"value": ec.get("value") if ver.ok(ee.get("source_url"), ee.get("excerpt", "")) else None,
                           "evidence": ee if ver.ok(ee.get("source_url"), ee.get("excerpt", "")) else None}

    de = a.get("disqualifier_evidence") or {}
    proven = ver.ok(de.get("source_url"), de.get("excerpt", ""))
    for flag in ("is_unrelated_business", "is_direct_compliance_platform_competitor"):
        if a.get(flag) and not proven:
            notes.append(f"AI said {flag} without proof I could find in the pages; ignored")
            a[flag] = False
    if a.get("business_model") == "not_saas" and not proven:
        notes.append("AI said not SaaS without proof I could find in the pages; treated as unclear")
        a["business_model"] = "unclear"
    a["disqualifier_evidence"] = de if proven else None
    a["conflicts"] = [{"claim": str(c.get("claim", ""))[:200], "evidence": [e for e in (c.get("evidence") or []) if isinstance(e, dict) and ver.ok(e.get("source_url"), e.get("excerpt", ""))]}
                      for c in (a.get("conflicts") or []) if isinstance(c, dict)]
    a["conflicts"] = [c for c in a["conflicts"] if len(c["evidence"]) >= 2]      # a conflict needs two verified sides
    a["missing"] = [m for m in (a.get("missing") or []) if isinstance(m, dict)]
    a["verification_notes"] = notes
    a.setdefault("company_name", domain)
    return a

# ====================================================================== decide (plain code, no AI)
def score(a):
    pts = {k: R.WEIGHTS[k] * a["dimensions"][k]["level"] / 2 for k in R.WEIGHTS}
    return pts, sum(pts.values())

def decide(a):
    """Gates (all required): Series A-C, SaaS (business fit level 2), compliance workload >= 1, total >= 65.
    Returns dict with status, reason, flags, gates, points, total. Status is one of
    QUALIFIED / DISQUALIFIED / INSUFFICIENT_EVIDENCE / LOW_PRIORITY."""
    D = a["dimensions"]
    pts, total = score(a)
    bf, cw = D["business_fit"], D["compliance_workload"]
    stage = normalise_stage(a["funding"]["stage"])       # never trust the caller to have cleaned it
    flags = ["funding_stage_unknown"] if stage == "unknown" else []
    out = {"flags": flags, "points": pts, "total": total}
    G = {}
    # --- SaaS gate
    if bf["level"] >= R.GATE_BUSINESS_FIT_LEVEL:
        G["saas"] = {"state": "passed", "detail": f"business fit level {bf['level']} (needs {R.GATE_BUSINESS_FIT_LEVEL})"}
    elif bf["state"] == "explicit_negative" or a.get("business_model") == "not_saas":
        G["saas"] = {"state": "failed", "detail": "evidence says this is not a cloud software business"}
    else:
        G["saas"] = {"state": "unknown", "detail": f"business fit level {bf['level']}, needs {R.GATE_BUSINESS_FIT_LEVEL}; evidence missing"}
    # --- funding gate
    if stage == "unknown":
        G["series"] = {"state": "unknown", "detail": "no dated source names the funding round (flagged, company continues)"}
    elif stage in R.ALLOWED_STAGES:
        G["series"] = {"state": "passed", "detail": stage.title()}
    else:
        G["series"] = {"state": "failed", "detail": f"{stage} is outside Series A-C"}
    # --- compliance gate
    if cw["level"] >= R.GATE_COMPLIANCE_MIN_LEVEL:
        G["compliance"] = {"state": "passed", "detail": f"compliance workload level {cw['level']} (needs {R.GATE_COMPLIANCE_MIN_LEVEL})"}
    elif cw["state"] == "explicit_negative":
        G["compliance"] = {"state": "failed", "detail": "evidence says no compliance workload"}
    else:
        G["compliance"] = {"state": "unknown", "detail": "no compliance evidence found yet (no badge does not mean no compliance)"}
    # --- score gate
    if total >= R.THRESHOLD_TOTAL:
        G["score"] = {"state": "passed", "detail": f"{total:.0f}/100 (needs {R.THRESHOLD_TOTAL})"}
    elif "unknown" in (G["saas"]["state"], G["compliance"]["state"]):
        G["score"] = {"state": "unknown", "detail": f"{total:.0f}/100 so far; evidence for other gates is still missing"}
    else:
        G["score"] = {"state": "failed", "detail": f"{total:.0f}/100 (needs {R.THRESHOLD_TOTAL})"}
    out["gates"] = G

    if a.get("is_unrelated_business") or a.get("is_direct_compliance_platform_competitor") or a.get("business_model") == "not_saas":
        out.update(status="DISQUALIFIED", reason="Confirmed not a fit: non-SaaS, unrelated, or a direct compliance-platform competitor.")
    elif G["saas"]["state"] == "failed":
        out.update(status="DISQUALIFIED", reason="Confirmed not SaaS: " + G["saas"]["detail"] + ".")
    elif G["series"]["state"] == "failed":
        out.update(status="DISQUALIFIED", reason=f"Funding stage is {stage}, outside Series A-C.")
    elif "unknown" in (G["saas"]["state"], G["compliance"]["state"]):
        miss = [n for n, k in (("SaaS fit", "saas"), ("compliance workload", "compliance")) if G[k]["state"] == "unknown"]
        out.update(status="INSUFFICIENT_EVIDENCE", reason="Could not confirm " + " and ".join(miss) + " from the pages read.")
    elif G["compliance"]["state"] == "failed" or total < R.THRESHOLD_TOTAL:
        out.update(status="LOW_PRIORITY", reason=f"Evidence is clear but the fit is weak: {total:.0f}/100, needs {R.THRESHOLD_TOTAL}.")
    else:
        extra = " Funding stage unknown: flagged for the Growth Lead." if stage == "unknown" else ""
        out.update(status="QUALIFIED", reason=f"Passed every gate: SaaS, compliance workload level {cw['level']}, {total:.0f}/100." + extra)
    return out

def evidence_list(a):
    out = []
    for k, d in a["dimensions"].items():
        for e in d["evidence"]:
            out.append({"dimension": k, **e})
    f = a["funding"]
    if f["stage"] != "unknown" and f.get("evidence"):
        out.append({"dimension": "funding", "source_url": f["evidence"].get("source_url"),
                    "excerpt": f["evidence"].get("excerpt"), "dated": f.get("date")})
    return out

def ev_key(e):
    return (e["dimension"], ukey(e["source_url"]), norm(e["excerpt"]))

# ====================================================================== research loop
def research_company(domain, st, usage):
    """Returns (assessment, sources) if QUALIFIED, else None. Writes the trail into st."""
    sources, fetched, pages = {}, set(), []
    st["pages"] = pages

    def read(c, rnd):
        p = fetch_page(c["url"])
        fetched.add(c["url"]); fetched.add(p["url"])
        pages.append({"url": c["url"], "type": c.get("type", "homepage"), "round": rnd, "status": p["status"],
                      "attempts": p["attempts"], "from_cache": p.get("from_cache", False),
                      "guessed_path": bool(c.get("guessed"))})
        if p["status"] == "ok":
            sources[p["url"]] = p["text"]
        return p

    home = read({"url": f"https://{domain}", "type": "homepage"}, 0)
    if home["status"] == "failed":                       # connection problem: try the www form once
        home = read({"url": f"https://www.{domain}", "type": "homepage"}, 0)
    if home["status"] != "ok":
        st["flags"] = ["retrieval_failure"]
        st.update(status="INSUFFICIENT_EVIDENCE", research_limit_hit=False,
                  reason=f"Could not read the homepage ({home['status']}). This is a retrieval failure, not a judgement on fit.")
        return None
    cands = candidates_from(domain, home["links"])
    brand = home.get("site_name") or domain.split(".")[0]
    for n in fetch_news(domain, brand):
        sources[n["url"]] = n["text"]
    first = pick_pages(cands, fetched, ["security", "customers", "product"], R.INITIAL_EXTRA_PAGES)
    for c in first:
        read(c, 0)
    a = assess(domain, sources, usage)
    d = decide(a)
    rounds = [{"round": 0, "gap": None, "source": [c["url"] for c in first] + ["homepage", "Google News headlines"],
               "new_evidence": evidence_list(a), "revised_decision": d["status"]}]
    st["rounds"] = rounds
    budget_hit = False
    for rnd in range(1, R.MAX_ROUNDS + 1):
        if d["status"] in ("QUALIFIED", "DISQUALIFIED") or (d["status"] == "LOW_PRIORITY" and not a["missing"]):
            break
        types = [m.get("page_type") for m in a["missing"] if m.get("page_type")] or ["security", "customers", "product", "careers", "news", "about"]
        chosen = pick_pages(cands, fetched, types, R.PAGES_PER_ROUND)
        if not chosen:
            rounds.append({"round": rnd, "gap": a["missing"], "source": [], "new_evidence": [],
                           "revised_decision": d["status"], "note": "no new relevant pages left to read"})
            st["research_stop"] = "no_new_pages"
            break
        before = {ev_key(e) for e in evidence_list(a)}
        gap = a["missing"]
        for c in chosen:
            read(c, rnd)
        a = assess(domain, sources, usage)
        d = decide(a)
        rounds.append({"round": rnd, "gap": gap, "source": [c["url"] for c in chosen],
                       "new_evidence": [e for e in evidence_list(a) if ev_key(e) not in before],
                       "revised_decision": d["status"]})
    else:
        if d["status"] not in ("QUALIFIED", "DISQUALIFIED"):
            budget_hit = True
            st["research_stop"] = "budget_used"
    failed = [p for p in pages if p["status"] != "ok" and not p["guessed_path"]]
    if failed:
        st.setdefault("flags", []).append("some_pages_not_readable")
    st.update(status=d["status"], reason=d["reason"], flags=sorted(set(st.get("flags", []) + d["flags"])),
              assessment=a, gates=d["gates"], points=d["points"], total=d["total"],
              sources_used=list(sources), research_limit_hit=budget_hit)
    S.kv_put("sources", domain, sources)
    return (a, sources) if d["status"] == "QUALIFIED" else None

# ====================================================================== Hunter.io (the only contact provider)
class HunterPaused(Exception):
    def __init__(self, msg, run_wide=False):
        super().__init__(msg)
        self.run_wide = run_wide

HUNTER = {"calls": 0, "paused": None, "credits_used": 0.0, "remaining_at_start": None, "_loaded": 0, "total_calls": 0}

def hunter_reset():
    HUNTER.update(calls=0, paused=None, credits_used=0.0, remaining_at_start=None, _loaded=0, total_calls=0)

def hunter_load(run_id):
    """Hosted mode: each request is a fresh process, so the per-run counters live in the database."""
    m = S.kv_get("meta", f"hunter_run_{run_id}") or {}
    HUNTER.update(calls=m.get("calls", 0), paused=m.get("paused"), credits_used=m.get("credits_used", 0.0),
                  remaining_at_start=m.get("remaining_at_start"), _loaded=m.get("calls", 0),
                  total_calls=(S.kv_get("meta", "hunter_total") or {}).get("calls", 0))

def hunter_save(run_id):
    S.kv_put("meta", f"hunter_run_{run_id}", {k: HUNTER[k] for k in ("calls", "paused", "credits_used", "remaining_at_start")})
    made = HUNTER["calls"] - HUNTER["_loaded"]
    if made:
        S.kv_put("meta", "hunter_total", {"calls": HUNTER["total_calls"] + made})

def hunter_remaining():
    """Free call (Hunter docs: GET /v2/account costs no credits)."""
    try:
        r = requests.get("https://api.hunter.io/v2/account", headers={"X-API-KEY": env("HUNTER_API_KEY", "")}, timeout=20)
        if r.ok:
            req = r.json()["data"]["requests"]
            return (req.get("credits") or req.get("searches") or {}).get("remaining")
    except (requests.RequestException, KeyError, ValueError):
        pass
    return None

def hunter_domain_search(domain):
    c = S.kv_get("cache", f"hunter_{domain}")
    if c:                                                    # every result is cached, including empty ones
        c["from_cache"] = True
        return c
    if HUNTER["paused"]:
        raise HunterPaused(HUNTER["paused"], run_wide=True)
    cap = int(env("HUNTER_MAX_CALLS_PER_RUN", "5"))
    if HUNTER["calls"] >= cap:
        HUNTER["paused"] = f"the limit of {cap} Hunter lookups per run was reached"
        raise HunterPaused(HUNTER["paused"], run_wide=True)
    if S.HOSTED:                                             # public site: a lifetime cap on fresh lookups protects your credits
        total_cap = int(env("HUNTER_MAX_CALLS_TOTAL", "10"))
        if HUNTER["total_calls"] + (HUNTER["calls"] - HUNTER["_loaded"]) >= total_cap:
            HUNTER["paused"] = f"the hosted site's cap of {total_cap} fresh Hunter lookups is used up (raise HUNTER_MAX_CALLS_TOTAL to allow more)"
            raise HunterPaused(HUNTER["paused"], run_wide=True)
    key = env("HUNTER_API_KEY")
    if not key:
        raise HunterPaused("HUNTER_API_KEY is missing in .env", run_wide=True)
    if HUNTER["remaining_at_start"] is None:
        HUNTER["remaining_at_start"] = hunter_remaining()
        if HUNTER["remaining_at_start"] is not None and HUNTER["remaining_at_start"] < 1:
            HUNTER["paused"] = "the Hunter quota is used up"
            raise HunterPaused(HUNTER["paused"], run_wide=True)
    try:
        # personal addresses only (generic info@ boxes have no named person); free plan allows limit <= 10
        r = requests.get("https://api.hunter.io/v2/domain-search", headers={"X-API-KEY": key},
                         params={"domain": domain, "limit": 10, "type": "personal"}, timeout=25)
    except requests.RequestException as e:
        raise HunterPaused(f"could not reach Hunter ({type(e).__name__})")
    HUNTER["calls"] += 1
    try:
        HUNTER["credits_used"] += float(r.headers.get("X-Credits-Used", 0) or 0)
    except ValueError:
        pass
    # Hunter docs: 401 bad key, 403 = RATE limit, 429 = USAGE (quota) limit. Opposite of the usual convention.
    if r.status_code == 401:
        HUNTER["paused"] = "Hunter rejected the API key (401)"
        raise HunterPaused(HUNTER["paused"], run_wide=True)
    if r.status_code == 429:
        HUNTER["paused"] = "the Hunter usage quota is used up (429)"
        raise HunterPaused(HUNTER["paused"], run_wide=True)
    if r.status_code == 403:
        HUNTER["paused"] = "Hunter says rate limit reached (403); wait a minute and retry"
        raise HunterPaused(HUNTER["paused"], run_wide=True)
    if r.status_code != 200:
        raise HunterPaused(f"Hunter returned HTTP {r.status_code}")
    c = {"retrieved_at": today(), "credits_used": r.headers.get("X-Credits-Used"), "data": r.json().get("data", {}), "from_cache": False}
    S.kv_put("cache", f"hunter_{domain}", {k: v for k, v in c.items() if k != "from_cache"})
    return c

# The four roles in the rubric (PDF section 5). A title counts only if it names the function AND a leadership level:
# "Head of Security" yes, "Security Engineer" no, "Director, Strategy & Operations" no.
LEADER = r"chief|ciso|\bvp\b|vice president|\bsvp\b|\bevp\b|head\b|director|\blead\b|manager|officer|principal|owner|president"
ROLE_RULES = [("compliance_grc_leader", rf"(compliance|grc|governance|risk|privacy|trust).*({LEADER})|({LEADER}).*(compliance|grc|governance|risk|privacy|trust)"),
              ("security_leader_ciso", rf"\bciso\b|(security|infosec).*({LEADER})|({LEADER}).*(security|infosec)"),
              ("cto_vp_engineering", r"\bcto\b|chief (technology|technical)|head of (software |platform |product )?(engineering|technology)|(vp|vice president|director|svp|evp)[,\s]*(of|-)?\s*(software |platform |product )?engineering\b|engineering (director|head)\b"),
              ("technical_founder_ceo", r"founder|\bceo\b|chief executive")]
ROLE_LABEL = {"compliance_grc_leader": "compliance/GRC leader", "security_leader_ciso": "security leader",
              "cto_vp_engineering": "CTO / engineering head", "technical_founder_ceo": "founder / CEO", "other": "other role"}

def classify_role(position):
    p = (position or "").lower()
    for role, rx in ROLE_RULES:
        if re.search(rx, p):
            return role
    return "other"

CONTACT_KEYS = ("company_association", "functional_ownership", "ability_to_drive_action", "match_to_need", "identity_quality")

def build_candidates(emails, domain):
    cands = []
    for e in emails:
        name = f"{e.get('first_name') or ''} {e.get('last_name') or ''}".strip()
        if not name:
            continue
        srcs = e.get("sources") or []
        cands.append({"name": name, "first_name": e.get("first_name") or name.split()[0], "position": e.get("position"),
                      "department": e.get("department"), "seniority": e.get("seniority"), "confidence": e.get("confidence"),
                      "verification": (e.get("verification") or {}).get("status"),
                      "source_count": len(srcs), "last_seen_on": max([s.get("last_seen_on") or "" for s in srcs], default="") or None,
                      "still_on_page": any(s.get("still_on_page") for s in srcs),
                      "email_domain_matches_company": (e.get("value") or "").lower().endswith("@" + domain),
                      "source_links": [s.get("uri") for s in srcs if s.get("uri")][:5],
                      "email": e.get("value")})
    return cands

def score_candidates(company, cands, usage):
    """AI scores each Hunter candidate 0-2 on five criteria; CODE clamps, adds up, applies the pass rule and the role order.
    Returns (scored, chosen_contact_or_None)."""
    hide = ("email", "first_name", "source_links")
    out = gemini_json(R.contacts_prompt(company, [{k: v for k, v in c.items() if k not in hide} for c in cands]), usage)
    ranked, scored = [], []
    for x in out.get("candidates", []):
        i = x.get("index")
        if not isinstance(i, int) or not 0 <= i < len(cands):
            continue
        s = {k: clamp((x.get("scores") or {}).get(k), 0, 2) for k in CONTACT_KEYS}
        if not cands[i].get("position"):
            s["functional_ownership"] = 0                      # no title on record = no evidence of ownership
        total = sum(s.values())
        role = classify_role(cands[i].get("position"))          # role from the title text, by code
        role_ok = (not getattr(R, "ONLY_PREFERRED_ROLES", True)) or role != "other"     # must be one of the four rubric roles
        ok = total >= R.CONTACT_PASS_TOTAL and s["company_association"] > 0 and s["functional_ownership"] > 0 and role_ok
        why = str(x.get("reasoning", ""))[:300] + ("" if role_ok else " | NOT eligible: the title is not one of the four roles the rubric prefers (compliance/GRC, security, CTO/VP Engineering, founder/CEO).")
        rec = {"index": i, "name": cands[i]["name"], "position": cands[i].get("position"), "scores": s, "total": total,
               "pass": ok, "role_category": role, "role_ok": role_ok, "reasoning": why}
        scored.append(rec)
        if ok:
            ranked.append((R.ROLE_PREFERENCE.index(role), -total, -(cands[i].get("confidence") or 0), i, rec))
    if not ranked:
        return scored, None
    ranked.sort(key=lambda t: t[:4])
    rec = ranked[0][4]
    c = dict(cands[rec["index"]])
    c.update(score=rec["total"], scores=rec["scores"], role_category=rec["role_category"], reasoning=rec["reasoning"],
             email_verified=(c.get("verification") == "valid"), index=rec["index"])
    c["why"] = (f"{c['name']}, {c.get('position')}: contact score {rec['total']}/10. Picked as the {ROLE_LABEL[rec['role_category']]}"
                f" with the best score among {len([s for s in scored if s['pass']])} passing of {len(cands)} candidates.")
    return scored, c

def pick_contact(company, domain, st, usage):
    try:
        res = hunter_domain_search(domain)
    except HunterPaused as e:
        st["status"] = "CONTACT_LOOKUP_PAUSED"
        st["reason"] = f"Contact lookup paused: {e}. Qualified, but no contact searched yet."
        st["hunter_limit_hit"] = True
        return None
    st["hunter"] = {"retrieved_at": res.get("retrieved_at"), "from_cache": res.get("from_cache"), "credits_used": res.get("credits_used")}
    cands = build_candidates((res.get("data") or {}).get("emails") or [], domain)
    st["hunter_candidates"] = cands
    if not cands:
        st["status"], st["reason"] = "NO_SUITABLE_CONTACT", "Hunter returned no named people for this domain."
        return None
    scored, chosen = score_candidates(company, cands, usage)
    st["contact_scores"] = scored
    if not chosen:
        st["status"], st["reason"] = "NO_SUITABLE_CONTACT", "No Hunter candidate scored 8/10 or more with company link and role ownership."
        return None
    return chosen

# ====================================================================== pain points + email
MAX_PAIN = {"account_specific_evidence": 3, "relevance_to_person": 2, "plausible_business_consequence": 2,
            "capability_match": 2, "honest_wording": 1}

def pain_points(a, sources, contact, st, usage):
    ver = Verifier(sources)
    ev = evidence_list(a)
    text = "\n".join(f"[{e['dimension']}] {e['excerpt']} (source: {e['source_url']}, dated: {e.get('dated')})" for e in ev)
    for c in a.get("compliance_claims_exact", [])[:5]:
        text += f"\n[compliance claim] {c}"
    facts = {"company_name": a.get("company_name", ""), "evidence_text": text}
    fb = None
    for attempt in range(2):
        res = gemini_json(R.pain_prompt(facts, contact, fb), usage)
        good = []
        for h in res.get("hypotheses", []):
            s = {k: clamp((h.get("scores") or {}).get(k), 0, m) for k, m in MAX_PAIN.items()}
            e = h.get("evidence") or {}
            total = sum(s.values())
            ok = (total >= R.PAIN_PASS_TOTAL and s["account_specific_evidence"] >= R.PAIN_MIN_EVIDENCE and s["honest_wording"] == 1
                  and ver.ok(e.get("source_url"), e.get("excerpt", "")) and h.get("capability") in R.APPROVED_CAPABILITIES
                  and bool(h.get("hypothesis")))
            h["scores"], h["total"], h["pass"] = s, total, ok
            if ok:
                good.append(h)
        st.setdefault("pain_attempts", []).append({"attempt": attempt + 1, "passing": len(good), "raw": res.get("hypotheses")})
        good.sort(key=lambda h: -h["total"])
        pair = next(((x, y) for i, x in enumerate(good) for y in good[i + 1:]
                     if norm(x["hypothesis"]) != norm(y["hypothesis"])
                     and (x["capability"] != y["capability"] or norm(x["evidence"]["excerpt"]) != norm(y["evidence"]["excerpt"]))), None)
        if pair:
            return facts, list(pair), ver
        fb = "need two distinct hypotheses, each scoring 7/10 or more, account evidence 2 or more, honest wording 1, a VERBATIM excerpt, and an approved capability"
    st["status"], st["reason"] = "INSUFFICIENT_PAIN_EVIDENCE", "Could not write two distinct, evidence-backed pain hypotheses."
    return facts, None, ver

FRAMEWORKS = ["soc 2", "soc2", "iso 27001", "iso27001", "gdpr", "hipaa", "pci dss", "pci-dss"]
BANNED = [r"\bre:", r"following up", r"follow-up", r"follow up", r"circling back", r"guarantee", r"\b100%"]

def numbers(text):
    t = (text or "").lower()
    for f in FRAMEWORKS:
        t = t.replace(f, " ")
    return {n.replace(",", "") for n in re.findall(r"\d+(?:[.,]\d+)*", t)}

def code_checks(draft, contact, hyp, ev_text):
    """Deterministic checks that do not depend on the AI grading itself."""
    body, subj, problems = draft.get("body", ""), draft.get("subject", ""), []
    words = len(body.split())
    if not R.WORDS_MIN <= words <= R.WORDS_MAX:
        problems.append(f"word count is {words}, target 80-120")
    if body.count("?") != 1:
        problems.append(f"the email must contain exactly one question (found {body.count('?')})")
    if contact["first_name"].lower() not in body[:80].lower():
        problems.append("it must greet the person by first name at the start")
    low = (subj + " " + body).lower()
    for rx in R.BANNED_PATTERNS:
        if re.search(rx, low):
            problems.append(f"remove the phrase matching '{rx}'")
    loose = numbers(body) - numbers(ev_text)
    if loose:
        problems.append(f"numbers {sorted(loose)} are not in the verified evidence; remove them")
    return words, problems

def write_email(facts, ver, contact, hyp, st, usage):
    sender = env("SENDER_NAME", "")
    fb, attempts = None, []
    st["email_attempts"] = attempts
    ev_text = facts["evidence_text"]
    for rev in range(R.MAX_REVISIONS + 1):
        draft = gemini_json(R.draft_prompt(facts, contact, hyp, sender, fb), usage)
        if not isinstance(draft, dict) or not draft.get("body") or not draft.get("subject"):
            fb = "return both a subject and a body"
            attempts.append({"revision": rev, "error": "draft missing subject or body"})
            continue
        words, problems = code_checks(draft, contact, hyp, ev_text)
        qa = gemini_json(R.qa_prompt(facts, contact, hyp, draft, words), usage)
        qs = {k: clamp((qa.get("scores") or {}).get(k), 0, 2) for k in ("specific_hook", "useful_hypothesis", "relevant_value", "simple_next_step", "clarity")}
        total = sum(qs.values())
        mand = qa.get("mandatory") or {}
        mand_ok = all(mand.get(k) is True for k in R.MANDATORY_CHECKS)     # a missing check counts as failed
        passed = total >= R.EMAIL_PASS_TOTAL and mand_ok and not problems
        attempts.append({"revision": rev, "words": words, "qa_total": total, "qa_scores": qs, "mandatory": mand,
                         "mandatory_ok": mand_ok, "code_problems": problems, "feedback": qa.get("feedback"), "passed": passed,
                         "subject": draft["subject"], "body": draft["body"]})
        if passed:
            return {"subject": draft["subject"].strip(), "body": draft["body"].strip()}, total
        fb = "; ".join(problems + ([qa.get("feedback")] if qa.get("feedback") else []))
    st["status"], st["reason"] = "EMAIL_QA_FAILED", "The draft still failed the checks after two revisions."
    return None, 0

# ====================================================================== orchestration
def set_stage(st, stage):
    st["stage"] = stage
    S.write_state(st)

def finish_text(st):
    """Plain-words 'what happened / what to do' for statuses that are not a ready draft."""
    s = st.get("status")
    return {"DISQUALIFIED": "Dropped. Nothing further needed.",
            "LOW_PRIORITY": "Real fit signals but weaker than the bar. No contact searched, so no Hunter credit spent.",
            "INSUFFICIENT_EVIDENCE": "Could not confirm the required gates. Worth a manual look if this company matters.",
            "NO_SUITABLE_CONTACT": "Qualified, but no Hunter candidate passed the contact check. Find a contact by hand if you want this one.",
            "CONTACT_LOOKUP_PAUSED": "Qualified, but the contact lookup is paused. Use Retry once the limit clears.",
            "INSUFFICIENT_PAIN_EVIDENCE": "Qualified with a contact, but the evidence could not support two honest pain points.",
            "EMAIL_QA_FAILED": "A draft was written but failed the quality checks after two revisions. Its attempts are saved in the trail.",
            "ERROR": "Something broke for this company only; the rest of the batch continued."}.get(s)

def continue_after_qualified(domain, st, a, sources, usage):
    set_stage(st, "finding_contact")
    contact = pick_contact(a.get("company_name", domain), domain, st, usage)
    if not contact:
        return
    st["contact"] = contact
    set_stage(st, "writing_email")
    facts, hyps, ver = pain_points(a, sources, contact, st, usage)
    if not hyps:
        return
    st["pain_points"] = hyps
    draft, qa_total = write_email(facts, ver, contact, hyps[0], st, usage)
    if draft:
        st.update(status="DRAFT_READY", reason="Draft ready for your review.", draft=draft, original_draft=dict(draft), qa_total=qa_total)

def _close(st):
    """Final bookkeeping once a company has reached its end state."""
    st["stage"] = "done"
    st["timing"]["finished_at"] = S.now_iso()
    st["escalation"] = None if st["status"] in ("DRAFT_READY", "QUALIFIED") else f"{st['reason']} {finish_text(st) or ''}".strip()
    st["hunter_calls_this_run_so_far"] = HUNTER["calls"]
    S.write_state(st)
    log(f"[{st['domain']}] {st['status']} | {st.get('reason', '')}")
    return st

def start_research(domain):
    """Step 1: read the website and decide. If the company qualifies it stops at stage 'finding_contact'."""
    t0, usage = time.time(), {}
    old = S.read_state(domain)
    st = {"domain": domain, "run_date": today(), "status": "STARTED", "stage": "reading_website",
          "timing": {"started_at": S.now_iso()}}
    if old and old.get("review"):
        st["previous_review_note"] = "an earlier run of this company had a review; it was replaced by this run"
    S.write_state(st)
    try:
        got = research_company(domain, st, usage)
        if got:
            st["stage"] = "finding_contact"
    except Exception as e:                    # one company failing must never stop the batch
        st["status"], st["reason"] = "ERROR", f"{type(e).__name__}: {str(e)[:200]}"
        st["stage"] = "reading_website"
    st["usage"] = usage
    st["timing"]["seconds"] = round(time.time() - t0, 1)
    if st["stage"] == "finding_contact":
        S.write_state(st)
        return st
    return _close(st)

def finish_contact(domain):
    """Step 2 (qualified companies only): Hunter contact, pain points, email, QA. Also used to retry a paused lookup."""
    st = S.read_state(domain)
    if not st or "assessment" not in st or st.get("status") not in ("QUALIFIED", "CONTACT_LOOKUP_PAUSED"):
        raise ValueError("This company is not waiting for a contact lookup.")
    t0, usage = time.time(), st.get("usage", {})
    st.pop("hunter_limit_hit", None)
    st.update(status="QUALIFIED", stage="finding_contact")
    S.write_state(st)
    try:
        continue_after_qualified(domain, st, st["assessment"], S.kv_get("sources", domain) or {}, usage)
    except Exception as e:
        st["status"], st["reason"] = "ERROR", f"{type(e).__name__}: {str(e)[:200]}"
    st["usage"] = usage
    st["timing"]["seconds"] = round(st["timing"].get("seconds", 0) + time.time() - t0, 1)
    return _close(st)

def run_domain(domain):
    st = start_research(domain)
    if st.get("stage") == "finding_contact":
        st = finish_contact(domain)
    return st

def resume_contact(domain):
    """Retry the contact step for a company whose lookup was paused."""
    st = S.read_state(domain)
    if not st or st.get("status") != "CONTACT_LOOKUP_PAUSED":
        return None
    return finish_contact(domain)

def clean_domain(line):
    s = line.strip().lower()
    if not s or s.startswith("#"):
        return None
    s = re.sub(r"^https?://", "", s).split("/")[0].split("?")[0]
    s = re.sub(r"^www\.", "", s)
    return s if re.fullmatch(r"[a-z0-9-]+(\.[a-z0-9-]+)+", s) else "INVALID:" + line.strip()[:60]

def run_batch(domains, on_start=None):
    hunter_reset()
    rows, seen = [], set()
    for d in domains:
        if d in seen:
            continue
        seen.add(d)
        if d.startswith("INVALID:"):
            log(f"skipped (not a domain): {d[8:]}")
            continue
        if on_start:
            on_start(d)
        rows.append(run_domain(d))
    if not S.HOSTED:
        write_summary(rows)
    return rows

def write_summary(rows):
    os.makedirs(OUT, exist_ok=True)
    with open(os.path.join(OUT, "summary.csv"), "w", encoding="utf-8", newline="") as f:
        w = csv.writer(f)
        w.writerow(["domain", "status", "total_score", "seconds", "reason"])
        for s in rows:
            w.writerow([s["domain"], s["status"], round(s.get("total", 0), 1) if "total" in s else "",
                        s.get("timing", {}).get("seconds", ""), s.get("reason", "")])

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("domains_file")
    args = ap.parse_args()
    domains = [x for x in (clean_domain(l) for l in open(args.domains_file, encoding="utf-8")) if x]
    if not domains:
        sys.exit("domains file has no domains. Add one per line.")
    rows = run_batch(domains)
    log(f"\nDone. {len(rows)} companies. Results: {os.path.join(OUT, 'summary.csv')}")

if __name__ == "__main__":
    main()
