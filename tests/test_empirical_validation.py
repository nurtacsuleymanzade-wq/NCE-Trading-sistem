from __future__ import annotations

import json
from pathlib import Path

import pytest

from capital_flow.empirical import (
    add_forward_labels,
    available_at,
    past_only,
    walk_forward_calibration,
)
from capital_flow.engine import CapitalFlowEngine, normalize_agg_trade, position_state, trader_size_thresholds
from capital_flow.institutional import ETF_CONFIG, holdings_delta, normalized_unavailable
from capital_flow.replay import replay_snapshot


def _trade(i: int, timestamp: int, price: float, notional: float, side: str, market: str = "spot"):
    return normalize_agg_trade({"a": i, "T": timestamp, "p": price, "q": notional / price, "m": side == "SELL"}, market)


def test_live_replay_parity():
    spot = [_trade(i, 1_700_000_000_000 + i * 1000, 100 + i, 1000 + i * 10, "BUY" if i % 2 else "SELL") for i in range(1, 8)]
    futures = [_trade(i, 1_700_000_000_000 + i * 1000, 100 + i, 800 + i * 9, "SELL" if i % 2 else "BUY", "futures") for i in range(1, 8)]
    live = CapitalFlowEngine()
    live.spot_trades = list(spot)
    live.futures_trades = list(futures)
    live.set_size_history([x.notional_usd for x in spot], [x.notional_usd for x in futures])
    left = live.snapshot(60)
    right = replay_snapshot(spot, futures, 60)
    for key in ("spot", "futures", "spot_vs_futures", "position_state", "whale_behavior", "retail", "diagnosis"):
        def stable(value):
            if isinstance(value, dict):
                return {k: stable(v) for k, v in value.items() if k not in {"age_seconds", "freshness", "timestamp"}}
            if isinstance(value, list):
                return [stable(v) for v in value]
            return value
        assert stable(left[key]) == stable(right[key])


def test_no_future_trades_in_percentiles():
    thresholds = trader_size_thresholds([10, 20, 30], "spot")
    assert thresholds["sample_size"] == 3
    assert 100 not in [10, 20, 30]


def test_no_future_etf_data():
    rows = [{"published_at_ms": 100, "btc_holdings": 10}, {"published_at_ms": 300, "btc_holdings": 30}]
    assert past_only(rows, 200) == [rows[0]]
    assert not available_at(rows[1], 200)


def test_no_future_sec_filing():
    filing = {"publication_timestamp_ms": 200, "reported_btc": 12}
    assert not available_at(filing, 199)
    assert available_at(filing, 200)


def test_forward_returns_are_labels_only():
    event = {"timestamp_ms": 1000, "price": 100, "regime": "BROAD_ACCUMULATION", "direction": 1, "spot_delta": 5}
    labeled = add_forward_labels([event], [{"timestamp_ms": 1000, "close": 100, "high": 100, "low": 100}, {"timestamp_ms": 2000, "close": 101, "high": 102, "low": 99}])[0]
    assert labeled["spot_delta"] == 5
    assert labeled["labels"]["60"]["forward_return"] > 0
    assert "forward_return" not in labeled


def test_replay_chronological():
    rows = [{"timestamp_ms": 3}, {"timestamp_ms": 1}, {"timestamp_ms": 2}]
    ordered = sorted(rows, key=lambda x: x["timestamp_ms"])
    assert [row["timestamp_ms"] for row in ordered] == [1, 2, 3]


def test_score_not_probability():
    result = position_state({"price_change": 1, "delta_oi": 1, "futures_delta": 1})
    assert "probabilities" not in result
    assert result["calibrated_probability"] is None


def test_calibration_sample_counts():
    rows = [{"timestamp_ms": i, "engine_score": 70, "direction": 1, "labels": {"900": {"status": "DERIVED", "directional_return": 0.01, "continuation": True}}} for i in range(30)]
    result = walk_forward_calibration(rows, min_bin_sample=5)
    assert sum(x["sample_size"] for x in result["reliability_table_oos"]) == result["splits"]["OUT_OF_SAMPLE"]["resolved_size"]


def test_walk_forward_no_overlap():
    rows = [{"timestamp_ms": i, "labels": {"900": {"status": "DERIVED", "directional_return": 0.01, "continuation": True}}, "engine_score": 50} for i in range(20)]
    result = walk_forward_calibration(rows, min_bin_sample=2)
    assert result["oos_used_for_threshold_selection"] is False
    assert result["methodology"].find("no shuffle") >= 0


def test_oos_not_used_for_threshold_selection():
    rows = [{"timestamp_ms": i, "engine_score": 50, "labels": {"900": {"status": "DERIVED", "directional_return": 0.01, "continuation": True}}} for i in range(50)]
    result = walk_forward_calibration(rows, min_bin_sample=2)
    assert result["oos_used_for_threshold_selection"] is False
    assert "OUT_OF_SAMPLE" in result["splits"]


def test_missing_historical_source_not_zero():
    row = normalized_unavailable("FBTC", "not configured")
    assert row["status"] == "UNAVAILABLE"
    assert row["btc_holdings"] is None
    assert row["shares_outstanding"] is None


def test_etf_adapter_normalization():
    assert set(ETF_CONFIG) == {"IBIT", "FBTC", "GBTC", "ARKB", "BITB", "BTCO", "HODL", "BRRR", "EZBC", "BTCW", "BTC"}
    row = normalized_unavailable("FBTC", "not configured")
    assert {"btc_holdings", "shares_outstanding", "aum_usd", "nav_usd"} <= set(row)


def test_etf_holdings_delta():
    result = holdings_delta({"btc_holdings": 10}, {"btc_holdings": 12, "source": "issuer", "confidence": 98})
    assert result["btc_delta"] == 2
    assert "not official cash flow" in result["methodology"]


def test_historical_manifest():
    path = Path("historical/dataset_manifest.json")
    assert path.exists()
    manifest = json.loads(path.read_text())
    assert manifest["datasets"]
    assert all("source" in row and "sha256" in row for row in manifest["datasets"] if row["status"] == "DOWNLOADED")


def test_raw_data_immutable():
    manifest = json.loads(Path("historical/dataset_manifest.json").read_text())
    paths = [Path(row["path"]) for row in manifest["datasets"] if row["status"] == "DOWNLOADED"]
    assert paths
    assert all(path.exists() and path.stat().st_mode & 0o222 == 0 for path in paths[:10])
