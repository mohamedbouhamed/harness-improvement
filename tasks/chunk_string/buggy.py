def chunk_string(text, size):
    """Split text into consecutive chunks of `size` characters."""
    if size < 1:
        raise ValueError("size must be >= 1")
    return [text[i:i + size] for i in range(0, len(text) - size, size)]
