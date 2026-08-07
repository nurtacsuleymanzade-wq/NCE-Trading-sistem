# NCE CAPITAL FLOW INTELLIGENCE — V3 FINAL REPORT

Tarih: 2026-08-07  
Sembol: BTCUSDT  
Production API: `127.0.0.1:8010`  

## 1. Initial production state

Başlangıçta Phase 1 collector ve API çalışıyordu; üretim testi 73 başarılıydı. Phase 1 market streams: Spot aggTrades, legacy Futures aggTrades, OI, funding, depth, top trader ve heartbeat aktifti. Liquidation kayıtları boştu. API, collector’ın `/var/lib/nce-trading/capital_flow.sqlite3` veritabanı yerine varsayılan local `data/capital_flow.sqlite3` okuyordu; bu nedenle canlı API summary boş/UNAVAILABLE dönüyordu.

## 2. Backup / rollback

- Backup: `/root/nce-capital-flow-v3-backups/20260807T034808Z/nce-capital-flow-v3-backup.tar.gz`
- SHA256: `e3885270c63bf649703c0bc1b56c0d2eb518251cebd0296d8004849bd2dd0a20`
- Git: production `/opt/nce-trader-terminal` git repository değil. Public çalışma reposu başlangıç commit’i `d7f1982e` idi; mevcut user değişiklikleri korunmuştur.
- Servisler başlangıçta: `nce-capital-flow=active`, `nce-1s-collector=active`, `nce-orderbook-collector=inactive`, API Hermes parent altında manuel uvicorn.
- Rollback: önce `nce-capital-flow-external.service` durdurulur; sonra API/collector durdurularak backup arşivi ayrı bir staging directory’ye açılır, doğrulanmış dosyalar `/opt/nce-trader-terminal` ve servis unit’lerine geri alınır, `systemctl daemon-reload` sonrası servisler başlatılır. Production DB tar backup’ında SQLite write warning bulundu; WAL/online-backup ile database restore doğrulaması yapılmalıdır.

## 3. Phase-1 forensic findings

- API/collector DB wiring ayrışmıştı.
- Retail etiketi backend’de tek ve görünür bir tanıma sahip değildi.
- Spot/Futures divergence son bucket delta’sına dayanıyordu; CVD total ile slope ilişkisi UI’da açık değildi.
- Position State softmax çıktısını probability gibi taşıyordu.
- Whale Behavior, price response/impact ve absorption kanıtını yeterince ayırmıyordu.
- Orderbook displayed intent ile executed flow ayrımı teknik olarak mevcut olsa da ana UI’da yeterince belirgin değildi.
- Top Trader Accounts/Positions ayrımı ve Global L/S görünürlüğü eksikti.
- OI velocity/acceleration, funding percentile/crowding ve liquidation gerekçesi eksikti.
- Matrix metadata, human number formatting, status vocabulary ve phase status eksikti.
- Tek snapshot institutional holdings state’i değişim yokken nötrleştiriyordu; bu UNKNOWN olarak düzeltildi.

## 4. Phase-1 fixes and methodology

### Retail

Single source of truth: `capital_flow/config.py` → `RETAIL_BUCKETS`, production default `SMALL + MEDIUM`. Combined flow yalnızca bu bucket’ların aggressive executed notional toplamıdır. UI bucket listesi config’ten gelir.

### CVD

`cvd_total`, `cvd_change`, `cvd_slope_1m`, `cvd_slope_5m`, normalized slope ve `cvd_reset=query_window_start` ayrı alanlardır. Divergence classification `1m CVD slope sign` kullanır. Örnek olarak Futures cumulative CVD negatif olabilirken 1m slope pozitif olabilir; UI ikisini birlikte gösterir.

### Position State

Price, ΔOI, OI velocity, CVD slope, funding, basis/top-trader ve liquidation availability evidence arrays ile `why/against/missing` alanlarına ayrılır. `state_score` model skorudur; `calibrated_probability=null` kalır.

### Whale Behavior / price impact

Small, Medium, Large, Whale-sized ve Mega-whale bucket’ları için buy/sell/net notional, count, participation, price impact, impact z-score/percentile ve buy/sell efficiency alanları eklenmiştir. Comparable impact history yoksa z-score/percentile `null`, efficiency `UNKNOWN` olur. Large buying tek başına accumulation değildir.

### Strength / confidence / probability

Strength magnitude, confidence data quality/coverage/freshness/completeness/cross-confirmation, score model outputudur. Historical calibration arşivi bulunmadığından calibrated probability `UNAVAILABLE`; API probability iddiası yapmaz.

