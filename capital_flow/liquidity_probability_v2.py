"""Liquidity Probability & Targeting Engine V2.

This module is intentionally conservative.  It computes auditable observed and
derived features, but it never promotes a score, a heuristic, or a legacy V1
calibration table to a production probability.  A model probability is only
available when a V2 walk-forward calibration artifact is supplied.

Every public formula is documented in the requested four-part format in the
function docstrings.  The returned payload keeps ``OBSERVED / DERIVED /
ESTIMATED · MODEL`` provenance on every important field so the UI cannot
silently merge market facts with estimates.
"""
from __future__ import annotations

import math
import statistics
from collections import defaultdict
from typing import Any, Mapping, Sequence


HORIZONS_MINUTES = (5, 15, 30, 60, 240)
EPSILON = 1e-12
PROVENANCE = {
    "observed": "OBSERVED · REAL",
    "derived": "DERIVED",
    "model": "ESTIMATED · MODEL",
}


def _num(value: Any, default: float | None = None) -> float | None:
    try:
        number = float(value)
        return number if math.isfinite(number) else default
    except (TypeError, ValueError):
        return default


def _clip(value: float | None, low: float = 0.0, high: float = 1.0) -> float | None:
    if value is None:
        return None
    return max(low, min(high, float(value)))


def _round(value: float | None, digits: int = 6) -> float | None:
    return None if value is None else round(float(value), digits)


def _percentile(values: Sequence[float], value: float) -> float | None:
    clean = sorted(float(x) for x in values if _num(x) is not None)
    if not clean:
        return None
    below = sum(1 for x in clean if x <= value)
    return below / len(clean)


def provenance_value(value: Any, status: str, *, source: str, timestamp_ms: int | None = None, age_seconds: float | None = None, methodology: str | None = None) -> dict[str, Any]:
    """Wrap a value without losing its provenance."""
    return {
        "value": value,
        "status": status,
        "source": source,
        "timestamp_ms": timestamp_ms,
        "age_seconds": age_seconds,
        "methodology": methodology,
    }


def price_distance(target: float, current_price: float) -> dict[str, Any]:
    """FORMÜL:
    d_i = (L_i - P_t) / P_t

    NE HESAPLIYOR?
    Hedefin mevcut mark price'a signed yüzde uzaklığını hesaplar.

    DEĞİŞKENLER:
    L_i = target fiyatı; P_t = mevcut mark price; d_i = signed uzaklık.

    ÇIKTI NASIL YORUMLANIR?
    d_i > 0 hedef yukarıda, d_i < 0 hedef aşağıda, d_i = 0 aynı seviyededir.

    ÖRNEK:
    P_t=65000 ve L_i=63700 ise d_i=-0.02, yani -2.00%.
    """
    distance = (float(target) - float(current_price)) / float(current_price) if current_price else None
    return {"value": _round(distance), "percent": _round(distance * 100 if distance is not None else None, 4), "status": "DERIVED"}


def atr_normalized_distance(target: float, current_price: float, atr: float | None) -> dict[str, Any]:
    """FORMÜL:
    d_i^ATR = |L_i - P_t| / ATR_n

    NE HESAPLIYOR?
    Hedefin fiyat birimi yerine mevcut volatilite birimindeki uzaklığını hesaplar.

    DEĞİŞKENLER:
    L_i = target; P_t = current price; ATR_n = seçilen lookback ATR; d_i^ATR = ATR uzaklığı.

    ÇIKTI NASIL YORUMLANIR?
    2.0, hedefe ulaşmak için yaklaşık iki ATR hareket gerektiğini belirtir.

    ÖRNEK:
    |64500-65000| / 250 = 2.0 ATR.
    """
    value = abs(float(target) - float(current_price)) / float(atr) if atr and atr > 0 else None
    return {"value": _round(value, 4), "status": "DERIVED" if value is not None else "UNAVAILABLE"}


def robust_density(raw_exposures: Sequence[float], raw_exposure: float) -> dict[str, Any]:
    """FORMÜL:
    Z_i^Density = [log(1+Q_i) - Median(log(1+Q))] /
                   [1.4826 * MAD(log(1+Q)) + epsilon]
    D_i = sigmoid(Z_i^Density) = 1 / (1 + exp(-Z_i^Density))

    NE HESAPLIYOR?
    Bir cluster'ın diğer cluster'lara göre robust/anormal büyüklüğünü hesaplar.

    DEĞİŞKENLER:
    Q_i = raw estimated exposure; Median = log exposure ortancası;
    MAD = median absolute deviation; 1.4826 = normal ölçek düzeltmesi;
    epsilon = sıfıra bölme koruması; Z = robust z-score; D = density score.

    ÇIKTI NASIL YORUMLANIR?
    Z=0 tipik yoğunluk, Z>2 sıra dışı büyük cluster; D olasılık değildir.

    ÖRNEK:
    Q değerleri loglandıktan sonra median ve MAD ile normalize edilir; D yalnızca
    sıralama/yoğunluk metriğidir, hedefe gitme probability'si değildir.
    """
    logs = [math.log1p(max(0.0, float(x))) for x in raw_exposures if _num(x) is not None and float(x) >= 0]
    if not logs:
        return {"z": None, "score": None, "status": "UNAVAILABLE", "provenance": PROVENANCE["derived"]}
    median = statistics.median(logs)
    mad = statistics.median([abs(x - median) for x in logs])
    current = math.log1p(max(0.0, float(raw_exposure)))
    z = (current - median) / (1.4826 * mad + EPSILON)
    density = 1.0 / (1.0 + math.exp(-max(-60.0, min(60.0, z))))
    return {"z": _round(z, 4), "score": _round(density, 6), "median_log": _round(median, 6), "mad_log": _round(mad, 6), "status": "DERIVED", "provenance": PROVENANCE["derived"]}


