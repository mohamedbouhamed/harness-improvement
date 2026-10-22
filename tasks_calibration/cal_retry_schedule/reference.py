RETRYABLE = {"timeout", "unavailable", "rate_limited"}


def plan_retries(outcomes, max_attempts, base_delay, max_delay):
    """Simulate a capped exponential-backoff retry loop over scripted outcomes."""
    delays = []
    for attempt in range(1, max_attempts + 1):
        result = outcomes[attempt - 1]
        if result == "ok":
            return attempt, delays, "ok"
        if result not in RETRYABLE:
            return attempt, delays, result
        if attempt == max_attempts:
            return attempt, delays, "exhausted"
        delays.append(min(max_delay, base_delay * 2 ** (attempt - 1)))
