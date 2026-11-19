"""Step 4: shortlist. Plain code, no AI. Rules and the size come from Neon (rubric_rules shortlist.policy, rubric_limits shortlist_size).
For every Google News result it records what happened to it (outcome), merges articles about the same company, ranks the rest, and keeps the top N.
Each kept company becomes a QUEUED company row; its website is found in the next step (website_finder)."""
import re, datetime as dt
import db, research

def norm_name(s):
    return re.sub(r"[^a-z0-9]+", "", (s or "").lower())

def stage_points(headline, pol):
    """Returns (points, in_scope, outside_label). Series A-C in the headline scores; a stage OUTSIDE A-C (and none inside) drops the company."""
    h = (headline or "").lower()
    inside = [p for p in pol["in_scope_stage_patterns"] if re.search(p, h)]
    outside = [m.group(0) for p in pol["outside_stage_patterns"] for m in [re.search(p, h)] if m]
    if inside:
        return pol["stage_points"], True, None
    return 0, False, outside[0] if outside else None

def signals(text, pol):
    t = (text or "").lower()
    found = [k for k in pol["signal_keywords"] if re.search(rf"\b{re.escape(k)}\b", t)]
    return found, min(pol["signal_points_max"], pol["signal_points_each"] * len(found))

def build(run_id):
    cfg = research.ensure_rubric()
    pol, size = cfg["rules"][("shortlist", "policy")], cfg["limits"]["shortlist_size"]
    today = dt.date.today()
    rows = db.q("SELECT * FROM discovery_results WHERE run_id=%s ORDER BY id", (run_id,))
    cands, outcome = [], {}
    for r in rows:
        if not r["company_name"]:
            outcome[r["id"]] = ("not_funding_news", "No single company named in the headline (a round-up or unclear story).")
            continue
        if r["published_at"] and (today - r["published_at"]).days > pol["max_age_months"] * 30.5:
            outcome[r["id"]] = ("too_old", f"Published {r['published_at']}, older than {pol['max_age_months']} months.")
            continue
        sp, inside, outside = stage_points(r["headline"], pol)
        if outside and not inside:
            outcome[r["id"]] = ("outside_stage", f"Headline says '{outside}': outside Series A-C, so it is dropped before any research.")
            continue
        found, kp = signals(r["headline"] + " " + (r["source_name"] or ""), pol)
        cands.append({"row": r, "score": sp + kp, "stage_in": inside, "signals": found})
    groups = {}
    for c in cands:
        groups.setdefault(norm_name(c["row"]["company_name"]), []).append(c)
    primaries = []
    for g in groups.values():
        g.sort(key=lambda c: (-c["score"], -(c["row"]["published_at"] or dt.date.min).toordinal()))
        primary, rest = g[0], g[1:]
        primary["merged"] = len(rest)
        for m in rest:
            outcome[m["row"]["id"]] = ("merged", f"Same company as '{primary['row']['headline'][:70]}'; merged into one.")
            db.x("UPDATE discovery_results SET merged_into=%s WHERE id=%s", (primary["row"]["id"], m["row"]["id"]))
        primaries.append(primary)
    primaries.sort(key=lambda c: (-c["score"], -(c["row"]["published_at"] or dt.date.min).toordinal()))
    kept = primaries[:size]
    company_ids = []
    for rank, c in enumerate(kept, 1):
        r = c["row"]
        reason = (("Series A-C named in the headline" if c["stage_in"] else "Funding stage not stated in the headline (flagged, continues)")
                  + (f"; signals: {', '.join(c['signals'])}" if c["signals"] else "; no sector signal words")
                  + (f"; {c['merged']} more article(s) about it merged" if c["merged"] else "") + f"; published {r['published_at']}")
        cid = db.one("INSERT INTO companies (run_id, name, status, stage, shortlist_rank, flags) VALUES (%s,%s,'QUEUED','finding_website',%s,%s) RETURNING id",
                     (run_id, r["company_name"], rank, [] if c["stage_in"] else ["funding_stage_unknown"]))["id"]
        db.x("INSERT INTO shortlist (run_id, discovery_result_id, company_name, rank, reason, company_id) VALUES (%s,%s,%s,%s,%s,%s) ON CONFLICT (run_id, company_name) DO NOTHING",
             (run_id, r["id"], r["company_name"], rank, reason, cid))
        db.x("UPDATE discovery_results SET company_id=%s WHERE id=%s", (cid, r["id"]))
        outcome[r["id"]] = ("shortlisted", f"#{rank} of {len(kept)}: {reason}")
        company_ids.append(cid)
    for c in primaries[size:]:
        outcome[c["row"]["id"]] = ("not_shortlisted", f"Ranked below the top {size} (score {c['score']}).")
    for rid, (o, note) in outcome.items():
        db.x("UPDATE discovery_results SET outcome=%s, outcome_note=%s WHERE id=%s", (o, note, rid))
    counts = {}
    for o, _ in outcome.values():
        counts[o] = counts.get(o, 0) + 1
    research.log(run_id, None, "shortlist_built", {"size": size, "kept": len(kept), "outcomes": counts})
    return {"kept": len(kept), "company_ids": company_ids, "outcomes": counts}
