"""NCE Probability Map engines.

The module deliberately contains three separate engines:

* liquidity: observed order-book state and lifecycle;
* liquidation: estimated OI-cohort inventory;
* probability: candidate features, attraction score and calibrated outcomes.

No score is returned as a probability.  A calibrated probability is ``None``
until a historical calibration mapping with sufficient observations is
provided.
"""
from __future__ import annotations

import math
import statistics
import time
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Any, Iterable, Mapping, Sequence


HORIZONS_MINUTES = (15, 30, 60, 240)
LEVERAGE_BUCKETS = (2, 3, 5, 10, 20, 25, 50, 75, 100, 125)


def _finite(value: Any, default: float | None = None) -> float | None:
    try:
        number = float(value)
        return number if math.isfinite(number) else default
    except (TypeError, ValueError):
        return default


def clamp(value: float | None, low: float = 0.0, high: float = 1.0) -> float | None:
    if value is None:
        return None
    return max(low, min(high, float(value)))


def round_probability(value: float | None) -> float | None:
    """Return human-safe probability precision, never false decimal precision."""
    if value is None:
        return None
    return round(clamp(value) or 0.0, 3)


def adaptive_price_bin_size(price: float, atr: float | None = None, timeframe: str = "5m", zoom: str = "normal") -> float:
    """Choose a volatility-aware display bin, never smaller than BTC tick size."""
    tick = 0.10
    tf_factor = {"1s": 0.15, "5s": 0.20, "15s": 0.25, "30s": 0.30, "1m": 0.35, "5m": 0.60, "15m": 0.90, "30m": 1.20, "1h": 1.60}.get(timeframe, 0.75)
    volatility_bin = (atr or price * 0.001) * tf_factor / 8.0
    if zoom == "high":
        volatility_bin *= 0.5
    elif zoom == "low":
        volatility_bin *= 2.0
    # Keep a stable, readable decimal grid while preserving exact price levels.
    raw = max(tick, volatility_bin)
    exponent = math.floor(math.log10(raw)) if raw else -1
    step = 10 ** exponent
    normalized = raw / step
    multiplier = 1 if normalized <= 1 else 2 if normalized <= 2 else 5 if normalized <= 5 else 10
    return round(max(tick, multiplier * step), 8)


def _level_map(book: Mapping[str, Any], side: str) -> dict[float, float]:
    levels: dict[float, float] = {}
    for row in book.get(side, []) or []:
        if not isinstance(row, (list, tuple)) or len(row) < 2:
            continue
        price, qty = _finite(row[0]), _finite(row[1])
        if price is not None and qty is not None and qty > 0:
            levels[price] = qty
    return levels


def liquidity_lifecycle(
    previous: Mapping[str, Any] | None,
    current: Mapping[str, Any],
    *,
    executed: Mapping[str, float] | None = None,
    timestamp_ms: int | None = None,
    update_id: int | None = None,
) -> list[dict[str, Any]]:
    """Classify level changes without pretending a diff proves execution.

    ``EXECUTED`` is only emitted for a caller-supplied aggressive execution at
    that exact price/side. A quantity decrease in a depth diff alone is
    ``DEPLETED``; a level removal alone is ``CANCELLED``.
    """
    events: list[dict[str, Any]] = []
    executed = executed or {}
    stamp = timestamp_ms or int(time.time() * 1000)
    for side in ("bids", "asks"):
        before, after = _level_map(previous or {}, side), _level_map(current, side)
        for price in sorted(set(before) | set(after)):
            old_qty, new_qty = before.get(price, 0.0), after.get(price, 0.0)
            if old_qty == new_qty and old_qty > 0:
                continue
            if old_qty == 0 and new_qty > 0:
                action = "ADDED"
                amount = new_qty
            elif old_qty > 0 and new_qty == 0:
                action = "CANCELLED"
                amount = old_qty
            elif new_qty > old_qty:
                action = "REPLENISHED"
                amount = new_qty - old_qty
            else:
                action = "DEPLETED"
                amount = old_qty - new_qty
            exec_qty = _finite(executed.get(f"{side}:{price}"), 0.0) or 0.0
            if exec_qty > 0:
                action = "EXECUTED"
                amount = min(old_qty or amount, exec_qty)
            events.append({
                "timestamp_ms": stamp,
                "update_id": update_id,
                "price": price,
                "side": "BID" if side == "bids" else "ASK",
                "quantity": max(0.0, amount),
                "notional": max(0.0, amount) * price,
                "action": action,
                "previous_quantity": old_qty,
                "remaining_quantity": new_qty,
                "status": "REAL",
                "source": "Binance REST snapshot + diff depth",
            })
    return events