def weighted_orderbook_imbalance(book: Mapping[str, Any], current_price: float, *, levels: int = 20, decay_lambda: float | None = None) -> dict[str, Any]:
    """FORMÜL:
    B_K = sum(k=1..K) w_k q_k^bid; A_K = sum(k=1..K) w_k q_k^ask;
    w_k = exp(-lambda * d_k); I_book = (B_K - A_K)/(B_K + A_K + epsilon)

    NE HESAPLIYOR?
    İlk K bid/ask seviyesindeki displayed liquidity dengesini hesaplar.

    DEĞİŞKENLER:
    q_k^bid/ask = seviyedeki quantity; d_k = current price'a relatif uzaklık;
    lambda = distance decay coefficient; B_K/A_K = weighted sides;
    I_book = -1..+1 normalized imbalance.

    ÇIKTI NASIL YORUMLANIR?
    +1 bid dominance, -1 ask dominance, 0 balance. Bu displayed intent'tir,
    gerçekleşmiş flow değildir.

    ÖRNEK:
    Weighted bid=800, ask=500 ise I_book=300/1300=0.231.
    """
    if not current_price or not isinstance(book, Mapping):
        return {"value": None, "weightedBid": None, "weightedAsk": None, "status": "UNAVAILABLE", "provenance": PROVENANCE["derived"]}
    # If a learned/configured decay is absent, infer it from the observed book
    # span. This is a measurement-derived coefficient, not a hand-tuned score.
    lam = _num(decay_lambda)
    bids, asks = 0.0, 0.0
    observed_distances: list[float] = []
    for key in ("bids", "asks"):
        for row in list(book.get(key, []) or [])[:levels]:
            if isinstance(row, (list, tuple)) and len(row) >= 1:
                price = _num(row[0])
                if price is not None and price > 0:
                    observed_distances.append(abs(price - current_price) / current_price)
    if lam is None:
        median_distance = statistics.median([x for x in observed_distances if x > 0]) if any(x > 0 for x in observed_distances) else None
        lam = 1.0 / median_distance if median_distance else 0.0
    for side, key in (("bid", "bids"), ("ask", "asks")):
        rows = book.get(key, []) or []
        total = 0.0
        for row in list(rows)[:levels]:
            if not isinstance(row, (list, tuple)) or len(row) < 2:
                continue
            price, quantity = _num(row[0]), _num(row[1])
            if price is None or quantity is None or quantity < 0:
                continue
            distance = abs(price - current_price) / current_price
            total += math.exp(-lam * distance) * quantity
        if side == "bid":
            bids = total
        else:
            asks = total
    value = (bids - asks) / (bids + asks + EPSILON)
    return {"value": _round(value, 6), "weightedBid": _round(bids, 8), "weightedAsk": _round(asks, 8), "lambda": lam, "levels": levels, "status": "DERIVED", "provenance": PROVENANCE["derived"], "interpretation": "displayed liquidity / intent; not executed flow"}


def aggression(market_buy_volume: float, market_sell_volume: float) -> dict[str, Any]:
    """FORMÜL:
    Agg_t = (V_t^MarketBuy - V_t^MarketSell) /
            (V_t^MarketBuy + V_t^MarketSell + epsilon)

    NE HESAPLIYOR?
    Aggressive trades'in buy/sell dengesini hesaplar.

    DEĞİŞKENLER:
    V_t^MarketBuy = ask'a vuran volume; V_t^MarketSell = bid'e vuran volume;
    Agg_t = normalized aggression; epsilon = zero-division guard.

    ÇIKTI NASIL YORUMLANIR?
    +1 buy aggression, -1 sell aggression, 0 balanced.

    ÖRNEK:
    Buy=80 BTC, sell=20 BTC => Agg=60/100=0.60.
    """
    buy, sell = max(0.0, float(market_buy_volume or 0)), max(0.0, float(market_sell_volume or 0))
    return {"value": _round((buy - sell) / (buy + sell + EPSILON), 6), "marketBuyVolume": buy, "marketSellVolume": sell, "status": "DERIVED", "provenance": PROVENANCE["derived"]}


def robust_zscore(value: float | None, history: Sequence[float]) -> dict[str, Any]:
    """FORMÜL:
    Z_t = [X_t - Median(X)] / [1.4826 * MAD(X) + epsilon]

    NE HESAPLIYOR?
    Anlık feature'ın rolling history'ye göre sıra dışılığını hesaplar.

    DEĞİŞKENLER:
    X_t = current feature; Median = history median; MAD = median absolute
    deviation; 1.4826 = normal ölçek düzeltmesi; epsilon = guard.

    ÇIKTI NASIL YORUMLANIR?
    Z=+3.2, current value'ın history'ye göre güçlü pozitif outlier olduğunu gösterir.

    ÖRNEK:
    Agg=0.4 normal olabilir; Agg Z=3.2 olağandışı buy aggression'dır.
    """
    values = [float(x) for x in history if _num(x) is not None]
    if value is None or not values:
        return {"value": None, "status": "UNAVAILABLE", "provenance": PROVENANCE["derived"]}
    median = statistics.median(values)
    mad = statistics.median([abs(x - median) for x in values])
    return {"value": _round((float(value) - median) / (1.4826 * mad + EPSILON), 5), "median": _round(median, 6), "mad": _round(mad, 6), "status": "DERIVED", "provenance": PROVENANCE["derived"]}


