import os
import sqlite3
from typing import Optional

DB_PATH = os.getenv("DB_PATH", "tri_buddy_os.db")


def get_db_connection() -> sqlite3.Connection:
    return sqlite3.connect(DB_PATH)


def init_db() -> None:
    with get_db_connection() as conn:
        conn.execute("DROP TABLE IF EXISTS tasks")
        conn.execute(
            """
            CREATE TABLE tasks (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                title TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'pending',
                assignee TEXT
            )
            """
        )
        conn.execute("DROP TABLE IF EXISTS leads")
        conn.execute(
            """
            CREATE TABLE leads (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL,
                stage TEXT NOT NULL DEFAULT 'new',
                notes TEXT
            )
            """
        )
        conn.commit()
