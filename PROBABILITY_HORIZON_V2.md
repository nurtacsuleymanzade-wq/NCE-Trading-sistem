# Hermes BTCUSDT Multi-Horizon Probability V2

## Contract

The engine answers three different questions independently:

| Forecast | 1s trigger | 1m setup | 5m context |
| --- | ---: | ---: | ---: |
| 5 minutes | 50% | 35% | 15% |
| 10 minutes | 30% | 42% | 28% |
| 30 minutes | 10% | 35% | 55% |

The 1-second state can therefore move the five-minute forecast quickly, but it
cannot blindly rewrite the thirty-minute regime. Every prediction freezes its
own zone, feature hash, probabilities, model version, creation time, and due
time.

## Inputs

| Input | Source | Use |
| --- | --- | --- |
| Aggressor side, quote volume, trade rate | Binance Futures `aggTrade` | aggression delta, intensity, CVD context |
| Bid/ask depth | Binance Futures depth | top-20 imbalance, microprice |
| 1-minute closed candles | Binance Futures kline | return, volatility and 1m/5m context |
| Open interest history | Binance Futures Data | OI change and price/OI agreement |
| Funding | Binance Futures premium index | crowding context; low contrarian weight |

Only information available at prediction time enters the feature state.

## Calculation

Each timeframe produces a bounded directional signal from transformed feature
values. For horizon `h`:

`S_h = w_1s,h × S_1s + w_1m,h × S_1m + w_5m,h × S_5m`

The first output is a normalized `UP / RANGE / DOWN` model distribution. This
is labelled `MODEL_ESTIMATE`, not `CALIBRATED`. A horizon-specific empirical
mapping may replace it only when its signal bucket contains at least 100
purged outcomes. Evidence score and data quality are never probabilities.

Zone width comes from recent one-minute realized volatility scaled by the
square root of the requested horizon. It is a price interval, not a one-tick
target and not a guarantee.

## Observer

`horizon_predictions` is append-only and stores frozen forecasts. When the due
time arrives, `horizon_outcomes` records the first observed Futures trade at or
after that time and assigns `UP`, `RANGE`, or `DOWN` using the frozen neutral
zone.

Reported metrics are calculated separately for 5, 10 and 30 minutes:

- multiclass Brier score;
- log loss;
- expected calibration error;
- directional accuracy;
- raw and purged sample counts.

For metrics and later calibration, observations are purged with an embargo
equal to the forecast horizon. Second-by-second storage therefore does not
turn overlapping future paths into fake independent evidence.

## Deployment truth

The GitHub Pages client can calculate a live fallback from public Binance data.
Persistent cross-device Observer history and `CALIBRATED` status require the
updated Python collector/API process to be deployed with its SQLite database.
The UI explicitly reports whether it is using `PYTHON_COLLECTOR` or
`DIRECT_BINANCE_FALLBACK`.