def absorption(aggressive_volume: float, price_change: float, tick_size: float, refill_ratio: float) -> dict[str, Any]:
    """FORMÜL:
    Abs = AggressiveVolume / (|deltaPrice| / tickSize + 1) * RefillRatio

    NE HESAPLIYOR?
    Aggressive flow'un fiyatı hareket ettirememesi ve pasif likiditenin refill
    olması birlikteyken absorption intensity hesaplar.

    DEĞİŞKENLER:
    AggressiveVolume = ilgili yöndeki market volume; deltaPrice = interval price
    change; tickSize = minimum price increment; RefillRatio = refill ratio.

    ÇIKTI NASIL YORUMLANIR?
    Ham değer tek başına HIGH/LOW değildir; rolling percentile/z-score ile okunmalıdır.

    ÖRNEK:
    Buy flow yüksek, |deltaPrice| küçük ve refill yüksekse ask absorption artar.
    """
    denominator = abs(float(price_change or 0.0)) / max(float(tick_size or 0.1), EPSILON) + 1.0
    raw = max(0.0, float(aggressive_volume or 0.0)) / denominator * max(0.0, float(refill_ratio or 0.0))
    return {"raw": _round(raw, 8), "status": "DERIVED", "provenance": PROVENANCE["derived"], "interpretation": "rank only after rolling percentile/z-score"}


def replenishment(removed_quantity: float, added_quantity: float) -> dict[str, Any]:
    """FORMÜL:
    R_p = AddedQty_p / (RemovedQty_p + epsilon)

    NE HESAPLIYOR?
    Bir price level'de kaldırılan liquidity'nin ne kadarının yeniden geldiğini hesaplar.

    DEĞİŞKENLER:
    AddedQty_p = yeniden eklenen quantity; RemovedQty_p = kaldırılan quantity;
    R_p = replenishment ratio; epsilon = guard.

    ÇIKTI NASIL YORUMLANIR?
    R_p=0.85, removed 100 ve added 85 örneğinde yüksek replenishment'tır.

    ÖRNEK:
    85 / (100 + epsilon) yaklaşık 0.85.
    """
    removed, added = max(0.0, float(removed_quantity or 0.0)), max(0.0, float(added_quantity or 0.0))
    return {"value": _round(added / (removed + EPSILON), 6), "status": "DERIVED", "provenance": PROVENANCE["derived"]}


def _profile_membership(price: float, profile: Mapping[str, Any]) -> tuple[float, str | None]:
    for label, strength in (("hvn", 1.0), ("poc", 0.8), ("vah", 0.6), ("val", 0.6)):
        values = profile.get(label, []) if label == "hvn" else [profile.get(label)]
        if isinstance(values, (int, float)):
            values = [values]
        if any(_num(x) is not None and abs(float(x) - price) <= max(abs(price) * 0.0005, 1.0) for x in values):
            return strength, label.upper()
    return 0.0, None


