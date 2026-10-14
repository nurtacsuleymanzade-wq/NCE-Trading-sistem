"""Hierarchical BTCUSDT probability forecasts and observer metrics.

The contract intentionally keeps three concepts separate:

* ``evidenceScore`` is feature agreement, never a probability;
* ``distribution`` is a model estimate, and is only labelled CALIBRATED when
  an empirical horizon-specific calibration row has enough samples;
* ``dataQuality`` describes input coverage/freshness, not expected accuracy.

One-second microstructure is a trigger, one-minute flow is a transition state,
and five-minute structure is context.  Each forecast horizon has its own gate
weights and frozen zone boundaries.
"""
from __future__ import annotations

import hashlib
import json
import math
import statistics
import time
from typing import Any, Mapping, Sequence


FORECAST_HORIZONS = (5, 10, 30)
STATE_WINDOWS_SECONDS = (1, 60, 300)
HORIZON_WEIGHTS = {
    5: {"1s": .50, "1m": .35, "5m": .15},
    10: {"1s": .30, "1m": .42, "5m": .28},
    30: {"1s": .10, "1m": .35, "5m": .55},
}


def _number(value: Any, default: float | None = None) -> float | None:
    try:
        value = float(value)
        return value if math.isfinite(value) else default
    except (TypeError, ValueError):
        return default


def _clip(value: float, low: float = 0.0, high: float = 1.0) -> float:
    return max(low, min(high, value))


def _get(row: Any, key: str, default: Any = None) -> Any:
    return row.get(key, default) if isinstance(row, Mapping) else getattr(row, key, default)


def _timestamp(row: Any) -> int:
    return int(_get(row, "timestamp_ms", _get(row, "timestamp", _get(row, "T", 0))) or 0)


def _price(row: Any) -> float | None:
    return _number(_get(row, "price", _get(row, "p")))


def _notional(row: Any) -> float:
    direct = _number(_get(row, "notional_usd", _get(row, "notional")))
    if direct is not None:
        return max(0.0, direct)
    return max(0.0, (_price(row) or 0.0) * (_number(_get(row, "quantity_btc", _get(row, "q")), 0.0) or 0.0))


def _side(row: Any) -> str:
    direct = str(_get(row, "aggressor_side", "") or "").upper()
    if direct in {"BUY", "SELL"}:
        return direct
    maker = _get(row, "buyer_is_maker", _get(row, "m"))
    return "SELL" if bool(maker) else "BUY"


def _book_imbalance(book: Mapping[str, Any] | None, levels: int = 20) -> tuple[float | None, float | None]:
    book = book or {}
    bids, asks = list(book.get("bids", []) or [])[:levels], list(book.get("asks", []) or [])[:levels]
    bid = sum((_number(x[0], 0.0) or 0.0) * (_number(x[1], 0.0) or 0.0) for x in bids if len(x) >= 2)
    ask = sum((_number(x[0], 0.0) or 0.0) * (_number(x[1], 0.0) or 0.0) for x in asks if len(x) >= 2)
    total = bid + ask
    if not total:
        return None, None
    imbalance = (bid - ask) / total
    microprice = None
    if bids and asks:
        bp, bq = _number(bids[0][0]), _number(bids[0][1])
        ap, aq = _number(asks[0][0]), _number(asks[0][1])
        if None not in (bp, bq, ap, aq) and bq + aq > 0:
            microprice = (ap * bq + bp * aq) / (bq + aq)
    return imbalance, microprice


def _oi_change(oi_history: Sequence[Mapping[str, Any]], window_seconds: int, now_ms: int) -> float | None:
    values = []
    for row in oi_history or []:
        value = _number(row.get("open_interest", row.get("sumOpenInterest", row.get("value"))))
        stamp = int(row.get("timestamp_ms", row.get("timestamp", 0)) or 0)
        if value is not None and stamp and stamp >= now_ms - window_seconds * 1000:
            values.append((stamp, value))
    values.sort()
    if len(values) < 2 or values[0][1] == 0:
        return None
    return values[-1][1] / values[0][1] - 1


