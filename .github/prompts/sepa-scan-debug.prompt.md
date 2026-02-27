---
name: 'sepa-scan-debug'
description: 'Debug SEPA scan pipeline issues — trace data flow through Stage 1→2→3'
agent: 'agent'
tools:
  - 'search'
  - 'codebase'
  - 'terminalLastCommand'
  - 'readFile'
---
# Debug SEPA Scan Pipeline

You are debugging the 3-stage SEPA scan pipeline in SEPA-StockLab.

## Context
The scan flows through:
1. **Stage 1** (`screener.py`) — finvizfinance coarse filter via `data_pipeline.py`
2. **Stage 2** (`screener.py`) — TT1-TT10 trend template validation using yfinance data
3. **Stage 3** (`screener.py`) — SEPA 5-pillar scoring with weights from `trader_config.py`

Web API trigger: `POST /api/scan/run` → background thread → poll `GET /api/scan/status/<jid>`

## Debugging Steps
1. **Identify which stage fails** — Check `logs/` for the scan log file. Look for `Stage1`, `Stage2`, `Stage3` markers.
2. **Check data_pipeline.py** — Is finvizfinance returning data? Is yfinance cache stale? Are there rate limit errors?
3. **Verify trader_config.py thresholds** — Are TT parameters, fundamental filters, and price/volume minimums reasonable?
4. **Check DataFrame shape** — At each stage, how many stocks survive? If 0 stocks pass Stage 2, the TT criteria may be too strict.
5. **Inspect _clean() output** — Are NaN/infinity values causing JSON serialization errors?

## Key Files to Examine
- `modules/screener.py` — Core scan logic
- `modules/data_pipeline.py` — Data fetching and caching
- `trader_config.py` — All thresholds
- `app.py` — API endpoints and background job management
- `logs/` — Per-scan log files

## Output
Provide a diagnosis with:
- Which stage the issue occurs in
- Root cause (data source, threshold, serialization, threading)
- Specific fix with code changes