def path_analysis(current_price: float, target: float, levels: Sequence[Mapping[str, Any]], profile: Mapping[str, Any] | None = None, *, directional_aggression: float | None = None, atr: float | None = None) -> dict[str, Any]:
    """FORMÜL:
    O_j = mean(Depth_j, Absorption_j, HVN_j, OpposingFlow_j, Replenishment_j)
    PathResistance_i = (1/N) * sum(j=1..N) O_j
    PathEase_i = 1 / (1 + PathResistance_i)

    NE HESAPLIYOR?
    Current price ile target arasındaki bin'lerin geçiş zorluğunu ve engelleri
    çıkarır; component weights model eğitilmeden elle uydurulmaz.

    DEĞİŞKENLER:
    O_j = j'inci bin obstacle score; N = bin sayısı; Depth = resting wall;
    Absorption = passive absorption; HVN = high-volume node; OpposingFlow = ters
    aggression; Replenishment = refill evidence; PathResistance = ortalama;
    PathEase = resistance'in reciprocal diagnostic'i.

    ÇIKTI NASIL YORUMLANIR?
    PathEase yüksekse yol daha açık, düşükse yol daha zor. PathEase probability değildir.

    ÖRNEK:
    İki güçlü bid wall ve bir HVN target yolunda ise obstacle listesinde görünür;
    mainObstacle en yüksek severity'li bariyerdir.
    """
    if not current_price or not target:
        return {"status": "UNAVAILABLE", "pathResistance": None, "pathEase": None, "steps": [], "mainObstacle": None}
    lo, hi = sorted((float(current_price), float(target)))
    direction = 1 if target > current_price else -1
    path_rows: list[dict[str, Any]] = []
    candidate_levels: list[tuple[float, Mapping[str, Any]]] = []
    for row in levels or []:
        price = _num(row.get("price"))
        if price is not None and lo < price < hi:
            candidate_levels.append((price, row))
    profile = profile or {}
    for price, row in sorted(candidate_levels, key=lambda x: abs(x[0] - current_price)):
        depth = _clip(_num(row.get("wall_strength"), 0.0), 0.0, 1.0) or 0.0
        absorbed = _clip(_num(row.get("absorption_score"), 0.0), 0.0, 1.0) or 0.0
        replenish = _clip(_num(row.get("replenishment_ratio"), 0.0), 0.0, 1.0) or 0.0
        hvn, profile_label = _profile_membership(price, profile)
        side = str(row.get("side", "")).upper()
        opposing = 1.0 if ((direction > 0 and side == "ASK") or (direction < 0 and side == "BID")) else 0.0
        flow_against = 0.0
        if directional_aggression is not None:
            flow_against = _clip((-direction * directional_aggression + 1.0) / 2.0, 0.0, 1.0) or 0.0
        components = {"Depth": depth, "Absorption": absorbed, "HVN": hvn, "OpposingFlow": max(opposing, flow_against), "Replenishment": replenish}
        obstacle = sum(components.values()) / len(components)
        row_type = "bid wall" if side == "BID" else "ask wall" if side == "ASK" else "orderbook level"
        path_rows.append({"price": price, "type": profile_label or row_type, "strength": _round(obstacle, 4), "components": {k: _round(v, 4) for k, v in components.items()}, "classification": str(row.get("classification", "RESTING/UNCONFIRMED")), "liveStatus": row.get("status", "DERIVED"), "provenance": PROVENANCE["derived"]})
    if not path_rows:
        return {"status": "DERIVED", "pathResistance": 0.0, "pathEase": 1.0, "steps": [], "mainObstacle": None, "formula": "PathResistance = mean(obstacle bins); no observed obstacle in interval"}
    resistance = sum(float(x["strength"]) for x in path_rows) / len(path_rows)
    main = max(path_rows, key=lambda x: x["strength"])
    # Slim: the UI renders path as a compact route (6-10 levels).  Emitting
    # every order-book bin inflated the API payload to hundreds of MB
    # (271 targets x thousands of steps, repeated under targets/v2/hova).
    MAX_PATH_STEPS = 15
    steps = path_rows[:MAX_PATH_STEPS]
    return {"status": "DERIVED", "pathResistance": _round(resistance, 4), "pathEase": _round(1.0 / (1.0 + resistance), 6), "steps": steps, "mainObstacle": main, "label": "LOW" if resistance < .25 else "MEDIUM" if resistance < .5 else "HIGH", "formula": "PathResistance = mean(obstacle bins); PathEase = 1/(1+PathResistance)", "provenance": PROVENANCE["derived"]}


def volatility_touch_probability(target: float, current_price: float, sigma_per_sqrt_minute: float | None, horizon_minutes: int) -> dict[str, Any]:
    """FORMÜL:
    a = |ln(L_i / P_t)|
    P(tau_a <= H) ~= 2 * [1 - Phi(a / (sigma * sqrt(H)))]

    NE HESAPLIYOR?
    Eğitimli historical model yokken transparent volatility-only touch baseline'i verir.

    DEĞİŞKENLER:
    a = log-price distance; L_i = target; P_t = current price;
    sigma = realized volatility per square-root minute; H = horizon minutes;
    Phi = standard normal CDF; tau_a = first touch time.

    ÇIKTI NASIL YORUMLANIR?
    Bu production/calibrated probability değildir; UI'da VOLATILITY BASELINE ·
    NOT CALIBRATED olarak gösterilmelidir.

    ÖRNEK:
    sigma ve H arttıkça aynı target distance için baseline touch likelihood artar.
    """
    if not target or not current_price or not sigma_per_sqrt_minute or sigma_per_sqrt_minute <= 0:
        return {"value": None, "status": "MODEL UNAVAILABLE", "provenance": PROVENANCE["model"]}
    distance = abs(math.log(float(target) / float(current_price)))
    denominator = float(sigma_per_sqrt_minute) * math.sqrt(float(horizon_minutes))
    cdf = 0.5 * (1.0 + math.erf((distance / denominator) / math.sqrt(2.0)))
    value = max(0.0, min(1.0, 2.0 * (1.0 - cdf)))
    return {"value": _round(value, 6), "status": "VOLATILITY BASELINE · NOT CALIBRATED", "provenance": PROVENANCE["model"], "calibrated": False, "distanceLog": _round(distance, 8), "sigmaPerSqrtMinute": _round(sigma_per_sqrt_minute, 8), "horizonMinutes": horizon_minutes}


