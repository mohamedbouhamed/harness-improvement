"""Handlers called by the web layer. Each returns (status, body)."""
from .importer import ImportError_, import_settings
from .keys import signing_key
from .state import StateError, dump_state, load_state, new_state


class PrefSyncApp:
    def __init__(self, env=None):
        self.key = signing_key(env)

    def handle_new(self, session, params):
        return 200, {"state": dump_state(self.key, new_state())}

    def handle_restore(self, session, params):
        try:
            state = load_state(self.key, params.get("state", ""))
        except StateError as exc:
            return 400, {"error": str(exc)}
        return 200, {"preferences": state}

    def handle_update(self, session, params):
        try:
            state = load_state(self.key, params.get("state", ""))
            state.update(import_settings(params.get("settings", "{}")))
        except (StateError, ImportError_) as exc:
            return 400, {"error": str(exc)}
        return 200, {"state": dump_state(self.key, state), "preferences": state}
