import logging
import os
import re
import sqlite3
from typing import Any, Iterable, List, Optional, Set

DB_PATH = os.getenv("DB_PATH", "tri_buddy_os.db")

# Every table this app owns lives in its own schema. The names it uses - leads,
# tasks, reports - are exactly what a business product would also call its tables,
# so sharing "public" with another application risks colliding with real data.
DEFAULT_SCHEMA = "tri_buddy"


def connect_timeout() -> int:
    try:
        return max(1, int(os.getenv("DB_CONNECT_TIMEOUT", "5")))
    except ValueError:
        return 5


def database_url() -> str:
    return os.getenv("DATABASE_URL", "").strip()


def using_postgres() -> bool:
    return bool(database_url())


def schema_name() -> str:
    name = os.getenv("DB_SCHEMA", DEFAULT_SCHEMA).strip() or DEFAULT_SCHEMA
    if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", name):
        raise ValueError(f"Invalid DB_SCHEMA: {name!r}")
    if name.lower() == "public" and os.getenv("DB_ALLOW_PUBLIC_SCHEMA") != "1":
        raise ValueError(
            "Refusing to use the public schema: this app's table names (leads, tasks, "
            "reports, memories) are likely to collide with another product's. Set "
            "DB_SCHEMA to a dedicated schema, or DB_ALLOW_PUBLIC_SCHEMA=1 to override."
        )
    return name


# --- SQL translation ------------------------------------------------------------

_AUTOINC = re.compile(r"INTEGER\s+PRIMARY\s+KEY\s+AUTOINCREMENT", re.IGNORECASE)


def to_postgres(sql: str) -> str:
    sql = _AUTOINC.sub("SERIAL PRIMARY KEY", sql)
    return sql.replace("?", "%s")


class _PgCursor:
    """Just enough of the sqlite3 cursor API for this codebase."""

    def __init__(self, cursor, lastrowid: Optional[int] = None) -> None:
        self._cursor = cursor
        self.lastrowid = lastrowid

    def fetchone(self):
        return self._cursor.fetchone()

    def fetchall(self):
        return self._cursor.fetchall()


class _PgConnection:
    """Adapts psycopg to the sqlite3 connection API the tools already use."""

    def __init__(self, conn) -> None:
        self._conn = conn

    def execute(self, sql: str, params: Iterable[Any] = ()) -> _PgCursor:
        translated = to_postgres(sql)
        is_insert = translated.lstrip().upper().startswith("INSERT")
        wants_id = is_insert and "RETURNING" not in translated.upper()
        if wants_id:
            translated += " RETURNING id"

        cursor = self._conn.cursor()
        cursor.execute(translated, tuple(params))

        lastrowid = None
        if wants_id:
            row = cursor.fetchone()
            lastrowid = row[0] if row else None
        return _PgCursor(cursor, lastrowid)

    def commit(self) -> None:
        self._conn.commit()

    def close(self) -> None:
        self._conn.close()

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        if exc_type:
            self._conn.rollback()
        else:
            self._conn.commit()
        self._conn.close()
        return False


def get_db_connection():
    if using_postgres():
        import psycopg

        schema = schema_name()
        # search_path is set as a startup option rather than a SET statement. A SET is
        # session state, and a transaction-mode pooler can hand the next statement to a
        # different backend session, which would silently drop it and let unqualified
        # names resolve against public. A startup option is applied per connection.
        # connect_timeout is essential, not a nicety. libpq tries every address the
        # host resolves to, and without a bound a misconfigured DATABASE_URL makes
        # /health hang - which makes Render's health check fail and leaves a deploy
        # stuck "in progress" forever.
        conn = psycopg.connect(
            database_url(),
            options=f"-c search_path={schema}",
            connect_timeout=connect_timeout(),
        )
        with conn.cursor() as cursor:
            # Explicitly named, so it works even before search_path resolves.
            cursor.execute(f'CREATE SCHEMA IF NOT EXISTS "{schema}"')
            cursor.execute(f'SET search_path TO "{schema}"')
        conn.commit()
        return _PgConnection(conn)

    return sqlite3.connect(DB_PATH)


def table_columns(conn, table: str) -> Set[str]:
    """Column names for a table, on either backend."""
    if using_postgres():
        rows = conn.execute(
            "SELECT column_name FROM information_schema.columns "
            "WHERE table_schema = ? AND table_name = ?",
            (schema_name(), table),
        ).fetchall()
        return {row[0] for row in rows}
    return {row[1] for row in conn.execute(f"PRAGMA table_info({table})").fetchall()}


# --- schema ---------------------------------------------------------------------

_last_init_error: Optional[str] = None


def last_init_error() -> Optional[str]:
    return _last_init_error


def db_status() -> dict:
    """Backend, schema and whether a connection actually works."""
    status = {
        "backend": "postgres" if using_postgres() else "sqlite",
        "schema": schema_name() if using_postgres() else None,
    }
    try:
        with get_db_connection() as conn:
            conn.execute("SELECT 1").fetchone()
        status["connected"] = True
    except Exception as error:  # noqa: BLE001
        status["connected"] = False
        status["error"] = str(error)[:200]
    if _last_init_error:
        status["init_error"] = _last_init_error
    return status


def init_db() -> None:
    """Create the schema. Never fatal: a bad DATABASE_URL must not stop the app
    from booting, because a service that will not start cannot be diagnosed."""
    global _last_init_error
    try:
        _init_db()
        _last_init_error = None
    except Exception as error:  # noqa: BLE001
        _last_init_error = str(error)[:300]
        logging.getLogger(__name__).error(
            "Database init failed; the app will start but storage is unavailable | "
            "backend=%s error=%s",
            "postgres" if using_postgres() else "sqlite", error,
        )


def _init_db() -> None:
    with get_db_connection() as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS tasks (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                title TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'pending',
                assignee TEXT
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS leads (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL,
                stage TEXT NOT NULL DEFAULT 'new',
                notes TEXT
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS memories (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                key TEXT NOT NULL UNIQUE,
                value TEXT NOT NULL
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS reports (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                slack_user_id TEXT NOT NULL,
                user_name TEXT,
                text TEXT NOT NULL,
                created_at TEXT NOT NULL
            )
            """
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_reports_user ON reports (slack_user_id)"
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS seo_audits (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                url TEXT NOT NULL,
                checked_at TEXT NOT NULL,
                passed INTEGER NOT NULL,
                total INTEGER NOT NULL,
                payload TEXT NOT NULL
            )
            """
        )
        _migrate_leads(conn)
        conn.commit()


def _migrate_leads(conn) -> None:
    """Widen the original leads table in place.

    It shipped as (id, name, stage, notes); lead capture needs contact details,
    a score and provenance. ALTER TABLE ADD COLUMN keeps existing rows.
    """
    existing = table_columns(conn, "leads")
    columns = {
        "lead_ref": "TEXT",
        "phone": "TEXT",
        "business_type": "TEXT",
        "score": "INTEGER NOT NULL DEFAULT 0",
        "source": "TEXT",
        "created_at": "TEXT",
    }
    for column, ddl in columns.items():
        if column not in existing:
            conn.execute(f"ALTER TABLE leads ADD COLUMN {column} {ddl}")