def hazard_curve(touch_probabilities: Mapping[int, float | None]) -> dict[str, Any]:
    """FORMÜL:
    h_k = P(tau=k | tau>=k); S(H) = product(k=1..H)(1-h_k); F(H)=1-S(H)

    NE HESAPLIYOR?
    Touch CDF'yi discrete survival/hazard gösterimine çevirir.

    DEĞİŞKENLER:
    h_k = bucket hazard; S(H) = horizon sonuna kadar untouched survival;
    F(H) = cumulative touch probability.

    ÇIKTI NASIL YORUMLANIR?
    Her ileri horizon için F monotonic olmalı; monotonluk bozulursa model bug'ıdır.

    ÖRNEK:
    F(5m)=0.10 ve F(15m)=0.20 ise 15m touch, 5m'den küçük olamaz.
    """
    last = 0.0
    survival = 1.0
    hazards: dict[str, float | None] = {}
    survival_out: dict[str, float | None] = {}
    cdf: dict[str, float | None] = {}
    for horizon in HORIZONS_MINUTES:
        value = _num(touch_probabilities.get(horizon))
        if value is None:
            hazards[str(horizon)], survival_out[str(horizon)], cdf[str(horizon)] = None, None, None
            continue
        value = max(last, min(1.0, value))
        increment = value - last
        hazard = increment / max(1.0 - last, EPSILON)
        survival = max(0.0, 1.0 - value)
        hazards[str(horizon)], survival_out[str(horizon)], cdf[str(horizon)] = _round(hazard), _round(survival), _round(value)
        last = value
    return {"hazard": hazards, "survival": survival_out, "touch": cdf, "monotonic": True, "status": "DERIVED", "provenance": PROVENANCE["derived"]}


def expected_touch_time(touch_probabilities: Mapping[int, float | None]) -> dict[str, Any]:
    """FORMÜL:
    E[tau] ~= sum_k S(k) * Delta_t
    t_50 = min{t : F(t) >= 0.5}, where F(t)=1-S(t)

    NE HESAPLIYOR?
    İlk touch için expected ve median zamanı hesaplar.

    DEĞİŞKENLER:
    S(k) = k bucket sonuna kadar untouched survival; Delta_t = bucket süresi;
    E[tau] = expected first-touch time; F(t) = cumulative touch CDF.

    ÇIKTI NASIL YORUMLANIR?
    CDF 50%'ye ulaşmıyorsa median unresolved gösterilir.

    ÖRNEK:
    Touch curve 1h'de 0.42'de kalıyorsa Median touch unresolved'dır.
    """
    curve = hazard_curve(touch_probabilities)
    known = [(h, curve["survival"].get(str(h))) for h in HORIZONS_MINUTES if curve["survival"].get(str(h)) is not None]
    if not known:
        return {"expectedMinutes": None, "medianMinutes": None, "status": "MODEL UNAVAILABLE", "provenance": PROVENANCE["model"]}
    expected = 0.0
    previous_horizon = 0
    for horizon, survival in known:
        expected += float(survival) * (horizon - previous_horizon)
        previous_horizon = horizon
    median = next((h for h in HORIZONS_MINUTES if (curve["touch"].get(str(h)) or 0) >= 0.5), None)
    return {"expectedMinutes": _round(expected, 2), "medianMinutes": median, "status": "DERIVED · BASELINE CURVE", "provenance": PROVENANCE["derived"], "medianResolved": median is not None}


def competing_risks(targets: Sequence[Mapping[str, Any]], horizon_minutes: int = 60) -> dict[str, float | None]:
    """FORMÜL:
    CIF_i(t) = integral_0^t S(u-) * h_i(u) du

    NE HESAPLIYOR?
    Aynı anda yarışan targetlar içinde target i'nin önce touch edilme cumulative
    incidence'ını hesaplar.

    DEĞİŞKENLER:
    h_i(u) = target i cause-specific hazard; S(u-) = hiçbir target henüz
    touch edilmemiş survival; CIF_i(t) = target i first-hit probability.

    ÇIKTI NASIL YORUMLANIR?
    NEXT TARGET PROBABILITY, tek target'ın touch probability'sinden farklıdır.

    ÖRNEK:
    İki target aynı anda yarışırken her biri toplam survival'dan pay alır;
    first-hit olasılıklarının toplamı, en fazla herhangi bir target touch olasılığıdır.
    """
    hazards: dict[str, float] = {}
    for index, target in enumerate(targets):
        probability = _num((target.get("touchProbability") or {}).get(str(horizon_minutes), (target.get("touchProbability") or {}).get(horizon_minutes)))
        if probability is not None and probability > 0:
            hazards[str(target.get("id", index))] = -math.log(max(1.0 - min(probability, 0.999999), EPSILON)) / max(1, horizon_minutes)
    total = sum(hazards.values())
    if total <= 0:
        return {str(target.get("id", index)): None for index, target in enumerate(targets)}
    overall = 1.0 - math.exp(-total * horizon_minutes)
    return {str(target.get("id", index)): _round(overall * hazards.get(str(target.get("id", index)), 0.0) / total) for index, target in enumerate(targets)}


