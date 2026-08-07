from capital_flow.graphsense import aggregate_exchange_flow, classify_transaction, resource_audit
from capital_flow.historical import calibrate_probabilities, event_outcomes, walk_forward_prefixes
from capital_flow.institutional import holdings_delta, institutional_aggregate
from capital_flow.phase2 import BinanceWeb3Adapter, normalize_smart_money


def test_exchange_internal_and_low_coverage_are_not_signal():
    assert classify_transaction({"from_entity": "binance", "to_entity": "binance", "btc_amount": 4}, {"binance"}) == "BINANCE_INTERNAL"
    result = aggregate_exchange_flow([{"from_entity": "outside", "to_entity": "binance", "btc_amount": 1}], {"binance"})
    assert result["observed_inflow_btc"] == 1
    assert result["classified_coverage_pct"] == 100
    assert result["unknown_flow_pct"] == 0


def test_exchange_low_coverage_is_explicit():
    result = aggregate_exchange_flow([{"from_entity": "outside", "to_entity": "binance", "btc_amount": 1}, {"btc_amount": 9}], {"binance"})
    assert result["confidence"] == "LOW"
    assert "Low coverage" in result["coverage_warning"]


def test_smart_money_rejects_btc_and_preserves_chain_semantics():
    result = BinanceWeb3Adapter("/does/not/exist").run("smart-money-inflow", {"chainId": "BTC", "period": "24h"})
    assert result["status"] == "UNAVAILABLE"
    assert normalize_smart_money({"status": "REAL", "chain": "56", "period": "24h", "payload": {"data": [{"tokenName": "X", "ca": "0x1", "inflow": "2"}]}})["chain"] == "56"


def test_etf_holdings_delta_is_not_cash_flow():
    result = holdings_delta({"btc_holdings": 10}, {"btc_holdings": 12, "source": "issuer", "confidence": 0.98})
    assert result["status"] == "DERIVED"
    assert result["btc_delta"] == 2
    assert "not official cash flow" in result["methodology"]
    assert institutional_aggregate([{"btc_holdings": 12}], {"1D": 2})["state"] == "INFLOW"


def test_historical_prefixes_do_not_see_future():
    rows = [{"timestamp_ms": i, "value": i} for i in range(4)]
    prefixes = walk_forward_prefixes(rows, min_train=1)
    assert prefixes[0]["history_size"] == 0
    assert prefixes[2]["history"][-1]["value"] == 1
    assert all(x["row"]["timestamp_ms"] not in [y["timestamp_ms"] for y in x["history"]] for x in prefixes)


def test_outcomes_and_calibration_are_explicit():
    events = [{"timestamp_ms": 0, "price": 100, "direction": "LONG", "score": 0.7}]
    prices = [{"timestamp_ms": 1000, "price": 101, "high": 102, "low": 99}]
    outcomes = event_outcomes(events, prices, (60,))[0]["outcomes"]["60"]
    assert outcomes["forward_return"] > 0
    calibration = calibrate_probabilities([{"score": 0.7, "outcome": True}, {"score": 0.7, "outcome": False}])
    assert calibration["sample_size"] == 2
    assert calibration["bins"][0]["calibrated_probability"] == 0.5


def test_graphsense_resource_audit_is_read_only():
    audit = resource_audit()
    assert audit["status"] == "REAL"
    assert "recommendation" in audit
