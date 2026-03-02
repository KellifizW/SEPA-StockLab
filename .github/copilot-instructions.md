# SEPA-StockLab — GitHub Copilot Instructions

> This file is the **always-on instruction** for GitHub Copilot in VS Code.
> It is automatically included in every chat request within this workspace.
> For task-specific workflows, use the slash commands defined in `.github/prompts/`.
> For specialized AI personas, use the custom agents in `.github/agents/`.
> For file-type-specific rules, see `.github/instructions/`.

---

## Project Overview

SEPA-StockLab is a full-stack stock screening and portfolio management tool that implements
**three independent trading methodologies** on the US stock market:

### 1. SEPA (Minervini) — `screener.py`
Based on Mark Minervini's *Trade Like a Stock Market Wizard*. 3-stage funnel:
1. **Stage 1** — finvizfinance / NASDAQ FTP coarse filter (price, volume, ROE)
2. **Stage 2** — TT1-TT10 Trend Template validation (SMA alignment, 52-week position, RS rank)
3. **Stage 3** — SEPA 5-Pillar scoring (Trend, Fundamentals, Catalyst, Entry Timing, Risk/Reward)

### 2. QM (Qullamaggie) — `qm_screener.py`
Kristjan Kullamägi's breakout swing trading system. Pure technical, no fundamental filtering:
1. **Stage 1** — finvizfinance broad filter: price, volume, basic momentum
2. **Stage 2** — ADR veto + dollar volume + 1M/3M/6M momentum confirmation
3. **Stage 3** — Consolidation pattern, MA alignment, higher lows scoring + 6-dimension star rating

### 3. ML (Martin Luk) — `ml_screener.py`
Martin Luk's pullback swing trading system. EMA-based, AVWAP as key S/R:
1. **Stage 1** — finvizfinance / NASDAQ FTP coarse filter
2. **Stage 2** — ADR + dollar volume + EMA structure + momentum confirmation
3. **Stage 3** — EMA alignment, pullback depth, AVWAP confluence, volume dry-up + 7-dimension star rating

### Combined Scanner — `combined_scanner.py`
Runs SEPA + QM in parallel using a **single shared universe fetch**, saving ~40-60% scan time.

### Additional Features
- **VCP Backtester** — Walk-forward VCP signal backtest over 2 years of price history, with look-ahead bias protection
- **VCP Detection** — Real-time Volatility Contraction Pattern detection
- **Single-Stock Analysis** — Deep analysis for all three strategies (SEPA: BUY/WATCH/AVOID; QM/ML: star rating)
- **Position Monitor** — 14-signal health checks, Minervini trailing stops
- **Watchlist** — A/B/C grade watchlist with auto-grading, HTMX partial updates
- **Market Environment** — SPY/QQQ/IWM breadth, distribution days, sector rotation
- **RS Ranking** — IBD-style Relative Strength percentile ranking
- **Interactive Charts** — Enriched daily, weekly, and intraday candlestick charts with overlays
- **DuckDB Persistence** — All historical scan/RS/market/position/watchlist data

**Dual interface:**
- **Web UI** — Flask (port 5000) with Bootstrap 5 dark theme, Jinja2 templates, HTMX partial updates
- **CLI** — argparse subcommands (`scan`, `analyze`, `watchlist`, `positions`, `market`, `report`, `daily`, `rs`, `vcp`)

**Target user:** Single retail trader, HK timezone. UI is bilingual (Traditional Chinese primary, English secondary).

It screens the entire US stock market through a **3-stage funnel**:
1. **Stage 1 (Coarse Filter)** — finvizfinance broad filter (price, volume, ROE)
2. **Stage 2 (Trend Template)** — TT1-TT10 validation against SMA alignment, 52-week position, RS ranking
3. **Stage 3 (SEPA 5-Pillar Scoring)** — Trend, Fundamentals, Catalyst, Entry Timing, Risk/Reward

Additional features: VCP (Volatility Contraction Pattern) detection, position tracking with
Minervini trailing stops, market regime classification, watchlist management, RS ranking.

**Dual interface:**
- **Web UI** — Flask (port 5000) with Bootstrap 5 dark theme, Jinja2 templates
- **CLI** — argparse subcommands (`scan`, `analyze`, `watchlist`, `positions`, `market`, `report`, `daily`, `rs`, `vcp`)

