"""NCE Edge Finder - shared logic (PREREG_EDGE.md). Public market data only."""
import numpy as np, pandas as pd, json, math
from scipy.stats import binom

COMBOS = {
 "PDH_SFP_SHORT": ("PDH süpürülüb altında bağlandı → 4s sonra aşağı", 4, -1),
 "PDL_SFP_LONG": ("PDL süpürülüb üstündə bağlandı → 4s sonra yuxarı", 4, 1),
 "SPOT_LEADS_UP": ("Spot CVD alır, futures satır (4s) → 4s sonra yuxarı", 4, 1),
 "SPOT_LEADS_DOWN": ("Spot CVD satır, futures alır (4s) → 4s sonra aşağı", 4, -1),
 "OI_UP_PRICE_DOWN": ("OI ≥+2%, qiymət ≤−1% (yeni shortlar) → 4s sonra aşağı", 4, -1),
 "LONG_FLUSH": ("OI ≤−3%, qiymət ≤−1.5% (long flush) → 4s sonra yuxarı", 4, 1),
 "FUNDING_HOT": ("Funding ≥0.03% → 8s sonra aşağı", 8, -1),
 "FUNDING_NEG": ("Funding ≤−0.01% → 8s sonra yuxarı", 8, 1),
 "CROWDED_LONG": ("Top trader L/S 30 günün 90%-dən yuxarı → 4s sonra aşağı", 4, -1),
 "TAKER_SELL_EXHAUST": ("Taker alış/satış ≤0.7 (1s) → 1s sonra yuxarı", 1, 1),
 "VA_REENTRY_POC": ("Value Area-ya geri giriş → 8s içində POC-a toxunur", 8, 0),
 "PDH_MAGNET": ("Qiymət PDH-a 0.3% yaxın → 4s içində PDH-a toxunur", 4, 0),
 "BOOK_BID_HEAVY": ("Book imbalance ≥0.30 → 15 dəq sonra yuxarı", 15, 1),
 "BOOK_ASK_HEAVY": ("Book imbalance ≤−0.30 → 15 dəq sonra aşağı", 15, -1),
 "SFP_PLUS_CROWD": ("PDH SFP + funding >0.01% + OI artır → 4s sonra aşağı", 4, -1),
}
BONF = 0.05/15

def wilson(k, n, z=1.96):
    if n == 0: return (0.0, 0.0)
    p = k/n; d = 1+z*z/n; c = p+z*z/(2*n); r = z*math.sqrt(p*(1-p)/n+z*z/(4*n*n))
    return ((c-r)/d, (c+r)/d)

