import os
import sqlite3
from typing import Optional

DB_PATH = os.getenv("DB_PATH", "state.db")

def _conn():
    return sqlite3.connect(DB_PATH, check_same_thread=False)

def init_db():
    with _conn() as con:
        con.execute("""
        CREATE TABLE IF NOT EXISTS kv (
            key TEXT PRIMARY KEY,
            value TEXT NOT NULL
        )
        """)
        con.commit()

def get(key: str, default: Optional[str] = None) -> Optional[str]:
    with _conn() as con:
        cur = con.execute("SELECT value FROM kv WHERE key=?", (key,))
        row = cur.fetchone()
        return row[0] if row else default

def set(key: str, value: str) -> None:
    with _conn() as con:
        con.execute(
            "INSERT INTO kv(key,value) VALUES(?,?) "
            "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
            (key, value),
        )
        con.commit()
