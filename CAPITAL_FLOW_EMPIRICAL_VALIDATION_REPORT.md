# NCE Capital Flow Intelligence — Empirical Validation Report

Date: 2026-08-07 UTC
Symbol: BTCUSDT

## Executive result

**FINAL VERDICT: `CAPITAL_FLOW_NOT_EMPIRICALLY_VALIDATED`**

The replay pipeline is operational and uses the production `CapitalFlowEngine`, but the evidence does not meet the empirical-validation gate. It reconstructed **314** event snapshots across **7** regimes; the chronological OOS set has **61** resolved 15-minute labels. This is insufficient to claim a stable edge or probability calibration.

The report therefore separates `DATA_CONFIDENCE` from `EMPIRICAL_CONFIDENCE`, treats score as a non-probabilistic model output, and keeps GraphSense deferred.

## 1. Previous V3 audit

The four requested V3 reports were read and checked against source, tests, production SQLite evidence, and replay output. The main prior overstatement was Phase 5: look-ahead-safe primitives existed, but there was no production-grade reconstructed historical event set, OOS result, or calibrated probability table. Phase 3 was also explicitly partial: IBIT was real while the remaining ETF adapters were unavailable.

See [CAPITAL_FLOW_ACCEPTANCE_GAP_MATRIX.md](CAPITAL_FLOW_ACCEPTANCE_GAP_MATRIX.md).

## 2. Missing acceptance criteria

The blockers are: OOS resolved sample below the minimum 100-event gate; only seven existing regimes observed; sparse score bins; no stable temporal robustness result; most issuer daily holdings unavailable; liquidations/top-trader history are not complete over the replay window; and production live/API verification could not be performed from this sandbox socket context.

## 3. ETF coverage by fund

Holdings are normalized as BTC holdings, shares outstanding, AUM, and NAV. Unavailable fields are null. Holdings deltas are not called cash flow.

| Fund | Issuer | CIK | Official source | Daily holdings | BTC holdings | Shares | AUM | NAV | Adapter | Coverage | Confidence |
|---|---|---|---|---|---:|---:|---:|---:|---|---:|---:|
| IBIT | BlackRock iShares | 1980994 | https://www.ishares.com/us/products/333011/ishares-bitcoin-trust-etf | REAL | 744548 | 1.30912e+09 | UNAVAILABLE | UNAVAILABLE | REAL | 0.5 | 98 |
| FBTC | Fidelity | 1852317 | https://www.fidelity.com/etfs/fbtc | UNAVAILABLE | UNAVAILABLE | UNAVAILABLE | UNAVAILABLE | UNAVAILABLE | UNAVAILABLE | 0 | UNAVAILABLE |
| GBTC | Grayscale | 1588489 | https://www.grayscale.com/crypto-products/grayscale-bitcoin-trust-etf | UNAVAILABLE | UNAVAILABLE | UNAVAILABLE | UNAVAILABLE | UNAVAILABLE | UNAVAILABLE | 0 | UNAVAILABLE |
| ARKB | ARK 21Shares | 1869699 | https://www.21shares.com/en-us/products/arkb | UNAVAILABLE | UNAVAILABLE | UNAVAILABLE | UNAVAILABLE | UNAVAILABLE | UNAVAILABLE | 0 | UNAVAILABLE |
| BITB | Bitwise | 1763415 | https://bitwiseinvestments.com/crypto-funds/bitb | UNAVAILABLE | UNAVAILABLE | UNAVAILABLE | UNAVAILABLE | UNAVAILABLE | UNAVAILABLE | 0 | UNAVAILABLE |
| BTCO | Invesco Galaxy | 1855781 | https://www.invesco.com/us/en/financial-products/etfs/product-detail?audienceType=Investor&productId=ETF-BTCO | UNAVAILABLE | UNAVAILABLE | UNAVAILABLE | UNAVAILABLE | UNAVAILABLE | UNAVAILABLE | 0 | UNAVAILABLE |
| HODL | VanEck | 1838028 | https://www.vaneck.com/us/en/investments/bitcoin-etf-hodl/ | UNAVAILABLE | UNAVAILABLE | UNAVAILABLE | UNAVAILABLE | UNAVAILABLE | UNAVAILABLE | 0 | UNAVAILABLE |
| BRRR | CoinShares | 1841175 | https://coinshares.com/us/etf/brrr/ | UNAVAILABLE | UNAVAILABLE | UNAVAILABLE | UNAVAILABLE | UNAVAILABLE | UNAVAILABLE | 0 | UNAVAILABLE |
| EZBC | Franklin Templeton | 1992870 | https://www.franklintempleton.com/investments/options/exchange-traded-funds/products/43964/SINGLCLASS/franklin-bitcoin-etf/EZBC | UNAVAILABLE | UNAVAILABLE | UNAVAILABLE | UNAVAILABLE | UNAVAILABLE | UNAVAILABLE | 0 | UNAVAILABLE |
| BTCW | WisdomTree | 1850391 | https://www.wisdomtree.com/investments/etfs/crypto/btcw | UNAVAILABLE | UNAVAILABLE | UNAVAILABLE | UNAVAILABLE | UNAVAILABLE | UNAVAILABLE | 0 | UNAVAILABLE |
| BTC | Grayscale | 2015034 | https://www.grayscale.com/crypto-products/grayscale-bitcoin-mini-trust-etf | UNAVAILABLE | UNAVAILABLE | UNAVAILABLE | UNAVAILABLE | UNAVAILABLE | UNAVAILABLE | 0 | UNAVAILABLE |

