import os
import sqlite3
from typing import Optional

DB_PATH = os.getenv("DB_PATH", "tri_buddy_os.db")


def get_db_connection() -> sqlite3.Connection:
    return sqlite3.connect(DB_PATH)


def init_db() -> None:
    with get_db_connection() as conn:
        # CREATE TABLE IF NOT EXISTS, never DROP: init_db() runs on every app start,
        # so dropping here wiped every task and lead on each restart and deploy.
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
    existing = {row[1] for row in conn.execute("PRAGMA table_info(leads)").fetchall()}
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
