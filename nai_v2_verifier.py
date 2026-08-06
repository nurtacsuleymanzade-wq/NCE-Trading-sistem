#!/usr/bin/env python3
"""Independent NAI V2 production verifier for NCE Trading.

Verifies quote-volume invariants, closed/provisional event gating, and real
non-repaint behavior under open-candle mutation + closed replay/restart.
"""
from __future__ import annotations
import copy, hashlib, json, math, os, subprocess, sys, tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parent
DATA = REPO / "data"
EPS = 1e-9
TF_FILES = {
    "1s": "bars_1s.json", "1m": "bars_1m.json", "5m": "bars_5m.json",
    "15m": "bars_15m.json", "30m": "bars_30m.json", "1h": "bars_1h.json",
    "4h": "bars_4h.json", "1D": "bars_1d.json", "3D": "bars_3d.json",
}


def ema(prev, value, length):
    alpha = 2 / (length + 1)
    return value if prev is None else prev + alpha * (value - prev)


def rma(prev, value, length):
    return value if prev is None else ((prev * (length - 1)) + value) / length


def clamp(x, lo, hi):
    return max(lo, min(hi, x))


def normalize(row):
    raw_v = float(row.get("v", row.get("volume", 0)) or 0)
    c = float(row.get("c", row.get("close", 0)) or 0)
    unit = str(row.get("volume_unit", ""))
    quote_v = float(row.get("quote_v", row.get("quote_volume", raw_v * c if "BASE" in unit else raw_v)) or 0)
    base_v = float(row.get("base_v", row.get("base_volume", raw_v if "BASE" in unit else quote_v / (c or 1))) or 0)
    bv = float(row.get("bv", row.get("buy_volume", 0)) or 0)
    sv = row.get("sv")
    sv = float(quote_v - bv if sv is None else sv)
    return {
        "t": int(row.get("t", row.get("time", 0))),
        "o": float(row.get("o", row.get("open", 0))),
        "h": float(row.get("h", row.get("high", 0))),
        "l": float(row.get("l", row.get("low", 0))),
        "c": c,
        "v": quote_v, "quote_v": quote_v, "base_v": base_v, "bv": bv, "sv": sv,
        "volume_unit": "QUOTE_USDT",
        "closed": row.get("closed") is True,
    }


def event_signature(rows):
    return [(r["t"], tuple(r.get("events", [])), round(r.get("buyLine", 0), 12), round(r.get("sellLine", 0), 12)) for r in rows if r.get("closed")]


def marker_signature(rows):
    return [(r["t"], tuple(r.get("events", []))) for r in rows if r.get("closed") and r.get("events")]


def snapshot_closed(rows):
    snap = []
    for r in rows:
        if not r.get("closed"):
            continue
        snap.append({
            "timestamp": r["t"],
            "OHLC": [round(r["o"], 12), round(r["h"], 12), round(r["l"], 12), round(r["c"], 12)],
            "bv": round(r["bv"], 8),
            "sv": round(r["sv"], 8),
            "BuyLine": round(r.get("buyLine", 0), 12),
            "SellLine": round(r.get("sellLine", 0), 12),
            "events": list(r.get("events", [])),
            "markers": list(r.get("events", [])) if r.get("events") else [],
            "BX_SX": [e for e in r.get("events", []) if e in ("BX_CONFIRMED", "SX_CONFIRMED")],
            "trapped": r.get("trapped", []),
        })
    return snap


def write_calc_snapshot(in_path, out_path):
    rows = json.loads(Path(in_path).read_text())
    Path(out_path).write_text(json.dumps(snapshot_closed(calc_nai(rows)), sort_keys=True, separators=(",", ":")))


