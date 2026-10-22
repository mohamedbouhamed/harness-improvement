"""Building a configuration from defaults and overrides."""
from .merge import deep_merge

DEFAULTS = {"server": {"host": "localhost", "port": 8080}, "features": []}


def load(*overrides):
    """Apply `overrides` in order on top of DEFAULTS. DEFAULTS is never modified by load or its result."""
    config = DEFAULTS
    for override in overrides:
        config = deep_merge(config, override)
    return config