def confidence_from_calibration(ece: float | None, sample_size: int | None, shrinkage_k: int, distribution_shift: float | None, missing_weight: float, total_weight: float) -> dict[str, Any]:
    """FORMÜL:
    C_cal = 1 - ECE; C_n = n / (n+k); C_shift = exp(-lambda * D_shift)
    C_data = 1 - MissingWeightedFeatures / TotalWeightedFeatures
    Confidence = 100 * (C_cal * C_n * C_shift * C_data)^(1/4)

    NE HESAPLIYOR?
    Probability değil, tahmin güvenilirliği için calibration, sample size,
    distribution shift ve input completeness'i birleştirir.

    DEĞİŞKENLER:
    ECE = expected calibration error; n = similar historical samples;
    k = shrinkage constant; D_shift = training/current distribution distance;
    MissingWeightedFeatures = eksik feature ağırlığı; TotalWeightedFeatures = total;
    lambda = shift decay coefficient; C_cal/C_n/C_shift/C_data = components.

    ÇIKTI NASIL YORUMLANIR?
    Confidence, olayın probability'si değildir; modelin ne kadar güvenilir
    uygulanabildiğini belirtir.

    ÖRNEK:
    ECE yükselir veya n düşerse confidence düşer; probability değişmez.
    """
    if ece is None or sample_size is None or total_weight <= 0:
        return {"value": None, "status": "MODEL UNAVAILABLE", "components": {"calibration": None, "sample": None, "shift": None, "data": None}}
    cal = max(0.0, min(1.0, 1.0 - float(ece)))
    n_component = max(0.0, min(1.0, float(sample_size) / max(1.0, float(sample_size) + float(shrinkage_k))))
    shift_component = math.exp(-max(0.0, float(distribution_shift or 0.0)))
    data_component = max(0.0, min(1.0, 1.0 - float(missing_weight) / float(total_weight)))
    value = 100.0 * max(0.0, cal * n_component * shift_component * data_component) ** 0.25
    return {"value": _round(value, 2), "status": "CALIBRATED", "components": {"calibration": _round(cal, 6), "sample": _round(n_component, 6), "shift": _round(shift_component, 6), "data": _round(data_component, 6)}, "provenance": PROVENANCE["model"]}


def liquidity_gravity(density: float | None, touch_probability: float | None, flow_alignment: float | None, cascade_potential: float | None, distance_atr: float | None, distance_decay: float | None) -> dict[str, Any]:
    """FORMÜL:
    LG_i = D_i * P_i * F_i * C_i * exp(-k * d_i^ATR)
    GravityScore_i = 100 * ECDF(LG_i)

    NE HESAPLIYOR?
    Cluster büyüklüğü, reachability, flow alignment, cascade potential ve
    distance'ı tek bir ranking metric'inde birleştirir.

    DEĞİŞKENLER:
    D_i = normalized liquidation density; P_i = touch probability;
    F_i = target yönündeki derived flow alignment; C_i = cascade potential;
    d_i^ATR = ATR-normalized distance; k = validated distance decay coefficient;
    LG_i = raw gravity; ECDF = candidate-set empirical CDF.

    ÇIKTI NASIL YORUMLANIR?
    GravityScore ranking score'dur, probability değildir. Herhangi bir model
    input'u yoksa raw gravity ve score bilinçli olarak UNAVAILABLE kalır.

    ÖRNEK:
    D=.84, P=.56, F=.68, C=.73, d=1.4 ve learned k varsa LG hesaplanabilir;
    k hard-code edilmez.
    """
    components = (density, touch_probability, flow_alignment, cascade_potential, distance_atr, distance_decay)
    if any(x is None for x in components):
        return {"raw": None, "score": None, "status": "MODEL UNAVAILABLE", "rankingLabel": "Ranking score — not probability", "provenance": PROVENANCE["model"]}
    raw = float(density) * float(touch_probability) * float(flow_alignment) * float(cascade_potential) * math.exp(-float(distance_decay) * float(distance_atr))
    return {"raw": _round(raw, 8), "score": None, "status": "ESTIMATED · MODEL", "rankingLabel": "Ranking score — not probability", "provenance": PROVENANCE["model"], "formula": "LG=D×P×F×C×exp(-k×d_ATR)"}


def trigger_for_path(current_price: float, target: float, path: Mapping[str, Any]) -> dict[str, Any]:
    """FORMÜL:
    P_trigger = P(target/cascade activation | X_t), activated only when the
    structural price condition is true.

    NE HESAPLIYOR?
    Trigger'ı yalnızca seviyeye dokunma değil, conditional activation condition
    olarak tanımlar.

    DEĞİŞKENLER:
    P_trigger = trained conditional activation probability; X_t = current state;
    structural condition = acceptance beyond the first material obstacle.

    ÇIKTI NASIL YORUMLANIR?
    V2 model/threshold yoksa probability ve threshold UNAVAILABLE kalır.

    ÖRNEK:
    Aşağı target için trigger, ilk obstacle altında acceptance olarak gösterilir;
    price halen üstündeyse active=false.
    """
    obstacle = path.get("mainObstacle") if isinstance(path, Mapping) else None
    trigger_price = _num(obstacle.get("price")) if isinstance(obstacle, Mapping) else None
    direction = "DOWN" if target < current_price else "UP"
    active = None if trigger_price is None else (current_price < trigger_price if direction == "DOWN" else current_price > trigger_price)
    condition = None
    if trigger_price is not None:
        condition = f"acceptance {'below' if direction == 'DOWN' else 'above'} {trigger_price:g}"
    return {"price": trigger_price, "direction": direction, "condition": condition, "active": active, "probability": None, "threshold": None, "status": "MODEL UNAVAILABLE", "reason": "V2 trigger calibration/threshold artifact is not installed", "provenance": PROVENANCE["model"]}


def _trade_flow(trades: Sequence[Any]) -> tuple[dict[str, Any], list[float]]:
    buys, sells = 0.0, 0.0
    aggression_history: list[float] = []
    for trade in trades:
        side = str(getattr(trade, "aggressor_side", "") or "").upper()
        volume = _num(getattr(trade, "quantity_btc", None), 0.0) or 0.0
        if side == "BUY":
            buys += volume
        elif side == "SELL":
            sells += volume
    return aggression(buys, sells), aggression_history


