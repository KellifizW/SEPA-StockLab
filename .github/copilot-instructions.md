# SEPA-StockLab — GitHub Copilot Instructions

> This file is the **always-on instruction** for GitHub Copilot in VS Code.
> It is automatically included in every chat request within this workspace.
> For task-specific workflows, use the slash commands defined in `.github/prompts/`.
> For specialized AI personas, use the custom agents in `.github/agents/`.
> For file-type-specific rules, see `.github/instructions/`.

---

## Project Overview

SEPA-StockLab is a full-stack stock screening and portfolio management tool based on
Mark Minervini's **SEPA (Specific Entry Point Analysis)** methodology from
*Trade Like a Stock Market Wizard*.

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
- **finvizfinance** — Coarse stock screening, sector rankings
- **yfinance** — Historical OHLCV, fundamentals, earnings, institutional data
- **pandas_ta** — Technical indicators (SMA, RSI, ATR, Bollinger Bands)
- **pandas + numpy** — Data manipulation
- **pyarrow** — Parquet file I/O for price cache
- **tabulate** — Terminal table formatting
- **threading** — Background jobs for long-running scans

**No database.** All persistence uses JSON files, Parquet, and CSV in `data/`.

---

## File Structure & Module Responsibilities

```
app.py                  # Flask web server, all API routes, background job management
minervini.py            # CLI entry point (argparse subcommands)
trader_config.py        # Centralized config — ALL Minervini parameters as constants

modules/
  data_pipeline.py      # SINGLE data access layer — wraps finvizfinance, yfinance, pandas_ta
                        # All other modules import ONLY from here for market data
  screener.py           # 3-stage SEPA scan funnel (Stage 1→2→3), TT validation, 5-pillar scoring
  rs_ranking.py         # IBD-style Relative Strength percentile ranking engine
  vcp_detector.py       # VCP auto-detection — swing contractions, ATR/BBands, volume dry-up
  stock_analyzer.py     # Deep single-stock analysis, BUY/WATCH/AVOID recommendation
  market_env.py         # Market regime classifier — SPY/QQQ/IWM breadth, distribution days, sector rotation
  position_monitor.py   # Position tracking, 14-signal health checks, Minervini trailing stops
  watchlist.py          # A/B/C grade watchlist with auto-grading, promote/demote
  report.py             # HTML report generation, CSV export, terminal table formatting

templates/              # Jinja2 HTML templates (Bootstrap 5 dark theme)
  base.html             # Shared layout — navbar, toast, CSS custom properties
  dashboard.html        # Landing page
  scan.html             # Scan UI (the largest template ~1000 lines)
  analyze.html          # Single-stock deep analysis
  watchlist.html        # Watchlist management
  positions.html        # Position monitoring
  market.html           # Market environment dashboard
  vcp.html              # VCP detection results
  guide.html            # Renders GUIDE.md

data/                   # Runtime data (JSON, CSV, Parquet, cache)
  price_cache/          # yfinance OHLCV cache (Parquet + .meta files)
  last_scan.json        # Persisted scan results
  rs_cache.csv          # RS ranking cache

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
- `/trading-verify` — Verify Minervini trading logic correctness (TT, scoring, VCP)

### Available Custom Agents
- **Planner** — Read-only feature planning agent; creates implementation blueprints
- **Code Reviewer** — Reviews code quality, security, and trading logic accuracy
- **Trading Expert** — Minervini SEPA methodology specialist for domain questions

---

## Domain Knowledge — Minervini SEPA System

When working on this project, understand these core concepts:

### Trend Template (TT1-TT10)
10 technical criteria that a stock must pass to be in a "Stage 2 uptrend":
- TT1: Price > SMA50 > SMA150 > SMA200
- TT4: SMA200 rising for ≥22 days
- TT7: Price ≥ 25% above 52-week low
- TT8: Price within 25% of 52-week high
- TT9: RS rank ≥ 70 percentile
- Full definitions in `screener.py` and `stockguide.md`

### SEPA 5 Pillars
1. **Trend (趨勢)** — Stage 2 uptrend confirmed by TT
2. **Fundamentals (基本面)** — EPS growth ≥25%, revenue growth ≥20%, ROE ≥17%
3. **Catalyst (催化劑)** — Earnings acceleration, new products, industry tailwinds
4. **Entry Timing (入場時機)** — VCP breakout, volume surge, proper base
5. **Risk/Reward (風險回報)** — Stop ≤ 7-8%, R:R ≥ 2:1

### VCP (Volatility Contraction Pattern)
A base pattern with progressively tightening price contractions (T-2, T-3, T-4+).
Key signals: decreasing swing ranges, volume dry-up near pivot, ATR/BBands contraction.
Detected in `vcp_detector.py`.

### Market Regime
Classification system using SPY/QQQ/IWM breadth, distribution day count, NH/NL ratio.
States: CONFIRMED_UPTREND → UPTREND_UNDER_PRESSURE → MARKET_IN_CORRECTION → DOWNTREND.
Determines position sizing and aggressiveness.

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
- Public API: `snake_case` (e.g., `run_scan()`, `detect_vcp()`, `assess()`)
- ANSI terminal colours: `_GREEN`, `_RED`, `_BOLD`, `_RESET` (defined per module)
- Minervini reference codes: `TT1`-`TT10` (trend template), `F1`-`F9` (fundamentals), `D1`-`D11` (entry rules)

### Flask API Pattern
Background jobs follow this pattern:
```python
POST /api/{feature}/run   → starts background thread, returns {"job_id": jid}
GET  /api/{feature}/status/<jid>  → polls job status/progress
```
Progress tracking uses module-level `threading.Lock` + dict.

### Error Handling
- Wrap ALL external API calls (yfinance, finvizfinance) in try/except
- Provide graceful fallbacks (e.g., finvizfinance unavailable → yfinance-only scan)
- Use `_clean()` to recursively sanitize numpy NaN/DataFrame values before JSON response
- Write per-scan log files to `logs/` directory

### JSON Persistence
For stateful features (watchlist, positions), use the `_load()` / `_save()` pattern
with JSON files in `data/`.

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
| SEPA Weights | `W_` | `W_TREND`, `W_FUNDAMENTAL` |

When adding new parameters, follow this convention:
- Use `UPPER_SNAKE_CASE`
- Add inline comment explaining trading meaning
- Group with related parameters
- Reference Minervini chapter/page if applicable

---

## Critical Rules — DO NOT Violate

### Data Integrity
- NEVER bypass `data_pipeline.py` for market data access
- NEVER modify cached data files directly — always go through module APIs
- When adding new data sources, register them in `data_pipeline.py`

### Trading Logic Accuracy
- NEVER change Minervini parameter thresholds without explicit user instruction
- ALWAYS preserve the TT1-TT10 evaluation order in `screener.py`
- VCP detection must maintain progressive contraction logic (each swing < previous)
- Score calculations in `screener.py` must use weights from `trader_config.py`

### UI/UX
- Maintain Bootstrap 5 dark theme consistency across all templates
- All user-facing text should be bilingual (Traditional Chinese + English) where feasible
- JS stays inline in templates (no build tooling)
- CSS custom properties defined in `base.html` must be used — do not add random inline styles

### Security
- Do NOT hardcode API keys or secrets
- API endpoints should validate input before processing
- Keep `host="0.0.0.0"` only for development; production should bind `127.0.0.1`

---

## Data Flow Reference

```
[finvizfinance API]
       ↓
  data_pipeline.py (coarse filter: price > $10, volume > 200K, ROE > 10%)
       ↓
  screener.py Stage 1 (initial candidate list, ~100-300 stocks)
       ↓
  [yfinance API] → data_pipeline.py (OHLCV + fundamentals, Parquet cache)
       ↓
  screener.py Stage 2 (TT1-TT10 validation, ~30-80 stocks)
       ↓
  [pandas_ta] → data_pipeline.py (SMA, RSI, ATR, BBands computation)
       ↓
  screener.py Stage 3 (SEPA 5-pillar scoring, final ranked list)
       ↓
  ┌─ stock_analyzer.py (deep single-stock analysis → BUY/WATCH/AVOID)
  ├─ vcp_detector.py (VCP pattern detection on candidates)
  ├─ position_monitor.py (daily health checks on held positions)
  ├─ watchlist.py (A/B/C grading and tracking)
  └─ market_env.py (regime classification for overall exposure)