IBIT is the only validated issuer daily holdings point in the available production snapshot (`744,547.9297 BTC`, as-of 2026-08-05). FBTC, GBTC, ARKB, BITB, BTCO, HODL, BRRR, EZBC, BTCW, and BTC remain `UNAVAILABLE`; no zero was substituted.

## 4. Historical data inventory

The official manifest contains 210 planned Binance datasets, of which 204 are downloaded. Compressed raw archive storage is 670,945,568 bytes. The local production SQLite snapshot is approximately 342 MB and contains real spot/futures executions, OI, funding, orderbook, positioning, ETF source, and external-context rows.

| Source | Dataset | Date/resolution | Raw/derived | Completeness | Reliability | Backtest |
|---|---|---|---|---|---|---|
| Binance Data Vision | aggTrades | 2026-07-08 → 2026-07-08 | RAW | DOWNLOADED | HIGH | True |
| Binance Data Vision | index_price_1m | 2026-07-08 → 2026-07-08 | RAW | DOWNLOADED | HIGH | True |
| Binance Data Vision | klines_1m | 2026-07-08 → 2026-07-08 | RAW | DOWNLOADED | HIGH | True |
| Binance Data Vision | mark_price_1m | 2026-07-08 → 2026-07-08 | RAW | DOWNLOADED | HIGH | True |
| Binance Data Vision | metrics | 2026-07-08 → 2026-07-08 | RAW | DOWNLOADED | HIGH | True |
| Binance Data Vision | premium_index_1m | 2026-07-08 → 2026-07-08 | RAW | DOWNLOADED | HIGH | True |
| Binance Data Vision | aggTrades | 2026-07-08 → 2026-07-08 | RAW | DOWNLOADED | HIGH | True |
| local production SQLite snapshot | spot_aggtrades_raw | 2026-08-07T03:01:26.214000+00:00 → 2026-08-07T05:08:42.515000+00:00 | RAW | PARTIAL | HIGH | True |
| local production SQLite snapshot | futures_aggtrades_raw | 2026-08-07T02:55:38.944000+00:00 → 2026-08-07T05:08:42.947000+00:00 | RAW | PARTIAL | HIGH | True |
| local production SQLite snapshot | oi_raw | 2026-08-07T03:01:21.412000+00:00 → 2026-08-07T05:08:03.387000+00:00 | RAW | PARTIAL | HIGH | True |
| local production SQLite snapshot | funding_raw | 2026-08-07T03:01:25+00:00 → 2026-08-07T05:08:07.001000+00:00 | RAW | PARTIAL | HIGH | True |
| local production SQLite snapshot | orderbook_raw | 2026-08-07T03:01:26.076000+00:00 → 2026-08-07T05:08:43.741000+00:00 | RAW | PARTIAL | HIGH | True |
| local production SQLite snapshot | liquidations_raw | None → None | RAW | EMPTY | UNAVAILABLE | False |
| local production SQLite snapshot | global_ls_raw | 2026-08-07T00:00:00+00:00 → 2026-08-07T05:05:00+00:00 | RAW | PARTIAL | HIGH | True |
| local production SQLite snapshot | top_trader_accounts_raw | 2026-08-07T00:00:00+00:00 → 2026-08-07T05:05:00+00:00 | RAW | PARTIAL | HIGH | True |
| local production SQLite snapshot | top_trader_positions_raw | 2026-08-07T00:00:00+00:00 → 2026-08-07T05:05:00+00:00 | RAW | PARTIAL | HIGH | True |

