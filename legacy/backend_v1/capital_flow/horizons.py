"""Time-horizon separation and interpretation layer.

External context is deliberately kept out of the Phase 1 microstructure score.
Each state carries its own status, timeframe, freshness and confidence.
"""
from __future__ import annotations

from typing import Any, Mapping


def _state(name: str, value: str, timeframe: str, status: str, *, strength: float | None = None,
           confidence: float | None = None, source: str = "NCE Capital Flow") -> dict[str, Any]:
    return {"name": name, "state": value, "strength": strength, "confidence": confidence,
            "timeframe": timeframe, "status": status, "source": source}


def _sign(value: Any) -> int:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return 0
    return 1 if number > 0 else -1 if number < 0 else 0


def build_horizon_states(snapshot: Mapping[str, Any], external: Mapping[str, Any] | None = None) -> dict[str, Any]:
    external = external or {}
    spot = snapshot.get("spot", {}).get("value") or {}
    futures = snapshot.get("futures", {}).get("value") or {}
    orderbook = snapshot.get("orderbook") or {}
    pos = snapshot.get("position_state") or {}
    divergence = snapshot.get("spot_vs_futures") or {}
    oi = snapshot.get("oi") or {}
    liquidation = snapshot.get("liquidations") or {}
    spot_sign, futures_sign = _sign(spot.get("delta_usd")), _sign(futures.get("delta_usd"))
    micro_state = "BULLISH" if spot_sign > 0 and futures_sign > 0 else "BEARISH" if spot_sign < 0 and futures_sign < 0 else "MIXED_FLOW"
    micro_status = "DERIVED" if spot and futures else "UNAVAILABLE"
    micro_conf = 90.0 if micro_status == "DERIVED" else None
    intraday_value = pos.get("state") if pos.get("status") == "DERIVED" else "UNKNOWN"
    intraday_state = intraday_value if intraday_value != "UNKNOWN" else ("BULLISH" if futures_sign > 0 else "BEARISH" if futures_sign < 0 else "UNKNOWN")
    intraday_status = "DERIVED" if pos.get("status") == "DERIVED" else "UNAVAILABLE"
    exchange = external.get("exchange") or {}
    smart = external.get("smart_money") or {}
    exchange_status = exchange.get("status", "UNAVAILABLE")
    onchain_state = exchange.get("state", "UNKNOWN") if exchange_status not in ("UNAVAILABLE", "STALE") else "UNKNOWN"
    inst = external.get("institutional") or {}
    inst_status = inst.get("status", "UNAVAILABLE")
    inst_state = inst.get("state", "UNKNOWN") if inst_status not in ("UNAVAILABLE", "STALE") else "UNKNOWN"
    network = external.get("network_context") or {}
    network_status = network.get("status", "UNAVAILABLE")
    network_state = network.get("state", "UNKNOWN") if network_status not in ("UNAVAILABLE", "STALE") else "UNKNOWN"
    states = {
        "MICRO_FLOW": _state("MICRO_FLOW", micro_state, "1s-1m", micro_status, strength=None, confidence=micro_conf),
        "INTRADAY_FLOW": _state("INTRADAY_FLOW", intraday_state, "5m-4h", intraday_status, strength=None, confidence=75.0 if intraday_status == "DERIVED" else None),
        "ONCHAIN_FLOW": _state("ONCHAIN_FLOW", onchain_state, "1h-24h", exchange_status, strength=exchange.get("strength"), confidence=exchange.get("confidence"), source=exchange.get("source", "GraphSense")),
        "INSTITUTIONAL_FLOW": _state("INSTITUTIONAL_FLOW", inst_state, "1d-20d", inst_status, strength=inst.get("strength"), confidence=inst.get("confidence"), source=inst.get("source", "SEC/issuer ETF holdings")),
        "NETWORK_CONTEXT": _state("NETWORK_CONTEXT", network_state, "1d+", network_status, strength=network.get("strength"), confidence=network.get("confidence"), source=network.get("source", "Coin Metrics Community")),
    }
    why: list[str] = []
    against: list[str] = []
    if spot_sign > 0: why.append("Spot executed delta positive")
    if spot_sign < 0: against.append("Spot executed delta negative")
    if futures_sign > 0: why.append("Futures executed delta positive")
    if futures_sign < 0: against.append("Futures executed delta negative")
    if (oi.get("delta") or 0) > 0: why.append("Open interest increased")
    if orderbook.get("status") == "DERIVED" and (orderbook.get("imbalance_10bps") or 0) < 0: against.append("10bps orderbook is ask-heavy")
    if exchange_status in ("UNAVAILABLE", "STALE"): against.append("Exchange flow is unavailable or stale")
    if inst_status in ("UNAVAILABLE", "STALE"): against.append("Institutional holdings context is unavailable or stale")
    regime = snapshot.get("diagnosis", {}).get("regime", "NO_CLEAR_EDGE")
    return {"states": states, "regime": regime, "why": why, "against": against,
            "trade_implication": "SQUEEZE_CONTEXT_POSSIBLE" if intraday_state in {"NEW_SHORTS", "SHORT_LIQUIDATION"} and spot_sign > 0 else "CONTEXT_ONLY",
            "execution": "NOT_AUTHORIZED", "required": ["price acceptance", "market structure", "microstructure reversal", "order-flow trigger"],
            "methodology": "separate horizons; external context never overrides execution or intraday positioning"}


def external_unavailable(reason: str) -> dict[str, Any]:
    return {"status": "UNAVAILABLE", "state": "UNKNOWN", "reason": reason, "confidence": None, "strength": None}
