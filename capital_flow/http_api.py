from __future__ import annotations

import os
import json
import statistics
from datetime import datetime, timezone
from functools import lru_cache
from pathlib import Path
from typing import Any

from .engine import CapitalFlowEngine
from .interpretation import build_interpretation
from .storage import CapitalFlowStore, read_heartbeat
from .horizons import build_horizon_states


TF_SECONDS = {"1s": 1, "5s": 5, "15s": 15, "30s": 30, "1m": 60, "3m": 180, "5m": 300, "15m": 900, "30m": 1800, "1h": 3600, "4h": 14400, "24h": 86400}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _metadata(payload: dict[str, Any] | None, default_source: str) -> dict[str, Any]:
    """Return the common top-level data contract for every endpoint."""
    value = payload or {}
    return {
        "source": value.get("source", default_source),
        "timestamp": value.get("timestamp"),
        "freshness": value.get("freshness", "UNKNOWN"),
        "confidence": value.get("confidence"),
        "status": value.get("status", "UNAVAILABLE"),
    }


def _historical_context(path: Path, regime: str | None = None, oi_state: str | None = None, divergence: str | None = None) -> dict[str, Any]:
    if not path.exists():
        return {"status": "UNAVAILABLE", "reason": "historical validation artifact is not installed", "data_confidence": None, "empirical_confidence": None}
    try:
        summary = json.loads(path.read_text())
        events_path = path.parent / "events" / "capital_flow_events.jsonl"
        events = [json.loads(line) for line in events_path.read_text().splitlines() if line.strip()] if events_path.exists() else []
        requested = regime or "NO_CLEAR_EDGE"
        levels = [
            ("A", lambda row: row.get("regime") == requested),
            ("B", lambda row: row.get("regime") == requested and ((row.get("delta_oi") is not None and float(row.get("delta_oi")) > 0) if oi_state in (None, "RISING") else True)),
            ("C", lambda row: row.get("regime") == requested and ((row.get("delta_oi") is not None and float(row.get("delta_oi")) > 0) if oi_state in (None, "RISING") else True) and (row.get("divergence") == divergence if divergence else True)),
            ("D", lambda row: row.get("regime") == requested and ((row.get("delta_oi") is not None and float(row.get("delta_oi")) > 0) if oi_state in (None, "RISING") else True) and (row.get("divergence") == divergence if divergence else True) and (row.get("whale_buy_efficiency") == "HIGH" if divergence else True)),
        ]
        selected_level, selected = "A", []
        for level, predicate in levels:
            candidate = [row for row in events if predicate(row)]
            if len(candidate) >= 20 or level == "A":
                selected_level, selected = level, candidate
                if len(candidate) >= 20:
                    break
        labels = [(row.get("labels") or {}).get("900", {}) for row in selected]
        resolved = [row for row in labels if row.get("status") == "DERIVED" and row.get("directional_return") is not None]
        returns = [float(row["directional_return"]) for row in resolved]
        mfes = [float(row["mfe"]) for row in resolved if row.get("mfe") is not None]
        maes = [float(row["mae"]) for row in resolved if row.get("mae") is not None]
        regime_summary = (summary.get("regime_statistics") or {}).get("regimes", {}).get(requested, {})
        oos = ((summary.get("walk_forward") or {}).get("splits") or {}).get("OUT_OF_SAMPLE", {})
        return {"status": "DERIVED", "requested_regime": requested, "selected_level": selected_level, "sample_size": len(selected), "resolved_sample_size": len(resolved), "positive_return_15m": sum(x > 0 for x in returns) / len(returns) if returns else None, "median_15m_return": statistics.median(returns) if returns else None, "median_mfe": statistics.median(mfes) if mfes else None, "median_mae": statistics.median(maes) if maes else None, "oos_sample_size": oos.get("resolved_size", 0), "empirical_confidence": summary.get("EMPIRICAL_CONFIDENCE"), "data_confidence": summary.get("DATA_CONFIDENCE"), "score_is_probability": False, "regime_summary": regime_summary, "fallback_rule": "broaden from A to D only when the narrower level has fewer than 20 events"}
    except (OSError, ValueError, TypeError, KeyError) as exc:
        return {"status": "UNRELIABLE", "reason": type(exc).__name__}


