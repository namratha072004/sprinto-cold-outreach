"""Rubric editor behind Settings. The Growth Lead owns the ICP, so she can change numbers here. Every save creates a NEW rubric version
(old versions are never changed, so every past run still points at the rules it used). Level texts, gates and the banned phrases are
shown but not edited here. Changes apply from the NEXT run."""
import datetime as dt
import db, rubric_db

THRESHOLD_FIELDS = {"total_min": (0, 100), "contact_pass_total": (0, 10), "pain_pass_total": (0, 10), "email_pass_total": (0, 10),
                    "email_words_target_min": (20, 400), "email_words_target_max": (20, 400),
                    "reason_now_level1_months": (1, 60), "reason_now_level2_months": (1, 60)}
LIMIT_FIELDS = {"initial_extra_pages": (1, 10), "max_extra_rounds": (0, 6), "pages_per_round": (1, 6), "max_read_attempts": (1, 5),
                "email_max_revisions": (0, 5), "hunter_calls_per_run": (0, 50), "shortlist_size": (1, 50)}
POLICY_NUMERIC = {"stage_points": (0, 10), "signal_points_each": (0, 5), "signal_points_max": (0, 10), "max_age_months": (1, 60)}
RULE_FLAGS = {"contact.only_preferred_roles": ("contact", "only_preferred_roles")}
LABELS = {"total_min": "Score needed to qualify (out of 100)", "contact_pass_total": "Contact score needed (out of 10)", "pain_pass_total": "Pain-point score needed (out of 10)",
          "email_pass_total": "Email quality score needed (out of 10)", "email_words_target_min": "Email length, fewest words", "email_words_target_max": "Email length, most words",
          "reason_now_level1_months": "Reason-to-engage window, level 1 (months)", "reason_now_level2_months": "Reason-to-engage window, level 2 (months)",
          "initial_extra_pages": "Pages read after the homepage", "max_extra_rounds": "Extra research rounds", "pages_per_round": "New pages per round",
          "max_read_attempts": "Tries for a page that fails", "email_max_revisions": "Email revisions allowed", "hunter_calls_per_run": "Hunter lookups per run",
          "shortlist_size": "Companies on the shortlist", "stage_points": "Shortlist points for Series A-C in the headline", "signal_points_each": "Points per sector word",
          "signal_points_max": "Most sector-word points", "max_age_months": "How old a funding story can be (months)"}

def current():
    cfg = rubric_db.load()
    pol = dict(cfg["rules"].get(("shortlist", "policy"), {}))
    versions = db.q("SELECT version, source, is_active, created_at FROM rubric_versions ORDER BY id DESC LIMIT 12")
    return {"version": cfg["version"],
            "weights": [{"key": d["key"], "label": d["label"], "weight": d["weight"]} for d in cfg["dimensions"]],
            "thresholds": {k: {"value": cfg["thresholds"][k], "label": LABELS[k], "min": lo, "max": hi} for k, (lo, hi) in THRESHOLD_FIELDS.items() if k in cfg["thresholds"]},
            "limits": {k: {"value": cfg["limits"][k], "label": LABELS[k], "min": lo, "max": hi} for k, (lo, hi) in LIMIT_FIELDS.items() if k in cfg["limits"]},
            "shortlist": {"numbers": {k: {"value": pol[k], "label": LABELS[k], "min": lo, "max": hi} for k, (lo, hi) in POLICY_NUMERIC.items() if k in pol},
                          "signal_keywords": pol.get("signal_keywords", [])},
            "capabilities": cfg["capabilities"],
            "queries": cfg["rules"].get(("search", "query_templates_PROPOSED")) or cfg["rules"].get(("search", "query_templates")) or [],
            "read_only": {"gates": [{"key": g["key"], "label": g["label"], "rule": g["params"]} for g in cfg["gates"].values()],
                          "levels": {d["key"]: cfg["levels"][d["key"]] for d in cfg["dimensions"]},
                          "banned_phrases": cfg["rules"].get(("email", "banned_patterns"), []), "only_preferred_roles": cfg["rules"].get(("contact", "only_preferred_roles"), True), "role_order": cfg["rules"].get(("contact", "role_preference"), [])},
            "versions": versions}

