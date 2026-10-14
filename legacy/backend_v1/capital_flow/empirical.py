"""Historical Capital Flow reconstruction and empirical validation.

This module is intentionally an orchestration layer around the production
``CapitalFlowEngine``. It does not define a second flow formula: replay builds
the same engine, supplies an event-time prefix/window, and calls
``CapitalFlowEngine.snapshot``. Future observations are used only by the label
writer.
"""
from __future__ import annotations

import csv
import datetime as dt
import json
import math
import sqlite3
import statistics
import zipfile
from array import array
from bisect import bisect_right
from collections import defaultdict, deque
from itertools import chain
from pathlib import Path
from typing import Any, Iterable, Iterator, Mapping, Sequence

from .engine import AggTrade, CapitalFlowEngine, MetricStatus, trader_size_thresholds
from .storage import CapitalFlowStore


HORIZONS = (60, 300, 900, 1800, 3600, 14400, 86400)
REGIME_DIRECTION = {
    "WHALE_ACCUMULATION_RETAIL_SELLING": 1,
    "SPOT_ACCUMULATION_AGAINST_DERIVATIVE_SHORTS": 1,
    "LEVERAGED_SHORT_BUILDUP": 1,
    "SHORT_SQUEEZE_PRESSURE": 1,
    "INSTITUTIONAL_ACCUMULATION": 1,
    "BROAD_ACCUMULATION": 1,
    "WHALE_DISTRIBUTION_RETAIL_BUYING": -1,
    "SPOT_DISTRIBUTION_AGAINST_LEVERAGED_LONGS": -1,
    "LEVERAGED_LONG_BUILDUP": -1,
    "FORCED_DELEVERAGING": -1,
    "LONG_SQUEEZE_PRESSURE": -1,
    "BROAD_DISTRIBUTION": -1,
}


def available_at(record: Mapping[str, Any], timestamp_ms: int) -> bool:
    """Return whether an external record was published by event time."""
    published = record.get("published_at_ms", record.get("publication_timestamp_ms", record.get("timestamp_ms")))
    try:
        return published is not None and int(published) <= int(timestamp_ms)
    except (TypeError, ValueError):
        return False


def past_only(records: Iterable[Mapping[str, Any]], timestamp_ms: int) -> list[Mapping[str, Any]]:
    return [record for record in records if available_at(record, timestamp_ms)]


def utc_now() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat()


def atomic_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(value, indent=2, sort_keys=True, default=str) + "\n")
    temporary.replace(path)


def ensure_layout(root: Path) -> dict[str, Path]:
    paths = {name: root / name for name in ("raw", "normalized", "features", "events", "labels", "calibration")}
    for path in paths.values():
        path.mkdir(parents=True, exist_ok=True)
    return paths


def _bool(value: Any) -> bool:
    return str(value).strip().lower() in {"true", "1", "yes"}


def _float(value: Any) -> float | None:
    try:
        number = float(value)
        return number if math.isfinite(number) else None
    except (TypeError, ValueError):
        return None


def _date_from_path(path: Path) -> str | None:
    for part in path.name.split("-"):
        pass
    text = path.name
    for index in range(len(text) - 9):
        candidate = text[index:index + 10]
        try:
            dt.date.fromisoformat(candidate)
            return candidate
        except ValueError:
            continue
    return None


def iter_aggtrade_archive(path: Path, market: str) -> Iterator[AggTrade]:
    with zipfile.ZipFile(path) as archive:
        names = [name for name in archive.namelist() if name.lower().endswith(".csv")]
        if not names:
            return
        with archive.open(names[0], "r") as binary:
            lines = (line.decode("utf-8").rstrip("\n") for line in binary)
            first = next(lines, "")
            has_header = first.lower().startswith("agg_trade_id")
            stream = chain([first], lines)
            reader = csv.DictReader(stream) if has_header else None
            if reader is not None:
                rows = reader
            else:
                rows = ({"agg_trade_id": values[0], "price": values[1], "quantity": values[2], "transact_time": values[5], "is_buyer_maker": values[6]} for values in csv.reader(chain([first], lines)))
            for row in rows:
                timestamp = int(float(row.get("transact_time") or row.get("T") or 0))
                if timestamp > 10_000_000_000_000:
                    timestamp //= 1000
                price = _float(row.get("price"))
                quantity = _float(row.get("quantity"))
                if not timestamp or not price or not quantity:
                    continue
                maker = _bool(row.get("is_buyer_maker"))
                yield AggTrade(market, "BTCUSDT", timestamp, int(row.get("agg_trade_id") or 0), price, quantity, price * quantity, maker, "SELL" if maker else "BUY")


