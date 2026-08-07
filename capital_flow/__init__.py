"""NCE Capital Flow Intelligence Engine.

The package is deliberately independent from the legacy candle-based Money
Flow implementation.  Its core functions accept normalized raw events, so
the same code can be used by live collectors, historical replay, and tests.
"""

from .engine import (
    CapitalFlowEngine,
    MetricStatus,
    aggregate_trades,
    classify_trade_size,
    normalize_agg_trade,
)

__all__ = [
    "CapitalFlowEngine",
    "MetricStatus",
    "aggregate_trades",
    "classify_trade_size",
    "normalize_agg_trade",
]