def liquidity_zone_metrics(events: Sequence[Mapping[str, Any]], current_price: float, *, now_ms: int | None = None) -> list[dict[str, Any]]:
    """Aggregate lifecycle evidence by side/price for the heatmap tooltip."""
    now = now_ms or int(time.time() * 1000)
    grouped: dict[tuple[str, float], dict[str, Any]] = {}
    for row in events:
        price = _finite(row.get("price"))
        if price is None or price <= 0:
            continue
        key = (str(row.get("side", "UNKNOWN")), price)
        item = grouped.setdefault(key, {"price": price, "side": key[0], "displayed_liquidity": 0.0, "executed_liquidity": 0.0, "cancelled_liquidity": 0.0, "replenished_liquidity": 0.0, "depleted_liquidity": 0.0, "first_timestamp_ms": None, "last_timestamp_ms": None, "remaining_liquidity": _finite(row.get("remaining_quantity"), 0.0) or 0.0})
        notional = _finite(row.get("notional"), 0.0) or 0.0
        action = str(row.get("action", "")).upper()
        if action in {"ADDED", "RESTING", "REPLENISHED"}:
            item["displayed_liquidity"] += notional
        if action == "EXECUTED":
            item["executed_liquidity"] += notional
        if action == "CANCELLED":
            item["cancelled_liquidity"] += notional
        if action == "REPLENISHED":
            item["replenished_liquidity"] += notional
        if action == "DEPLETED":
            item["depleted_liquidity"] += notional
        stamp = int(row.get("timestamp_ms", now))
        item["first_timestamp_ms"] = stamp if item["first_timestamp_ms"] is None else min(item["first_timestamp_ms"], stamp)
        item["last_timestamp_ms"] = stamp if item["last_timestamp_ms"] is None else max(item["last_timestamp_ms"], stamp)
        remaining = _finite(row.get("remaining_notional"))
        if remaining is None:
            quantity = _finite(row.get("remaining_quantity"))
            remaining = quantity * price if quantity is not None else None
        if remaining is not None:
            item["remaining_liquidity"] = remaining
    result = []
    for item in grouped.values():
        displayed = item["displayed_liquidity"]
        executed = item["executed_liquidity"]
        cancelled = item["cancelled_liquidity"]
        replenished = item["replenished_liquidity"]
        denominator = displayed + executed + cancelled
        persistence = max(0.0, (now - (item["first_timestamp_ms"] or now)) / 1000)
        execution_ratio = executed / denominator if denominator else 0.0
        cancel_ratio = cancelled / max(cancelled + executed + item["remaining_liquidity"], 1.0)
        replenishment_ratio = replenished / max(displayed, 1.0)
        spoof_score = clamp(cancel_ratio * (1 - execution_ratio), 0, 1)
        absorption_score = clamp(execution_ratio * (0.5 + 0.5 * replenishment_ratio), 0, 1)
        item.update({
            "distance_pct": (item["price"] / current_price - 1) * 100 if current_price else None,
            "distance_bps": (item["price"] / current_price - 1) * 10000 if current_price else None,
            "persistence_seconds": round(persistence, 2),
            "execution_ratio": round(execution_ratio, 4),
            "cancel_ratio": round(cancel_ratio, 4),
            "replenishment_ratio": round(replenishment_ratio, 4),
            "depletion_ratio": round(item["depleted_liquidity"] / max(displayed, 1.0), 4),
            "wall_strength": round(clamp(displayed / max(current_price * 1000, 1), 0, 1) or 0, 4),
            "wall_persistence": round(clamp(persistence / 300, 0, 1) or 0, 4),
            "spoof_score": round(spoof_score or 0, 4),
            "iceberg_score": round(clamp(replenishment_ratio * execution_ratio, 0, 1) or 0, 4),
            "absorption_score": round(absorption_score or 0, 4),
            "classification": "SPOOF/PULLED" if (spoof_score or 0) >= 0.65 else "ABSORPTION/REAL_PASSIVE" if (absorption_score or 0) >= 0.20 else "RESTING/UNCONFIRMED",
            "confidence": round(100 * clamp(min(1, len(events) / 20)) if events else 0, 1),
            "status": "DERIVED",
        })
        result.append(item)
    return sorted(result, key=lambda x: abs(x["price"] - current_price))


def build_liquidity_heatmap(
    snapshots: Sequence[Mapping[str, Any]],
    events: Sequence[Mapping[str, Any]],
    current_price: float,
    *,
    timeframe: str = "5m",
    atr: float | None = None,
    max_rows: int = 240,
) -> dict[str, Any]:
    """Build a compact time × price representation from real depth rows."""
    if not snapshots and not events:
        return {"status": "UNAVAILABLE", "dataStatus": "UNAVAILABLE", "market": "SPOT", "rows": [], "reason": "no order-book snapshot/diff rows"}
    bin_size = adaptive_price_bin_size(current_price, atr, timeframe)
    buckets: dict[tuple[int, int], dict[str, Any]] = {}
    for row in events:
        price = _finite(row.get("price"))
        stamp = int(row.get("timestamp_ms", 0))
        if price is None or not stamp:
            continue
        bucket = int(round(price / bin_size))
        time_bucket = stamp // (max(1000, {"1s": 1000, "5s": 5000, "15s": 15000, "30s": 30000, "1m": 60000, "5m": 300000}.get(timeframe, 300000)))
        key = (time_bucket, bucket)
        cell = buckets.setdefault(key, {"timeBucket": time_bucket, "price": bucket * bin_size, "displayedNotional": 0.0, "executedNotional": 0.0, "cancelledNotional": 0.0, "replenishedNotional": 0.0, "actions": []})
        action = str(row.get("action", "")).upper()
        notional = _finite(row.get("notional"), 0.0) or 0.0
        cell["actions"].append(action)
        if action in {"ADDED", "RESTING", "REPLENISHED"}: cell["displayedNotional"] += notional
        if action == "EXECUTED": cell["executedNotional"] += notional
        if action == "CANCELLED": cell["cancelledNotional"] += notional
        if action == "REPLENISHED": cell["replenishedNotional"] += notional
    rows = list(buckets.values())[-max_rows:]
    for cell in rows:
        cell["intensity"] = round(math.log1p(cell["displayedNotional"]) / 20, 4)
        cell["actions"] = sorted(set(cell["actions"]))
        cell["status"] = "REAL"
    return {"status": "PASS", "dataStatus": "REAL", "market": "SPOT", "binSize": bin_size, "rows": rows, "levels": liquidity_zone_metrics(events, current_price), "snapshotCount": len(snapshots), "eventCount": len(events), "legend": {"intensity": "resting/displayed notional", "EXECUTED": "aggressive execution observed", "CANCELLED": "depth removed without execution proof", "REPLENISHED": "display increased at an existing level"}}


