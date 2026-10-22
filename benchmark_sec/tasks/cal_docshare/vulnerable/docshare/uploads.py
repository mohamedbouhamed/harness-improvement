"""Storage for files uploaded by staff, kept apart from the curated library."""
import os
import re
import time

_SAFE_CHARS = re.compile(r"[^A-Za-z0-9._-]+")


class UploadError(Exception):
    pass


def clean_filename(filename: str) -> str:
    """Keep only the last path component and a conservative character set."""
    base = os.path.basename(filename.replace("\\", "/"))
    base = _SAFE_CHARS.sub("_", base).lstrip(".")
    if not base:
        raise UploadError("empty file name")
    return base[:120]


class UploadArea:
    def __init__(self, settings):
        self.settings = settings
        self.root = settings.upload_root

    def save(self, user: str, filename: str, content: bytes) -> str:
        name = clean_filename(filename)
        ext = os.path.splitext(name)[1].lower()
        if ext not in self.settings.allowed_extensions:
            raise UploadError(f"extension {ext or '(none)'} is not allowed")
        if len(content) > self.settings.max_upload_bytes:
            raise UploadError("file too large")
        folder = os.path.join(self.root, user)
        os.makedirs(folder, exist_ok=True)
        stored = f"{int(time.time())}-{name}"
        with open(os.path.join(folder, stored), "wb") as fh:
            fh.write(content)
        return stored

    def list_for(self, user: str) -> list:
        folder = os.path.join(self.root, user)
        if not os.path.isdir(folder):
            return []
        return sorted(os.listdir(folder))
