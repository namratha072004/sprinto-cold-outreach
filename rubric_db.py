"""Loads the ACTIVE rubric from Neon and applies it, so no rule value lives in code.
apply() sets the values on the rubric module (rubric.py keeps only the prompt wording); every other module reads rubric.X as before."""
import db

def load(version=None):
    v = db.one("SELECT * FROM rubric_versions WHERE version=%s", (version,)) if version else db.one("SELECT * FROM rubric_versions WHERE is_active")
    if not v:
        raise RuntimeError("No active rubric in Neon. Run scripts/seed_rubric.py.")
    vid = v["id"]
    dims = db.q("SELECT * FROM rubric_dimensions WHERE rubric_version_id=%s ORDER BY sort_order", (vid,))
    levels = {}
    for r in db.q("SELECT d.key, l.level, l.description FROM rubric_levels l JOIN rubric_dimensions d ON d.id=l.dimension_id "
                  "WHERE d.rubric_version_id=%s ORDER BY d.sort_order, l.level", (vid,)):
        levels.setdefault(r["key"], {})[r["level"]] = r["description"]
    return {
        "id": vid, "version": v["version"],
        "dimensions": dims,
        "weights": {d["key"]: d["weight"] for d in dims},
        "levels": levels,
        "gates": {g["key"]: g for g in db.q("SELECT * FROM rubric_gates WHERE rubric_version_id=%s ORDER BY sort_order", (vid,))},
        "thresholds": {r["key"]: float(r["value"]) for r in db.q("SELECT * FROM rubric_thresholds WHERE rubric_version_id=%s", (vid,))},
        "limits": {r["key"]: r["value"] for r in db.q("SELECT * FROM rubric_limits WHERE rubric_version_id=%s", (vid,))},
        "capabilities": [r["name"] for r in db.q("SELECT name FROM rubric_capabilities WHERE rubric_version_id=%s ORDER BY name", (vid,))],
        "rules": {(r["area"], r["key"]): r["value"] for r in db.q("SELECT * FROM rubric_rules WHERE rubric_version_id=%s", (vid,))},
    }

def apply(R, cfg):
    """Put the Neon values onto the rubric module that the pipeline code reads."""
    t, L, g = cfg["thresholds"], cfg["limits"], cfg["gates"]
    R.WEIGHTS = dict(cfg["weights"])
    R.THRESHOLD_TOTAL = t["total_min"]
    R.GATE_BUSINESS_FIT_LEVEL = g["saas"]["params"]["min"]
    R.GATE_COMPLIANCE_MIN_LEVEL = g["compliance"]["params"]["min"]
    R.ALLOWED_STAGES = set(g["series"]["params"]["stages"])
    R.INITIAL_EXTRA_PAGES = L["initial_extra_pages"]
    R.MAX_ROUNDS = L["max_extra_rounds"]
    R.PAGES_PER_ROUND = L["pages_per_round"]
    R.MAX_READ_ATTEMPTS = L["max_read_attempts"]
    R.MAX_CHARS_PER_SOURCE = L["max_chars_per_source"]
    R.MAX_REVISIONS = L["email_max_revisions"]
    R.CONTACT_PASS_TOTAL = int(t["contact_pass_total"])
    R.PAIN_PASS_TOTAL = int(t["pain_pass_total"])
    R.PAIN_MIN_EVIDENCE = int(t["pain_min_evidence_score"])
    R.EMAIL_PASS_TOTAL = int(t["email_pass_total"])
    R.WORDS_MIN, R.WORDS_MAX = int(t["email_words_tolerance_min"]), int(t["email_words_tolerance_max"])
    R.ROLE_PREFERENCE = list(cfg["rules"][("contact", "role_preference")]) + ["other"]
    R.APPROVED_CAPABILITIES = list(cfg["capabilities"])
    R.LEVELS = "\n".join(f"{d['key']} ({d['weight']}): " + "; ".join(f"{lv}={txt}" for lv, txt in sorted(cfg["levels"][d["key"]].items()))
                         for d in cfg["dimensions"])
    R.MANDATORY_CHECKS = list(cfg["rules"][("email", "mandatory_checks")])
    R.BANNED_PATTERNS = list(cfg["rules"][("email", "banned_patterns")])
    R.REASON_NOW_L1_MONTHS = int(t["reason_now_level1_months"])
    R.REASON_NOW_L2_MONTHS = int(t["reason_now_level2_months"])
    R.RUBRIC_VERSION = cfg["version"]
    return cfg
