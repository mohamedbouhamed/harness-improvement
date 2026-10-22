"""Tokenizing helpers."""
import re

_WORD = re.compile(r"[A-Za-z']+")


def words(text):
    """Return the words of `text` (runs of ASCII letters and apostrophes), lower-cased, in order."""
    return _WORD.findall(text)


def chunks(items, size):
    """Split `items` into consecutive lists of `size` items; the last list may be shorter."""
    if size < 1:
        raise ValueError("size must be >= 1")
    return [items[i:i + size] for i in range(0, len(items) - size, size)]
