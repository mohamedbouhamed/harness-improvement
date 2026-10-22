def dedupe_events(events):
    """Keep the latest event per id and return the survivors in timestamp order."""
    best = {}  # id -> (input index, event)
    for index, event in enumerate(events):
        current = best.get(event["id"])
        if current is None or event["ts"] > current[1]["ts"]:
            best[event["id"]] = (index, event)
    kept = sorted(best.values(), key=lambda pair: (pair[1]["ts"], pair[0]))
    return [event for _, event in kept]
