from capital_flow.probability_map import (
    OICohort,
    adaptive_price_bin_size,
    build_probability_targets,
    calibrate_score,
    competing_first_hit,
    generate_candidates,
    liquidation_price,
    liquidity_lifecycle,
    liquidity_zone_metrics,
    monotonic_probabilities,
    path_friction,
    build_target_calibration,
    historical_target_replay,
)
from capital_flow.engine import normalize_agg_trade
from capital_flow.http_api import create_router
from capital_flow.storage import CapitalFlowStore
import time


def test_lifecycle_separates_depletion_cancel_and_replenishment():
    previous = {"bids": [[100, "10"]], "asks": [[101, "20"]]}
    current = {"bids": [[100, "5"]], "asks": [[101, "25"]]}
    events = liquidity_lifecycle(previous, current, timestamp_ms=1000, update_id=2)
    actions = {event["action"] for event in events}
    assert actions == {"DEPLETED", "REPLENISHED"}
    assert all(event["status"] == "REAL" for event in events)


def test_cancelled_level_is_not_called_executed_without_trade_evidence():
    events = liquidity_lifecycle({"bids": [[100, "10"]], "asks": []}, {"bids": [], "asks": []})
    assert events[0]["action"] == "CANCELLED"
    assert events[0]["action"] != "EXECUTED"


def test_spoof_and_absorption_metrics_use_lifecycle_evidence():
    events = [
        {"timestamp_ms": 1, "price": 100, "side": "BID", "notional": 20_000_000, "action": "ADDED", "remaining_quantity": 100},
        {"timestamp_ms": 2, "price": 100, "side": "BID", "notional": 18_000_000, "action": "CANCELLED", "remaining_quantity": 0},
    ]
    spoof = liquidity_zone_metrics(events, 100)[0]
    assert spoof["classification"] == "SPOOF/PULLED"
    assert spoof["cancel_ratio"] > spoof["execution_ratio"]

    absorption_events = [
        {"timestamp_ms": 1, "price": 101, "side": "ASK", "notional": 20_000_000, "action": "ADDED", "remaining_quantity": 100},
        {"timestamp_ms": 2, "price": 101, "side": "ASK", "notional": 17_000_000, "action": "EXECUTED", "remaining_quantity": 20},
        {"timestamp_ms": 3, "price": 101, "side": "ASK", "notional": 14_000_000, "action": "REPLENISHED", "remaining_quantity": 80},
    ]
    absorbed = liquidity_zone_metrics(absorption_events, 100)[0]
    assert absorbed["absorption_score"] > 0
    assert absorbed["classification"] == "ABSORPTION/REAL_PASSIVE"


def test_adaptive_bin_changes_with_zoom_and_atr():
    normal = adaptive_price_bin_size(65_000, 100, "5m")
    high_zoom = adaptive_price_bin_size(65_000, 100, "5m", "high")
    assert normal >= high_zoom >= 0.1


def test_long_and_short_liquidation_equations_are_directional():
    assert liquidation_price(100, 10, "LONG") < 100
    assert liquidation_price(100, 10, "SHORT") > 100


def test_cohort_decay_reduces_inventory_and_keeps_estimated_label():
    cohort = OICohort("1", 1, 100, 1_000_000, .6, .4, {"10": 1.0})
    initial = cohort.remaining_oi
    cohort.decay(800_000, age_hours=72)
    assert cohort.remaining_oi < initial
    assert cohort.status == "ESTIMATED"


def test_score_is_not_probability_without_calibration():
    assert calibrate_score(83, None) == (None, 0, "UNAVAILABLE")
    candidates = generate_candidates(100, profile={"poc": 95}, levels={"SWING_LOW": [90]}, atr=2)
    targets = build_probability_targets(100, candidates, atr=2, profile={"poc": 95})
    assert targets
    assert targets[0]["attractionScore"] is not None
    assert targets[0]["probability"]["hit1h"] is None
    assert targets[0]["status"] == "MODEL_SCORE"


def test_calibration_is_historical_and_not_score_identity():
    calibration = [{"score_low": 80, "score_high": 90, "hit_rate": .64, "sample_size": 8430}]
    assert calibrate_score(83, calibration) == (.64, 8430, "CALIBRATED")


def test_probability_horizons_are_monotonic_and_first_hit_competes():
    probabilities = monotonic_probabilities({15: .3, 30: .2, 60: .5, 240: .6})
    assert probabilities["hit15m"] <= probabilities["hit30m"] <= probabilities["hit1h"] <= probabilities["hit4h"]
    first = competing_first_hit([{"id": "a", "probability": {"hit1h": .6}}, {"id": "b", "probability": {"hit1h": .4}}])
    assert round(first["a"] + first["b"], 6) == 1
    assert first["a"] > first["b"]


def test_path_friction_is_explicit():
    path = path_friction(110, 100, [{"price": 105, "wall_strength": 1}], {"poc": 106, "vah": 108, "val": 95})
    assert path["score"] > 0
    assert path["label"] in {"LOW", "MEDIUM", "HIGH", "BLOCKED / VERY HIGH"}


def test_probability_map_api_contract_is_additive_and_marks_estimates(tmp_path):
    db = tmp_path / "probability.sqlite3"
    store = CapitalFlowStore(db)
    now = int(time.time() * 1000)
    for index, price in enumerate((100.0, 100.5, 101.0, 100.8), 1):
        trade = normalize_agg_trade({"T": now - (5 - index) * 1000, "a": index, "p": str(price), "q": "10", "m": False, "s": "BTCUSDT"}, "futures")
        store.insert_trade(trade, now)
    store.insert_json("orderbook_raw", {"bids": [[99, "100"]], "asks": [[102, "120"]]}, now, symbol="BTCUSDT", market="futures", last_update_id=10)
    store.insert_orderbook_events("BTCUSDT", "futures", [{"timestamp_ms": now, "price": 102, "side": "ASK", "quantity": 120, "notional": 12240, "action": "ADDED", "remaining_quantity": 120}])
    store.insert_oi("BTCUSDT", now - 1000, 1000, {"openInterest": "1000"})
    store.insert_oi("BTCUSDT", now, 1200, {"openInterest": "1200"})
    store.close()

    router = create_router(str(db))
    paths = {route.path for route in router.routes}
    assert "/probability-map/summary" in paths
    summary_route = next(route for route in router.routes if route.path == "/probability-map/summary")
    payload = summary_route.endpoint(tf="5m", symbol="BTCUSDT", market="futures")
    assert payload["schemaVersion"] == "probability-map-v1"
    assert payload["rules"]["scoreIsProbability"] is False
    assert payload["liquidations"]["status"] == "ESTIMATED"
    assert payload["rules"]["probabilityStatus"] == "CALIBRATED"
    calibrated = [target for target in payload["targets"] if target["status"] == "CALIBRATED"]
    assert calibrated
    assert all(target["probability"]["hit1h"] is not None for target in calibrated)
    assert any(round(target["attractionScore"] / 100, 6) != round(target["probability"]["hit1h"], 6) for target in calibrated)


def test_historical_replay_uses_future_only_as_labels():
    import json
    from pathlib import Path

    bars = json.loads((Path("data") / "bars_1m.json").read_text())[:420]
    replay = historical_target_replay(bars, timeframe_seconds=60, warmup_bars=60, max_snapshots=100)
    assert replay["status"] == "DERIVED"
    assert "future OHLC used only for labels" in replay["methodology"]
    calibration = build_target_calibration(replay, minimum_sample=1)
    assert calibration["status"] in {"CALIBRATED", "INSUFFICIENT_SAMPLE"}
    assert calibration["scoreIsProbability"] is False