def _realized_sigma(trades: Sequence[Any]) -> float | None:
    prices = [(_num(getattr(x, "price", None)), _num(getattr(x, "timestamp", None))) for x in trades]
    returns: list[float] = []
    for (previous, _), (current, _) in zip(prices, prices[1:]):
        if previous and current and previous > 0 and current > 0:
            returns.append(math.log(current / previous))
    if len(returns) < 20:
        return None
    # Trades are irregular. The engine labels this as a short-window diagnostic
    # realized sigma; a production artifact should use timestamp-bucketed bars.
    return statistics.pstdev(returns) / math.sqrt(max(1.0, len(returns) / 60.0))


def _model_health(artifact: Mapping[str, Any] | None, *, stale: bool, feature_count: int, missing_count: int) -> dict[str, Any]:
    is_v2 = bool(artifact and str(artifact.get("engine_version", "")).lower() == "v2" and artifact.get("status") == "CALIBRATED")
    if stale:
        status = "MODEL OUTPUT SUPPRESSED"
    elif not artifact:
        status = "MODEL UNAVAILABLE"
    elif not is_v2:
        status = "UNCALIBRATED"
    else:
        status = "CALIBRATED"
    return {"status": status, "engineVersion": "v2", "trainingSamples": artifact.get("training_samples") if is_v2 else None, "currentRegimeSamples": artifact.get("current_regime_samples") if is_v2 else None, "metrics": artifact.get("metrics", {}) if is_v2 else {}, "calibration": artifact.get("calibration", {}) if is_v2 else {}, "missingFeatures": missing_count, "totalFeatures": feature_count, "noFabricatedProbability": True, "provenance": PROVENANCE["model"]}


