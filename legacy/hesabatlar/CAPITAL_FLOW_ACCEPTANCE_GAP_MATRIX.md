# Capital Flow Empirical Validation — Acceptance Gap Matrix

Audit date: 2026-08-07 UTC  
Evidence base: V3 reports, source code, tests, read-only production SQLite, official Binance Data Vision manifest, replay artifacts.

| REQUIREMENT | IMPLEMENTED | PARTIAL | UNAVAILABLE | NOT_IMPLEMENTED | EVIDENCE | FILE | TEST | LIVE VERIFIED |
|---|---:|---:|---:|---:|---|---|---|---:|
| Existing V3 reports audited |  | X |  |  | Reports overstate Phase 5 completion; evidence reviewed | `CAPITAL_FLOW_*_REPORT.md` | audit matrix | X |
| Real missing acceptance items identified | X |  |  |  | Matrix records concrete gaps | this file | manual audit | X |
| ETF universe audited (11 funds) |  | X |  |  | Normalized per-fund output; only IBIT real | `historical/etf_coverage.json` | `test_etf_adapter_normalization` | IBIT only |
| Available official ETF adapters completed |  | X |  |  | IBIT issuer CSV; remaining daily issuer adapters not validated | `capital_flow/institutional.py` | `test_missing_historical_source_not_zero` | IBIT |
| Historical data inventory created | X |  |  |  | 210 planned datasets + local SQLite tables | `historical/historical_inventory.json` | `test_historical_manifest` | X |
| Maximum safe historical dataset acquired |  | X |  |  | 204/210 official archives, 670,945,568 bytes; 2026-08-06 futures gap | `historical/dataset_manifest.json` | manifest checks | X |
| Raw/normalized/features/labels separated | X |  |  |  | Separate directories and artifacts | `historical/{raw,normalized,features,labels}` | artifact existence | X |
| Dataset manifest with checksum/schema/date | X |  |  |  | source/date/size/etag/checksum captured | `historical/dataset_manifest.json` | `test_historical_manifest` | X |
| LIVE/REPLAY same calculation engine | X |  |  |  | Replay imports and calls `CapitalFlowEngine.snapshot` | `capital_flow/empirical.py`, `capital_flow/replay.py` | `test_live_replay_parity` | unit parity |
| Live/replay parity test PASS | X |  |  |  | Stable fields match; volatile age metadata excluded | `tests/test_empirical_validation.py` | `test_live_replay_parity` | unit |
| Event-time chronological replay | X |  |  |  | Prefix/window timestamp cutoff | `capital_flow/empirical.py` | `test_replay_chronological` | replay |
| Past-only percentile thresholds | X |  |  |  | Thresholds use prior trades and hourly refresh | `capital_flow/empirical.py` | `test_no_future_trades_in_percentiles` | replay |
| No future ETF/SEC data | X |  |  |  | Publication timestamp helper; ETF/SEC not backfilled without availability | `capital_flow/empirical.py` | `test_no_future_etf_data`, `test_no_future_sec_filing` | not live |
| Historical regimes reconstructed |  | X |  |  | 314 events; 7 existing regimes observed | `historical/features/regime_statistics.json` | replay run | no production write |
| Forward labels generated | X |  |  |  | 1m–24h labels, MFE/MAE, squeeze/target labels | `historical/labels/forward_labels.jsonl` | `test_forward_returns_are_labels_only` | replay |
| Regime statistics generated | X |  |  |  | sample sizes and horizon summaries | `historical/features/regime_statistics.json` | artifact QA | replay |
| Conditional edge analysis generated | X |  |  |  | Four predeclared context cuts; orderbook cell unavailable | `historical/features/conditional_statistics.json` | artifact QA | replay |
| Minimum sample rules applied | X |  |  |  | `min_sample=20`; insufficient cells explicitly flagged | `capital_flow/empirical.py` | `test_calibration_sample_counts` | replay |
| Walk-forward completed | X |  |  |  | 60/20/20 chronological split | `historical/calibration/walk_forward.json` | `test_walk_forward_no_overlap` | replay |
| OOS results reported |  | X |  |  | OOS exists but n=61 resolved, below validation gate | `historical/calibration/walk_forward.json` | `test_oos_not_used_for_threshold_selection` | not production |
| Score/probability separation enforced | X |  |  |  | `score_is_probability=false`; calibrated mapping separate | engine/API/report | `test_score_not_probability` | X |
| Calibration results generated |  | X |  |  | Reliability table + Wilson intervals; sparse bins | `historical/calibration/walk_forward.json` | `test_calibration_sample_counts` | replay |
| DATA_CONFIDENCE and EMPIRICAL_CONFIDENCE separate | X |  |  |  | Separate JSON objects and API fields | `historical/calibration/confidence.json` | artifact QA | API local |
| Historical context API exposed | X |  |  |  | Additive endpoint and level fallback A–D | `capital_flow/http_api.py` | direct endpoint smoke | local |
| UI historical context works |  | X |  |  | Additive card reads `historical_context`; production artifact availability pending deploy | `index.html` | JS parse pending | not public-verified |
| Production regression PASS |  | X |  |  | Capital Flow scoped 30/30 + empirical 15/15; full repo has 6 unrelated legacy failures; live health/summary HTTP 200 | test output, live curl | pytest | HTTP 200; historical-context absent |
| Live collector unaffected |  | X |  |  | No production service mutation; read-only DB audit; live summary returned PASS | production report, live curl | no-write audit | partially verified |
| No fake values | X |  |  |  | unavailable fields null, no fake ETF zero, GraphSense untouched | adapters/report | missing-source test | X |
| GraphSense untouched | X |  |  |  | No install/change; status remains deferred | `capital_flow/graphsense.py` | resource audit | X |
| Final empirical verdict |  | X |  |  | OOS/sample gate fails; honest negative verdict | `historical/validation_summary.json` | artifact QA | X |

## Current gate

`CAPITAL_FLOW_NOT_EMPIRICALLY_VALIDATED` — the pipeline is implemented and produces auditable evidence, but OOS resolved sample is 61 and no stable empirical edge has been established.
