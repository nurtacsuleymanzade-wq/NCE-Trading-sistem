"""Validated source adapters and historical primitives for Capital Flow V3.

Adapters accept source payloads rather than inventing values. Network callers
are intentionally opt-in; the production live path remains independent.
"""

from __future__ import annotations

import json
import math
import statistics
import time
from dataclasses import dataclass, asdict
from typing import Any, Iterable, Mapping


def _num(value: Any) -> float | None:
    try:
        result = float(value)
        return result if math.isfinite(result) else None
    except (TypeError, ValueError):
        return None


@dataclass(frozen=True)
class ETFSourceAdapter:
    fund: str
    issuer: str
    official_url: str
    source_type: str = "ISSUER"

    def normalize(self, payload: Mapping[str, Any], timestamp: str | int) -> dict[str, Any]:
        holdings = _num(payload.get("btc_holdings", payload.get("bitcoin_holdings")))
        return {"fund": self.fund, "timestamp": timestamp, "btc_holdings": holdings, "shares_outstanding": _num(payload.get("shares_outstanding")), "aum_usd": _num(payload.get("aum_usd")), "nav": _num(payload.get("nav")), "official_daily_flow_usd": _num(payload.get("official_daily_flow_usd")), "source": self.official_url, "source_type": self.source_type, "status": "REAL" if holdings is not None else "UNAVAILABLE", "confidence": 100 if holdings is not None else None, "reason": None if holdings is not None else "issuer payload did not report BTC holdings"}


ETF_FUND_CONFIG = {
    "IBIT": {"issuer": "BlackRock", "official_url": "https://www.ishares.com/"},
    "FBTC": {"issuer": "Fidelity", "official_url": "https://digital.fidelity.com/"},
    "GBTC": {"issuer": "Grayscale", "official_url": "https://etfs.grayscale.com/"},
    "ARKB": {"issuer": "ARK/21Shares", "official_url": "https://www.21shares.com/"},
    "BITB": {"issuer": "Bitwise", "official_url": "https://bitwiseinvestments.com/"},
    "BTCO": {"issuer": "Invesco Galaxy", "official_url": "https://www.invesco.com/"},
    "HODL": {"issuer": "VanEck", "official_url": "https://www.vaneck.com/"},
    "BRRR": {"issuer": "Valkyrie/CoinShares", "official_url": "https://coinshares.com/"},
    "EZBC": {"issuer": "Franklin Templeton", "official_url": "https://www.franklintempleton.com/"},
    "BTCW": {"issuer": "WisdomTree", "official_url": "https://www.wisdomtree.com/"},
}


def issuer_adapter(fund: str) -> ETFSourceAdapter:
    key = fund.upper()
    config = ETF_FUND_CONFIG.get(key)
    if not config:
        raise ValueError(f"unsupported ETF fund: {fund}")
    return ETFSourceAdapter(key, config["issuer"], config["official_url"])


def holdings_delta(points: Iterable[Mapping[str, Any]], reference_btc_price: float | None = None) -> dict[str, Any]:
    rows = [dict(x) for x in points if _num(x.get("btc_holdings")) is not None]
    if len(rows) < 2:
        return {"status": "UNAVAILABLE", "btc_delta_1D": None, "btc_delta_5D": None, "btc_delta_20D": None, "reason": "requires at least two validated holdings observations"}
    latest = float(rows[-1]["btc_holdings"])
    def delta(days: int) -> float | None:
        if len(rows) <= days:
            return None
        return latest - float(rows[-1 - days]["btc_holdings"])
    delta_1d, delta_5d, delta_20d = delta(1), delta(5), delta(20)
    return {"status": "DERIVED", "btc_delta_1D": delta_1d, "btc_delta_5D": delta_5d, "btc_delta_20D": delta_20d, "estimated_notional_holdings_flow_1D": delta_1d * reference_btc_price if delta_1d is not None and reference_btc_price else None, "methodology": "BTC holdings delta; estimated notional is not official cash flow"}


def parse_sec_submission(payload: Mapping[str, Any], fund: str, cik: str | None = None) -> dict[str, Any]:
    """Normalize only fields actually present in SEC submissions/companyfacts."""
    filings = payload.get("filings", {}).get("recent", {}) if isinstance(payload.get("filings"), Mapping) else payload.get("recent", {})
    forms = filings.get("form", []) if isinstance(filings, Mapping) else []
    if not forms:
        return {"status": "UNAVAILABLE", "fund": fund, "cik": cik, "state": "GROUND_TRUTH_SNAPSHOT", "reason": "SEC payload contains no recent filings"}
    return {"status": "REAL", "fund": fund, "cik": cik, "state": "GROUND_TRUTH_SNAPSHOT", "filings": [{"accession_number": filings.get("accessionNumber", [None] * len(forms))[i], "form_type": forms[i], "filed_at": filings.get("filingDate", [None] * len(forms))[i], "period_end": filings.get("reportDate", [None] * len(forms))[i]} for i in range(len(forms))]}


def validate_source_conflict(sec_value: float | None, issuer_value: float | None, tolerance_pct: float = 0.5) -> dict[str, Any]:
    if sec_value is None or issuer_value is None:
        return {"status": "UNKNOWN", "difference_abs": None, "difference_pct": None, "confidence": None, "reason": "one source did not report BTC holdings"}
    difference = abs(sec_value - issuer_value)
    pct = difference / abs(sec_value) * 100 if sec_value else 0.0
    return {"status": "SOURCE_CONFLICT" if pct > tolerance_pct else "MATCH", "difference_abs": difference, "difference_pct": pct, "confidence": 100 if pct <= tolerance_pct else 40, "tolerance_pct": tolerance_pct}


