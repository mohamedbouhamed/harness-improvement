"""Notes: create, list and search."""
import time

ORDER_BY = {"created": "created DESC", "title": "title COLLATE NOCASE ASC"}


def create_note(conn, owner_id: int, title: str, body: str) -> int:
    if not title.strip():
        raise ValueError("title required")
    cur = conn.execute("INSERT INTO notes (owner_id, title, body, created) VALUES (?, ?, ?, ?)",
                       (owner_id, title.strip(), body, time.time()))
    conn.commit()
    return cur.lastrowid


def list_notes(conn, owner_id: int, sort: str = "created") -> list:
    order = ORDER_BY.get(sort, ORDER_BY["created"])
    rows = conn.execute(f"SELECT id, title, created FROM notes WHERE owner_id = ? ORDER BY {order}", (owner_id,))
    return [dict(r) for r in rows]


def search_notes(conn, owner_id: int, term: str) -> list:
    pattern = "%" + term.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%"
    rows = conn.execute("SELECT id, title FROM notes WHERE owner_id = ? AND (title LIKE ? ESCAPE '\\' "
                        "OR body LIKE ? ESCAPE '\\') ORDER BY id", (owner_id, pattern, pattern))
    return [dict(r) for r in rows]
