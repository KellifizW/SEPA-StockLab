# SEPA-StockLab

**Minervini SEPA 選股系統 — Stock Screening & Portfolio Management Tool**

基於 Mark Minervini《Trade Like a Stock Market Wizard》的 **SEPA（Specific Entry Point Analysis）** 方法論，對全美股市進行三階段漏斗式篩選，結合 VCP 形態偵測、倉位管理與市場制度分類。

A full-stack stock screening tool implementing Minervini's 3-stage funnel (Coarse Filter → Trend Template → SEPA 5-Pillar Scoring), VCP detection, position tracking with trailing stops, and market regime classification.

---

## Features 功能

| Feature | Description |
|---------|-------------|
| **3-Stage SEPA Scan** | finvizfinance 粗篩 → TT1-TT10 趨勢模板驗證 → SEPA 五大支柱評分 |
| **VCP Detection** | 自動偵測 Volatility Contraction Pattern（波動收縮形態） |
| **Single-Stock Analysis** | 深度個股分析，BUY / WATCH / AVOID 推薦 |
| **Position Monitor** | 14 項健康信號檢查、Minervini 移動停損 |
| **Watchlist** | A/B/C 評級自動分級、升降級管理 |
| **Market Environment** | SPY/QQQ/IWM 市場制度分類、分佈日計數、板塊輪動 |
| **RS Ranking** | IBD 風格相對強度百分位排名 |
| **DuckDB Persistence** | 歷史掃描/RS/市場/倉位/觀察清單持久化存儲及趨勢圖表 |
| **Dual Interface** | Web UI (Flask) + CLI (argparse) |

---

## Quick Start 快速開始

### Prerequisites 環境需求

- Python 3.10+
- pip

### Installation 安裝

```bash
# Clone the repository
git clone https://github.com/YOUR_USERNAME/SEPA-StockLab.git
cd SEPA-StockLab

# Install dependencies
pip install -r requirements.txt
```

### Run Web UI 啟動網頁介面

```bash
python app.py
# Opens http://localhost:5000 in browser automatically
```

### Run CLI 命令列操作

```bash
# Full SEPA scan
python minervini.py scan

# Analyze a single stock
python minervini.py analyze NVDA

# Watchlist management
python minervini.py watchlist list
python minervini.py watchlist add NVDA

# Position tracking
python minervini.py positions list
python minervini.py positions add NVDA 500.00 10 480.00
python minervini.py positions check

# Market environment
python minervini.py market

# VCP detection
python minervini.py vcp NVDA

# RS ranking
python minervini.py rs top --min 85

# Daily routine (scan + market + positions)
python minervini.py daily

# Generate HTML report
python minervini.py report
```

---

## Architecture 架構

```
                    ┌─────────────┐
                    │  User / 用戶 │
                    └──────┬──────┘
                 ┌─────────┴─────────┐
                 │                   │
          ┌──────▼──────┐    ┌───────▼───────┐
          │   app.py    │    │ minervini.py  │
          │  (Web UI)   │    │    (CLI)      │
          └──────┬──────┘    └───────┬───────┘
                 └─────────┬─────────┘
                           │
          ┌────────────────▼────────────────┐
          │         modules/                │
          │  ┌──────────────────────────┐   │
          │  │    data_pipeline.py      │   │  ← Single data access layer
          │  │  (finviz / yfinance /    │   │
          │  │   pandas_ta / cache)     │   │
          │  └────────────┬─────────────┘   │
          │       ┌───────┼───────┐         │
          │       ▼       ▼       ▼         │
          │  screener  analyzer  vcp        │
          │  rs_rank   market    positions  │
          │  watchlist report               │
          └─────────────────────────────────┘
```

### Module Responsibilities 模組職責

