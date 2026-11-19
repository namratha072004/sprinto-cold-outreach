"""Step 3: find companies in Google News using search queries stored in Neon (rubric_rules search.*). Saves every result.
The company NAME is read from the headline by the AI and then checked by code (it must appear word for word in the headline).
The company DOMAIN is never guessed: it is 'confirmed' only if the headline or snippet itself writes a domain; otherwise 'unknown'.
Usage:  .venv/bin/python discovery.py"""
import re, datetime as dt
import db, scraper, research
import pipeline as P

DOMAIN_RX = re.compile(r"\b((?:[a-z0-9-]+\.)+(?:com|io|ai|co|app|dev|net|org|so|xyz|tech|cloud))\b", re.I)

def queries(cfg):
    """Search queries come from Neon. The window follows the rubric's 12-month rule."""
    months = int(cfg["thresholds"]["reason_now_level1_months"])
    when = f" when:{months // 12}y" if months % 12 == 0 else f" when:{months * 30}d"
    templates = cfg["rules"].get(("search", "query_templates_PROPOSED")) or cfg["rules"].get(("search", "query_templates")) or []
    return [t.replace("{year}", str(dt.date.today().year)) + when for t in templates]

def confirmed_domain(item):
    """A domain counts only if it is written in the headline/snippet and is not the publisher's own site."""
    pub = (item.get("publisher_url") or "").lower()
    for m in DOMAIN_RX.findall(item["headline"] + " " + (item.get("description") or "")):
        m = m.lower()
        if m not in pub and not any(x in m for x in ("google.", "news.")):
            return m
    return None

def name_in_headline(name, headline):
    return bool(name) and norm(name) in norm(headline)

def norm(s):
    return re.sub(r"\W+", " ", (s or "").lower()).strip()

def discover(run_id, per_query=10):
    cfg = research.ensure_rubric()
    saved, usage = 0, {}
    for q in queries(cfg):
        items, info = scraper.google_news(q, per_query)
        research.log(run_id, None, "discovery_query", {"query": q, "items": len(items), "from_cache": info["from_cache"], "attempts": info["attempts"],
                     "note": "Google News RSS read by the Growth Lead's decision (Google's robots.txt does not allow it)"})
        for it in items:
            if db.one("SELECT 1 FROM discovery_results WHERE run_id=%s AND source_url=%s", (run_id, it["link"])):
                continue
            dom = confirmed_domain(it)
            db.x("INSERT INTO discovery_results (run_id,query,headline,source_name,source_url,published_at,company_domain,domain_status,domain_evidence) "
                 "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s)",
                 (run_id, q, it["headline"], it["publisher"], it["link"], it["published"], dom, "confirmed" if dom else "unknown",
                  "domain written in the headline or snippet" if dom else None))
            saved += 1
    names = 0
    try:
        rows = db.q("SELECT id, headline FROM discovery_results WHERE run_id=%s AND company_name IS NULL ORDER BY id", (run_id,))
        for i in range(0, len(rows), 25):
            batch = rows[i:i + 25]
            prompt = ("TASK=NAMES\nFor each numbered headline, return the company the story is about (the one raising money or making the announcement), "
                      "copied EXACTLY as written in the headline. If there is no single clear company, return null. Never guess or add words.\n"
                      'Return ONLY JSON: {"items":[{"id": int, "company": str|null}]}\nHEADLINES:\n' + "\n".join(f"{r['id']}: {r['headline']}" for r in batch))
            out = P.gemini_json(prompt, usage)
            by_id = {r["id"]: r["headline"] for r in batch}
            for x in out.get("items", []):
                if x.get("id") in by_id and name_in_headline(x.get("company"), by_id[x["id"]]):     # code check: must be in the headline
                    db.x("UPDATE discovery_results SET company_name=%s WHERE id=%s", (x["company"].strip(), x["id"])); names += 1
    except Exception as e:
        research.log(run_id, None, "discovery_name_extraction_failed", {"error": f"{type(e).__name__}: {str(e)[:160]}"}, "error")
    return {"saved": saved, "names_read": names}

if __name__ == "__main__":
    rid = research.start_run({"trigger": "discovery.py"})
    print("run", rid, discover(rid))
    s = db.one("SELECT count(*) n, count(*) FILTER (WHERE domain_status='confirmed') conf, count(*) FILTER (WHERE company_name IS NOT NULL) named FROM discovery_results WHERE run_id=%s", (rid,))
    print("saved results:", s["n"], "| domain confirmed from a source:", s["conf"], "| company name read and verified:", s["named"])
    for r in db.q("SELECT headline, source_name, published_at, company_name, domain_status FROM discovery_results WHERE run_id=%s ORDER BY id LIMIT 8", (rid,)):
        print(" -", r["published_at"], "|", r["headline"][:95], "|", r["company_name"], "|", r["domain_status"])
