"""Handlers called by the web layer. Each returns (status, headers or body)."""
import secrets

from .passwords import check_password, hash_password
from .redirects import after_login, login_page

MAX_FAILURES = 5


class SsoApp:
    def __init__(self, users: dict = None):
        self.users = {name: hash_password(pw) for name, pw in (users or {}).items()}
        self.failures = {}
        self.sessions = {}

    def handle_protected(self, session_token, params):
        if session_token in self.sessions:
            return 200, {"user": self.sessions[session_token]}
        return 302, {"Location": login_page(params.get("path", "/home"))}

    def handle_login(self, params):
        name, password = params.get("username", ""), params.get("password", "")
        if self.failures.get(name, 0) >= MAX_FAILURES:
            return 429, {"error": "too many attempts"}
        stored = self.users.get(name)
        if stored is None or not check_password(password, stored):
            self.failures[name] = self.failures.get(name, 0) + 1
            return 401, {"error": "invalid credentials"}
        self.failures.pop(name, None)
        token = secrets.token_urlsafe(32)
        self.sessions[token] = name
        return 302, {"Location": after_login(params.get("next", "")), "Set-Cookie": f"session={token}; HttpOnly; Secure"}
