from capital_flow.engine import normalize_agg_trade
from capital_flow.liquidity_probability_v2 import (
    atr_normalized_distance,
    build_v2_decision,
    competing_risks,
    expected_touch_time,
    price_distance,
    robust_density,
    weighted_orderbook_imbalance,
)


def test_v2_distance_and_density_are_derived_not_probability():
    assert price_distance(63_700, 65_000)["percent"] == -2.0
    assert atr_normalized_distance(64_500, 65_000, 250)["value"] == 2.0
    density = robust_density([10, 10, 10, 100], 100)
    assert density["status"] == "DERIVED"
    assert density["score"] > 0.5


def test_v2_orderbook_imbalance_is_bounded_and_observed_levels_are_separate():
    result = weighted_orderbook_imbalance({"bids": [[99, 800]], "asks": [[101, 500]]}, 100)
    assert -1 <= result["value"] <= 1
    assert result["status"] == "DERIVED"
    assert result["interpretation"].startswith("displayed")


def test_v2_competing_risks_is_not_touch_probability():
    targets = [
        {"id": "down", "touchProbability": {"60": 0.6}},
        {"id": "up", "touchProbability": {"60": 0.4}},
    ]
    result = competing_risks(targets)
    # First-hit CIFs sum to the probability that any competing target touches;
    # the remaining mass is "none touched by the horizon".
    assert round(sum(result.values()), 6) == 0.76
    assert result["down"] > result["up"]


def test_v2_untrained_model_does_not_emit_calibrated_confidence_or_cascade():
    now = 1_700_000_000_000
    trades = [
        normalize_agg_trade({"T": now + i * 1000, "a": i, "p": str(100 + i * 0.1), "q": "1", "m": False, "s": "BTCUSDT"}, "futures")
        for i in range(30)
    ]
    result = build_v2_decision(
        current_price=103,
        atr=1,
        candidates=[{"id": "down", "targetCenter": 100, "targetLow": 100, "targetHigh": 100, "types": ["LONG_LIQ"], "estimatedNotional": 1000}],
        liquidity_levels=[{"price": 101, "side": "BID", "wall_strength": 0.8, "absorption_score": 0.7, "status": "DERIVED"}],
        liquidation_zones=[{"center_price": 100, "estimated_notional": 1000}],
        profile={"status": "DERIVED", "hvn": [101]},
        book={"bids": [[102, "10"]], "asks": [[104, "8"]]},
        trades=trades,
        now_ms=now + 30_000,
        input_timestamps={"Trades": now + 29_000, "Orderbook": now + 29_000, "OI": now + 29_000},
        model_artifact=None,
    )
    target = result["targets"][0]
    assert result["modelHealth"]["status"] == "MODEL UNAVAILABLE"
    assert target["cascadePotential"]["value"] is None
    assert target["confidence"] is None
    assert target["touchProbabilityStatus"] == "VOLATILITY BASELINE · NOT CALIBRATED"


def test_v2_stale_critical_input_suppresses_model_output():
    result = build_v2_decision(
        current_price=100,
        atr=1,
        candidates=[{"id": "down", "targetCenter": 99, "estimatedNotional": 10}],
        liquidity_levels=[],
        liquidation_zones=[],
        profile={},
        book={},
        trades=[],
        now_ms=10_000,
        input_timestamps={"Orderbook": -1},
        model_artifact=None,
    )
    assert result["status"] == "MODEL OUTPUT SUPPRESSED"
    assert result["targets"][0]["touchProbability"]["60"] is None


def test_expected_touch_median_is_unresolved_below_fifty_percent():
    result = expected_touch_time({5: 0.1, 15: 0.2, 30: 0.3, 60: 0.4, 240: 0.49})
    assert result["medianMinutes"] is None
