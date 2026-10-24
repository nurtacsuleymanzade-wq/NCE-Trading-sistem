# PREREG_EDGE — NCE Edge Finder kalibrasiyası (DONDURULUB)
Tarix: 2026-10-24 (Bakı vaxtı). Bu sənəd nəticələrə baxılmadan yazılıb. Ayrıca /workspace/ob100_prereg/PREREG_FROZEN.md-yə toxunmur.

## Data (ictimai, yenidən endirilə bilən)
- Binance USDT-M futures BTCUSDT 1h klines (taker buy həcmi daxil) — data.binance.vision, 2022-01 → son tam gün.
- Binance spot BTCUSDT 1h klines (taker buy həcmi) — eyni dövr.
- Binance futures metrics (5m: OI, top trader L/S hesab nisbəti, taker L/S həcm nisbəti) — günlük fayllar, saat sonu dəyəri.
- Binance futures fundingRate (8h) — aylıq fayllar.
- Book: /workspace/boss_msb/feat/vps_book_1m.parquet (11 avq – 7 okt 2026, 1 dəq; imb10_mean).
- CVD = Σ(2·taker_buy − həcm). Likvidasiya xəritəsi tarixən bərpa oluna bilmir → "maqnit" PDH/PDL likvidlik proksisi ilə yoxlanır (hesabatda qeyd olunur).

## Bölgü (xronoloji)
- Bar/metrics kombinləri: TRAIN 2022-01-01 → 2024-12-31; OOS 2025-01-01 → son.
- Book kombinləri: ilk 60% günlər TRAIN, son 40% OOS.
- Verdikt YALNIZ OOS əsasında. TRAIN yalnız məlumat üçündür. Hədlər aşağıda sabitdir (TRAIN-dən optimallaşdırılmır).

## Kombinlər (15) — hadisə → nəticə
Hadisələr saat bağlanışında yoxlanır (book: dəqiqə). Eyni kombində üst-üstə düşən hadisələr: hadisədən sonra nəticə üfüqü qədər yeni hadisə sayılmır (müstəqillik üçün).
1. PDH_SFP_SHORT: 1h şam ötən günün (UTC) high-ını keçir, altında bağlanır → 4h sonra qiymət girişdən aşağı.
2. PDL_SFP_LONG: PDL altına keçir, üstündə bağlanır → 4h sonra yuxarı.
3. SPOT_LEADS_UP: son 4h spot CVD > 0 və futures CVD < 0 və hər biri öz 4h həcminin ≥5%-i → 4h sonra yuxarı.
4. SPOT_LEADS_DOWN: spot CVD < 0, futures CVD > 0 (eyni 5% şərti) → 4h sonra aşağı.
5. OI_UP_PRICE_DOWN: 4h-da OI ≥ +2% və qiymət ≤ −1% (yeni shortlar) → 4h sonra aşağı (davam).
6. LONG_FLUSH: 4h-da OI ≤ −3% və qiymət ≤ −1.5% → 4h sonra yuxarı (geri qayıtma).
7. FUNDING_HOT: son funding ≥ 0.03% → 8h sonra aşağı.
8. FUNDING_NEG: son funding ≤ −0.01% → 8h sonra yuxarı.
9. CROWDED_LONG: top trader L/S hesab nisbəti son 30 günün 90-cı faizindən yuxarı → 4h sonra aşağı.
10. TAKER_SELL_EXHAUST: 1h taker buy/sell həcm nisbəti ≤ 0.7 → 1h sonra yuxarı.
11. VA_REENTRY_POC: ötən günün value area-sından (1h həcm profili, 70%, 0.1% qutular) kənarda açılıb içəri bağlanan saat → 8h içində ötən günün POC-una toxunur. Baza: bütün saatlarda 8h içində POC-a toxunma.
12. PDH_MAGNET: qiymət PDH-ın 0.3%-i altında (toxunmadan) → 4h içində PDH-a toxunur. Baza: eyni anda simmetrik səviyyəyə (bağlanış −eyni məsafə) toxunma tezliyi.
13. BOOK_BID_HEAVY: imb10_mean ≥ 0.30 → 15 dəq sonra mid yuxarı.
14. BOOK_ASK_HEAVY: imb10_mean ≤ −0.30 → 15 dəq sonra mid aşağı.
15. SFP_PLUS_CROWD: #1 hadisəsi + funding > 0.01% + 4h OI > 0 → 4h sonra aşağı.

## Baza (random)
Nəticə istiqamətli kombinlərdə: eyni OOS dövründə BÜTÜN saatlarda (book: dəqiqələrdə) eyni istiqamətli nəticənin tezliyi (təsadüfi giriş). #11, #12 yuxarıdakı kimi.

## Statistika
- Faiz + Wilson 95% CI. Lift = faiz / baza.
- Birtərəfli binomial test (baza ehtimalına qarşı), Bonferroni: p < 0.05/15 = 0.00333.
- n < 20 → ETİBARSIZ (UNRELIABLE).
- EDGE: OOS n ≥ 20, OOS Wilson aşağı sərhəd > baza VƏ Bonferroni p < 0.00333. Əks halda NO EDGE.
- Trade çevirməsi (məlumat): hadisə istiqamətində orta irəli gəlir − 10 bp xərc. EDGE olsa belə net ≤ 0 isə "xərcdən sonra trade edilməz" yazılır.

## Canlı vəziyyət və forward tracking
Build zamanı ictimai REST datası ilə hansı şərtlərin indi doğru olduğu (zaman möhürü ilə). Kollektor skripti yeni hadisələri və nəticələrini əlavə edir; forward nəticələr ayrıca göstərilir, OOS verdiktini dəyişmir.
