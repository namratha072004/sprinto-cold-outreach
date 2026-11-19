"""Research one company end to end and save everything to Neon: pages, news, evidence (with labels and dates), every round,
scores, gates, retries, timing, status and escalation reason. Plain code decides; the AI only reads and writes.
Usage:  .venv/bin/python research.py cal.com            (creates a run, researches that company, prints the saved result)"""
import sys, time, datetime as dt
import db, rubric_db, scraper, sprinto_context
import rubric as R
import settings as S
import pipeline as P

CFG = {}

def ensure_rubric():
    global CFG
    cfg = CFG = rubric_db.load()
    rubric_db.apply(R, cfg)
    cfg["context_sha256"] = sprinto_context.apply(R)["sha256"]      # the Sprinto facts an email may use
    return cfg

def start_run(params=None):
    cfg = ensure_rubric()
    row = db.one("INSERT INTO runs (rubric_version_id, params) VALUES (%s,%s) RETURNING id", (cfg["id"], db.J({**(params or {}), "rubric_version": cfg["version"], "context_sha256": cfg["context_sha256"]})))
    return str(row["id"])

def close_run_if_done(run_id):
    """A run is DONE once none of its companies is still working."""
    db.x("UPDATE runs SET status='DONE', finished_at=now() WHERE id=%s AND status='RUNNING' AND NOT EXISTS (SELECT 1 FROM companies WHERE run_id=%s AND stage<>'done')", (run_id, run_id))

def log(run_id, company_id, event, detail=None, level="info"):
    """Logging must never break a company (rubric 11: logging fails -> keep the work, retry logging only)."""
    try:
        db.x("INSERT INTO run_log (run_id, company_id, level, event, detail) VALUES (%s,%s,%s,%s,%s)", (run_id, company_id, level, event, db.J(detail)))
    except Exception:
        pass

def _kind(url, news):
    return "news" if url in news else "website"

