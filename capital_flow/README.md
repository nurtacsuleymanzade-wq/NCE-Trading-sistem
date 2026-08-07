# Capital Flow Intelligence Engine V2

This is an additive, raw-data-first Phase 1 implementation for BTCUSDT. The
core is shared by live collectors, historical replay, and unit tests.

## Integration

In the existing FastAPI application, add:

```python
from capital_flow.http_api import create_router
app.include_router(create_router(), prefix="/api/v1")
```

The endpoints are:

`/api/v1/capital-flow/summary`, `/spot`, `/futures`, `/trader-size`,
`/top-traders`, `/orderbook`, `/exchange`, `/institutional`, `/smart-money`,
and `/data-health`.

Run the collector explicitly, after confirming there is no existing owner for
the same stream:

```bash
python -m capital_flow.collector --db data/capital_flow.sqlite3 --symbol BTCUSDT
```

It uses only public Binance market data, has no trading code, and requires no
private Binance key. `websockets` is needed only for the opt-in collector;
FastAPI/httpx are needed by the host application.

## Semantics

`m=false` is aggressive/taker buy and `m=true` is aggressive/taker sell. Spot
and futures are never merged. Futures buy is not labeled as a new long without
OI/position/liquidation evidence. Displayed orderbook liquidity is not counted
as executed money flow. Missing data remains `UNAVAILABLE`, never neutral.

The current package intentionally returns `UNAVAILABLE` for GraphSense
exchange flow, ETF/institutional flow, and Binance Web3 smart-money until their
official/open-source adapters are actually installed and validated.
