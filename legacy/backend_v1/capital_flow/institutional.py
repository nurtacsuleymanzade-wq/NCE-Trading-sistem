"""SEC and issuer ETF holdings adapters.

Only IBIT has a validated official daily CSV adapter initially. Other funds
are represented by explicit configurations and return UNAVAILABLE until an
official normalized source is validated; this prevents silent scraper drift.
"""
from __future__ import annotations

import csv
import io
import json
import os
import time
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from typing import Any, Mapping

from .external_context import unavailable


ETF_CONFIG = {
    "IBIT": {"issuer": "BlackRock iShares", "cik": "1980994", "official_url": "https://www.ishares.com/us/products/333011/ishares-bitcoin-trust-etf", "daily_url": "https://www.ishares.com/us/products/333011/ishares-bitcoin-trust-etf/latest-holdings.csv"},
    "FBTC": {"issuer": "Fidelity", "cik": "1852317", "official_url": "https://www.fidelity.com/etfs/fbtc"},
    "GBTC": {"issuer": "Grayscale", "cik": "1588489", "official_url": "https://www.grayscale.com/crypto-products/grayscale-bitcoin-trust-etf"},
    "ARKB": {"issuer": "ARK 21Shares", "cik": "1869699", "official_url": "https://www.21shares.com/en-us/products/arkb"},
    "BITB": {"issuer": "Bitwise", "cik": "1763415", "official_url": "https://bitwiseinvestments.com/crypto-funds/bitb"},
    "BTCO": {"issuer": "Invesco Galaxy", "cik": "1855781", "official_url": "https://www.invesco.com/us/en/financial-products/etfs/product-detail?audienceType=Investor&productId=ETF-BTCO"},
    "HODL": {"issuer": "VanEck", "cik": "1838028", "official_url": "https://www.vaneck.com/us/en/investments/bitcoin-etf-hodl/"},
    "BRRR": {"issuer": "CoinShares", "cik": "1841175", "official_url": "https://coinshares.com/us/etf/brrr/"},
    "EZBC": {"issuer": "Franklin Templeton", "cik": "1992870", "official_url": "https://www.franklintempleton.com/investments/options/exchange-traded-funds/products/43964/SINGLCLASS/franklin-bitcoin-etf/EZBC"},
    "BTCW": {"issuer": "WisdomTree", "cik": "1850391", "official_url": "https://www.wisdomtree.com/investments/etfs/crypto/btcw"},
    "BTC": {"issuer": "Grayscale", "cik": "2015034", "official_url": "https://www.grayscale.com/crypto-products/grayscale-bitcoin-mini-trust-etf"},
}

ETF_FIELDS = ("btc_holdings", "shares_outstanding", "aum_usd", "nav_usd")


@dataclass(frozen=True)
class ETFHolding:
    fund: str
    timestamp_ms: int
    btc_holdings: float | None
    shares_outstanding: float | None
    aum_usd: float | None
    nav_usd: float | None
    source: str
    source_type: str
    status: str
    confidence: float | None
    as_of: str | None = None


class ETFSourceAdapter:
    fund = "UNKNOWN"

    def __init__(self, config: Mapping[str, Any] | None = None) -> None:
        self.config = dict(config or ETF_CONFIG.get(self.fund, {}))

    def fetch_holdings(self) -> dict[str, Any]:
        return normalized_unavailable(self.fund, "validated official daily holdings source is not configured")


def normalized_unavailable(fund: str, reason: str, *, source: str | None = None) -> dict[str, Any]:
    config = ETF_CONFIG.get(fund, {})
    return {
        "fund": fund, "issuer": config.get("issuer"), "cik": config.get("cik"),
        "btc_holdings": None, "shares_outstanding": None, "aum_usd": None, "nav_usd": None,
        "timestamp_ms": None, "as_of": None, "source": source or config.get("official_url"),
        "source_type": "ISSUER", "status": "UNAVAILABLE", "coverage": 0.0,
        "confidence": None, "reason": reason,
        "methodology": "official issuer ground truth required; unavailable fields remain null",
    }


