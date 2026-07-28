#!/usr/bin/env python3
"""Update NCE Trading static GitHub Pages market data and optionally push it.

Safe behavior:
- Updates only data/*.json files.
- Never prints credentials.
- Uses Binance public HTTP APIs.
- Git commit stages only data/*.json.
"""
import argparse
import json
import os
import subprocess
import sys
import time
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

REPO = Path(os.environ.get("NCE_REPO", "/root/NCE-Trading-sistem-inspect"))
DATA = REPO / "data"
SYMBOL = os.environ.get("NCE_SYMBOL", "BTCUSDT")
API = os.environ.get("NCE_BINANCE_API", "https://fapi.binance.com")
MARKET = os.environ.get("NCE_MARKET", "USD-M Futures")
API_PATH_PREFIX = os.environ.get("NCE_BINANCE_PATH_PREFIX", "/fapi/v1")

TF_MAP = {
    "1m": ("1m", 1000),
    "5m": ("5m", 1000),
    "15m": ("15m", 1000),
    "30m": ("30m", 1000),
    "1h": ("1h", 1000),
    "4h": ("4h", 1000),
    "1D": ("1d", 365),
}


def fetch_json(url: str, timeout: int = 20):
    req = urllib.request.Request(url, headers={"User-Agent": "nce-trading-updater/1.0"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        if r.status != 200:
            raise RuntimeError(f"HTTP {r.status} for {url}")
        return json.loads(r.read().decode("utf-8"))


def server_time_ms() -> int:
    try:
        data = fetch_json(f"{API}{API_PATH_PREFIX}/time", timeout=10)
        return int(data["serverTime"])
    except Exception:
        return int(time.time() * 1000)


def kline_to_bar(row, server_ms: int):
    total_quote = float(row[7])
    buy_quote = float(row[10])
    sell_quote = max(0.0, total_quote - buy_quote)
    return {
        "t": int(int(row[0]) / 1000),
        "o": float(row[1]),
        "h": float(row[2]),
        "l": float(row[3]),
        "c": float(row[4]),
        "v": total_quote,
        "bv": buy_quote,
        "sv": sell_quote,
        "volume_unit": "QUOTE_USDT",
        "closed": int(row[6]) < server_ms,
    }


def write_json(path: Path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(data, separators=(",", ":")), encoding="utf-8")
    tmp.replace(path)


def aggregate_3d(days):
    out = []
    days = [x for x in days if x.get("closed") is True]
    for i in range(0, len(days), 3):
        group = days[i:i + 3]
        if len(group) < 3:
            continue
        bv = sum(x.get("bv", 0) for x in group)
        sv = sum(x.get("sv", max(0.0, x.get("v", 0) - x.get("bv", 0))) for x in group)
        out.append({
            "t": group[0]["t"],
            "o": group[0]["o"],
            "h": max(x["h"] for x in group),
            "l": min(x["l"] for x in group),
            "c": group[-1]["c"],
            "v": bv + sv,
            "bv": bv,
            "sv": sv,
            "volume_unit": "QUOTE_USDT",
            "closed": all(x.get("closed") is True for x in group),
        })
    return out


def update_klines():
    latest_daily = None
    counts = {}
    server_ms = server_time_ms()
    for tf, (interval, limit) in TF_MAP.items():
        url = f"{API}{API_PATH_PREFIX}/klines?symbol={SYMBOL}&interval={interval}&limit={limit}"
        rows = fetch_json(url)
        # Static JSON is historical bootstrap data only. Keep open REST klines out;
        # live/provisional updates are handled by the browser WebSocket layer.
        bars = [b for b in (kline_to_bar(r, server_ms) for r in rows) if b["closed"]]
        suffix = "1d" if tf == "1D" else tf.lower()
        write_json(DATA / f"bars_{suffix}.json", bars)
        counts[tf] = len(bars)
        if tf == "1D":
            latest_daily = bars
            write_json(DATA / "btc_daily.json", [
                {"time": x["t"], "open": x["o"], "high": x["h"], "low": x["l"], "close": x["c"], "volume": x["v"], "volume_unit": "QUOTE_USDT"}
                for x in bars
            ])
    if latest_daily:
        bars_3d = aggregate_3d(latest_daily)
        write_json(DATA / "bars_3d.json", bars_3d)
        counts["3D"] = len(bars_3d)
    return counts


def update_1s():
    rows = fetch_json(f"{API}{API_PATH_PREFIX}/aggTrades?symbol={SYMBOL}&limit=1000")
    buckets = {}
    now_s = int(time.time())
    for r in rows:
        t = int(int(r["T"]) / 1000)
        if t >= now_s:
            continue
        price = float(r["p"])
        qty = float(r["q"])
        quote_volume = price * qty
        buy = not bool(r.get("m"))
        b = buckets.get(t)
        if not b:
            b = buckets[t] = {"t": t, "o": price, "h": price, "l": price, "c": price, "v": 0.0, "bv": 0.0, "sv": 0.0, "volume_unit": "QUOTE_USDT", "closed": True}
        b["h"] = max(b["h"], price)
        b["l"] = min(b["l"], price)
        b["c"] = price
        b["v"] += quote_volume
        if buy:
            b["bv"] += quote_volume
        else:
            b["sv"] += quote_volume
    bars = [buckets[k] for k in sorted(buckets)]
    write_json(DATA / "bars_1s.json", bars)
    return len(bars)


def update_status(counts):
    ticker = fetch_json(f"{API}{API_PATH_PREFIX}/ticker/24hr?symbol={SYMBOL}")
    price = float(ticker["lastPrice"])
    status = {
        "updated": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC"),
        "symbol": SYMBOL,
        "market": MARKET,
        "api": API,
        "volume_unit": "QUOTE_USDT",
        "price": price,
        "change_percent_24h": float(ticker.get("priceChangePercent", 0)),
        "high_24h": float(ticker.get("highPrice", 0)),
        "low_24h": float(ticker.get("lowPrice", 0)),
        "volume_24h": float(ticker.get("volume", 0)),
        "bars": counts,
    }
    write_json(DATA / "live_status.json", status)
    return status


def run(cmd, cwd=REPO, check=False):
    p = subprocess.run(cmd, cwd=str(cwd), text=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
    if check and p.returncode:
        raise RuntimeError(f"command failed: {' '.join(cmd)}\n{p.stdout}")
    return p.returncode, p.stdout.strip()


def git_commit_push(push=True):
    data_files = [
        "data/bars_1s.json", "data/bars_1m.json", "data/bars_5m.json",
        "data/bars_15m.json", "data/bars_30m.json", "data/bars_1h.json",
        "data/bars_4h.json", "data/bars_1d.json", "data/bars_3d.json",
        "data/btc_daily.json", "data/live_status.json",
    ]
    run(["git", "add", *data_files])
    code, diff = run(["git", "diff", "--cached", "--quiet"])
    if code == 0:
        return {"changed": False, "pushed": False, "message": "no data changes"}

    # If GitHub auth is missing, repeated 5-min updates must not create an infinite
    # local commit chain. Keep only one unpushed data commit by amending it.
    msg = "Update NCE Trading market data"
    ahead_code, ahead_out = run(["git", "rev-list", "--count", "origin/main..HEAD"])
    head_code, head_msg = run(["git", "log", "-1", "--pretty=%s"])
    can_amend = ahead_code == 0 and ahead_out.isdigit() and int(ahead_out) > 0 and head_msg == msg
    if can_amend:
        code, out = run(["git", "commit", "--amend", "--no-edit"])
    else:
        code, out = run(["git", "commit", "-m", msg])
    if code != 0:
        raise RuntimeError(out)

    if not push:
        return {"changed": True, "pushed": False, "message": "committed locally; push disabled"}
    code, out = run(["git", "push", "origin", "main"])
    if code != 0:
        return {"changed": True, "pushed": False, "message": "push failed", "git_output": out[-600:]}
    return {"changed": True, "pushed": True, "message": "pushed to origin/main"}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--no-push", action="store_true")
    ap.add_argument("--check-only", action="store_true")
    args = ap.parse_args()

    if not (REPO / ".git").exists():
        raise SystemExit(f"repo not found: {REPO}")
    DATA.mkdir(parents=True, exist_ok=True)

    counts = update_klines()
    counts["1s"] = update_1s()
    status = update_status(counts)
    git_result = git_commit_push(push=not args.no_push and not args.check_only)
    print(json.dumps({"ok": True, "status": status, "git": git_result}, ensure_ascii=False))


if __name__ == "__main__":
    main()