def build_timeframe_state(
    trades: Sequence[Any],
    *,
    window_seconds: int,
    now_ms: int,
    current_price: float,
    book: Mapping[str, Any] | None = None,
    oi_history: Sequence[Mapping[str, Any]] = (),
    funding_rate: float | None = None,
) -> dict[str, Any]:
    """Create a past-only state for one independent observation window."""
    rows = [x for x in trades if _timestamp(x) >= now_ms - window_seconds * 1000 and _price(x)]
    rows.sort(key=_timestamp)
    buys = sum(_notional(x) for x in rows if _side(x) == "BUY")
    sells = sum(_notional(x) for x in rows if _side(x) == "SELL")
    total = buys + sells
    delta = (buys - sells) / total if total else None
    start, end = (_price(rows[0]), _price(rows[-1])) if rows else (None, None)
    return_bps = ((end / start - 1) * 10_000) if start and end else None
    prices = [_price(x) for x in rows]
    log_returns = [math.log(prices[i] / prices[i - 1]) for i in range(1, len(prices)) if prices[i] and prices[i - 1] and prices[i] != prices[i - 1]]
    volatility_bps = statistics.pstdev(log_returns) * 10_000 if len(log_returns) >= 2 else None
    imbalance, microprice = _book_imbalance(book)
    microprice_bps = ((microprice / current_price - 1) * 10_000) if microprice and current_price else None
    oi_delta = _oi_change(oi_history, max(window_seconds, 300), now_ms)

    features = {
        "aggressionDelta": delta,
        "priceReturnBps": return_bps,
        "realizedVolatilityBps": volatility_bps,
        "tradeCount": len(rows),
        "quoteVolume": total,
        "tradeIntensity": len(rows) / max(1, window_seconds),
        "bookImbalance": imbalance,
        "micropriceBps": microprice_bps,
        "oiChangePct": oi_delta * 100 if oi_delta is not None else None,
        "fundingRatePct": funding_rate * 100 if funding_rate is not None else None,
    }
    parts = []
    if delta is not None:
        parts.append(.38 * math.tanh(delta * 3.0))
    if return_bps is not None:
        parts.append(.27 * math.tanh(return_bps / max(2.0, 4.0 * math.sqrt(window_seconds))))
    if imbalance is not None:
        parts.append(.18 * math.tanh(imbalance * 3.0))
    if microprice_bps is not None:
        parts.append(.07 * math.tanh(microprice_bps / 2.0))
    if oi_delta is not None:
        price_sign = 1 if (return_bps or 0) >= 0 else -1
        parts.append(.07 * math.tanh(oi_delta * 100) * price_sign)
    if funding_rate is not None:
        # Funding is context/crowding and receives a small contrarian weight.
        parts.append(-.03 * math.tanh(funding_rate / .0005))
    signal = _clip(sum(parts), -1, 1)
    available = sum(value is not None for key, value in features.items() if key not in {"tradeCount", "quoteVolume", "tradeIntensity"})
    quality = round(100 * _clip((available / 7) * min(1.0, len(rows) / max(3, window_seconds * .4))), 1)
    return {
        "window": f"{window_seconds}s" if window_seconds < 60 else f"{window_seconds // 60}m",
        "role": "MICRO TRIGGER" if window_seconds == 1 else "FLOW SETUP" if window_seconds == 60 else "REGIME CONTEXT",
        "signal": round(signal, 5),
        "direction": "LONG" if signal >= 0 else "SHORT",
        "evidenceScore": round(abs(signal) * 100, 1),
        "dataQuality": quality,
        "features": features,
        "source": "Binance Futures aggTrade + depth + OI + funding",
        "status": "OBSERVED+DERIVED",
    }


def _calibrate(raw: dict[str, float], horizon: int, calibration: Mapping[str, Any] | None) -> tuple[dict[str, float], str, int]:
    rows = ((calibration or {}).get(str(horizon)) or []) if isinstance(calibration, Mapping) else []
    scalar = raw["UP"] - raw["DOWN"]
    matches = [row for row in rows if _number(row.get("signal_low"), -1) <= scalar < _number(row.get("signal_high"), 1) and int(row.get("sample_size", 0)) >= 100]
    if not matches:
        return raw, "MODEL_ESTIMATE", 0
    row = max(matches, key=lambda x: int(x.get("sample_size", 0)))
    values = {key: _number(row.get(key.lower() + "_rate")) for key in ("UP", "RANGE", "DOWN")}
    if any(value is None for value in values.values()) or sum(values.values()) <= 0:
        return raw, "MODEL_ESTIMATE", 0
    total = sum(values.values())
    return {key: round(values[key] / total, 4) for key in values}, "CALIBRATED", int(row["sample_size"])


