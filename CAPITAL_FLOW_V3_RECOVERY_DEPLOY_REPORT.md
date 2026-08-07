# NCE Capital Flow V3 Recovery / Controlled Production Promotion

Date: 2026-08-07 UTC  
Final verdict: **RECOVERED_AND_DEPLOYED**

## 1. Recovery source

Canonical latest source:

`/root/NCE-Trading-sistem-inspect`

Initial recovery snapshot verified:

`/root/nce-recovery-20260807T054215Z`

The snapshot contained the production/inspect Capital Flow files, UI references, hashes, manifest, `git-status.txt`, `uncommitted.diff`, and Git command error metadata. Production had no `.git` directory, so the production Git commands correctly failed with “not a git repository”; this was recorded and no production Git state was discarded.

## 2. Production source

`/opt/nce-trader-terminal`

Production was a non-Git deployment tree. Its production-only application, router, service configuration, runtime configuration, database, and collector integration were inventoried and preserved.

## 3. Backup paths

- Initial recovery: `/root/nce-recovery-20260807T054215Z`
- Pre-promotion production backup: `/root/nce-capital-flow-v3-production-backup-20260807T055820Z`
- Staging copy: `/root/nce-capital-flow-v3-staging`
- Runtime SQLite online snapshot used for staging: `/root/nce-capital-flow-v3-staging/data/capital_flow.snapshot.sqlite3`

The pre-promotion backup included production `capital_flow`, FastAPI entry/router/config files, service files, runtime context/heartbeat/lock references, and a consistent online SQLite backup. SQLite integrity was `ok` before promotion.

## 4. Inspect Git status before promotion

Tracked modifications were in `capital_flow/collector.py`, `engine.py`, `external_context.py`, `http_api.py`, `storage.py`, and `tests/test_capital_flow.py`.

Untracked but relevant work included the five Capital Flow reports, V3 backend modules, empirical/historical tooling, the external collector service artifact, derived historical metadata, and the five Capital Flow test files. Generated `__pycache__` directories and raw historical archives were not treated as source code.

## 5. Production inventory

Production contained the existing Capital Flow engine, collector, storage, external context, exchange/GraphSense/smart-money/institutional modules, FastAPI integration under `app/main.py` and `app/api/routes.py`, the public runtime database, frontend references, and both Capital Flow systemd units.

The production database had 15 existing Capital Flow tables, including spot/futures raw data, orderbook, OI, funding, liquidations, top-trader accounts/positions, Coin Metrics, GraphSense, ETF, SEC, smart-money, and Global L/S data.

## 6. File diff

Classification before promotion:

- `IDENTICAL`: collector, storage, external context, historical/context support modules, service-compatible integration files, and common documentation.
- `CONFLICT`: `capital_flow/engine.py`, `capital_flow/http_api.py`, `capital_flow/institutional.py`.
- `INSPECT_ONLY`: V3/empirical modules, V3 tests, derived historical artifacts, reports, and the external service artifact.
- `PRODUCTION_ONLY`: production `app/main.py`, `app/api/routes.py`, production-only services/tests/deploy scripts, and runtime configuration. These were retained.
- No production-only file was deleted.

The controlled manifest is recorded at `/root/CAPITAL_FLOW_V3_DEPLOY_MANIFEST.md`.

## 7. Feature diff

| Feature | Inspect | Production after promotion |
|---|---|---|
| Interpretation layer | PRESENT | PRESENT/live |
| Short market summary | PRESENT | PRESENT/live |
| Why / Against / Missing / Conflicts | PRESENT | PRESENT/live |
| Retail definition | PRESENT | PRESENT/live |
| CVD total and slope | PRESENT | PRESENT/live |
| Position State / Whale Behavior evidence | PRESENT | PRESENT/live |
| Price impact / whale efficiency | PRESENT | PRESENT/live in size-bucket payloads |
| Global L/S, liquidations | PRESENT | PRESENT; liquidations explicitly UNAVAILABLE when absent |
| Top Trader Accounts / Positions | PRESENT | PRESENT/live, REAL |
| Coin Metrics / SEC / ETF | PRESENT | PRESENT; status/source exposed |
| Binance Smart Money | PRESENT | PRESENT; UNAVAILABLE when source absent |
| Institutional Flow | PRESENT | PRESENT/live, DERIVED with source context |
| GraphSense / Exchange Flow | PRESENT | PRESENT; UNAVAILABLE with reason when not available |
| Historical replay / calibration | PRESENT | PRESENT via derived artifacts and historical endpoint |
| Score vs probability | PRESENT | PRESENT; `score_is_probability` metadata exposed |
| Extended Capital Flow Matrix | PRESENT | PRESENT/live |
| Metadata/tooltips / Data Health | PRESENT | PRESENT/live |

