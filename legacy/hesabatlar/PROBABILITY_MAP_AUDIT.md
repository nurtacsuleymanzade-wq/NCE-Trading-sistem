# NCE Probability Map — Forensic Audit

Audit date: 2026-08-07  
Repository: `nurtacsuleymanzade-wq/NCE-Trading-sistem` (`main`, `0fb5f609`)

## Scope and evidence

The audit inspected the local repository, the `capital_flow` package, the
static dashboard, the historical artifacts, and the production-staging API
adapter. The supplied GitHub Pages URL could not be fetched from this
restricted environment: the agent-reach Jina backend was unavailable and the
web reader rejected the URL. No claim below is based on an unobserved live
page.

## What exists

| Area | Evidence | Status |
|---|---|---|
| Spot/Futures executed flow | `capital_flow/engine.py`, `collector.py` | Reusable; separate aggTrade tapes, CVD, delta, impact and flow states |
| Binance depth | `collector.py:DepthReconciler` | Reusable sequence guard; REST snapshot + Spot `depth@100ms` diff stream |
| Raw order book | `capital_flow/storage.py:orderbook_raw` | Real snapshots are stored, but market identity and level lifecycle were missing |
| OI/funding/positioning | `collector.py`, `engine.py`, storage tables | Reusable public Binance inputs; freshness is exposed |
| Actual liquidations | `liquidations_raw`, `forceOrder` collector | Reusable as observed events; historically often empty and must not be used as a full liquidation map |
| Volume profile | Existing frontend calculation and profile endpoint in the production adapter | Partially reusable; no backend target feature contract |
| Target ETA | Existing frontend/backend target-ETA code | Context only; not connected to candidate target competition |
| Historical calibration | `capital_flow/historical.py`, `historical/calibration/*` | Reusable primitives; no target-level multi-horizon/first-hit calibration |
| Probability UI | Existing `Olasılık Haritası` panel | Existing calibrated directional panel; it is not a price-level Probability Map |
| Liquidity UI | Existing panel and frontend orderbook fallback | Existing snapshot/proxy view; no time × price lifecycle heatmap |

## What was placeholder, conflated, or missing

- `orderbook_metrics()` explicitly returned `wall_persistence`,
  `spoof_probability`, and `liquidity_pull` as `None`; it did not maintain
  ADDED/CANCELLED/EXECUTED/REPLENISHED state.
- Existing `compare_orderbooks()` only exposed aggregate added/removed USD and
  did not identify whether depletion was execution or cancellation.
- The existing public liquidation input is observed `forceOrder` flow, not the
  complete position inventory. A full liquidation map therefore did not exist.
- No OI cohorts, directional OI split, leverage buckets, maintenance-margin
  liquidation equations, accessibility, cascade or path-friction engine was
  present.
- No target candidate contract existed for zones, confluence, calibrated
  `P(hit)` horizons, first-hit, ETA interval, why/against/missing or confidence.
- The existing score/probability guard correctly keeps evidence scores apart
  from probability, but target-level calibration and competing risks were
  absent. The new engine preserves this guard and returns `UNAVAILABLE` for
  calibrated probabilities until target outcomes are available.

## Reusable modules

- `capital_flow.collector.DepthReconciler`
- `capital_flow.storage.CapitalFlowStore`
- `capital_flow.engine.CapitalFlowEngine`, CVD, order-book metrics and data
  health primitives
- `capital_flow.historical` chronological replay and calibration primitives
- Existing Capital Flow and Target ETA API contracts
- Existing frontend panel routing, status/error handling and static chart

## New implementation boundary

The implementation adds `capital_flow/probability_map.py` as a pure, testable
engine. It keeps three namespaces separate:

1. Liquidity Engine — observed order-book lifecycle and resting notional.
2. Liquidation Engine — estimated OI-cohort inventory and liquidation zones.
3. Probability Engine — candidate features, attraction score, calibration,
   competing first-hit probabilities and ETA.

Raw order-book events and derived target artifacts use additive SQLite tables;
existing tables and APIs remain intact. The frontend labels liquidation values
`ESTIMATED` and only renders calibrated probabilities when a calibration table
has adequate evidence.

## Known limitations before implementation

- The current historical archive contains extensive aggTrade/market data but
  does not prove a complete historical order-book lifecycle archive or target
  outcome archive. Production readiness therefore depends on collecting these
  new raw/derived tables and running a chronological replay.
- Binance public APIs do not disclose every trader's entry price, leverage or
  liquidation price. Liquidation zones are explicitly `ESTIMATED`/`DERIVED`.
- Options/gamma, validated institutional flow and BTC-native smart-money data
  remain unavailable unless their existing adapters provide real rows.
- A static GitHub Pages frontend cannot itself own a WebSocket or durable
  SQLite store; the read-only API/collector remains the source of truth.

## Audit verdict

`PROBABILITY_MAP_NOT_READY` before this change. The existing system has useful
building blocks, but the three critical acceptance conditions were not yet
met as a single end-to-end feature.