def build_horizon_forecast(
    trades: Sequence[Any],
    *,
    current_price: float,
    book: Mapping[str, Any] | None = None,
    oi_history: Sequence[Mapping[str, Any]] = (),
    funding_rate: float | None = None,
    calibration: Mapping[str, Any] | None = None,
    observer: Mapping[str, Any] | None = None,
    now_ms: int | None = None,
) -> dict[str, Any]:
    """Return separate 5/10/30m distributions from 1s/1m/5m states."""
    now_ms = int(now_ms or time.time() * 1000)
    states = {
        "1s": build_timeframe_state(trades, window_seconds=1, now_ms=now_ms, current_price=current_price, book=book, oi_history=oi_history, funding_rate=funding_rate),
        "1m": build_timeframe_state(trades, window_seconds=60, now_ms=now_ms, current_price=current_price, book=book, oi_history=oi_history, funding_rate=funding_rate),
        "5m": build_timeframe_state(trades, window_seconds=300, now_ms=now_ms, current_price=current_price, book=book, oi_history=oi_history, funding_rate=funding_rate),
    }
    vol_values = [state["features"].get("realizedVolatilityBps") for state in states.values()]
    vol_values = [x for x in vol_values if x is not None and x > 0]
    base_vol_bps = statistics.median(vol_values) if vol_values else 2.5
    horizons = []
    for horizon in FORECAST_HORIZONS:
        weights = HORIZON_WEIGHTS[horizon]
        signal = sum(states[key]["signal"] * weight for key, weight in weights.items())
        quality = sum(states[key]["dataQuality"] * weight for key, weight in weights.items())
        strength = abs(math.tanh(signal * 1.8))
        range_probability = _clip(.58 - .34 * strength, .20, .62)
        directional = 1 - range_probability
        up = directional * (.5 + .5 * math.tanh(signal * 1.7))
        raw = {"UP": round(up, 4), "RANGE": round(range_probability, 4), "DOWN": round(directional - up, 4)}
        distribution, probability_status, samples = _calibrate(raw, horizon, calibration)
        direction = "LONG" if distribution["UP"] >= distribution["DOWN"] else "SHORT"
        # Empirical sigma is converted into a horizon range; the floor avoids
        # displaying one-tick precision when recent trades are quiet.
        sigma = current_price * max(2.0, base_vol_bps) / 10_000 * math.sqrt(max(1, horizon * 60))
        shift = math.tanh(signal * 1.5) * sigma * .65
        center = current_price + shift
        half_width = max(current_price * .00015, sigma * .55)
        zone_low, zone_high = center - half_width, center + half_width
        neutral_half_width = max(current_price * .00008, sigma * .22)
        evidence_score = round(abs(signal) * 100, 1)
        prediction_id = hashlib.sha256(f"BTCUSDT:{now_ms}:{horizon}:{current_price:.2f}".encode()).hexdigest()[:20]
        horizons.append({
            "predictionId": prediction_id,
            "horizonMinutes": horizon,
            "direction": direction,
            "distribution": {key: round(value, 4) for key, value in distribution.items()},
            "probabilityStatus": probability_status,
            "calibrationSample": samples,
            "evidenceScore": evidence_score,
            "dataQuality": round(quality, 1),
            "zone": {"low": round(zone_low, 2), "center": round(center, 2), "high": round(zone_high, 2), "frozenAtPrediction": True},
            "neutralZone": {"low": round(current_price - neutral_half_width, 2), "high": round(current_price + neutral_half_width, 2), "frozenAtPrediction": True},
            "expectedMoveBps": round(abs(shift) / current_price * 10_000, 2),
            "weights": weights,
            "resolvedAtMs": now_ms + horizon * 60_000,
        })
    payload = {
        "status": "PASS",
        "schemaVersion": "probability-horizons-v2",
        "symbol": "BTCUSDT",
        "timestamp": now_ms,
        "currentPrice": round(current_price, 2),
        "timeframeStates": states,
        "horizons": horizons,
        "observer": dict(observer or {"status": "COLLECTING", "resolvedSamples": 0, "minimumCalibrationSamples": 100}),
        "rules": {
            "scoreIsProbability": False,
            "dataQualityIsProbability": False,
            "separateModelPerHorizon": True,
            "futureDataUsedForFeatures": False,
            "oneSecondRole": "trigger, not long-horizon truth",
            "training": "purged chronological walk-forward; overlapping labels embargoed",
        },
    }
    payload["featureHash"] = hashlib.sha256(json.dumps(states, sort_keys=True, separators=(",", ":")).encode()).hexdigest()[:16]
    return payload


