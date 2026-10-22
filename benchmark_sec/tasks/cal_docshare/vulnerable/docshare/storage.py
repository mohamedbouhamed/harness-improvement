"""File access confined to the document library."""
import os


class StorageError(Exception):
    """Raised for missing or forbidden documents."""


class DocumentStore:
    def __init__(self, settings):
        self.settings = settings
        self.root = os.path.normpath(settings.docs_root)

    def _resolve(self, relative_name: str) -> str:
        if not relative_name or "\x00" in relative_name:
            raise StorageError("invalid document name")
        candidate = os.path.normpath(os.path.join(self.root, relative_name))
        if not candidate.startswith(self.root):
            raise StorageError("document is outside the library")
        return candidate

    def _visible(self, name: str) -> bool:
        return not name.startswith(self.settings.hidden_prefixes)

    def read(self, relative_name: str) -> bytes:
        path = self._resolve(relative_name)
        if not os.path.isfile(path):
            raise StorageError("no such document")
        with open(path, "rb") as fh:
            return fh.read()

    def list_folder(self, folder: str = "") -> list:
        path = self._resolve(folder or ".")
        if not os.path.isdir(path):
            raise StorageError("no such folder")
        entries = []
        for name in sorted(os.listdir(path)):
            if not self._visible(name):
                continue
            full = os.path.join(path, name)
            kind = "folder" if os.path.isdir(full) else "file"
            size = os.path.getsize(full) if kind == "file" else None
            entries.append({"name": name, "kind": kind, "size": size})
        return entries

    def size(self, relative_name: str) -> int:
        return os.path.getsize(self._resolve(relative_name))
