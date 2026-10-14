#!/usr/bin/env python3
"""Run bounded historical replay and write auditable validation artifacts."""
from __future__ import annotations

import argparse
import datetime as dt
import json
import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from capital_flow.empirical import (
    add_forward_labels,
    atomic_json,
    build_data_confidence,
    conditional_statistics,
    empirical_confidence,
    ensure_layout,
    inventory,
    load_archive_day,
    load_local_day,
    reconstruct_day,
    regime_statistics,
    utc_now,
    walk_forward_calibration,
)
from capital_flow.institutional import audit_etf_universe


def write_jsonl(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    with temporary.open("w") as handle:
        for row in rows:
            handle.write(json.dumps(row, separators=(",", ":"), default=str) + "\n")
    temporary.replace(path)


def date_range(start: str, end: str):
    cursor = dt.date.fromisoformat(start)
    last = dt.date.fromisoformat(end)
    while cursor <= last:
        yield cursor.isoformat()
        cursor += dt.timedelta(days=1)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", default="historical")
    parser.add_argument("--archive-root", default="historical/raw/binance")
    parser.add_argument("--manifest", default="historical/dataset_manifest.json")
    parser.add_argument("--db", default="/var/lib/nce-trading/capital_flow.sqlite3")
    parser.add_argument("--start", required=True)
    parser.add_argument("--end", required=True)
    parser.add_argument("--local-date", default="2026-08-07")
    parser.add_argument("--timeframe-seconds", type=int, default=300)
    parser.add_argument("--min-sample", type=int, default=20)
    parser.add_argument("--max-events-per-day", type=int, default=400)
    args = parser.parse_args()

    root = Path(args.root)
    paths = ensure_layout(root)
    all_events: list[dict] = []
    day_inventory: list[dict] = []
    for date in date_range(args.start, args.end):
        if date == args.local_date:
            spot, futures, prices, metrics = load_local_day(args.db, date)
            source = "local production SQLite snapshot"
        else:
            spot, futures, prices, metrics = load_archive_day(Path(args.archive_root), date)
            source = "Binance Data Vision official archive"
        if not spot or not futures:
            day_inventory.append({"date": date, "status": "UNAVAILABLE", "spot_rows": len(spot), "futures_rows": len(futures), "source": source})
            continue
        events = reconstruct_day(spot, futures, prices, metrics, date=date, timeframe_seconds=args.timeframe_seconds, source=source, max_events=args.max_events_per_day)
        labels = add_forward_labels(events, prices)
        all_events.extend(labels)
        day_inventory.append({"date": date, "status": "DERIVED", "spot_rows": len(spot), "futures_rows": len(futures), "price_rows": len(prices), "metrics_rows": len(metrics), "event_rows": len(labels), "source": source})

    write_jsonl(paths["events"] / "capital_flow_events.jsonl", all_events)
    write_jsonl(paths["labels"] / "forward_labels.jsonl", [{"timestamp_ms": row.get("timestamp_ms"), "regime": row.get("regime"), "labels": row.get("labels", {})} for row in all_events])
    stats = regime_statistics(all_events, args.min_sample)
    conditions = conditional_statistics(all_events, args.min_sample)
    validation = walk_forward_calibration(all_events, max(5, args.min_sample))
    inv = inventory(Path(args.archive_root), Path(args.manifest), args.db)
    etfs = audit_etf_universe()
    # The external collector already persisted the last issuer result in the
    # production SQLite. Use that read-only evidence when the current network
    # probe is unavailable; never replace a real row with zero.
    try:
        conn = sqlite3.connect(f"file:{Path(args.db).resolve()}?mode=ro", uri=True)
        latest = {}
        for timestamp_ms, fund, payload_json in conn.execute("SELECT timestamp_ms, fund, payload_json FROM etf_source_raw ORDER BY timestamp_ms"):
            latest[fund] = json.loads(payload_json)
        conn.close()
        for row in etfs:
            persisted = latest.get(row["fund"])
            if row.get("adapter_status") == "UNAVAILABLE" and persisted and persisted.get("status") == "REAL":
                for key in ("btc_holdings", "shares_outstanding", "aum_usd", "nav_usd", "coverage", "confidence", "as_of", "timestamp_ms"):
                    row[key] = persisted.get(key)
                row["coverage"] = row.get("coverage") if row.get("coverage") is not None else 0.5
                row["adapter_status"] = "REAL"
                row["daily_holdings_availability"] = "REAL"
                row["reason"] = "read-only production etf_source_raw snapshot"
    except (OSError, sqlite3.Error, ValueError, TypeError):
        pass
    data_confidence = build_data_confidence(inv, all_events)
    empirical = empirical_confidence(all_events, validation)

    atomic_json(paths["normalized"] / "replay_inventory.json", {"generated_at": utc_now(), "dates": day_inventory, "timeframe_seconds": args.timeframe_seconds, "engine": "capital_flow.engine.CapitalFlowEngine", "engine_path_shared_with_live": True, "lookahead_contract": {"features": "timestamp <= T", "percentiles": "past-only; bounded 200000 observation history", "labels": "future data allowed only in labels", "etf_sec": "not used in replay without publication timestamps"}})
    atomic_json(paths["features"] / "regime_statistics.json", stats)
    atomic_json(paths["features"] / "conditional_statistics.json", conditions)
    atomic_json(paths["calibration"] / "walk_forward.json", validation)
    atomic_json(paths["calibration"] / "confidence.json", {"DATA_CONFIDENCE": data_confidence, "EMPIRICAL_CONFIDENCE": empirical})
    atomic_json(root / "etf_coverage.json", {"generated_at": utc_now(), "funds": etfs, "normalized_schema": ["fund", "issuer", "official_source", "cik", "timestamp_ms", "btc_holdings", "shares_outstanding", "aum_usd", "nav_usd", "status", "coverage", "confidence"], "unavailable_fields_are_null": True})
    atomic_json(root / "historical_inventory.json", {"generated_at": utc_now(), "datasets": inv, "day_replay": day_inventory})
    atomic_json(root / "validation_summary.json", {"generated_at": utc_now(), "event_count": len(all_events), "regime_count": len(stats.get("regimes", {})), "regime_statistics": stats, "conditional_statistics": conditions, "walk_forward": validation, "DATA_CONFIDENCE": data_confidence, "EMPIRICAL_CONFIDENCE": empirical, "ETF_COVERAGE": etfs, "GRAPH_SENSE": "DEFERRED/UNAVAILABLE", "score_is_probability": False, "verdict": "CAPITAL_FLOW_EMPIRICALLY_VALIDATED" if empirical.get("level") in {"HIGH", "MEDIUM"} and (validation.get("splits", {}).get("OUT_OF_SAMPLE", {}).get("resolved_size") or 0) >= 100 else "CAPITAL_FLOW_NOT_EMPIRICALLY_VALIDATED"})
    print(json.dumps({"event_count": len(all_events), "regimes": len(stats.get("regimes", {})), "oos": validation.get("splits", {}).get("OUT_OF_SAMPLE"), "data_confidence": data_confidence, "empirical_confidence": empirical}, indent=2, default=str))


if __name__ == "__main__":
    main()