def classify_realized_zone(prediction: Mapping[str, Any], realized_price: float) -> str:
    origin = _number(prediction.get("originPrice"), _number(prediction.get("currentPrice"), 0.0)) or 0.0
    zone = prediction.get("neutralZone") or {}
    low = _number(zone.get("low"), origin * .9995) or origin * .9995
    high = _number(zone.get("high"), origin * 1.0005) or origin * 1.0005
    return "UP" if realized_price > high else "DOWN" if realized_price < low else "RANGE"


def observer_metrics(rows: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    """Measure probabilistic quality; accuracy alone is deliberately insufficient."""
    resolved = [row for row in rows if row.get("actualClass") in {"UP", "RANGE", "DOWN"}]
    if not resolved:
        return {"status": "COLLECTING", "resolvedSamples": 0, "minimumCalibrationSamples": 100, "byHorizon": {}}
    result: dict[str, Any] = {}
    for horizon in FORECAST_HORIZONS:
        raw_group = [row for row in resolved if int(row.get("horizonMinutes", 0)) == horizon]
        if not raw_group:
            continue
        # Overlapping labels make second-by-second observations look far more
        # certain than they are. Metrics use an embargo equal to the horizon.
        if all(row.get("predictionTimestamp") is not None for row in raw_group):
            group, last_stamp = [], -1
            for row in sorted(raw_group, key=lambda x: int(x["predictionTimestamp"])):
                stamp = int(row["predictionTimestamp"])
                if stamp >= last_stamp + horizon * 60_000:
                    group.append(row)
                    last_stamp = stamp
        else:
            group = raw_group
        brier, logloss, correct = [], [], 0
        calibration_bins: dict[int, list[tuple[float, int]]] = {}
        for row in group:
            probs = row.get("distribution") or {}
            actual = str(row["actualClass"])
            brier.append(sum((float(probs.get(label, 0)) - int(label == actual)) ** 2 for label in ("UP", "RANGE", "DOWN")) / 3)
            p_actual = _clip(float(probs.get(actual, 0)), 1e-9, 1)
            logloss.append(-math.log(p_actual))
            chosen = max(("UP", "RANGE", "DOWN"), key=lambda label: float(probs.get(label, 0)))
            correct += int(chosen == actual)
            p_chosen = float(probs.get(chosen, 0))
            calibration_bins.setdefault(min(9, int(p_chosen * 10)), []).append((p_chosen, int(chosen == actual)))
        ece = sum(len(items) / len(group) * abs(sum(p for p, _ in items) / len(items) - sum(y for _, y in items) / len(items)) for items in calibration_bins.values())
        result[str(horizon)] = {
            "rawResolvedSamples": len(raw_group),
            "sampleSize": len(group),
            "accuracy": round(correct / len(group), 4),
            "brierScore": round(sum(brier) / len(brier), 5),
            "logLoss": round(sum(logloss) / len(logloss), 5),
            "expectedCalibrationError": round(ece, 5),
            "status": "CALIBRATION_READY" if len(group) >= 100 else "COLLECTING",
        }
    return {"status": "ACTIVE", "resolvedSamples": len(resolved), "minimumCalibrationSamples": 100, "metricSampling": "PURGED; embargo equals forecast horizon", "byHorizon": result}
