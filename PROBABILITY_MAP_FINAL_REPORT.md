# NCE Probability Map — Final Implementation Report

Date: 2026-08-07  
Verdict: **PROBABILITY_MAP_PRODUCTION_READY** (V1, decision-support only)

V1 now has separate Liquidity, Liquidation and Probability engines; real
Spot/Futures order-book lifecycle data; explicitly estimated liquidation
inventory; target-level historical calibration; first-hit and multi-horizon
probabilities; ETA intervals; API/UI integration; live backend verification;
and a green repository regression run.

## 1. Architecture audit and reused modules

The forensic audit is in `PROBABILITY_MAP_AUDIT.md`. Reused components include
`CapitalFlowEngine`, Spot/Futures aggTrade and CVD, OI/funding/public
positioning, `DepthReconciler`, SQLite storage, historical replay primitives,
Target ETA and the existing static dashboard. Existing routes remain additive.

## 2. New modules and engine boundaries

`capital_flow/probability_map.py` contains three deliberately separate layers:

1. **Liquidity Engine** — observed passive order-book state and lifecycle.
2. **Liquidation Engine** — estimated OI-cohort inventory and liquidation zones.
3. **Probability Engine** — candidate features, attraction ranking,
   calibration, competing first-hit probabilities and ETA.

The UI and API preserve these statuses instead of presenting them as one
undifferentiated signal.

## 3. Liquidity Heatmap and reconciliation

REST snapshots plus WebSocket diffs are reconciled with sequence checks. Spot
and Futures books are separate. Futures `pu` mismatches and `U/u` gaps trigger
resynchronization; stale books are not silently used.

`orderbook_events_raw` stores timestamp, market, sequence, price, side,
quantity, notional, action and remaining quantity. The lifecycle actions are
`ADDED`, `RESTING`, `EXECUTED`, `CANCELLED`, `REPLENISHED` and `DEPLETED`.
Aggressive aggTrade evidence is required before a decrease is classified as
execution. Displayed, executed, cancelled, replenished and remaining liquidity
are distinct metrics. Adaptive time/price bins keep the heatmap bounded.

Spoof, iceberg and absorption fields are derived diagnostics: persistence,
execution/cancel/replenishment/depletion ratios, wall strength, spoof score,
iceberg score, absorption score, classification and confidence.

## 4. Estimated liquidation methodology

Every liquidation output is marked `ESTIMATED` or `DERIVED`; the product never
claims to know all Binance accounts' real liquidation prices. OI increases
create cohorts with entry zone, estimated directional split, leverage prior,
remaining inventory and aging decay. Observed `forceOrder` events remain a
separate REAL input.

Leverage buckets are 2x, 3x, 5x, 10x, 20x, 25x, 50x, 75x, 100x and 125x. The
linear-contract liquidation projection accepts maintenance-margin and fee
buffer inputs and keeps the result estimated until exact contract tier data is
available. Accessibility and cascade probability include distance, ATR,
friction, volatility, nearby clusters, path gaps and directional flow.

## 5. Candidate targets and path friction

Candidates combine estimated liquidation zones, observed order-book walls,
executed volume-profile POC/VAH/VAL, and extensible structure levels. Nearby
levels merge into `targetLow`, `targetCenter`, `targetHigh` while preserving
deduplicated confluence types.

Path friction scores walls, profile levels and opposing levels and returns a
0–100 value plus `OPEN PATH`, `LOW`, `MEDIUM`, `HIGH` or `BLOCKED / VERY HIGH`.
The target feature vector includes distance, ATR, liquidity/liquidation,
profile, flow, positioning, structure, volatility, session and availability
fields.

## 6. Score, historical calibration, first-hit and ETA

`TargetAttractivenessScore` is only a ranking score. It is not probability.
Historical replay builds candidates from data available before timestamp T;
future OHLC is used only for labels. Labels include 15m, 30m, 1h and 4h hits,
time-to-hit, first target, MFE/MAE, invalidation and cascade fields.

`historical/calibration/target_probability.json` contains the generated
chronological artifact:

- 640 replay snapshots and 3,089 target outcomes;
- train/validation/OOS chronological split of 60%/20%/20%;
- `no_lookahead: true`;
- status `CALIBRATED` with minimum calibration sample 30;
- score/probability kept as separate fields;
- monotonic post-processing guarantees `P15 <= P30 <= P1h <= P4h`;
- competing-risk style first-hit normalization;
- ETA median, P25 and P75 intervals.