def research_company(run_id, domain, cid=None):
    """Returns the saved company row. status QUALIFIED means: ready for the Hunter contact step."""
    cfg = ensure_rubric()
    t0, usage = time.time(), {}
    if cid:                                                    # a shortlisted company: the row already exists
        db.x("UPDATE companies SET domain=%s, status='WORKING', stage='reading_website', started_at=COALESCE(started_at, now()) WHERE id=%s", (domain, cid))
    else:
        cid = db.one("INSERT INTO companies (run_id, domain, status, stage, started_at) VALUES (%s,%s,'WORKING','reading_website',now()) "
                     "ON CONFLICT (run_id, domain) DO UPDATE SET status='WORKING', stage='reading_website', started_at=now() RETURNING id", (run_id, domain))["id"]
    log(run_id, cid, "company_started", {"domain": domain, "rubric": cfg["version"]})
    sources, fetched, pages, retrieved, news_pub, first_round = {}, set(), [], {}, {}, {}
    result = {"status": "ERROR", "stage": "done", "reason": "", "flags": [], "limit_hit": False}
    a = d = None
    rounds_saved = []

    def read(c, rnd):
        p = scraper.fetch_page(c["url"])
        fetched.add(c["url"]); fetched.add(p["url"])
        rec = {"url": c["url"], "type": c.get("type", "homepage"), "round": rnd, "result": p["status"], "attempts": p["attempts"],
               "from_cache": p.get("from_cache", False), "kind": "website", "guessed_path": bool(c.get("guessed")), "note": p.get("note")}
        pages.append(rec)
        log(run_id, cid, "page_read", {k: rec[k] for k in ("url", "round", "result", "attempts", "from_cache")},
            "info" if p["status"] == "ok" else "warn")
        if p["status"] == "ok":
            sources[p["url"]] = p["text"]; retrieved[p["url"]] = p["retrieved_at"]
        return p, rec

    def save_round(rnd, gap, srcs, new_ev, decision, note=None):
        db.x("INSERT INTO research_rounds (company_id,round_no,gap,sources,new_evidence,revised_decision) VALUES (%s,%s,%s,%s,%s,%s) "
             "ON CONFLICT (company_id,round_no) DO UPDATE SET gap=EXCLUDED.gap, sources=EXCLUDED.sources, new_evidence=EXCLUDED.new_evidence, revised_decision=EXCLUDED.revised_decision",
             (cid, rnd, db.J({"gap": gap, "note": note}), db.J(srcs), db.J(new_ev), decision))
        log(run_id, cid, "round_done", {"round": rnd, "decision": decision, "new_evidence": len(new_ev)})

    try:
        home, hrec = read({"url": f"https://{domain}", "type": "homepage"}, 0)
        if home["status"] in ("failed",):                              # connection problem: try the www form once
            home, hrec = read({"url": f"https://www.{domain}", "type": "homepage"}, 0)
        if home["status"] != "ok":
            result.update(status="INSUFFICIENT_EVIDENCE", stage="done", flags=["retrieval_failure"],
                          reason=f"Could not read the homepage ({home['status'].replace('_', ' ')}). This is a retrieval failure, not a judgement on fit.")
            save_round(0, None, pages, [], "INSUFFICIENT_EVIDENCE", "homepage not readable")
        else:
            cands = P.candidates_from(domain, home["links"])
            brand = home.get("site_name") or domain.split(".")[0]
            news_src, news_items, ninfo = scraper.company_news(domain, brand, int(cfg["thresholds"]["reason_now_level1_months"]))
            for link, text in news_src.items():
                sources[link] = text; retrieved[link] = S.today()
            for it in news_items:
                news_pub[it["link"]] = it["published"]
            log(run_id, cid, "google_news_read", {"items": len(news_items), "from_cache": ninfo["from_cache"], "attempts": ninfo["attempts"],
                                                  "note": "Google News RSS read by the Growth Lead's decision (Google's robots.txt does not allow it)"})
            first = P.pick_pages(cands, fetched, ["security", "customers", "product"], R.INITIAL_EXTRA_PAGES)
            for c in first:
                read(c, 0)
            a = P.assess(domain, sources, usage)
            d = P.decide(a)
            for e in P.evidence_list(a):
                first_round.setdefault(P.ev_key(e), 0)
            save_round(0, None, pages + [{"url": "Google News", "kind": "news", "result": "ok" if news_items else "no items", "items": len(news_items)}],
                       P.evidence_list(a), d["status"])
            budget_hit, stop = False, None
            for rnd in range(1, R.MAX_ROUNDS + 1):
                if d["status"] in ("QUALIFIED", "DISQUALIFIED") or (d["status"] == "LOW_PRIORITY" and not a["missing"]):
                    break
                types = [m.get("page_type") for m in a["missing"] if m.get("page_type")] or ["security", "customers", "product", "careers", "news", "about"]
                chosen = P.pick_pages(cands, fetched, types, R.PAGES_PER_ROUND)
                if not chosen:
                    save_round(rnd, a["missing"], [], [], d["status"], "no new relevant pages left to read")
                    stop = "no_new_pages"
                    break
                before = {P.ev_key(e) for e in P.evidence_list(a)}
                gap = a["missing"]
                before_pages = len(pages)
                for c in chosen:
                    read(c, rnd)
                a = P.assess(domain, sources, usage)
                d = P.decide(a)
                new_ev = [e for e in P.evidence_list(a) if P.ev_key(e) not in before]
                for e in new_ev:
                    first_round.setdefault(P.ev_key(e), rnd)
                save_round(rnd, gap, pages[before_pages:], new_ev, d["status"])
            else:
                if d["status"] not in ("QUALIFIED", "DISQUALIFIED"):
                    budget_hit, stop = True, "budget_used"
            flags = sorted(set(d["flags"] + (["some_pages_not_readable"] if any(p["result"] != "ok" and not p["guessed_path"] for p in pages) else [])))
            result.update(status=d["status"], reason=d["reason"], flags=flags, limit_hit=budget_hit, stop=stop,
                          stage="finding_contact" if d["status"] == "QUALIFIED" else "done")
            _save_evidence(cid, a, d, sources, retrieved, news_pub, first_round, ninfo["url"])
    except Exception as e:                                             # one company failing never stops the run
        result.update(status="ERROR", stage="done", reason=f"{type(e).__name__}: {str(e)[:200]}")
        log(run_id, cid, "company_error", {"error": result["reason"]}, "error")

    secs = round(time.time() - t0, 1)
    esc = None if result["status"] in ("QUALIFIED", "DRAFT_READY") else f"{result['reason']} {P.finish_text({'status': result['status']}) or ''}".strip()
    retry = {"page_reads": len(pages), "page_attempts": sum(p["attempts"] for p in pages), "pages_not_ok": len([p for p in pages if p["result"] != "ok"]),
             "model_calls": usage.get("calls", 0), "model_retries": usage.get("retries", 0)}
    fd = P.parse_date(a["funding"].get("date")) if a else None
    db.x("""UPDATE companies SET name=%s, status=%s, stage=%s, funding_stage=%s, funding_date=%s, total_score=%s, gates=%s, flags=%s, reason=%s,
            escalation_reason=%s, research_limit_hit=%s, retry_counts=%s, usage=%s, finished_at=CASE WHEN %s='done' THEN now() END,
            seconds=COALESCE(seconds,0)+%s, assessment=%s, sources_used=%s, sources_text=%s, updated_at=now() WHERE id=%s""",
         ((a or {}).get("company_name") or domain, result["status"], result["stage"], (a or {}).get("funding", {}).get("stage"), fd,
          d["total"] if d else None, db.J(d["gates"] if d else None), result["flags"], result["reason"], esc, result["limit_hit"],
          db.J(retry), db.J(usage), result["stage"], secs, db.J(a), db.J([{"url": u, "kind": _kind(u, news_pub)} for u in sources]), db.J(sources), cid))
    log(run_id, cid, "company_finished", {"status": result["status"], "seconds": secs, "stop": result.get("stop")})
    return db.one("SELECT * FROM companies WHERE id=%s", (cid,))