def value_area(day):
    lo, hi = day.l.min(), day.h.max(); w = day.c.iloc[0]*0.001
    nb = max(1, int(math.ceil((hi-lo)/w))); vol = np.zeros(nb); edges = lo+np.arange(nb+1)*w
    for l, h, v in zip(day.l, day.h, day.v):
        a = int((l-lo)//w); b = min(nb-1, int((h-lo)//w)); vol[a:b+1] += v/(b-a+1)
    p = int(vol.argmax()); s = vol[p]; a = b = p; tot = vol.sum()
    while s < 0.7*tot:
        up = vol[b+1] if b+1 < nb else -1; dn = vol[a-1] if a > 0 else -1
        if up >= dn: b += 1; s += up
        else: a -= 1; s += dn
    return edges[p]+w/2, edges[a], edges[b+1]

def features(H):
    """H: hourly frame indexed by open time UTC with o,h,l,c,v,tbv (futures), sv,stbv (spot), oi, tls, fund."""
    F = pd.DataFrame(index=H.index)
    d = H.index.floor("D"); dh = H.h.groupby(d).max().shift(1); dl = H.l.groupby(d).min().shift(1)
    F["pdh"] = dh.reindex(d).values; F["pdl"] = dl.reindex(d).values
    fd = (2*H.tbv-H.v).rolling(4).sum(); sd = (2*H.stbv-H.sv).rolling(4).sum()
    fv = H.v.rolling(4).sum(); sv = H.sv.rolling(4).sum()
    oi4 = H.oi.pct_change(4, fill_method=None); p4 = H.c.pct_change(4)
    q90 = H.tls.rolling(720, min_periods=500).quantile(0.9)
    tr = H.tbv/(H.v-H.tbv)
    va = {}
    for day, g in H.groupby(d):
        if len(g) >= 20: va[day+pd.Timedelta("1D")] = value_area(g)
    vadf = pd.DataFrame(va, index=["poc","val","vah"]).T.reindex(d)
    F["poc"], F["val"], F["vah"] = vadf.poc.values, vadf.val.values, vadf.vah.values
    E = pd.DataFrame(index=H.index)
    E["PDH_SFP_SHORT"] = (H.h > F.pdh) & (H.c < F.pdh)
    E["PDL_SFP_LONG"] = (H.l < F.pdl) & (H.c > F.pdl)
    E["SPOT_LEADS_UP"] = (sd >= 0.05*sv) & (fd <= -0.05*fv)
    E["SPOT_LEADS_DOWN"] = (sd <= -0.05*sv) & (fd >= 0.05*fv)
    E["OI_UP_PRICE_DOWN"] = (oi4 >= 0.02) & (p4 <= -0.01)
    E["LONG_FLUSH"] = (oi4 <= -0.03) & (p4 <= -0.015)
    E["FUNDING_HOT"] = H.fund >= 0.0003
    E["FUNDING_NEG"] = H.fund <= -0.0001
    E["CROWDED_LONG"] = H.tls > q90
    E["TAKER_SELL_EXHAUST"] = tr <= 0.7
    inside = lambda x: (x >= F.val) & (x <= F.vah)
    E["VA_REENTRY_POC"] = (~inside(H.o)) & inside(H.c)
    E["PDH_MAGNET"] = (H.c < F.pdh) & (H.c >= F.pdh*0.997) & (H.h < F.pdh)
    E["SFP_PLUS_CROWD"] = E.PDH_SFP_SHORT & (H.fund > 0.0001) & (oi4 > 0)
    E = E.fillna(False).astype(bool)
    # outcomes
    O = {}
    for k in (1, 4, 8): O[f"r{k}"] = H.c.shift(-k)/H.c-1
    fh = lambda n: H.h[::-1].rolling(n, min_periods=n).max()[::-1].shift(-1)
    fl = lambda n: H.l[::-1].rolling(n, min_periods=n).min()[::-1].shift(-1)
    h8, l8, h4, l4 = fh(8), fl(8), fh(4), fl(4)
    O["poc8"] = ((h8 >= F.poc) & (l8 <= F.poc)).where(h8.notna())
    O["pdh4"] = (h4 >= F.pdh).where(h4.notna())
    O["sym4"] = (l4 <= H.c-(F.pdh-H.c)).where(l4.notna())
    return E, pd.DataFrame(O, index=H.index)

def decluster(mask, horizon):
    idx = np.flatnonzero(mask.values); keep = []; last = -10**9
    for i in idx:
        if i-last >= horizon: keep.append(i); last = i
    return mask.index[keep]

def score(name, E, O, period):
    lab, hz, dr = COMBOS[name]
    sel = (E.index >= period[0]) & (E.index < period[1])
    Ep, Op = E[sel], O[sel]
    t = decluster(Ep[name], hz)
    if name == "VA_REENTRY_POC":
        y = Op.loc[t, "poc8"].dropna(); base = Op["poc8"].dropna().mean(); net = None
    elif name == "PDH_MAGNET":
        y = Op.loc[t, "pdh4"].dropna(); base = Op.loc[t, "sym4"].dropna().mean() if len(t) else float("nan"); net = None
    else:
        r = Op[f"r{hz}"].dropna(); yr = (dr*Op.loc[t, f"r{hz}"]).dropna()
        y = yr > 0; base = (dr*r > 0).mean(); net = float(yr.mean()*1e4-10) if len(yr) else None
    return pack(name, y.astype(float), base, net)

def pack(name, y, base, net):
    n = int(len(y)); k = int(y.sum()); lo, hi = wilson(k, n)
    p = float(binom.sf(k-1, n, base)) if n and base == base else 1.0
    pct = k/n if n else None
    if n < 20: v = "UNRELIABLE"
    elif lo > base and p < BONF: v = "EDGE"
    else: v = "NO EDGE"
    return dict(combo=name, label=COMBOS[name][0], n=n, hits=k, pct=pct, base=float(base) if base == base else None,
                lift=(pct/base if pct is not None and base else None), ci_lo=lo, ci_hi=hi, p=p, net_bp=net, verdict=v)

def book_eval(B, period):
    B = B[(B.index >= period[0]) & (B.index < period[1])]
    r = B.mid.shift(-15)/B.mid-1; out = []
    for name, cond, dr in (("BOOK_BID_HEAVY", B.imb >= 0.30, 1), ("BOOK_ASK_HEAVY", B.imb <= -0.30, -1)):
        t = decluster(cond.fillna(False), 15); yr = (dr*r.loc[t]).dropna(); rr = r.dropna()
        out.append(pack(name, (yr > 0).astype(float), float((dr*rr > 0).mean()), float(yr.mean()*1e4-10) if len(yr) else None))
    return out