class IBITAdapter(ETFSourceAdapter):
    fund = "IBIT"

    def fetch_holdings(self) -> dict[str, Any]:
        url = self.config["daily_url"]
        try:
            import httpx
            response = httpx.get(url, timeout=30, headers={"User-Agent": "NCE-Capital-Flow/3.0"})
            response.raise_for_status()
            text = response.content.decode("utf-8-sig")
            rows = list(csv.reader(io.StringIO(text)))
            as_of = None
            shares = None
            btc = None
            for row in rows[:12]:
                if len(row) >= 2 and row[0].strip().lower().startswith("fund holdings as of"):
                    as_of = row[1].strip()
                if len(row) >= 2 and row[0].strip().lower().startswith("shares outstanding"):
                    shares = float(row[1].replace(",", ""))
            header_index = next((index for index, row in enumerate(rows) if row and row[0].strip() == "Ticker"), None)
            if header_index is not None:
                headers = rows[header_index]
                for row in rows[header_index + 1:]:
                    record = dict(zip(headers, row))
                    if record.get("Ticker", "").strip().upper() == "BTC":
                        btc = float(record.get("Quantity", "").replace(",", ""))
                        break
            if btc is None:
                return unavailable(url, "iShares latest-holdings.csv parser", "BTC row or Quantity field missing")
            timestamp_ms = int(time.time() * 1000)
            if as_of:
                for fmt in ("%b %d, %Y", "%B %d, %Y"):
                    try:
                        timestamp_ms = int(datetime.strptime(as_of, fmt).replace(tzinfo=timezone.utc).timestamp() * 1000)
                        break
                    except ValueError:
                        pass
            point = ETFHolding("IBIT", timestamp_ms, btc, shares, None, None, url, "ISSUER", "REAL", 98.0, as_of)
            return {**asdict(point), "issuer": ETF_CONFIG["IBIT"]["issuer"], "cik": ETF_CONFIG["IBIT"]["cik"], "coverage": 0.5, "methodology": "official iShares latest holdings CSV; BTC quantity is holdings ground truth snapshot, not cash flow"}
        except Exception as exc:
            return normalized_unavailable("IBIT", f"iShares latest-holdings.csv parser: {type(exc).__name__}", source=url)


class FBTCAdapter(ETFSourceAdapter):
    fund = "FBTC"


class GBTCAdapter(ETFSourceAdapter):
    fund = "GBTC"


class ARKBAdapter(ETFSourceAdapter):
    fund = "ARKB"


class BITBAdapter(ETFSourceAdapter):
    fund = "BITB"


class BTCOAdapter(ETFSourceAdapter):
    fund = "BTCO"


class HODLAdapter(ETFSourceAdapter):
    fund = "HODL"


class BRRRAdapter(ETFSourceAdapter):
    fund = "BRRR"


class EZBCAdapter(ETFSourceAdapter):
    fund = "EZBC"


class BTCWAdapter(ETFSourceAdapter):
    fund = "BTCW"


class BTCAdapter(ETFSourceAdapter):
    fund = "BTC"


ADAPTERS = {name: cls(ETF_CONFIG[name]) for name, cls in {"IBIT": IBITAdapter, "FBTC": FBTCAdapter, "GBTC": GBTCAdapter, "ARKB": ARKBAdapter, "BITB": BITBAdapter, "BTCO": BTCOAdapter, "HODL": HODLAdapter, "BRRR": BRRRAdapter, "EZBC": EZBCAdapter, "BTCW": BTCWAdapter, "BTC": BTCAdapter}.items()}


