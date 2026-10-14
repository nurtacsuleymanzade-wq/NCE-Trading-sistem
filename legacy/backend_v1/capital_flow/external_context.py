from __future__ import annotations

import json
import os
import subprocess
import time
from dataclasses import dataclass, asdict
from pathlib import Path
from typing import Any, Iterable, Mapping


def unavailable(source: str, methodology: str, reason: str) -> dict[str, Any]:
    return {
        "status": "UNAVAILABLE",
        "source": source,
        "source_type": source,
        "timestamp": None,
        "age_seconds": None,
        "freshness": "UNKNOWN",
        "methodology": methodology,
        "confidence": None,
        "coverage": 0.0,
        "reason": reason,
    }


class CoinMetricsCommunityClient:
    """Metadata-first Coin Metrics Community adapter.

    It refuses to request hard-coded metrics before the reference-data cache
    has confirmed that the metric is available for BTC/community scope.
    """

    def __init__(self, cache_path: str | os.PathLike[str] = "data/coinmetrics_cache.json", base_url: str = "https://community-api.coinmetrics.io/v4") -> None:
        self.cache_path = Path(cache_path)
        self.base_url = base_url.rstrip("/")

    def _cache(self) -> dict[str, Any]:
        if not self.cache_path.exists():
            return {}
        try:
            return json.loads(self.cache_path.read_text())
        except (OSError, ValueError):
            return {}

    def discover(self) -> dict[str, Any]:
        try:
            import httpx
            response = httpx.get(f"{self.base_url}/catalog-v2/asset-metrics", params={"assets": "btc"}, timeout=15, headers={"User-Agent": "NCE-Capital-Flow/3.0"})
            response.raise_for_status()
            payload = response.json()
            discovered = []
            for asset in payload.get("data", []) if isinstance(payload, dict) else []:
                for metric in asset.get("metrics", []):
                    for frequency in metric.get("frequencies", []):
                        discovered.append({"metric_name": metric.get("metric"), "frequency": frequency.get("frequency"), "start_time": frequency.get("min_time"), "end_time": frequency.get("max_time"), "community_accessible": bool(frequency.get("community")), "source": self.base_url})
            self.cache_path.parent.mkdir(parents=True, exist_ok=True)
            cache_payload = {"discovered_at": int(time.time() * 1000), "payload": payload, "metrics": discovered}
            self.cache_path.write_text(json.dumps(cache_payload))
            return {"status": "REAL", "source": self.base_url, "payload": payload, "metrics": discovered, "metadata_cached": True, "coverage": len(discovered)}
        except Exception as exc:
            return unavailable(self.base_url, "GET /reference-data/asset-metrics?assets=btc; metadata cache", type(exc).__name__)

    def fetch(self, metrics: Iterable[str], frequency: str = "1d", limit: int = 30) -> dict[str, Any]:
        metric_names = [str(x) for x in metrics if str(x)]
        cache = self._cache()
        if not metric_names or not cache.get("payload"):
            return unavailable(self.base_url, "metadata-first GET /timeseries/asset-metrics", "metric list is empty or BTC metadata has not been discovered")
        available = {str(x.get("metric_name")) for x in cache.get("metrics", []) if x.get("community_accessible") and x.get("frequency") == frequency}
        allowed = [metric for metric in metric_names if metric in available]
        missing = [metric for metric in metric_names if metric not in available]
        if not allowed:
            return unavailable(self.base_url, "metadata-first GET /timeseries/asset-metrics", f"requested metrics are not community-accessible for BTC/{frequency}: {missing}")
        try:
            import httpx
            response = httpx.get(f"{self.base_url}/timeseries/asset-metrics", params={"assets": "btc", "metrics": ",".join(allowed), "frequency": frequency, "limit_per_asset": limit}, timeout=15, headers={"User-Agent": "NCE-Capital-Flow/3.0"})
            response.raise_for_status()
            payload = response.json()
            return {"status": "REAL", "source": self.base_url, "source_type": "Coin Metrics Community API", "timestamp": int(time.time() * 1000), "frequency": frequency, "metrics": allowed, "missing_metrics": missing, "payload": payload, "methodology": "metadata-validated secondary BTC network context; not primary trade flow", "confidence": 70 if not missing else 55, "coverage": len(allowed) / len(metric_names)}
        except Exception as exc:
            return unavailable(self.base_url, "GET /timeseries/asset-metrics; cached metadata and rate-limited requests", type(exc).__name__)