| Module | Role |
|--------|------|
| `data_pipeline.py` | **唯一資料層** — 封裝 finvizfinance、yfinance、pandas_ta，處理快取與降級 |
| `db.py` | **DuckDB 持久層** — scan_history、rs_history、market_env_history、watchlist_store、positions |
| `screener.py` | 三階段 SEPA 掃描漏斗（Stage 1→2→3）、TT 驗證、五大支柱評分 |
| `rs_ranking.py` | IBD 相對強度百分位排名引擎 |
| `vcp_detector.py` | VCP 自動偵測 — 波動收縮、ATR/BBands、量能乾涸 |
| `stock_analyzer.py` | 深度個股分析 → BUY/WATCH/AVOID 推薦 |
| `market_env.py` | 市場制度分類（確認上漲 → 上漲受壓 → 修正 → 下跌） |
| `position_monitor.py` | 倉位追蹤、14 項健康信號、Minervini 移動停損 |
| `watchlist.py` | A/B/C 等級觀察名單 |
| `report.py` | HTML 報告、CSV 匯出、終端表格 |

---

## Configuration 配置

All Minervini parameters are centralized in `trader_config.py`:

所有 Minervini 參數集中在 `trader_config.py`：

| Group | Prefix | Examples |
|-------|--------|---------|
| Account & Risk | — | `ACCOUNT_SIZE`, `MAX_RISK_PER_TRADE_PCT` |
| Stop Loss | — | `MAX_STOP_LOSS_PCT`, `ATR_STOP_MULTIPLIER` |
| Trend Template | `TT_` | `TT_SMA_PERIODS`, `TT9_MIN_RS_RANK` |
| Fundamentals | `F1_`–`F8_` | `F1_MIN_EPS_QOQ_GROWTH`, `F8_MIN_ROE` |
| VCP | `VCP_` | `VCP_MIN_CONTRACTIONS`, `VCP_MIN_BASE_WEEKS` |
| RS Ranking | `RS_` | `RS_WEIGHT_3M`, `RS_WEIGHT_12M` |
| Database (DuckDB) | `DB_` | `DB_FILE`, `DB_JSON_BACKUP_ENABLED`, `DB_JSON_BACKUP_DIR` |
| SEPA Weights | `W_` | `W_TREND`, `W_FUNDAMENTAL` |

---

## Data Persistence 資料儲存

Hybrid persistence — DuckDB as primary store, flat files as fallback/cache:

混合持久化：DuckDB 為主要存儲，JSON/CSV/Parquet 為備用快取：

| File / Table | Format | Purpose |
|---|---|---|
| `data/sepa_stock.duckdb` | DuckDB | 主要持久層（掃描歷史/RS/市場/倉位/觀察清單） |
| `data/price_cache/*.parquet` | Parquet | OHLCV 價格快取 |
| `data/price_cache/*.json` | JSON | 基本面資料快取 |
| `data/last_scan.json` | JSON | 最近一次掃描結果（同時寫入 scan_history） |
| `data/rs_cache.csv` | CSV | RS 排名快取（同時寫入 rs_history） |
| `data/watchlist.json` | JSON | 觀察名單備份（主要來源：watchlist_store 表） |
| `data/positions.json` | JSON | 倉位記錄備份（主要來源：open_positions 表） |
| `data/db_backups/` | JSON | DuckDB 雙寫安全備份（每日自動寫入） |

### DuckDB Tables DuckDB 表結構

| Table | Purpose |
|-------|---------|
| `scan_history` | 每日掃描結果（每日 × 每股） |
| `rs_history` | RS 排名快照（每日 × 每股） |
| `market_env_history` | 市場環境日誌（每日一行） |
| `watchlist_store` | 觀察名單持久存儲（主要來源） |
| `open_positions` | 開倉持倉持久存儲（主要來源） |
| `closed_positions` | 已平倉歷史記錄 |
| `watchlist_log` | 觀察名單異動審計日誌 |
| `position_log` | 倉位開倉/平倉日誌 |
| `fundamentals_cache` | 基本面快取（JSON 序列化） |

---

## SEPA Methodology 方法論

This tool implements Minervini's complete system:

### 3-Stage Funnel 三階段漏斗

