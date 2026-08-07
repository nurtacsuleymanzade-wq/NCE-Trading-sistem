# NCE Probability Map — Final Implementation Report

Date: 2026-08-07  
Verdict: **PROBABILITY_MAP_NOT_READY**

The implementation establishes the Probability Map foundation, but production
readiness is not claimed yet because a target-level historical calibration
archive and live deployment verification are not available in this environment.

## 1. Architecture audit

Existing reusable components were `CapitalFlowEngine`, separate Spot/Futures
aggTrade/CVD, OI/funding/public positioning, `DepthReconciler`, raw SQLite
storage, historical replay primitives, Target ETA and the static dashboard.

Missing before this change were lifecycle heatmap events, OI-cohort liquidation
inventory, candidate target zones, target-level calibration, first-hit
competition, ETA intervals and a combined target UI. Full evidence is in
`PROBABILITY_MAP_AUDIT.md`.

## 2. Implementation

Added `capital_flow/probability_map.py` as a pure engine with three isolated
namespaces:

1. Liquidity Engine: observed order-book snapshots, diff lifecycle and heatmap.
2. Liquidation Engine: estimated OI-cohort inventory and liquidation zones.
3. Probability Engine: candidate features, attraction score, calibration,
   competing first-hit probability and ETA.

Existing Capital Flow routes remain additive and unchanged. New code adds the
Probability Map API, storage tables, Futures depth collection and UI wiring.

## 3. Liquidity Heatmap and reconciliation

`orderbook_raw` now carries `market` (`spot`/`futures`).
`orderbook_events_raw` stores timestamp, sequence, price, side, quantity,
notional, action and remaining quantity. Actions are separated as `ADDED`,
`RESTING`, `EXECUTED`, `CANCELLED`, `REPLENISHED` and `DEPLETED`.

A diff quantity decrease alone is never called `EXECUTED`; execution is written
from observed aggressive aggTrade evidence. `RESTING`/displayed notional,
executed notional, cancelled notional and replenished notional have distinct
fields and legends. Adaptive time/price bins are used for compact heatmaps.

REST snapshot + WebSocket diff reconciliation rejects `U/u` gaps. Futures
`pu` previous-update mismatches are also rejected and force a fresh snapshot;
stale books are not silently used.

Spoof/absorption fields include persistence, execution/cancel/replenishment/
depletion ratios, wall strength, spoof score, iceberg score, absorption score,
classification and confidence. High cancellation with little execution is
`SPOOF/PULLED`; execution with replenishment is `ABSORPTION/REAL_PASSIVE`.

## 4. Estimated liquidation methodology

Liquidation output is labelled `ESTIMATED` everywhere. Binance public data does
not disclose every account’s entry, leverage or liquidation price.

OI increases create cohorts with creation time, entry zone, estimated long/short
split, leverage prior, remaining inventory and age decay. Observed `forceOrder`
events remain a separate real input. Leverage buckets are 2x, 3x, 5x, 10x,
20x, 25x, 50x, 75x, 100x and 125x; the distribution is an explicitly estimated
prior, not account data.

The liquidation equation is a linear-contract projection with configurable
maintenance-margin and fee-buffer inputs. Exact tiered contract mechanics must
be supplied for production precision, so projected zones remain estimated.

Accessibility combines distance %, distance ATR, path friction, directional
support and volatility. Cascade probability uses nearby cluster density, path
gaps and volatility; a liquidation hit and cascade are not conflated.

## 5. Candidate targets and path

Candidates come from liquidation zones, observed order-book walls, executed
volume-profile POC/VAH/VAL and extensible structure levels. Nearby levels merge
into `targetLow`, `targetCenter`, `targetHigh` while retaining confluence types.

Path friction scores order-book walls, profile levels and opposing levels and
returns both a 0–100 score and `OPEN PATH` / `LOW` / `MEDIUM` / `HIGH` /
`BLOCKED / VERY HIGH` label.

The target feature vector contains distance, ATR distance, liquidation density,
cascade, path friction, flow, OI/funding, direction, type, session and status
fields. Missing values remain missing.

