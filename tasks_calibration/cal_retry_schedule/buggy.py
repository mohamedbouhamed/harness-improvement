RETRYABLE = {"timeout", "unavailable", "rate_limited"}


def plan_retries(outcomes, max_attempts, base_delay, max_delay):
    """Simulate a capped exponential-backoff retry loop over scripted outcomes."""
    delays = []
    attempt = 0
    while attempt <= max_attempts:
        result = outcomes[attempt]
        attempt += 1
        if result == "ok":
            return attempt, delays, "ok"
        if result not in RETRYABLE and attempt > 1:
            return attempt, delays, result
        delays.append(min(max_delay, base_delay * 2 ** attempt))
    return attempt, delays, "exhausted"
