"""Human-readable, evidence-first interpretation contract."""

from __future__ import annotations

from typing import Any, Mapping

from .config import RETAIL_BUCKETS


def _state(value: str, strength: float | None, confidence: float | None, timeframe: str, status: str, **extra: Any) -> dict[str, Any]:
    return {"state": value or "UNKNOWN", "strength": strength, "confidence": confidence, "timeframe": timeframe, "status": status, **extra}


def _flow_state(flow: Mapping[str, Any], label: str, timeframe: str) -> dict[str, Any]:
    value = flow.get("value") or {}
    delta = value.get("cvd_slope_1m", value.get("delta_usd"))
    state = "BULLISH" if delta is not None and delta > 0 else "BEARISH" if delta is not None and delta < 0 else "UNKNOWN"
    return _state(state, abs(delta) if delta is not None else None, flow.get("metadata", {}).get("confidence"), timeframe, flow.get("metadata", {}).get("status", "UNAVAILABLE"), basis="cvd_slope_1m", cvd_total=value.get("cvd_total", value.get("cvd")), cvd_slope_1m=value.get("cvd_slope_1m"), cvd_slope_5m=value.get("cvd_slope_5m"), cvd_reset=value.get("cvd_reset"))


def _plain_flow(label: str, state: Mapping[str, Any]) -> str:
    value = state.get("state", "UNKNOWN")
    if value == "UNKNOWN":
        return f"{label} executed flow bu pencerede kullanılamıyor veya sınıflandırılamıyor."
    return f"{label} executed flow, 1 dakikalık CVD eğiminde {value.lower()} görünüyor."