@dataclass(frozen=True)
class ExchangeLabel:
    address: str
    entity: str
    label: str
    category: str
    source: str
    source_url: str | None
    first_seen: str | None
    last_verified: str | None
    confidence: str
    verification_method: str


def classify_utxo_transfer(from_label: ExchangeLabel | None, to_label: ExchangeLabel | None, amount_btc: float) -> dict[str, Any]:
    """Exclude internal, uncertain, and unknown movements from observed flow."""
    from_exchange = from_label is not None and from_label.entity.upper() == "BINANCE"
    to_exchange = to_label is not None and to_label.entity.upper() == "BINANCE"
    eligible = {"VERIFIED", "HIGH"}
    if from_exchange and to_exchange:
        classification, eligible_for_flow = "BINANCE_INTERNAL", False
    elif to_exchange and from_label and from_label.confidence in eligible:
        classification, eligible_for_flow = "EXTERNAL_TO_BINANCE", True
    elif from_exchange and to_label and to_label.confidence in eligible:
        classification, eligible_for_flow = "BINANCE_TO_EXTERNAL", True
    elif from_exchange or to_exchange:
        classification, eligible_for_flow = "UNCERTAIN", False
    else:
        classification, eligible_for_flow = "UNKNOWN", False
    return {"classification": classification, "amount_btc": amount_btc, "eligible_for_observed_flow": eligible_for_flow, "internal_transfer_excluded": classification == "BINANCE_INTERNAL"}


def aggregate_observed_exchange_flow(transfers: Iterable[Mapping[str, Any]]) -> dict[str, Any]:
    total = sum(float(x.get("amount_btc") or 0) for x in transfers)
    inflow = sum(float(x.get("amount_btc") or 0) for x in transfers if x.get("classification") == "EXTERNAL_TO_BINANCE" and x.get("eligible_for_observed_flow"))
    outflow = sum(float(x.get("amount_btc") or 0) for x in transfers if x.get("classification") == "BINANCE_TO_EXTERNAL" and x.get("eligible_for_observed_flow"))
    internal = sum(float(x.get("amount_btc") or 0) for x in transfers if x.get("classification") == "BINANCE_INTERNAL")
    unknown = total - inflow - outflow - internal
    coverage = (inflow + outflow) / total * 100 if total else 0.0
    return {"status": "DERIVED" if inflow or outflow else "UNAVAILABLE", "observed_inflow_btc": inflow, "observed_outflow_btc": outflow, "observed_netflow_btc": inflow - outflow, "classified_coverage_pct": coverage, "verified_label_coverage_pct": coverage, "unknown_flow_pct": unknown / total * 100 if total else 0.0, "internal_transfer_pct": internal / total * 100 if total else 0.0, "confidence": 90 if coverage >= 90 else 60 if coverage >= 50 else 20, "terminology": "OBSERVED classified netflow; low coverage is not strong trade evidence"}


def forward_outcomes(rows: list[Mapping[str, Any]], horizons: Mapping[str, int] | None = None) -> list[dict[str, Any]]:
    horizons = horizons or {"1m": 60, "5m": 300, "15m": 900, "30m": 1800, "1h": 3600, "4h": 14400, "24h": 86400}
    ordered = sorted((dict(x) for x in rows), key=lambda x: int(x.get("timestamp", 0)))
    result = []
    for index, row in enumerate(ordered):
        current = _num(row.get("price"))
        if current is None:
            continue
        later = ordered[index + 1:]
        outcomes: dict[str, Any] = {}
        for name, seconds in horizons.items():
            future = next((x for x in later if int(x.get("timestamp", 0)) >= int(row["timestamp"]) + seconds), None)
            if not future or _num(future.get("price")) is None:
                outcomes[name] = None
            else:
                outcomes[name] = (_num(future.get("price")) - current) / current * 100
        result.append({**row, "forward_returns": outcomes})
    return result


def walk_forward(rows: list[Mapping[str, Any]], train_size: int, validation_size: int, test_size: int) -> dict[str, Any]:
    ordered = sorted((dict(x) for x in rows), key=lambda x: int(x.get("timestamp", 0)))
    if len(ordered) < train_size + validation_size + test_size:
        return {"status": "UNAVAILABLE", "reason": "insufficient chronological samples", "sample_size": len(ordered)}
    return {"status": "DERIVED", "train": ordered[:train_size], "validation": ordered[train_size:train_size + validation_size], "out_of_sample": ordered[train_size + validation_size:train_size + validation_size + test_size], "shuffle": False}


def calibrate_scores(rows: Iterable[Mapping[str, Any]], score_key: str = "state_score", outcome_key: str = "forward_returns", horizon: str = "1h", min_samples: int = 30) -> dict[str, Any]:
    buckets: dict[int, list[float]] = {}
    for row in rows:
        score = _num(row.get(score_key))
        outcome = row.get(outcome_key, {}).get(horizon) if isinstance(row.get(outcome_key), Mapping) else None
        if score is None or outcome is None:
            continue
        buckets.setdefault(int(score // 10 * 10), []).append(float(outcome) > 0)
    result = []
    for bucket, values in sorted(buckets.items()):
        result.append({"score_bucket": bucket, "sample_size": len(values), "calibrated_probability": round(sum(values) / len(values) * 100, 2) if len(values) >= min_samples else None, "status": "REAL" if len(values) >= min_samples else "LOW_CONFIDENCE"})
    return {"status": "DERIVED" if result else "UNAVAILABLE", "horizon": horizon, "score_is_probability": False, "buckets": result, "methodology": "chronological empirical continuation frequency; no future rows in score calculation"}
