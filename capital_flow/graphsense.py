"""GraphSense feasibility audit and coverage-aware exchange flow logic.

No node, Cassandra cluster, or GraphSense process is installed by this module.
"""
from __future__ import annotations

import os
import shutil
from dataclasses import asdict, dataclass
from typing import Any, Iterable, Mapping


CONFIDENCE = {"VERIFIED": 1.0, "HIGH": 0.85, "MEDIUM": 0.55, "LOW": 0.2}


def resource_audit() -> dict[str, Any]:
    disk = shutil.disk_usage("/")
    memory = {line.split(":", 1)[0]: line.split()[1] for line in open("/proc/meminfo", encoding="utf-8") if ":" in line}
    load = os.getloadavg() if hasattr(os, "getloadavg") else (None, None, None)
    cassandra = any(os.path.exists(path) for path in ("/etc/cassandra", "/usr/bin/cassandra", "/usr/sbin/cassandra"))
    free_gib = round(disk.free / 1024**3, 2)
    ram_gib = round(int(memory.get("MemTotal", 0)) / 1024**2, 2)
    recommendation = "DO_NOT_INSTALL" if not cassandra else "REVIEW_EXISTING_CASSANDRA"
    return {"status": "REAL", "disk_free_gib": free_gib, "disk_total_gib": round(disk.total / 1024**3, 2),
            "ram_total_gib": ram_gib, "cpu_count": os.cpu_count(), "load": list(load),
            "cassandra_detected": cassandra, "recommendation": recommendation,
            "methodology": "read-only local resource audit; GraphSense self-host is not automatically installed"}


@dataclass(frozen=True)
class LabelRecord:
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


def classify_transaction(tx: Mapping[str, Any], binance_entities: set[str]) -> str:
    source = str(tx.get("from_entity") or tx.get("from") or "")
    target = str(tx.get("to_entity") or tx.get("to") or "")
    source_is = source in binance_entities or bool(tx.get("from_is_binance"))
    target_is = target in binance_entities or bool(tx.get("to_is_binance"))
    if source_is and target_is: return "BINANCE_INTERNAL"
    if target_is and not source_is: return "EXTERNAL_TO_BINANCE"
    if source_is and not target_is: return "BINANCE_TO_EXTERNAL"
    if source or target: return "UNKNOWN"
    return "UNCERTAIN"


def aggregate_exchange_flow(transactions: Iterable[Mapping[str, Any]], binance_entities: set[str]) -> dict[str, Any]:
    rows = list(transactions)
    totals = {key: 0.0 for key in ("EXTERNAL_TO_BINANCE", "BINANCE_TO_EXTERNAL", "BINANCE_INTERNAL", "UNCERTAIN", "UNKNOWN")}
    for tx in rows:
        classification = tx.get("classification") or classify_transaction(tx, binance_entities)
        amount = float(tx.get("btc_amount") or tx.get("amount_btc") or 0)
        totals[classification] = totals.get(classification, 0.0) + amount
    relevant = sum(totals.values())
    classified = totals["EXTERNAL_TO_BINANCE"] + totals["BINANCE_TO_EXTERNAL"] + totals["BINANCE_INTERNAL"]
    verified = totals["EXTERNAL_TO_BINANCE"] + totals["BINANCE_TO_EXTERNAL"]
    coverage = classified / relevant * 100 if relevant else 0.0
    label_coverage = verified / relevant * 100 if relevant else 0.0
    confidence = "HIGH" if coverage >= 80 and label_coverage >= 60 else "MEDIUM" if coverage >= 40 else "LOW"
    net = totals["EXTERNAL_TO_BINANCE"] - totals["BINANCE_TO_EXTERNAL"]
    return {"status": "DERIVED" if verified > 0 else "UNAVAILABLE", "source": "GraphSense/verified exchange labels", "observed_inflow_btc": totals["EXTERNAL_TO_BINANCE"], "observed_outflow_btc": totals["BINANCE_TO_EXTERNAL"], "observed_netflow_btc": net, "classified_coverage_pct": round(coverage, 2), "verified_label_coverage_pct": round(label_coverage, 2), "unknown_flow_pct": round((totals["UNKNOWN"] + totals["UNCERTAIN"]) / relevant * 100, 2) if relevant else 0.0, "internal_transfer_pct": round(totals["BINANCE_INTERNAL"] / relevant * 100, 2) if relevant else 0.0, "confidence": confidence, "strength": min(100.0, abs(net) / max(relevant, 1e-9) * 100), "state": "INFLOW" if net > 0 else "OUTFLOW" if net < 0 else "NEUTRAL", "methodology": "verified external transfers only; internal, uncertain and unknown transfers excluded from signal", "coverage_warning": "Low coverage: context-only; not eligible as strong trade evidence" if confidence == "LOW" else None}
