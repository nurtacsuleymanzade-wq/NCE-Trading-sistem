"""Single-source configuration for Capital Flow semantics."""

from __future__ import annotations

import os


def _csv_env(name: str, default: tuple[str, ...]) -> tuple[str, ...]:
    raw = os.environ.get(name)
    if not raw:
        return default
    values = tuple(x.strip().upper() for x in raw.split(",") if x.strip())
    return values or default


# Retail is deliberately defined once in the backend. The frontend consumes
# this value from the interpretation contract and never redefines it.
RETAIL_BUCKETS: tuple[str, ...] = _csv_env("NCE_CAPITAL_FLOW_RETAIL_BUCKETS", ("SMALL", "MEDIUM"))
SIZE_BUCKETS: tuple[str, ...] = ("SMALL", "MEDIUM", "LARGE", "WHALE_SIZE", "MEGA_WHALE_SIZE")

CVD_RESET_SEMANTICS = "query_window_start"
