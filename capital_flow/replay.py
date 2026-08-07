from __future__ import annotations

import json
from pathlib import Path
from typing import Iterable

from .engine import AggTrade, CapitalFlowEngine, normalize_agg_trade, trader_size_thresholds


def load_jsonl(path: str | Path, market: str, symbol: str = "BTCUSDT") -> list[AggTrade]:
    trades: list[AggTrade] = []
    for line in Path(path).read_text().splitlines():
        if not line.strip():
            continue
        trades.append(normalize_agg_trade(json.loads(line), market, symbol))
    return sorted(trades, key=lambda x: (x.timestamp, x.aggregate_trade_id))


def replay_snapshot(spot: Iterable[AggTrade], futures: Iterable[AggTrade], timeframe_seconds: int = 300) -> dict:
    """Build a snapshot using only data available in the supplied replay.

    Callers can feed chronologically increasing prefixes to obtain a true
    walk-forward series. Thresholds are computed from that prefix only; no
    future data is consulted.
    """
    engine = CapitalFlowEngine()
    engine.spot_trades.extend(sorted(spot, key=lambda x: x.timestamp))
    engine.futures_trades.extend(sorted(futures, key=lambda x: x.timestamp))
    engine.spot_thresholds = trader_size_thresholds((x.notional_usd for x in engine.spot_trades), "spot")
    engine.futures_thresholds = trader_size_thresholds((x.notional_usd for x in engine.futures_trades), "futures")
    return engine.snapshot(timeframe_seconds)
