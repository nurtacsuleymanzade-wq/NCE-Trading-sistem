from capital_flow.engine import (
    AggTrade,
    CapitalFlowEngine,
    MetricStatus,
    aggregate_trades,
    classify_trade_size,
    compare_orderbooks,
    normalize_agg_trade,
    position_state,
    trader_size_thresholds,
)
from capital_flow.collector import DepthReconciler, SingleOwnerLock
from capital_flow.storage import CapitalFlowStore


def raw(ts=1_700_000_000_000, aid=1, price="100", qty="2", maker=False):
    return {"e": "aggTrade", "T": ts, "a": aid, "p": price, "q": qty, "m": maker, "s": "BTCUSDT"}


def test_binance_buyer_maker_mapping_is_explicit():
    assert normalize_agg_trade(raw(maker=False), "spot").aggressor_side == "BUY"
    assert normalize_agg_trade(raw(aid=2, maker=True), "spot").aggressor_side == "SELL"


def test_notional_and_trade_market_are_preserved():
    trade = normalize_agg_trade(raw(price="101.25", qty="0.5"), "futures")
    assert trade.notional_usd == 50.625
    assert trade.market == "futures"
    assert trade.quantity_btc == 0.5


def test_local_aggregation_delta_and_cvd_do_not_use_candle_volume():
    trades = [
        normalize_agg_trade(raw(aid=1, ts=1_700_000_000_001, price="100", qty="2", maker=False), "spot"),
        normalize_agg_trade(raw(aid=2, ts=1_700_000_001_000, price="101", qty="1", maker=True), "spot"),
        normalize_agg_trade(raw(aid=3, ts=1_700_000_002_000, price="102", qty="1", maker=False), "spot"),
    ]
    rows = aggregate_trades(trades, 5)
    assert len(rows) == 1
    assert rows[0]["buy_usd"] == 302
    assert rows[0]["sell_usd"] == 101
    assert rows[0]["delta_usd"] == 201
    assert rows[0]["cvd"] == 201
    assert rows[0]["trade_count"] == 3


def test_percentile_buckets_are_dynamic_and_separate():
    thresholds = trader_size_thresholds(range(1, 101), "spot")
    assert thresholds["status"] == MetricStatus.DERIVED.value
    assert classify_trade_size(thresholds["thresholds"]["p70"], thresholds) in {"MEDIUM", "LARGE"}
    assert classify_trade_size(thresholds["thresholds"]["p99_9"] + 1, thresholds) == "MEGA_WHALE_SIZE"
    assert trader_size_thresholds([], "futures")["status"] == MetricStatus.UNAVAILABLE.value


def test_position_state_returns_score_not_probability():
    state = position_state({"price_change": 1, "delta_oi": 1, "futures_delta": 1, "long_liquidation": 0, "short_liquidation": 0})
    assert state["status"] == MetricStatus.DERIVED.value
    assert 0 <= state["state_score"] <= 100
    assert state["calibrated_probability"] is None
    assert "probabilities" not in state


def test_depth_sequence_gap_is_not_silently_accepted():
    assert compare_orderbooks(None, {"bids": [[100, 1]], "asks": [[101, 1]]})["status"] == MetricStatus.UNAVAILABLE.value
    diff = compare_orderbooks({"bids": [[100, 1]], "asks": [[101, 1]]}, {"bids": [[100, 2]], "asks": [[101, 0]]})
    assert diff["status"] == MetricStatus.DERIVED.value
    assert diff["added_usd"] > 0
    assert diff["removed_usd"] > 0


def test_depth_reconciler_requires_contiguous_update_ids():
    book = DepthReconciler()
    book.seed({"lastUpdateId": 10, "bids": [[100, "1"]], "asks": [[101, "1"]]})
    assert book.apply({"U": 12, "u": 12, "bids": [], "asks": []}) is False
    assert book.needs_resync is True
    book = DepthReconciler()
    book.seed({"lastUpdateId": 10, "bids": [[100, "1"]], "asks": [[101, "1"]]})
    assert book.apply({"U": 10, "u": 11, "bids": [[100, "2"]], "asks": []}) is True
    assert book.last_update_id == 11


def test_raw_store_is_additive_and_round_trips_trade(tmp_path):
    store = CapitalFlowStore(tmp_path / "flow.sqlite3")
    trade = normalize_agg_trade(raw(), "spot")
    store.insert_trade(trade, 1_700_000_000_100)
    store.insert_trade(trade, 1_700_000_000_101)
    rows = store.trades("spot")
    assert len(rows) == 1
    assert store.health()["tables"]["spot_aggtrades_raw"] == 1
    store.close()


def test_single_owner_lock_rejects_second_instance(tmp_path):
    path = str(tmp_path / "collector.lock")
    first = SingleOwnerLock(path)
    second = SingleOwnerLock(path)
    assert first.acquire() is True
    assert second.acquire() is False
    second.release()
    first.release()


def test_engine_keeps_external_context_unavailable_instead_of_fabricating():
    engine = CapitalFlowEngine()
    engine.add_trade(normalize_agg_trade(raw(), "spot"))
    snapshot = engine.snapshot(300)
    assert snapshot["status"] == "PASS"
    assert snapshot["spot"]["metadata"]["status"] == MetricStatus.DERIVED.value
    assert snapshot["futures"]["metadata"]["status"] == MetricStatus.UNAVAILABLE.value
    assert snapshot["diagnosis"]["regime"] != "BROAD_ACCUMULATION"
