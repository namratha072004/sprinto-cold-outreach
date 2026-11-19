"""Neon Postgres helper. One cached connection per process, reconnects once if it dropped.
Secrets: DATABASE_URL is read from .env / Vercel env and never printed."""
import json, os, re, time
import psycopg2, psycopg2.extras
import settings as S

import threading
_T = threading.local()
class _Slot:
    """_C["conn"] keeps working for tests, but each thread gets its own connection."""
    def __getitem__(self, k): return getattr(_T, "conn", None)
    def __setitem__(self, k, v): _T.conn = v
_C = _Slot()

def conn():
    c = _C["conn"]
    if c is None or c.closed:
        url = S.env("DATABASE_URL")
        if not url:
            raise RuntimeError("DATABASE_URL is missing.")
        if os.environ.get("SPRINTO_DB_SCHEMA"):               # tests: use the DIRECT connection. Neon's pooler shares sessions, so a SET would leak to other clients
            url = url.replace("-pooler", "")
        c = _C["conn"] = psycopg2.connect(url, connect_timeout=20)
        c.autocommit = True
        schema = os.environ.get("SPRINTO_DB_SCHEMA")          # tests only: a throwaway schema (direct connection, closed afterwards)
        if schema:
            if not re.fullmatch(r"[a-z0-9_]+", schema):
                raise RuntimeError("bad schema name")
            with c.cursor() as cur:
                cur.execute(f"SET search_path TO {schema}")
    return c

def _run(sql, params, fetch):
    """Runs one statement. A dropped or hiccuping connection (SSL error, server closed it, no free socket) is retried on a fresh connection, up to 3 tries."""
    last = None
    for attempt in range(1, 4):
        try:
            with conn().cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
                cur.execute(sql, params)
                return cur.fetchall() if fetch and cur.description else cur.rowcount
        except (psycopg2.OperationalError, psycopg2.InterfaceError, psycopg2.DatabaseError) as e:
            if isinstance(e, (psycopg2.errors.SyntaxError, psycopg2.IntegrityError, psycopg2.ProgrammingError, psycopg2.DataError)):
                raise                                           # a real mistake in the statement: do not retry
            last = e
            try:
                if _C["conn"] is not None:
                    _C["conn"].close()
            except Exception:
                pass
            _C["conn"] = None
            time.sleep(0.4 * attempt)
    raise RuntimeError("Database connection failed.") from last

def q(sql, params=()):
    """Run a query, return a list of dict rows."""
    return _run(sql, params, True)

def one(sql, params=()):
    rows = q(sql, params)
    return rows[0] if rows else None

def x(sql, params=()):
    """Run a statement, return the row count."""
    return _run(sql, params, False)

def J(obj):
    """Wrap a Python value for a jsonb column."""
    return psycopg2.extras.Json(obj, dumps=lambda o: json.dumps(o, default=str))

import contextlib

@contextlib.contextmanager
def transaction():
    """All-or-nothing block on this thread's connection."""
    c = conn()
    c.autocommit = False
    try:
        yield c.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
        c.commit()
    except Exception:
        c.rollback()
        raise
    finally:
        c.autocommit = True
