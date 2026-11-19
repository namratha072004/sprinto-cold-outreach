"""Shared helper: a throwaway Postgres schema in Neon (direct connection) so tests exercise real SQL and never touch real data."""
import os, sys, uuid, unittest
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import settings as S

def create():
    if not S.env("DATABASE_URL"):
        raise unittest.SkipTest("DATABASE_URL not set")
    import psycopg2
    schema = "t_" + uuid.uuid4().hex[:10]
    with psycopg2.connect(S.env("DATABASE_URL").replace("-pooler", ""), connect_timeout=30) as c:
        c.autocommit = True
        c.cursor().execute(f"CREATE SCHEMA {schema}")
    os.environ["SPRINTO_DB_SCHEMA"] = schema
    import db
    db._C["conn"] = None
    db.conn().cursor().execute(open(os.path.join(S.BASE, "schema.sql"), encoding="utf-8").read())
    db.x("INSERT INTO rubric_versions (id, version, source, is_active) VALUES (1, 'test', 'test', true)")
    return schema

def drop(schema):
    import db, psycopg2
    if db._C["conn"] is not None:
        db._C["conn"].close()
    db._C["conn"] = None
    os.environ.pop("SPRINTO_DB_SCHEMA", None)
    with psycopg2.connect(S.env("DATABASE_URL").replace("-pooler", ""), connect_timeout=30) as c:
        c.autocommit = True
        c.cursor().execute(f"DROP SCHEMA {schema} CASCADE")
