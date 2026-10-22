"""SQLite connection and schema."""
import sqlite3

SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
    id INTEGER PRIMARY KEY,
    username TEXT UNIQUE NOT NULL,
    display_name TEXT NOT NULL,
    sort_pref TEXT NOT NULL DEFAULT 'created'
);
CREATE TABLE IF NOT EXISTS notes (
    id INTEGER PRIMARY KEY,
    owner_id INTEGER NOT NULL REFERENCES users(id),
    title TEXT NOT NULL,
    body TEXT NOT NULL,
    created REAL NOT NULL
);
"""


def connect(path: str = ":memory:"):
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    conn.executescript(SCHEMA)
    return conn


def create_user(conn, username: str, display_name: str) -> int:
    cur = conn.execute("INSERT INTO users (username, display_name) VALUES (?, ?)", (username, display_name))
    conn.commit()
    return cur.lastrowid
