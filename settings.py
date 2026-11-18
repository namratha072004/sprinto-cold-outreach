"""Shared settings: paths, .env loading and non-rubric constants.
Scoring rules and thresholds stay in rubric.py. Secrets are read from .env and never printed or logged."""
import os, json, threading, datetime as dt

BASE = os.path.dirname(os.path.abspath(__file__))
HOSTED = bool(os.environ.get("VERCEL"))      # True on Vercel: results live in the Neon database instead of files
OUT = os.environ.get("SPRINTO_OUT") or os.path.join(BASE, "output")   # SPRINTO_OUT only for tests/screenshots
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

# ---- storage: local files on your Mac, or a Neon Postgres table when hosted on Vercel ----
# kinds: state (one per company), sources (page text per company), cache (pages + Hunter results), meta (counters)
def _remote():
    """Database mode: always on Vercel; on your Mac only if SPRINTO_USE_DB=1 is set (used by the upload script)."""
    return HOSTED or bool(os.environ.get("SPRINTO_USE_DB"))

_DB = {"conn": None, "ready": False}

def _db():
    """One cached connection per process, reconnected if it dropped (serverless instances freeze and thaw)."""
    import psycopg2
    url = env("DATABASE_URL")
    if not url:
        raise RuntimeError("Database not configured: DATABASE_URL is missing.")
    c = _DB["conn"]
    if c is None or c.closed:
        c = _DB["conn"] = psycopg2.connect(url, connect_timeout=15)
        c.autocommit = True
        _DB["ready"] = False
    if not _DB["ready"]:
        with c.cursor() as cur:
            cur.execute("CREATE TABLE IF NOT EXISTS kv (kind text NOT NULL, key text NOT NULL, data jsonb NOT NULL, "
                        "updated_at timestamptz NOT NULL DEFAULT now(), PRIMARY KEY (kind, key))")
        _DB["ready"] = True
    return c

def _sql(query, params=()):
    import psycopg2
    for attempt in (1, 2):
        try:
            with _db().cursor() as cur:
                cur.execute(query, params)
                return cur.fetchall() if cur.description else []
        except (psycopg2.OperationalError, psycopg2.InterfaceError):
            _DB["conn"] = None                       # stale connection: reconnect once
            if attempt == 2:
                raise RuntimeError("Database connection failed.")

def _file_path(kind, key):
    if kind == "state":
        return os.path.join(OUT, key, "state.json")
    if kind == "sources":
        return os.path.join(OUT, key, "sources.json")
    return os.path.join(CACHE, key + ".json")      # cache + meta

def kv_get(kind, key):
    if _remote():
        rows = _sql("SELECT data FROM kv WHERE kind=%s AND key=%s", (kind, key))
        return rows[0][0] if rows else None
    try:
        with open(_file_path(kind, key), encoding="utf-8") as f:
            return json.load(f)
    except (OSError, json.JSONDecodeError):
        return None

def kv_put(kind, key, obj):
    if _remote():
        _sql("INSERT INTO kv (kind, key, data) VALUES (%s, %s, %s::jsonb) "
             "ON CONFLICT (kind, key) DO UPDATE SET data = EXCLUDED.data, updated_at = now()",
             (kind, key, json.dumps(obj, default=str)))
        return
    p = _file_path(kind, key)
    os.makedirs(os.path.dirname(p), exist_ok=True)
    tmp = p + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(obj, f, indent=2 if kind == "state" else None, default=str)
    os.replace(tmp, p)

def kv_list(kind):
    if _remote():
        return [r[0] for r in _sql("SELECT data FROM kv WHERE kind=%s ORDER BY key", (kind,))]
    out = []
    if kind == "state" and os.path.isdir(OUT):
        for name in sorted(os.listdir(OUT)):
            st = kv_get("state", name) if name != "cache" else None
            if st:
                out.append(st)
    return out

# ---- state files: one record per company, one lock ----
_LOCK = threading.RLock()

def company_dir(domain):
    d = os.path.join(OUT, domain)
    os.makedirs(d, exist_ok=True)
    return d

def state_path(domain):
    return _file_path("state", domain)

def read_state(domain):
    return kv_get("state", domain)

def write_state(st):
    with _LOCK:
        st["updated_at"] = now_iso()
        kv_put("state", st["domain"], st)

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
    return kv_list("state")