def audit_etf_universe() -> list[dict[str, Any]]:
    """Fetch the current official adapter result for every required fund."""
    results = []
    for fund, adapter in ADAPTERS.items():
        try:
            point = adapter.fetch_holdings()
        except Exception as exc:
            point = normalized_unavailable(fund, type(exc).__name__)
        config = ETF_CONFIG[fund]
        results.append({
            "fund": fund, "issuer": config.get("issuer"), "official_source": config.get("official_url"), "cik": config.get("cik"),
            "daily_holdings_availability": "REAL" if point.get("status") == "REAL" else "UNAVAILABLE",
            "btc_holdings": point.get("btc_holdings"), "shares_outstanding": point.get("shares_outstanding"),
            "aum_usd": point.get("aum_usd"), "nav_usd": point.get("nav_usd"), "sec_ground_truth": "FILING_METADATA_ONLY",
            "adapter_status": point.get("status", "UNAVAILABLE"), "last_update": point.get("as_of") or point.get("timestamp_ms"),
            "coverage": point.get("coverage", 0.0), "confidence": point.get("confidence"), "reason": point.get("reason"),
        })
    return results


class SECClient:
    def __init__(self, user_agent: str = "NCE Capital Flow Intelligence contact@nce.local") -> None:
        self.user_agent = user_agent

    def submissions(self, cik: str) -> dict[str, Any]:
        try:
            import httpx
            padded = str(cik).zfill(10)
            response = httpx.get(f"https://data.sec.gov/submissions/CIK{padded}.json", timeout=30, headers={"User-Agent": self.user_agent, "Accept-Encoding": "gzip, deflate"})
            response.raise_for_status()
            return {"status": "REAL", "source": "SEC EDGAR submissions", "timestamp": int(time.time() * 1000), "cik": cik, "payload": response.json(), "methodology": "regulatory filing metadata; not intraday ETF flow", "confidence": 98.0, "coverage": 100.0}
        except Exception as exc:
            return unavailable("SEC EDGAR", "submissions/CIK##########.json", type(exc).__name__)

    @staticmethod
    def parse_filings(payload: Mapping[str, Any], fund: str) -> list[dict[str, Any]]:
        recent = (payload.get("filings") or {}).get("recent") or {}
        fields = ("accessionNumber", "form", "filingDate", "reportDate", "primaryDocument")
        length = len(recent.get("accessionNumber", []))
        return [{"fund": fund, "accession_number": recent.get("accessionNumber", [None] * length)[index], "form_type": recent.get("form", [None] * length)[index], "filed_at": recent.get("filingDate", [None] * length)[index], "period_end": recent.get("reportDate", [None] * length)[index], "document": recent.get("primaryDocument", [None] * length)[index], "reported_btc": None, "shares_outstanding": None, "status": "REAL", "methodology": "SEC filing metadata; reported BTC parsed only when explicitly present"} for index in range(length)]


def holdings_delta(previous: Mapping[str, Any] | None, current: Mapping[str, Any]) -> dict[str, Any]:
    old = previous.get("btc_holdings") if previous else None
    new = current.get("btc_holdings")
    if old is None or new is None:
        return {"status": "UNAVAILABLE", "btc_delta": None, "estimated_notional_holdings_flow": None, "methodology": "requires two known holdings snapshots"}
    delta = float(new) - float(old)
    return {"status": "DERIVED", "btc_delta": delta, "estimated_notional_holdings_flow": None, "methodology": "BTC HOLDINGS CHANGE; not official cash flow", "source": current.get("source"), "confidence": min(float(current.get("confidence") or 0), 80.0)}


def institutional_aggregate(points: list[Mapping[str, Any]], deltas: Mapping[str, float | None] | None = None) -> dict[str, Any]:
    valid = [float(point["btc_holdings"]) for point in points if point.get("btc_holdings") is not None]
    if not valid:
        return unavailable("SEC/issuer ETF holdings", "aggregate normalized holdings", "no validated holdings points")
    delta = (deltas or {}).get("1D")
    state = "UNKNOWN" if delta is None else "NEUTRAL" if delta == 0 else "INFLOW" if delta > 0 else "OUTFLOW"
    return {"status": "DERIVED", "state": state, "aggregate_btc": sum(valid), "delta_1D": delta, "delta_5D": (deltas or {}).get("5D"), "delta_20D": (deltas or {}).get("20D"), "confidence": 80.0, "methodology": "aggregate BTC holdings changes; never labeled ETF cash flow"}
