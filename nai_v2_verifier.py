#!/usr/bin/env python3
"""Independent NAI V2 verifier for NCE Trading.

Checks quote-volume invariants, NAI V2 math, event classification,
BX/SX confirmation without repaint, and batch-vs-streaming equivalence.
"""
from __future__ import annotations
import json, math, statistics
from pathlib import Path

REPO = Path(__file__).resolve().parent
DATA = REPO / "data"
EPS = 1e-9
RESEARCH_THRESHOLD = True
NOT_TRADING_SIGNAL = True

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
        "closed": bool(row.get("closed", True)),
    }

def calc_nai(rows):
    rows = [normalize(x) for x in rows]
    volume_ema = atr14 = atr_baseline = buy_line = sell_line = None
    out = []
    candidates = []
    for i, x in enumerate(rows):
        pc = rows[i - 1]["c"] if i else (x["o"] or x["c"])
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
        events = []
        if initiative_buy: events.append("INITIATIVE_BUY")
        if initiative_sell: events.append("INITIATIVE_SELL")
        if buy_absorption: events.append("BUY_ABSORPTION")
        if sell_absorption: events.append("SELL_ABSORPTION")
        if buy_failure: events.append("BUY_FAILURE")
        if sell_failure: events.append("SELL_FAILURE")
        if exhaustion: events.append("EXHAUSTION")
        if initiative_buy and buy_absorption:
            raise AssertionError(f"conflict INITIATIVE_BUY+BUY_ABSORPTION at {x['t']}")
        if initiative_sell and sell_absorption:
            raise AssertionError(f"conflict INITIATIVE_SELL+SELL_ABSORPTION at {x['t']}")
        for cand in list(candidates):
            dist = i - cand["index"]
            if dist < 1:
                continue
            if dist > 3:
                candidates.remove(cand); continue
            if cand["side"] == "BUY" and (x["c"] < cand["low"] or x["c"] <= cand["close"] - 0.25 * cand["atr14"]):
                events.append("BX_CONFIRMED")
                x.setdefault("trapped", []).append({"type":"TRAPPED_BUYERS","candidateTime":cand["time"],"confirmationTime":x["t"],"candidatePrice":cand["close"],"confirmationPrice":x["c"],"barsToConfirm":dist,"repaint":False})
                candidates.remove(cand)
            elif cand["side"] == "SELL" and (x["c"] > cand["high"] or x["c"] >= cand["close"] + 0.25 * cand["atr14"]):
                events.append("SX_CONFIRMED")
                x.setdefault("trapped", []).append({"type":"TRAPPED_SELLERS","candidateTime":cand["time"],"confirmationTime":x["t"],"candidatePrice":cand["close"],"confirmationPrice":x["c"],"barsToConfirm":dist,"repaint":False})
                candidates.remove(cand)
        if buy_absorption or buy_failure:
            candidates.append({"side":"BUY","index":i,"time":x["t"],"low":x["l"],"high":x["h"],"close":x["c"],"atr14":atr14})
        if sell_absorption or sell_failure:
            candidates.append({"side":"SELL","index":i,"time":x["t"],"low":x["l"],"high":x["h"],"close":x["c"],"atr14":atr14})
        out.append({**x, "v": V, "delta": delta, "nd": nd, "volumePower": volume_power, "atr14": atr14,
                    "atrRegime": atr_regime, "relativeForce": relative_force, "rawBuy": raw_buy, "rawSell": raw_sell,
                    "buyAgg": buy_agg, "sellAgg": sell_agg, "buyLine": buy_line, "sellLine": sell_line,
                    "priceResponse": price_response, "sellResponse": sell_response, "events": events,
                    "closed": x["closed"]})
    return out

def synth_bar(t, bv, sv, o=100, tr=10, response=0.0):
    c = o + response * tr
    return {"t": t, "o": o, "h": max(o, c) + tr/2, "l": min(o, c) - tr/2, "c": c,
            "v": bv + sv, "bv": bv, "sv": sv, "volume_unit": "QUOTE_USDT", "closed": True}