def leverage_prior(oi_delta: float | None = None, volatility: float | None = None) -> dict[str, Any]:
    """Data-driven prior, explicitly not account-level leverage information."""
    base = {2: .05, 3: .07, 5: .12, 10: .20, 20: .22, 25: .14, 50: .10, 75: .05, 100: .04, 125: .01}
    if volatility is not None and volatility > 0.012:
        base[2] += .04; base[5] += .02; base[50] -= .03; base[75] -= .02; base[100] -= .01
    if oi_delta is not None and oi_delta < 0:
        base[2] += .02; base[10] += .02; base[50] -= .02
    total = sum(max(0.0, x) for x in base.values())
    distribution = {str(k): round(max(0.0, v) / total, 4) for k, v in base.items()}
    return {"state": "PRIOR_ESTIMATE", "distribution": distribution, "confidence": 22, "methodology": "data-driven prior adjusted only by observed OI/volatility; not trader account data", "status": "ESTIMATED"}


def liquidation_price(entry_price: float, leverage: float, side: str, maintenance_margin_rate: float = 0.004, fee_buffer: float = 0.001) -> float:
    """Approximate Binance-style isolated liquidation price.

    This is a conservative linear-contract projection. Contract-specific
    maintenance tiers must be supplied by the caller for production exactness.
    """
    entry, lev = float(entry_price), max(1.0, float(leverage))
    margin = 1.0 / lev
    buffer = max(0.0, maintenance_margin_rate) + max(0.0, fee_buffer)
    if str(side).upper().startswith("LONG"):
        return entry * (1 - margin + buffer)
    return entry * (1 + margin - buffer)


@dataclass
class OICohort:
    cohort_id: str
    created_at_ms: int
    entry_price: float
    new_oi: float
    estimated_long_share: float
    estimated_short_share: float
    leverage_distribution: dict[str, float]
    remaining_oi: float = field(init=False)
    status: str = "ESTIMATED"

    def __post_init__(self) -> None:
        self.remaining_oi = max(0.0, self.new_oi)

    def decay(self, current_oi: float | None = None, age_hours: float = 0.0) -> float:
        age_factor = math.exp(-max(0.0, age_hours) / 72.0)
        inventory_factor = min(1.0, max(0.0, current_oi / self.new_oi)) if current_oi is not None and self.new_oi > 0 else 1.0
        self.remaining_oi = self.new_oi * age_factor * inventory_factor
        return self.remaining_oi


def build_oi_cohorts(oi_history: Sequence[Mapping[str, Any]], current_price: float, *, now_ms: int | None = None, volatility: float | None = None) -> list[OICohort]:
    rows = sorted(oi_history, key=lambda x: int(x.get("timestamp_ms", x.get("timestamp", 0))))
    if not rows or current_price <= 0:
        return []
    cohorts: list[OICohort] = []
    previous = _finite(rows[0].get("open_interest", rows[0].get("value")), 0.0) or 0.0
    now = now_ms or int(time.time() * 1000)
    for index, row in enumerate(rows[1:], 1):
        value = _finite(row.get("open_interest", row.get("value")), 0.0) or 0.0
        delta = value - previous
        stamp = int(row.get("timestamp_ms", row.get("timestamp", now)))
        if delta > 0:
            price = _finite(row.get("price"), current_price) or current_price
            # Directional split uses observable price/CVD proxies when present;
            # absent evidence remains a low-confidence estimate, never 50/50 fact.
            price_change = _finite(row.get("price_change"), 0.0) or 0.0
            fut_delta = _finite(row.get("futures_delta"), 0.0) or 0.0
            long_share = 0.62 if price_change >= 0 and fut_delta >= 0 else 0.38 if price_change < 0 and fut_delta < 0 else 0.50
            prior = leverage_prior(delta, volatility)
            cohort = OICohort(f"{stamp}-{index}", stamp, price, delta, long_share, 1 - long_share, prior["distribution"])
            cohort.decay(value, (now - stamp) / 3_600_000)
            cohorts.append(cohort)
        previous = value
    return cohorts[-200:]


