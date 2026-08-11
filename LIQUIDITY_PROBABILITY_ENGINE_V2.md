# NCE Liquidity Probability & Targeting Engine V2

## Implementation status

The V2 engine is implemented additively in
[`capital_flow/liquidity_probability_v2.py`](capital_flow/liquidity_probability_v2.py).
The existing V1 API fields remain for backwards compatibility, while
`/api/v1/probability-map/summary` now also returns a complete `v2` decision
contract and Hova-facing fields.

The repository does not currently contain a versioned V2 walk-forward model
artifact (`historical/calibration/liquidity_probability_v2.json`). Therefore
V2 does not claim calibrated production probabilities, cascade probabilities,
trigger thresholds, gravity rankings, or confidence. It returns explicit
`MODEL UNAVAILABLE`, `UNCALIBRATED`, or `MODEL OUTPUT SUPPRESSED` states and a
transparent `VOLATILITY BASELINE · NOT CALIBRATED` touch curve when the data is
fresh enough to calculate it.

## Provenance contract

| Class | Examples | UI label |
| --- | --- | --- |
| Observed / real | mark/last trade, bid/ask, depth, aggTrades, OI, funding, forceOrder | `OBSERVED · REAL` |
| Derived | distance, ATR distance, density z-score, book imbalance, aggression, absorption, replenishment, path | `DERIVED` |
| Estimated / model | touch, next target, cascade, trigger probability, ETTT, gravity, confidence | `ESTIMATED · MODEL` |

Observed fields are never substituted with neutral values when unavailable.
Critical stale inputs suppress model output.

## Mathematical models

Each formula is documented in code using the required `FORMÜL / NE HESAPLIYOR?
/ DEĞİŞKENLER / ÇIKTI NASIL YORUMLANIR? / ÖRNEK` format.

Implemented deterministic layers:

1. signed price distance and ATR-normalized distance;
2. robust log/MAD liquidation density normalization;
3. distance-decayed first-K order-book imbalance;
4. aggressive buy/sell aggression and robust z-score;
5. absorption and replenishment diagnostics;
6. path bins with explicit obstacle components and no hand-tuned component weights;
7. volatility-only touch baseline, discrete hazard/survival, expected and median touch time;
8. competing-risk first-target CIF;
9. confidence decomposition, gated on V2 calibration;
10. Liquidity Gravity formula and ECDF ranking, gated until all model inputs exist;
11. structural trigger condition with unavailable conditional probability until trigger calibration exists.

The volatility baseline is not the production model. It is intentionally labeled
and is not used to claim calibration or confidence.

## API contract

The summary endpoint returns the legacy fields plus:

```json
{
  "marketState": {},
  "direction": {"pUpFirst": null, "pDownFirst": null},
  "primaryTarget": {},
  "path": [],
  "alternativeTargets": [],
  "largestPool": {},
  "dataHealth": [],
  "modelHealth": {}
}
```

The same object is nested under `v2` for clients migrating from the previous
contract. `nextTargetProbability` is distinct from `touchProbability`; the
largest raw exposure is distinct from the most reachable target.

## Calibration and training gate

Only an artifact with `engine_version: "v2"` and `status: "CALIBRATED"` is
accepted by the runtime. The artifact must contain chronological walk-forward
training/validation/test metadata, horizon-level Brier/ECE/Log Loss/ROC-AUC/
PR-AUC, regime cells, bootstrap intervals, trigger threshold validation, and
cascade transition samples. The V1 `target_probability.json` is deliberately
not accepted as a V2 model.

When the artifact is absent or insufficient:

- no hard-coded percentage is emitted;
- next-target/cascade/trigger/gravity/confidence model fields are `null`;
- the UI explains why the result is unavailable;
- the baseline touch curve is explicitly not calibrated.

## UX acceptance coverage

The decision-first panel now exposes, without interpreting a chart:

- current price, target side, target probability and all requested horizons;
- expected and median touch time;
- structural trigger and whether it is active;
- path steps and strongest obstacle classification;
- cascade state;
- largest pool versus next target;
- density/next/gravity table separation;
- data freshness, provenance and model health;
- Trader View and Research View;
- separate data window and forecast controls.

If a critical input is stale, the top state is `MODEL OUTPUT SUPPRESSED` and
touch probabilities are cleared.

## Validation run

The V2 unit tests cover formula directionality, bounded imbalance, robust
density, competing risks, baseline labeling, stale suppression, and unresolved
median touch. The full existing suite remains green after the additive change.

Remaining work before production-calibrated V2:

1. build the event-level V2 historical dataset with no look-ahead;
2. train discrete hazards and directional/flow models with walk-forward splits;
3. fit Platt or isotonic calibration per horizon/regime;
4. estimate cascade transitions and validated distance decay;
5. run moving-block or stationary bootstrap intervals;
6. install the versioned V2 artifact and re-run the UX scenario matrix.