class BinanceSkillRunner:
    """Run Binance Web3 Skills only from a sandbox-local install."""

    ALLOWED = {"smart-money-inflow", "address-pnl-rank", "crypto-market-rank"}

    def __init__(self, skill_dir: str | os.PathLike[str] = "/tmp/nce-binance-skills") -> None:
        self.skill_dir = Path(skill_dir)

    def run(self, command: str, payload: Mapping[str, Any]) -> dict[str, Any]:
        if command not in self.ALLOWED:
            raise ValueError("unsupported Binance skill command")
        cli = self.skill_dir / "scripts" / "cli.mjs"
        if not cli.exists():
            return unavailable("Binance Skills Hub", "sandbox-local crypto-market-rank CLI", "skill is not installed in the sandbox; no global installation was attempted")
        try:
            result = subprocess.run(["node", str(cli), command, json.dumps(dict(payload), separators=(",", ":"))], capture_output=True, text=True, timeout=30, check=True)
            parsed = json.loads(result.stdout)
            chain = parsed.get("chain") or parsed.get("chain_name") or payload.get("chain")
            token = parsed.get("token") or parsed.get("symbol") or payload.get("token")
            if not chain:
                return unavailable("Binance Skills Hub", command, "payload did not identify chain; cannot call it BTC smart money")
            return {"status": "REAL", "source": "Binance Skills Hub crypto-market-rank", "command": command, "timestamp": int(time.time() * 1000), "chain": chain, "token": token, "period": parsed.get("period") or payload.get("period"), "payload": parsed, "methodology": "chain-specific Web3 rank context; not BTC Spot or Futures flow", "confidence": 70, "coverage": 1.0}
        except Exception as exc:
            return unavailable("Binance Skills Hub", command, type(exc).__name__)


@dataclass(frozen=True)
class HoldingsPoint:
    fund: str
    timestamp_ms: int
    btc_holdings: float
    shares_outstanding: float | None = None
    aum_usd: float | None = None
    nav_usd: float | None = None
    source: str = "unknown"
    source_type: str = "unknown"
    confidence: float | None = None


def holdings_flow(previous: HoldingsPoint | None, current: HoldingsPoint, reference_btc_price: float | None = None) -> dict[str, Any]:
    if not previous:
        return {"status": "UNAVAILABLE", "fund": current.fund, "btc_delta": None, "usd_holdings_delta": None, "methodology": "requires previous known holdings point"}
    delta = current.btc_holdings - previous.btc_holdings
    return {
        "status": "DERIVED",
        "fund": current.fund,
        "btc_delta": delta,
        "usd_holdings_delta": delta * reference_btc_price if reference_btc_price else None,
        "methodology": "BTC holdings change; not cash-flow ground truth",
        "source": current.source,
        "source_type": current.source_type,
        "confidence": current.confidence,
        "coverage": 1.0,
    }


def institutional_state(delta_5d_btc: float | None, confidence: float | None) -> dict[str, Any]:
    if delta_5d_btc is None:
        return unavailable("SEC/issuer ETF holdings", "official holdings snapshots and derived BTC delta", "no validated holdings series")
    magnitude = abs(delta_5d_btc)
    state = "STRONG_INFLOW" if delta_5d_btc > 0 and magnitude > 1000 else "INFLOW" if delta_5d_btc > 0 else "STRONG_OUTFLOW" if delta_5d_btc < -1000 else "OUTFLOW" if delta_5d_btc < 0 else "NEUTRAL"
    return {"status": "DERIVED", "state": state, "delta_5d_btc": delta_5d_btc, "confidence": confidence, "methodology": "holdings delta; not direct net ETF cash flow"}