def _num(v, lo, hi, name):
    try:
        x = float(v)
    except (TypeError, ValueError):
        raise ValueError(f"{LABELS.get(name, name)} must be a number.")
    if not lo <= x <= hi:
        raise ValueError(f"{LABELS.get(name, name)} must be between {lo} and {hi}.")
    return int(x) if x == int(x) else x

def validate(ch):
    out = {}
    if "weights" in ch:
        w = {k: _num(v, 0, 100, "weight") for k, v in ch["weights"].items()}
        if sum(w.values()) != 100:
            raise ValueError(f"The five weights must add up to 100 (they add up to {sum(w.values())}).")
        out["weights"] = w
    if "thresholds" in ch:
        out["thresholds"] = {k: _num(v, *THRESHOLD_FIELDS[k], k) for k, v in ch["thresholds"].items() if k in THRESHOLD_FIELDS}
        t = out["thresholds"]
        if "email_words_target_min" in t and "email_words_target_max" in t and t["email_words_target_min"] >= t["email_words_target_max"]:
            raise ValueError("The shortest email length must be below the longest.")
    if "limits" in ch:
        out["limits"] = {k: _num(v, *LIMIT_FIELDS[k], k) for k, v in ch["limits"].items() if k in LIMIT_FIELDS}
    if "shortlist_numbers" in ch:
        out["shortlist_numbers"] = {k: _num(v, *POLICY_NUMERIC[k], k) for k, v in ch["shortlist_numbers"].items() if k in POLICY_NUMERIC}
    if "signal_keywords" in ch:
        kw = [str(x).strip().lower() for x in ch["signal_keywords"] if str(x).strip()]
        if not 1 <= len(kw) <= 40:
            raise ValueError("Give between 1 and 40 sector words.")
        out["signal_keywords"] = kw
    if "capabilities" in ch:
        caps = [str(x).strip() for x in ch["capabilities"] if str(x).strip()]
        if not caps:
            raise ValueError("At least one approved Sprinto capability is required.")
        out["capabilities"] = caps
    if "rules" in ch:
        bad = [k for k in ch["rules"] if k not in RULE_FLAGS]
        if bad:
            raise ValueError("Unknown rule: " + ", ".join(bad))
        out["rules"] = {k: bool(v) for k, v in ch["rules"].items()}
    if "queries" in ch:
        qs = [str(x).strip() for x in ch["queries"] if str(x).strip()]
        if not 1 <= len(qs) <= 10:
            raise ValueError("Give between 1 and 10 Google News searches.")
        out["queries"] = qs
    return out