Observed OOS metrics from the generated artifact:

| Horizon | Brier | Calibration error | OOS samples |
|---|---:|---:|---:|
| 15m | 0.1586 | 0.0047 | 598 |
| 30m | 0.1584 | 0.0338 | 598 |
| 1h | 0.1711 | 0.0008 | 598 |
| 4h | 0.2652 | 0.2482 | 598 |

The 4h result is weak and is exposed as a calibration limitation, not hidden
behind false precision. Confidence is separate from probability and is reduced
by sample size, data freshness, source completeness and regime uncertainty.

## 7. API and storage

Under the existing `/api/v1` prefix:

- `/probability-map/summary`
- `/probability-map/targets`
- `/probability-map/liquidity`
- `/probability-map/liquidations`
- `/probability-map/history`
- `/probability-map/data-health`

Additive tables are `orderbook_events_raw`, `probability_target_snapshots`,
`target_features`, `target_outcomes`, `liquidity_heatmap_buckets`,
`liquidation_cohorts`, `liquidation_map_buckets`, `probability_calibration` and
`historical_analogues`. Raw and derived data remain separate.

## 8. UI

Both Probability Map panels use the combined payload and support Spot/Futures
selection. The interface exposes separate REAL liquidity and ESTIMATED
liquidation legends, target zones, confluence, attraction score, P15/P30/P1h/P4h,
first-hit, ETA interval, confidence, WHY/AGAINST/MISSING, lifecycle rows,
liquidation rows and data health. It explicitly states that the output does
not authorize automatic execution.

## 9. Live verification

The deployed backend at `/opt/nce-trader-terminal` was updated with the engine
and calibration artifact, then the API and collector were controlled-restarted.
Live checks passed:

- health and Probability Map API responses: HTTP 200;
- Futures depth heartbeat: alive;
- order-book status: `REAL`;
- liquidation status: `ESTIMATED`;
- historical model status: `CALIBRATED`;
- bounded live target payload: 50 targets;
- calibrated target count: 50.

The deployment keeps the existing ownership split: the Capital Flow collector
owns Spot/Futures depth while the legacy Futures aggTrade collector remains the
Futures trade source when that environment flag is disabled.

## 10. Metric status contract

- **REAL:** Binance REST/WS depth, aggTrades, OI, funding, public ratios and
  observed forceOrder rows when present.
- **DERIVED:** lifecycle aggregates, profile, path friction, accessibility,
  flow features, target score, ETA and health.
- **ESTIMATED:** OI split, leverage prior, liquidation zones and cascade risk.
- **PROXY:** explicitly identified proxy sources only.
- **UNAVAILABLE/STALE:** missing or expired inputs; no fabricated numbers.

## 11. Tests

The complete repository suite passes: **72 passed**. Coverage includes:

- snapshot/diff reconciliation and Futures `pu` gaps;
- added/cancelled/executed/replenished/depleted lifecycle;
- spoof/absorption and adaptive binning;
- directional liquidation formulas and cohort decay;
- cluster accessibility and cascade risk;
- target generation, path friction and score/probability separation;
- monotonic horizons, first-hit, ETA, staleness and API schema;
- chronological historical replay and no-lookahead calibration.

Compilation and `git diff --check` also pass.

## 12. Known limitations and rollback

The liquidation map remains an estimate by design. Exact account-level
liquidation distribution, options/gamma data and a deeper multi-regime archive
are unavailable. The 4h calibration metric needs more data and recalibration.
The public GitHub Pages repository was not force-pushed; the backend was
verified in the deployed checkout and the frontend source is in `index.html`.

Rollback is additive: restore the prior versions of
`capital_flow/collector.py`, `capital_flow/http_api.py`,
`capital_flow/storage.py`, `capital_flow/probability_map.py` and `index.html`,
then leave the new tables/artifact unused. No existing raw table is dropped or
rewritten.

## Final verdict

**PROBABILITY_MAP_PRODUCTION_READY** for V1 decision support, with calibrated
probabilities clearly separated from attraction scores and with the stated
4h/estimated-data limitations. It is not an execution engine and does not
replace entry, trigger, RR or invalidation confirmation.