def iter_kline_archive(path: Path) -> Iterator[dict[str, Any]]:
    with zipfile.ZipFile(path) as archive:
        names = [name for name in archive.namelist() if name.lower().endswith(".csv")]
        if not names:
            return
        with archive.open(names[0], "r") as binary:
            reader = csv.reader((line.decode("utf-8") for line in binary))
            for row in reader:
                if len(row) < 7 or not str(row[0]).isdigit():
                    continue
                yield {"timestamp_ms": int(row[0]), "open": _float(row[1]), "high": _float(row[2]), "low": _float(row[3]), "close": _float(row[4]), "close_time_ms": int(row[6])}


def iter_metrics_archive(path: Path) -> Iterator[dict[str, Any]]:
    with zipfile.ZipFile(path) as archive:
        names = [name for name in archive.namelist() if name.lower().endswith(".csv")]
        if not names:
            return
        with archive.open(names[0], "r") as binary:
            reader = csv.DictReader((line.decode("utf-8") for line in binary))
            for row in reader:
                stamp = row.get("create_time")
                try:
                    timestamp_ms = int(dt.datetime.strptime(str(stamp), "%Y-%m-%d %H:%M:%S").replace(tzinfo=dt.timezone.utc).timestamp() * 1000)
                except (TypeError, ValueError):
                    continue
                yield {"timestamp_ms": timestamp_ms, "open_interest": _float(row.get("sum_open_interest")), "global_ls": _float(row.get("count_long_short_ratio")), "top_trader_ls": _float(row.get("count_toptrader_long_short_ratio")), "payload": dict(row)}


def _archive_files(root: Path, market: str, dataset: str, start: str, end: str) -> list[Path]:
    output = []
    cursor = dt.date.fromisoformat(start)
    last = dt.date.fromisoformat(end)
    while cursor <= last:
        path = root / market / dataset / f"BTCUSDT-{dataset}-{cursor.isoformat()}.zip"
        if path.exists():
            output.append(path)
        cursor += dt.timedelta(days=1)
    return output


def load_archive_day(root: Path, date: str) -> tuple[list[AggTrade], list[AggTrade], list[dict[str, Any]], list[dict[str, Any]]]:
    spot_files = _archive_files(root, "spot", "aggTrades", date, date)
    future_files = _archive_files(root, "futures", "aggTrades", date, date)
    kline_files = _archive_files(root, "futures", "klines_1m", date, date)
    metric_files = _archive_files(root, "futures", "metrics", date, date)
    spot = sorted((trade for path in spot_files for trade in iter_aggtrade_archive(path, "spot")), key=lambda x: (x.timestamp, x.aggregate_trade_id))
    futures = sorted((trade for path in future_files for trade in iter_aggtrade_archive(path, "futures")), key=lambda x: (x.timestamp, x.aggregate_trade_id))
    prices = sorted((row for path in kline_files for row in iter_kline_archive(path)), key=lambda x: x["timestamp_ms"])
    metrics = sorted((row for path in metric_files for row in iter_metrics_archive(path)), key=lambda x: x["timestamp_ms"])
    return spot, futures, prices, metrics


