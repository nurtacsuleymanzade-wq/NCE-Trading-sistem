from capital_flow.external_context import (
    HoldingsPoint,
    aggregate_exchange_flow,
    classify_exchange_transaction,
    holdings_flow,
    institutional_state,
)


def test_exchange_classification_excludes_internal_and_unknown_from_signal():
    assert classify_exchange_transaction("wallet-a", "binance")["classification"] == "EXTERNAL_TO_BINANCE"
    assert classify_exchange_transaction("binance", "binance")["classification"] == "BINANCE_INTERNAL"
    assert classify_exchange_transaction(None, "binance")["classification"] == "UNCERTAIN"
    result = aggregate_exchange_flow([
        {"btc_amount": 3, "from_entity": "wallet-a", "to_entity": "binance"},
        {"btc_amount": 2, "from_entity": "binance", "to_entity": "wallet-b"},
        {"btc_amount": 10, "from_entity": "binance", "to_entity": "binance"},
        {"btc_amount": 5, "from_entity": None, "to_entity": "binance"},
    ])["summary"]
    assert result["inflow_btc"] == 3
    assert result["outflow_btc"] == 2
    assert result["internal_btc"] == 10
    assert result["unknown_btc"] == 5
    assert result["coverage_pct"] == 25


def test_etf_holdings_delta_is_derived_not_cash_flow():
    prev = HoldingsPoint("IBIT", 1, 1000, source="issuer", source_type="official", confidence=0.95)
    current = HoldingsPoint("IBIT", 2, 1120, source="issuer", source_type="official", confidence=0.95)
    flow = holdings_flow(prev, current, reference_btc_price=50000)
    assert flow["status"] == "DERIVED"
    assert flow["btc_delta"] == 120
    assert flow["usd_holdings_delta"] == 6_000_000
    assert "not cash-flow ground truth" in flow["methodology"].lower()
    assert institutional_state(1200, 0.9)["state"] == "STRONG_INFLOW"