def liquidation_zones(cohorts: Sequence[OICohort], current_price: float, *, atr: float | None = None) -> list[dict[str, Any]]:
    zones: dict[tuple[str, int], dict[str, Any]] = {}
    if current_price <= 0:
        return []
    for cohort in cohorts:
        for side, share in (("LONG_LIQ", cohort.estimated_long_share), ("SHORT_LIQ", cohort.estimated_short_share)):
            for leverage, weight in cohort.leverage_distribution.items():
                notional = cohort.remaining_oi * share * float(weight) * cohort.entry_price
                price = liquidation_price(cohort.entry_price, float(leverage), "LONG" if side == "LONG_LIQ" else "SHORT")
                key = (side, round(price / max(atr or current_price * .001, 1)))
                zone = zones.setdefault(key, {"price_low": price, "price_high": price, "center_price": price, "side": side, "estimated_notional": 0.0, "cohort_count": 0, "leverage_weighted": 0.0, "oi_density": 0.0, "confidence": 0.0})
                zone["estimated_notional"] += notional
                zone["cohort_count"] += 1
                zone["leverage_weighted"] += notional * float(leverage)
                zone["oi_density"] += cohort.remaining_oi * share * float(weight)
    result = []
    for zone in zones.values():
        total = zone["estimated_notional"] or 1.0
        zone["estimated_avg_leverage"] = round(zone["leverage_weighted"] / total, 2)
        zone["distance_pct"] = (zone["center_price"] / current_price - 1) * 100
        zone["distance_atr"] = abs(zone["center_price"] - current_price) / (atr or max(current_price * .001, 1))
        zone["accessibility"] = round(accessibility_score(zone["distance_pct"], zone["distance_atr"], 0.0, 0.0), 2)
        zone["cascade_probability"] = 0.0
        zone["confidence"] = 24.0
        zone["status"] = "ESTIMATED"
        zone["methodology"] = "OI cohort × estimated directional split × leverage prior × contract liquidation projection"
        result.append(zone)
    # Re-evaluate cascade after all clusters are known. Nearby secondary pools
    # and a short distance between them are what make a cascade plausible.
    scale = max(atr or current_price * .001, 1.0)
    for zone in result:
        nearby = sorted((x for x in result if x is not zone and x["side"] == zone["side"]), key=lambda x: abs(x["center_price"] - zone["center_price"]))[:3]
        closest = abs(nearby[0]["center_price"] - zone["center_price"]) if nearby else scale * 10
        cluster_gap = clamp(1 - closest / (scale * 4), 0, 1) or 0.0
        zone["cascade_probability"] = round(cascade_probability([zone] + nearby, zone["estimated_notional"], cluster_gap, volatility=(atr / current_price if current_price else None)), 3)
    return sorted(result, key=lambda x: (-x["accessibility"], -x["estimated_notional"]))


def accessibility_score(distance_pct: float | None, distance_atr: float | None, path_friction: float, directional_support: float, volatility: float | None = None) -> float:
    if distance_pct is None or distance_atr is None:
        return 0.0
    distance = math.exp(-max(abs(distance_pct), 0.0) / 2.0) * 55 + math.exp(-max(distance_atr, 0.0) / 2.0) * 30
    friction = (1 - clamp(path_friction / 100) or 0) * 10
    flow = clamp((directional_support + 1) / 2) or 0.5
    vol_bonus = min(5.0, max(0.0, (volatility or 0) * 100))
    return max(0.0, min(100.0, distance + friction + flow * 5 + vol_bonus))


def cascade_probability(nearby_zones: Sequence[Mapping[str, Any]], primary_notional: float, path_gap: float, volatility: float | None = None) -> float:
    secondary = sum(_finite(x.get("estimated_notional"), 0.0) or 0.0 for x in nearby_zones[1:4])
    density = min(1.0, secondary / max(primary_notional, 1.0))
    gap = clamp(path_gap, 0, 1) or 0.0
    vol = min(1.0, max(0.0, (volatility or 0) * 50))
    return clamp(0.45 * density + 0.35 * gap + 0.20 * vol) or 0.0


def volume_profile(prices: Sequence[Mapping[str, Any]], bins: int = 48) -> dict[str, Any]:
    rows = [(float(p), float(n)) for p, n in ((
        (_finite(x.get("price", x.get("close"))), _finite(x.get("notional", x.get("volume", x.get("quantity", 0))))) for x in prices
    )) if p is not None and n is not None and p > 0 and n > 0]
    if not rows:
        return {"status": "UNAVAILABLE", "poc": None, "vah": None, "val": None, "hvn": [], "lvn": [], "bins": []}
    low, high = min(x[0] for x in rows), max(x[0] for x in rows)
    step = max((high - low) / max(1, bins), 0.01)
    buckets = defaultdict(float)
    for price, notional in rows:
        buckets[int((price - low) / step)] += notional
    ordered = sorted(buckets.items())
    poc_bin = max(ordered, key=lambda x: x[1])[0]
    total = sum(x[1] for x in ordered)
    target = total * .70
    included: set[int] = {poc_bin}; covered = buckets[poc_bin]
    while covered < target and len(included) < len(ordered):
        candidates = [(idx, value) for idx, value in ordered if idx not in included]
        idx, value = max(candidates, key=lambda x: x[1])
        included.add(idx); covered += value
    centers = {idx: low + (idx + .5) * step for idx in buckets}
    poc, vah, val = centers[poc_bin], max(centers[i] for i in included), min(centers[i] for i in included)
    avg = total / len(buckets)
    return {"status": "DERIVED", "poc": poc, "vah": vah, "val": val, "hvn": [centers[i] for i, v in ordered if v >= avg * 1.5], "lvn": [centers[i] for i, v in ordered if v <= avg * .5], "bins": [{"price": centers[i], "notional": v} for i, v in ordered], "methodology": "executed trade notional by adaptive price bins"}


def path_friction(target: float, current_price: float, liquidity_levels: Sequence[Mapping[str, Any]], profile: Mapping[str, Any], opposing_levels: Sequence[float] = (), *, atr: float | None = None) -> dict[str, Any]:
    if not current_price or not target:
        return {"score": None, "label": "UNAVAILABLE", "barriers": []}
    lo, hi = sorted((current_price, target))
    barriers: list[tuple[str, float, float]] = []
    for level in liquidity_levels:
        price = _finite(level.get("price"))
        if price is not None and lo <= price <= hi:
            barriers.append(("orderbook wall", price, min(1.0, (_finite(level.get("wall_strength"), 0.0) or 0.0) + (_finite(level.get("absorption_score"), 0.0) or 0.0))))
    for label in ("poc", "vah", "val"):
        price = _finite(profile.get(label))
        if price is not None and lo <= price <= hi:
            barriers.append((label.upper(), price, .45))
    for price in opposing_levels:
        if lo <= price <= hi:
            barriers.append(("opposing structure", price, .55))
    score = min(100.0, sum(weight * 28 for _, _, weight in barriers) + min(25, len(barriers) * 3))
    label = "OPEN PATH" if score <= 20 else "LOW" if score <= 40 else "MEDIUM" if score <= 60 else "HIGH" if score <= 80 else "BLOCKED / VERY HIGH"
    return {"score": round(score, 2), "label": label, "barriers": [{"type": x[0], "price": x[1], "weight": x[2]} for x in barriers], "status": "DERIVED"}


