# NCE Capital Flow Intelligence Engine V2 — implementation report

Date: 2026-08-07 UTC

## A. Before

The Money Flow tab was frontend-only. `index.html` summed `v/bv/sv` candle
fields over the last 220 bars, used P60/P85 candle-volume cutoffs, and exposed
`totalBuy`, `totalSell`, `direction`, and `bias`. It was explicitly labeled
`Binance candle volume / frontend calculation`; it was not an executed Spot
tape and did not separate Spot from Futures.

The recovery audit found the historical FastAPI backend and collector snapshots,
but no active service/port/container in this sandbox. The exact forensic record
is in `CAPITAL_FLOW_AUDIT.md`.

## B. After

```text
Binance Spot aggTrade ─┐
                        ├─ normalize → immutable raw SQLite → local aggregation
Binance Futures aggTrade┘                       │
Binance depth snapshot + diff ──────────────────┤
Futures OI/funding/ratios/forceOrder ────────────┤
                                                ↓
Spot/Futures CVD + size buckets + impact + evidence probabilities
                                                ↓
Capital Flow Matrix + regime + data-health metadata
                                                ↓
versioned API → CAPITAL FLOW INTELLIGENCE panel
```

The engine keeps Spot executed flow, Futures executed flow, orderbook
liquidity, positioning, liquidations, exchange flow, smart money, and ETF
holdings as separate domains. Missing values remain `UNAVAILABLE` and are not
converted to neutral.

## C. Modified files

- `index.html`: removed candle-volume Money Flow fallback; added the new
  versioned API call, regime/matrix/data-health UI, executed-flow histograms,
  CVD, size buckets, whale behavior, derivatives, and explicit unavailable
  external-context cards. Navigation remains `Money Flow`; page title is
  `CAPITAL FLOW INTELLIGENCE`.

## D. New files

- `CAPITAL_FLOW_AUDIT.md`
- `capital_flow/engine.py`, `storage.py`, `collector.py`, `http_api.py`,
  `replay.py`, `live_verify.py`, `external_context.py`
- `capital_flow/README.md`, `EXTERNAL_CONTEXT.md`, `requirements.txt`,
  `__init__.py`
- `tests/test_capital_flow.py`, `tests/test_external_context.py`

## E. Services and deployment safety

No service was stopped, restarted, enabled, or duplicated. The new collector
is opt-in only:

```bash
python -m capital_flow.collector --db data/capital_flow.sqlite3 --symbol BTCUSDT
```

It uses a separate database by default. Deployment must first confirm that the
historical `nce-1s-collector.service` is not already the owner of the same
stream. Existing NCE databases and services are untouched. Rollback is to stop
the opt-in collector, remove only the new router include, and restore the old
frontend file.

## F. API routes

After including `create_router()` in the existing FastAPI app:

- `/api/v1/capital-flow/summary`
- `/api/v1/capital-flow/spot`
- `/api/v1/capital-flow/futures`
- `/api/v1/capital-flow/trader-size`
- `/api/v1/capital-flow/top-traders`
- `/api/v1/capital-flow/orderbook`
- `/api/v1/capital-flow/exchange`
- `/api/v1/capital-flow/institutional`
- `/api/v1/capital-flow/smart-money`
- `/api/v1/capital-flow/data-health`

The sandbox does not have FastAPI installed, so route import was not executed
here; the host recovery requirements already specify FastAPI/httpx and the
router source compiles independently.

## G. Database schemas

The separate additive SQLite schema creates (only if absent):

`spot_aggtrades_raw`, `futures_aggtrades_raw`, `orderbook_raw`, `oi_raw`,
`funding_raw`, `liquidations_raw`, `top_trader_accounts_raw`,
`top_trader_positions_raw`, `coinmetrics_raw`, `graphsense_transactions_raw`,
`etf_source_raw`, `sec_filings_raw`, and `smart_money_raw`.

Raw executions are immutable by primary key `(symbol, aggregate_trade_id)`;
derived timeframe rows are rebuilt in memory from raw events. No existing
schema is altered or overwritten.

## H. Official endpoints and source semantics

| Domain | Endpoint/stream | Status |
|---|---|---|
| Spot executions | `wss://stream.binance.com:9443/ws/btcusdt@aggTrade`; REST fallback `https://data-api.binance.vision/api/v3/aggTrades` | Implemented |
| Futures executions | `wss://fstream.binance.com/ws/btcusdt@aggTrade` | Implemented |
| Futures OI | `https://fapi.binance.com/fapi/v1/openInterest` | Implemented |
| Funding/mark | `https://fapi.binance.com/fapi/v1/premiumIndex`; historical `.../fapi/v1/fundingRate` | Implemented |
| Liquidations | `wss://fstream.binance.com/ws/btcusdt@forceOrder` | Implemented raw collector |
| Top trader accounts | `https://fapi.binance.com/futures/data/topLongShortAccountRatio` | Implemented raw collector |
| Top trader positions | `https://fapi.binance.com/futures/data/topLongShortPositionRatio` | Implemented raw collector |
| Global crowd | `https://fapi.binance.com/futures/data/globalLongShortAccountRatio` | Endpoint reserved; not presented until wired |
| Spot depth | `https://api.binance.com/api/v3/depth` + `btcusdt@depth@100ms` | Sequence guard implemented |
| Coin Metrics | `https://community-api.coinmetrics.io/v4/reference-data/asset-metrics`; `.../timeseries/asset-metrics` | Metadata-first adapter; not enabled |
| GraphSense | hosted/self-host adapter boundary | Explicitly deferred |
| SEC/issuer ETF | adapter boundary | Explicitly deferred |
| Binance Web3 Skills | sandbox-local `crypto-market-rank/scripts/cli.mjs` | Explicitly unavailable until local install |