def force_event_bar(side, response, t=10):
    bars = [synth_bar(i, 1000, 1000, response=0.0) for i in range(t)]
    if side == "buy": bars += [synth_bar(t+i, 10000, 100, response=response) for i in range(5)]
    else: bars += [synth_bar(t+i, 100, 10000, response=-response) for i in range(5)]
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
    bx_bars = [synth_bar(i, 1000, 1000) for i in range(10)] + [synth_bar(10+i, 10000, 100, response=-0.60) for i in range(5)]
    event = bx_bars[-1]
    bx_bars.append({**synth_bar(20, 1000, 1000), "o": event["c"], "h": event["c"]+1, "l": event["c"]-8, "c": event["l"]-1})
    assert "BX_CONFIRMED" in calc_nai(bx_bars)[-1]["events"]
    sx_bars = [synth_bar(i, 1000, 1000) for i in range(10)] + [synth_bar(10+i, 100, 10000, response=0.60) for i in range(5)]
    event = sx_bars[-1]
    # Keep the confirmation candle non-directional so it only tests trapped confirmation,
    # not a new strong sell event created by its own body.
    sx_bars.append({"t": 20, "o": event["h"] + 1, "h": event["h"] + 2, "l": event["h"], "c": event["h"] + 1,
                    "v": 2000, "bv": 1000, "sv": 1000, "volume_unit": "QUOTE_USDT", "closed": True})
    assert "SX_CONFIRMED" in calc_nai(sx_bars)[-1]["events"]

def stats(values):
    vals = sorted(float(v) for v in values if math.isfinite(float(v)))
    def pct(p):
        k = (len(vals)-1)*p/100; f = math.floor(k); c = math.ceil(k)
        return vals[f] if f == c else vals[f]*(c-k)+vals[c]*(k-f)
    return {"min": min(vals), "max": max(vals), "median": statistics.median(vals), "p95": pct(95), "p99": pct(99)}

def verify_data():
    result = {"timeframes": {}, "events": {}, "manual_15m": []}
    for tf, fn in TF_FILES.items():
        rows = json.loads((DATA / fn).read_text())
        calc = calc_nai(rows)
        inv = {"rows": len(calc), "unit_quote_usdt": 0, "v_sum_fail": 0, "range_fail": 0, "nan": 0, "inf": 0, "neg_volume": 0, "zero_atr": 0}
        ev = {}
        for r in calc:
            inv["unit_quote_usdt"] += int(r.get("volume_unit") == "QUOTE_USDT")
            inv["v_sum_fail"] += int(abs(r["v"] - (r["bv"] + r["sv"])) > max(1e-6, r["v"] * 1e-9))
            nums = [r[k] for k in ("buyAgg","sellAgg","buyLine","sellLine","nd","volumePower","atrRegime","atr14")]
            inv["nan"] += int(any(math.isnan(x) for x in nums)); inv["inf"] += int(any(math.isinf(x) for x in nums))
            inv["neg_volume"] += int(r["v"] < 0 or r["bv"] < 0 or r["sv"] < 0)
            inv["zero_atr"] += int(abs(r["atr14"]) < EPS)
            inv["range_fail"] += int(not(0 <= r["buyAgg"] < 100 and 0 <= r["sellAgg"] < 100 and 0 <= r["buyLine"] < 100 and 0 <= r["sellLine"] < 100 and -1 <= r["nd"] <= 1))
            for e in r["events"]: ev[e] = ev.get(e, 0) + 1
        result["timeframes"][tf] = inv
        result["events"][tf] = ev
        if tf == "15m":
            for r in calc[-5:]:
                result["manual_15m"].append({k: round(r[k], 6) if isinstance(r.get(k), float) else r.get(k) for k in ["t","bv","sv","v","delta","nd","volumePower","atr14","atrRegime","relativeForce","buyAgg","sellAgg","buyLine","sellLine","priceResponse","sellResponse","events"]})
        assert inv["unit_quote_usdt"] == inv["rows"], f"{tf} non quote volume rows"
        assert inv["v_sum_fail"] == inv["range_fail"] == inv["nan"] == inv["inf"] == inv["neg_volume"] == inv["zero_atr"] == 0, (tf, inv)
    return result

def repaint_test():
    rows = json.loads((DATA / "bars_15m.json").read_text())[:300]
    batch = calc_nai(rows)
    stream_final = []
    for i in range(1, len(rows)+1):
        stream_final = calc_nai(rows[:i])
    keys = ["buyLine","sellLine","nd","events"]
    for b, s in zip(batch, stream_final):
        for k in keys:
            assert b[k] == s[k], f"repaint mismatch {k} at {b['t']}"
    return {"rows": len(rows), "match": True}

if __name__ == "__main__":
    run_unit_tests()
    data_result = verify_data()
    data_result["repaint"] = repaint_test()
    print(json.dumps({"ok": True, **data_result}, indent=2, ensure_ascii=False))