def build_interpretation(snapshot: Mapping[str, Any], external_context: Mapping[str, Any] | None = None) -> dict[str, Any]:
    spot = snapshot.get("spot", {})
    futures = snapshot.get("futures", {})
    retail = snapshot.get("retail", {})
    whale = snapshot.get("whale_behavior", {})
    position = snapshot.get("position_state", {})
    oi = snapshot.get("oi", {})
    funding = snapshot.get("funding", {})
    book = snapshot.get("orderbook", {})
    top = snapshot.get("top_traders", {})
    liq = snapshot.get("liquidations", {})
    exchange = (external_context or {}).get("exchange", {"status": "UNAVAILABLE", "reason": "GraphSense/verified labels not configured"})
    institutional = (external_context or {}).get("institutional", {"status": "UNAVAILABLE", "reason": "SEC/issuer adapter not configured"})
    network = (external_context or {}).get("network_context", {"status": "UNAVAILABLE", "reason": "Coin Metrics metadata not discovered"})
    smart = (external_context or {}).get("smart_money", {"status": "UNAVAILABLE", "reason": "BTC-native smart money scope not verified"})
    timeframe = f"{snapshot.get('timeframe_seconds', 'unknown')}s"
    spot_state = _flow_state(spot, "Spot", timeframe)
    futures_state = _flow_state(futures, "Futures", timeframe)
    orderbook_state = "BID_HEAVY" if book.get("imbalance_10bps") is not None and book.get("imbalance_10bps") > 0.1 else "ASK_HEAVY" if book.get("imbalance_10bps") is not None and book.get("imbalance_10bps") < -0.1 else "BALANCED" if book.get("imbalance_10bps") is not None else "UNKNOWN"
    oi_value = oi.get("value")
    oi_state = "RISING" if oi.get("delta", 0) > 0 else "FALLING" if oi.get("delta", 0) < 0 else "FLAT" if oi_value is not None else "UNKNOWN"
    top_value = top.get("value") or {}
    account_bias = (top_value.get("account_bias") or {}).get("bias")
    position_bias = (top_value.get("position_bias") or {}).get("bias")
    top_state = "LONG_BIASED" if account_bias == "LONG" or position_bias == "LONG" else "SHORT_BIASED" if account_bias == "SHORT" or position_bias == "SHORT" else "MIXED" if top_value else "UNKNOWN"
    liq_value = liq.get("value")
    liq_state = "UNAVAILABLE" if liq.get("metadata", {}).get("status") == "UNAVAILABLE" else (liq_value or {}).get("state", "UNKNOWN")
    global_ls = snapshot.get("global_ls") or {}
    global_value = global_ls.get("value") or {}
    global_payload = global_value.get("value") or {}
    global_long = float(global_payload.get("longAccount")) if global_payload.get("longAccount") is not None else None
    global_short = float(global_payload.get("shortAccount")) if global_payload.get("shortAccount") is not None else None
    global_state = "LONG_BIASED" if global_long is not None and global_short is not None and global_long > global_short else "SHORT_BIASED" if global_long is not None and global_short is not None and global_short > global_long else "MIXED" if global_long is not None else "UNKNOWN"
    conflicts: list[dict[str, Any]] = []
    if spot_state["state"] != "UNKNOWN" and futures_state["state"] != "UNKNOWN" and spot_state["state"] != futures_state["state"]:
        conflicts.append({"type": "SPOT_BUYING_VS_DERIVATIVES_SELLING", "interpretation": "Spot and derivatives are not moving in the same direction.", "status": "DERIVED"})
    if book and orderbook_state == "ASK_HEAVY" and spot_state["state"] == "BULLISH":
        conflicts.append({"type": "EXECUTED_BUYING_VS_DISPLAYED_ASK_LIQUIDITY", "interpretation": "Executed spot buying meets heavier displayed ask liquidity; display is intent, not executed flow.", "status": "DERIVED"})

    why = list((snapshot.get("diagnosis") or {}).get("why", []))
    against = list((snapshot.get("diagnosis") or {}).get("against", []))
    missing = list((snapshot.get("diagnosis") or {}).get("missing", []))
    if whale.get("behavior") not in {None, "UNKNOWN", "NO_CLEAR_BEHAVIOR"}:
        why.append(f"Whale behavior = {whale.get('behavior')}")
    if retail.get("status") == "UNAVAILABLE":
        missing.append(f"Retail combined flow ({' + '.join(RETAIL_BUCKETS)})")
    if liq_state == "UNAVAILABLE":
        missing.append((liq.get("metadata") or {}).get("reason", "Liquidations unavailable"))
    if institutional.get("status") == "UNAVAILABLE":
        missing.append(institutional.get("reason", "Institutional holdings unavailable"))
    if exchange.get("status") == "UNAVAILABLE":
        missing.append(exchange.get("reason", "Exchange flow unavailable"))

    regime = (snapshot.get("diagnosis") or {}).get("regime", "NO_CLEAR_EDGE")
    if spot_state["state"] == "BULLISH" and futures_state["state"] == "BEARISH":
        flow_bias = "MIXED"
    elif spot_state["state"] == futures_state["state"] and spot_state["state"] != "UNKNOWN":
        flow_bias = spot_state["state"]
    else:
        flow_bias = "UNKNOWN"
    trade = "WAIT"
    execution = "NOT_AUTHORIZED"
    short_lines = [_plain_flow("Spot", spot_state), _plain_flow("Futures", futures_state)]
    if oi_value is not None:
        short_lines.append(f"OI {oi_state.lower()}; kaldıraç bağlamı {'artıyor' if oi_state == 'RISING' else 'azalıyor' if oi_state == 'FALLING' else 'yatay'}." )
    if orderbook_state != "UNKNOWN":
        short_lines.append(f"Görüntülenen orderbook likiditesi {orderbook_state.lower()}; bu niyettir, gerçekleşmiş para akışı değildir.")
    if institutional.get("status") == "DERIVED" or institutional.get("status") == "REAL":
        short_lines.append(f"Kurumsal bağlam kendi yüksek zaman ufkunda {institutional.get('state', 'available').lower()} görünüyor.")
    else:
        short_lines.append("Kurumsal bağlam kullanılamıyor; nötr değerle değiştirilmedi.")
    summary_text = " ".join(short_lines[:6])
    external = external_context or {}
    phase1_status = "COMPLETE" if snapshot.get("status") == "PASS" and spot_state.get("status") == "DERIVED" and futures_state.get("status") == "DERIVED" else "PARTIAL"
    network_status = network.get("status")
    smart_status = smart.get("status")
    phase2_status = "COMPLETE" if network_status in {"REAL", "DERIVED"} and (smart_status in {"REAL", "DERIVED"} or smart.get("btc_status") == "UNAVAILABLE") else "PARTIAL"
    phase3_status = "COMPLETE" if institutional.get("status") in {"REAL", "DERIVED"} and any((item or {}).get("status") == "REAL" for item in (institutional.get("funds") or [])) else "PENDING"
    phase4_status = "COMPLETE" if exchange.get("status") == "DERIVED" else "DEFERRED" if external.get("graphsense_audit") else "PENDING"
    phase5_status = "COMPLETE" if external.get("historical", {}).get("calibration_status") == "AVAILABLE" else "PARTIAL"
    states = {
        "MICRO_FLOW": _state(flow_bias, None, min(x for x in [spot_state.get("confidence"), futures_state.get("confidence")] if x is not None) if any(x is not None for x in [spot_state.get("confidence"), futures_state.get("confidence")]) else None, "1s–1m", "DERIVED" if flow_bias != "UNKNOWN" else "UNKNOWN"),
        "INTRADAY_FLOW": _state(position.get("state", "UNKNOWN"), position.get("state_score"), position.get("confidence"), "5m–4h", position.get("status", "UNAVAILABLE"), calibrated_probability=position.get("calibrated_probability")),
        "ONCHAIN_FLOW": _state(exchange.get("state", "UNKNOWN"), exchange.get("strength"), exchange.get("confidence"), "1h–24h+", exchange.get("status", "UNAVAILABLE"), coverage=exchange.get("coverage_pct")),
        "INSTITUTIONAL_FLOW": _state(institutional.get("state", "UNKNOWN"), institutional.get("strength"), institutional.get("confidence"), "1d–20d+", institutional.get("status", "UNAVAILABLE")),
        "NETWORK_CONTEXT": _state(network.get("state", "UNKNOWN"), network.get("strength"), network.get("confidence"), network.get("timeframe", "daily"), network.get("status", "UNAVAILABLE")),
        "GLOBAL_POSITIONING": _state(global_state, abs(global_long - global_short) * 100 if global_long is not None and global_short is not None else None, 95.0 if global_ls.get("metadata", {}).get("status") == "REAL" else None, "5m–4h", global_ls.get("metadata", {}).get("status", "UNAVAILABLE"), long_accounts=global_long, short_accounts=global_short, ratio=global_payload.get("longShortRatio")),
    }
    matrix = list(snapshot.get("capital_flow_matrix") or [])
    for row in matrix:
        row.setdefault("interpretation", f"{row.get('source', 'Metric')}: {row.get('direction', 'UNKNOWN')}.")
        row.setdefault("strength", None)
        row.setdefault("confidence", None)
        row.setdefault("timeframe", timeframe)
        row.setdefault("freshness", "UNKNOWN")
    return {
        "summary": {"headline": "CAPITAL FLOW — KISA PIYASA OZETI", "shortText": summary_text, "headline_tr": "CAPITAL FLOW — KISA PIYASA OZETI", "flowBias": flow_bias, "capitalRegime": regime, "tradeImplication": trade, "execution": execution, "strength": None, "confidence": min((x for x in [spot_state.get("confidence"), futures_state.get("confidence"), position.get("confidence")] if x is not None), default=None)},
        "states": {"SPOT": spot_state, "FUTURES": futures_state, "WHALE_SIZED": _state(whale.get("behavior", "UNKNOWN"), whale.get("state_score"), whale.get("confidence"), timeframe, whale.get("status", "UNAVAILABLE")), "RETAIL": _state(retail.get("state", "UNKNOWN"), abs(retail.get("net_flow")) if retail.get("net_flow") is not None else None, 98.0 if retail.get("status") == "DERIVED" else None, timeframe, retail.get("status", "UNAVAILABLE"), buckets=retail.get("buckets", list(RETAIL_BUCKETS)), net_flow=retail.get("net_flow")), "OI": _state(oi_state, abs(oi.get("delta")) if oi.get("delta") is not None else None, (oi.get("metadata") or {}).get("confidence"), "5m–4h", (oi.get("metadata") or {}).get("status", "UNAVAILABLE")), "DERIVATIVES": _state(position.get("state", "UNKNOWN"), position.get("state_score"), position.get("confidence"), "5m–4h", position.get("status", "UNAVAILABLE")), "ORDERBOOK": _state(orderbook_state, abs(book.get("imbalance_10bps")) * 100 if book.get("imbalance_10bps") is not None else None, 98 if book.get("status") == "DERIVED" else None, timeframe, book.get("status", "UNAVAILABLE"), semantics="displayed liquidity / intent"), "TOP_TRADERS": _state(top_state, None, (top.get("metadata") or {}).get("confidence") or (95.0 if (top.get("metadata") or {}).get("status") == "REAL" else None), "5m–24h", (top.get("metadata") or {}).get("status", "UNAVAILABLE"), accounts=(top_value.get("account_bias") or {}), positions=(top_value.get("position_bias") or {})), "GLOBAL_POSITIONING": _state(global_state, abs(global_long - global_short) * 100 if global_long is not None and global_short is not None else None, 95.0 if global_ls.get("metadata", {}).get("status") == "REAL" else None, "5m–4h", global_ls.get("metadata", {}).get("status", "UNAVAILABLE"), long_accounts=global_long, short_accounts=global_short, ratio=global_payload.get("longShortRatio")), "LIQUIDATIONS": _state(liq_state, None, (liq.get("metadata") or {}).get("confidence"), "1s–1m", (liq.get("metadata") or {}).get("status", "UNAVAILABLE")), "EXCHANGE": _state(exchange.get("state", "UNKNOWN"), exchange.get("strength"), exchange.get("confidence"), "1h–24h+", exchange.get("status", "UNAVAILABLE")), "INSTITUTIONAL": _state(institutional.get("state", "UNKNOWN"), institutional.get("strength"), institutional.get("confidence"), "1d–20d+", institutional.get("status", "UNAVAILABLE")), "NETWORK_CONTEXT": _state(network.get("state", "UNKNOWN"), network.get("strength"), network.get("confidence"), network.get("timeframe", "daily"), network.get("status", "UNAVAILABLE")), "SMART_MONEY": _state(smart.get("state", "UNKNOWN"), smart.get("strength"), smart.get("confidence"), smart.get("period", "7d–90d"), smart.get("status", "UNAVAILABLE"))},
        "horizonStates": states,
        "why": list(dict.fromkeys(why)), "against": list(dict.fromkeys(against)), "missing": list(dict.fromkeys(missing)), "conflicts": conflicts,
        "matrix": matrix,
        "metadata": {"retail_buckets": list(RETAIL_BUCKETS), "score_is_probability": False, "calibration_status": "UNAVAILABLE", "execution_authorized": False},
        "phaseStatus": {"phase1": phase1_status, "phase2": phase2_status, "phase3": phase3_status, "phase4": phase4_status, "phase5": phase5_status},
        "externalContext": {"exchange": exchange, "institutional": institutional, "network_context": network, "smart_money": smart},
    }