def build_v2_decision(*, current_price: float, atr: float | None, candidates: Sequence[Mapping[str, Any]], liquidity_levels: Sequence[Mapping[str, Any]], liquidation_zones: Sequence[Mapping[str, Any]], profile: Mapping[str, Any] | None, book: Mapping[str, Any], trades: Sequence[Any], now_ms: int, input_timestamps: Mapping[str, int | None] | None = None, model_artifact: Mapping[str, Any] | None = None) -> dict[str, Any]:
    """Build the additive Hova V2 contract from current raw/derived inputs."""
    input_timestamps = input_timestamps or {}
    flow, _ = _trade_flow(trades[-2000:])
    aggression_history = []
    sigma = _realized_sigma(trades[-2000:])
    book_metric = weighted_orderbook_imbalance(book, current_price)
    stale_inputs = []
    stale_limits = {"Trades": 30, "Orderbook": 10, "OI": 180, "Funding": 7200, "ForceOrder": 60, "Liquidation model": 900}
    health: list[dict[str, Any]] = []
    for name, timestamp in input_timestamps.items():
        age = None if timestamp is None else max(0.0, (now_ms - int(timestamp)) / 1000.0)
        limit = stale_limits.get(name, 300)
        status = "UNAVAILABLE" if timestamp is None else "STALE" if age is not None and age > limit else "REAL"
        if status == "STALE":
            stale_inputs.append(name)
        health.append({"source": name, "status": status, "age_seconds": _round(age, 2), "stale_after_seconds": limit, "provenance": PROVENANCE["observed"] if status != "UNAVAILABLE" else None, "reason": "critical input stale" if status == "STALE" else None})
    stale = bool(stale_inputs)
    model_health = _model_health(model_artifact, stale=stale, feature_count=7, missing_count=sum(x["status"] == "UNAVAILABLE" for x in health))
    density_values = [float(_num(x.get("estimated_notional", x.get("displayed_liquidity", 0.0)), 0.0) or 0.0) for x in list(liquidation_zones) + list(liquidity_levels)]
    targets: list[dict[str, Any]] = []
    for index, raw in enumerate(candidates):
        center = _num(raw.get("targetCenter"))
        if center is None:
            continue
        dist = price_distance(center, current_price)
        dist_atr = atr_normalized_distance(center, current_price, atr)
        q = _num(raw.get("estimatedNotional"), 0.0) or 0.0
        density = robust_density(density_values, q)
        path = path_analysis(current_price, center, liquidity_levels, profile, directional_aggression=flow.get("value"), atr=atr)
        touch: dict[str, Any] = {}
        for horizon in HORIZONS_MINUTES:
            baseline = volatility_touch_probability(center, current_price, sigma, horizon)
            if stale:
                baseline = {**baseline, "value": None, "status": "MODEL OUTPUT SUPPRESSED"}
            touch[str(horizon)] = baseline.get("value")
        curve = hazard_curve({h: touch.get(str(h)) for h in HORIZONS_MINUTES})
        eta = expected_touch_time({h: touch.get(str(h)) for h in HORIZONS_MINUTES})
        flow_alignment = None
        if flow.get("value") is not None:
            direction = 1 if center > current_price else -1
            flow_alignment = _clip((1.0 + direction * float(flow["value"])) / 2.0)
        cascade = None if not model_artifact or model_health["status"] != "CALIBRATED" else _num(raw.get("cascadeProbability"))
        gravity = liquidity_gravity(density.get("score"), touch.get("60"), flow_alignment, cascade, dist_atr.get("value"), _num((model_artifact or {}).get("distance_decay")))
        target = {
            "id": str(raw.get("id", f"target-{index + 1}")), "price": round(center, 2), "targetCenter": round(center, 2), "targetLow": raw.get("targetLow", center), "targetHigh": raw.get("targetHigh", center), "side": "UP" if center > current_price else "DOWN", "targetType": raw.get("types", []),
            "distance": dist, "distanceATR": dist_atr, "rawExposure": q, "density": density, "touchProbability": touch, "touchProbabilityStatus": "MODEL OUTPUT SUPPRESSED" if stale else "VOLATILITY BASELINE · NOT CALIBRATED", "hazard": curve, "expectedTouchTime": eta, "nextTargetProbability": None, "flowAlignment": {"value": _round(flow_alignment), "status": "DERIVED" if flow_alignment is not None else "UNAVAILABLE", "provenance": PROVENANCE["derived"]}, "cascadePotential": {"value": cascade, "status": "ESTIMATED · MODEL" if cascade is not None else "MODEL UNAVAILABLE", "provenance": PROVENANCE["model"]}, "liquidityGravity": gravity, "path": path, "trigger": trigger_for_path(current_price, center, path), "mainObstacle": path.get("mainObstacle"), "confidence": None if model_health["status"] != "CALIBRATED" else None,
            "provenance": {"price": PROVENANCE["observed"], "distance": PROVENANCE["derived"], "density": PROVENANCE["derived"], "touchProbability": PROVENANCE["model"], "nextTargetProbability": PROVENANCE["model"]},
            "why": [f"Distance {dist.get('percent')}%", f"ATR distance {dist_atr.get('value')}" if dist_atr.get("value") is not None else "ATR unavailable", "Raw exposure is estimated from candidate inputs"], "against": ([f"Main obstacle: {path['mainObstacle']['type']} at {path['mainObstacle']['price']:g}"] if path.get("mainObstacle") else ["No obstacle evidence in path"]), "missing": ["V2 calibrated touch model", "cascade transition dataset", "trigger threshold calibration"],
        }
        targets.append(target)
    # A competing-risk output is itself a probability model output.  Do not
    # expose even a baseline first-hit percentage unless a V2 calibration
    # artifact is installed and the inputs are fresh.
    first_hit = competing_risks(targets, 60) if model_health["status"] == "CALIBRATED" and not stale else {}
    for target in targets:
        target["nextTargetProbability"] = first_hit.get(target["id"])
        target["provenance"]["nextTargetProbability"] = PROVENANCE["model"]
    ranked_density = sorted(targets, key=lambda x: x["rawExposure"], reverse=True)
    ranked_next = sorted(targets, key=lambda x: (x["nextTargetProbability"] is not None, x["nextTargetProbability"] or -1), reverse=True)
    for rank, item in enumerate(ranked_density, 1):
        item["rankDensity"] = rank
    for rank, item in enumerate(ranked_next, 1):
        item["rankNext"] = rank
    largest = ranked_density[0] if ranked_density else None
    primary = ranked_next[0] if ranked_next and ranked_next[0].get("nextTargetProbability") is not None else None
    direction = {"pUpFirst": None, "pDownFirst": None, "status": "MODEL UNAVAILABLE", "provenance": PROVENANCE["model"]}
    if primary:
        up = sum(x.get("nextTargetProbability") or 0 for x in targets if x["side"] == "UP")
        down = sum(x.get("nextTargetProbability") or 0 for x in targets if x["side"] == "DOWN")
        direction = {"pUpFirst": _round(up), "pDownFirst": _round(down), "status": "VOLATILITY BASELINE · NOT CALIBRATED", "provenance": PROVENANCE["model"]}
    return {
        "marketState": {"currentPrice": current_price, "atr": atr, "sigmaPerSqrtMinute": sigma, "regime": "UNKNOWN", "flow": flow, "orderBookImbalance": book_metric, "provenance": {"currentPrice": PROVENANCE["observed"], "atr": PROVENANCE["derived"], "flow": PROVENANCE["derived"], "orderBookImbalance": PROVENANCE["derived"]}},
        "direction": direction, "primaryTarget": primary, "targets": targets, "path": primary.get("path", {}).get("steps", []) if primary else [], "alternativeTargets": [x for x in targets if not primary or x["id"] != primary["id"]][:5], "largestPool": largest, "dataHealth": health, "modelHealth": model_health, "status": "MODEL OUTPUT SUPPRESSED" if stale else "PASS" if current_price else "UNAVAILABLE", "summary": "MODEL OUTPUT SUPPRESSED" if stale else "V2 calibrated target model unavailable; volatility baseline values are not calibrated." if not primary else f"Most probable baseline next target: {primary['price']:,.2f} {'above' if primary['side'] == 'UP' else 'below'} price.", "rules": {"probabilityIsCalibratedOnlyWithV2Artifact": True, "scoreIsProbability": False, "estimatedAndObservedSeparated": True, "horizonsMinutes": list(HORIZONS_MINUTES)}, "formulas": {"priceDistance": "d=(L-P)/P", "atrDistance": "|L-P|/ATR", "density": "robust z-score on log(1+Q), then sigmoid", "touch": "volatility-only baseline unless V2 calibrated artifact exists", "nextTarget": "competing-risks CIF", "gravity": "LG=D×P×F×C×exp(-k×d_ATR)"},
    }