**Target user:** Single retail trader, HK timezone. UI is bilingual (Traditional Chinese primary, English secondary).

---

## Tech Stack

- **Python 3.10+** — Core language
- **Flask 3.0** — Web server with JSON API + Jinja2 templates
- **Bootstrap 5.3 (dark theme)** — Frontend via CDN (no build step, no bundler)
- **HTMX** — Partial-page updates for watchlist and position rows (via CDN)
- **finvizfinance** — Coarse stock screening, sector rankings (primary universe source)
- **NASDAQ FTP** — Free alternative universe source (no API key required, 24h cached)
- **yfinance** — Historical OHLCV, fundamentals, earnings, institutional data
- **pandas_ta** — Technical indicators (SMA, EMA, RSI, ATR, Bollinger Bands, AVWAP)
- **pandas + numpy** — Data manipulation
- **pyarrow** — Parquet file I/O for price cache
- **duckdb** — Analytical SQL database for historical scan/RS/market/position/watchlist persistence
- **tabulate** — Terminal table formatting
- **threading / ThreadPoolExecutor** — Background jobs and parallel scan execution

**Hybrid persistence:** DuckDB (`data/sepa_stock.duckdb`) is the primary store for watchlist, positions, and historical data. Flat files (JSON, Parquet, CSV) remain as fallback/cache and receive dual-writes for safety.

---

## File Structure & Module Responsibilities

```
app.py                  # Flask web server — ALL routes (~3100 lines), background jobs, chart API
minervini.py            # CLI entry point (argparse subcommands)
trader_config.py        # Centralized config — ALL strategy parameters as constants
start_web.py            # Convenience launcher for web UI

modules/
  data_pipeline.py      # SINGLE data access layer — wraps finvizfinance, yfinance, pandas_ta
                        # All other modules import ONLY from here for market data
  db.py                 # DuckDB persistence layer — scan_history, rs_history, market_env_history,
                        # watchlist_store, open_positions, closed_positions, fundamentals_cache
                        # Public APIs: wl_load/wl_save, pos_load/pos_save, append_*, query_*, db_stats
  nasdaq_universe.py    # Free NASDAQ FTP universe (alt to finvizfinance); 24h cache; ~2-4 min

  # ── SEPA (Minervini) ─────────────────────────────────────────────────────
  screener.py           # 3-stage SEPA scan funnel (Stage 1→2→3), TT1-TT10, 5-pillar scoring
  stock_analyzer.py     # Deep SEPA single-stock analysis → BUY/WATCH/AVOID recommendation

  # ── QM (Qullamaggie) ────────────────────────────────────────────────────
  qm_screener.py        # 3-stage QM breakout scan funnel — pure technical, ADR gate
  qm_analyzer.py        # QM 6-dimension star rating engine (A-F: momentum, ADR, consolidation…)
  qm_setup_detector.py  # QM setup detection helpers
  qm_position_rules.py  # QM position sizing and management rules

  # ── ML (Martin Luk) ──────────────────────────────────────────────────────
  ml_screener.py        # 3-stage ML pullback scan funnel — EMA-based, AVWAP S/R
  ml_analyzer.py        # ML 7-dimension star rating engine (A-G: EMA structure, pullback, AVWAP…)
  ml_setup_detector.py  # ML setup detection helpers
  ml_position_rules.py  # ML position sizing rules (max stop 2.5%, risk 0.50%)

  # ── Combined ─────────────────────────────────────────────────────────────
  combined_scanner.py   # Unified scanner: SEPA + QM parallel, single universe fetch (~40-60% faster)

  # ── Common ───────────────────────────────────────────────────────────────
  rs_ranking.py         # IBD-style Relative Strength percentile ranking engine
  vcp_detector.py       # VCP auto-detection — swing contractions, ATR/BBands, volume dry-up
  backtester.py         # Walk-forward VCP backtest engine (2y history, no look-ahead bias)
  market_env.py         # Market regime classifier — SPY/QQQ/IWM breadth, distribution days, sector rotation
  position_monitor.py   # Position tracking, 14-signal health checks, Minervini trailing stops
  watchlist.py          # A/B/C grade watchlist with auto-grading, promote/demote
  report.py             # HTML report generation, CSV export, terminal table formatting

templates/              # Jinja2 HTML templates (Bootstrap 5 dark theme)
  base.html             # Shared layout — navbar, toast, CSS custom properties
  dashboard.html        # Landing page
  scan.html             # SEPA scan UI
  analyze.html          # SEPA single-stock deep analysis
  qm_scan.html          # QM breakout scan UI
  qm_analyze.html       # QM single-stock star rating analysis
  qm_guide.html         # QM methodology guide (renders Qullamaggie docs)
  ml_scan.html          # ML pullback scan UI
  ml_analyze.html       # ML single-stock star rating analysis
  ml_guide.html         # ML methodology guide (renders Martin Luk docs)
  combined_scan.html    # Combined SEPA + QM scan UI
  watchlist.html        # Watchlist management (HTMX partial updates)
  positions.html        # Position monitoring
  market.html           # Market environment dashboard
  vcp.html              # VCP detection results
  backtest.html         # VCP backtest results
  guide.html            # Renders docs/GUIDE.md
  _position_row.html    # HTMX partial — single position row
  _watchlist_rows.html  # HTMX partial — watchlist table body

data/                   # Runtime data (DuckDB, JSON, CSV, Parquet, cache)
  sepa_stock.duckdb         # DuckDB database — primary persistent store
  price_cache/              # yfinance OHLCV cache (Parquet + .meta files)
  db_backups/               # Daily JSON backups (DuckDB dual-write safety)
  last_scan.json            # SEPA scan results cache
  qm_last_scan.json         # QM scan results cache
  ml_last_scan.json         # ML scan results cache
  combined_last_scan.json   # Combined scan results cache
  rs_cache.csv              # RS ranking cache
  watchlist.json            # Watchlist JSON backup (primary: watchlist_store table)
  positions.json            # Positions JSON backup (primary: open_positions table)
  nasdaq_universe_cache.json # NASDAQ FTP ticker list (24h cache)

scan_results/           # Generated scan CSV exports (not committed)
  combined_*.csv        # Combined scan exports
  combined_sepa_*.csv   # SEPA portion of combined scan

.github/                # AI-assisted development configuration
  copilot-instructions.md   # Always-on instructions (this file)
  instructions/             # File-based instructions (applied by glob pattern)
  prompts/                  # Prompt files — slash commands for common workflows
  agents/                   # Custom agents — specialized AI personas
  hooks/                    # Lifecycle hooks — automated quality checks
```

