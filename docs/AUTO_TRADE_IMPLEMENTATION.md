# Auto-Trade 自動交易功能 — Implementation Summary

> Feature: Automated Buy Execution Engine (QM + ML Strategy Switching)
> Date: 2026-03-06

---

## Overview 概述

Auto-Trade is a background execution engine that automatically evaluates scan candidates from the QM (Qullamaggie breakout) and ML (Martin Luk pullback) strategies, then places buy orders via IBKR when all quality gates are passed.

自動交易是一個背景執行引擎，自動評估 QM（突破）和 ML（回調）策略的掃描候選股，當所有質量門檻通過後通過 IBKR 下達買入訂單。

---

## Architecture 架構

### 5-Phase Pipeline

```
Phase 0 ─ Load scan results (combined_last_scan.json / individual JSONs)
    ↓
Phase 1 ─ Market-regime gate (BULL_CONFIRMED → allowed, DOWNTREND → blocked)
    ↓
Phase 2 ─ Pre-filter by screener star rating ≥ threshold (3.5★ default)
    ↓
Phase 3 ─ Deep analysis (analyze_qm / analyze_ml) + watch-score evaluation
    ↓     ├─ Phase 3A: Full 6/7-dimension analysis → stars, vetoes
    ↓     └─ Phase 3B: Watch-score signals → iron rules, confidence 0-100
    ↓
Phase 4 ─ Position sizing (calc_qm_position_size / calc_ml_position_size)
    ↓     └─ Apply regime-based size multiplier
    ↓
Phase 5 ─ Execute order via ibkr_client.place_order() + attach Day1 stop
```

### Polling Architecture

- Background thread polls every **5 minutes** (configurable: `AUTO_TRADE_POLL_INTERVAL_SEC`)
- Stocks that did not qualify in earlier cycles may qualify later as conditions change
- Same-day cooldown (86400s) prevents re-buying the same ticker
- Daily cap on total buys (`AUTO_TRADE_MAX_BUYS_PER_DAY = 3`)

### Safety Layers

| Layer | Description |
|-------|-------------|
| **Master Switch** | `AUTO_TRADE_ENABLED = False` — must be explicitly enabled |
| **DRY-RUN** | `AUTO_TRADE_DRY_RUN = True` — logs actions without real orders |
| **Regime Gate** | QM only in bull/transition; ML wider but not in bear/downtrend |
| **Star Gate** | Minimum 3.5★ from deep analysis |
| **Veto System** | ADR, MARKET, WEEKLY, RISK vetoes block entry |
| **Decision Tree** | ML 9-question decision tree must be GO or CAUTION |
| **Iron Rules** | Watch-mode iron rules with `severity: block` prevent entry |
| **Watch Score** | Minimum 70/100 confidence score |
| **Daily Cap** | Max 3 buys per day |
| **Cooldown** | Same ticker cannot be bought twice in 24 hours |
| **IBKR_READONLY** | IBKR read-only mode prevents order placement |

---

## Files Modified / Created

### New Files

| File | Purpose |
|------|---------|
| `modules/auto_trader.py` | Core engine — 5-phase pipeline, polling loop, all business logic |
| `routes/auto_trade_api.py` | Flask Blueprint — `/api/auto-trade/start\|stop\|status\|history` |
| `templates/auto_trade.html` | Web UI — control panel, live metrics, execution history |

### Modified Files

| File | Change |
|------|--------|
| `trader_config.py` | Added ~40 lines of `AUTO_TRADE_*` parameters |
| `modules/db.py` | Added `auto_trade_log` table schema + `append_auto_trade()` + `query_auto_trade_log()` |
| `routes/__init__.py` | Registered `auto_trade_api` blueprint |
| `routes/pages.py` | Added `/auto-trade` page route |
| `templates/base.html` | Added "Auto-Trade 自動交易" navbar link |

---

## Configuration Parameters

All in `trader_config.py` under `# AUTO-TRADE` section:

