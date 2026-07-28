#!/usr/bin/env python3
"""RED tests for Nur's simple order-flow event system.

These tests intentionally describe the requested behavior before implementation.
They use synthetic quote-volume bars and must fail on the previous NAI V2 event system.
"""
import inspect
from nai_v2_verifier import calc_nai, synth_bar


def event_names(rows):
    return [e for r in calc_nai(rows) for e in r.get("events", [])]


def warmup(n=20):
    return [synth_bar(i, 1000, 1000, o=100, tr=10, response=0.0, closed=True) for i in range(n)]


def test_buy_strong_price_flat_produces_ba_short_code():
    rows = warmup() + [synth_bar(20, 20000, 100, o=100, tr=10, response=0.02, closed=True)]
    assert "BA" in event_names(rows)


def test_sell_strong_price_flat_produces_sa_short_code():
    rows = warmup() + [synth_bar(20, 100, 20000, o=100, tr=10, response=-0.02, closed=True)]
    assert "SA" in event_names(rows)


def test_buy_strong_price_drops_produces_bf_not_ba():
    rows = warmup() + [synth_bar(20, 20000, 100, o=100, tr=10, response=-0.30, closed=True)]
    events = event_names(rows)
    assert "BF" in events
    assert "BA" not in events


def test_sell_strong_price_rises_produces_sf_not_sa():
    rows = warmup() + [synth_bar(20, 100, 20000, o=100, tr=10, response=0.30, closed=True)]
    events = event_names(rows)
    assert "SF" in events
    assert "SA" not in events


def test_ba_or_bf_low_break_confirms_bx_only_on_break_candle():
    rows = warmup() + [synth_bar(20, 20000, 100, o=100, tr=10, response=-0.30, closed=True)]
    candidate_low = rows[-1]["l"]
    rows += [synth_bar(21, 1000, 1000, o=97, tr=10, response=0.0, closed=True)]
    rows[-1]["l"] = candidate_low - 1
    rows[-1]["c"] = candidate_low - 0.5
    calc = calc_nai(rows)
    assert "BX" in calc[-1]["events"]
    assert "BX" not in calc[-2]["events"]


def test_sa_or_sf_high_break_confirms_sx_only_on_break_candle():
    rows = warmup() + [synth_bar(20, 100, 20000, o=100, tr=10, response=0.30, closed=True)]
    candidate_high = rows[-1]["h"]
    rows += [synth_bar(21, 1000, 1000, o=103, tr=10, response=0.0, closed=True)]
    rows[-1]["h"] = candidate_high + 1
    rows[-1]["c"] = candidate_high + 0.5
    calc = calc_nai(rows)
    assert "SX" in calc[-1]["events"]
    assert "SX" not in calc[-2]["events"]


def test_buy_exhaustion_does_not_require_single_candle_85_to_55_drop():
    rows = warmup()
    rows += [synth_bar(20+i, 30000, 100, o=100+i*0.1, tr=10, response=0.04, closed=True) for i in range(3)]
    rows += [synth_bar(23+i, 1500, 1200, o=100.2, tr=10, response=-0.01, closed=True) for i in range(3)]
    assert "BE" in event_names(rows)


def test_sell_exhaustion_does_not_require_single_candle_85_to_55_drop():
    rows = warmup()
    rows += [synth_bar(20+i, 100, 30000, o=100-i*0.1, tr=10, response=-0.04, closed=True) for i in range(3)]
    rows += [synth_bar(23+i, 1200, 1500, o=99.8, tr=10, response=0.01, closed=True) for i in range(3)]
    assert "SE" in event_names(rows)


def test_same_aggression_episode_has_single_initiative_marker():
    rows = warmup() + [synth_bar(20+i, 100, 30000, o=100-i, tr=10, response=-0.60, closed=True) for i in range(5)]
    events = event_names(rows)
    assert events.count("IS") == 1


def test_final_events_never_appear_on_open_candle():
    rows = warmup() + [synth_bar(20, 20000, 100, o=100, tr=10, response=-0.30, closed=False)]
    assert calc_nai(rows)[-1]["events"] == []


def test_one_second_absorption_duration_counts_single_episode():
    rows = []
    for i in range(10):
        if i < 6:
            rows.append(synth_bar(i, 20000, 100, o=100, tr=10, response=0.02, closed=True))
        else:
            rows.append(synth_bar(i, 1000, 1000, o=100, tr=10, response=0.0, closed=True))
    calc = calc_nai(rows, timeframe="1s")
    all_events = [e for r in calc for e in r.get("events", [])]
    duration_events = [e for e in all_events if e.startswith("BA ")]
    assert "BA" not in all_events
    assert duration_events == ["BA 5s", "BA 6s", "BA 7s", "BA 8s", "BA 9s", "BA 10s"]


def test_refresh_replay_keeps_marker_signature_stable():
    rows = warmup() + [synth_bar(20, 20000, 100, o=100, tr=10, response=-0.30, closed=True)]
    sig1 = [(r["t"], tuple(r.get("events", []))) for r in calc_nai(rows)]
    sig2 = [(r["t"], tuple(r.get("events", []))) for r in calc_nai(list(rows))]
    assert sig1 == sig2
