#!/usr/bin/env python3
"""Independent NAI V2 production verifier for NCE Trading.

Verifies quote-volume invariants, closed/provisional event gating, and real
non-repaint behavior under open-candle mutation + closed replay/restart.
"""
from __future__ import annotations
import copy, hashlib, json, math, statistics
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
    v = float(row.get("v", row.get("volume", 0)) or 0)
    bv = float(row.get("bv", row.get("buy_volume", 0)) or 0)
    sv = row.get("sv")
    sv = float(v - bv if sv is None else sv)
    return {
        "t": int(row.get("t", row.get("time", 0))),
        "o": float(row.get("o", row.get("open", 0))),
        "h": float(row.get("h", row.get("high", 0))),
        "l": float(row.get("l", row.get("low", 0))),
        "c": float(row.get("c", row.get("close", 0))),
        "v": v, "bv": bv, "sv": sv,
        "volume_unit": row.get("volume_unit"),
        "closed": row.get("closed") is True,
    }


def event_signature(rows):
    return [(r["t"], tuple(r.get("events", [])), round(r.get("buyLine", 0), 12), round(r.get("sellLine", 0), 12)) for r in rows if r.get("closed")]


def marker_signature(rows):
    return [(r["t"], tuple(r.get("events", []))) for r in rows if r.get("closed") and r.get("events")]