Officially acquired: Spot/Futures aggTrades, Futures 1m klines, mark-price 1m, index-price 1m, premium-index 1m, and Futures metrics for the available 2026-07-08 through 2026-08-05 window. 2026-08-06 Futures archives were not published at acquisition time and are recorded as a gap; the Spot-only file is not used as a two-sided replay day.

## 5. Storage, manifest, and immutability

Raw ZIP files are under `historical/raw/binance/`, normalized event output under `historical/normalized/`, feature summaries under `historical/features/`, event snapshots under `historical/events/`, forward labels under `historical/labels/`, and walk-forward/calibration output under `historical/calibration/`. The manifest records source, symbol, market, date, size, ETag, Last-Modified, SHA-256, and raw immutability. Raw ZIPs are read-only.

## 6. Replay architecture and LIVE/REPLAY parity

Replay imports the same `CapitalFlowEngine`, `AggTrade` normalization, trader-size thresholds, CVD, bucket flow, price impact, position, whale, retail, divergence, and regime code used by live. Only the source changes: live/current rows versus chronological historical event-time input. A stable-field parity test passes; volatile wall-clock metadata is intentionally excluded from numerical parity.

## 7. Look-ahead prevention

At event time T, feature rows are limited to timestamp ≤ T. Percentile history is strictly prior-only with a deterministic bounded history and hourly refresh. ETF and SEC records require publication timestamps before visibility. Forward prices are read only by the label writer. No forward return, MFE, MAE, squeeze, target, or invalidation field is used as a feature.

## 8. Reconstructed events and regime samples

Total reconstructed events: **314**. Existing engine regimes observed:

| Regime | n | Minimum sample status |
|---|---:|---|
| BROAD_ACCUMULATION | 51 | SUFFICIENT |
| BROAD_DISTRIBUTION | 73 | SUFFICIENT |
| FORCED_DELEVERAGING | 106 | SUFFICIENT |
| MIXED_FLOW | 24 | SUFFICIENT |
| SPOT_ACCUMULATION_AGAINST_DERIVATIVE_SHORTS | 23 | SUFFICIENT |
| SPOT_DISTRIBUTION_AGAINST_LEVERAGED_LONGS | 28 | SUFFICIENT |
| WHALE_ACCUMULATION_RETAIL_SELLING | 9 | INSUFFICIENT_SAMPLE |

No regime listed in the master prompt was fabricated. Regimes absent from the engine output were not manufactured to fill the table.

## 9. Forward-return statistics, MFE/MAE

The full horizon table is saved in `historical/features/regime_statistics.json`. The compact 15-minute conditional view below is shown only as descriptive evidence; it is not a trading claim.

| Context | n | 15m positive directional return | Median MFE | Median MAE |
|---|---:|---:|---:|---:|
| BASE | 314 | 47.92% | 0.00% | 0.00% |
| FUTURES_CVD_DOWN | 158 | 52.45% | 0.00% | 0.00% |
| HIGH_WHALE_BUY_EFFICIENCY | 113 | 51.43% | 0.00% | 0.00% |
| OI_RISING | 288 | 47.37% | 0.00% | 0.00% |
| ORDERBOOK_BID_ABSORPTION | 0 | UNAVAILABLE | UNAVAILABLE | UNAVAILABLE |

`ORDERBOOK_BID_ABSORPTION` has n=0 because historical orderbook absorption is unavailable; it is not imputed.

## 10. Conditional edge analysis

Four predeclared cuts were evaluated: OI rising, futures CVD down, high whale buy efficiency, and orderbook bid absorption. No combinatorial search or post-hoc threshold sweep was used. Multiple-testing risk is stated in the artifact and small cells remain `INSUFFICIENT_SAMPLE`.

## 11. Walk-forward methodology and validation

Chronological split: 60% TRAIN, 20% VALIDATION, 20% OUT_OF_SAMPLE. Threshold/calibration mapping is learned on TRAIN only; validation is not used to rewrite OOS; OOS is never used for threshold selection; random shuffle is disabled.

| Split | Events | Resolved 15m labels | Continuation rate | Median directional return |
|---|---:|---:|---:|---:|
| OUT_OF_SAMPLE | 63 | 61 | 50.82% | 6.98088e-05 |
| TRAIN | 188 | 188 | 39.89% | -0.000180887 |
| VALIDATION | 63 | 63 | 50.79% | 8.63579e-05 |

