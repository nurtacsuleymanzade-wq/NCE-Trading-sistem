# CAPITAL FLOW INTERPRETATION UI REPORT

## Amaç

Capital Flow ekranı, ham metrikleri kaldırmadan mühendislik dashboard’ından kanıt-zinciri dashboard’ına dönüştürüldü. İlk görünür katman artık `KISA PIYASA OZETI`, `FLOW BIAS`, `CAPITAL REGIME`, `TRADE IMPLICATION` ve `WHY / AGAINST / MISSING` bloklarını gösteriyor. Teknik ayrıntılar ikinci katmanda kalıyor.

## UI sözleşmesi

- `summary.shortText`: gerçek canlı state’lerden dinamik üretilen en fazla altı kısa cümle.
- `summary.flowBias`, `summary.capitalRegime`, `summary.tradeImplication`, `summary.execution`: enum ve insan okunabilir kart değerleri.
- `states`: SPOT, FUTURES, WHALE_SIZED, RETAIL, OI, DERIVATIVES, ORDERBOOK, TOP_TRADERS, GLOBAL_POSITIONING, LIQUIDATIONS, EXCHANGE, INSTITUTIONAL, NETWORK_CONTEXT ve SMART_MONEY.
- Her state `state`, `strength`, `confidence`, `timeframe`, `status` taşır. `score` hiçbir yerde probability olarak sunulmaz.
- `why`, `against`, `missing`, `conflicts`: snapshot girdilerinden türetilir; hard-coded piyasa sonucu yoktur.
- `metadata_contract`: retail bucket kaynağı, calibration durumu ve `execution_authorized=false` sözleşmesini açıklar.

## İnsan diline çevrilen kritik ayrımlar

- Spot/Futures CVD’de total, change, 1m slope, 5m slope ve reset kuralı ayrıdır. Sınıflandırma 1m CVD slope işaretini kullanır; cumulative total ayrı gösterilir.
- `query_window_start` reset semantiği API’de görünür.
- Retail tek backend kaynağından `RETAIL_BUCKETS = ["SMALL", "MEDIUM"]` olarak gelir. UI aynı listeyi ve combined net flow’u gösterir.
- Orderbook `displayed liquidity / intent`; aggTrade `executed flow` olarak gösterilir. Ask/bid imbalance gerçekleşmiş para akışı değildir.
- Top Trader Accounts ve Top Trader Positions ayrıdır; Global Positioning ayrıca crowd state olarak gösterilir.
- Institutional tek snapshot’ta değişim yoksa `UNKNOWN`; yapay `NEUTRAL` değildir.
- Liquidation sink boşsa `UNAVAILABLE` ve nedeni gösterilir.
- Score, strength ve confidence birbirinden ayrıdır. Historical calibration yoksa calibrated probability `UNAVAILABLE` kalır.

## Canlı UI doğrulaması

2026-08-07 tarihinde BTCUSDT production API’den doğrulanan örnek:

- HTTP 200 / `status=PASS`
- Spot ve Futures CVD total/slope/reset alanları mevcut.
- Retail: `SMALL + MEDIUM`, `SELLING`, combined net flow mevcut.
- Global Positioning: Binance public global account ratio, `LONG_BIASED`, `REAL`, confidence 95.
- Top Trader Accounts ve Positions ayrı payload olarak mevcut.
- Network: Coin Metrics Community, `DERIVED`, `CONTRACTING`.
- Institutional: IBIT issuer snapshot `REAL`; delta için ikinci snapshot olmadığı için aggregate state `UNKNOWN`.
- Smart Money: BTC-native eşleşme ve chain adapterları `UNAVAILABLE`; fake BTC flow üretilmedi.
- Exchange Flow: GraphSense attribution ertelendiği için `DEFERRED/UNAVAILABLE`.
- Trade: `WAIT`; execution: `NOT_AUTHORIZED`.

## Frontend QA

`index.html` içine eklenen yorumlama görünümü ile teknik görünüm birlikte tutuldu. JavaScript parse kontrolü iki script bloğunda başarılıdır. Ham API değerleri korunur; ana kartlarda compact number formatting kullanılır (`$9.01`, `$20.71K`, `$15.46M`).