def save(changes, who="growth lead"):
    ch = validate(changes)
    if not ch:
        raise ValueError("Nothing to save.")
    with db.transaction() as cur:
        cur.execute("SELECT id, version FROM rubric_versions WHERE is_active")
        old = cur.fetchone()
        cur.execute("SELECT count(*) n FROM rubric_versions"); n = cur.fetchone()["n"]
        name = f"v{n + 1}-edited-{dt.datetime.now().strftime('%Y%m%d-%H%M%S')}"
        cur.execute("UPDATE rubric_versions SET is_active=false WHERE is_active")
        cur.execute("INSERT INTO rubric_versions (version, source, is_active, notes) VALUES (%s,%s,true,%s) RETURNING id",
                    (name, f"edited in Settings by the {who}, copied from {old['version']}", "; ".join(sorted(ch))))
        vid, ovid = cur.fetchone()["id"], old["id"]
        cur.execute("INSERT INTO rubric_dimensions (rubric_version_id,key,label,weight,max_level,sort_order) SELECT %s,key,label,weight,max_level,sort_order FROM rubric_dimensions WHERE rubric_version_id=%s", (vid, ovid))
        cur.execute("INSERT INTO rubric_levels (dimension_id,level,description) SELECT nd.id, l.level, l.description FROM rubric_levels l "
                    "JOIN rubric_dimensions od ON od.id=l.dimension_id JOIN rubric_dimensions nd ON nd.rubric_version_id=%s AND nd.key=od.key WHERE od.rubric_version_id=%s", (vid, ovid))
        cur.execute("INSERT INTO rubric_gates (rubric_version_id,key,label,kind,params,sort_order) SELECT %s,key,label,kind,params,sort_order FROM rubric_gates WHERE rubric_version_id=%s", (vid, ovid))
        cur.execute("INSERT INTO rubric_thresholds (rubric_version_id,key,value) SELECT %s,key,value FROM rubric_thresholds WHERE rubric_version_id=%s", (vid, ovid))
        cur.execute("INSERT INTO rubric_limits (rubric_version_id,key,value) SELECT %s,key,value FROM rubric_limits WHERE rubric_version_id=%s", (vid, ovid))
        cur.execute("INSERT INTO rubric_capabilities (rubric_version_id,name) SELECT %s,name FROM rubric_capabilities WHERE rubric_version_id=%s", (vid, ovid))
        cur.execute("INSERT INTO rubric_rules (rubric_version_id,area,key,value) SELECT %s,area,key,value FROM rubric_rules WHERE rubric_version_id=%s", (vid, ovid))
        for k, v in ch.get("weights", {}).items():
            cur.execute("UPDATE rubric_dimensions SET weight=%s WHERE rubric_version_id=%s AND key=%s", (v, vid, k))
        for k, v in ch.get("thresholds", {}).items():
            cur.execute("UPDATE rubric_thresholds SET value=%s WHERE rubric_version_id=%s AND key=%s", (v, vid, k))
            if k == "total_min":                                  # the score gate follows the threshold
                cur.execute("UPDATE rubric_gates SET params=jsonb_set(params,'{min}',to_jsonb(%s::numeric)) WHERE rubric_version_id=%s AND key='score'", (v, vid))
        for k, v in ch.get("limits", {}).items():
            cur.execute("UPDATE rubric_limits SET value=%s WHERE rubric_version_id=%s AND key=%s", (v, vid, k))
        for k, v in ch.get("rules", {}).items():
            area, key = RULE_FLAGS[k]
            cur.execute("INSERT INTO rubric_rules VALUES (%s,%s,%s,%s) ON CONFLICT (rubric_version_id, area, key) DO UPDATE SET value=EXCLUDED.value", (vid, area, key, db.J(v)))
        if "capabilities" in ch:
            cur.execute("DELETE FROM rubric_capabilities WHERE rubric_version_id=%s", (vid,))
            for c in ch["capabilities"]:
                cur.execute("INSERT INTO rubric_capabilities VALUES (%s,%s)", (vid, c))
        if "queries" in ch:
            cur.execute("UPDATE rubric_rules SET value=%s WHERE rubric_version_id=%s AND area='search' AND key LIKE 'query_templates%%'", (db.J(ch["queries"]), vid))
        if "shortlist_numbers" in ch or "signal_keywords" in ch:
            cur.execute("SELECT value FROM rubric_rules WHERE rubric_version_id=%s AND area='shortlist' AND key='policy'", (vid,))
            pol = cur.fetchone()["value"]
            pol.update(ch.get("shortlist_numbers", {}))
            if "signal_keywords" in ch:
                pol["signal_keywords"] = ch["signal_keywords"]
            cur.execute("UPDATE rubric_rules SET value=%s WHERE rubric_version_id=%s AND area='shortlist' AND key='policy'", (db.J(pol), vid))
    return name
