#!/usr/bin/env python3
"""Download a bounded, immutable Binance historical inventory.

The script deliberately downloads only official Data Vision archives for one
symbol and one date window. Existing complete files are not downloaded again;
partial files are resumed with curl. A manifest records the HTTP headers,
download timestamp and SHA-256 for every archive.
"""
from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import os
import shutil
import subprocess
import tempfile
from pathlib import Path
from urllib.request import Request, urlopen


BASE = "https://data.binance.vision/data"
DATASETS = (
    ("spot", "aggTrades", "spot/daily/aggTrades/{symbol}/{symbol}-aggTrades-{date}.zip"),
    ("futures", "aggTrades", "futures/um/daily/aggTrades/{symbol}/{symbol}-aggTrades-{date}.zip"),
    ("futures", "klines_1m", "futures/um/daily/klines/{symbol}/1m/{symbol}-1m-{date}.zip"),
    ("futures", "mark_price_1m", "futures/um/daily/markPriceKlines/{symbol}/1m/{symbol}-1m-{date}.zip"),
    ("futures", "index_price_1m", "futures/um/daily/indexPriceKlines/{symbol}/1m/{symbol}-1m-{date}.zip"),
    ("futures", "premium_index_1m", "futures/um/daily/premiumIndexKlines/{symbol}/1m/{symbol}-1m-{date}.zip"),
    ("futures", "metrics", "futures/um/daily/metrics/{symbol}/{symbol}-metrics-{date}.zip"),
)


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def headers(url: str) -> dict[str, str]:
    request = Request(url, method="HEAD", headers={"User-Agent": "NCE-Capital-Flow/empirical-validation"})
    with urlopen(request, timeout=60) as response:
        return {str(key).lower(): str(value) for key, value in response.headers.items()}


def dates(start: dt.date, end: dt.date):
    cursor = start
    while cursor <= end:
        yield cursor
        cursor += dt.timedelta(days=1)


def download(url: str, destination: Path) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    if destination.exists():
        return
    partial = destination.with_suffix(destination.suffix + ".part")
    command = [
        "curl", "--fail", "--location", "--retry", "5", "--retry-delay", "2",
        "--connect-timeout", "30", "--max-time", "900", "--continue-at", "-",
        "--output", str(partial), url,
    ]
    subprocess.run(command, check=True)
    os.replace(partial, destination)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", default="historical/raw/binance")
    parser.add_argument("--symbol", default="BTCUSDT")
    parser.add_argument("--start", required=True)
    parser.add_argument("--end", required=True)
    parser.add_argument("--manifest", default="historical/dataset_manifest.json")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    root = Path(args.root)
    manifest_path = Path(args.manifest)
    start = dt.date.fromisoformat(args.start)
    end = dt.date.fromisoformat(args.end)
    existing = json.loads(manifest_path.read_text()) if manifest_path.exists() else {"schema_version": "1", "datasets": []}
    by_path = {row.get("path"): row for row in existing.get("datasets", [])}

    for day in dates(start, end):
        date = day.isoformat()
        for market, dataset, template in DATASETS:
            url = f"{BASE}/{template.format(symbol=args.symbol, date=date)}"
            relative = f"{market}/{dataset}/{args.symbol}-{dataset}-{date}.zip"
            destination = root / relative
            try:
                head = headers(url)
            except Exception as exc:
                row = {"path": str(destination), "source": url, "symbol": args.symbol, "market": market, "dataset": dataset, "date": date, "status": "UNAVAILABLE", "reason": type(exc).__name__}
                by_path[str(destination)] = row
                continue
            expected_size = int(head.get("content-length", "0") or 0)
            row = by_path.get(str(destination), {})
            if not args.dry_run:
                if destination.exists() and expected_size and destination.stat().st_size != expected_size:
                    destination.unlink()
                download(url, destination)
                actual_size = destination.stat().st_size
                if expected_size and actual_size != expected_size:
                    raise RuntimeError(f"size mismatch for {destination}: {actual_size} != {expected_size}")
                checksum = sha256(destination)
                destination.chmod(0o444)
            else:
                actual_size = destination.stat().st_size if destination.exists() else None
                checksum = row.get("sha256")
            row.update({
                "path": str(destination), "source": url, "symbol": args.symbol, "market": market,
                "dataset": dataset, "date": date, "status": "DOWNLOADED" if not args.dry_run else "PLANNED",
                "content_length": expected_size, "size_bytes": actual_size, "sha256": checksum,
                "downloaded_at": dt.datetime.now(dt.timezone.utc).isoformat(),
                "etag": head.get("etag"), "last_modified": head.get("last-modified"),
                "raw_immutable": True,
            })
            by_path[str(destination)] = row

    manifest_path.parent.mkdir(parents=True, exist_ok=True)
    payload = {"schema_version": "1", "generated_at": dt.datetime.now(dt.timezone.utc).isoformat(), "symbol": args.symbol, "start": args.start, "end": args.end, "datasets": sorted(by_path.values(), key=lambda x: x.get("path", ""))}
    temp = manifest_path.with_suffix(manifest_path.suffix + ".tmp")
    temp.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n")
    os.replace(temp, manifest_path)
    print(json.dumps({"manifest": str(manifest_path), "datasets": len(payload["datasets"]), "downloaded_bytes": sum(int(x.get("size_bytes") or 0) for x in payload["datasets"])}, indent=2))


if __name__ == "__main__":
    main()