| Parameter | Default | Description |
|-----------|---------|-------------|
| `AUTO_TRADE_ENABLED` | `False` | Master on/off switch |
| `AUTO_TRADE_DRY_RUN` | `True` | Simulate without real orders |
| `AUTO_TRADE_MAX_BUYS_PER_DAY` | `3` | Daily buy limit |
| `AUTO_TRADE_MIN_STAR_QM` | `3.5` | QM minimum star rating |
| `AUTO_TRADE_MIN_STAR_ML` | `3.5` | ML minimum star rating |
| `AUTO_TRADE_MIN_WATCH_SCORE` | `70` | Minimum watch-mode confidence |
| `AUTO_TRADE_MAX_CANDIDATES` | `10` | Max candidates per strategy per cycle |
| `AUTO_TRADE_ORDER_TYPE` | `"LMT"` | Order type: LMT or MKT |
| `AUTO_TRADE_LMT_BUFFER_PCT` | `0.1` | Limit price buffer above entry |
| `AUTO_TRADE_ATTACH_STOP` | `True` | Auto-attach Day1 stop-loss order |
| `AUTO_TRADE_POLL_INTERVAL_SEC` | `300` | Polling interval (5 minutes) |
| `AUTO_TRADE_COOLDOWN_SEC` | `86400` | Same-ticker cooldown (24 hours) |
| `AUTO_QM_REGIMES_ENABLED` | Bull/Transition/Bottom | Allowed regimes for QM |
| `AUTO_ML_REGIMES_ENABLED` | Bull/Transition/Choppy/Bottom | Allowed regimes for ML |
| `AUTO_QM_SIZE_MULTIPLIERS` | per-regime | Size scaling by market regime |
| `AUTO_ML_SIZE_MULTIPLIERS` | per-regime | Size scaling by market regime |

---

## API Endpoints

| Method | Path | Description |
|--------|------|-------------|
| `POST` | `/api/auto-trade/start` | Start engine `{ "dry_run": true }` |
| `POST` | `/api/auto-trade/stop` | Stop engine |
| `GET` | `/api/auto-trade/status` | Poll current status + config |
| `GET` | `/api/auto-trade/history?days=7` | Query execution log from DuckDB |

---

## Web UI

Accessible at `/auto-trade` (navbar: "Auto-Trade 自動交易")

- **Control Panel**: Start/Stop buttons, DRY-RUN toggle, config display
- **Live Metrics**: Buys today, cycle count, candidate count, last cycle time
- **Candidates Table**: Real-time display of current evaluation cycle results
- **Execution History**: Filterable log (1/7/30 days) with all actions (BUY/SKIP/BLOCKED)

---

## DuckDB Schema

```sql
CREATE TABLE IF NOT EXISTS auto_trade_log (
    id          INTEGER PRIMARY KEY DEFAULT(nextval('seq_auto_trade')),
    trade_date  DATE,
    trade_time  TIMESTAMP,
    ticker      VARCHAR,
    strategy    VARCHAR,     -- 'QM' | 'ML'
    stars       DOUBLE,
    watch_score INTEGER,
    regime      VARCHAR,
    action      VARCHAR,     -- 'BUY' | 'SKIP' | 'BLOCKED'
    reason      VARCHAR,
    order_type  VARCHAR,
    qty         INTEGER,
    limit_price DOUBLE,
    stop_price  DOUBLE,
    order_id    INTEGER,
    dry_run     BOOLEAN,
    iron_rules  VARCHAR,     -- JSON array
    dim_summary VARCHAR      -- JSON object
);
```

---

## Strategy Switching Logic

```
Market Regime Assessment
    ↓
┌──── QM Allowed? (BULL_CONFIRMED, BULL_UNCONFIRMED, TRANSITION, BOTTOM_FORMING)
│     ↓ YES                              ↓ NO → BLOCK all QM candidates
│   QM Breakout Pipeline
│     Stars ≥ 3.5 → analyze_qm() → watch_score ≥ 70 → BUY
│
└──── ML Allowed? (BULL_*, TRANSITION, UPTREND_UNDER_PRESSURE, BOTTOM_FORMING, CHOPPY)
      ↓ YES                              ↓ NO → BLOCK all ML candidates
    ML Pullback Pipeline
      Stars ≥ 3.5 → analyze_ml() → decision_tree GO → watch_score ≥ 70 → BUY
```

ML has wider regime tolerance because pullback entries are inherently lower-risk.

---

## Usage 使用方法

1. Run a Combined Scan first to populate `combined_last_scan.json`
2. Navigate to Auto-Trade page
3. Keep DRY-RUN enabled (recommended for first few days)
4. Click **Start** — engine begins polling every 5 minutes
5. Monitor candidates and history in the UI
6. When satisfied, disable DRY-RUN for live execution (requires IBKR connection)

**Important**: Ensure `AUTO_TRADE_ENABLED = True` in `trader_config.py` before starting live mode.
