"""Word statistics built on the tokenizer."""
from collections import Counter

from .tokenize import chunks, words


def window_counts(text, size):
    """Word counts for each consecutive window of `size` words (the last window may be shorter)."""
    return [dict(Counter(window)) for window in chunks(words(text), size)]
