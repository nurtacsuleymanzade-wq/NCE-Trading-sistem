#!/usr/bin/env python3
"""Build look-ahead-safe target-level Probability Map calibration.

The script reads closed historical candles only. Features are built from the
past prefix at each timestamp; future candles are used only as outcome labels.
"""
from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path

from capital_flow.probability_map import build_target_calibration, historical_target_replay


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--bars", default="data/bars_1m.json")
    parser.add_argument("--out", default="historical/calibration/target_probability.json")
    parser.add_argument("--warmup", type=int, default=120)
    parser.add_argument("--max-snapshots", type=int, default=None)
    parser.add_argument("--minimum-sample", type=int, default=30)
    args = parser.parse_args()

    bars_path = Path(args.bars)
    bars = json.loads(bars_path.read_text())
    replay = historical_target_replay(bars, timeframe_seconds=60, warmup_bars=args.warmup, max_snapshots=args.max_snapshots)
    calibration = build_target_calibration(replay, minimum_sample=args.minimum_sample)
    payload = {
        **calibration,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "source": str(bars_path),
        "raw_or_derived": "DERIVED",
        "no_lookahead": True,
        "replay": {
            "status": replay.get("status"),
            "sample_size": replay.get("sample_size", 0),
            "snapshot_size": replay.get("snapshot_size", 0),
            "timeframe_seconds": replay.get("timeframe_seconds"),
            "methodology": replay.get("methodology"),
        },
    }
    output = Path(args.out)
    output.parent.mkdir(parents=True, exist_ok=True)
    temporary = output.with_suffix(output.suffix + ".tmp")
    temporary.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n")
    temporary.replace(output)
    print(json.dumps({"status": payload["status"], "sample_size": payload["sample_size"], "snapshot_size": payload["snapshot_size"], "out": str(output), "metrics": payload["metrics"]}, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
