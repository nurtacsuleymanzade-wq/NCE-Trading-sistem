from __future__ import annotations

import os
from datetime import datetime, timezone
from functools import lru_cache

from .engine import CapitalFlowEngine
from .storage import CapitalFlowStore, read_heartbeat


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
        funding = store.latest("funding_raw", symbol)
        if funding:
            engine.set_funding(float(funding["funding_rate"]), int(funding["timestamp_ms"]))
        accounts = store.latest("top_trader_accounts_raw", symbol)
        positions = store.latest("top_trader_positions_raw", symbol)
        if accounts or positions:
            engine.set_top_traders({"account_ratio": accounts.get("payload") if accounts else None, "position_ratio": positions.get("payload") if positions else None}, max((x.get("timestamp_ms", 0) for x in (accounts, positions) if x), default=0))
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
    database = db_path or os.environ.get("NCE_CAPITAL_FLOW_DB", "data/capital_flow.sqlite3")
    heartbeat_path = os.environ.get("NCE_CAPITAL_FLOW_HEARTBEAT", "data/capital_flow_heartbeat.json")

    def snapshot(tf: str, symbol: str):
        if tf not in TF_SECONDS:
            return {"status": "UNRELIABLE", "time_utc": _now(), "warning": f"unsupported timeframe: {tf}", "allowed": sorted(TF_SECONDS)}
        engine = load_engine(database, symbol.upper(), tf)
        result = engine.snapshot(TF_SECONDS[tf])
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
        return {"status": "UNAVAILABLE", "time_utc": _now(), "source": "GraphSense/verified labels not configured", "timestamp": None, "freshness": "UNKNOWN", "confidence": None, "metadata": {"status": "UNAVAILABLE", "methodology": "unknown and internal transfers are excluded"}}

    @router.get("/capital-flow/institutional")
    def institutional():
        return {"status": "UNAVAILABLE", "time_utc": _now(), "source": "SEC/issuer ETF adapter not configured", "timestamp": None, "freshness": "UNKNOWN", "confidence": None, "metadata": {"status": "UNAVAILABLE", "methodology": "holdings and cash flow are not conflated"}}

    @router.get("/capital-flow/smart-money")
    def smart_money():
        return {"status": "UNAVAILABLE", "time_utc": _now(), "source": "Binance Web3 Skills not installed", "timestamp": None, "freshness": "UNKNOWN", "confidence": None, "metadata": {"status": "UNAVAILABLE", "methodology": "chain-specific smart-money output is not BTC spot flow"}}

    @router.get("/capital-flow/data-health")
    def data_health(symbol: str = Query("BTCUSDT")):
        store = CapitalFlowStore(database)
        try:
            heartbeat = read_heartbeat(heartbeat_path)
            return {"status": "PASS", "time_utc": _now(), "source": "Capital Flow source health aggregation", "timestamp": heartbeat.get("timestamp_ms"), "freshness": heartbeat.get("status", "UNKNOWN"), "confidence": None, "storage": store.health(), "sources": load_engine(database, symbol.upper(), "5m").data_health(), "collector_health": heartbeat}
        finally:
            store.close()

    return router
