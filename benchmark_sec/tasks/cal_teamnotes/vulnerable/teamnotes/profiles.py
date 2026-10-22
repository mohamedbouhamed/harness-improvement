"""Profile settings."""

SORT_OPTIONS = ("created", "title")
MAX_NAME = 60


class ProfileError(ValueError):
    pass


def get_profile(conn, user_id: int) -> dict:
    row = conn.execute("SELECT username, display_name, sort_pref FROM users WHERE id = ?", (user_id,)).fetchone()
    if row is None:
        raise ProfileError("unknown user")
    return dict(row)


def update_profile(conn, user_id: int, display_name: str = None, sort_pref: str = None) -> dict:
    if display_name is not None:
        display_name = display_name.strip()
        if not display_name or len(display_name) > MAX_NAME:
            raise ProfileError("display name must be 1-60 characters")
        conn.execute("UPDATE users SET display_name = ? WHERE id = ?", (display_name, user_id))
    if sort_pref is not None:
        if sort_pref not in SORT_OPTIONS:
            raise ProfileError(f"sort must be one of {SORT_OPTIONS}")
        conn.execute("UPDATE users SET sort_pref = ? WHERE id = ?", (sort_pref, user_id))
    conn.commit()
    return get_profile(conn, user_id)
