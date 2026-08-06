#!/usr/bin/env python3
"""Persistent BTCUSDT USD-M Futures 1s aggTrade collector.

- Groups Binance futures aggTrade stream into quote-volume 1s bars.
- Keeps up to 24h (86400 bars) and at least enables 60m archive after warm-up.
- Persists to repo data/bars_1s.json atomically.
- Does not fabricate missing seconds; gaps remain absent timestamps.
"""
import asyncio
import json
import os
import time
from pathlib import Path
from urllib.request import urlopen

import websockets

REPO = Path(os.environ.get("NCE_REPO", "/root/NCE-Trading-sistem-inspect"))
STATE = Path(os.environ.get("NCE_1S_STATE_DIR", "/var/lib/nce-trading"))
STATE.mkdir(parents=True, exist_ok=True)
ARCHIVE = STATE / "bars_1s_archive.json"
GAPS = STATE / "gaps_1s.json"
LOCK = Path(os.environ.get("NCE_LOCK", "/run/nce-trading-updater.lock"))
WS_URL = os.environ.get("NCE_AGG_WS", "wss://fstream.binance.com/market/ws/btcusdt@aggTrade")
KEEP_SECONDS = int(os.environ.get("NCE_1S_KEEP_SECONDS", "86400"))
FLUSH_INTERVAL = float(os.environ.get("NCE_1S_FLUSH_INTERVAL", "2"))
MIN_SECONDS = int(os.environ.get("NCE_1S_MIN_SECONDS", "3600"))
SYMBOL = os.environ.get("NCE_SYMBOL", "BTCUSDT")


def load_archive():
    if not ARCHIVE.exists():
        return {}
    try:
        rows = json.loads(ARCHIVE.read_text())
        return {int(x["t"]): x for x in rows if x.get("t") is not None}
    except Exception:
        return {}


def compact(bars):
    if not bars:
        return bars
    newest = max(bars)
    cutoff = newest - KEEP_SECONDS + 1
    return {k: bars[k] for k in sorted(bars) if k >= cutoff}


def write_archive(bars):
    STATE.mkdir(parents=True, exist_ok=True)
    now_s = int(time.time())
    closed_keys = [k for k in sorted(bars) if k < now_s]
    for k in closed_keys:
        bars[k]["closed"] = True
    keys = closed_keys
    rows = [bars[k] for k in keys]
    tmp = ARCHIVE.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(rows, separators=(",", ":")), encoding="utf-8")
    tmp.replace(ARCHIVE)
    gaps = []
    for a, b in zip(keys, keys[1:]):
        if b - a > 1:
            gaps.append({"from": a + 1, "to": b - 1, "seconds": b - a - 1, "reason": "NO_AGGTRADE_BAR; no synthetic volume generated"})
    gtmp = GAPS.with_suffix(".json.tmp")
    gtmp.write_text(json.dumps(gaps, separators=(",", ":")), encoding="utf-8")
    gtmp.replace(GAPS)


def merge_trade(bars, msg):
    t = int(int(msg["T"]) / 1000)
    price = float(msg["p"])
    qty = float(msg["q"])
    quote = price * qty
    buy = not bool(msg.get("m"))
    b = bars.get(t)
    if not b:
        b = bars[t] = {"t": t, "o": price, "h": price, "l": price, "c": price, "v": 0.0, "quote_v": 0.0, "base_v": 0.0, "bv": 0.0, "sv": 0.0, "volume_unit": "QUOTE_USDT", "closed": True}
    b["h"] = max(b["h"], price)
    b["l"] = min(b["l"], price)
    b["c"] = price
    b["v"] += quote
    b["quote_v"] += quote
    b["base_v"] += qty
    if buy:
        b["bv"] += quote
    else:
        b["sv"] += quote


def fetch_agg(params):
    qs = "&".join(f"{k}={v}" for k, v in params.items())
    with urlopen(f"https://fapi.binance.com/fapi/v1/aggTrades?{qs}", timeout=20) as r:
        return json.loads(r.read().decode())


def backfill_if_needed(bars):
    if bars and max(bars) - min(bars) >= MIN_SECONDS - 1:
        return bars
    pages = int(os.environ.get("NCE_1S_BACKFILL_PAGES", "120"))
    try:
        page = fetch_agg({"symbol": SYMBOL, "limit": 1000})
    except Exception as e:
        print(json.dumps({"ok": False, "backfill_error": str(e)[-200:]}), flush=True)
        return bars
    for _ in range(pages):
        if not page:
            break
        min_id = min(int(x["a"]) for x in page)
        for x in page:
            merge_trade(bars, x)
        if bars and max(bars) - min(bars) >= MIN_SECONDS - 1:
            break
        if min_id <= 1:
            break
        try:
            page = fetch_agg({"symbol": SYMBOL, "fromId": max(0, min_id - 1000), "limit": 1000})
            page = [x for x in page if int(x["a"]) < min_id]
        except Exception as e:
            print(json.dumps({"ok": False, "backfill_error": str(e)[-200:]}), flush=True)
            break
    bars = compact(bars)
    write_archive(bars)
    return bars


def update_bar(bars, msg):
    t = int(int(msg["T"]) / 1000)
    now_s = int(time.time())
    price = float(msg["p"])
    qty = float(msg["q"])
    quote = price * qty
    buy = not bool(msg.get("m"))  # m=false => buyer was taker => aggressive buy
    b = bars.get(t)
    if not b:
        b = bars[t] = {"t": t, "o": price, "h": price, "l": price, "c": price, "v": 0.0, "quote_v": 0.0, "base_v": 0.0, "bv": 0.0, "sv": 0.0, "volume_unit": "QUOTE_USDT", "closed": t < now_s}
    b["h"] = max(b["h"], price)
    b["l"] = min(b["l"], price)
    b["c"] = price
    b["v"] += quote
    b["quote_v"] += quote
    b["base_v"] += qty
    if buy:
        b["bv"] += quote
    else:
        b["sv"] += quote
    # Mark older bars closed. Current second remains provisional until next flush.
    for k in list(bars.keys()):
        if k < now_s:
            bars[k]["closed"] = True


async def main():
    bars = backfill_if_needed(compact(load_archive()))
    last_flush = 0.0
    backoff = 1
    while True:
        try:
            async with websockets.connect(WS_URL, ping_interval=20, ping_timeout=20, close_timeout=5) as ws:
                backoff = 1
                async for raw in ws:
                    msg = json.loads(raw)
                    update_bar(bars, msg)
                    now = time.time()
                    if now - last_flush >= FLUSH_INTERVAL:
                        bars = compact(bars)
                        write_archive(bars)
                        last_flush = now
        except Exception as e:
            print(json.dumps({"ok": False, "collector_error": type(e).__name__, "message": str(e)[-200:]}), flush=True)
            await asyncio.sleep(backoff)
            backoff = min(backoff * 2, 60)


if __name__ == "__main__":
    asyncio.run(main())