```

---

## Known Technical Debt

These issues exist and should be incrementally addressed:

1. **No tests** — The project has no test suite. When adding new features, consider writing pytest tests for algorithmic logic (scoring, VCP detection, TT validation). Use `/tdd` slash command to follow TDD workflow.
2. **DRY violations** — ANSI colour constants (`_GREEN`, `_RED`, `_BOLD`, `_RESET`) are copy-pasted across 6+ modules. Extract to a shared `modules/utils.py`. Use `/refactor` slash command to address.
3. **ROOT path boilerplate** — `ROOT = Path(...).parent.parent; sys.path.insert(...)` repeated in every module. Consider a package setup with proper `__init__.py`.
4. **Missing type annotations** — Functions return `pd.DataFrame`, `dict`, or `tuple` inconsistently without type hints. Add return type annotations.
5. **Thread safety** — `_finviz_cache` in `data_pipeline.py` is shared mutable state without locking. Needs `threading.Lock`.
6. **Large templates** — `scan.html` is ~1000 lines. Consider splitting JS logic.
7. **Inline HTML in report.py** — Should be moved to a Jinja2 template.

---

## Reference Documents

- **Trading methodology:** See `stockguide.md` for complete Minervini SEPA methodology (15 parts)
- **User guide:** See `GUIDE.md` for UI/CLI usage guide (bilingual)
- **Configuration:** See `trader_config.py` for all parameter definitions with comments
- **Data sources:** finvizfinance (screening), yfinance (OHLCV/fundamentals), pandas_ta (indicators)
