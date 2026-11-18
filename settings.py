"""Shared settings: paths, .env loading and non-rubric constants.
Scoring rules and thresholds stay in rubric.py. Secrets are read from .env and never printed or logged."""
import os, json, threading, datetime as dt

BASE = os.path.dirname(os.path.abspath(__file__))
OUT = os.environ.get("SPRINTO_OUT") or os.path.join(BASE, "output")   # override only for tests/screenshots
CACHE = os.path.join(OUT, "cache")

_ENV = {}

def load_env():
    p = os.path.join(BASE, ".env")
    if os.path.exists(p):
        for line in open(p, encoding="utf-8"):
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                _ENV[k.strip()] = v.strip().strip('"').strip("'")

def env(key, default=None):
    """Real environment wins over .env. Empty values count as missing."""
    return os.environ.get(key) or _ENV.get(key) or default

load_env()

# ---- non-rubric constants (flagged as my assumptions in README) ----
PAGE_CACHE_DAYS = 7            # a cached page is reused for a week, then fetched again
MIN_SAMPLE_FOR_VERDICT = 5     # fewer reviewed companies than this = "not enough data" for kill criteria
BASELINE_MINUTES = 15          # low end of the Growth Lead's 15-20 min manual baseline
TARGET_MINUTES = 2             # slide 02: under 2 minutes per company
# Gemini price per 1M tokens in USD (paid tier, from Google's pricing page; free tier actually costs $0).
# Keyed by model name. Unknown model -> cost shown as "unknown", never guessed.
GEMINI_PRICE = {"gemini-2.5-flash": {"in": 0.30, "out": 2.50},
                "gemini-3.8-flash": {"in": 0.75, "out": 3.75}}   # intro price until 31 Dec 2026, then 1.50 / 7.50

def today():
    return dt.date.today().isoformat()

def now_iso():
    return dt.datetime.now().replace(microsecond=0).isoformat()

# ---- state files: one folder per company, atomic writes, one lock ----
_LOCK = threading.RLock()

def company_dir(domain):
    d = os.path.join(OUT, domain)
    os.makedirs(d, exist_ok=True)
    return d

def state_path(domain):
    return os.path.join(OUT, domain, "state.json")

def read_state(domain):
    try:
        with open(state_path(domain), encoding="utf-8") as f:
            return json.load(f)
    except (OSError, json.JSONDecodeError):
        return None

def write_state(st):
    with _LOCK:
        d = company_dir(st["domain"])
        tmp = os.path.join(d, "state.json.tmp")
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(st, f, indent=2, default=str)
        os.replace(tmp, state_path(st["domain"]))

def update_state(domain, fn):
    """Read-modify-write under the lock. fn(state) edits it in place."""
    with _LOCK:
        st = read_state(domain)
        if st is None:
            return None
        fn(st)
        write_state(st)
        return st

def all_states():
    out = []
    if os.path.isdir(OUT):
        for name in sorted(os.listdir(OUT)):
            st = read_state(name) if name != "cache" else None
            if st:
                out.append(st)
    return out
