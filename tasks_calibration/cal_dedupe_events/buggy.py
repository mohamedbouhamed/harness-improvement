def dedupe_events(events):
    """Keep the latest event per id and return the survivors in timestamp order."""
    latest = {}
    for event in events:
        current = latest.get(event["id"])
        if current is None or event["ts"] >= current["ts"]:
            latest[event["id"]] = event
    return sorted(latest.values(), key=lambda e: (e["ts"], e["id"]))