def _target_type(item: Mapping[str, Any]) -> str:
    return str(item.get("type", item.get("side", "LEVEL"))).upper()


def generate_candidates(current_price: float, *, liquidation: Sequence[Mapping[str, Any]] = (), liquidity: Sequence[Mapping[str, Any]] = (), profile: Mapping[str, Any] | None = None, levels: Mapping[str, Sequence[float]] | None = None, atr: float | None = None) -> list[dict[str, Any]]:
    profile = profile or {}
    levels = levels or {}
    raw: list[tuple[float, str, float, float]] = []
    for zone in liquidation:
        price = _finite(zone.get("center_price"))
        if price and abs(price - current_price) > max((atr or current_price * .0002) * .15, .01):
            raw.append((price, _target_type(zone), _finite(zone.get("estimated_notional"), 0.0) or 0.0, _finite(zone.get("cascade_probability"), 0.0) or 0.0))
    for row in liquidity:
        price = _finite(row.get("price")); notional = _finite(row.get("displayed_liquidity"), 0.0) or 0.0
        if price and abs(price - current_price) > .01:
            raw.append((price, "ORDERBOOK_WALL", notional, 0.0))
    for label in ("poc", "vah", "val"):
        price = _finite(profile.get(label))
        if price and abs(price - current_price) > .01: raw.append((price, label.upper(), 0.0, 0.0))
    for label, values in levels.items():
        for price in values or []:
            price = _finite(price)
            if price and abs(price - current_price) > .01: raw.append((price, str(label).upper(), 0.0, 0.0))
    raw.sort(key=lambda x: x[0])
    merged: list[dict[str, Any]] = []
    merge_distance = max((atr or current_price * .001) * .20, .01)
    for price, kind, notional, cascade in raw:
        if merged and abs(price - merged[-1]["targetCenter"]) <= merge_distance:
            item = merged[-1]
            item["targetLow"] = min(item["targetLow"], price); item["targetHigh"] = max(item["targetHigh"], price)
            item["types"].append(kind); item["estimatedNotional"] += notional; item["cascadeProbability"] = max(item["cascadeProbability"], cascade)
        else:
            merged.append({"targetCenter": price, "targetLow": price, "targetHigh": price, "types": [kind], "estimatedNotional": notional, "cascadeProbability": cascade})
    return merged


def attraction_score(target: Mapping[str, Any], current_price: float, *, atr: float | None = None, liquidity_accessibility: float = 0.0, profile_confluence: float = 0.0, structure_alignment: float = 0.0, directional_flow: float = 0.0, positioning_support: float = 0.0, path_friction_score: float = 50.0) -> float:
    distance_atr = abs(float(target["targetCenter"]) - current_price) / max(atr or current_price * .001, 1e-9)
    distance = max(0.0, 1 - min(1.0, distance_atr / 8)) * 20
    confluence = min(20, len(set(target.get("types", []))) * 5) + profile_confluence * 10
    score = distance + liquidity_accessibility * 0.22 + confluence + structure_alignment * 10 + directional_flow * 10 + positioning_support * 8 + (1 - path_friction_score / 100) * 20
    return round(max(0.0, min(100.0, score)), 2)


def calibrate_score(score: float, calibration: Sequence[Mapping[str, Any]] | None, *, minimum_sample: int = 30) -> tuple[float | None, int, str]:
    if not calibration:
        return None, 0, "UNAVAILABLE"
    rows = [x for x in calibration if _finite(x.get("score_low", 0)) <= score <= _finite(x.get("score_high", 100)) and int(x.get("sample_size", 0)) >= minimum_sample and x.get("hit_rate") is not None]
    if not rows:
        return None, 0, "UNAVAILABLE"
    row = max(rows, key=lambda x: int(x.get("sample_size", 0)))
    return round_probability(_finite(row.get("hit_rate"))), int(row.get("sample_size", 0)), "CALIBRATED"


def monotonic_probabilities(values: Mapping[int, float | None]) -> dict[str, float | None]:
    last = 0.0
    out: dict[str, float | None] = {}
    for minutes in HORIZONS_MINUTES:
        value = values.get(minutes)
        if value is not None:
            last = max(last, float(value))
            value = last
        out[{15: "hit15m", 30: "hit30m", 60: "hit1h", 240: "hit4h"}[minutes]] = round_probability(value)
    return out


def competing_first_hit(targets: Sequence[Mapping[str, Any]]) -> dict[str, float | None]:
    weights = {str(x.get("id", index)): _finite((x.get("probability") or {}).get("hit1h"), 0.0) or 0.0 for index, x in enumerate(targets)}
    total = sum(weights.values())
    if not total:
        return {key: None for key in weights}
    return {key: round_probability(value / total) for key, value in weights.items()}