---

## AI Customization Architecture

This project uses GitHub Copilot's full customization stack, inspired by the
[everything-claude-code](https://github.com/affaan-m/everything-claude-code) methodology:

| ECC Concept | Copilot Equivalent | Location | Invocation |
|---|---|---|---|
| CLAUDE.md / Rules | Always-on instructions | `.github/copilot-instructions.md` | Automatic |
| File-specific rules | File-based instructions | `.github/instructions/*.instructions.md` | Auto (by glob) |
| Skills / Commands | Prompt files | `.github/prompts/*.prompt.md` | `/command` in chat |
| Agents / Subagents | Custom agents | `.github/agents/*.agent.md` | Agent dropdown |
| Hooks | Agent hooks | `.github/hooks/*.json` | Lifecycle events |

### Available Slash Commands (Prompt Files)
- `/sepa-scan-debug` — Debug scan pipeline issues (Stage 1→2→3 data flow)
- `/add-feature` — Plan and implement a new feature following project conventions
- `/code-review` — Review code for SEPA-StockLab conventions and trading logic accuracy
- `/tdd` — Test-driven development workflow (RED → GREEN → REFACTOR)
- `/refactor` — Refactor code focusing on DRY, type hints, and known technical debt
- `/trading-verify` — Verify Minervini/Qullamaggie/Martin Luk trading logic correctness

### Available Custom Agents
- **Planner** — Read-only feature planning agent; creates implementation blueprints
- **Code Reviewer** — Reviews code quality, security, and trading logic accuracy
- **Trading Expert** — SEPA/QM/ML methodology specialist for domain questions

---

## Domain Knowledge — Three Trading Systems

### System 1: SEPA (Minervini)

#### Trend Template (TT1-TT10)
10 technical criteria for a Stage 2 uptrend:
- TT1: Price > SMA50 > SMA150 > SMA200
- TT4: SMA200 rising for ≥22 days
- TT7: Price ≥ 25% above 52-week low
- TT8: Price within 25% of 52-week high
- TT9: RS rank ≥ 70 percentile
- Full definitions in `screener.py` and `docs/stockguide.md`

#### SEPA 5 Pillars
1. **Trend (趨勢)** — Stage 2 uptrend confirmed by TT
2. **Fundamentals (基本面)** — EPS growth ≥25%, revenue growth ≥20%, ROE ≥17%
3. **Catalyst (催化劑)** — Earnings acceleration, new products, industry tailwinds
4. **Entry Timing (入場時機)** — VCP breakout, volume surge, proper base
5. **Risk/Reward (風險回報)** — Stop ≤ 7-8%, R:R ≥ 2:1

### System 2: QM (Qullamaggie)
Pure technical breakout system. Key concepts:
- **ADR (Average Daily Range)** has **independent veto power** — stocks below `QM_MIN_ADR_PCT` always rejected
- **6-dimension star rating** (A–F): Momentum Quality, ADR Level, Consolidation Quality, MA Alignment, Stock Type, Market Timing
- Star rating → position sizing: 5+★ → 20-25%, 5★ → 15-25%, 4-4.5★ → 10-15%, 3-3.5★ ≤10%, <3★ → PASS
- Market gate: QM breakouts blocked in confirmed bear/downtrend

### System 3: ML (Martin Luk)
Pullback swing system using EMA structure. Key concepts:
- **EMA (not SMA)** based: 9/21/50/150 EMA stacking
- **AVWAP (Anchored VWAP)** as key support/resistance indicator
- **7-dimension star rating** (A–G): EMA Structure, Pullback Quality, AVWAP Confluence, Volume Pattern, Risk/Reward, Relative Strength, Market Environment
- Max stop loss: 2.5%; risk per trade: 0.50% of account

### VCP (Volatility Contraction Pattern)
A base pattern with progressively tightening price contractions (T-2, T-3, T-4+).
Key signals: decreasing swing ranges, volume dry-up near pivot, ATR/BBands contraction.
Detected in `vcp_detector.py`. Backtested in `backtester.py`.

### Market Regime
Classification using SPY/QQQ/IWM breadth, distribution day count, NH/NL ratio.
States: CONFIRMED_UPTREND → UPTREND_UNDER_PRESSURE → MARKET_IN_CORRECTION → DOWNTREND.
Gating: all three scan systems respect this regime — QM/ML block new entries in downtrend.

---

## Code Conventions

### Import Pattern
Every module under `modules/` follows this boilerplate:
```python
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
import trader_config as C
```
Always import `trader_config` as `C`. Access parameters as `C.PARAM_NAME`.

### Data Access — CRITICAL RULE
**All market data (prices, fundamentals, indicators) MUST go through `data_pipeline.py`.**
Never call yfinance, finvizfinance, or pandas_ta directly from other modules.
`data_pipeline` handles caching, error fallbacks, and rate limiting.

### Naming Conventions
- Module constants: `UPPER_SNAKE_CASE` (from `trader_config`)
- Private functions: `_leading_underscore` (e.g., `_clean()`, `_load()`, `_save()`)
- Public API: `snake_case` (e.g., `run_scan()`, `run_qm_scan()`, `run_ml_scan()`, `detect_vcp()`)
- ANSI terminal colours: `_GREEN`, `_RED`, `_YELLOW`, `_BOLD`, `_RESET` (defined per module)
- Minervini reference codes: `TT1`-`TT10` (trend template), `F1`-`F9` (fundamentals), `D1`-`D11` (entry rules)
- QM reference codes: dimensions `A`-`F` (star rating), `QM_` prefix for config
- ML reference codes: dimensions `A`-`G` (star rating), `ML_` prefix for config

### Flask Route Structure
```
# Page routes
GET  /               → dashboard.html
GET  /scan           → SEPA scan
GET  /qm/scan        → Qullamaggie scan
GET  /ml/scan        → Martin Luk scan
GET  /combined       → Combined SEPA + QM scan (parallel)
GET  /analyze        → SEPA deep analysis
GET  /qm/analyze     → QM star rating analysis
GET  /ml/analyze     → ML star rating analysis
GET  /backtest       → VCP backtest
GET  /watchlist, /positions, /market, /vcp, /guide

# Background job pattern
POST /api/{scan}/run          → starts thread, returns {"job_id": jid}
GET  /api/{scan}/status/<jid> → polls progress/result
POST /api/{scan}/cancel/<jid> → cancels running job

# HTMX partials (return HTML fragments)
GET  /htmx/watchlist/body
POST /htmx/watchlist/promote|demote|remove
POST /htmx/positions/add

# Chart API
GET  /api/chart/enriched/<ticker>  → daily OHLCV + indicators
GET  /api/chart/weekly/<ticker>    → weekly chart data
GET  /api/chart/intraday/<ticker>  → intraday data

# DuckDB history API
GET  /api/db/stats|scan-trend/<t>|persistent-signals|rs-trend/<t>|market-history|watchlist-history|price-history/<t>
```

### Progress Tracking Pattern (Scan Modules)
Each scan module exposes module-level progress state:
```python
_scan_lock    = threading.Lock()
_cancel_event = threading.Event()
_progress     = {"stage": "idle", "pct": 0, "msg": "", "ticker": "", "log_lines": []}
_log_lines    = []
```
`app.py` polls `/api/{scan}/status/<jid>` every second to update the progress bar.

### Error Handling
- Wrap ALL external API calls (yfinance, finvizfinance) in try/except
- Provide graceful fallbacks (e.g., finvizfinance unavailable → yfinance-only scan)
- Use `_clean()` to recursively sanitize numpy NaN/DataFrame values before JSON response
- Write per-scan log files to `logs/` directory

### Persistence Pattern (Phase 2+)
All stateful data (watchlist, positions, scan history, RS, market env) is persisted in DuckDB via `modules/db.py`.
- **Watchlist / Positions**: `db.wl_load()` / `db.wl_save()`, `db.pos_load()` / `db.pos_save()`
  — DuckDB is primary; JSON files in `data/` receive dual-writes as safety backup (`DB_JSON_BACKUP_ENABLED`)
- **Historical logging**: `db.append_scan_history()`, `db.append_rs_history()`, `db.append_market_env()`
  — Append-only; used for trend charts and persistent signal detection
- **Flat-file fallback**: If DuckDB is unavailable, all operations fall back to `_load()` / `_save()` JSON helpers
  in the respective module (`watchlist.py`, `position_monitor.py`)

---

## Configuration System

`trader_config.py` is the SINGLE source of truth for all Minervini parameters.
Organized into named groups with inline comments:

| Group | Prefix | Examples |
|-------|--------|---------|
| Account & Risk | (none) | `ACCOUNT_SIZE`, `MAX_RISK_PER_TRADE_PCT` |
| Stop Loss | (none) | `MAX_STOP_LOSS_PCT`, `ATR_STOP_MULTIPLIER` |
| Trend Template | `TT_` / `TT4_` / `TT7_`… | `TT_SMA_PERIODS`, `TT9_MIN_RS_RANK` |
| Fundamentals | `F1_` / `F3_`… | `F1_MIN_EPS_QOQ_GROWTH`, `F8_MIN_ROE` |
| Entry Rules | (none) | `MAX_CHASEUP_PCT`, `MIN_BREAKOUT_VOL_MULT` |
| VCP | `VCP_` | `VCP_MIN_CONTRACTIONS`, `VCP_MIN_BASE_WEEKS` |
| RS Ranking | `RS_` | `RS_WEIGHT_3M`, `RS_WEIGHT_12M` |
| Market | (none) | `MKT_INDICES` |
| Database (DuckDB) | `DB_` | `DB_FILE`, `DB_JSON_BACKUP_ENABLED`, `DB_JSON_BACKUP_DIR` |
| SEPA Weights | `W_` | `W_TREND`, `W_FUNDAMENTAL` |
| QM | `QM_` | `QM_MIN_ADR_PCT`, `QM_MIN_DOLLAR_VOL` |
| ML | `ML_` | `ML_MAX_STOP_PCT`, `ML_MAX_RISK_PCT` |

When adding new parameters, follow this convention:
- Use `UPPER_SNAKE_CASE`
- Add inline comment explaining trading meaning
- Group with related parameters
- Reference Minervini/Qullamaggie/Martin Luk chapter/page if applicable

---

## File Creation Guidelines

**CRITICAL: ALL new files MUST be created in their designated directory. NEVER create in root.**

When asked to create a new file, determine its type first and place it in the correct location:

### File Type → Directory Mapping

| File Type | Directory | Examples |
|-----------|-----------|----------|
| **Source Code (Core)** | Root | `app.py`, `minervini.py`, `trader_config.py`, `start_web.py` |
| **Test Files** | `tests/` | `test_*.py`, integration tests |
| **Utility Scripts** | `scripts/` | `verify_*.py`, `fix_*.py`, analysis scripts, migration scripts |
| **Documentation** | `docs/` | `.md` files, guides, reports, summaries |
| **Web Templates** | `templates/` | `*.html` Jinja2 templates |
| **Business Logic** | `modules/` | `screener.py`, `qm_screener.py`, `ml_screener.py`, `backtester.py`, etc. |
| **Configuration** | Root | `trader_config.py`, `requirements.txt` |
| **CI/CD & Dev Config** | `.github/` | `copilot-instructions.md`, prompts, agents, hooks |
| **Runtime Data** | `data/` | `.duckdb`, `.json`, `.csv`, `.parquet` (never commit) |
| **Reports & Analysis** | `reports/` | HTML reports, analysis results (generated, not committed) |
| **Scan Result CSVs** | `scan_results/` | `combined_*.csv`, `sepa_*.csv` (generated, not committed) |

### Decision Tree for New Files

```
Is it a test (test_*.py) or demo/temporary script?
├─ YES → tests/test_*.py
└─ NO

Is it a utility/analysis/verification script?
├─ YES → scripts/{name}.py
└─ NO

Is it documentation (.md)?
├─ YES → docs/{name}.md
└─ NO

Is it a module for /modules directory?
├─ YES → modules/{name}.py
└─ NO

Is it a Flask route or core application logic?
├─ YES → Root (app.py, minervini.py, start_web.py)
└─ NO

Is it configuration or project settings?
├─ YES → Root (trader_config.py, requirements.txt)
└─ NO

Is it an HTML template?
├─ YES → templates/{name}.html
└─ NO

Otherwise: Ask user for clarification before creating
```

### Workflow Before Creating Files

When you are asked to create a file:
1. **STOP** and identify the file type
2. **CLASSIFY** it using the table above
3. **CONFIRM** the correct directory in your response
4. **CREATE** with the full path: `/directory/filename.ext`

### Examples

✅ **CORRECT**
- `tests/test_my_feature.py` ← Test file
- `scripts/analyze_vcp.py` ← Analysis script
- `docs/IMPLEMENTATION_PLAN.md` ← Documentation
- `modules/new_indicator.py` ← Business logic module
- `app.py` ← Core Flask application (root)

❌ **WRONG**
- `test_my_feature.py` ← MUST be in tests/
- `analyze_vcp.py` ← MUST be in scripts/
- `IMPLEMENTATION_PLAN.md` ← MUST be in docs/
- `root/new_indicator.py` ← MUST be in modules/ as `new_indicator.py`

---

## Critical Rules — DO NOT Violate

### Data Integrity
- NEVER bypass `data_pipeline.py` for market data access
- NEVER modify cached data files directly — always go through module APIs
- When adding new data sources, register them in `data_pipeline.py`

### Trading Logic Accuracy
- NEVER change strategy parameter thresholds without explicit user instruction
- ALWAYS preserve the TT1-TT10 evaluation order in `screener.py`
- **QM: ADR gate has INDEPENDENT veto power** — must be checked before star rating computation
- **ML: EMA stacking order is 9>21>50>150** — never reorder
- VCP detection must maintain progressive contraction logic (each swing < previous)
- Score calculations must use weights from `trader_config.py`

### UI/UX
- Maintain Bootstrap 5 dark theme consistency across all templates
- All user-facing text should be bilingual (Traditional Chinese + English) where feasible
- JS stays inline in templates (no build tooling)
- CSS custom properties defined in `base.html` must be used — do not add random inline styles
- HTMX partials (`/htmx/*`) must return HTML fragments only, not full pages

### Security
- Do NOT hardcode API keys or secrets
- API endpoints should validate input before processing
- Keep `host="0.0.0.0"` only for development; production should bind `127.0.0.1`

---

## Data Flow Reference

```
[finvizfinance API] or [NASDAQ FTP (nasdaq_universe.py)]
       ↓
  data_pipeline.py (coarse filter: price > $10, volume > 200K)
       ↓
  Stage 1: initial candidate list (~100-500 stocks)
       ↓
  [yfinance API] → data_pipeline.py (OHLCV + fundamentals, Parquet cache)
  [pandas_ta]   → data_pipeline.py (SMA/EMA, RSI, ATR, BBands, AVWAP)
       ↓
  ┌──────────────────────────────────────────────────────────────────┐
  │  SEPA (screener.py)       │  QM (qm_screener.py)                │
  │  Stage 2: TT1-TT10        │  Stage 2: ADR veto + momentum       │
  │  Stage 3: 5-pillar score  │  Stage 3: 6★ star rating            │
  ├───────────────────────────┤─────────────────────────────────────┤
  │  ML (ml_screener.py) — independent scan path                    │
  │  Stage 2: EMA + AVWAP     │  Stage 3: 7★ star rating            │
  └──────────────────────────────────────────────────────────────────┘
       ↓
  modules/db.py:
    append_scan_history() → DuckDB scan_history
    append_rs_history()   → DuckDB rs_history
    append_market_env()   → DuckDB market_env_history
       ↓
  ┌─ stock_analyzer.py  (SEPA deep analysis → BUY/WATCH/AVOID)
  ├─ qm_analyzer.py     (QM 6★ star rating → position size)
  ├─ ml_analyzer.py     (ML 7★ star rating → position size)
  ├─ vcp_detector.py    (real-time VCP pattern detection)
  ├─ backtester.py      (walk-forward VCP backtest, 2y history)
  ├─ position_monitor.py (health checks, trailing stops)
  │    └─ db.pos_load() / db.pos_save() — DuckDB open/closed_positions
  ├─ watchlist.py       (A/B/C grading and tracking)
  │    └─ db.wl_load() / db.wl_save() — DuckDB watchlist_store
  ├─ rs_ranking.py      → db.append_rs_history()
  └─ market_env.py      → db.append_market_env()
```

---

## Known Technical Debt

1. **Partial test coverage** — Core algorithmic logic (scoring, VCP, TT, QM star rating, ML star rating) needs unit tests. Use `/tdd`.
2. **DRY violations** — ANSI colour constants (`_GREEN`, `_RED`, `_YELLOW`, `_BOLD`, `_RESET`) are copy-pasted across 10+ modules. Extract to `modules/utils.py`. Use `/refactor`.
3. **ROOT path boilerplate** — `ROOT = Path(...).parent.parent; sys.path.insert(...)` repeated in every module. Consider a package setup with `__init__.py`.
4. **Missing type annotations** — Functions return `pd.DataFrame`, `dict`, or `tuple` inconsistently. Add return type hints.
5. **Thread safety** — `_finviz_cache` in `data_pipeline.py` is shared mutable state without lock. (`db.py` is protected by `_lock` and `_schema_lock`.)
6. **Large templates** — `scan.html`, `qm_scan.html`, `ml_scan.html` are each ~800-1000 lines. Consider splitting JS.
7. **Large app.py** — At ~3100 lines, route handlers could be split into Flask blueprints per strategy.
8. **Inline HTML in report.py** — Should be moved to a Jinja2 template.

---

## Reference Documents

- **SEPA methodology:** `docs/stockguide.md` — Minervini SEPA (15 parts)
- **QM methodology:** `docs/QullamaggieStockguide.md`, `docs/QullamaggieStockguideMorePart1.md`, `docs/QullamaggieStockguideMorePart2.md`
- **ML methodology:** `docs/MartinLukStockGuidePart1.md`, `docs/MartinLukStockGuidePart2.md`
- **User guide:** `docs/GUIDE.md` — UI/CLI usage guide (bilingual)
- **Configuration:** `trader_config.py` — all parameter definitions with comments
- **Data sources:** finvizfinance (screening), NASDAQ FTP (free universe), yfinance (OHLCV/fundamentals), pandas_ta (indicators)
