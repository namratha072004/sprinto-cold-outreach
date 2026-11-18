"""Scorecard from the saved results: time, rewrites, contact accuracy, cost, limit hits, and the kill criteria.
Run `python3 metrics.py` or open the Scorecard in the app. Numbers come only from saved runs; with too few
reviewed companies the verdict says 'not enough data' instead of guessing."""
import settings as S

def _pct(n, d):
    return round(100 * n / d, 1) if d else None

def gemini_cost(usage):
    p = S.GEMINI_PRICE.get(usage.get("model"))
    if not p:
        return None
    out_tokens = usage.get("output_tokens", 0) + usage.get("thought_tokens", 0)     # thinking is billed as output
    return (usage.get("prompt_tokens", 0) * p["in"] + out_tokens * p["out"]) / 1_000_000

def compute():
    states = S.all_states()
    done = [s for s in states if s.get("stage") == "done"]
    drafts = [s for s in done if s.get("draft") or s.get("original_draft")]
    reviewed = [s for s in drafts if (s.get("review") or {}).get("decision")]
    rewrites = [s for s in reviewed if s["review"]["decision"] == "rejected" or s["review"].get("edited")]
    heavy = [s for s in reviewed if s["review"]["decision"] == "rejected" or (s["review"].get("edit_similarity") or 1) < 0.7]
    labelled = [s for s in done if "contact_correct" in (s.get("review") or {})]
    right = [s for s in labelled if s["review"]["contact_correct"]]
    secs = [s["timing"]["seconds"] for s in done if s.get("timing", {}).get("seconds") is not None]
    rev_secs = [s["review"]["review_seconds"] for s in reviewed if s["review"].get("review_seconds") is not None]
    costs = [gemini_cost(s.get("usage", {})) for s in done if s.get("usage")]
    known_costs = [c for c in costs if c is not None]
    researched = [s for s in done if "research_limit_hit" in s]
    n = len(reviewed)
    avg_review = sum(rev_secs) / len(rev_secs) if rev_secs else None

    m = {
        "companies_processed": len(done),
        "drafts_reviewed": n,
        "pipeline_seconds_avg": round(sum(secs) / len(secs), 1) if secs else None,
        "review_seconds_avg": round(avg_review, 1) if avg_review is not None else None,
        "rewrite_rate_pct": _pct(len(rewrites), n),
        "heavy_rewrite_rate_pct": _pct(len(heavy), n),
        "contact_accuracy_pct": _pct(len(right), len(labelled)),
        "contact_labelled": len(labelled),
        "gemini_tokens_total": sum(s.get("usage", {}).get("prompt_tokens", 0) + s.get("usage", {}).get("output_tokens", 0)
                                   + s.get("usage", {}).get("thought_tokens", 0) for s in done),
        "gemini_cost_per_company_usd_if_paid": round(sum(known_costs) / len(known_costs), 4) if known_costs else None,
        "hunter_credits_spent": round(sum(float(s.get("hunter", {}).get("credits_used") or 0) for s in done if s.get("hunter") and not s["hunter"].get("from_cache")), 1),
        "research_limit_hit_rate_pct": _pct(len([s for s in researched if s["research_limit_hit"]]), len(researched)),
        "hunter_limit_hits": len([s for s in done if s.get("hunter_limit_hit")]),
    }
    enough = n >= S.MIN_SAMPLE_FOR_VERDICT
    m["kill_checks"] = {
        "enough_data": enough,
        "min_sample": S.MIN_SAMPLE_FOR_VERDICT,
        "rewrites_over_50pct": (m["rewrite_rate_pct"] > 50) if enough and m["rewrite_rate_pct"] is not None else None,
        "contact_accuracy_under_60pct": (m["contact_accuracy_pct"] < 60) if len(labelled) >= S.MIN_SAMPLE_FOR_VERDICT else None,
        "takes_more_time_than_it_saves": (avg_review / 60 >= S.BASELINE_MINUTES) if enough and avg_review is not None else None,
    }
    m["targets"] = {"minutes_per_company": S.TARGET_MINUTES, "rewrite_rate_pct": 20, "contact_accuracy_pct": 60,
                    "baseline_minutes": S.BASELINE_MINUTES}
    return m

if __name__ == "__main__":
    import json
    print(json.dumps(compute(), indent=2))
