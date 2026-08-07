# NCE Capital Flow Intelligence Engine V2 — forensic audit

Audit date: 2026-08-07 UTC

## A. Existing architecture

- Repository: `/root/NCE-Trading-sistem-inspect`, branch `main`, clean working tree at audit start, HEAD `9892ede7`.
- Frontend: static `index.html`, deployed by GitHub Pages. The page points to the public API `https://nce-api.204.168.191.154.sslip.io` (local fallback `127.0.0.1:8791`).
- Backend source is not present in the frontend repository. The latest available recovery snapshot is `/root/nce_rollback/20260729_144911_collector_hardening/opt_nce-trader-terminal`, a FastAPI app served historically on `127.0.0.1:8010` behind the public API.
- Historical recovery snapshots show `nce-trader-api.service` and `nce-1s-collector.service`; neither is active in this sandbox. No active systemd service, systemd-user service, timer, Docker container, or listening port is visible here.
- Existing frontend data files contain candle/1s archives (`bars_*.json`, `live_status.json`, `gaps_1s.json`).

## B. Current Money Flow path

`Binance Futures/Spot kline or local bars -> index.html loadLocalBars/loadFromBinance -> curData -> analyzeMoneyFlow(curData, tf) -> moneyFlowView/terminalView`.

The exact implementation is in `index.html` around `analyzeMoneyFlow`:

1. It reads visible candle fields `v`, `bv`, `sv`.
2. It uses the last 220 closed bars.
3. It computes P60 and P85 of candle volume and creates only `small`, `medium`, and `large` groups.
4. It sums `totalBuy`, `totalSell`, `direction`, and a buy-share `bias`.
5. `loadMoneyFlowLocal` labels the result `Binance candle volume / frontend calculation` and is used instead of a backend Money Flow endpoint.

This is not executed spot flow. It is a candle-volume-derived approximation and cannot distinguish spot/futures, executed/displayed liquidity, trader size, position opening/closing, or forced flow.

The existing 1s browser stream does consume Spot `@aggTrade` and maps `m=false` to buy (`buy = !d.m`), but it only creates local 1s bars. Money Flow does not consume those normalized trades and does not persist a backend raw tape.

## C. Reusable components found

- Existing browser Spot aggTrade WebSocket and 1s bar aggregation.
- Historical Binance-derived bar archives and a persistent 1s collector snapshot.
- Recovery backend has public Futures price, OI, funding, derivatives, liquidity, and data-health routes, but no Capital Flow Intelligence route.
- Existing service snapshots provide a rollback reference; no live service is modified by this change.

## D. Missing or unsafe components

- No backend Spot raw aggTrade store or Spot/Futures-separated CVD.
- No immutable raw schema for aggTrades, OI, funding, liquidations, top-trader ratios, orderbook events, or external context.
- No rolling 30-day trader-size thresholds; current P60/P85 candle thresholds are not valid participant-size buckets.
- No sequence-reconciled depth snapshot/diff collector.
- No evidence/probability position-state engine.
- No source metadata contract with `REAL/DERIVED/PROXY/UNAVAILABLE/STALE/UNRELIABLE`.
- No replay/backtest path using the same calculations as live.
- Coin Metrics, GraphSense, ETF issuer/SEC, and Binance Web3 Skills are not installed or connected in this workspace.

## E. Duplicate-data risk

- A new collector must not be enabled beside the historical `nce-1s-collector.service` without an ownership check. The new package is therefore opt-in and uses a separate database path by default.
- Browser Spot aggTrade, historical collector Spot/Futures aggTrades, and any future backend WebSocket can overlap. Raw rows use market/symbol/trade id uniqueness where possible; deployment must choose one owner for each stream.
- Existing candle files are preserved; no existing database or service is migrated in-place.

## F. Incremental migration and rollback

1. Additive package and new API namespace `/api/v1/capital-flow/*`.
2. Create a separate `capital_flow.sqlite3` only when the collector is explicitly launched; schema creation is additive with `IF NOT EXISTS`.
3. Deploy the backend router separately, verify data health, then switch only the Money Flow panel.
4. Rollback is a frontend revert to the prior static panel plus stopping the new opt-in collector; existing routes/files remain untouched.

## G. Phase plan

- Phase 1 implemented here: normalized Spot/Futures aggTrade engine, CVD, local timeframe aggregation, rolling size thresholds, orderbook metrics, OI/funding/ratio/liquidation inputs, position/whale/divergence/regime evidence, data health, raw schema, collector skeleton, API router, frontend contract, unit/replay tests.
- Phase 2–4 stay explicitly unavailable until their source adapters are verified. No proxy is presented as real data.
- A live deployment still requires copying the package into the actual backend working tree and enabling one collector owner; this sandbox has no active API process to restart or verify in-place.