## I. REAL metrics

Direct public Binance inputs are `REAL`: raw Spot/Futures aggTrades, OI,
funding/premium, forceOrder events, top-trader ratio payloads, and depth
events, subject to source-specific freshness and coverage.

## J. DERIVED metrics

Notional, aggressor side, buy/sell USD, delta, CVD, local timeframe series,
trade intensity, notional/second, dynamic size buckets, size participation,
price-impact context, orderbook depth/imbalance, divergence, evidence-score
position state, whale behavior, regime, and capital-flow matrix.

The size engine uses rolling historical Spot and Futures distributions with
P70/P90/P99/P99.9 boundaries. It never calls a trade “a whale identity”; UI
uses `Whale-sized` semantics.

## K. PROXY metrics

None of the Phase 1 executed-flow values are proxies. A future margin-pressure
state may be labeled `PROXY` because public platform-wide margin data is not
guaranteed. Coin Metrics is context only; it is not primary trade flow.

## L. UNAVAILABLE / deferred metrics

- GraphSense exchange inflow/outflow is unavailable until verified Binance
  labels/entities and transaction coverage are configured.
- ETF/institutional flow is unavailable until official issuer/SEC adapters are
  validated. Holdings delta will be named holdings change, not cash flow.
- Binance Web3 smart-money and address-PnL ranks are unavailable because the
  sandbox-local skill is not installed; BTC-native scope is not assumed.
- Full top-trader deltas across 5m/15m/30m/1h/4h/24h require storing and
  querying the ratio history; the raw collector stores the source period and
  timestamp, while the UI currently avoids inventing a delta.

## M. Data health

Per-source health includes status, last update, age, latency placeholder,
error count, coverage, confidence, and freshness. Thresholds are source
specific: execution/depth seconds, OI/funding minutes/hours, and external
filing/metric cadence.

## N. Tests

`11 passed`:

- buyer-maker mapping
- notional and market separation
- local aggregation/delta/CVD
- rolling percentile classification
- evidence-score probabilities
- orderbook sequence gap handling
- additive raw-store deduplication
- exchange transaction classification/coverage
- ETF holdings delta semantics
- Python compilation
- inline frontend JavaScript parse

## O. Live BTCUSDT sample

Read-only verification at `2026-08-07` returned `PASS` from the official
public endpoints. Sample timestamp was `1786070217313` ms.

- Spot aggTrade sample: `m=false` → `BUY`, `64363.08 × 0.00776 = 499.4575008`
  USD; `m=true` → `SELL`, `64363.07 × 0.00465 = 299.2882755` USD.
- Futures OI: `105423.915` BTC contracts at the sampled source timestamp.
- Futures last funding rate: `0.00002204`.
- Top Trader account ratio: long `0.5632`, short `0.4368`, ratio `1.2894`.

These are a verification sample, not a stored fake live value. The browser
panel only shows live numbers after the backend raw collector/API is deployed.

## P. Historical validation

The same pure functions are exposed through `capital_flow.replay` and accept
chronological raw-event prefixes, so live/replay/backtest use the same
normalization and aggregation code. No verified historical Binance aggTrade
archive is present in this workspace; existing `bars_*.json` files are candle
archives and are intentionally not treated as raw execution history. A
production walk-forward run therefore remains pending the official aggTrade
archive ingestion.

## Q. GraphSense resource status

Sandbox resource audit: approximately 439 GB free disk, 30 GiB RAM, 16 CPU,
load around `0.59`, and no active NCE service/container/port. GraphSense
self-hosting was not installed; Cassandra/full blockchain ingestion would be a
separate resource and rollback risk. Hosted/public access or a separate node
is the safe next decision.

## R. Coin Metrics, ETF, GraphSense, Smart Money

Adapters and explicit status contracts are present in `external_context.py`.
They remain outside Phase 1 regime calculation until source availability,
coverage, timestamp semantics, and cache/backoff behavior are validated in the
deployment environment.

## S. Remaining limitations

The engine reconstructs exchange-local executions, not global BTC capital
ownership. AggTrade size is order/event size, not trader identity. Futures
aggressive buy can open a long or close a short; OI/liquidation evidence only
raises probabilities and does not prove intent. Orderbook walls are displayed
liquidity and can be cancelled. Exchange inflow is potential supply, not a
confirmed sale. ETF holdings changes are not cash-flow ground truth. Capital
flow remains context for Trader Hova and does not open trades.
