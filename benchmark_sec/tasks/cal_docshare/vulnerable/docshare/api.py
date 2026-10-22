"""Request handlers called by the HTTP layer. Each returns (status, body)."""
from .audit import AuditLog
from .storage import DocumentStore, StorageError
from .uploads import UploadArea, UploadError


class DocshareApp:
    def __init__(self, settings):
        self.store = DocumentStore(settings)
        self.uploads = UploadArea(settings)
        self.audit = AuditLog(settings)

    def handle_browse(self, session: dict, params: dict):
        folder = params.get("folder", "")
        try:
            entries = self.store.list_folder(folder)
        except StorageError as exc:
            return 404, {"error": str(exc)}
        return 200, {"folder": folder, "entries": entries}

    def handle_download(self, session: dict, params: dict):
        name = params.get("name", "")
        try:
            data = self.store.read(name)
        except StorageError as exc:
            return 404, {"error": str(exc)}
        self.audit.record(session["user"], "download", name)
        return 200, data

    def handle_upload(self, session: dict, params: dict):
        try:
            stored = self.uploads.save(session["user"], params.get("filename", ""), params.get("content", b""))
        except UploadError as exc:
            return 400, {"error": str(exc)}
        self.audit.record(session["user"], "upload", stored)
        return 201, {"stored": stored}

    def handle_my_uploads(self, session: dict, params: dict):
        return 200, {"files": self.uploads.list_for(session["user"])}