OOS continuation is approximately 50.8% with 61 resolved labels. That is near coin-flip evidence with wide uncertainty and is below the minimum gate for empirical validation.

## 12. Calibration and score/probability separation

The engine score remains a score, not a probability. A TRAIN-only score-bin mapping is emitted separately. The OOS reliability table includes sample counts and 95% Wilson intervals. Sparse bins have null calibrated values. Brier score is only evaluated for the TRAIN-derived mapped probability, not by pretending the raw score is probabilistic.

OOS mapped Brier score: `0.262619`.

## 13. DATA_CONFIDENCE

`{"coverage": 0.9680365296803652, "cross_source_agreement": "NOT_ASSESSED", "event_count": 314, "freshness": "HISTORICAL_AS_OF_MANIFEST", "level": "MEDIUM", "missing_inputs": 7, "source_quality": "HIGH"}`

This reflects source quality, acquired coverage, missing inputs, event count, freshness semantics, and the fact that cross-source agreement was not assessed across all external datasets.

## 14. EMPIRICAL_CONFIDENCE

`{"calibration_quality": "REPORTED_WITH_CI", "level": "MEDIUM", "methodology": "empirical confidence is separate from data confidence; it is not a probability", "oos_sample_size": 61, "sample_size": 314, "stability": "CHECK_REQUIRED"}`

This is separate from data confidence and is not a probability. With 61 resolved OOS labels, stability is not established.

## 15. Historical analog API

The source implementation exposes `/api/v1/capital-flow/historical-context?regime=...`. Local route smoke returned `DERIVED` with a layered A–D fallback. The currently running production API returned HTTP 200 for health and summary, but its summary did not yet contain `historical_context`; deployment of this additive route is therefore still pending.

## 16. UI historical context

The frontend source adds an additive `HISTORICAL CONTEXT` card. It displays only API-derived values; missing artifacts render `UNAVAILABLE`. It does not describe the engine score as a probability and does not authorize execution. The live public frontend deployment was not changed in this run.

## 17. Tests and regression

Scoped Capital Flow + empirical tests: **45 passed**. This includes live/replay parity, no-future publication checks, label-only forward returns, calibration sample counts, walk-forward no-overlap, missing-source non-zero behavior, ETF normalization, manifest, and raw immutability. A full repository run was 53 passed / 6 unrelated legacy `test_simple_event_system.py` failures; those pre-existing failures are not presented as Capital Flow passes.

The running local and public production health/Capital Flow summary endpoints were rechecked: HTTP 200, `status=PASS`. The deployed production response still lacks the new historical-context field, so the API/UI acceptance item remains partial.

## 18. Production safety and live verification

No production service, VPS, GraphSense, Cassandra, or Bitcoin full node was changed. The production SQLite was read through a read-only connection for the audit/replay input. Heavy replay ran as a separate local process. Existing production health and summary HTTP endpoints were live and returned 200; live collector unaffected was not independently measured beyond the live response.

## 19. Known limitations and unavailable datasets

- ETF daily holdings are validated only for IBIT; other funds remain unavailable.
- 2026-08-06 Futures Data Vision archives were unavailable at acquisition time.
- Historical orderbook depth/absorption, liquidation rows, and historical positioning coverage are not sufficient for all events.
- SEC filing publication timestamps and Coin Metrics availability delays are not integrated into the replay features.
- The current event grid is 5-minute sampled; it is not every raw trade timestamp.
- The observed OOS set is too small to claim an empirical edge, stable probability, or tradeability.
- Gross directional evidence is not net profitability; fees, spread, and slippage are not modeled.
- GraphSense exchange attribution remains deferred/unavailable by explicit instruction.

## 20. Resource impact and rollback

Raw download: 670,945,568 bytes compressed; raw folder: approximately 641 MB; free disk after acquisition: approximately 437 GB. Replay is separate from the live collector. Rollback backup: `/root/nce-capital-flow-empirical-backup-20260807T043950Z.tar.gz`, SHA-256 `9019c30ebb3549db50dc34121cdf639121a27f59115e81b610ca5842d693846e`, 2.3 MB source/work-copy archive. Remove only the empirical artifact directories and revert the source changes from that backup; production DB was not modified.

## Final verdict

**`CAPITAL_FLOW_NOT_EMPIRICALLY_VALIDATED`**

The system now produces honest historical evidence and exposes it additively in source/local API, but it must not claim empirical validation until larger, complete, availability-correct historical replay produces a sufficiently sized and temporally stable OOS result and the additive historical context route is deployed and live-verified.
