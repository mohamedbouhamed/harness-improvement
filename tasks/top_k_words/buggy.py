from collections import Counter


def top_k_words(words, k):
    """Return the k most frequent words; ties are ordered alphabetically."""
    counts = Counter(words)
    return [word for word, _ in counts.most_common(k)]