def load_local_day(db_path: str, date: str) -> tuple[list[AggTrade], list[AggTrade], list[dict[str, Any]], list[dict[str, Any]]]:
    start = int(dt.datetime.fromisoformat(date).replace(tzinfo=dt.timezone.utc).timestamp() * 1000)
    end = start + 86400000
    uri = f"file:{Path(db_path).resolve()}?mode=ro"
    conn = sqlite3.connect(uri, uri=True)
    conn.row_factory = sqlite3.Row
    try:
        def trades(table: str, market: str) -> list[AggTrade]:
            rows = conn.execute(f"SELECT * FROM {table} WHERE symbol = ? AND timestamp_ms >= ? AND timestamp_ms < ? ORDER BY timestamp_ms, aggregate_trade_id", ("BTCUSDT", start, end)).fetchall()
            return [AggTrade(market, row["symbol"], row["timestamp_ms"], row["aggregate_trade_id"], row["price"], row["quantity_btc"], row["notional_usd"], bool(row["buyer_is_maker"]), row["aggressor_side"]) for row in rows]
        spot = trades("spot_aggtrades_raw", "spot")
        futures = trades("futures_aggtrades_raw", "futures")
        rows = conn.execute("SELECT timestamp_ms, open_interest, payload_json FROM oi_raw WHERE symbol = ? AND timestamp_ms >= ? AND timestamp_ms < ? ORDER BY timestamp_ms", ("BTCUSDT", start, end)).fetchall()
        metrics = [{"timestamp_ms": int(row[0]), "open_interest": float(row[1]) if row[1] is not None else None, "payload": json.loads(row[2])} for row in rows]
        prices = [{"timestamp_ms": x.timestamp // 60000 * 60000, "open": x.price, "high": x.price, "low": x.price, "close": x.price} for x in futures]
        return spot, futures, prices, metrics
    finally:
        conn.close()


def inventory(root: Path, manifest_path: Path, db_path: str | None = None) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    if manifest_path.exists():
        manifest = json.loads(manifest_path.read_text())
        for item in manifest.get("datasets", []):
            rows.append({"source": "Binance Data Vision", "dataset": item.get("dataset"), "symbol": item.get("symbol", "BTCUSDT"), "market": item.get("market"), "earliest_date": item.get("date"), "latest_date": item.get("date"), "resolution": "raw archive", "raw_or_derived": "RAW", "size_bytes": item.get("size_bytes"), "completeness": item.get("status"), "gaps": None, "reliability": "HIGH" if item.get("status") == "DOWNLOADED" else "UNAVAILABLE", "usable_for_backtest": item.get("status") == "DOWNLOADED", "path": item.get("path"), "checksum": item.get("sha256")})
    if db_path:
        uri = f"file:{Path(db_path).resolve()}?mode=ro"
        conn = sqlite3.connect(uri, uri=True)
        try:
            for market, table in (("spot", "spot_aggtrades_raw"), ("futures", "futures_aggtrades_raw"), ("futures", "oi_raw"), ("futures", "funding_raw"), ("futures", "orderbook_raw"), ("futures", "liquidations_raw"), ("futures", "global_ls_raw"), ("futures", "top_trader_accounts_raw"), ("futures", "top_trader_positions_raw")):
                row = conn.execute(f"SELECT COUNT(*), MIN(timestamp_ms), MAX(timestamp_ms) FROM {table}").fetchone()
                rows.append({"source": "local production SQLite snapshot", "dataset": table, "symbol": "BTCUSDT", "market": market, "earliest_date": dt.datetime.fromtimestamp(row[1] / 1000, dt.timezone.utc).isoformat() if row[1] else None, "latest_date": dt.datetime.fromtimestamp(row[2] / 1000, dt.timezone.utc).isoformat() if row[2] else None, "resolution": "event/native", "raw_or_derived": "RAW", "size_bytes": Path(db_path).stat().st_size, "completeness": "PARTIAL" if row[0] else "EMPTY", "gaps": None, "reliability": "HIGH" if row[0] else "UNAVAILABLE", "usable_for_backtest": bool(row[0]), "row_count": row[0], "path": db_path})
        finally:
            conn.close()
    return rows


def _direction(regime: str) -> int:
    return REGIME_DIRECTION.get(regime, 0)


def _latest(rows: Sequence[Mapping[str, Any]], timestamp_ms: int) -> Mapping[str, Any] | None:
    if not rows:
        return None
    stamps = [int(row.get("timestamp_ms", 0)) for row in rows]
    index = bisect_right(stamps, timestamp_ms) - 1
    return rows[index] if index >= 0 else None


def _price_at(rows: Sequence[Mapping[str, Any]], timestamp_ms: int) -> Mapping[str, Any] | None:
    return _latest(rows, timestamp_ms)


def _thresholds(values: Sequence[float], market: str) -> dict[str, Any]:
    # Exact percentile input is bounded only for memory safety. The bound is
    # deterministic and remains strictly past-only; it is recorded in output.
    sample = values[-200_000:]
    result = trader_size_thresholds(sample, market)
    result["past_only_history_cap"] = 200_000
    return result


def _engine_at(spot_window: list[AggTrade], futures_window: list[AggTrade], spot_history: Sequence[float], futures_history: Sequence[float], metrics: Sequence[Mapping[str, Any]], timestamp_ms: int, timeframe_seconds: int, spot_thresholds: Mapping[str, Any] | None = None, futures_thresholds: Mapping[str, Any] | None = None) -> dict[str, Any]:
    engine = CapitalFlowEngine()
    engine.spot_trades = spot_window
    engine.futures_trades = futures_window
    engine.spot_thresholds = dict(spot_thresholds or _thresholds(spot_history, "spot"))
    engine.futures_thresholds = dict(futures_thresholds or _thresholds(futures_history, "futures"))
    metric = _latest(metrics, timestamp_ms)
    if metric and metric.get("open_interest") is not None:
        previous = None
        prior = [row for row in metrics if int(row.get("timestamp_ms", 0)) < int(metric.get("timestamp_ms", 0)) and row.get("open_interest") is not None]
        if prior:
            previous = prior[-1].get("open_interest")
        engine.set_oi(float(metric["open_interest"]), int(metric["timestamp_ms"]), float(previous) if previous is not None else None)
        engine.set_oi_history(metrics)
    return engine.snapshot(timeframe_seconds)


def _compact_event(snapshot: Mapping[str, Any], timestamp_ms: int, price: float | None, source: str) -> dict[str, Any]:
    diagnosis = snapshot.get("diagnosis") or {}
    spot = (snapshot.get("spot") or {}).get("value") or {}
    futures = (snapshot.get("futures") or {}).get("value") or {}
    retail = snapshot.get("retail") or {}
    whale = snapshot.get("whale_behavior") or {}
    pos = snapshot.get("position_state") or {}
    oi = snapshot.get("oi") or {}
    divergence = snapshot.get("spot_vs_futures") or {}
    book = snapshot.get("orderbook") or {}
    score = whale.get("state_score") if whale.get("state_score") is not None else pos.get("state_score")
    event = {
        "timestamp_ms": timestamp_ms, "price": price, "source": source,
        "regime": diagnosis.get("regime", "NO_CLEAR_EDGE"), "strength": diagnosis.get("strength"), "confidence": pos.get("confidence"),
        "engine_score": score, "direction": _direction(diagnosis.get("regime", "")),
        "spot_delta": spot.get("delta_usd"), "spot_cvd": spot.get("cvd_total", spot.get("cvd")), "spot_cvd_slope": spot.get("cvd_slope_1m"),
        "futures_delta": futures.get("delta_usd"), "futures_cvd": futures.get("cvd_total", futures.get("cvd")), "futures_cvd_slope": futures.get("cvd_slope_1m"),
        "small_net": (snapshot.get("spot") or {}).get("trader_size", {}).get("SMALL", {}).get("net_flow"),
        "medium_net": (snapshot.get("spot") or {}).get("trader_size", {}).get("MEDIUM", {}).get("net_flow"),
        "retail_net": retail.get("net_flow"), "whale_net": (whale.get("inputs") or {}).get("whale_buy_usd", 0) - (whale.get("inputs") or {}).get("whale_sell_usd", 0),
        "whale_buy_efficiency": (snapshot.get("spot") or {}).get("trader_size", {}).get("WHALE_SIZE", {}).get("buy_efficiency"),
        "whale_sell_efficiency": (snapshot.get("spot") or {}).get("trader_size", {}).get("WHALE_SIZE", {}).get("sell_efficiency"),
        "oi": oi.get("value"), "delta_oi": oi.get("delta"), "funding": (snapshot.get("funding") or {}).get("rate"), "global_ls": (snapshot.get("global_ls") or {}).get("value"),
        "position_state": pos.get("state"), "divergence": divergence.get("state"), "orderbook_imbalance": book.get("imbalance_10bps"),
        "missing_inputs": list(dict.fromkeys((diagnosis.get("missing") or []) + (pos.get("missing") or []) + (whale.get("missing") or []))),
    }
    return event


def reconstruct_day(spot: Sequence[AggTrade], futures: Sequence[AggTrade], prices: Sequence[Mapping[str, Any]], metrics: Sequence[Mapping[str, Any]], *, date: str, timeframe_seconds: int = 300, source: str = "Binance Data Vision", max_events: int | None = None) -> list[dict[str, Any]]:
    if not spot or not futures:
        return []
    spot = sorted(spot, key=lambda x: (x.timestamp, x.aggregate_trade_id))
    futures = sorted(futures, key=lambda x: (x.timestamp, x.aggregate_trade_id))
    prices = sorted(prices, key=lambda x: int(x.get("timestamp_ms", 0)))
    metrics = sorted(metrics, key=lambda x: int(x.get("timestamp_ms", 0)))
    first = max(min(x.timestamp for x in spot), min(x.timestamp for x in futures))
    last = min(max(x.timestamp for x in spot), max(x.timestamp for x in futures))
    bucket = ((first // 1000) // timeframe_seconds) * timeframe_seconds
    end = ((last // 1000) // timeframe_seconds) * timeframe_seconds + timeframe_seconds
    spot_history = array("d")
    futures_history = array("d")
    spot_index = futures_index = 0
    events: list[dict[str, Any]] = []
    cached_spot_thresholds: Mapping[str, Any] | None = None
    cached_futures_thresholds: Mapping[str, Any] | None = None
    cached_threshold_hour: int | None = None
    while bucket <= end:
        event_ts = bucket * 1000 + timeframe_seconds * 1000 - 1
        while spot_index < len(spot) and spot[spot_index].timestamp <= event_ts:
            spot_index += 1
        while futures_index < len(futures) and futures[futures_index].timestamp <= event_ts:
            futures_index += 1
        spot_window_start = event_ts - timeframe_seconds * 1000
        spot_window = [x for x in spot[:spot_index] if x.timestamp > spot_window_start]
        futures_window = [x for x in futures[:futures_index] if x.timestamp > spot_window_start]
        if spot_window and futures_window:
            threshold_hour = bucket // 3600
            if cached_threshold_hour != threshold_hour:
                prior_spot = [x.notional_usd for x in spot[:spot_index - len(spot_window)] if x.timestamp <= spot_window_start]
                prior_futures = [x.notional_usd for x in futures[:futures_index - len(futures_window)] if x.timestamp <= spot_window_start]
                cached_spot_thresholds = _thresholds(prior_spot, "spot")
                cached_futures_thresholds = _thresholds(prior_futures, "futures")
                cached_threshold_hour = threshold_hour
            else:
                prior_spot = []
                prior_futures = []
            snapshot = _engine_at(spot_window, futures_window, prior_spot, prior_futures, metrics, event_ts, timeframe_seconds, cached_spot_thresholds, cached_futures_thresholds)
            price = _price_at(prices, event_ts)
            value = price.get("close") if price else (futures_window[-1].price if futures_window else None)
            event = _compact_event(snapshot, event_ts, value, source)
            event["raw_trade_counts"] = {"spot": len(spot_window), "futures": len(futures_window), "spot_history": len(prior_spot), "futures_history": len(prior_futures), "threshold_refresh": "hourly_past_only"}
            events.append(event)
            if max_events and len(events) >= max_events:
                break
        bucket += timeframe_seconds
    return events


def _wilson(success: int, total: int) -> tuple[float | None, float | None]:
    if not total:
        return None, None
    z = 1.959963984540054
    p = success / total
    denominator = 1 + z * z / total
    centre = p + z * z / (2 * total)
    spread = z * math.sqrt((p * (1 - p) + z * z / (4 * total)) / total)
    return (centre - spread) / denominator, (centre + spread) / denominator


def add_forward_labels(events: Sequence[Mapping[str, Any]], prices: Sequence[Mapping[str, Any]], horizons: Sequence[int] = HORIZONS) -> list[dict[str, Any]]:
    ordered = sorted(prices, key=lambda x: int(x.get("timestamp_ms", 0)))
    stamps = [int(x.get("timestamp_ms", 0)) for x in ordered]
    output = []
    for original in events:
        event = dict(original)
        event["labels"] = {}
        timestamp = int(event["timestamp_ms"])
        entry_row = _latest(ordered, timestamp)
        entry = entry_row.get("close") if entry_row else event.get("price")
        if entry is None or not entry:
            output.append(event)
            continue
        start = bisect_right(stamps, timestamp)
        direction = int(event.get("direction") or 0)
        for horizon in horizons:
            end = bisect_right(stamps, timestamp + horizon * 1000)
            future = ordered[start:end]
            highs = [x.get("high") or x.get("close") for x in future]
            lows = [x.get("low") or x.get("close") for x in future]
            closes = [x.get("close") for x in future if x.get("close") is not None]
            if not closes:
                event["labels"][str(horizon)] = {"status": "UNAVAILABLE", "future_data_used_for_label_only": True}
                continue
            actual = float(closes[-1]) / float(entry) - 1
            max_up = max(float(x) / float(entry) - 1 for x in highs if x is not None)
            max_down = min(float(x) / float(entry) - 1 for x in lows if x is not None)
            directional = actual * direction if direction else None
            directional_up = max_up * direction if direction else None
            directional_down = max_down * direction if direction else None
            event["labels"][str(horizon)] = {
                "status": "DERIVED", "entry_price": float(entry), "forward_return": actual, "directional_return": directional,
                "max_up_move": max_up, "max_down_move": max_down, "mfe": max(directional_up, 0) if directional_up is not None else None,
                "mae": min(directional_down, 0) if directional_down is not None else None,
                "continuation": directional is not None and directional > 0, "reversal": directional is not None and directional < 0,
                "short_squeeze": max_up >= 0.002 and direction >= 0, "long_squeeze": max_down <= -0.002 and direction <= 0,
                "target_hit": directional is not None and directional >= 0.002, "invalidation_hit": directional is not None and directional <= -0.001,
                "future_data_used_for_label_only": True,
            }
        output.append(event)
    return output


def _values(rows: Sequence[Mapping[str, Any]], horizon: int, field: str) -> list[float]:
    return [float((row.get("labels") or {}).get(str(horizon), {}).get(field)) for row in rows if (row.get("labels") or {}).get(str(horizon), {}).get(field) is not None]


def _summary(values: Sequence[float]) -> dict[str, Any]:
    ordered = sorted(values)
    if not ordered:
        return {"mean": None, "median": None, "std": None, "p10": None, "p90": None}
    return {"mean": statistics.fmean(ordered), "median": statistics.median(ordered), "std": statistics.stdev(ordered) if len(ordered) > 1 else 0.0, "p10": ordered[max(0, int((len(ordered) - 1) * 0.10))], "p90": ordered[min(len(ordered) - 1, int((len(ordered) - 1) * 0.90))]}


def regime_statistics(rows: Sequence[Mapping[str, Any]], min_sample: int = 20) -> dict[str, Any]:
    grouped: dict[str, list[Mapping[str, Any]]] = defaultdict(list)
    for row in rows:
        grouped[str(row.get("regime", "UNKNOWN"))].append(row)
    result: dict[str, Any] = {}
    for regime, group in sorted(grouped.items()):
        horizon_rows: dict[str, Any] = {}
        for horizon in HORIZONS:
            returns = _values(group, horizon, "directional_return")
            mfe = _values(group, horizon, "mfe")
            mae = _values(group, horizon, "mae")
            labels = [(row.get("labels") or {}).get(str(horizon), {}) for row in group]
            resolved = [label for label in labels if label.get("status") == "DERIVED"]
            continuation = [x for x in resolved if x.get("continuation") is not None]
            horizon_rows[str(horizon)] = {"sample_size": len(resolved), "sufficient_sample": len(resolved) >= min_sample, "forward_return": _summary(returns), "positive_return_rate": sum(x > 0 for x in returns) / len(returns) if returns else None, "mfe": _summary(mfe), "mae": _summary(mae), "continuation_rate": sum(bool(x.get("continuation")) for x in continuation) / len(continuation) if continuation else None, "reversal_rate": sum(bool(x.get("reversal")) for x in continuation) / len(continuation) if continuation else None, "short_squeeze_rate": sum(bool(x.get("short_squeeze")) for x in resolved) / len(resolved) if resolved else None, "long_squeeze_rate": sum(bool(x.get("long_squeeze")) for x in resolved) / len(resolved) if resolved else None}
        result[regime] = {"sample_size": len(group), "sufficient_sample": len(group) >= min_sample, "horizons": horizon_rows}
    return {"min_sample": min_sample, "regimes": result, "methodology": "chronological event-time snapshots; directional returns only when the existing engine regime has a directional mapping"}


def conditional_statistics(rows: Sequence[Mapping[str, Any]], min_sample: int = 20) -> dict[str, Any]:
    conditions = {
        "OI_RISING": lambda row: row.get("delta_oi") is not None and float(row.get("delta_oi")) > 0,
        "FUTURES_CVD_DOWN": lambda row: row.get("futures_cvd_slope") is not None and float(row.get("futures_cvd_slope")) < 0,
        "HIGH_WHALE_BUY_EFFICIENCY": lambda row: row.get("whale_buy_efficiency") == "HIGH",
        "ORDERBOOK_BID_ABSORPTION": lambda row: row.get("orderbook_imbalance") is not None and float(row.get("orderbook_imbalance")) > 0.2,
    }
    groups: dict[str, list[Mapping[str, Any]]] = {"BASE": list(rows)}
    for name, predicate in conditions.items():
        groups[name] = [row for row in rows if predicate(row)]
    output = {}
    for name, group in groups.items():
        values = _values(group, 900, "directional_return")
        output[name] = {"sample_size": len(group), "sufficient_sample": len(group) >= min_sample, "15m": {"positive_return_rate": sum(x > 0 for x in values) / len(values) if values else None, "median_mfe": _summary(_values(group, 900, "mfe"))["median"], "median_mae": _summary(_values(group, 900, "mae"))["median"]}, "multiple_testing_note": "Four predeclared context cuts; no post-hoc threshold search; small cells are not promoted to evidence"}
    return {"groups": output, "min_sample": min_sample}


def walk_forward_calibration(rows: Sequence[Mapping[str, Any]], min_bin_sample: int = 20) -> dict[str, Any]:
    ordered = sorted(rows, key=lambda x: int(x.get("timestamp_ms", 0)))
    n = len(ordered)
    train_end, validation_end = int(n * 0.6), int(n * 0.8)
    splits = {"TRAIN": ordered[:train_end], "VALIDATION": ordered[train_end:validation_end], "OUT_OF_SAMPLE": ordered[validation_end:]}
    def target(row: Mapping[str, Any]) -> bool | None:
        label = (row.get("labels") or {}).get("900") or {}
        return label.get("continuation") if label.get("continuation") is not None else None
    train_bins: dict[int, list[bool]] = defaultdict(list)
    for row in splits["TRAIN"]:
        score = row.get("engine_score"); outcome = target(row)
        if score is not None and outcome is not None:
            train_bins[min(9, max(0, int(float(score) // 10)))].append(bool(outcome))
    mapping = {bucket: (sum(values) / len(values) if len(values) >= min_bin_sample else None) for bucket, values in train_bins.items()}
    def evaluate(name: str) -> dict[str, Any]:
        group = splits[name]; resolved = 0; wins = 0; returns = []
        for row in group:
            outcome = target(row)
            if outcome is not None:
                resolved += 1; wins += bool(outcome)
                label = (row.get("labels") or {}).get("900") or {}
                if label.get("directional_return") is not None: returns.append(float(label["directional_return"]))
        return {"sample_size": len(group), "resolved_size": resolved, "continuation_rate": wins / resolved if resolved else None, "median_return": statistics.median(returns) if returns else None}
    reliability = []
    for bucket in range(10):
        group = [row for row in splits["OUT_OF_SAMPLE"] if row.get("engine_score") is not None and min(9, max(0, int(float(row["engine_score"]) // 10))) == bucket and target(row) is not None]
        observed = sum(bool(target(row)) for row in group) / len(group) if group else None
        low, high = _wilson(sum(bool(target(row)) for row in group), len(group)) if group else (None, None)
        reliability.append({"score_bucket": f"{bucket * 10}-{bucket * 10 + 10}", "sample_size": len(group), "train_calibrated_probability": mapping.get(bucket), "observed_frequency_oos": observed, "confidence_interval_95": [low, high] if low is not None else None})
    scored = [(mapping.get(min(9, max(0, int(float(row["engine_score"]) // 10)))), target(row)) for row in splits["OUT_OF_SAMPLE"] if row.get("engine_score") is not None and target(row) is not None and mapping.get(min(9, max(0, int(float(row["engine_score"]) // 10)))) is not None]
    brier = sum((float(prob) - bool(outcome)) ** 2 for prob, outcome in scored) / len(scored) if scored else None
    return {"status": "DERIVED" if ordered else "UNAVAILABLE", "splits": {name: evaluate(name) for name in splits}, "train_bin_mapping": mapping, "reliability_table_oos": reliability, "oos_brier_score_of_train_mapping": brier, "score_is_probability": False, "methodology": "60/20/20 chronological train/validation/out-of-sample; no shuffle; calibration mapping learned on TRAIN only; OOS never used for threshold selection", "oos_used_for_threshold_selection": False}


def empirical_confidence(rows: Sequence[Mapping[str, Any]], validation: Mapping[str, Any]) -> dict[str, Any]:
    n = len(rows); oos = (validation.get("splits") or {}).get("OUT_OF_SAMPLE", {})
    status = "HIGH" if n >= 500 and (oos.get("resolved_size") or 0) >= 100 else "MEDIUM" if n >= 200 and (oos.get("resolved_size") or 0) >= 40 else "LOW" if n >= 50 else "INSUFFICIENT_SAMPLE"
    return {"level": status, "sample_size": n, "oos_sample_size": oos.get("resolved_size", 0), "stability": "NOT_ESTABLISHED" if n < 200 else "CHECK_REQUIRED", "calibration_quality": "NOT_ESTABLISHED" if not validation.get("reliability_table_oos") else "REPORTED_WITH_CI", "methodology": "empirical confidence is separate from data confidence; it is not a probability"}


def build_data_confidence(inventory_rows: Sequence[Mapping[str, Any]], events: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    usable = sum(bool(row.get("usable_for_backtest")) for row in inventory_rows)
    missing = sum(not bool(row.get("usable_for_backtest")) for row in inventory_rows)
    return {"level": "HIGH" if usable and not missing else "MEDIUM" if usable else "LOW", "source_quality": "HIGH", "coverage": usable / len(inventory_rows) if inventory_rows else 0.0, "missing_inputs": missing, "event_count": len(events), "freshness": "HISTORICAL_AS_OF_MANIFEST", "cross_source_agreement": "NOT_ASSESSED"}