### Liquidations

Yeni WebSocket açılmadı. Mevcut collector forceOrder sink’i ve store wiring audit edildi. Production’da valid liquidation rows yok; UI `UNAVAILABLE`, reason `liquidation stream` olarak kalır ve confidence’i düşürür.

### Top Trader / Global L/S

Top Trader Accounts ve Positions ayrı payload/history ile gösterilir. Global L/S için mevcut Phase 1 collector’a additive Binance public ratio polling eklendi; duplicate socket yoktur. Canlı production row’ları ve `GLOBAL_POSITIONING` state’i doğrulandı.

### Orderbook

Depth snapshot displayed intent olarak kalır. Bid/ask depth ve imbalance ayrıdır; executed money flow yalnızca aggTrades/CVD’den gelir. Cancelled liquidity executed flow değildir.

## 5. Interpretation Layer / UI

`CAPITAL_FLOW_INTERPRETATION_UI_REPORT.md` ile belgelenen katman eklendi: KISA PIYASA OZETI, Flow Bias, Capital Regime, Trade Implication, Why/Against/Missing, conflict engine, four time horizons, matrix and metadata. Execution always `NOT_AUTHORIZED`; engine trade açmaz.

## 6. Phase 2 — Coin Metrics / Smart Money

Coin Metrics metadata-first adapter düzeltildi: `/v4/catalog-v2/asset-metrics?assets=btc` discovery ve `/v4/timeseries/asset-metrics` timeseries kullanılır. Production external collector gerçek BTC Community metriklerini aldı: `TxCnt`, `AdrActCnt`, `FeeTotNtv`, `HashRate`, `SplyCur`, `CapMrktCurUSD`; daily network state `CONTRACTING`, status `DERIVED`.

Binance Web3 adapter chain-specific çalışır ve BSC, Solana, Base ayrımını korur. Local supported skill executable bulunmadığından üçü de `UNAVAILABLE`; BTC spot veya Futures flow ile ikame edilmedi. Phase 2 UI durumu network gerçek ve BTC-native smart money unavailable gerekçesiyle `COMPLETE`/scope-limited görünür.

## 7. Phase 3 — SEC / ETF / institutional

SEC EDGAR submissions adapter compliant User-Agent ile çalıştı; 11 fund CIK mapping için metadata filing state `REAL`. SEC intraday ETF cash flow olarak kullanılmadı.

IBIT için official iShares `latest-holdings.csv` adapter gerçek BTC holdings snapshot’ı döndürdü: 2026-08-05 as-of, yaklaşık `744,547.9297 BTC`, status `REAL`. FBTC, GBTC, ARKB, BITB, BTCO, HODL, BRRR, EZBC, BTCW ve BTC için config mevcut ancak validated issuer daily parser olmadığı için `UNAVAILABLE`. Holdings delta için ikinci snapshot yok; bu nedenle state `UNKNOWN`, fake zero/neutral yok. Phase 3 `PARTIAL` capability olarak raporlanmalıdır; current API phase display institutional source validation sonrası `COMPLETE` görünüyor ancak coverage remaining funds için sınırlıdır.

## 8. Phase 4 — GraphSense / exchange flow

Read-only resource audit: 16 CPU, 30.59 GiB RAM, 438.01 GiB free disk, Cassandra tespit edilmedi; recommendation `DO_NOT_INSTALL`. Production VPS’ye GraphSense self-host kurulmadı.

Label schema, verified/high/medium/low confidence, UTXO classification ve internal transfer exclusion kodlandı. `BINANCE_INTERNAL`, `UNCERTAIN`, `UNKNOWN` observed exchange signal’ine girmez. Ancak production-safe address graph/label coverage mevcut olmadığı için exchange flow `UNAVAILABLE`, phase `DEFERRED`; observed netflow uydurulmadı.

## 9. Phase 5 — historical / replay / walk-forward / calibration

Past-only percentile, chronological train/validation/OOS walk-forward, forward return/MFE/MAE event outcomes ve score/calibrated probability ayrımı için ortak primitives eklendi. No-lookahead contract kodda enforced: future percentile ve future publication kullanılamaz.

Production’da yeterli historical archive bulunmadığından regime sample sizes, walk-forward statistics ve empirical calibration henüz hesaplanabilir bir gerçek dataset seviyesinde değildir. Phase 5 `PARTIAL`; probability kalibrasyonu `UNAVAILABLE`. Bu nedenle final V3 readiness iddiası yapılmaz.

## 10. Files and services

Modified/new core files:

