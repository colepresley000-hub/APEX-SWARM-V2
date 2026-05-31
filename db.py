"""
APEX SWARM — Database connection layer.

PostgreSQL (production, via DATABASE_URL) with a transparent SQLite fallback for
local dev. The PgConnectionWrapper makes a psycopg2 connection quack like a
sqlite3 connection (conn.execute(...) returns a cursor, "?" placeholders work),
so the rest of the app writes one flavor of SQL regardless of backend.

Behavior note: straight extraction from main.py. Schema creation / migrations
(init_db) intentionally stay in main.py because they wire up many module-level
globals there — only the pure connection primitives live here.
"""
import logging
import sqlite3

from config import DATABASE_PATH

logger = logging.getLogger("apex-swarm")

DATABASE_URL = __import__("os").getenv("DATABASE_URL", "")
USE_POSTGRES = bool(DATABASE_URL)

if USE_POSTGRES:
    try:
        import psycopg2
        import psycopg2.extras
        logger.info("✅ PostgreSQL driver loaded")
    except ImportError:
        USE_POSTGRES = False
        logger.warning("⚠️ psycopg2 not installed — falling back to SQLite")


class PgConnectionWrapper:
    """Wraps psycopg2 connection to behave like SQLite (conn.execute returns cursor with fetchone/fetchall)."""

    def __init__(self, conn):
        self._conn = conn

    def execute(self, sql, params=None):
        # Convert ? to %s for Postgres
        sql = sql.replace("?", "%s")
        cur = self._conn.cursor()
        cur.execute(sql, params or ())
        return cur

    def executescript(self, sql):
        cur = self._conn.cursor()
        cur.execute(sql)
        return cur

    def commit(self):
        self._conn.commit()

    def close(self):
        self._conn.close()

    def cursor(self):
        return self._conn.cursor()

    @property
    def autocommit(self):
        return self._conn.autocommit

    @autocommit.setter
    def autocommit(self, val):
        self._conn.autocommit = val


def get_db():
    if USE_POSTGRES:
        raw = psycopg2.connect(DATABASE_URL)
        raw.autocommit = False
        return PgConnectionWrapper(raw)
    else:
        conn = sqlite3.connect(DATABASE_PATH)
        conn.execute("PRAGMA journal_mode=WAL")
        return conn


def db_execute(conn, sql, params=None):
    """Execute SQL — wrapper handles Postgres compatibility."""
    return conn.execute(sql, params or ())


def db_fetchone(conn, sql, params=None):
    cur = conn.execute(sql, params or ())
    return cur.fetchone()


def db_fetchall(conn, sql, params=None):
    cur = conn.execute(sql, params or ())
    return cur.fetchall()
