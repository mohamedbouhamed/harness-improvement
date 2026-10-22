"""Handlers called by the web layer. Each returns (status, body)."""
from . import notes, profiles, reports


class TeamNotesApp:
    def __init__(self, conn):
        self.conn = conn

    def handle_update_profile(self, session, params):
        try:
            body = profiles.update_profile(self.conn, session["user_id"], params.get("display_name"),
                                           params.get("sort"))
        except profiles.ProfileError as exc:
            return 400, {"error": str(exc)}
        return 200, body

    def handle_create_note(self, session, params):
        try:
            note_id = notes.create_note(self.conn, session["user_id"], params.get("title", ""), params.get("body", ""))
        except ValueError as exc:
            return 400, {"error": str(exc)}
        return 201, {"id": note_id}

    def handle_list(self, session, params):
        sort = params.get("sort") or profiles.get_profile(self.conn, session["user_id"])["sort_pref"]
        return 200, {"notes": notes.list_notes(self.conn, session["user_id"], sort)}

    def handle_search(self, session, params):
        return 200, {"notes": notes.search_notes(self.conn, session["user_id"], params.get("q", ""))}

    def handle_activity(self, session, params):
        return 200, reports.activity_report(self.conn, session["user_id"])
