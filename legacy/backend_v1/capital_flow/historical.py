"""Look-ahead-safe replay, outcome labelling and probability calibration."""
from __future__ import annotations

import math
from collections import defaultdict
from typing import Any, Iterable, Mapping, Sequence


def past_only_percentile(values: Sequence[float], value: float, percentile: float = 0.99) -> bool:
    if not values: return False
    ordered = sorted(float(x) for x in values)
    rank = max(0, min(len(ordered) - 1, math.ceil(percentile * len(ordered)) - 1))
    return float(value) >= ordered[rank]


def walk_forward_prefixes(rows: Sequence[Mapping[str, Any]], *, min_train: int = 1) -> list[dict[str, Any]]:
    """Return event-time prefixes; row i can only see rows before i."""
    output = []
    for index, row in enumerate(rows):
        history = list(rows[max(0, index - 30 * 24 * 60):index])
        output.append({"row": dict(row), "history_size": len(history), "history": history if len(history) < 5000 else history[-5000:], "eligible": len(history) >= min_train})
    return output


def event_outcomes(events: Sequence[Mapping[str, Any]], prices: Sequence[Mapping[str, Any]], horizons: Iterable[int] = (60, 300, 900, 1800, 3600, 14400, 86400)) -> list[dict[str, Any]]:
    price_rows = sorted(prices, key=lambda x: int(x.get("timestamp_ms", x.get("timestamp", 0))))
    result = []
    for event in events:
        ts = int(event.get("timestamp_ms", event.get("timestamp", 0)))
        direction = 1 if str(event.get("direction", "")).upper() in {"BUY", "LONG", "BULLISH"} else -1
        entry = event.get("price")
        if entry is None:
            before = [row for row in price_rows if int(row.get("timestamp_ms", row.get("timestamp", 0))) <= ts]
            entry = before[-1].get("price", before[-1].get("close")) if before else None
        item = dict(event); item["timestamp_ms"] = ts; item["entry_price"] = entry; item["outcomes"] = {}
        if entry is None: result.append(item); continue
        for horizon in horizons:
            future = [row for row in price_rows if ts < int(row.get("timestamp_ms", row.get("timestamp", 0))) <= ts + horizon * 1000]
            if not future: item["outcomes"][str(horizon)] = {"status": "UNAVAILABLE"}; continue
            closes = [float(row.get("price", row.get("close"))) for row in future if row.get("price", row.get("close")) is not None]
            highs = [float(row.get("high", row.get("price", row.get("close")))) for row in future]
            lows = [float(row.get("low", row.get("price", row.get("close")))) for row in future]
            end = closes[-1]
            item["outcomes"][str(horizon)] = {"status": "DERIVED", "forward_return": (end / float(entry) - 1) * direction, "max_up_move": (max(highs) / float(entry) - 1) * direction, "max_down_move": (min(lows) / float(entry) - 1) * direction, "mfe": max((max(highs) / float(entry) - 1) * direction, 0), "mae": min((min(lows) / float(entry) - 1) * direction, 0), "trend_continuation": ((end / float(entry) - 1) * direction) > 0, "reversal": ((end / float(entry) - 1) * direction) < 0}
        result.append(item)
    return result


def walk_forward_evaluate(rows: Sequence[Mapping[str, Any]], train_fraction: float = 0.6, validation_fraction: float = 0.2) -> dict[str, Any]:
    ordered = sorted(rows, key=lambda x: int(x.get("timestamp_ms", x.get("timestamp", 0))))
    n = len(ordered); train_end = int(n * train_fraction); validation_end = int(n * (train_fraction + validation_fraction))
    groups = {"TRAIN": ordered[:train_end], "VALIDATION": ordered[train_end:validation_end], "OUT_OF_SAMPLE": ordered[validation_end:]}
    summary = {}
    for name, group in groups.items():
        returns = [float(x.get("outcome", x.get("forward_return"))) for x in group if x.get("outcome", x.get("forward_return")) is not None]
        summary[name] = {"sample_size": len(group), "resolved_size": len(returns), "win_rate": sum(x > 0 for x in returns) / len(returns) if returns else None, "expected_return": sum(returns) / len(returns) if returns else None, "median_return": sorted(returns)[len(returns)//2] if returns else None}
    return {"status": "DERIVED" if rows else "UNAVAILABLE", "splits": summary, "methodology": "chronological train/validation/out-of-sample; no random shuffle"}


def calibrate_probabilities(rows: Sequence[Mapping[str, Any]], bins: int = 10) -> dict[str, Any]:
    groups: dict[int, list[Mapping[str, Any]]] = defaultdict(list)
    for row in rows:
        score = row.get("score", row.get("probability")); outcome = row.get("outcome", row.get("target_hit"))
        if score is None or outcome is None: continue
        score = max(0.0, min(1.0, float(score))); groups[min(bins - 1, int(score * bins))].append({"score": score, "outcome": bool(outcome)})
    values = []
    for index, group in sorted(groups.items()):
        forecast = sum(x["score"] for x in group) / len(group); observed = sum(x["outcome"] for x in group) / len(group)
        values.append({"bin": index, "forecast": forecast, "observed": observed, "sample_size": len(group), "calibrated_probability": observed})
    total = sum(x["sample_size"] for x in values)
    return {"status": "DERIVED" if total else "UNAVAILABLE", "bins": values, "sample_size": total, "brier_score": sum((float(row.get("score", row.get("probability"))) - bool(row.get("outcome", row.get("target_hit")))) ** 2 for row in rows if row.get("score", row.get("probability")) is not None and row.get("outcome", row.get("target_hit")) is not None) / total if total else None, "methodology": "score and calibrated_probability are separate; observed bin rates are not asserted without sample size"}
