# NCE Trading System — sayt (frontend)

Canlı sayt: https://nurtacsuleymanzade-wq.github.io/NCE-Trading-sistem/

BTCUSDT (Binance USDT-M) üçün order flow, volume profile (AMT), ICT strukturu, capital flow, giriş qiymətinə əsaslanan
likvidasiya xəritəsi və **ölçülmüş** yekdil qərar. **Bu, maliyyə məsləhəti deyil.**

## Quruluş (14.10.2026-dan)

| Hissə | Harada | Nə edir |
|---|---|---|
| Sayt (bu repo) | `index.html`, `assets/nce-app.js`, `assets/nce.css` | Bir nüsxə kod. TF: 1s, 1m, 3m, 5m, 15m, 1h, 4h, 1D, 3D. Panellər: yekdil qərar, TF matrisi, volume profile, ICT, capital flow, likvidasiya, ssenarilər, jurnal statistikası, metod |
| Backend | private repo `nce-core` (VPS-də işləyir) | 7/24 collector (Binance/Bybit/Deribit public), hər 60 s mühərrik, jurnallar (hədəf, ICT, ssenari, proqnoz), yekdil qərar |
| API | `https://151.115.77.234.sslip.io/nce-api/` | Yalnız oxuma JSON (`all.json`, `consensus.json`, `stats.json` …). CORS yalnız bu saytın domeninə |
| Data | VPS `/data/muhittin_models/ncecore/` + Google Drive `Muhittin/NCE_data` (gündə 2 dəfə) | Xam data (order book 100 ms, trade, OI, funding, likvidasiya, max pain), 1 s snapshot-lar, DuckDB jurnalları |
| Arxiv | private repo `NCE-arxiv` (bütün branch-lar), VPS-də git bundle, bu repoda tag `arxiv-2026-10-14` | 14.10.2026-ya qədərki tam vəziyyət |

Backend əlçatmazdırsa sayt açıq şəkildə **"backend offline"** yazır — uydurma və ya səssiz fallback rəqəm göstərilmir.

## Köhnə versiya

- `legacy/index.html` — köhnə sayt (brauzerdə birbaşa Binance fallback ilə işləyir; köhnə backend artıq yoxdur).
- `legacy/hesabatlar/` — köhnə bot hesabatları; `legacy/backend_v1/` — köhnə backend-in repoda qalan hissəsi (capital_flow, skriptlər).
- `data/` və `historical/` — köhnə data faylları (13.08.2026-da donub). Repoya yeni data commit edilmir; data VPS + Drive-dadır.

## Yekdil qərar necə qurulur

1. Hər model ayrıca göstərilir, yanında jurnaldan ölçülmüş tarixi isabət və baza.
2. Konsensusa yalnız bazanı həm statistik (Wilson CI alt sərhədi > baza), həm də praktik (lift ≥ 1.05) keçən modellər girir; çəki = lift.
3. Heç biri keçmirsə: "edge yoxdur" + baza tezliyi.
4. Data keyfiyyəti proqnoz güvənindən ayrı göstərilir.
