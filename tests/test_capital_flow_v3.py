from __future__ import annotations

from capital_flow.engine import (
    AggTrade,
    CapitalFlowEngine,
    aggregate_trades,
    bucket_flows,
    human_number,
    position_state,
    retail_flow,
    spot_futures_divergence,
    trader_size_thresholds,
    whale_behavior,
)
from capital_flow.sources_v3 import (
    ExchangeLabel,
    aggregate_observed_exchange_flow,
    calibrate_scores,
    classify_utxo_transfer,
    holdings_delta,
    issuer_adapter,
    walk_forward,
)


def _trade(i: int, price: float, notional: float, side: str, market: str = "spot") -> AggTrade:
    quantity = notional / price
    return AggTrade(market, "BTCUSDT", 1_700_000_000_000 + i * 1000, i, price, quantity, notional, side == "SELL", side)


def _trades():
    return [_trade(1, 100, 10, "BUY"), _trade(2, 101, 100, "SELL"), _trade(3, 102, 1000, "BUY"), _trade(4, 103, 10000, "SELL"), _trade(5, 104, 100000, "BUY")]


def test_retail_definition_matches_regime():
    trades = _trades()
    thresholds = {"thresholds": {"p70": 50, "p90": 500, "p99": 5000, "p99_9": 50000}}
    retail = retail_flow(bucket_flows(trades, thresholds))
    assert retail["buckets"] == ["SMALL", "MEDIUM"]
    assert retail["state"] == "SELLING"


def test_cvd_total_vs_slope_semantics():
    rows = aggregate_trades(_trades(), 60)
    assert rows[-1]["cvd"] == sum(row["delta_usd"] for row in rows)
    engine = CapitalFlowEngine()
    engine.spot_trades = _trades()
    engine.futures_trades = [_trade(i, 100 + i, 10, "SELL", "futures") for i in range(1, 6)]
    engine.set_size_history([x.notional_usd for x in engine.spot_trades], [x.notional_usd for x in engine.futures_trades])
    snap = engine.snapshot(60)
    assert snap["spot"]["value"]["cvd_total"] > 0
    assert snap["spot"]["value"]["cvd_reset"] == "query_window_start"
    assert snap["spot_vs_futures"]["classification_basis"] == "1m CVD slope sign; cumulative CVD total is shown separately"


def test_spot_futures_divergence_explanation():
    result = spot_futures_divergence({"cvd_slope_1m": 5}, {"cvd_slope_1m": -4})
    assert result["state"] == "SPOT_UP_FUTURES_DOWN"
    assert "cumulative CVD" in result["classification_basis"]


def test_position_state_evidence_and_score_not_probability():
    result = position_state({"price_change": 1, "delta_oi": 2, "futures_delta": 3, "long_liquidation": None, "short_liquidation": None})
    assert result["state"] == "NEW_LONGS"
    assert result["state_score"] is not None
    assert "probabilities" not in result
    assert "Liquidations unavailable" in result["missing"]


def test_missing_liquidations_reduce_confidence():
    with_missing = position_state({"price_change": 1, "delta_oi": 2, "futures_delta": 3, "long_liquidation": None, "short_liquidation": None})
    with_liq = position_state({"price_change": 1, "delta_oi": 2, "futures_delta": 3, "long_liquidation": 10, "short_liquidation": 0})
    assert with_missing["confidence"] < with_liq["confidence"]


def test_whale_behavior_evidence():
    result = whale_behavior({"whale_buy_usd": 100, "whale_sell_usd": 20, "mega_whale_buy_usd": 50, "mega_whale_sell_usd": 0, "spot_cvd": 10, "price_change": -1, "buy_efficiency": "LOW"})
    assert result["behavior"] in {"ACCUMULATION", "ABSORPTION", "AGGRESSIVE_BUYING"}
    assert result["state_score"] is not None
    assert result["calibrated_probability"] is None


def test_unknown_not_neutral_and_unavailable_not_neutral():
    assert retail_flow({})["state"] == "UNKNOWN"
    assert spot_futures_divergence(None, 1)["state"] == "UNKNOWN"
    assert position_state({})["state"] == "UNKNOWN"


def test_orderbook_displayed_not_executed():
    engine = CapitalFlowEngine()
    engine.orderbook = {"bids": [[99, 10]], "asks": [[101, 1]]}
    result = engine.snapshot(60)
    assert result["orderbook"]["methodology"].endswith("displayed liquidity is not executed flow")


def test_matrix_contains_required_columns_and_human_number_format():
    engine = CapitalFlowEngine()
    engine.spot_trades = _trades()
    engine.futures_trades = [_trade(i, 100 + i, 10, "SELL", "futures") for i in range(1, 6)]
    engine.set_size_history([x.notional_usd for x in engine.spot_trades], [x.notional_usd for x in engine.futures_trades])
    row = engine.snapshot(60)["capital_flow_matrix"][0]
    assert {"source", "metric", "value", "direction", "strength", "confidence", "status", "timeframe", "freshness", "interpretation"} <= set(row)
    assert human_number(9.006230799999999, currency=True) == "$9.01"
    assert human_number(15460000, currency=True) == "$15.46M"


def test_top_trader_account_position_separate():
    engine = CapitalFlowEngine()
    engine.set_top_traders({"account_ratio": {"longAccount": 0.6, "shortAccount": 0.4}, "position_ratio": {"longPosition": 0.7, "shortPosition": 0.3}}, 1000)
    value = engine.snapshot(60)["top_traders"]["value"]
    assert value["account_bias"]["long"] == 0.6
    assert value["position_bias"]["long"] == 0.7
    assert value["account_bias"] != value["position_bias"]


def test_summary_does_not_claim_missing_data():
    snapshot = CapitalFlowEngine().snapshot(60)
    assert snapshot["summary"]["tradeImplication"] == "WAIT"
    assert snapshot["summary"]["execution"] == "NOT_AUTHORIZED"
    assert any("unavailable" in value.lower() or "missing" in value.lower() for value in snapshot["missing"])


def test_source_normalization_and_internal_transfer_exclusion():
    adapter = issuer_adapter("IBIT")
    assert adapter.normalize({"btc_holdings": "123.4"}, "2026-08-06")["status"] == "REAL"
    label = ExchangeLabel("a", "BINANCE", "hot", "exchange", "test", None, None, None, "VERIFIED", "verified")
    internal = classify_utxo_transfer(label, label, 5)
    assert internal["classification"] == "BINANCE_INTERNAL"
    assert not internal["eligible_for_observed_flow"]
    aggregate = aggregate_observed_exchange_flow([internal])
    assert aggregate["status"] == "UNAVAILABLE"


def test_holdings_delta_and_walk_forward_calibration():
    points = [{"btc_holdings": 100}, {"btc_holdings": 102}, {"btc_holdings": 104}]
    assert holdings_delta(points)["btc_delta_1D"] == 2
    rows = [{"timestamp": i, "price": 100 + i, "state_score": 70, "forward_returns": {"1h": 1.0}} for i in range(40)]
    assert walk_forward(rows, 20, 10, 10)["shuffle"] is False
    calibrated = calibrate_scores(rows, min_samples=30)
    assert calibrated["buckets"][0]["calibrated_probability"] == 100.0