def network_context(payload: Mapping[str, Any] | None) -> dict[str, Any]:
    """Classify only returned Coin Metrics values; absent fields remain unknown."""
    if not payload or payload.get("status") not in {"REAL", "DERIVED"}:
        return unavailable("Coin Metrics Community", "metadata-validated BTC daily network metrics", "no validated payload")
    rows = (payload.get("payload") or {}).get("data", [])
    if len(rows) < 2:
        return unavailable("Coin Metrics Community", "daily BTC network context", "fewer than two observations")
    latest, previous = rows[-1], rows[-2]
    values = []
    for key in ("TxCnt", "AdrActCnt", "FeeTotNtv"):
        if latest.get(key) is not None and previous.get(key) is not None:
            values.append(float(latest[key]) - float(previous[key]))
    if not values:
        return unavailable("Coin Metrics Community", "daily BTC network context", "selected metrics absent from payload")
    positive = sum(x > 0 for x in values)
    negative = sum(x < 0 for x in values)
    state = "EXPANDING" if positive >= 2 else "CONTRACTING" if negative >= 2 else "MIXED" if positive and negative else "NEUTRAL"
    return {"status": "DERIVED", "state": state, "strength": round(abs(sum(values)) / max(1, len(values)), 4), "confidence": payload.get("confidence"), "timeframe": "1d", "why": [f"{key} direction={('up' if float(latest[key]) > float(previous[key]) else 'down' if float(latest[key]) < float(previous[key]) else 'flat')}" for key in ("TxCnt", "AdrActCnt", "FeeTotNtv") if latest.get(key) is not None and previous.get(key) is not None], "methodology": "directional comparison of returned daily network metrics; secondary context only"}


def classify_exchange_transaction(from_entity: str | None, to_entity: str | None, exchange_entity: str = "BINANCE") -> dict[str, Any]:
    source = (from_entity or "UNKNOWN").upper()
    target = (to_entity or "UNKNOWN").upper()
    exchange = exchange_entity.upper()
    source_is_exchange = source == exchange
    target_is_exchange = target == exchange
    if source_is_exchange and target_is_exchange:
        classification, confidence = "BINANCE_INTERNAL", "HIGH"
    elif target_is_exchange and source != "UNKNOWN":
        classification, confidence = "EXTERNAL_TO_BINANCE", "HIGH"
    elif source_is_exchange and target != "UNKNOWN":
        classification, confidence = "BINANCE_TO_EXTERNAL", "HIGH"
    else:
        classification, confidence = "UNCERTAIN", "LOW"
    return {"classification": classification, "confidence": confidence}


def aggregate_exchange_flow(transactions: Iterable[Mapping[str, Any]]) -> dict[str, Any]:
    totals = {"inflow_btc": 0.0, "outflow_btc": 0.0, "internal_btc": 0.0, "unknown_btc": 0.0, "covered_btc": 0.0, "total_btc": 0.0}
    rows = []
    for tx in transactions:
        amount = float(tx.get("btc_amount") or 0.0)
        classification = classify_exchange_transaction(tx.get("from_entity"), tx.get("to_entity"), tx.get("exchange_entity", "BINANCE"))
        totals["total_btc"] += amount
        if classification["classification"] == "EXTERNAL_TO_BINANCE":
            totals["inflow_btc"] += amount; totals["covered_btc"] += amount
        elif classification["classification"] == "BINANCE_TO_EXTERNAL":
            totals["outflow_btc"] += amount; totals["covered_btc"] += amount
        elif classification["classification"] == "BINANCE_INTERNAL":
            totals["internal_btc"] += amount
        else:
            totals["unknown_btc"] += amount
        rows.append({**dict(tx), **classification})
    totals["net_btc"] = totals["inflow_btc"] - totals["outflow_btc"]
    totals["coverage_pct"] = totals["covered_btc"] / totals["total_btc"] * 100 if totals["total_btc"] else 0.0
    totals["status"] = "DERIVED" if totals["covered_btc"] else "UNAVAILABLE"
    totals["methodology"] = "verified external inflow minus verified external outflow; internal and unknown excluded"
    return {"summary": totals, "transactions": rows}
