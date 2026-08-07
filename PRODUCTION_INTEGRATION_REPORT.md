# PRODUCTION INTEGRATION REPORT

## 1. Production backend path

`/opt/nce-trader-terminal`

Production is not a Git checkout. The integration branch/commit was created in the inspect repository instead: `main`, commit `e6853cfc`.

## 2. Backend service name

`nce-trader-terminal.service` (systemd user service, root user)

## 3. Backend port

`127.0.0.1:8010`, published through Nginx HTTPS at `nce-api.78.46.134.148.sslip.io`.

## 4. Python/venv path

`/opt/nce-trader-terminal/.venv/bin/python`

## 5. Capital Flow collector service

`nce-capital-flow.service`, enabled and active.

Current owner at final verification: PID `938069`.

The existing `nce-1s-collector.service` remains the sole Futures aggTrade socket owner. It now publishes raw Futures executions into the additive Capital Flow SQLite store. Capital Flow itself owns Spot aggTrade, Spot depth, forceOrder, OI, funding, and top-trader polling; its Futures socket is intentionally disabled to prevent duplication.

## 6. Duplicate lock mechanism

Application-level `fcntl.flock`:

`/var/lib/nce-trading/capital_flow_collector.lock`

The duplicate test returned exit code `2` and `ANOTHER_COLLECTOR_ALREADY_ACTIVE`. systemd provides the single service owner and `Restart=always`.

## 7. Router integration file

`/opt/nce-trader-terminal/app/main.py`

The Capital Flow router is included with `prefix="/api/v1"`; its own `/capital-flow/...` paths are not double-prefixed.

## 8. Added endpoints

All returned HTTP 200 locally and publicly:

`/api/v1/capital-flow/summary`, `/spot`, `/futures`, `/trader-size`, `/top-traders`, `/orderbook`, `/exchange`, `/institutional`, `/smart-money`, `/data-health`.

Every endpoint exposes the common source/timestamp/freshness/confidence/status contract. Exchange, institutional, and smart-money endpoints remain explicitly `UNAVAILABLE` until their sources are verified.

## 9. Live endpoint responses

Final public summary: HTTP 200, `PASS`, source `Capital Flow Intelligence Engine`, `FRESH` collector.

Final public data-health: HTTP 200, `PASS`, `FRESH` collector.

Live raw counts at final check: Spot `3090`, Futures `7758`, orderbook `5080`, OI `18`, funding `18`, top-trader accounts `11`, positions `12`; liquidations had no events and remained `UNAVAILABLE`.

## 10. Binance live verification

Validated official payload semantics:

- Spot `m=false` → aggressive/taker BUY; `m=true` → aggressive/taker SELL.
- Futures uses the same maker-side semantics.
- Notional is `price * quantity`; CVD increment is `buy_notional - sell_notional`.
- OI, funding, account ratio and position ratio were fetched from official public Futures endpoints.
- Account and position ratios are stored and displayed separately; position payload field names are normalized without calling them institutional data.

## 11. Existing NCE regression tests

Capital Flow tests: `12 passed`.

Existing production suite: `67 passed` when excluding the live-price-dependent `test_target_eta.py`. One pre-existing target ETA assertion fails because the current live price produces a negative distance value; the failure is unrelated to Capital Flow imports/routes. Existing HTTP regression probes for market klines, order-flow, liquidity, profile, smart-money, simulation, data-health, orderbook health, and derivatives all returned HTTP 200.

## 12. Public frontend status

GitHub Pages deployment is live at the existing NCE site. Headless-browser verification confirmed:

- `CAPITAL FLOW INTELLIGENCE` renders.
- Capital Flow summary request returns HTTP 200.
- No page errors occurred.
- The old candle-volume Money Flow fallback is absent.
- The only observed 422 was the pre-existing `interval=1M` market-klines request, not a Capital Flow endpoint.

## 13. CPU/RAM usage

At final observation:

- Capital Flow collector: approximately `28–33 MB` RSS, peak approximately `34 MB`.
- Backend: approximately `63 MB` RSS, peak approximately `127 MB`.
- Host: approximately `30 GiB` RAM, `20 GiB` available.
- Collector heartbeat: fresh, Spot/depth/liquidation alive, reconnects `0`, errors `0`.

Depth raw persistence was reduced to one sample per second and top-100 levels after the initial forensic measurement found unbounded full-depth writes.

## 14. Disk growth estimate

The additive Capital Flow SQLite file was approximately `298 MB` at final check. The post-downsampling short observation measured roughly `0.5 MB/min`, approximately `0.7 GB/day` or `21 GB/30 days`. This is an estimate, not a long-term guarantee. Existing raw data was not deleted. A future Parquet archival/retention job should be added before the database approaches the available-disk budget.

## 15. External adapters status

- GraphSense/exchange-labelled Bitcoin flow: `UNAVAILABLE`.
- Coin Metrics Community context: adapter/documentation present, not enabled as a live signal source.
- Binance Web3 Smart Money: `UNAVAILABLE`; no BTC-chain scope was inferred.
- SEC/issuer ETF holdings and institutional flow: `UNAVAILABLE`.
- Liquidation flow: collector live, no event observed in the verification window; numeric fallback was not fabricated.

## 16. Known limitations

The existing Futures collector is a deliberate upstream owner rather than a second Capital Flow Futures socket. Its raw sink is additive and restart-safe, but the owner migration should eventually be consolidated if the legacy collector is retired. Raw retention/Parquet archival remains a follow-up. The existing `nce-orderbook-collector.service` was a broken enabled unit pointing to a missing script and was stopped; Capital Flow now owns Spot depth.

## 17. Rollback command

Backup directory:

`/root/nce-production-backups/20260807T024940Z`

High-level rollback:

```bash
systemctl stop nce-capital-flow.service
systemctl disable nce-capital-flow.service
cp /root/nce-production-backups/20260807T024940Z/files/main.py /opt/nce-trader-terminal/app/main.py
cp /root/nce-production-backups/20260807T024940Z/files/nce-trader-terminal.user.service /root/.config/systemd/user/nce-trader-terminal.service
cp /root/nce-production-backups/20260807T024940Z/files/nce-1s-collector.py /usr/local/bin/nce-1s-collector.py
systemctl daemon-reload
systemctl --user daemon-reload
systemctl --user restart nce-trader-terminal.service
systemctl restart nce-1s-collector.service
```

The Capital Flow SQLite database is additive and can be retained for forensic rollback; it is not required by the existing NCE API after router rollback.

## 18. Final verdict

**PRODUCTION_READY**
