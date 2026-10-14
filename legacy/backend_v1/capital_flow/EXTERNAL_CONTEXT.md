# External context status

The adapters are additive and are not connected to the Phase 1 entry context
by default.

## Coin Metrics Community

- Base: `https://community-api.coinmetrics.io/v4`
- Metadata: `GET /reference-data/asset-metrics?assets=btc`
- Data: `GET /timeseries/asset-metrics?assets=btc&metrics=...&frequency=...`
- The adapter discovers/cache-checks metadata first, uses a cache, and keeps
  Coin Metrics as low/medium-priority network context rather than executed
  trade flow. It does not assume a metric is available.

## Binance Web3 Skills

`BinanceSkillRunner` executes only a sandbox-local
`crypto-market-rank/scripts/cli.mjs`. It does not run `npx skills add`, does
not install globally, does not request keys, and returns `UNAVAILABLE` until a
local install is explicitly validated. Its output is chain-specific smart
money context, not BTC Spot, Futures, or Top Trader data.

## ETF / institutional

`HoldingsPoint` and `holdings_flow` distinguish holdings ground truth from
derived holdings change. A holdings delta is never named net ETF cash flow.
No issuer/SEC scraper is enabled until a validated official source adapter is
configured.

## GraphSense / exchange flow

`aggregate_exchange_flow` excludes internal and uncertain transfers and
reports `coverage_pct`. It requires verified labels supplied by a GraphSense
hosted/self-host or another documented label source; it does not infer a
Binance address from a transaction alone.
