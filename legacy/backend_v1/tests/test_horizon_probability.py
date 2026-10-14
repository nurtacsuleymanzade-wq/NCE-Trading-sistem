import time

from capital_flow.engine import normalize_agg_trade
from capital_flow.horizon_probability import (
    build_horizon_forecast,
    classify_realized_zone,
    observer_metrics,
)
from capital_flow.http_api import create_router
from capital_flow.storage import CapitalFlowStore


def trades(now_ms, long_bias=True):
    rows = []
    for index in range(360):
        stamp = now_ms - (359 - index) * 1000
        up = index % 5 != 0 if long_bias else index % 5 == 0
        price = 100 + index * (.001 if long_bias else -.001)
        rows.append(normalize_agg_trade({"T": stamp, "a": index + 1, "p": str(price), "q": "2", "m": not up, "s": "BTCUSDT"}, "futures"))
    return rows


def test_timeframes_and_horizons_are_independent_and_normalized():
    now = int(time.time()) * 1000
    payload = build_horizon_forecast(
        trades(now), current_price=100.36,
        book={"bids": [[100.35, 80]], "asks": [[100.37, 20]]}, now_ms=now,
    )
    assert set(payload["timeframeStates"]) == {"1s", "1m", "5m"}
    assert [row["horizonMinutes"] for row in payload["horizons"]] == [5, 10, 30]
    assert payload["rules"]["scoreIsProbability"] is False
    for row in payload["horizons"]:
        assert abs(sum(row["distribution"].values()) - 1) < .001
        assert row["probabilityStatus"] == "MODEL_ESTIMATE"
        assert row["zone"]["frozenAtPrediction"] is True
        assert row["weights"] != payload["horizons"][0]["weights"] or row["horizonMinutes"] == 5


def test_one_second_shock_changes_near_horizon_more_than_thirty_minute_context():
    now = int(time.time()) * 1000
    baseline = trades(now, long_bias=True)
    shocked = list(baseline)
    # A final aggressive sell is intentionally large; it is a trigger, not a
    # rewrite of the full five-minute regime.
    shocked.append(normalize_agg_trade({"T": now, "a": 9999, "p": "99.5", "q": "100", "m": True, "s": "BTCUSDT"}, "futures"))
    a = build_horizon_forecast(baseline, current_price=100.36, now_ms=now)
    b = build_horizon_forecast(shocked, current_price=99.5, now_ms=now)
    delta5 = abs(a["horizons"][0]["distribution"]["UP"] - b["horizons"][0]["distribution"]["UP"])
    delta30 = abs(a["horizons"][2]["distribution"]["UP"] - b["horizons"][2]["distribution"]["UP"])
    assert delta5 > delta30


def test_calibration_requires_one_hundred_samples():
    now = int(time.time()) * 1000
    calibration = {"5": [{"signal_low": -1, "signal_high": 1, "up_rate": .7, "range_rate": .2, "down_rate": .1, "sample_size": 99}]}
    value = build_horizon_forecast(trades(now), current_price=100.36, calibration=calibration, now_ms=now)
    assert value["horizons"][0]["probabilityStatus"] == "MODEL_ESTIMATE"
    calibration["5"][0]["sample_size"] = 100
    value = build_horizon_forecast(trades(now), current_price=100.36, calibration=calibration, now_ms=now)
    assert value["horizons"][0]["probabilityStatus"] == "CALIBRATED"
    assert value["horizons"][0]["distribution"]["UP"] == .7


def test_observer_uses_brier_logloss_ece_and_sample_count():
    rows = [
        {"horizonMinutes": 5, "distribution": {"UP": .7, "RANGE": .2, "DOWN": .1}, "actualClass": "UP"},
        {"horizonMinutes": 5, "distribution": {"UP": .2, "RANGE": .3, "DOWN": .5}, "actualClass": "DOWN"},
    ]
    metrics = observer_metrics(rows)
    assert metrics["resolvedSamples"] == 2
    assert metrics["byHorizon"]["5"]["accuracy"] == 1
    assert 0 <= metrics["byHorizon"]["5"]["brierScore"] <= 1
    assert metrics["byHorizon"]["5"]["logLoss"] > 0


def test_store_freezes_and_resolves_predictions(tmp_path):
    db = tmp_path / "observer.sqlite3"
    store = CapitalFlowStore(db)
    due = int(time.time()) * 1000
    payload = {
        "schemaVersion": "probability-horizons-v2", "symbol": "BTCUSDT", "timestamp": due - 300_000,
        "currentPrice": 100, "featureHash": "abc",
        "horizons": [{"predictionId": "p1", "horizonMinutes": 5, "resolvedAtMs": due, "distribution": {"UP": .6, "RANGE": .2, "DOWN": .2}, "neutralZone": {"low": 99.9, "high": 100.1}}],
    }
    assert store.record_horizon_forecast(payload) == 1
    trade = normalize_agg_trade({"T": due + 100, "a": 1, "p": "101", "q": "1", "m": False, "s": "BTCUSDT"}, "futures")
    store.insert_trade(trade, due + 100)
    assert store.resolve_horizon_predictions("BTCUSDT", due + 1000) == 1
    assert store.horizon_observer_rows()[0]["actualClass"] == "UP"
    store.close()


def test_api_exposes_horizon_and_observer_routes(tmp_path):
    router = create_router(str(tmp_path / "api.sqlite3"))
    paths = {route.path for route in router.routes}
    assert "/probability-map/horizons" in paths
    assert "/probability-map/observer" in paths


def test_realized_class_uses_frozen_neutral_zone():
    prediction = {"originPrice": 100, "neutralZone": {"low": 99, "high": 101}}
    assert classify_realized_zone(prediction, 102) == "UP"
    assert classify_realized_zone(prediction, 100.5) == "RANGE"
    assert classify_realized_zone(prediction, 98) == "DOWN"

