# NCE Capital Flow Intelligence — Phase-1 Forensic / UI Consistency Audit

Audit timestamp: 2026-08-07 UTC  
Audited deployment: `/opt/nce-trader-terminal`  
Audited database: `/var/lib/nce-trading/capital_flow.sqlite3`  
Initial test baseline: **73 passed** (pytest; cache-write warnings only)

## Evidence and production wiring

The collector is active as `nce-capital-flow.service` and owns the lock
`/var/lib/nce-trading/capital_flow_collector.lock`. It runs with
`NCE_CAPITAL_FLOW_DB=/var/lib/nce-trading/capital_flow.sqlite3`, while the
manually launched API process has no equivalent environment. `create_router()`
therefore defaults to `data/capital_flow.sqlite3`. The live API returned HTTP
200 but an empty `capital-flow/summary`; the real database contained spot and
futures aggTrades, orderbook, OI, funding, and top-trader rows. This is a
critical wiring defect, not a neutral market state.

The production database at audit time contained approximately 11.5k spot
trades, 18.8k futures trades, 6.8k orderbook snapshots, 47 OI rows, 47 funding
rows, 20 account-ratio rows, and 21 position-ratio rows. The liquidation table
was empty. `nce-orderbook-collector.service` was inactive, but the Capital Flow
collector's depth stream was live and persisted snapshots.

No git metadata exists under `/opt/nce-trader-terminal`; the deployment commit
is therefore recorded as `NOT_AVAILABLE — deployed directory is not a git repository`.

## Audit matrix

| State / metric | Inputs and source | Current formula / window | Finding | Required semantic contract |
|---|---|---|---|---|
| Spot/Futures flow | Binance official aggTrade rows | buy notional minus sell notional | Primary executed evidence is correctly separate, but API wiring hid it | `m=false` is taker buy, `m=true` taker sell; spot/futures never merge |
| CVD total | Local aggTrade aggregation | cumulative delta over the loaded 30-day query result | Reset/reference was not exposed; value was a rolling query-window total, not UTC-day CVD | Expose `cvd_total`, `cvd_reset`, and `cvd_change` explicitly |
| CVD slope | None as a named field | Divergence used the latest bucket `delta_usd` and called it a slope | The label was ambiguous and made `SPOT_UP_FUTURES_UP` appear contradictory to a negative cumulative CVD | Use signed change over explicit 1m/5m completed buckets; keep total and slope separate |
| Retail | No backend configuration or aggregate | Regime could emit `WHALE_ACCUMULATION_RETAIL_SELLING` solely from whale behavior | Retail was not defined and was not required for the label | One backend `RETAIL_BUCKETS` config, currently `SMALL + MEDIUM`; show component and combined nets |
| Position state | price change, latest ΔOI, latest futures bucket delta, liquidations | evidence points passed through a softmax and returned as `probabilities` | Softmax is an internal score normalization, not calibrated probability; missing liquidation became numeric zero | Return score, confidence, calibrated probability only when historically calibrated, why/against/missing |
| Whale behavior | whale/mega flow, spot CVD, price | positive whale flow + CVD; price/impact only partially used | Accumulation/distribution could be asserted without price response, depth, volatility, absorption, or replenishment evidence | Price-impact metrics and explicit evidence lists; insufficient evidence is UNKNOWN |
| Price impact | Raw trade price and notional exist | Not calculated per size bucket | Large buying cannot be distinguished from absorbed buying | Per bucket buy/sell impact, z-score/percentile where sample-valid, and efficiency |
| Orderbook | Binance depth stream and snapshots | 5/10/25 bps depth and 10 bps imbalance | Displayed liquidity is correctly separate, but add/remove was not persisted into state and no absorption/replenishment semantics existed | Label intent/displayed liquidity; never count it as executed flow |
| Funding | Binance premiumIndex | raw funding only | No percentile or crowding | State, percentile, crowding; unavailable history must remain unavailable |
| OI | Binance open interest REST | value and latest difference | No velocity or acceleration | value, ΔOI, velocity, acceleration, explicit caution on exact position inference |
| Top traders | Binance account/position ratio REST | latest row across periods | Account and position fields were partly separated, but period deltas and full series were absent | Separate accounts vs positions; preserve 5m–24h observations and deltas |
| Global L/S | Not collected | No state | Missing source was not represented in matrix/state vector | Explicit UNKNOWN/UNAVAILABLE with reason |
| Liquidations | Existing forceOrder task | table empty at audit time | Existing stream was audited; no duplicate socket is allowed. Current state is UNAVAILABLE, not quiet | Reason, count, notional, intensity; LONG_FLUSH/SHORT_FLUSH/MIXED/QUIET only with data |
| Regime | latest spot/futures bucket, OI, whale behavior | `WHALE...` could be selected without retail; broad accumulation from spot/futures signs | A label could contradict its own evidence | Regime must be generated from state vector and explainable evidence |
| Matrix | six rows | direction defaulted SELL when value was missing; confidence/strength mostly null | Missing values could look like SELL/zero and matrix was incomplete | Required columns with `UNKNOWN`, status, timeframe, freshness, interpretation |
| Metadata | `_meta()` for some metrics | source/method/window/freshness mostly present | API-level external and derived values lacked complete contract | Every important metric carries source, method, timeframe/window, timestamp, age, coverage, confidence, status |
| UI | `/root/NCE-Trading-sistem-inspect/index.html` | existing technical panel plus partial horizon overlay | It expected `horizons`/`external_context` not returned by backend and displayed raw machine enums/softmax probabilities | Meaning first, technical detail expandable, readable enums and humanized numbers |