def eta_estimate(current_price: float, target: float, *, atr: float | None = None, volatility: float | None = None, path_friction_score: float = 50.0, directional_velocity: float | None = None, historical_eta: Mapping[str, Any] | None = None) -> dict[str, Any]:
    if historical_eta and historical_eta.get("medianMinutes") is not None:
        median = max(1.0, float(historical_eta["medianMinutes"])); p25 = historical_eta.get("p25Minutes", median * .6); p75 = historical_eta.get("p75Minutes", median * 1.6); status = "DERIVED/HISTORICAL_ANALOGUE"
    else:
        distance = abs(target - current_price)
        velocity = abs(directional_velocity or 0.0)
        baseline = (distance / velocity / 60) if velocity > 0 else (distance / max(atr or current_price * .001, 1) * 15)
        median = max(1.0, baseline * (1 + path_friction_score / 100))
        p25, p75, status = max(1.0, median * .55), median * 1.85, "DERIVED_MODEL_ESTIMATE"
    return {"medianMinutes": round(median, 1), "p25Minutes": round(p25, 1), "p75Minutes": round(p75, 1), "status": status, "confidence": round(max(0, 100 - min(90, path_friction_score)) * .35, 1)}


def build_target_feature_vector(current_price: float, target: Mapping[str, Any], *, atr: float | None = None, path: Mapping[str, Any] | None = None, flow: Mapping[str, Any] | None = None, positioning: Mapping[str, Any] | None = None, session: str | None = None) -> dict[str, Any]:
    path = path or {}; flow = flow or {}; positioning = positioning or {}
    center = float(target["targetCenter"])
    distance = center - current_price
    vector = {
        "timestamp": int(time.time() * 1000), "symbol": "BTCUSDT", "current_price": current_price, "target_price": center, "target_low": target.get("targetLow", center), "target_high": target.get("targetHigh", center), "target_type": target.get("types", []), "direction": "UP" if distance > 0 else "DOWN", "distance_usd": distance, "distance_pct": distance / current_price * 100 if current_price else None, "distance_bps": distance / current_price * 10000 if current_price else None, "distance_atr": abs(distance) / max(atr or current_price * .001, 1e-9), "liquidation_density": target.get("estimatedNotional", 0.0), "cascade_probability": target.get("cascadeProbability"), "path_friction": path.get("score"), "price_velocity": flow.get("price_velocity"), "price_acceleration": flow.get("price_acceleration"), "futures_cvd": flow.get("futures_cvd"), "spot_cvd": flow.get("spot_cvd"), "delta": flow.get("delta"), "OI": positioning.get("OI"), "delta_OI": positioning.get("delta_OI"), "funding": positioning.get("funding"), "session": session, "status": "DERIVED",
    }
    return vector


def build_probability_targets(current_price: float, candidates: Sequence[Mapping[str, Any]], *, calibration: Mapping[int, Sequence[Mapping[str, Any]]] | None = None, atr: float | None = None, liquidity_levels: Sequence[Mapping[str, Any]] = (), profile: Mapping[str, Any] | None = None, flow: Mapping[str, Any] | None = None, positioning: Mapping[str, Any] | None = None, max_targets: int = 50) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    for index, raw in enumerate(candidates):
        target = dict(raw); center = float(target["targetCenter"])
        target["types"] = sorted(set(target.get("types", [])))
        path = path_friction(center, current_price, liquidity_levels, profile or {}, atr=atr)
        score = attraction_score(target, current_price, atr=atr, liquidity_accessibility=accessibility_score((center / current_price - 1) * 100, abs(center - current_price) / max(atr or current_price * .001, 1), path.get("score") or 50, 0), profile_confluence=min(1, len(set(target.get("types", []))) / 4), directional_flow=(1 if (center < current_price and (flow or {}).get("futures_cvd", 0) < 0) or (center > current_price and (flow or {}).get("futures_cvd", 0) > 0) else 0), path_friction_score=path.get("score") or 50)
        cal_rows = (calibration or {}).get(60, ())
        p1h, sample, cal_status = calibrate_score(score, cal_rows)
        probabilities = monotonic_probabilities({15: None if p1h is None else p1h * .38, 30: None if p1h is None else p1h * .64, 60: p1h, 240: None if p1h is None else min(1.0, p1h * 1.23)})
        target["id"] = f"target-{index + 1}"; target["direction"] = "UP" if center > current_price else "DOWN"; target["distancePct"] = abs(center / current_price - 1) * 100; target["distanceAtr"] = abs(center - current_price) / max(atr or current_price * .001, 1); target["attractionScore"] = score; target["probability"] = probabilities; target["status"] = cal_status if p1h is not None else "MODEL_SCORE"; target["calibrationSampleSize"] = sample; target["pathFriction"] = path.get("score"); target["pathFrictionLabel"] = path.get("label"); target["eta"] = eta_estimate(current_price, center, atr=atr, path_friction_score=path.get("score") or 50, directional_velocity=(flow or {}).get("price_velocity")); target["confidence"] = round(min(100, (35 if cal_status == "CALIBRATED" else 15) + min(30, sample / 10) + (20 if liquidity_levels else 0) + (10 if profile and profile.get("status") != "UNAVAILABLE" else 0)), 1); target["why"] = [f"{x} confluence" for x in sorted(set(target.get("types", [])))]; target["against"] = [f"Path friction {path.get('label', 'UNKNOWN')}" if path.get("score") is not None else "Path friction unavailable"]; target["missing"] = [] if cal_status == "CALIBRATED" else ["target-level historical calibration"]
        target["featureVector"] = build_target_feature_vector(current_price, target, atr=atr, path=path, flow=flow, positioning=positioning)
        result.append(target)
    result = sorted(result, key=lambda x: x.get("attractionScore", 0), reverse=True)[:max(1, max_targets)]
    first = competing_first_hit(result)
    for item in result: item["probability"]["firstHit"] = first.get(item["id"])
    return sorted(result, key=lambda x: (x.get("probability", {}).get("firstHit") is not None, x.get("probability", {}).get("firstHit") or -1, x.get("attractionScore", 0)), reverse=True)


