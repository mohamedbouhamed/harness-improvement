"""Handlers called by the web layer. Each returns (status, body)."""
from . import probe, thumbs
from .settings import MediaSettings


class MediaApp:
    def __init__(self, settings: MediaSettings = MediaSettings()):
        self.settings = settings

    def handle_inspect(self, session, params):
        try:
            return 200, probe.describe(self.settings, params.get("filename", ""))
        except probe.ProbeError as exc:
            return 400, {"error": str(exc)}

    def handle_thumbnail(self, session, params):
        try:
            path = thumbs.thumbnail(self.settings, params.get("filename", ""), params.get("size", ""))
        except thumbs.ThumbError as exc:
            return 400, {"error": str(exc)}
        return 201, {"thumbnail": path}

    def handle_status(self, session, params):
        return 200, {"disk": probe.disk_usage(self.settings)}