## 6. Score, calibration and ETA

`TargetAttractivenessScore` is a V1 ranking score. It is never copied into a
probability. Without target-level calibration, the API returns `MODEL_SCORE`,
null `hit15m`, `hit30m`, `hit1h`, `hit4h` and `firstHit`, plus a missing-data
reason.

With adequate historical calibration rows, observed hit rates are returned as
`CALIBRATED`. Horizon post-processing enforces `P15 <= P30 <= P1h <= P4h`.
First-hit values use competing-risk style normalization among candidate
targets. ETA returns median, P25, P75 and separate confidence.

## 7. APIs and storage

Added under `/api/v1`:

- `/probability-map/summary`
- `/probability-map/targets`
- `/probability-map/liquidity`
- `/probability-map/liquidations`
- `/probability-map/history`
- `/probability-map/data-health`

Additive tables are `orderbook_events_raw`, `probability_target_snapshots`,
`target_features`, `target_outcomes`, `liquidity_heatmap_buckets`,
`liquidation_cohorts`, `liquidation_map_buckets`, `probability_calibration`
and `historical_analogues`. Raw and derived artifacts stay separate.

## 8. UI

Both probability/liquidity panels now use the combined Probability Map payload.
The UI exposes Futures/Spot toggle, separate REAL liquidity and ESTIMATED
liquidation legends, target zones and confluence, score, P15/P30/P1h/P4h,
first-hit, ETA interval, confidence, why/against/missing, lifecycle rows,
liquidation rows and source health. It also states that the output does not
authorize automatic execution.

## 9. Backtest and calibration status

Chronological calibration primitives and storage are implemented, but the
existing archive does not contain a defensible target-level feature/outcome
dataset. Therefore no live BTCUSDT target probability or first-hit percentage
is displayed yet. The correct UI result is `UNAVAILABLE`, not a fabricated
percentage.

Required next run: collect lifecycle data; replay candidates using only data
available at T; label 15m/30m/1h/4h hits, first target, ETA, MFE/MAE and
cascade; write chronological train/validation/OOS calibration rows; then report
Brier score, calibration error, bucket hit rates and ETA interval coverage.

## 10. Metric status contract

- REAL: Binance REST/WS depth, aggTrades, OI, funding, public ratios and
  observed forceOrder rows when present.
- DERIVED: lifecycle aggregates, volume profile, path friction, accessibility,
  flow features, target score, ETA and data health.
- ESTIMATED: OI split, leverage prior, liquidation zones and cascade probability.
- PROXY: only sources that explicitly identify themselves as proxy.
- UNAVAILABLE: target probability without calibration, unavailable options or
  missing raw feeds.
- STALE: source rows past freshness thresholds.

## 11. Tests

Focused implementation and existing Capital Flow/phase suites pass: **66
tests**. New tests cover sequence reconciliation, lifecycle actions,
spoof/absorption, adaptive bins, directional liquidation formulas, cohort
decay, score/probability separation, monotonic horizons, first-hit competition,
path friction and API schema.

The complete repository suite has **5 unrelated pre-existing failures** in
`test_simple_event_system.py` for NAI BA/SA/BF/SF fixture emission and duration
expectations; the run was **66 passed, 5 failed**. They were not caused by the
Probability Map tests and require a separate baseline investigation before the
full repository can be called regression-green.

## 12. Limitations, rollback and verdict

Known limitations are the missing historical order-book lifecycle archive,
missing target calibration artifact, approximate maintenance-margin tiers,
unavailable options/gamma and no live deployment verification from this
restricted workspace. No production service was restarted or deployed.

Rollback is additive: restore the prior versions of
`capital_flow/collector.py`, `capital_flow/http_api.py`, `capital_flow/storage.py`
and `index.html`; leave the new tables unused. Existing raw tables are not
dropped or rewritten.

Final verdict: **PROBABILITY_MAP_NOT_READY**. The three critical conditions are
enforced in code—real lifecycle order-book data, explicit estimated liquidation
data, and score/probability separation—but calibration, live verification and
a green full regression suite remain before production readiness.