## Specific contradiction investigations

### `WHALE_ACCUMULATION_RETAIL_SELLING`

The current regime branch checks `whale_behavior == ACCUMULATION`; it does not
compute retail at all. Therefore the suffix is not evidence-backed. The
implementation must make `RETAIL_BUCKETS` a single backend configuration and
only emit the combined regime when the combined small/medium net is selling.

### `SPOT_UP_FUTURES_UP` with negative futures CVD

This is not inherently contradictory: a cumulative CVD may be negative while
its current slope is positive. In the audited implementation, however, the
classification uses the latest bucket delta and the methodology string says
“CVD/delta slope” without exposing either definition. The fix exposes total,
change, and 1m/5m slopes and states which field drives classification.

### `NEW_LONGS`

The audited evidence was price up, ΔOI up, and the latest futures bucket delta
up. That supports a `NEW_LONGS` hypothesis but does not prove it. The prior
softmax output (`58.26`) was not a probability. The corrected contract labels it
`state_score`, lowers confidence for missing liquidation/other confirmation,
and includes against/missing evidence.

### `ACCUMULATION`

The prior function used whale/mega buy and sell, spot CVD, and price, but did
not have a validated price-impact or absorption input in the live snapshot.
Consequently large buying alone must not be called accumulation. The corrected
engine emits `ACCUMULATION` only with a positive whale net plus price-response
support or defensible absorption evidence; otherwise it uses buying,
distribution, absorption, or UNKNOWN.

## Status vocabulary

- `REAL`: source payload is directly received and validated.
- `DERIVED`: deterministic calculation from validated source data.
- `PROXY`: explicitly approximate and never silently used as primary evidence.
- `UNKNOWN`: data exists but classification is not reliable.
- `UNAVAILABLE`: required source/data is missing or inaccessible.
- `NEUTRAL`: data exists and genuinely indicates no directional edge.

`strength`, `confidence`, `state_score`, and `calibrated_probability` are
different fields. A score is never rendered as a probability.

## Phase-1 gate at audit start

Phase 1 was **PARTIAL**, despite the collector being active, because the API
read the wrong database and several semantic contracts were incomplete. The
implementation can move to `COMPLETE` only after the corrected API, tests, and
live comparison pass. External phases remain independent and cannot block the
Phase-1 collector.
