"""Thumbnail generation with an external converter."""
import os
import re
import subprocess

SIZE = re.compile(r"^(\d{2,4})x(\d{2,4})$")


class ThumbError(Exception):
    pass


def thumbnail(settings, name: str, size: str) -> str:
    if not SIZE.match(size or ""):
        raise ThumbError("size must look like 320x240")
    base = os.path.basename(name)
    src = os.path.join(settings.library_dir, base)
    dst = os.path.join(settings.thumbs_dir, f"{size}-{base}")
    args = [settings.converter, src, "-resize", size, dst]
    proc = subprocess.run(args, capture_output=True, text=True, timeout=30)
    if proc.returncode != 0:
        raise ThumbError("conversion failed")
    return dst
