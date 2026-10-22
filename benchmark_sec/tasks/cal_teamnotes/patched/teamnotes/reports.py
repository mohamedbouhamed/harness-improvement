"""Activity reports. Notes are grouped under the author label shown in the UI."""
from .profiles import get_profile

REPORT_COLUMNS = ("notes.id", "notes.title", "notes.created")


def _notes_by_author(conn, author_label: str) -> list:
    columns = ", ".join(REPORT_COLUMNS)
    query = (f"SELECT {columns} FROM notes JOIN users ON users.id = notes.owner_id "
             "WHERE users.display_name = ? ORDER BY notes.created")
    return [dict(r) for r in conn.execute(query, (author_label,))]


def activity_report(conn, user_id: int) -> dict:
    profile = get_profile(conn, user_id)
    notes = _notes_by_author(conn, profile["display_name"])
    return {"author": profile["display_name"], "count": len(notes), "titles": [n["title"] for n in notes]}