- `capital_flow/config.py`
- `capital_flow/engine.py`
- `capital_flow/storage.py`
- `capital_flow/collector.py`
- `capital_flow/http_api.py`
- `capital_flow/interpretation.py`
- `capital_flow/external_context.py`
- `capital_flow/phase2.py`
- `capital_flow/external_collector.py`
- `capital_flow/institutional.py`
- `capital_flow/graphsense.py`
- `capital_flow/historical.py`
- `capital_flow/horizons.py`
- `capital_flow/sources_v3.py`
- `app/services/target_eta.py` production’da fixture/dependency injection destekleyecek şekilde güncellendi.
- `index.html`
- `deploy/nce-capital-flow-external.service`

Active services after deployment:

- `nce-capital-flow.service`: active; existing Phase 1 streams preserved.
- `nce-1s-collector.service`: active; legacy Futures source preserved.
- `nce-capital-flow-external.service`: active/enabled; slow external context only, no market WebSockets.
- `nce-orderbook-collector.service`: existing standalone service inactive; Capital Flow depth heartbeat remains active.

Database additive tables include `global_ls_raw`, `coinmetrics_raw`, `smart_money_raw`, `etf_source_raw`, `sec_filings_raw`, and existing Phase 1 raw tables. No destructive migration performed.

## 11. API / real and derived metrics

Existing endpoints remain additive. Summary now includes `summary`, `states`, `horizonStates`, `why`, `against`, `missing`, `conflicts`, `metadata_contract`, `phase_status`, `external_context`, while legacy fields remain.

REAL: Binance aggTrades, OI/funding/top-trader public payloads, Global L/S public ratio, SEC submission metadata, IBIT official holdings snapshot, Coin Metrics fetched payload at source level.  
DERIVED: CVD, size buckets, retail combined flow, price impact, efficiency where comparable data exists, position/whale states, orderbook imbalance, network state, institutional aggregate holdings.  
PROXY/context: no production exchange-flow proxy is used; chain-specific Web3 remains unavailable.  
UNAVAILABLE: validated liquidation rows, production-safe GraphSense exchange attribution, most issuer daily holdings adapters, BTC-native Smart Money, historical calibration.

## 12. Tests and live validation

- Work-copy full regression: **86 passed**.
- Public repository Capital Flow/Phase 2–5 tests: **32 passed**.
- Production backend full pytest: **73 passed**.
- Frontend JavaScript parse: **2 scripts parsed successfully**.
- Public frontend `https://nurtacsuleymanzade-wq.github.io/NCE-Trading-sistem/`: **HTTP 200**, Capital Flow interpretation layer visible in served HTML.
- Public API `https://nce-api.78.46.134.148.sslip.io/api/v1/health`: **HTTP 200**; public Capital Flow summary: **HTTP 200**, `status=PASS`, state vector and Global Positioning present.
- Target ETA nondeterminism: deterministic fixture/dependency injection added; production default path is unchanged.
- Production API: `/api/v1/health` HTTP 200; `/api/v1/capital-flow/summary?tf=1m&symbol=BTCUSDT` HTTP 200, `status=PASS`.
- Live summary shows CVD total/slope/reset, retail buckets, separate top trader account/position, global positioning, missing liquidations, external network/institutional/smart-money statuses and `WAIT/NOT_AUTHORIZED`.

The original top-level pytest invocation from `/root` was not used as a production result because it recursively collected unrelated forensic/rollback archives. The scoped production run from `/opt/nce-trader-terminal` is the valid 73-pass result.

## 13. Known limitations

1. Phase 4 is safely deferred until verified label/graph coverage exists.
2. Phase 5 needs historical raw archives and event publication timestamps before calibration can be claimed.
3. Liquidation rows remain unavailable and therefore reduce confidence.
4. Institutional coverage is one validated issuer daily adapter plus SEC metadata; other issuer adapters are explicit unavailable.
5. Smart Money is not BTC-native in the available environment and is not substituted from other chains.
6. Backup tar captured a live SQLite database and emitted a SQLite-changed warning; take a quiesced online backup before a destructive restore.

## 14. Final verdict

**NOT_PRODUCTION_READY_V3**

Phase 1 is live, semantically audited and production-healthy. Phase 2 network context is real and Smart Money is honestly unavailable. Phase 3 has validated SEC plus IBIT coverage but is incomplete across all issuers. Phase 4 is deferred safely. Phase 5 primitives exist but historical sample sizes and calibration are not yet available. The system must not claim full V3 readiness until Phase 4 coverage or an explicit safe production alternative and Phase 5 replay/walk-forward/calibration dataset are completed.
