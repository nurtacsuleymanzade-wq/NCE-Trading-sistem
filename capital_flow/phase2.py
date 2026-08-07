"""Phase 2 external context adapters.

These adapters deliberately produce context, never primary execution flow.
They are usable by live polling and historical replay because the normalized
objects contain the source timestamp and the original raw payload.
"""
from __future__ import annotations

import json
import os
import subprocess
import time
from pathlib import Path
from typing import Any, Iterable, Mapping

from .external_context import unavailable


COIN_METRICS_CANDIDATES = (
    "TxCnt", "AdrActCnt", "FeeTotNtv", "HashRate", "SplyCur", "CapMrktCurUSD",
)
SMART_MONEY_CHAINS = {"56": "BSC", "CT_501": "Solana", "8453": "Base"}


class CoinMetricsCommunityAdapter:
    """Metadata-first Community API client with pagination and cache."""

    def __init__(self, cache_path: str | os.PathLike[str] = "data/coinmetrics_context.json", base_url: str = "https://community-api.coinmetrics.io/v4") -> None:
        self.cache_path = Path(cache_path)
        self.base_url = base_url.rstrip("/")

    def _load(self) -> dict[str, Any]:
        try:
            return json.loads(self.cache_path.read_text())
        except (OSError, ValueError):
            return {}

    def _save(self, value: dict[str, Any]) -> None:
        self.cache_path.parent.mkdir(parents=True, exist_ok=True)
        temp = self.cache_path.with_suffix(self.cache_path.suffix + ".tmp")
        temp.write_text(json.dumps(value, separators=(",", ":")))
        temp.replace(self.cache_path)

    def discover(self, max_pages: int = 20) -> dict[str, Any]:
        try:
            import httpx
            rows: list[dict[str, Any]] = []
            next_token: str | None = None
            for _ in range(max_pages):
                params = {"next_page_token": next_token} if next_token else {}
                params["assets"] = "btc"
                response = httpx.get(f"{self.base_url}/catalog-v2/asset-metrics", params=params, timeout=20, headers={"User-Agent": "NCE-Capital-Flow/3.0"})
                response.raise_for_status()
                payload = response.json()
                rows.extend(payload.get("data", []))
                next_token = payload.get("next_page_token")
                if not next_token:
                    break
            definitions = {}
            for asset in rows:
                for row in asset.get("metrics", []) if isinstance(asset, Mapping) else []:
                    if row.get("metric"):
                        definitions[str(row["metric"])] = row
            cache = {"discovered_at": int(time.time() * 1000), "metrics": definitions, "candidate_metrics": [m for m in COIN_METRICS_CANDIDATES if m in definitions]}
            self._save(cache)
            return {"status": "REAL", "source": self.base_url, "source_type": "Coin Metrics Community catalog-v2 metadata", "timestamp": cache["discovered_at"], "methodology": "paginated metadata discovery before timeseries calls", "coverage": 100.0, "confidence": 95.0, "metrics": definitions, "candidate_metrics": cache["candidate_metrics"]}
        except Exception as exc:
            return unavailable(self.base_url, "paginated /catalog-v2/asset-metrics metadata discovery", type(exc).__name__)

    def available_metrics(self) -> list[str]:
        cache = self._load()
        return list((cache.get("metrics") or {}).keys())

    def fetch(self, metrics: Iterable[str], frequency: str = "1d", limit: int = 30) -> dict[str, Any]:
        requested = [str(metric) for metric in metrics if str(metric)]
        available = set(self.available_metrics())
        if not available:
            discovered = self.discover()
            available = set(discovered.get("metrics", {}))
        rejected = sorted(set(requested) - available)
        selected = [metric for metric in requested if metric in available]
        if rejected or not selected:
            return unavailable(self.base_url, "metadata-validated /timeseries/asset-metrics", f"unavailable or undiscovered metrics: {rejected or requested}")
        try:
            import httpx
            response = httpx.get(f"{self.base_url}/timeseries/asset-metrics", params={"assets": "btc", "metrics": ",".join(selected), "frequency": frequency, "limit_per_asset": limit}, timeout=20, headers={"User-Agent": "NCE-Capital-Flow/3.0"})
            response.raise_for_status()
            return {"status": "REAL", "source": self.base_url, "source_type": "Coin Metrics Community API", "timestamp": int(time.time() * 1000), "frequency": frequency, "metrics": selected, "payload": response.json(), "methodology": "secondary BTC network context; never primary execution flow", "confidence": 80.0, "coverage": 100.0}
        except Exception as exc:
            return unavailable(self.base_url, "metadata-validated /timeseries/asset-metrics", type(exc).__name__)

    def network_context(self, frequency: str = "1d", limit: int = 30) -> dict[str, Any]:
        metrics = [metric for metric in COIN_METRICS_CANDIDATES if metric in set(self.available_metrics())]
        if not metrics:
            self.discover()
            metrics = [metric for metric in COIN_METRICS_CANDIDATES if metric in set(self.available_metrics())]
        fetched = self.fetch(metrics, frequency, limit)
        if fetched.get("status") != "REAL":
            return fetched
        rows = fetched.get("payload", {}).get("data", [])
        by_metric: dict[str, list[tuple[str, float]]] = {metric: [] for metric in metrics}
        for row in rows:
            for metric in metrics:
                try:
                    if row.get(metric) is not None:
                        by_metric[metric].append((str(row.get("time")), float(row[metric])))
                except (TypeError, ValueError):
                    continue
        changes: dict[str, float | None] = {}
        signs: list[int] = []
        for metric, points in by_metric.items():
            if len(points) < 2 or points[-2][1] == 0:
                changes[metric] = None
                continue
            pct = (points[-1][1] - points[-2][1]) / abs(points[-2][1]) * 100
            changes[metric] = pct
            if abs(pct) >= 1:
                signs.append(1 if pct > 0 else -1)
        if not signs:
            state = "NEUTRAL"
        elif all(value > 0 for value in signs):
            state = "EXPANDING"
        elif all(value < 0 for value in signs):
            state = "CONTRACTING"
        else:
            state = "MIXED"
        strength = min(100.0, abs(sum(signs)) / max(1, len(signs)) * 100.0)
        return {**fetched, "state": state, "strength": strength, "changes_pct": changes, "available_metrics": metrics, "status": "DERIVED", "methodology": "directional changes across available Community BTC network metrics; weak context only"}