def _save_evidence(cid, a, d, sources, retrieved, news_pub, first_round, news_query_url):
    db.x("DELETE FROM evidence WHERE company_id=%s", (cid,))
    db.x("DELETE FROM qualification_scores WHERE company_id=%s", (cid,))
    for e in P.evidence_list(a):
        url = e["source_url"]
        published = news_pub.get(url) or None
        pd = P.parse_date(published) if published else P.parse_date(e.get("dated"))
        db.x("INSERT INTO evidence (company_id,dimension,claim,source_kind,source_url,excerpt,retrieved_on,published_on,label,round_no) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
             (cid, e["dimension"], None, _kind(url, news_pub), url, e["excerpt"], retrieved.get(url) or S.today(), pd,
              e.get("label") or "observed", first_round.get(P.ev_key(e), 0)))
    for c in a.get("conflicts", []):
        for e in c["evidence"]:
            db.x("INSERT INTO evidence (company_id,dimension,claim,source_kind,source_url,excerpt,retrieved_on,published_on,label,round_no) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,'conflicting',NULL)",
                 (cid, "conflict", c["claim"], _kind(e["source_url"], news_pub), e["source_url"], e["excerpt"], retrieved.get(e["source_url"]) or S.today(), None))
    if a["funding"]["stage"] == "unknown":                             # say plainly that we looked and found nothing
        db.x("INSERT INTO evidence (company_id,dimension,claim,source_kind,source_url,excerpt,retrieved_on,published_on,label,round_no) VALUES (%s,'funding',%s,'news',%s,%s,%s,NULL,'unknown',NULL)",
             (cid, "Funding stage", news_query_url, "No source read named a funding round.", S.today()))
    for k, dim in a["dimensions"].items():
        db.x("INSERT INTO qualification_scores (company_id,dimension,level,points,state,reasoning) VALUES (%s,%s,%s,%s,%s,%s)",
             (cid, k, dim["level"], d["points"][k], dim["state"], dim["reasoning"]))

if __name__ == "__main__":
    domain = (sys.argv[1] if len(sys.argv) > 1 else "").strip().lower()
    if not domain:
        sys.exit("usage: research.py <domain>")
    rid = start_run({"trigger": "research.py", "domain": domain})
    row = research_company(rid, domain)
    print(f"run {rid}\n{row['domain']}: {row['status']} | stage {row['stage']} | score {row['total_score']} | {row['seconds']}s")
    print("reason:", row["reason"]); print("gates:", {k: v['state'] for k, v in (row['gates'] or {}).items()}, "| flags:", row["flags"])
