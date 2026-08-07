from __future__ import annotations

import math
import statistics
import time
from dataclasses import asdict, dataclass, field
from enum import Enum
from typing import Any, Iterable, Mapping


class MetricStatus(str, Enum):
    REAL = "REAL"
    DERIVED = "DERIVED"
    PROXY = "PROXY"
    UNAVAILABLE = "UNAVAILABLE"
    STALE = "STALE"
    UNRELIABLE = "UNRELIABLE"


@dataclass(frozen=True)
class MetricMeta:
    source: str
    source_type: str
    timestamp: int | None
    age_seconds: float | None
    freshness: str
    methodology: str
    confidence: float | None
    coverage: float | None
    status: MetricStatus

    def as_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["status"] = self.status.value
        return data


@dataclass(frozen=True)
class AggTrade:
    market: str
    symbol: str
    timestamp: int
    aggregate_trade_id: int
    price: float
    quantity_btc: float
    notional_usd: float
    buyer_is_maker: bool
    aggressor_side: str

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


def _number(value: Any, default: float = 0.0) -> float:
    try:
        result = float(value)
        return result if math.isfinite(result) else default
    except (TypeError, ValueError):
        return default


def _integer(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def normalize_agg_trade(
    payload: Mapping[str, Any], market: str, symbol: str = "BTCUSDT"
) -> AggTrade:
    """Normalize Spot/Futures aggTrade and validate Binance's maker flag.

    Binance's ``m``/``buyerMaker`` flag means "is the buyer the market
    maker?".  Therefore ``false`` means a taker buy and ``true`` means a
    taker sell.  This mapping is kept in one function and covered by tests.
    """
    body: Mapping[str, Any] = payload.get("data", payload)
    price = _number(body.get("p", body.get("price")))
    quantity = _number(body.get("q", body.get("quantity")))
    trade_id = _integer(body.get("a", body.get("aggregate_trade_id")))
    timestamp = _integer(body.get("T", body.get("time", body.get("timestamp"))))
    if price <= 0 or quantity <= 0 or timestamp <= 0:
        raise ValueError("aggTrade requires positive price, quantity, and timestamp")
    raw_maker = body.get("m", body.get("buyerMaker", body.get("buyer_is_maker")))
    if isinstance(raw_maker, str):
        buyer_is_maker = raw_maker.strip().lower() in {"1", "true", "yes"}
    else:
        buyer_is_maker = bool(raw_maker)
    return AggTrade(
        market=market.lower(),
        symbol=str(body.get("s", symbol)).upper(),
        timestamp=timestamp,
        aggregate_trade_id=trade_id,
        price=price,
        quantity_btc=quantity,
        notional_usd=price * quantity,
        buyer_is_maker=buyer_is_maker,
        aggressor_side="SELL" if buyer_is_maker else "BUY",
    )


def _percentile(values: Iterable[float], p: float) -> float | None:
    ordered = sorted(float(x) for x in values if math.isfinite(float(x)))
    if not ordered:
        return None
    p = max(0.0, min(1.0, p))
    position = (len(ordered) - 1) * p
    lower = math.floor(position)
    upper = math.ceil(position)
    if lower == upper:
        return ordered[lower]
    return ordered[lower] + (ordered[upper] - ordered[lower]) * (position - lower)


def aggregate_trades(
    trades: Iterable[AggTrade], timeframe_seconds: int, cvd_start: float = 0.0
) -> list[dict[str, Any]]:
    """Aggregate raw executions locally; no candle volume is inferred."""
    if timeframe_seconds <= 0:
        raise ValueError("timeframe_seconds must be positive")
    ordered = sorted(trades, key=lambda x: (x.timestamp, x.aggregate_trade_id))
    buckets: dict[int, list[AggTrade]] = {}
    for trade in ordered:
        bucket = (trade.timestamp // 1000 // timeframe_seconds) * timeframe_seconds
        buckets.setdefault(bucket, []).append(trade)
    rows: list[dict[str, Any]] = []
    cvd = cvd_start
    for bucket, items in sorted(buckets.items()):
        buys = [x for x in items if x.aggressor_side == "BUY"]
        sells = [x for x in items if x.aggressor_side == "SELL"]
        buy_usd = sum(x.notional_usd for x in buys)
        sell_usd = sum(x.notional_usd for x in sells)
        delta = buy_usd - sell_usd
        cvd += delta
        prices = [x.price for x in items]
        notionals = [x.notional_usd for x in items]
        rows.append(
            {
                "timestamp": bucket * 1000,
                "bucket_end": (bucket + timeframe_seconds) * 1000,
                "open": prices[0],
                "high": max(prices),
                "low": min(prices),
                "close": prices[-1],
                "price_change": prices[-1] - prices[0],
                "buy_usd": buy_usd,
                "sell_usd": sell_usd,
                "delta_usd": delta,
                "total_usd": buy_usd + sell_usd,
                "buy_trade_count": len(buys),
                "sell_trade_count": len(sells),
                "trade_count": len(items),
                "avg_trade_size_usd": statistics.fmean(notionals),
                "median_trade_size_usd": statistics.median(notionals),
                "max_trade_size_usd": max(notionals),
                "notional_per_second": (buy_usd + sell_usd) / timeframe_seconds,
                "cvd": cvd,
            }
        )
    return rows


def trader_size_thresholds(
    notional_history: Iterable[float], market: str, absolute_notional_guard: float | None = None
) -> dict[str, Any]:
    values = [float(x) for x in notional_history if _number(x) > 0]
    if not values:
        return {
            "market": market,
            "status": MetricStatus.UNAVAILABLE.value,
            "sample_size": 0,
            "thresholds": {},
        }
    thresholds = {f"p{label}": _percentile(values, p) for label, p in {
        "50": 0.50, "70": 0.70, "90": 0.90, "95": 0.95,
        "99": 0.99, "99_5": 0.995, "99_9": 0.999,
    }.items()}
    return {
        "market": market,
        "status": MetricStatus.DERIVED.value,
        "methodology": "rolling 30-day aggTrade notional distribution; no future observations",
        "sample_size": len(values),
        "thresholds": thresholds,
        "absolute_notional_guard": absolute_notional_guard or thresholds["p99"],
    }


def classify_trade_size(notional_usd: float, thresholds: Mapping[str, Any]) -> str:
    t = thresholds.get("thresholds", thresholds)
    if not t:
        return "UNAVAILABLE"
    value = float(notional_usd)
    if value > float(t.get("p99_9") or math.inf):
        return "MEGA_WHALE_SIZE"
    if value >= float(t.get("p99") or math.inf):
        return "WHALE_SIZE"
    if value >= float(t.get("p90") or math.inf):
        return "LARGE"
    if value >= float(t.get("p70") or math.inf):
        return "MEDIUM"
    return "SMALL"


def bucket_flows(trades: Iterable[AggTrade], thresholds: Mapping[str, Any]) -> dict[str, dict[str, Any]]:
    names = ["SMALL", "MEDIUM", "LARGE", "WHALE_SIZE", "MEGA_WHALE_SIZE"]
    result = {
        name: {
            "buy_usd": 0.0, "sell_usd": 0.0, "net_flow": 0.0,
            "buy_count": 0, "sell_count": 0, "participation_rate": 0.0,
            "cvd": 0.0, "average_notional": 0.0, "median_notional": 0.0,
            "max_notional": 0.0, "flow_velocity": 0.0,
        } for name in names
    }
    all_trades = list(trades)
    total_count = len(all_trades)
    for trade in all_trades:
        bucket = classify_trade_size(trade.notional_usd, thresholds)
        if bucket not in result:
            continue
        row = result[bucket]
        if trade.aggressor_side == "BUY":
            row["buy_usd"] += trade.notional_usd
            row["buy_count"] += 1
            row["cvd"] += trade.notional_usd
        else:
            row["sell_usd"] += trade.notional_usd
            row["sell_count"] += 1
            row["cvd"] -= trade.notional_usd
    for row in result.values():
        count = row["buy_count"] + row["sell_count"]
        values = [
            t.notional_usd for t in all_trades
            if classify_trade_size(t.notional_usd, thresholds) == next(
                name for name, item in result.items() if item is row
            )
        ]
        row["net_flow"] = row["buy_usd"] - row["sell_usd"]
        row["participation_rate"] = count / total_count if total_count else 0.0
        row["average_notional"] = statistics.fmean(values) if values else 0.0
        row["median_notional"] = statistics.median(values) if values else 0.0
        row["max_notional"] = max(values) if values else 0.0
    return result


def orderbook_metrics(snapshot: Mapping[str, Any], mid_price: float | None = None) -> dict[str, Any]:
    bids = [(float(x[0]), float(x[1])) for x in snapshot.get("bids", []) if len(x) >= 2 and _number(x[1]) > 0]
    asks = [(float(x[0]), float(x[1])) for x in snapshot.get("asks", []) if len(x) >= 2 and _number(x[1]) > 0]
    if not bids or not asks:
        return {"status": MetricStatus.UNAVAILABLE.value, "reason": "depth snapshot missing bids or asks"}
    best_bid, best_ask = max(x[0] for x in bids), min(x[0] for x in asks)
    mid = mid_price or ((best_bid + best_ask) / 2)

    def depth(side: list[tuple[float, float]], bps: float, is_bid: bool) -> float:
        distance = mid * bps / 10000
        if is_bid:
            return sum(p * q for p, q in side if mid - distance <= p <= mid)
        return sum(p * q for p, q in side if mid <= p <= mid + distance)

    depths = {}
    for bps in (5, 10, 25):
        depths[f"bid_depth_{bps}bps_usd"] = depth(bids, bps, True)
        depths[f"ask_depth_{bps}bps_usd"] = depth(asks, bps, False)
    total_10 = depths["bid_depth_10bps_usd"] + depths["ask_depth_10bps_usd"]
    depths.update({
        "best_bid": best_bid,
        "best_ask": best_ask,
        "mid": mid,
        "spread": best_ask - best_bid,
        "imbalance_10bps": (
            (depths["bid_depth_10bps_usd"] - depths["ask_depth_10bps_usd"]) / total_10
            if total_10 else None
        ),
        "wall_persistence": None,
        "spoof_probability": None,
        "liquidity_pull": None,
        "status": MetricStatus.DERIVED.value,
        "methodology": "REST snapshot/WS depth levels; displayed liquidity is not executed flow",
    })
    return depths


def compare_orderbooks(previous: Mapping[str, Any] | None, current: Mapping[str, Any]) -> dict[str, Any]:
    if not previous:
        return {"status": MetricStatus.UNAVAILABLE.value, "reason": "previous snapshot missing"}

    def side_map(data: Mapping[str, Any], key: str) -> dict[float, float]:
        return {float(row[0]): float(row[1]) for row in data.get(key, []) if len(row) >= 2}

    out: dict[str, Any] = {"status": MetricStatus.DERIVED.value, "added_usd": 0.0, "removed_usd": 0.0}
    for key in ("bids", "asks"):
        before, after = side_map(previous, key), side_map(current, key)
        for price in set(before) | set(after):
            delta = (after.get(price, 0.0) - before.get(price, 0.0)) * price
            if delta > 0:
                out["added_usd"] += delta
            else:
                out["removed_usd"] += abs(delta)
    out["cancel_ratio"] = out["removed_usd"] / (out["added_usd"] + out["removed_usd"]) if (out["added_usd"] + out["removed_usd"]) else None
    return out


def _softmax(scores: Mapping[str, float]) -> dict[str, float]:
    if not scores:
        return {}
    peak = max(scores.values())
    exps = {key: math.exp(max(-30.0, value - peak)) for key, value in scores.items()}
    total = sum(exps.values()) or 1.0
    return {key: round(value / total * 100, 2) for key, value in exps.items()}


def position_state(inputs: Mapping[str, Any]) -> dict[str, Any]:
    price = _number(inputs.get("price_change"))
    oi = _number(inputs.get("delta_oi"))
    fut = _number(inputs.get("futures_delta"))
    long_liq = _number(inputs.get("long_liquidation"))
    short_liq = _number(inputs.get("short_liquidation"))
    scores = {name: 0.0 for name in (
        "NEW_LONGS", "NEW_SHORTS", "LONG_CLOSING", "SHORT_CLOSING",
        "LONG_LIQUIDATION", "SHORT_LIQUIDATION", "DELEVERAGING",
        "LEVERAGE_BUILDUP", "MIXED",
    )}
    if price > 0 and oi > 0 and fut > 0:
        scores["NEW_LONGS"] += 3
    if price < 0 and oi > 0 and fut < 0:
        scores["NEW_SHORTS"] += 3
    if oi < 0:
        scores["DELEVERAGING"] += 2
        if price < 0 and fut > 0:
            scores["LONG_CLOSING"] += 2
        if price > 0 and fut < 0:
            scores["SHORT_CLOSING"] += 2
    if long_liq > short_liq and long_liq > 0:
        scores["LONG_LIQUIDATION"] += 4
        scores["DELEVERAGING"] += 2
    if short_liq > long_liq and short_liq > 0:
        scores["SHORT_LIQUIDATION"] += 4
        scores["DELEVERAGING"] += 2
    if oi > 0:
        scores["LEVERAGE_BUILDUP"] += 2
    if max(abs(price), abs(oi), abs(fut), long_liq, short_liq) == 0:
        return {"status": MetricStatus.UNAVAILABLE.value, "probabilities": {}, "state": "UNKNOWN"}
    probabilities = _softmax(scores)
    state = max(probabilities, key=probabilities.get)
    return {
        "status": MetricStatus.DERIVED.value,
        "probabilities": probabilities,
        "state": state,
        "inputs": dict(inputs),
        "methodology": "evidence score + softmax; not deterministic if/else and not a trade signal",
    }


def whale_behavior(inputs: Mapping[str, Any]) -> dict[str, Any]:
    buy = _number(inputs.get("whale_buy_usd")) + _number(inputs.get("mega_whale_buy_usd"))
    sell = _number(inputs.get("whale_sell_usd")) + _number(inputs.get("mega_whale_sell_usd"))
    cvd = _number(inputs.get("spot_cvd"))
    price = _number(inputs.get("price_change"))
    replenishment = _number(inputs.get("replenishment_ratio"))
    impact = _number(inputs.get("impact_zscore"))
    scores = {name: 0.0 for name in (
        "ACCUMULATION", "DISTRIBUTION", "ABSORPTION", "DUMP",
        "AGGRESSIVE_BUYING", "AGGRESSIVE_SELLING", "NO_CLEAR_BEHAVIOR",
    )}
    if buy > sell and cvd > 0:
        scores["AGGRESSIVE_BUYING"] += 2
        if price <= 0:
            scores["ACCUMULATION"] += 3
        if replenishment > 0.5 and abs(price) < abs(impact or 1):
            scores["ABSORPTION"] += 2
    if sell > buy and cvd < 0:
        scores["AGGRESSIVE_SELLING"] += 2
        if price >= 0:
            scores["DISTRIBUTION"] += 3
        if _number(inputs.get("mega_whale_sell_usd")) > _number(inputs.get("whale_sell_usd")) and impact > 1:
            scores["DUMP"] += 4
    if max(scores.values()) == 0:
        return {"status": MetricStatus.UNAVAILABLE.value, "probabilities": {}, "behavior": "NO_CLEAR_BEHAVIOR"}
    scores["NO_CLEAR_BEHAVIOR"] = 0.5
    probabilities = _softmax(scores)
    return {"status": MetricStatus.DERIVED.value, "probabilities": probabilities, "behavior": max(probabilities, key=probabilities.get), "inputs": dict(inputs)}


def spot_futures_divergence(spot_delta: float | None, futures_delta: float | None) -> dict[str, Any]:
    if spot_delta is None or futures_delta is None:
        return {"status": MetricStatus.UNAVAILABLE.value, "state": "UNKNOWN", "strength": None}
    spot_up, futures_up = spot_delta > 0, futures_delta > 0
    state = {
        (True, True): "SPOT_UP_FUTURES_UP",
        (True, False): "SPOT_UP_FUTURES_DOWN",
        (False, True): "SPOT_DOWN_FUTURES_UP",
        (False, False): "SPOT_DOWN_FUTURES_DOWN",
    }[(spot_up, futures_up)]
    denominator = abs(spot_delta) + abs(futures_delta)
    strength = round(abs(spot_delta - futures_delta) / denominator * 100, 2) if denominator else 0.0
    return {
        "status": MetricStatus.DERIVED.value,
        "state": state,
        "strength": strength,
        "methodology": "normalized local CVD/delta slope signs; spot and futures remain separate",
    }


def _meta(source: str, source_type: str, timestamp: int | None, status: MetricStatus, methodology: str, coverage: float | None = None, confidence: float | None = None, stale_after_seconds: int = 120) -> dict[str, Any]:
    now = int(time.time() * 1000)
    age = (now - timestamp) / 1000 if timestamp else None
    freshness = "UNKNOWN" if age is None else ("FRESH" if age < stale_after_seconds else "STALE")
    if status == MetricStatus.REAL and age is not None and age > stale_after_seconds:
        status = MetricStatus.STALE
    return MetricMeta(source, source_type, timestamp, age, freshness, methodology, confidence, coverage, status).as_dict()


@dataclass
class CapitalFlowEngine:
    symbol: str = "BTCUSDT"
    spot_trades: list[AggTrade] = field(default_factory=list)
    futures_trades: list[AggTrade] = field(default_factory=list)
    spot_thresholds: dict[str, Any] = field(default_factory=dict)
    futures_thresholds: dict[str, Any] = field(default_factory=dict)
    oi: dict[str, Any] | None = None
    funding: dict[str, Any] | None = None
    liquidations: dict[str, Any] | None = None
    top_traders: dict[str, Any] | None = None
    orderbook: dict[str, Any] | None = None
    previous_orderbook: dict[str, Any] | None = None

    def add_trade(self, trade: AggTrade) -> None:
        target = self.spot_trades if trade.market == "spot" else self.futures_trades
        target.append(trade)

    def set_size_history(self, spot_history: Iterable[float], futures_history: Iterable[float]) -> None:
        self.spot_thresholds = trader_size_thresholds(spot_history, "spot")
        self.futures_thresholds = trader_size_thresholds(futures_history, "futures")

    def set_oi(self, value: float, timestamp: int, previous: float | None = None) -> None:
        self.oi = {"value": value, "previous": previous, "delta": value - previous if previous is not None else None, "timestamp": timestamp}

    def set_funding(self, rate: float, timestamp: int) -> None:
        self.funding = {"rate": rate, "timestamp": timestamp}

    def set_liquidations(self, value: Mapping[str, Any], timestamp: int) -> None:
        self.liquidations = {**value, "timestamp": timestamp}

    def set_top_traders(self, value: Mapping[str, Any], timestamp: int) -> None:
        normalized = {**value, "timestamp": timestamp}
        accounts = normalized.get("account_ratio") or {}
        positions = normalized.get("position_ratio") or {}
        def ratio_bias(item: Mapping[str, Any], long_key: str, short_key: str) -> dict[str, Any]:
            long_value = _number(item.get(long_key))
            short_value = _number(item.get(short_key))
            total = long_value + short_value
            return {
                "long": long_value,
                "short": short_value,
                "ratio": long_value / short_value if short_value else None,
                "bias": "LONG" if long_value > short_value else "SHORT" if short_value > long_value else "UNKNOWN",
                "coverage": long_value > 0 or short_value > 0,
            }
        normalized["account_bias"] = ratio_bias(accounts, "longAccount", "shortAccount") if accounts else None
        # Binance's public topLongShortPositionRatio payload currently uses
        # longAccount/shortAccount field names even though the endpoint is a
        # position-weight ratio. Normalize those fields once, then keep the
        # account and position concepts separate in the output.
        normalized["position_bias"] = ratio_bias(positions, "longPosition", "shortPosition") if positions else None
        self.top_traders = normalized

    def set_orderbook(self, value: Mapping[str, Any], timestamp: int) -> None:
        self.previous_orderbook = self.orderbook
        self.orderbook = {**value, "timestamp": timestamp}

    def _flow(self, market: str, timeframe_seconds: int) -> dict[str, Any]:
        trades = self.spot_trades if market == "spot" else self.futures_trades
        thresholds = self.spot_thresholds if market == "spot" else self.futures_thresholds
        rows = aggregate_trades(trades, timeframe_seconds)
        # The bucket start is useful for chart alignment, but freshness must
        # be based on the newest raw execution, not a completed 5m/15m
        # bucket's opening timestamp.
        timestamp = max((trade.timestamp for trade in trades), default=None) if rows else None
        status = MetricStatus.DERIVED if rows else MetricStatus.UNAVAILABLE
        meta = _meta(
            f"binance_{market}_aggTrade",
            "Binance official WebSocket/REST aggTrades",
            timestamp,
            status,
            "notional = price * quantity; m=true is aggressive sell; local aggregation",
            coverage=1.0 if rows else 0.0,
            confidence=0.98 if rows else None,
        )
        return {
            "value": rows[-1] if rows else None,
            "series": rows[-240:],
            "trader_size": bucket_flows(trades, thresholds) if thresholds else {},
            "thresholds": thresholds,
            "metadata": meta,
        }

    def snapshot(self, timeframe_seconds: int = 300) -> dict[str, Any]:
        spot = self._flow("spot", timeframe_seconds)
        futures = self._flow("futures", timeframe_seconds)
        spot_value, futures_value = spot["value"], futures["value"]
        divergence = spot_futures_divergence(
            spot_value.get("delta_usd") if spot_value else None,
            futures_value.get("delta_usd") if futures_value else None,
        )
        oi_delta = self.oi.get("delta") if self.oi else None
        liq = self.liquidations or {}
        pos = position_state({
            "price_change": futures_value.get("price_change") if futures_value else None,
            "delta_oi": oi_delta,
            "futures_delta": futures_value.get("delta_usd") if futures_value else None,
            "long_liquidation": liq.get("long_liquidation_usd"),
            "short_liquidation": liq.get("short_liquidation_usd"),
        })
        size = spot.get("trader_size", {})
        whale = whale_behavior({
            "whale_buy_usd": size.get("WHALE_SIZE", {}).get("buy_usd"),
            "whale_sell_usd": size.get("WHALE_SIZE", {}).get("sell_usd"),
            "mega_whale_buy_usd": size.get("MEGA_WHALE_SIZE", {}).get("buy_usd"),
            "mega_whale_sell_usd": size.get("MEGA_WHALE_SIZE", {}).get("sell_usd"),
            "spot_cvd": spot_value.get("cvd") if spot_value else None,
            "price_change": spot_value.get("price_change") if spot_value else None,
        })
        book_metrics = orderbook_metrics(self.orderbook) if self.orderbook else {"status": MetricStatus.UNAVAILABLE.value}
        matrix = self._matrix(spot, futures, divergence, pos, whale, book_metrics)
        return {
            "status": "PASS" if spot_value or futures_value else "UNAVAILABLE",
            "schema_version": "capital-flow-v2",
            "symbol": self.symbol,
            "timeframe_seconds": timeframe_seconds,
            "spot": spot,
            "futures": futures,
            "spot_vs_futures": divergence,
            "position_state": pos,
            "whale_behavior": whale,
            "orderbook": book_metrics,
            "oi": self._oi_payload(),
            "funding": self._funding_payload(),
            "liquidations": self._external_payload(self.liquidations, "Binance Futures forceOrder", "liquidation stream"),
            "top_traders": self._external_payload(self.top_traders, "Binance Futures public ratios", "official top-trader account/position ratios"),
            "capital_flow_matrix": matrix,
            "diagnosis": self._diagnosis(spot, futures, divergence, pos, whale),
            "data_health": self.data_health(),
            "warnings": [
                "Executed trade flow is not exchange deposit/withdrawal flow.",
                "Futures aggressive buy does not prove a new long; OI, funding, and liquidation evidence are required.",
                "Missing data is UNKNOWN/UNAVAILABLE and is never treated as neutral.",
            ],
        }

    def _oi_payload(self) -> dict[str, Any]:
        if not self.oi:
            return {"value": None, "metadata": _meta("binance_futures_open_interest", "Binance official REST", None, MetricStatus.UNAVAILABLE, "GET /fapi/v1/openInterest")}
        return {**self.oi, "metadata": _meta("binance_futures_open_interest", "Binance official REST", self.oi.get("timestamp"), MetricStatus.REAL, "GET /fapi/v1/openInterest; delta is local difference", stale_after_seconds=180)}

    def _funding_payload(self) -> dict[str, Any]:
        if not self.funding:
            return {"value": None, "metadata": _meta("binance_futures_funding", "Binance official REST", None, MetricStatus.UNAVAILABLE, "GET /fapi/v1/premiumIndex or /fapi/v1/fundingRate")}
        return {**self.funding, "metadata": _meta("binance_futures_funding", "Binance official REST", self.funding.get("timestamp"), MetricStatus.REAL, "official funding rate; percentile/z-score require local history", stale_after_seconds=7200)}

    def _external_payload(self, value: Mapping[str, Any] | None, source: str, method: str) -> dict[str, Any]:
        if not value:
            return {"value": None, "metadata": _meta(source, source, None, MetricStatus.UNAVAILABLE, method)}
        threshold = 900 if ("top trader" in source.lower() or "ratio" in source.lower()) else 60
        return {"value": dict(value), "metadata": _meta(source, source, value.get("timestamp"), MetricStatus.REAL, method, stale_after_seconds=threshold)}

    def _matrix(self, spot: Mapping[str, Any], futures: Mapping[str, Any], divergence: Mapping[str, Any], pos: Mapping[str, Any], whale: Mapping[str, Any], book: Mapping[str, Any]) -> list[dict[str, Any]]:
        rows: list[dict[str, Any]] = []
        for source, label, value, direction, metadata in [
            ("Spot executed", "spot_delta_usd", (spot.get("value") or {}).get("delta_usd"), "BUY" if (spot.get("value") or {}).get("delta_usd", 0) > 0 else "SELL", spot.get("metadata")),
            ("Futures executed", "futures_delta_usd", (futures.get("value") or {}).get("delta_usd"), "BUY" if (futures.get("value") or {}).get("delta_usd", 0) > 0 else "SELL", futures.get("metadata")),
            ("Spot vs Futures", "divergence_strength", divergence.get("strength"), divergence.get("state"), {"status": divergence.get("status")}),
            ("Whale-sized", "whale_behavior", whale.get("behavior"), whale.get("behavior"), {"status": whale.get("status")}),
            ("Position state", "state", pos.get("state"), pos.get("state"), {"status": pos.get("status")}),
            ("Orderbook liquidity", "imbalance_10bps", book.get("imbalance_10bps"), "BID" if (book.get("imbalance_10bps") or 0) > 0 else "ASK", {"status": book.get("status")}),
        ]:
            rows.append({"source": source, "metric": label, "value": value, "direction": direction, "strength": None, "confidence": None, "status": metadata.get("status", MetricStatus.UNAVAILABLE.value), "timeframe": "local", "freshness": metadata.get("freshness")})
        return rows

    def _diagnosis(self, spot: Mapping[str, Any], futures: Mapping[str, Any], divergence: Mapping[str, Any], pos: Mapping[str, Any], whale: Mapping[str, Any]) -> dict[str, Any]:
        if not spot.get("value") and not futures.get("value"):
            return {"regime": "NO_CLEAR_EDGE", "status": MetricStatus.UNAVAILABLE.value, "why": ["Spot and futures execution tapes are unavailable"]}
        regime = "MIXED_FLOW"
        sv, fv = spot.get("value"), futures.get("value")
        if sv and fv and sv["delta_usd"] > 0 and fv["delta_usd"] < 0 and (self.oi or {}).get("delta", 0) > 0:
            regime = "SPOT_ACCUMULATION_AGAINST_DERIVATIVE_SHORTS"
        elif sv and fv and sv["delta_usd"] < 0 and fv["delta_usd"] > 0 and (self.oi or {}).get("delta", 0) > 0:
            regime = "SPOT_DISTRIBUTION_AGAINST_LEVERAGED_LONGS"
        elif pos.get("state") in {"LONG_LIQUIDATION", "DELEVERAGING"}:
            regime = "FORCED_DELEVERAGING"
        elif whale.get("behavior") == "ACCUMULATION":
            regime = "WHALE_ACCUMULATION_RETAIL_SELLING"
        elif sv and fv and sv["delta_usd"] > 0 and fv["delta_usd"] > 0:
            regime = "BROAD_ACCUMULATION"
        elif sv and fv and sv["delta_usd"] < 0 and fv["delta_usd"] < 0:
            regime = "BROAD_DISTRIBUTION"
        return {"regime": regime, "status": MetricStatus.DERIVED.value, "why": [
            f"spot={sv['delta_usd'] if sv else 'UNKNOWN'}",
            f"futures={fv['delta_usd'] if fv else 'UNKNOWN'}",
            f"position_state={pos.get('state', 'UNKNOWN')}",
            f"whale_behavior={whale.get('behavior', 'UNKNOWN')}",
        ], "interpretation": "Context only; capital flow does not open trades."}

    def data_health(self) -> list[dict[str, Any]]:
        checks = []
        for name, values, source, threshold in [
            ("Spot aggTrades", self.spot_trades, "Binance Spot aggTrade", 30),
            ("Futures aggTrades", self.futures_trades, "Binance Futures aggTrade", 30),
            ("OI", [self.oi] if self.oi else [], "Binance Futures OI", 180),
            ("Funding", [self.funding] if self.funding else [], "Binance Futures funding", 7200),
            ("Liquidations", [self.liquidations] if self.liquidations else [], "Binance forceOrder", 60),
            ("Top traders", [self.top_traders] if self.top_traders else [], "Binance top trader ratios", 900),
            ("Orderbook", [self.orderbook] if self.orderbook else [], "Binance depth", 30),
        ]:
            last = None
            if values:
                last = max((x.timestamp if isinstance(x, AggTrade) else x.get("timestamp", 0)) for x in values)
            now_ms = int(time.time() * 1000)
            age = (now_ms - last) / 1000 if last else None
            status = MetricStatus.REAL.value if values else MetricStatus.UNAVAILABLE.value
            if age is not None and age > threshold:
                status = MetricStatus.STALE.value
            checks.append({"source": source, "status": status, "last_update": last, "age_seconds": age, "latency": None, "error_count": 0, "coverage": 1.0 if values else 0.0, "confidence": 0.98 if values else None, "metric": name})
        return checks