class BinanceWeb3Adapter:
    """Safe wrapper around a sandbox/local crypto-market-rank CLI."""

    ALLOWED = {"smart-money-inflow", "address-pnl-rank", "crypto-market-rank"}

    def __init__(self, skill_dir: str | os.PathLike[str] = "/opt/nce-trader-terminal/.external/crypto-market-rank") -> None:
        self.skill_dir = Path(skill_dir)

    def run(self, command: str, payload: Mapping[str, Any]) -> dict[str, Any]:
        if command not in self.ALLOWED:
            raise ValueError("unsupported Binance Web3 command")
        chain = str(payload.get("chainId", ""))
        if command == "smart-money-inflow" and chain not in SMART_MONEY_CHAINS:
            return unavailable("Binance Skills Hub", command, f"chain {chain or 'UNKNOWN'} is not supported for smart-money-inflow")
        if command == "address-pnl-rank" and chain not in SMART_MONEY_CHAINS:
            return unavailable("Binance Skills Hub", command, f"chain {chain or 'UNKNOWN'} is not supported for address-pnl-rank")
        cli = self.skill_dir / "scripts" / "cli.mjs"
        if not cli.exists():
            return unavailable("Binance Skills Hub", command, "local skill installation is missing")
        try:
            result = subprocess.run(["node", str(cli), command, json.dumps(dict(payload), separators=(",", ":"))], capture_output=True, text=True, timeout=30, check=True)
            raw = json.loads(result.stdout)
            if raw.get("code") not in (None, "000000"):
                return unavailable("Binance Skills Hub", command, f"upstream code {raw.get('code')}")
            return {"status": "REAL", "source": "Binance Skills Hub crypto-market-rank", "source_type": "chain-specific Web3 rank", "timestamp": int(time.time() * 1000), "chain": chain, "chain_name": SMART_MONEY_CHAINS.get(chain), "command": command, "period": payload.get("period"), "payload": raw, "methodology": "Web3 smart-money/PnL context; not BTC Spot, Futures, or Top Trader flow", "confidence": 70.0, "coverage": 100.0}
        except Exception as exc:
            return unavailable("Binance Skills Hub", command, type(exc).__name__)


def normalize_smart_money(result: Mapping[str, Any]) -> dict[str, Any]:
    payload = result.get("payload") or {}
    rows = payload.get("data", []) if isinstance(payload, Mapping) else []
    return {"status": result.get("status", "UNAVAILABLE"), "source": result.get("source", "Binance Skills Hub"), "timestamp": result.get("timestamp"), "chain": result.get("chain"), "chain_name": result.get("chain_name"), "period": result.get("period"), "rows": [{"token": row.get("tokenName"), "contract": row.get("ca"), "net_inflow": row.get("inflow"), "traders": row.get("traders"), "rank": index + 1, "timestamp": result.get("timestamp")} for index, row in enumerate(rows) if isinstance(row, Mapping)], "metadata": {"status": result.get("status"), "methodology": result.get("methodology"), "confidence": result.get("confidence"), "coverage": result.get("coverage")}}


def smart_wallet_quality(result: Mapping[str, Any]) -> dict[str, Any]:
    payload = result.get("payload") or {}
    data = payload.get("data", {}).get("data", []) if isinstance(payload, Mapping) else []
    rows = []
    for row in data if isinstance(data, list) else []:
        rows.append({key: row.get(key) for key in ("address", "addressLabel", "realizedPnl", "realizedPnlPercent", "winRate", "period", "lastActivity", "tags") if key in row})
    return {"status": result.get("status", "UNAVAILABLE"), "source": result.get("source", "Binance Skills Hub"), "chain": result.get("chain"), "period": result.get("period"), "rows": rows, "methodology": "actual returned PnL/rank fields only; no synthetic score", "confidence": result.get("confidence"), "coverage": result.get("coverage")}