def data_health(rows: Mapping[str, Mapping[str, Any]]) -> list[dict[str, Any]]:
    result = []
    for name, value in rows.items():
        status = value.get("status", "UNAVAILABLE") if isinstance(value, Mapping) else "UNAVAILABLE"
        result.append({"source": name, "status": status, "age_seconds": value.get("age_seconds") if isinstance(value, Mapping) else None, "coverage": value.get("coverage") if isinstance(value, Mapping) else None, "confidence": value.get("confidence") if isinstance(value, Mapping) else None, "reason": value.get("reason") if isinstance(value, Mapping) else "not supplied"})
    return result


def _true_range(row: Mapping[str, Any], previous_close: float | None) -> float:
    high = _finite(row.get("high", row.get("h")), 0.0) or 0.0
    low = _finite(row.get("low", row.get("l")), 0.0) or 0.0
    close = _finite(row.get("close", row.get("c")), 0.0) or 0.0
    return max(high - low, abs(high - (previous_close or close)), abs(low - (previous_close or close)))


def historical_target_replay(
    bars: Sequence[Mapping[str, Any]],
    *,
    timeframe_seconds: int = 60,
    warmup_bars: int = 120,
    max_snapshots: int | None = None,
) -> dict[str, Any]:
    """Replay candidate targets without future feature leakage.

    Only ``bars[:index]`` builds the feature vector. Future bars are consulted
    exclusively by the outcome labeller. The returned rows are suitable for a
    chronological train/validation/OOS split.
    """
    ordered = sorted((dict(row) for row in bars if row.get("closed", True) is not False), key=lambda x: int(x.get("timestamp_ms", x.get("t", x.get("time", 0)))))
    if len(ordered) <= warmup_bars + 240:
        return {"status": "UNAVAILABLE", "reason": "insufficient closed bars for 4h labels", "rows": [], "snapshots": []}
    horizon_bars = {15: max(1, round(15 * 60 / timeframe_seconds)), 30: max(1, round(30 * 60 / timeframe_seconds)), 60: max(1, round(60 * 60 / timeframe_seconds)), 240: max(1, round(240 * 60 / timeframe_seconds))}
    last_index = len(ordered) - max(horizon_bars.values())
    if max_snapshots:
        first_index = max(warmup_bars, last_index - max_snapshots)
    else:
        first_index = warmup_bars
    rows: list[dict[str, Any]] = []
    snapshots: list[dict[str, Any]] = []
    for index in range(first_index, last_index):
        past = ordered[:index]
        current = _finite(past[-1].get("close", past[-1].get("c")))
        if current is None or current <= 0:
            continue
        ranges = [_true_range(row, _finite(past[pos - 1].get("close", past[pos - 1].get("c"))) if pos else None) for pos, row in enumerate(past[-60:], max(0, len(past) - 60))]
        atr = statistics.median([x for x in ranges if x > 0]) if any(x > 0 for x in ranges) else current * .001
        profile = volume_profile([{"price": _finite(row.get("close", row.get("c"))), "notional": _finite(row.get("volume", row.get("v")), 0.0)} for row in past[-240:]], bins=32)
        highs = [_finite(row.get("high", row.get("h"))) for row in past[-120:]]
        lows = [_finite(row.get("low", row.get("l"))) for row in past[-120:]]
        levels = {"SWING_HIGH": [max(x for x in highs if x is not None)] if any(x is not None for x in highs) else [], "SWING_LOW": [min(x for x in lows if x is not None)] if any(x is not None for x in lows) else []}
        candidates = generate_candidates(current, profile=profile, levels=levels, atr=atr)
        # A target is a zone, not a one-tick touch. The width is derived only
        # from past ATR and is fixed before any future bar is read.
        for candidate in candidates:
            center = candidate["targetCenter"]
            candidate["targetLow"] = center - .10 * atr
            candidate["targetHigh"] = center + .10 * atr
        cvd = sum((_finite(row.get("buy_volume", row.get("bv")), 0.0) or 0.0) - (_finite(row.get("sell_volume", row.get("sv")), 0.0) or 0.0) for row in past[-30:])
        feature_targets = build_probability_targets(current, candidates, atr=atr, profile=profile, flow={"futures_cvd": cvd, "price_velocity": (_finite(past[-1].get("close", past[-1].get("c"))) - _finite(past[-2].get("close", past[-2].get("c"))) if len(past) > 1 else None)}, positioning={"delta_OI": None})
        if not feature_targets:
            continue
        timestamp = int(past[-1].get("timestamp_ms", past[-1].get("t", past[-1].get("time", 0))))
        snapshot_targets: list[dict[str, Any]] = []
        for target_index, target in enumerate(feature_targets):
            outcome = {"hit15m": False, "hit30m": False, "hit1h": False, "hit4h": False, "timeToHitMinutes": {}, "firstHit": {}}
            for minutes, horizon in horizon_bars.items():
                future = ordered[index:index + horizon]
                first_hit = None
                for offset, bar in enumerate(future, 1):
                    high = _finite(bar.get("high", bar.get("h")))
                    low = _finite(bar.get("low", bar.get("l")))
                    if high is not None and low is not None and high >= target["targetLow"] and low <= target["targetHigh"]:
                        first_hit = offset
                        break
                field_name = {15: "hit15m", 30: "hit30m", 60: "hit1h", 240: "hit4h"}[minutes]
                outcome[field_name] = first_hit is not None
                outcome["timeToHitMinutes"][str(minutes)] = first_hit * timeframe_seconds / 60 if first_hit is not None else None
                outcome["firstHit"][str(minutes)] = None if first_hit is None else first_hit
            row = {"timestamp_ms": timestamp, "target_id": f"{timestamp}-{target_index}", "score": target["attractionScore"], "target_price": target["targetCenter"], "target_low": target["targetLow"], "target_high": target["targetHigh"], "direction": target["direction"], "types": target["types"], "outcome": outcome, "featureVector": target["featureVector"]}
            snapshot_targets.append(row)
            rows.append(row)
        # Label which candidate was touched first on each horizon. Ties remain
        # deterministic by candidate order and are not treated as probability.
        for minutes in HORIZONS_MINUTES:
            field_name = {15: "hit15m", 30: "hit30m", 60: "hit1h", 240: "hit4h"}[minutes]
            hit_rows = [row for row in snapshot_targets if row["outcome"][field_name]]
            first = min(hit_rows, key=lambda row: (row["outcome"]["timeToHitMinutes"][str(minutes)], abs(row["target_price"] - current))) if hit_rows else None
            for row in snapshot_targets:
                row["outcome"]["firstHit"][str(minutes)] = bool(first and first["target_id"] == row["target_id"])
        snapshots.append({"timestamp_ms": timestamp, "current_price": current, "atr": atr, "targets": snapshot_targets})
    return {"status": "DERIVED" if rows else "UNAVAILABLE", "rows": rows, "snapshots": snapshots, "sample_size": len(rows), "snapshot_size": len(snapshots), "timeframe_seconds": timeframe_seconds, "methodology": "past-only profile/ATR/swing candidate features; future OHLC used only for labels"}


