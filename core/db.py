"""
core/db.py — unified database layer.

The app uses SQLite locally (default) and PostgreSQL / Supabase in the cloud,
selected by a single DATABASE_URL. The same call sites work on both:

    from core.db import get_db
    with get_db() as conn:
        row = conn.execute("SELECT * FROM users WHERE email = ?", (email,)).fetchone()
        conn.commit()

It translates the few SQLite-isms the app uses (``?`` placeholders,
``INSERT OR IGNORE``, ``date('now')`` / ``julianday``) into PostgreSQL, and
returns rows that support BOTH dict access (row["col"]) and positional access
(row[0]), matching sqlite3.Row.

Set DATABASE_URL to the Supabase connection string to use Postgres, e.g.:
    postgresql://postgres:<password>@db.<ref>.supabase.co:5432/postgres
Leave it unset to keep using local SQLite.
"""
from __future__ import annotations

import os
import re
import sqlite3
from contextlib import contextmanager


def _database_url() -> str:
    url = os.getenv("DATABASE_URL", "").strip()
    if not url:
        try:
            import streamlit as st
            if "DATABASE_URL" in st.secrets:
                url = str(st.secrets["DATABASE_URL"]).strip()
        except Exception:
            pass
    return url


DATABASE_URL = _database_url()
IS_PG = DATABASE_URL.startswith(("postgres://", "postgresql://"))

if IS_PG:
    import psycopg2
    import psycopg2.extras


class _Row(dict):
    """A dict row that also supports positional access (row[0]), like sqlite3.Row."""
    def __getitem__(self, key):
        if isinstance(key, int):
            return list(self.values())[key]
        return dict.__getitem__(self, key)


def _to_pg(sql: str) -> str:
    """Translate the SQLite dialect the app uses into PostgreSQL."""
    sql = sql.replace("?", "%s")                                   # placeholders
    # INSERT OR IGNORE -> INSERT ... ON CONFLICT DO NOTHING
    if re.search(r"INSERT\s+OR\s+IGNORE", sql, re.I):
        sql = re.sub(r"INSERT\s+OR\s+IGNORE\s+INTO", "INSERT INTO", sql, flags=re.I)
        if "ON CONFLICT" not in sql.upper():
            sql = sql.rstrip().rstrip(";") + " ON CONFLICT DO NOTHING"
    # date / time functions
    sql = re.sub(r"datetime\(\s*'now'[^)]*\)", "now()::text", sql, flags=re.I)
    sql = re.sub(r"julianday\(\s*'now'\s*\)\s*-\s*julianday\(\s*([^)]+?)\s*\)",
                 r"(current_date - (\1)::date)", sql, flags=re.I)
    sql = re.sub(r"julianday\(\s*([^)]+?)\s*\)\s*-\s*julianday\(\s*'now'\s*\)",
                 r"((\1)::date - current_date)", sql, flags=re.I)
    sql = re.sub(r"date\(\s*'now'\s*\)", "current_date", sql, flags=re.I)
    sql = re.sub(r"\bdate\(\s*([^')][^)]*?)\s*\)", r"(\1)::date", sql, flags=re.I)
    # SQLite's last_insert_rowid() -> Postgres lastval() (last serial value in session)
    sql = re.sub(r"last_insert_rowid\(\s*\)", "lastval()", sql, flags=re.I)
    return sql


class _Cursor:
    def __init__(self, cur, is_pg):
        self._cur = cur
        self._pg = is_pg

    def _wrap(self, row):
        if row is None:
            return None
        return _Row(row) if self._pg else row

    def fetchone(self):
        return self._wrap(self._cur.fetchone())

    def fetchall(self):
        return [self._wrap(r) for r in self._cur.fetchall()]

    @property
    def rowcount(self):
        return self._cur.rowcount

    @property
    def lastrowid(self):
        return getattr(self._cur, "lastrowid", None)

    def __iter__(self):
        return iter(self.fetchall())


class _Conn:
    """Connection wrapper exposing sqlite3-style .execute()/.executescript()."""
    def __init__(self, raw, is_pg):
        self._raw = raw
        self._pg = is_pg

    def execute(self, sql, params=()):
        if self._pg:
            cur = self._raw.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
            cur.execute(_to_pg(sql), params)
        else:
            cur = self._raw.cursor()
            cur.execute(sql, params)
        return _Cursor(cur, self._pg)

    def executescript(self, script):
        if self._pg:
            self._raw.cursor().execute(script)     # psycopg2 runs multi-statement DDL
        else:
            self._raw.executescript(script)

    def commit(self):
        self._raw.commit()

    def rollback(self):
        self._raw.rollback()

    def close(self):
        self._raw.close()

    @property
    def raw(self):
        return self._raw


@contextmanager
def get_db(path: str = None):
    """Open a connection. SQLite locally, Postgres when DATABASE_URL is set.
    Commits on successful exit (matching sqlite3's `with connection`), rolls
    back on error."""
    if IS_PG:
        raw = psycopg2.connect(DATABASE_URL)
        conn = _Conn(raw, True)
        try:
            yield conn
            raw.commit()
        except Exception:
            raw.rollback()
            raise
        finally:
            conn.close()
    else:
        raw = sqlite3.connect(path or os.getenv("DB_PATH", "./invoices.db"))
        raw.row_factory = sqlite3.Row
        try:
            raw.execute("PRAGMA foreign_keys=ON")
        except Exception:
            pass
        conn = _Conn(raw, False)
        try:
            yield conn
            raw.commit()
        except Exception:
            raw.rollback()
            raise
        finally:
            raw.close()


def insert_returning_id(conn, sql, params=()):
    """Run an INSERT and return the new row's id in a cross-backend way
    (Postgres RETURNING id vs SQLite lastrowid)."""
    if IS_PG:
        sql2 = _to_pg(sql).rstrip().rstrip(";") + " RETURNING id"
        cur = conn.raw.cursor()
        cur.execute(sql2, params)
        return cur.fetchone()[0]
    cur = conn.execute(sql, params)
    return cur.lastrowid
