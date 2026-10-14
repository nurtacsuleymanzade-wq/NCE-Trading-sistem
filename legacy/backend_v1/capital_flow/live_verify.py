from __future__ import annotations

import json
import time
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from .engine import normalize_agg_trade


def get_json(base: str, path: str, params: dict[str, str]) -> object:
    url = f"{base}{path}?{urlencode(params)}"
    request = Request(url, headers={"User-Agent": "NCE-Capital-Flow/2.0"})
    with urlopen(request, timeout=15) as response:
        return json.loads(response.read().decode("utf-8"))


def verify(symbol: str = "BTCUSDT") -> dict:
    symbol = symbol.upper()
    now = int(time.time() * 1000)
    out: dict = {"status": "PASS", "symbol": symbol, "checked_at_ms": now, "sources": {}}
    try:
        spot = get_json("https://data-api.binance.vision", "/api/v3/aggTrades", {"symbol": symbol, "limit": "3"})
        normalized = [normalize_agg_trade(row, "spot", symbol).as_dict() for row in spot]  # type: ignore[arg-type]
        out["sources"]["spot_aggTrades"] = {"status": "REAL", "endpoint": "https://data-api.binance.vision/api/v3/aggTrades", "sample": normalized}
    except Exception as exc:
        out["status"] = "PARTIAL"
        out["sources"]["spot_aggTrades"] = {"status": "UNAVAILABLE", "error": type(exc).__name__}
    for key, base, path, params in [
        ("futures_open_interest", "https://fapi.binance.com", "/fapi/v1/openInterest", {"symbol": symbol}),
        ("futures_premium_index", "https://fapi.binance.com", "/fapi/v1/premiumIndex", {"symbol": symbol}),
        ("top_trader_accounts", "https://fapi.binance.com", "/futures/data/topLongShortAccountRatio", {"symbol": symbol, "period": "5m", "limit": "1"}),
    ]:
        try:
            out["sources"][key] = {"status": "REAL", "endpoint": f"{base}{path}", "sample": get_json(base, path, params)}
        except Exception as exc:
            out["status"] = "PARTIAL"
            out["sources"][key] = {"status": "UNAVAILABLE", "error": type(exc).__name__}
    return out


if __name__ == "__main__":
    print(json.dumps(verify(), indent=2))