def load_engine(db_path: str, symbol: str, tf: str) -> CapitalFlowEngine:
    store = CapitalFlowStore(db_path)
    try:
        now_ms = int(datetime.now(timezone.utc).timestamp() * 1000)
        since = now_ms - 30 * 86400 * 1000
        engine = CapitalFlowEngine(symbol=symbol)
        spot = store.trades("spot", symbol, since_ms=since, limit=200000)
        futures = store.trades("futures", symbol, since_ms=since, limit=200000)
        engine.spot_trades.extend(spot)
        engine.futures_trades.extend(futures)
        engine.set_size_history((x.notional_usd for x in spot), (x.notional_usd for x in futures))
        oi = store.latest("oi_raw", symbol)
        if oi:
            previous_rows = store.conn.execute("SELECT open_interest FROM oi_raw WHERE symbol = ? ORDER BY timestamp_ms DESC LIMIT 2", (symbol,)).fetchall()
            previous = previous_rows[1][0] if len(previous_rows) > 1 else None
            engine.set_oi(float(oi["open_interest"]), int(oi["timestamp_ms"]), previous)
            engine.set_oi_history(store.history("oi_raw", symbol, 120))
        funding = store.latest("funding_raw", symbol)
        if funding:
            engine.set_funding(float(funding["funding_rate"]), int(funding["timestamp_ms"]))
            engine.set_funding_history(store.history("funding_raw", symbol, 120))
        accounts = store.latest("top_trader_accounts_raw", symbol)
        positions = store.latest("top_trader_positions_raw", symbol)
        if accounts or positions:
            engine.set_top_traders({"account_ratio": accounts.get("payload") if accounts else None, "position_ratio": positions.get("payload") if positions else None, "accounts_history": store.history("top_trader_accounts_raw", symbol, 200), "positions_history": store.history("top_trader_positions_raw", symbol, 200)}, max((x.get("timestamp_ms", 0) for x in (accounts, positions) if x), default=0))
        global_history = store.history("global_ls_raw", symbol, 200)
        if global_history:
            latest_global = global_history[-1]
            engine.set_global_ls({"period": latest_global.get("period"), "value": latest_global.get("payload"), "history": global_history}, int(latest_global.get("timestamp_ms", 0)))
        liquidations = store.liquidation_window(symbol, since_ms=now_ms - 5 * 60 * 1000)
        if any(liquidations.values()):
            engine.set_liquidations(liquidations, now_ms)
        orderbook = store.latest("orderbook_raw", symbol)
        if orderbook and orderbook.get("payload"):
            engine.set_orderbook(orderbook["payload"], int(orderbook["timestamp_ms"]))
        return engine
    finally:
        store.close()


