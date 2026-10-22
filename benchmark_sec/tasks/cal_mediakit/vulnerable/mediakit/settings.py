"""Operator settings."""
from dataclasses import dataclass


@dataclass(frozen=True)
class MediaSettings:
    library_dir: str = "/srv/media/library"
    thumbs_dir: str = "/srv/media/thumbs"
    converter: str = "convert"
    probe_timeout_s: int = 5