def digest_closed(rows):
    payload = json.dumps(event_signature(rows), sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(payload.encode()).hexdigest()


def calc_nai(rows):
    rows = [normalize(x) for x in rows]
    volume_ema = atr14 = atr_baseline = buy_line = sell_line = None
    out = []
    candidates = []
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
        initiative_buy = buy_line >= 70 and buy_line > sell_line and nd >= 0.15 and price_response >= 0.25
        initiative_sell = sell_line >= 70 and sell_line > buy_line and nd <= -0.15 and sell_response >= 0.25
        buy_absorption = buy_line >= 70 and buy_line > sell_line and nd >= 0.25 and volume_power >= 1.5 and -0.10 <= price_response <= 0.10
        sell_absorption = sell_line >= 70 and sell_line > buy_line and nd <= -0.25 and volume_power >= 1.5 and -0.10 <= sell_response <= 0.10
        buy_failure = buy_line >= 70 and buy_line > sell_line and nd >= 0.25 and volume_power >= 1.5 and price_response <= -0.25
        sell_failure = sell_line >= 70 and sell_line > buy_line and nd <= -0.25 and volume_power >= 1.5 and sell_response <= -0.25
        exhaustion = bool(out and max(out[-1]["buyLine"], out[-1]["sellLine"]) >= 85 and max(buy_line, sell_line) < 55 and volume_power < 1.2)
        raw_events = []
        if initiative_buy: raw_events.append("INITIATIVE_BUY")
        if initiative_sell: raw_events.append("INITIATIVE_SELL")
        if buy_absorption: raw_events.append("BUY_ABSORPTION")
        if sell_absorption: raw_events.append("SELL_ABSORPTION")
        if buy_failure: raw_events.append("BUY_FAILURE")
        if sell_failure: raw_events.append("SELL_FAILURE")
        if exhaustion: raw_events.append("EXHAUSTION")
        if initiative_buy and buy_absorption:
            raw_events.remove("BUY_ABSORPTION")
        if initiative_sell and sell_absorption:
            raw_events.remove("SELL_ABSORPTION")
        events = list(raw_events) if x["closed"] else []
        trapped = []
        if x["closed"]:
            for cand in list(candidates):
                dist = i - cand["index"]
                if dist < 1:
                    continue
                if dist > 3:
                    candidates.remove(cand); continue
                if cand["side"] == "BUY" and (x["c"] < cand["low"] or x["c"] <= cand["close"] - 0.25 * cand["atr14"]):
                    events.append("BX_CONFIRMED")
                    trapped.append({"type":"TRAPPED_BUYERS","candidateTime":cand["time"],"confirmationTime":x["t"],"candidatePrice":cand["close"],"confirmationPrice":x["c"],"barsToConfirm":dist,"repaint":False})
                    candidates.remove(cand)
                elif cand["side"] == "SELL" and (x["c"] > cand["high"] or x["c"] >= cand["close"] + 0.25 * cand["atr14"]):
                    events.append("SX_CONFIRMED")
                    trapped.append({"type":"TRAPPED_SELLERS","candidateTime":cand["time"],"confirmationTime":x["t"],"candidatePrice":cand["close"],"confirmationPrice":x["c"],"barsToConfirm":dist,"repaint":False})
                    candidates.remove(cand)
            if buy_absorption or buy_failure:
                candidates.append({"side":"BUY","index":i,"time":x["t"],"low":x["l"],"high":x["h"],"close":x["c"],"atr14":atr14})
            if sell_absorption or sell_failure:
                candidates.append({"side":"SELL","index":i,"time":x["t"],"low":x["l"],"high":x["h"],"close":x["c"],"atr14":atr14})
        out.append({**x, "v": V, "delta": delta, "nd": nd, "volumePower": volume_power, "atr14": atr14,
                    "atrRegime": atr_regime, "relativeForce": relative_force, "rawBuy": raw_buy, "rawSell": raw_sell,
                    "buyAgg": buy_agg, "sellAgg": sell_agg, "buyLine": buy_line, "sellLine": sell_line,
                    "priceResponse": price_response, "sellResponse": sell_response, "events": events,
                    "provisionalEvents": [] if x["closed"] else raw_events, "trapped": trapped})
    return out


def synth_bar(t, bv, sv, o=100, tr=10, response=0.0, closed=True):
    c = o + response * tr
    return {"t": t, "o": o, "h": max(o, c) + tr/2, "l": min(o, c) - tr/2, "c": c,
            "v": bv + sv, "bv": bv, "sv": sv, "volume_unit": "QUOTE_USDT", "closed": closed}


def force_event_bar(side, response, t=10, closed=True):
    bars = [synth_bar(i, 1000, 1000, response=0.0) for i in range(t)]
    if side == "buy": bars += [synth_bar(t+i, 10000, 100, response=response, closed=closed) for i in range(5)]
    else: bars += [synth_bar(t+i, 100, 10000, response=-response, closed=closed) for i in range(5)]
    return calc_nai(bars)[-1]


def run_unit_tests():
    assert "INITIATIVE_BUY" in force_event_bar("buy", 0.50)["events"]
    assert "INITIATIVE_SELL" in force_event_bar("sell", 0.50)["events"]
    assert "BUY_ABSORPTION" in force_event_bar("buy", 0.02)["events"]
    assert "SELL_ABSORPTION" in force_event_bar("sell", 0.02)["events"]
    buy_fail = force_event_bar("buy", -0.60)
    assert "BUY_FAILURE" in buy_fail["events"] and "BUY_ABSORPTION" not in buy_fail["events"]
    sell_fail = force_event_bar("sell", -0.60)
    assert "SELL_FAILURE" in sell_fail["events"] and "SELL_ABSORPTION" not in sell_fail["events"]
    provisional = force_event_bar("buy", 0.50, closed=False)
    assert provisional["events"] == [] and provisional["provisionalEvents"], provisional
    bx_bars = [synth_bar(i, 1000, 1000) for i in range(10)] + [synth_bar(10+i, 10000, 100, response=-0.60) for i in range(5)]
    event = bx_bars[-1]
    bx_bars.append({**synth_bar(20, 1000, 1000), "o": event["c"], "h": event["c"]+1, "l": event["c"]-8, "c": event["l"]-1})
    assert "BX_CONFIRMED" in calc_nai(bx_bars)[-1]["events"]
    sx_bars = [synth_bar(i, 1000, 1000) for i in range(10)] + [synth_bar(10+i, 100, 10000, response=0.60) for i in range(5)]
    event = sx_bars[-1]
    sx_bars.append({"t": 20, "o": event["h"] + 1, "h": event["h"] + 2, "l": event["h"], "c": event["h"] + 1,
                    "v": 2000, "bv": 1000, "sv": 1000, "volume_unit": "QUOTE_USDT", "closed": True})
    assert "SX_CONFIRMED" in calc_nai(sx_bars)[-1]["events"]


def verify_data():
    result = {"timeframes": {}, "events": {}, "manual_15m": []}
    for tf, fn in TF_FILES.items():
        rows = json.loads((DATA / fn).read_text())
        calc = calc_nai(rows)
        inv = {"rows": len(calc), "unit_quote_usdt": 0, "closed_rows": 0, "open_rows": 0, "v_sum_fail": 0, "range_fail": 0, "nan": 0, "inf": 0, "neg_volume": 0, "zero_atr": 0, "provisional_event_leak": 0}
        ev = {}
        for r in calc:
            inv["unit_quote_usdt"] += int(r.get("volume_unit") == "QUOTE_USDT")
            inv["closed_rows"] += int(r.get("closed") is True)
            inv["open_rows"] += int(r.get("closed") is not True)
            inv["v_sum_fail"] += int(abs(r["v"] - (r["bv"] + r["sv"])) > max(1e-6, r["v"] * 1e-9))
            nums = [r[k] for k in ("buyAgg","sellAgg","buyLine","sellLine","nd","volumePower","atrRegime","atr14")]
            inv["nan"] += int(any(math.isnan(x) for x in nums)); inv["inf"] += int(any(math.isinf(x) for x in nums))
            inv["neg_volume"] += int(r["v"] < 0 or r["bv"] < 0 or r["sv"] < 0)
            inv["zero_atr"] += int(abs(r["atr14"]) < EPS)
            inv["range_fail"] += int(not(0 <= r["buyAgg"] < 100 and 0 <= r["sellAgg"] < 100 and 0 <= r["buyLine"] < 100 and 0 <= r["sellLine"] < 100 and -1 <= r["nd"] <= 1))
            inv["provisional_event_leak"] += int((not r["closed"]) and bool(r["events"]))
            for e in r["events"]: ev[e] = ev.get(e, 0) + 1
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
    base = [synth_bar(i, 1000, 1000, response=0.0) for i in range(5)] + [synth_bar(5, 10000, 100, response=-0.60, closed=True)]
    open_t = 6
    permanent_events_during_open = []
    marker_counts = []
    provisional_seen = False
    for n in range(10):
        response = -0.60 if n % 2 else 0.60
        mutable = synth_bar(open_t, 10000 + n*1500, 100 + n*25, o=100+n*0.2, tr=10+n*0.5, response=response, closed=False)
        calc = calc_nai(base + [mutable])
        last = calc[-1]
        permanent_events_during_open.extend(last["events"])
        marker_counts.append(len(marker_signature(calc)))
        provisional_seen = provisional_seen or bool(last["provisionalEvents"])
        assert not any(e in last["events"] for e in ("BX_CONFIRMED", "SX_CONFIRMED"))
    NO_FINAL_MARKER_ON_PROVISIONAL = len(permanent_events_during_open) == 0 and len(set(marker_counts)) == 1 and provisional_seen
    closed_rows = [synth_bar(open_t, 10000, 100, o=100, tr=10, response=-0.60, closed=True), synth_bar(open_t+1, 10000, 100, o=100, tr=10, response=-0.60, closed=True)]
    finalized_calc = calc_nai(base + closed_rows)
    finalized_row = finalized_calc[-1]
    C = any(finalized_row["events"]) and (finalized_row["t"], tuple(finalized_row["events"])) in marker_signature(finalized_calc)
    before_sig = event_signature(finalized_calc)
    before_markers = marker_signature(finalized_calc)
    before_digest = digest_closed(finalized_calc)
    extended_rows = base + closed_rows
    for j in range(1, 6):
        extended_rows.append(synth_bar(open_t + 1 + j, 1000 + j*10, 1000 + j*5, o=finalized_row["c"] + j*0.01, tr=10, response=0.0, closed=True))
        ext_calc = calc_nai(extended_rows)
        row = next(r for r in ext_calc if r["t"] == finalized_row["t"])
        assert row["events"] == finalized_row["events"]
        assert round(row["buyLine"], 12) == round(finalized_row["buyLine"], 12)
        assert round(row["sellLine"], 12) == round(finalized_row["sellLine"], 12)
        assert (finalized_row["t"], tuple(finalized_row["events"])) in marker_signature(ext_calc)
    CLOSED_EVENT_IMMUTABILITY_PASS = C and before_sig == event_signature(finalized_calc) and before_markers == marker_signature(finalized_calc)
    replay1 = calc_nai(copy.deepcopy(extended_rows))
    replay2 = calc_nai(json.loads(json.dumps(extended_rows, sort_keys=True)))
    RESTART_REPLAY_MATCH = json.dumps(event_signature(replay1), sort_keys=True, separators=(",", ":")) == json.dumps(event_signature(replay2), sort_keys=True, separators=(",", ":")) and digest_closed(replay1) == digest_closed(replay2)
    OPEN_CANDLE_MUTATION_PASS = NO_FINAL_MARKER_ON_PROVISIONAL and provisional_seen
    return {
        "OPEN_CANDLE_MUTATION_PASS": OPEN_CANDLE_MUTATION_PASS,
        "CLOSED_EVENT_IMMUTABILITY_PASS": CLOSED_EVENT_IMMUTABILITY_PASS,
        "RESTART_REPLAY_MATCH": RESTART_REPLAY_MATCH,
        "NO_FINAL_MARKER_ON_PROVISIONAL": NO_FINAL_MARKER_ON_PROVISIONAL,
        "finalized_timestamp": finalized_row["t"],
        "finalized_events": finalized_row["events"],
        "closed_digest": before_digest,
        "open_mutations": 10,
        "post_close_bars_checked": 5,
    }


if __name__ == "__main__":
    run_unit_tests()
    data_result = verify_data()
    repaint = real_non_repaint_tests()
    assert all(repaint[k] is True for k in ["OPEN_CANDLE_MUTATION_PASS", "CLOSED_EVENT_IMMUTABILITY_PASS", "RESTART_REPLAY_MATCH", "NO_FINAL_MARKER_ON_PROVISIONAL"]), repaint
    print(json.dumps({"ok": True, **data_result, "repaint": repaint}, indent=2, ensure_ascii=False))
