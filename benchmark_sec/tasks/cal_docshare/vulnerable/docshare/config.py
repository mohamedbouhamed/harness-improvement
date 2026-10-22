"""Runtime configuration, read from environment variables set by the operators."""
import os
from dataclasses import dataclass, field


@dataclass(frozen=True)
class Settings:
    data_root: str
    max_upload_bytes: int = 5 * 1024 * 1024
    allowed_extensions: tuple = (".txt", ".md", ".csv", ".pdf")
    audit_log_name: str = "audit.log"
    hidden_prefixes: tuple = field(default=(".", "_"))

    @property
    def docs_root(self) -> str:
        return os.path.join(self.data_root, "docs")

    @property
    def upload_root(self) -> str:
        return os.path.join(self.data_root, "uploads")

    @property
    def audit_path(self) -> str:
        return os.path.join(self.data_root, self.audit_log_name)


def load_settings(env=None) -> Settings:
    env = os.environ if env is None else env
    root = env.get("DOCSHARE_ROOT", "/srv/docshare")
    limit = int(env.get("DOCSHARE_MAX_UPLOAD", 5 * 1024 * 1024))
    return Settings(data_root=root, max_upload_bytes=limit)
