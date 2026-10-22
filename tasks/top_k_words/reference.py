from collections import Counter


def top_k_words(words, k):
    """Return the k most frequent words; ties are ordered alphabetically."""
    if k <= 0:
        return []
    counts = Counter(words)
    ranked = sorted(counts.items(), key=lambda pair: (-pair[1], pair[0]))
    return [word for word, _ in ranked[:k]]