def build_target_calibration(replay: Mapping[str, Any], *, train_fraction: float = .60, validation_fraction: float = .20, minimum_sample: int = 30) -> dict[str, Any]:
    rows = sorted(list(replay.get("rows", [])), key=lambda x: int(x.get("timestamp_ms", 0)))
    timestamps = sorted({int(x.get("timestamp_ms", 0)) for x in rows})
    if not rows or len(timestamps) < 3:
        return {"status": "UNAVAILABLE", "reason": "no chronological replay rows", "calibration": {}, "metrics": {}}
    train_cut = timestamps[max(0, min(len(timestamps) - 1, int(len(timestamps) * train_fraction) - 1))]
    validation_cut = timestamps[max(0, min(len(timestamps) - 1, int(len(timestamps) * (train_fraction + validation_fraction)) - 1))]
    train = [row for row in rows if int(row["timestamp_ms"]) <= train_cut]
    validation = [row for row in rows if train_cut < int(row["timestamp_ms"]) <= validation_cut]
    oos = [row for row in rows if int(row["timestamp_ms"]) > validation_cut]
    calibration: dict[str, list[dict[str, Any]]] = {}
    for minutes, field_name in ((15, "hit15m"), (30, "hit30m"), (60, "hit1h"), (240, "hit4h")):
        values: list[dict[str, Any]] = []
        for bucket in range(10):
            low, high = bucket * 10, 100 if bucket == 9 else (bucket + 1) * 10
            group = [row for row in train if low <= float(row.get("score", 0)) < high]
            if not group:
                continue
            hits = [bool((row.get("outcome") or {}).get(field_name)) for row in group]
            values.append({"score_low": low, "score_high": high, "hit_rate": sum(hits) / len(hits), "sample_size": len(hits), "horizon_minutes": minutes, "status": "CALIBRATED" if len(hits) >= minimum_sample else "INSUFFICIENT_SAMPLE"})
        calibration[str(minutes)] = values

    def metrics(group: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
        output: dict[str, Any] = {}
        for minutes, field_name in ((15, "hit15m"), (30, "hit30m"), (60, "hit1h"), (240, "hit4h")):
            mapped = []
            for row in group:
                score = float(row.get("score", 0)); bin_rows = [x for x in calibration[str(minutes)] if x["score_low"] <= score < x["score_high"] and x["sample_size"] >= minimum_sample]
                if bin_rows:
                    mapped.append((bin_rows[0]["hit_rate"], bool((row.get("outcome") or {}).get(field_name))))
            brier = sum((p - int(hit)) ** 2 for p, hit in mapped) / len(mapped) if mapped else None
            ece = sum(abs(sum(int(hit) for _, hit in mapped) / len(mapped) - sum(p for p, _ in mapped) / len(mapped)) for _ in [0]) if mapped else None
            output[str(minutes)] = {"sample_size": len(mapped), "brier_score": brier, "calibration_error": ece}
        return output
    return {"status": "CALIBRATED" if any(x["sample_size"] >= minimum_sample for values in calibration.values() for x in values) else "INSUFFICIENT_SAMPLE", "methodology": "chronological TRAIN/VALIDATION/OUT_OF_SAMPLE; calibration learned from TRAIN only", "sample_size": len(rows), "snapshot_size": len(timestamps), "train_cutoff_ms": train_cut, "validation_cutoff_ms": validation_cut, "calibration": calibration, "metrics": {"TRAIN": metrics(train), "VALIDATION": metrics(validation), "OUT_OF_SAMPLE": metrics(oos)}, "scoreIsProbability": False, "minimum_calibration_sample": minimum_sample}