## 8. Deploy manifest

The promotion was additive and file-specific. The promoted code was:

- Inspect `capital_flow/engine.py`, `http_api.py`, `institutional.py`, `empirical.py`, and `requirements.txt` to production equivalents.
- Inspect V3/empirical/phase tests to production `tests/`.
- Inspect derived historical metadata to `/var/lib/nce-trading/capital_flow/historical` for runtime API use.
- Inspect derived metadata and 204 immutable raw archives to production `historical/` because the empirical tests resolve repository-relative historical paths.

The production FastAPI main/router, collector architecture, service environment, Python venv, DB path, and production-only files were kept. No full-directory replacement, reset, clean, blind sync, or blind recursive copy was used.

## 9. Files promoted

The three conflict files, four new backend/runtime modules, requirements metadata, five tests, empirical scripts, external service artifact, derived historical metadata, and required historical raw archives were promoted explicitly. Raw archives were made read-only after copy. The production historical tree contains 215 files and is approximately 643 MB.

## 10. Files intentionally not promoted

- Production `app/main.py` and `app/api/routes.py` were not overwritten; existing integration won and was verified against the promoted router module.
- Production collector/service configuration was not overwritten.
- Inspect `index.html` was not copied into the backend tree. The frontend source of truth is the tracked repository-root `index.html`, already matching the public GitHub Pages page.
- Raw historical archives were not added to the Git commit because they are large immutable runtime artifacts; they remain on disk in inspect and production.
- Generated `__pycache__` directories were not committed.

## 11. Service changes

Service unit values were verified and retained:

- Working directory: `/opt/nce-trader-terminal`
- Database: `/var/lib/nce-trading/capital_flow.sqlite3`
- Python: `/opt/nce-trader-terminal/.venv/bin/python`
- Main collector command: `python -m capital_flow.collector ...`

The backend user service was controlled-restarted after an existing stale/orphan listener had been identified on port 8010. The main `nce-capital-flow.service` collector was not restarted. Only `nce-capital-flow-external.service` was restarted after promotion so its process loaded the promoted institutional module. All three relevant services are active afterward.

## 12. DB/schema changes

No schema migration was needed. `storage.py` was compatible, the existing database had 15 tables, and `PRAGMA integrity_check` returned `ok`. No existing data was deleted or rewritten.

## 13. Tests before deploy

Inspect Capital Flow tests:

`47 passed in 0.06s`

The inspect tree had no target-ETA tests. The separate production target-ETA selection returned `9 passed, 64 deselected`; it was kept separate from Capital Flow deployment because its live-price assertion is a known potential flakiness concern.

## 14. Staging results

Staging tree: `/root/nce-capital-flow-v3-staging`.

- Production-layout import/router test: `PASS`, 60 routes loaded, no missing routes.
- Online SQLite snapshot integrity: `ok`.
- Summary, spot, futures, top-traders, data-health, and historical-context endpoint checks: HTTP 200.
- V3 summary fields and external status contracts loaded successfully.

## 15. Production API results

After controlled restart and external-context reload:

- `/api/v1/health`: HTTP 200.
- `/api/v1/capital-flow/summary`: HTTP 200.
- `/api/v1/capital-flow/spot`: HTTP 200.
- `/api/v1/capital-flow/futures`: HTTP 200.
- `/api/v1/capital-flow/top-traders`: HTTP 200.
- `/api/v1/capital-flow/data-health`: HTTP 200.
- `/api/v1/capital-flow/historical-context`: HTTP 200.

