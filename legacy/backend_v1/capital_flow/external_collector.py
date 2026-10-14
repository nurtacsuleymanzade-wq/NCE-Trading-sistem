"""Independent Phase 2-4 context poller.

This process owns no Binance market-data sockets.  It only polls slow external
context sources and writes an atomic state document consumed by the API.
"""
from __future__ import annotations

import argparse
import json
import os
import signal
import time
from pathlib import Path
from typing import Any

from .graphsense import aggregate_exchange_flow, resource_audit
from .institutional import ADAPTERS, ETF_CONFIG, SECClient, holdings_delta, institutional_aggregate
from .phase2 import BinanceWeb3Adapter, CoinMetricsCommunityAdapter, normalize_smart_money, smart_wallet_quality
from .storage import CapitalFlowStore


class ExternalContextCollector:
    def __init__(self, db_path: str | None = None, state_path: str | None = None, cache_path: str | None = None, skill_dir: str | None = None) -> None:
        self.db_path = db_path or os.environ.get("NCE_CAPITAL_FLOW_DB", "data/capital_flow.sqlite3")
        self.state_path = Path(state_path or os.environ.get("NCE_CAPITAL_FLOW_CONTEXT_STATE", "data/capital_flow_context.json"))
        self.cache_path = cache_path or os.environ.get("NCE_CAPITAL_FLOW_COINMETRICS_CACHE", "data/coinmetrics_context.json")
        self.skill_dir = skill_dir or os.environ.get("NCE_CAPITAL_FLOW_BINANCE_SKILL_DIR", "/opt/nce-trader-terminal/.external/crypto-market-rank")
        self.stop_requested = False

    def _write(self, state: dict[str, Any]) -> None:
        self.state_path.parent.mkdir(parents=True, exist_ok=True)
        temp = self.state_path.with_suffix(self.state_path.suffix + ".tmp")
        temp.write_text(json.dumps(state, separators=(",", ":"), default=str))
        temp.replace(self.state_path)

    def once(self) -> dict[str, Any]:
        now = int(time.time() * 1000)
        state: dict[str, Any] = {"timestamp": now, "source": "NCE external context collector", "status": "PASS", "horizons": {}, "network_context": {}, "smart_money": {"status": "UNAVAILABLE", "btc_status": "UNAVAILABLE", "chains": []}, "institutional": {}, "exchange": {"status": "UNAVAILABLE", "reason": "GraphSense resource/label integration deferred"}, "graphsense_audit": resource_audit(), "errors": []}
        store = CapitalFlowStore(self.db_path)
        try:
            cm = CoinMetricsCommunityAdapter(self.cache_path)
            network = cm.network_context("1d", 30)
            state["network_context"] = network
            if network.get("status") != "UNAVAILABLE": store.insert_json("coinmetrics_raw", network, now)
        except Exception as exc:
            state["errors"].append({"source": "Coin Metrics", "error": type(exc).__name__})
            state["network_context"] = {"status": "UNAVAILABLE", "reason": type(exc).__name__}
        try:
            web3 = BinanceWeb3Adapter(self.skill_dir)
            chains = []
            for chain, name in (("56", "BSC"), ("CT_501", "Solana"), ("8453", "Base")):
                result = web3.run("smart-money-inflow", {"chainId": chain, "period": "24h"})
                normalized = normalize_smart_money(result)
                normalized["chain_name"] = name
                chains.append(normalized)
                store.insert_json("smart_money_raw", normalized, now)
            state["smart_money"] = {"status": "REAL" if any(row.get("status") == "REAL" for row in chains) else "UNAVAILABLE", "btc_status": "UNAVAILABLE", "chains": chains, "methodology": "chain-specific Web3 smart-money; not BTC spot or futures"}
            pnl = web3.run("address-pnl-rank", {"chainId": "CT_501", "period": "30d", "tag": "ALL", "pageNo": 1, "pageSize": 25})
            state["smart_wallet_quality"] = smart_wallet_quality(pnl)
        except Exception as exc:
            state["errors"].append({"source": "Binance Web3 Skills", "error": type(exc).__name__})
            state["smart_money"] = {"status": "UNAVAILABLE", "btc_status": "UNAVAILABLE", "reason": type(exc).__name__}
        points: list[dict[str, Any]] = []
        for fund, adapter in ADAPTERS.items():
            try:
                point = adapter.fetch_holdings()
                point["fund"] = fund
                store.insert_json("etf_source_raw", point, int(point.get("timestamp_ms") or now), fund=fund)
                points.append(point)
            except Exception as exc:
                point = {"fund": fund, "status": "UNAVAILABLE", "source": ETF_CONFIG[fund].get("official_url"), "reason": type(exc).__name__}
                points.append(point)
            if fund == "IBIT":
                state["institutional_ibit"] = point
        try:
            sec = SECClient()
            filings: dict[str, Any] = {}
            for fund, config in ETF_CONFIG.items():
                result = sec.submissions(config["cik"])
                filings[fund] = result
                payload = result.get("payload") or {}
                parsed = sec.parse_filings(payload, fund)
                for row in parsed[:20]:
                    if row.get("accession_number"):
                        store.insert_json("sec_filings_raw", row, now, accession_number=row["accession_number"], filed_at=row.get("filed_at"))
            state["sec"] = {"status": "REAL", "funds": {fund: {"status": value.get("status"), "filing_count": len(SECClient.parse_filings(value.get("payload") or {}, fund))} for fund, value in filings.items()}, "methodology": "SEC submissions metadata; intraday ETF flow is not inferred"}
        except Exception as exc:
            state["errors"].append({"source": "SEC EDGAR", "error": type(exc).__name__})
            state["sec"] = {"status": "UNAVAILABLE", "reason": type(exc).__name__}
        valid_points = [point for point in points if point.get("btc_holdings") is not None]
        state["institutional"] = institutional_aggregate(valid_points, {}) if valid_points else {"status": "UNAVAILABLE", "reason": "no validated issuer holdings"}
        state["institutional"]["funds"] = points
        state["institutional"]["source"] = "SEC/issuer ETF holdings"
        state["institutional"]["methodology"] = "holdings snapshots; cash flow only if official, otherwise derived holdings delta"
        self._write(state)
        return state

    def run(self, interval_seconds: int = 900) -> None:
        def stop(*_: Any) -> None:
            self.stop_requested = True
        signal.signal(signal.SIGTERM, stop); signal.signal(signal.SIGINT, stop)
        while not self.stop_requested:
            try: self.once()
            except Exception as exc:
                self._write({"timestamp": int(time.time() * 1000), "status": "UNRELIABLE", "errors": [{"source": "external collector", "error": type(exc).__name__}]})
            for _ in range(interval_seconds):
                if self.stop_requested: break
                time.sleep(1)


def main() -> None:
    parser = argparse.ArgumentParser(); parser.add_argument("--once", action="store_true"); parser.add_argument("--interval", type=int, default=900)
    args = parser.parse_args(); collector = ExternalContextCollector()
    if args.once: collector.once()
    else: collector.run(args.interval)


if __name__ == "__main__": main()
