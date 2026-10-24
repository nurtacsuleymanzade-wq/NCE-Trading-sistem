"""NCE Edge Finder collector: updates hourly data from public Binance REST, recomputes live state,
tracks forward events/outcomes, writes edge_calibration.json. No keys, no orders."""
import json, os, sys, time, urllib.request, pandas as pd, numpy as np
from edge_lib import *
D = os.path.dirname(os.path.abspath(__file__)); OUT = os.environ.get("EDGE_OUT", D)
FWD_START = pd.Timestamp("2026-10-23")  # forward tracking begins after historical OOS end
def get(u):
    for i in range(3):
        try: return json.load(urllib.request.urlopen(u, timeout=20))
        except Exception as e: err = e; time.sleep(2)
    raise err
def kl(base, path):
    d = pd.DataFrame(get(f"{base}{path}?symbol=BTCUSDT&interval=1h&limit=1000"))
    d.index = pd.to_datetime(d[0], unit="ms"); d = d.iloc[:-1]  # drop open bar
    return d[[1,2,3,4,5,9]].astype(float).set_axis(["o","h","l","c","v","tbv"], axis=1)
def main():
    H = pd.read_parquet(f"{OUT}/hourly.parquet")
    f = kl("https://fapi.binance.com", "/fapi/v1/klines"); s = kl("https://api.binance.com", "/api/v3/klines")
    N = f.copy(); N["sv"] = s.v; N["stbv"] = s.tbv
    oi = pd.DataFrame(get("https://fapi.binance.com/futures/data/openInterestHist?symbol=BTCUSDT&period=1h&limit=500"))
    oi = oi.set_index(pd.to_datetime(oi.timestamp, unit="ms")).sumOpenInterest.astype(float)
    ls = pd.DataFrame(get("https://fapi.binance.com/futures/data/topLongShortAccountRatio?symbol=BTCUSDT&period=1h&limit=500"))
    ls = ls.set_index(pd.to_datetime(ls.timestamp, unit="ms")).longShortRatio.astype(float)
    fr = pd.DataFrame(get("https://fapi.binance.com/fapi/v1/fundingRate?symbol=BTCUSDT&limit=1000"))
    fr = fr.set_index(pd.to_datetime(fr.fundingTime, unit="ms")).fundingRate.astype(float).sort_index()
    close = N.index+pd.Timedelta("1h")
    # REST OI/LS timestamps mark the period start; value at bar close = stamp == close-1h? use the stamp at close if present else nearest earlier
    N["oi"] = oi.reindex(close, method="ffill", tolerance=pd.Timedelta("1h")).values
    N["tls"] = ls.reindex(close, method="ffill", tolerance=pd.Timedelta("1h")).values
    N["fund"] = fr.reindex(close, method="ffill").values
    H = N.combine_first(H) if False else pd.concat([H[~H.index.isin(N.index)], N]).sort_index()
    for c in ("oi","tls"):  # keep archive values where REST lacks them
        pass
    H.to_parquet(f"{OUT}/hourly.parquet")
    E, O = features(H)
    # forward tracking
    rows = []
    fwd = (FWD_START, pd.Timestamp("2100-01-01"))
    for name in COMBOS:
        if name.startswith("BOOK"): continue
        rows.append(score(name, E, O, fwd))
    # live
    last = E.index[-1]
    live = {k: bool(E[k].iloc[-1]) for k in E.columns}
    dep = get("https://fapi.binance.com/fapi/v1/depth?symbol=BTCUSDT&limit=20")
    bq = sum(float(p)*float(q) for p, q in dep["bids"][:10]); aq = sum(float(p)*float(q) for p, q in dep["asks"][:10])
    imb = (bq-aq)/(bq+aq); live["BOOK_BID_HEAVY"] = imb >= 0.30; live["BOOK_ASK_HEAVY"] = imb <= -0.30
    hist = json.load(open(f"{OUT}/calib_hist.json"))
    fmap = {r["combo"]: r for r in rows}
    for r in hist["rows"]:
        r["forward"] = fmap.get(r["combo"]); r["live"] = live.get(r["combo"], False)
    hist["meta"].update(prereg_sha256=open(f"{OUT}/PREREG_EDGE.sha256").read().split()[0],
        built_utc=pd.Timestamp.utcnow().strftime("%Y-%m-%d %H:%M"), last_bar_utc=str(last),
        live_price=float(H.c.iloc[-1]), live_book_imb10=round(imb, 3), forward_start=str(FWD_START.date()))
    tmp = f"{OUT}/edge_calibration.json.tmp"; json.dump(hist, open(tmp, "w"), indent=1, default=float)
    os.replace(tmp, f"{OUT}/edge_calibration.json"); print("ok", last, {k: v for k, v in live.items() if v})
if __name__ == "__main__": main()