Live summary exposed `shortText`, `flowBias`, `capitalRegime`, `tradeImplication`, `execution`, `confidence`, root `why/against/missing/conflicts`, interpretation metadata, state matrix, and historical context.

## 16. Collector health

The main collector is one process, PID `957319`, with heartbeat `RUNNING`, `error_count=0`, `spot_ws_alive=true`, and the known futures source `legacy_nce_1s_collector`. The external collector is one process, PID `1002163`, with context status `PASS`.

## 17. Duplicate protection

Exactly one main Capital Flow collector, one external context collector, and one backend uvicorn listener were present. Port 8010 was held by the expected production backend PID `999630`. No duplicate Futures/Spot socket process was created.

## 18. Live BTCUSDT semantic validation

Live BTCUSDT validation confirmed:

- Spot and futures CVD total, 1-minute slope, and 5-minute slope are exposed with query-window reset metadata.
- Retail is the SMALL+MEDIUM bucket definition and is separately labeled from WHALE_SIZED.
- Whale-sized and mega-whale flows expose buy/sell USD, net flow, price impact, and efficiency/bps-per-million fields.
- Positioning, Whale-Sized, OI, orderbook displayed-intent, Top Trader Accounts/Positions, and Global L/S states carry status/timeframe metadata.
- Liquidations, Smart Money, and Exchange/GraphSense are marked `UNAVAILABLE` where unavailable; no fabricated values were observed.
- Coin Metrics/network context, institutional/SEC/ETF context, and IBIT context expose their source/status contract. IBIT was `REAL`; aggregate institutional context was `DERIVED`.

## 19. Frontend deploy status

The tracked repository-root `index.html` was already deployed to GitHub Pages. Its SHA256 matched the public page exactly. No backend-local `index.html` was created. The public API source is `https://nce-api.78.46.134.148.sslip.io`.

## 20. Public UI status

Playwright smoke verification on the public page passed for Money Flow and found the Capital Flow UI markers for short summary, Flow Bias, Capital Regime, Trade Implication, Why/Against/Missing, matrix, Data Health, and Top Traders. Capital Flow network responses were HTTP 200 and no Capital Flow request failures were observed.

One pre-existing unrelated market request returned HTTP 422 for `/api/v1/market/klines?interval=1M`; it is outside Capital Flow and was not changed. `/tmp/nce-inline.js` passed `node --check`.

## 21. Regression results

Full production regression after promotion, including the historical repository-relative fixtures:

`120 passed in 2.31s`

## 22. Target ETA test status

Target ETA was tested separately: `9 passed, 64 deselected`. No Capital Flow production logic was changed to force this test green. The inspect Capital Flow test set did not contain target-ETA tests.

## 23. Commit hash

Canonical recovery/promotion commit:

`8610f46b5e1da050888681ba2189d81ecb8d352b`

Commit message: `feat: complete capital flow v3 interpretation and context layers`

## 24. Remaining limitations

- Liquidations, Smart Money, and GraphSense/Exchange Flow remain explicitly unavailable when their upstream resources are absent.
- Price impact and efficiency are available in the spot/futures size-bucket payloads; they are not duplicated as a separate top-level summary scalar.
- The unrelated public market `interval=1M` request still returns 422.
- The canonical inspect worktree still has untracked generated `__pycache__` directories and untracked raw historical archives; important source, tests, reports, and derived metadata were committed.

## 25. Rollback command

Do not run a blind directory restore. If rollback is required, stop only the backend user service, restore the explicitly listed production files from `/root/nce-capital-flow-v3-production-backup-20260807T055820Z`, restore the SQLite database using the backup’s consistent online snapshot procedure, validate ownership/modes, then start the backend and verify the collector/API. Keep the inspect source, both recovery backups, and staging tree intact. The main collector should not be restarted unless its own health check requires it.

## 26. Final verdict

**RECOVERED_AND_DEPLOYED**

