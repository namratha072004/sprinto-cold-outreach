"""Neon Postgres helper. One cached connection per process, reconnects once if it dropped.
Secrets: DATABASE_URL is read from .env / Vercel env and never printed."""
import json, os, re
import psycopg2, psycopg2.extras
import settings as S

_C = {"conn": None}

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
    for attempt in (1, 2):
        try:
            with conn().cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
                cur.execute(sql, params)
                return cur.fetchall() if fetch and cur.description else cur.rowcount
        except (psycopg2.OperationalError, psycopg2.InterfaceError):
            _C["conn"] = None
            if attempt == 2:
                raise RuntimeError("Database connection failed.")

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
