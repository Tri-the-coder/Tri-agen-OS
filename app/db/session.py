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
        conn.commit()
