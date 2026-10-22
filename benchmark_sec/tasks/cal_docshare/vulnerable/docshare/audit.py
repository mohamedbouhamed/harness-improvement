"""Append-only audit trail."""
import json
import time


class AuditLog:
    def __init__(self, settings):
        self.path = settings.audit_path

    def record(self, user: str, action: str, target: str) -> None:
        entry = {"ts": round(time.time(), 3), "user": user, "action": action, "target": target}
        with open(self.path, "a", encoding="utf-8") as fh:
            fh.write(json.dumps(entry) + "\n")

    def tail(self, count: int = 20) -> list:
        try:
            with open(self.path, encoding="utf-8") as fh:
                lines = fh.readlines()
        except FileNotFoundError:
            return []
        return [json.loads(line) for line in lines[-count:]]