def create_router(db_path: str | None = None):
    try:
        from fastapi import APIRouter, Query
    except ImportError as exc:
        raise RuntimeError("FastAPI is required to expose the Capital Flow API") from exc
    router = APIRouter()
    database = db_path or os.environ.get("NCE_CAPITAL_FLOW_DB", "/var/lib/nce-trading/capital_flow.sqlite3")
    heartbeat_path = os.environ.get("NCE_CAPITAL_FLOW_HEARTBEAT", "/var/lib/nce-trading/capital_flow_heartbeat.json")
    context_path = Path(os.environ.get("NCE_CAPITAL_FLOW_CONTEXT_STATE", "/var/lib/nce-trading/capital_flow_context.json"))
    historical_path = Path(os.environ.get("NCE_CAPITAL_FLOW_HISTORICAL_SUMMARY", "/var/lib/nce-trading/capital_flow/historical/validation_summary.json"))

    def external_context() -> dict[str, Any]:
        if not context_path.exists():
            return {"status": "UNAVAILABLE", "reason": "external context collector state file missing", "network_context": {"status": "UNAVAILABLE"}, "smart_money": {"status": "UNAVAILABLE", "btc_status": "UNAVAILABLE"}, "institutional": {"status": "UNAVAILABLE"}, "exchange": {"status": "UNAVAILABLE"}}
        try:
            value = json.loads(context_path.read_text())
            age = (int(datetime.now(timezone.utc).timestamp() * 1000) - int(value.get("timestamp", 0))) / 1000
            value["age_seconds"] = age
            value["freshness"] = "FRESH" if age <= 1800 else "STALE"
            if value["freshness"] == "STALE":
                for key in ("network_context", "smart_money", "institutional", "exchange"):
                    if isinstance(value.get(key), dict) and value[key].get("status") == "REAL": value[key]["status"] = "STALE"
            return value
        except (OSError, ValueError, TypeError):
            return {"status": "UNRELIABLE", "reason": "external context state is invalid"}

    def snapshot(tf: str, symbol: str):
        if tf not in TF_SECONDS:
            return {"status": "UNRELIABLE", "time_utc": _now(), "warning": f"unsupported timeframe: {tf}", "allowed": sorted(TF_SECONDS)}
        engine = load_engine(database, symbol.upper(), tf)
        result = engine.snapshot(TF_SECONDS[tf])
        external = external_context()
        result["time_utc"] = _now()
        result["request"] = {"symbol": symbol.upper(), "timeframe": tf}
        result["collector_health"] = read_heartbeat(heartbeat_path)
        result["metadata"] = {
            "source": "Capital Flow Intelligence Engine",
            "timestamp": result["collector_health"].get("timestamp_ms"),
            "freshness": result["collector_health"].get("status", "UNKNOWN"),
            "confidence": None,
            "status": result.get("status", "UNAVAILABLE"),
        }
        result.update({
            "source": result["metadata"]["source"],
            "timestamp": result["metadata"]["timestamp"],
            "freshness": result["metadata"]["freshness"],
            "confidence": result["metadata"]["confidence"],
        })
        result["external_context"] = external
        result["horizons"] = build_horizon_states(result, external)
        result["micro_flow"] = result["horizons"]["states"].get("MICRO_FLOW")
        result["intraday_flow"] = result["horizons"]["states"].get("INTRADAY_FLOW")
        result["onchain_flow"] = result["horizons"]["states"].get("ONCHAIN_FLOW")
        result["institutional_flow"] = result["horizons"]["states"].get("INSTITUTIONAL_FLOW")
        result["network_context"] = result["horizons"]["states"].get("NETWORK_CONTEXT")
        interpretation = build_interpretation(result, external)
        result.update({"summary": interpretation["summary"], "states": interpretation["states"], "interpretation": interpretation, "why": interpretation["why"], "against": interpretation["against"], "missing": interpretation["missing"], "conflicts": interpretation["conflicts"], "metadata_contract": interpretation["metadata"], "phase_status": interpretation["phaseStatus"]})
        result["historical_context"] = _historical_context(historical_path, interpretation["summary"].get("capitalRegime"), "RISING" if ((result.get("oi") or {}).get("delta") or 0) > 0 else None, (result.get("spot_vs_futures") or {}).get("state"))
        return result

    @router.get("/capital-flow/summary")
    def summary(tf: str = Query("5m"), symbol: str = Query("BTCUSDT")):
        return snapshot(tf, symbol)

    @router.get("/capital-flow/spot")
    def spot(tf: str = Query("5m"), symbol: str = Query("BTCUSDT")):
        data = snapshot(tf, symbol)
        meta = data["spot"].get("metadata", {})
        return {"status": data["status"], "time_utc": data["time_utc"], "symbol": data["symbol"], "source": meta.get("source", "Binance Spot aggTrade"), "timestamp": meta.get("timestamp"), "freshness": meta.get("freshness", "UNKNOWN"), "confidence": meta.get("confidence"), "spot": data["spot"], "metadata": meta}

    @router.get("/capital-flow/futures")
    def futures(tf: str = Query("5m"), symbol: str = Query("BTCUSDT")):
        data = snapshot(tf, symbol)
        meta = data["futures"].get("metadata", {})
        return {"status": data["status"], "time_utc": data["time_utc"], "symbol": data["symbol"], "source": meta.get("source", "Binance Futures aggTrade"), "timestamp": meta.get("timestamp"), "freshness": meta.get("freshness", "UNKNOWN"), "confidence": meta.get("confidence"), "futures": data["futures"], "metadata": meta}

    @router.get("/capital-flow/trader-size")
    def trader_size(tf: str = Query("5m"), symbol: str = Query("BTCUSDT")):
        data = snapshot(tf, symbol)
        spot_meta = data["spot"].get("metadata", {})
        futures_meta = data["futures"].get("metadata", {})
        return {"status": data["status"], "time_utc": data["time_utc"], "symbol": data["symbol"], "source": "Binance Spot/Futures aggTrade", "timestamp": max((spot_meta.get("timestamp") or 0), (futures_meta.get("timestamp") or 0)) or None, "freshness": {"spot": spot_meta.get("freshness", "UNKNOWN"), "futures": futures_meta.get("freshness", "UNKNOWN")}, "confidence": {"spot": spot_meta.get("confidence"), "futures": futures_meta.get("confidence")}, "spot": data["spot"].get("trader_size"), "futures": data["futures"].get("trader_size"), "spot_thresholds": data["spot"].get("thresholds"), "futures_thresholds": data["futures"].get("thresholds")}

    @router.get("/capital-flow/top-traders")
    def top_traders(tf: str = Query("5m"), symbol: str = Query("BTCUSDT")):
        data = snapshot(tf, symbol)
        result = data["top_traders"]
        meta = result.get("metadata", {})
        return {"status": meta.get("status", "UNAVAILABLE"), "time_utc": data["time_utc"], "symbol": data["symbol"], "source": meta.get("source", "Binance Futures public ratios"), "timestamp": meta.get("timestamp"), "freshness": meta.get("freshness", "UNKNOWN"), "confidence": meta.get("confidence"), "value": result.get("value"), "metadata": meta}

    @router.get("/capital-flow/orderbook")
    def orderbook(tf: str = Query("5m"), symbol: str = Query("BTCUSDT")):
        data = snapshot(tf, symbol)
        result = data["orderbook"]
        health = data.get("collector_health", {})
        return {**result, "time_utc": data["time_utc"], "symbol": data["symbol"], "source": "Binance Spot depth", "timestamp": health.get("timestamp_ms"), "freshness": health.get("status", "UNKNOWN"), "confidence": 0.98 if result.get("status") == "DERIVED" else None}

    @router.get("/capital-flow/exchange")
    def exchange():
        value = external_context().get("exchange") or {"status": "UNAVAILABLE"}
        return {**value, "time_utc": _now(), "source": value.get("source", "GraphSense/verified labels"), "timestamp": value.get("timestamp"), "freshness": value.get("freshness", "UNKNOWN"), "confidence": value.get("confidence"), "metadata": value}

    @router.get("/capital-flow/institutional")
    def institutional():
        value = external_context().get("institutional") or {"status": "UNAVAILABLE"}
        return {**value, "time_utc": _now(), "source": value.get("source", "SEC/issuer ETF holdings"), "timestamp": value.get("timestamp"), "freshness": external_context().get("freshness", "UNKNOWN"), "confidence": value.get("confidence"), "metadata": value}

    @router.get("/capital-flow/smart-money")
    def smart_money():
        value = external_context().get("smart_money") or {"status": "UNAVAILABLE", "btc_status": "UNAVAILABLE"}
        return {**value, "time_utc": _now(), "source": value.get("source", "Binance Skills Hub crypto-market-rank"), "timestamp": value.get("timestamp"), "freshness": external_context().get("freshness", "UNKNOWN"), "confidence": value.get("confidence"), "metadata": {**value, "btc_status": "UNAVAILABLE", "methodology": "chain-specific Web3 smart money is not BTC spot flow"}}

    @router.get("/capital-flow/network-context")
    def network_context():
        value = external_context().get("network_context") or {"status": "UNAVAILABLE"}
        return {**value, "time_utc": _now(), "source": value.get("source", "Coin Metrics Community"), "timestamp": value.get("timestamp"), "freshness": external_context().get("freshness", "UNKNOWN"), "confidence": value.get("confidence"), "metadata": value}

    @router.get("/capital-flow/data-health")
    def data_health(symbol: str = Query("BTCUSDT")):
        store = CapitalFlowStore(database)
        try:
            heartbeat = read_heartbeat(heartbeat_path)
            context = external_context()
            return {"status": "PASS", "time_utc": _now(), "source": "Capital Flow source health aggregation", "timestamp": heartbeat.get("timestamp_ms"), "freshness": heartbeat.get("status", "UNKNOWN"), "confidence": None, "storage": store.health(), "sources": load_engine(database, symbol.upper(), "5m").data_health(), "external_context": {"status": context.get("status"), "freshness": context.get("freshness"), "age_seconds": context.get("age_seconds"), "errors": context.get("errors", [])}, "collector_health": heartbeat}
        finally:
            store.close()

    @router.get("/capital-flow/historical-context")
    def historical_context(regime: str = Query("NO_CLEAR_EDGE"), oi_state: str | None = Query(None), divergence: str | None = Query(None)):
        return _historical_context(historical_path, regime, oi_state, divergence)

    return router
