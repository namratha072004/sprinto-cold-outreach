"""Scorecard from Neon: time per company, rewrite rate, contact accuracy, cost, limit hits, and the shut-down checks.
Numbers come only from saved results; with too few reviewed drafts the verdict says 'not enough data'."""
import db
import settings as S

def _pct(n, d):
    return round(100 * n / d, 1) if d else None

def gemini_cost(usage):
    p = S.GEMINI_PRICE.get((usage or {}).get("model"))
    if not p:
        return None
    return ((usage.get("prompt_tokens", 0)) * p["in"] + (usage.get("output_tokens", 0) + usage.get("thought_tokens", 0)) * p["out"]) / 1_000_000   # thinking is billed as output

def compute():
    done = db.q("SELECT seconds, usage, research_limit_hit FROM companies WHERE stage='done'")
    rev = db.q("SELECT review_decision, subject, body, original_subject, original_body, review_seconds FROM emails WHERE is_current AND review_decision<>'pending'")
    from review import similarity
    rewrites = [r for r in rev if r["review_decision"] == "rejected" or (r["subject"], r["body"]) != (r["original_subject"], r["original_body"])]
    heavy = [r for r in rev if r["review_decision"] == "rejected" or similarity((r["original_subject"] or "") + (r["original_body"] or ""), (r["subject"] or "") + (r["body"] or "")) < 0.7]
    labelled = db.q("SELECT contact_correct FROM contacts WHERE chosen AND contact_correct IS NOT NULL")
    secs = [float(c["seconds"]) for c in done if c["seconds"] is not None]
    rsecs = [float(r["review_seconds"]) for r in rev if r["review_seconds"] is not None]
    costs = [c for c in (gemini_cost(d["usage"]) for d in done) if c is not None]
    runs = db.one("SELECT COALESCE(SUM(hunter_credits),0) credits, COUNT(*) FILTER (WHERE hunter_paused IS NOT NULL) paused FROM runs")
    n, enough = len(rev), len(rev) >= S.MIN_SAMPLE_FOR_VERDICT
    avg_review = sum(rsecs) / len(rsecs) if rsecs else None
    m = {"companies_processed": len(done), "drafts_reviewed": n,
         "pipeline_seconds_avg": round(sum(secs) / len(secs), 1) if secs else None,
         "review_seconds_avg": round(avg_review, 1) if avg_review is not None else None,
         "rewrite_rate_pct": _pct(len(rewrites), n), "heavy_rewrite_rate_pct": _pct(len(heavy), n),
         "contact_accuracy_pct": _pct(len([c for c in labelled if c["contact_correct"]]), len(labelled)), "contact_labelled": len(labelled),
         "gemini_tokens_total": sum((d["usage"] or {}).get("prompt_tokens", 0) + (d["usage"] or {}).get("output_tokens", 0) + (d["usage"] or {}).get("thought_tokens", 0) for d in done),
         "gemini_cost_per_company_usd_if_paid": round(sum(costs) / len(costs), 4) if costs else None,
         "hunter_credits_spent": float(runs["credits"]),
         "research_limit_hit_rate_pct": _pct(len([d for d in done if d["research_limit_hit"]]), len(done)),
         "hunter_limit_hits": runs["paused"]}
    m["kill_checks"] = {"enough_data": enough, "min_sample": S.MIN_SAMPLE_FOR_VERDICT,
                        "rewrites_over_50pct": (m["rewrite_rate_pct"] > 50) if enough else None,
                        "contact_accuracy_under_60pct": (m["contact_accuracy_pct"] < 60) if len(labelled) >= S.MIN_SAMPLE_FOR_VERDICT else None,
                        "takes_more_time_than_it_saves": (avg_review / 60 >= S.BASELINE_MINUTES) if enough and avg_review is not None else None}
    m["targets"] = {"minutes_per_company": S.TARGET_MINUTES, "rewrite_rate_pct": 20, "contact_accuracy_pct": 60, "baseline_minutes": S.BASELINE_MINUTES}
    return m

if __name__ == "__main__":
    import json
    print(json.dumps(compute(), indent=2, default=str))