def digest_closed(rows):
    payload = json.dumps(event_signature(rows), sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(payload.encode()).hexdigest()


def calc_nai(rows, timeframe=None):
    """Calculate NAI lines and simple order-flow events.

    Keeps the original quote-volume math and EMA5 visual lines. Event logic is
    intentionally simple: raw aggression can trigger fast events while smoothed
    BuyLine/SellLine is used for visual dominance/trend confirmation.
    """
    rows = [normalize(x) for x in rows]
    volume_ema = atr14 = atr_baseline = buy_line = sell_line = None
    out = []
    candidates = []
    initiative_episode = None
    buy_was_strong = False
    sell_was_strong = False
    buy_peak = None
    sell_peak = None
    buy_peak_high = None
    sell_peak_low = None
    one_s_buy_window = []
    one_s_sell_window = []
    one_s_buy_episode = 0
    one_s_sell_episode = 0
    buy_failure_episode = False
    sell_failure_episode = False
    buy_absorption_episode = False
    sell_absorption_episode = False
    buy_window_active_prev = False
    sell_window_active_prev = False
    for i, x in enumerate(rows):
        pc = out[i - 1]["c"] if i else (x["o"] or x["c"])
        tr = max(x["h"] - x["l"], abs(x["h"] - pc), abs(x["l"] - pc))
        atr14 = rma(atr14, tr, 14)
        atr_baseline = ema(atr_baseline, atr14, 300)
        bv = max(0.0, x["bv"])
        sv = max(0.0, x["sv"])
        V = bv + sv
        delta = bv - sv
        nd = delta / (V + EPS)
        volume_ema = ema(volume_ema, V, 300)
        volume_power = clamp(V / ((volume_ema or V) + EPS), 0, 5)
        atr_regime = clamp(atr14 / ((atr_baseline or atr14) + EPS), 0.5, 3)
        relative_force = volume_power / math.sqrt(atr_regime)
        buy_share = bv / (V + EPS)
        sell_share = sv / (V + EPS)
        raw_buy = buy_share * (1 + max(nd, 0)) * relative_force
        raw_sell = sell_share * (1 + max(-nd, 0)) * relative_force
        buy_agg = 100 * (1 - math.exp(-raw_buy))
        sell_agg = 100 * (1 - math.exp(-raw_sell))
        buy_line = ema(buy_line, buy_agg, 5)
        sell_line = ema(sell_line, sell_agg, 5)
        price_response = (x["c"] - x["o"]) / (atr14 + EPS)
        sell_response = (x["o"] - x["c"]) / (atr14 + EPS)

        buy_strong = buy_agg >= 70 or (buy_line > sell_line and nd >= 0.20 and volume_power >= 1.30)
        sell_strong = sell_agg >= 70 or (sell_line > buy_line and nd <= -0.20 and volume_power >= 1.30)
        buy_dominant = buy_line > sell_line and buy_agg > sell_agg
        sell_dominant = sell_line > buy_line and sell_agg > buy_agg
        buy_absorption = buy_strong and buy_dominant and price_response <= 0.10 and price_response >= -0.20
        sell_absorption = sell_strong and sell_dominant and sell_response <= 0.10 and sell_response >= -0.20
        buy_failure = buy_strong and buy_dominant and price_response <= -0.20
        sell_failure = sell_strong and sell_dominant and price_response >= 0.20
        initiative_buy = buy_strong and buy_dominant and price_response >= 0.25
        initiative_sell = sell_strong and sell_dominant and sell_response >= 0.25

        # Reset initiative episode after neutralization or dominance flip.
        if initiative_episode == "BUY" and (sell_line >= buy_line or abs(buy_line - sell_line) < 5):
            initiative_episode = None
        if initiative_episode == "SELL" and (buy_line >= sell_line or abs(buy_line - sell_line) < 5):
            initiative_episode = None

        raw_events = []
        if initiative_buy and initiative_episode != "BUY":
            raw_events.append("IB")
            initiative_episode = "BUY"
        elif initiative_sell and initiative_episode != "SELL":
            raw_events.append("IS")
            initiative_episode = "SELL"
        if buy_failure and initiative_episode == "BUY" and not buy_failure_episode:
            raw_events.append("BF")
            buy_failure_episode = True
        if not buy_failure or initiative_episode != "BUY":
            buy_failure_episode = False
        if buy_absorption and initiative_episode == "BUY" and not buy_absorption_episode:
            raw_events.append("BA")
            buy_absorption_episode = True
        if not buy_absorption or initiative_episode != "BUY":
            buy_absorption_episode = False
        if sell_failure and initiative_episode == "SELL" and not sell_failure_episode:
            raw_events.append("SF")
            sell_failure_episode = True
        if not sell_failure or initiative_episode != "SELL":
            sell_failure_episode = False
        if sell_absorption and initiative_episode == "SELL" and not sell_absorption_episode:
            raw_events.append("SA")
            sell_absorption_episode = True
        if not sell_absorption or initiative_episode != "SELL":
            sell_absorption_episode = False

        # 1-second absorption duration: last-10-second rolling detector.
        # If at least 5 seconds in the last 10 qualify, keep one running episode label.
        if timeframe == "1s":
            raw_events = [e for e in raw_events if e not in {"BA", "SA"}]
            one_s_buy_window.append(bool(buy_absorption))
            one_s_sell_window.append(bool(sell_absorption))
            one_s_buy_window = one_s_buy_window[-10:]
            one_s_sell_window = one_s_sell_window[-10:]
            buy_window_active = sum(one_s_buy_window) >= 5
            sell_window_active = sum(one_s_sell_window) >= 5
            if buy_window_active:
                one_s_buy_episode = max(one_s_buy_episode + 1, sum(one_s_buy_window))
            else:
                one_s_buy_episode = 0
            if sell_window_active:
                one_s_sell_episode = max(one_s_sell_episode + 1, sum(one_s_sell_window))
            else:
                one_s_sell_episode = 0
            if buy_window_active and not buy_window_active_prev:
                raw_events = [e for e in raw_events if e != "BA"] + [f"BA {one_s_buy_episode}s"]
            if sell_window_active and not sell_window_active_prev:
                raw_events = [e for e in raw_events if e != "SA"] + [f"SA {one_s_sell_episode}s"]
            buy_window_active_prev = buy_window_active
            sell_window_active_prev = sell_window_active

        # Exhaustion: strong side reached first, then decays/crosses while price fails new extreme.
        buy_exhaustion = False
        sell_exhaustion = False
        if buy_strong:
            buy_was_strong = True
            buy_peak = max(buy_peak or buy_agg, buy_agg, buy_line)
            buy_peak_high = max(buy_peak_high or x["h"], x["h"])
        elif buy_was_strong and buy_peak is not None:
            drop = buy_peak - max(buy_agg, buy_line)
            spread_close = (buy_line <= sell_line) or ((buy_line - sell_line) <= 8) or drop >= 30
            no_new_high = buy_peak_high is not None and x["h"] <= buy_peak_high + EPS
            buy_exhaustion = drop >= 18 and spread_close and no_new_high
            if buy_exhaustion or sell_line > buy_line:
                buy_was_strong = False
                buy_peak = None
                buy_peak_high = None
        if sell_strong:
            sell_was_strong = True
            sell_peak = max(sell_peak or sell_agg, sell_agg, sell_line)
            sell_peak_low = min(sell_peak_low if sell_peak_low is not None else x["l"], x["l"])
        elif sell_was_strong and sell_peak is not None:
            drop = sell_peak - max(sell_agg, sell_line)
            spread_close = (sell_line <= buy_line) or ((sell_line - buy_line) <= 8) or drop >= 30
            no_new_low = sell_peak_low is not None and x["l"] >= sell_peak_low - EPS
            sell_exhaustion = drop >= 18 and spread_close and no_new_low
            if sell_exhaustion or buy_line > sell_line:
                sell_was_strong = False
                sell_peak = None
                sell_peak_low = None
        if buy_exhaustion:
            raw_events.append("BE")
        if sell_exhaustion:
            raw_events.append("SE")

        events = list(raw_events) if x["closed"] else []
        trapped = []
        if x["closed"]:
            for cand in list(candidates):
                dist = i - cand["index"]
                if dist < 1:
                    continue
                if dist > 3:
                    candidates.remove(cand); continue
                if cand["side"] == "BUY" and x["l"] < cand["low"]:
                    events.append("BX")
                    trapped.append({"type":"TRAPPED_BUYERS","candidateTime":cand["time"],"confirmationTime":x["t"],"candidatePrice":cand["close"],"confirmationPrice":x["c"],"barsToConfirm":dist,"repaint":False})
                    candidates.remove(cand)
                elif cand["side"] == "SELL" and x["h"] > cand["high"]:
                    events.append("SX")
                    trapped.append({"type":"TRAPPED_SELLERS","candidateTime":cand["time"],"confirmationTime":x["t"],"candidatePrice":cand["close"],"confirmationPrice":x["c"],"barsToConfirm":dist,"repaint":False})
                    candidates.remove(cand)
            if buy_absorption or buy_failure:
                candidates.append({"side":"BUY","index":i,"time":x["t"],"low":x["l"],"high":x["h"],"close":x["c"],"atr14":atr14})
            if sell_absorption or sell_failure:
                candidates.append({"side":"SELL","index":i,"time":x["t"],"low":x["l"],"high":x["h"],"close":x["c"],"atr14":atr14})
        out.append({**x, "v": V, "delta": delta, "nd": nd, "volumePower": volume_power, "atr14": atr14,
                    "atrRegime": atr_regime, "relativeForce": relative_force, "rawBuy": raw_buy, "rawSell": raw_sell,
                    "buyAgg": buy_agg, "sellAgg": sell_agg, "buyLine": buy_line, "sellLine": sell_line,
                    "buyStrong": buy_strong, "sellStrong": sell_strong, "buyDominant": buy_dominant, "sellDominant": sell_dominant,
                    "priceResponse": price_response, "sellResponse": sell_response, "events": events,
                    "provisionalEvents": [] if x["closed"] else raw_events, "trapped": trapped})

    return out

def synth_bar(t, bv, sv, o=100, tr=10, response=0.0, closed=True):
    c = o + response * tr
    return {"t": t, "o": o, "h": max(o, c) + tr/2, "l": min(o, c) - tr/2, "c": c,
            "v": bv + sv, "quote_v": bv + sv, "base_v": (bv + sv) / c, "bv": bv, "sv": sv, "volume_unit": "QUOTE_USDT", "closed": closed}


def force_event_bar(side, response, t=10, closed=True):
    bars = [synth_bar(i, 1000, 1000, response=0.0) for i in range(t)]
    warmup_response = 0.50 if side == "buy" else -0.50
    if side == "buy": bars += [synth_bar(t, 10000, 100, response=warmup_response, closed=closed)] + [synth_bar(t+i, 10000, 100, response=response, closed=closed) for i in range(1, 5)]
    else: bars += [synth_bar(t, 100, 10000, response=warmup_response, closed=closed)] + [synth_bar(t+i, 100, 10000, response=-response, closed=closed) for i in range(1, 5)]
    calc = calc_nai(bars)
    expected = {"buy": {"BA", "BF"}, "sell": {"SA", "SF"}}[side]
    return next((row for row in calc if any(event.split()[0] in expected for event in row["events"])), calc[-1])


def events_in(rows, timeframe=None):
    return [e for r in calc_nai(rows, timeframe=timeframe) for e in r["events"]]


def run_unit_tests():
    assert "IB" in events_in([synth_bar(i, 1000, 1000, response=0.0) for i in range(10)] + [synth_bar(10+i, 10000, 100, response=0.50) for i in range(5)])
    assert "IS" in events_in([synth_bar(i, 1000, 1000, response=0.0) for i in range(10)] + [synth_bar(10+i, 100, 10000, response=-0.50) for i in range(5)])
    assert "BA" in force_event_bar("buy", 0.02)["events"]
    assert "SA" in force_event_bar("sell", 0.02)["events"]
    buy_fail = force_event_bar("buy", -0.60)
    assert "BF" in buy_fail["events"] and "BA" not in buy_fail["events"]
    sell_fail = force_event_bar("sell", -0.60)
    assert "SF" in sell_fail["events"] and "SA" not in sell_fail["events"]
    provisional_rows = [synth_bar(i, 1000, 1000, response=0.0) for i in range(10)] + [synth_bar(10, 10000, 100, response=0.50, closed=False)]
    provisional = calc_nai(provisional_rows)[-1]
    assert provisional["events"] == [] and provisional["provisionalEvents"], provisional
    bx_bars = [synth_bar(i, 1000, 1000) for i in range(10)] + [synth_bar(10+i, 10000, 100, response=-0.60) for i in range(5)]
    event = bx_bars[-1]
    bx_bars.append({**synth_bar(20, 1000, 1000), "o": event["c"], "h": event["c"]+1, "l": event["l"]-1, "c": event["l"]-0.5})
    assert "BX" in calc_nai(bx_bars)[-1]["events"]
    sx_bars = [synth_bar(i, 1000, 1000) for i in range(10)] + [synth_bar(10+i, 100, 10000, response=0.60) for i in range(5)]
    event = sx_bars[-1]
    sx_bars.append({"t": 20, "o": event["h"] + 1, "h": event["h"] + 2, "l": event["h"], "c": event["h"] + 1,
                    "v": 2000, "bv": 1000, "sv": 1000, "volume_unit": "QUOTE_USDT", "closed": True})
    assert "SX" in calc_nai(sx_bars)[-1]["events"]


def verify_data():
    result = {"timeframes": {}, "events": {}, "examples": {}, "manual_15m": []}
    for tf, fn in TF_FILES.items():
        rows = json.loads((DATA / fn).read_text())
        calc = calc_nai(rows, timeframe=tf)
        inv = {"rows": len(calc), "unit_quote_usdt": 0, "closed_rows": 0, "open_rows": 0, "v_sum_fail": 0, "range_fail": 0, "nan": 0, "inf": 0, "neg_volume": 0, "zero_atr": 0, "provisional_event_leak": 0}
        ev = {}
        for idx, r in enumerate(calc):
            inv["unit_quote_usdt"] += int(r.get("volume_unit") == "QUOTE_USDT")
            inv["closed_rows"] += int(r.get("closed") is True)
            inv["open_rows"] += int(r.get("closed") is not True)
            inv["v_sum_fail"] += int(abs(r["v"] - (r["bv"] + r["sv"])) > max(1e-6, r["v"] * 1e-9))
            nums = [r[k] for k in ("buyAgg","sellAgg","buyLine","sellLine","nd","volumePower","atrRegime","atr14")]
            inv["nan"] += int(any(math.isnan(x) for x in nums)); inv["inf"] += int(any(math.isinf(x) for x in nums))
            inv["neg_volume"] += int(r["v"] < 0 or r["bv"] < 0 or r["sv"] < 0)
            # The first candle can legitimately have zero range; subsequent
            # candles must have a finite ATR regime.
            inv["zero_atr"] += int(idx > 0 and abs(r["atr14"]) < EPS)
            inv["range_fail"] += int(not(0 <= r["buyAgg"] < 100 and 0 <= r["sellAgg"] < 100 and 0 <= r["buyLine"] < 100 and 0 <= r["sellLine"] < 100 and -1 <= r["nd"] <= 1))
            inv["provisional_event_leak"] += int((not r["closed"]) and bool(r["events"]))
            for e in r["events"]:
                base_e = e.split()[0]
                ev[base_e] = ev.get(base_e, 0) + 1
                if base_e in {"BA", "SA", "BF", "SF", "BX", "SX", "BE", "SE"} and base_e not in result["examples"]:
                    result["examples"][base_e] = {
                        "tf": tf, "t": r["t"], "label": e, "o": round(r["o"], 2), "h": round(r["h"], 2), "l": round(r["l"], 2), "c": round(r["c"], 2),
                        "nd": round(r["nd"], 4), "volumePower": round(r["volumePower"], 4), "atr14": round(r["atr14"], 4),
                        "buyAgg": round(r["buyAgg"], 4), "sellAgg": round(r["sellAgg"], 4), "buyLine": round(r["buyLine"], 4), "sellLine": round(r["sellLine"], 4),
                        "priceResponse": round(r["priceResponse"], 4), "sellResponse": round(r["sellResponse"], 4), "closed": r["closed"],
                    }
        result["timeframes"][tf] = inv
        result["events"][tf] = ev
        if tf == "15m":
            for r in calc[-5:]:
                result["manual_15m"].append({k: round(r[k], 6) if isinstance(r.get(k), float) else r.get(k) for k in ["t","closed","bv","sv","v","delta","nd","volumePower","atr14","atrRegime","relativeForce","buyAgg","sellAgg","buyLine","sellLine","priceResponse","sellResponse","events","provisionalEvents"]})
        assert inv["unit_quote_usdt"] == inv["rows"], f"{tf} non quote volume rows"
        assert inv["closed_rows"] == inv["rows"] and inv["open_rows"] == 0, f"{tf} static JSON contains open rows"
        assert inv["v_sum_fail"] == inv["range_fail"] == inv["nan"] == inv["inf"] == inv["neg_volume"] == inv["zero_atr"] == inv["provisional_event_leak"] == 0, (tf, inv)
    return result


def real_non_repaint_tests():
    base = [synth_bar(i, 1000, 1000, response=0.0) for i in range(5)] + [synth_bar(5, 10000, 100, response=0.50, closed=True), synth_bar(6, 10000, 100, response=-0.60, closed=True)]
    open_t = 7
    permanent_events_during_open = []
    marker_counts = []
    provisional_seen = False
    baseline_marker_count = len(marker_signature(calc_nai(base)))
    for n in range(10):
        response = -0.60 if n % 2 else 0.60
        mutable = synth_bar(open_t, 10000 + n*1500, 100 + n*25, o=100+n*0.2, tr=10+n*0.5, response=response, closed=False)
        calc = calc_nai(base + [mutable])
        last = calc[-1]
        permanent_events_during_open.extend(last["events"])
        marker_counts.append(len(marker_signature(calc)))
        provisional_seen = provisional_seen or bool(last["provisionalEvents"])
        assert not any(e in last["events"] for e in ("BX_CONFIRMED", "SX_CONFIRMED"))
    NO_FINAL_MARKER_ON_PROVISIONAL = len(permanent_events_during_open) == 0 and all(count == baseline_marker_count for count in marker_counts)

    closed_rows = [
        synth_bar(open_t, 10000, 100, o=100, tr=10, response=-0.60, closed=True),
        synth_bar(open_t+1, 10000, 100, o=100, tr=10, response=-0.60, closed=True),
    ]
    extended_rows = base + closed_rows
    initial_calc = calc_nai(extended_rows)
    finalized_row = next((row for row in initial_calc if row["events"]), initial_calc[-1])
    target_ts = finalized_row["t"]
    closed_snapshot = next(x for x in snapshot_closed(initial_calc) if x["timestamp"] == target_ts)
    C = bool(finalized_row["events"]) and (finalized_row["t"], tuple(finalized_row["events"])) in marker_signature(initial_calc)

    # Real closed immutability: freeze one closed candle snapshot, append five later CLOSED candles,
    # recalculate full NAI, then locate the same timestamp and compare exact persisted fields.
    for j in range(1, 6):
        extended_rows.append(synth_bar(open_t + 1 + j, 1000 + j*10, 1000 + j*5, o=finalized_row["c"] + j*0.01, tr=10, response=0.0, closed=True))
    extended_calc = calc_nai(extended_rows)
    replayed_snapshot = next(x for x in snapshot_closed(extended_calc) if x["timestamp"] == target_ts)
    CLOSED_SNAPSHOT_IMMUTABILITY_PASS = C and closed_snapshot == replayed_snapshot

    # Real process restart/replay: run two separate Python interpreter processes, loading the same
    # closed-history JSON and serializing the same closed snapshot set.
    with tempfile.TemporaryDirectory(prefix="nce-restart-") as td:
        in_file = Path(td) / "history.json"
        out1 = Path(td) / "snapshot1.json"
        out2 = Path(td) / "snapshot2.json"
        in_file.write_text(json.dumps(extended_rows, sort_keys=True, separators=(",", ":")))
        subprocess.run([sys.executable, str(Path(__file__).resolve()), "--snapshot", str(in_file), str(out1)], check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        subprocess.run([sys.executable, str(Path(__file__).resolve()), "--snapshot", str(in_file), str(out2)], check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        PROCESS_RESTART_REPLAY_MATCH = out1.read_text() == out2.read_text()

    OPEN_CANDLE_MUTATION_PASS = NO_FINAL_MARKER_ON_PROVISIONAL
    return {
        "OPEN_CANDLE_MUTATION_PASS": OPEN_CANDLE_MUTATION_PASS,
        "CLOSED_SNAPSHOT_IMMUTABILITY_PASS": CLOSED_SNAPSHOT_IMMUTABILITY_PASS,
        "PROCESS_RESTART_REPLAY_MATCH": PROCESS_RESTART_REPLAY_MATCH,
        "NO_FINAL_MARKER_ON_PROVISIONAL": NO_FINAL_MARKER_ON_PROVISIONAL,
        "finalized_timestamp": target_ts,
        "finalized_events": finalized_row["events"],
        "closed_snapshot_fields": list(closed_snapshot.keys()),
        "closed_snapshot": closed_snapshot,
        "open_mutations": 10,
        "post_close_bars_checked": 5,
    }

if __name__ == "__main__":
    if len(sys.argv) == 4 and sys.argv[1] == "--snapshot":
        write_calc_snapshot(sys.argv[2], sys.argv[3])
        raise SystemExit(0)
    run_unit_tests()
    data_result = verify_data()
    repaint = real_non_repaint_tests()
    assert all(repaint[k] is True for k in ["OPEN_CANDLE_MUTATION_PASS", "CLOSED_SNAPSHOT_IMMUTABILITY_PASS", "PROCESS_RESTART_REPLAY_MATCH", "NO_FINAL_MARKER_ON_PROVISIONAL"]), repaint
    print(json.dumps({"ok": True, **data_result, "repaint": repaint}, indent=2, ensure_ascii=False))