1. **Stage 1 — Coarse Filter**：finvizfinance 篩選（price > $10, volume > 200K, ROE > 10%）→ ~100-300 股
2. **Stage 2 — Trend Template**：TT1-TT10 驗證（SMA 排列、52 週位置、RS 排名）→ ~30-80 股
3. **Stage 3 — SEPA Scoring**：五大支柱加權評分 → 最終排名清單

### Trend Template (TT1-TT10) 趨勢模板

- TT1: Price > SMA50 > SMA150 > SMA200
- TT4: SMA200 上升 ≥22 天
- TT7: 價格 ≥ 52 週低點 25% 以上
- TT8: 價格距 52 週高點 25% 以內
- TT9: RS ≥ 70 百分位

### VCP (Volatility Contraction Pattern) 波動收縮形態

逐步收緊的價格波動（T-2, T-3, T-4+），伴隨量能乾涸，在 pivot 附近突破。

---

## AI-Assisted Development AI 輔助開發

This project uses GitHub Copilot's full customization stack, inspired by
[everything-claude-code](https://github.com/affaan-m/everything-claude-code):

本專案使用 GitHub Copilot 完整客製化架構，參考 everything-claude-code 方法論：

| ECC Concept | Copilot Equivalent | Location | Invocation |
|---|---|---|---|
| CLAUDE.md | Always-on instructions | `.github/copilot-instructions.md` | Automatic |
| Rules | File-based instructions | `.github/instructions/*.instructions.md` | Auto (by glob) |
| Skills / Commands | Prompt files | `.github/prompts/*.prompt.md` | `/command` in chat |
| Agents / Subagents | Custom agents | `.github/agents/*.agent.md` | Agent dropdown |
| Hooks | Agent hooks | `.github/hooks/*.json` | Lifecycle events |

### Slash Commands 斜線指令

| Command | Purpose |
|---------|---------|
| `/sepa-scan-debug` | Debug scan pipeline issues (Stage 1→2→3) |
| `/add-feature` | Plan and implement new features |
| `/code-review` | Review code for conventions and trading logic |
| `/tdd` | Test-driven development (RED → GREEN → REFACTOR) |
| `/refactor` | Refactoring with DRY and tech debt focus |
| `/trading-verify` | Verify Minervini trading logic correctness |

### Custom Agents 自訂代理

| Agent | Role |
|-------|------|
| **Planner** | Read-only feature planning — creates implementation blueprints |
| **Code Reviewer** | Reviews code quality, security, trading logic accuracy |
| **Trading Expert** | Minervini SEPA methodology specialist |

---

## Documentation 文件

| Document | Description |
|----------|-------------|
| `GUIDE.md` | 完整使用者指南（繁體中文 / English 雙語） |
| `stockguide.md` | Minervini SEPA 方法論完整參考（15 部分） |
| `trader_config.py` | 所有參數定義及行內註解 |
| `.github/copilot-instructions.md` | GitHub Copilot AI 輔助開發指引 |
| `.github/instructions/` | 按檔案類型自動套用的編碼規則 |
| `.github/prompts/` | 可重用的開發工作流程斜線指令 |
| `.github/agents/` | 專業化 AI 代理（規劃、審查、交易邏輯） |
| `.github/hooks/` | 儲存後自動品質檢查 |

---

## Tech Stack 技術棧

- **Python 3.10+** — Core language
- **Flask 3.0** — Web server + JSON API + Jinja2
- **Bootstrap 5.3** — Dark theme UI (CDN, no build step)
- **finvizfinance** — Stock screening
- **yfinance** — OHLCV & fundamentals
- **pandas_ta** — Technical indicators
- **pandas / numpy** — Data processing
- **pyarrow** — Parquet I/O
- **duckdb** — Analytical SQL database for historical and persistent data

---

## License

For personal use. Trading involves risk — this tool is for educational and research purposes only.

僅供個人使用。投資有風險，本工具僅供教育與研究用途。
