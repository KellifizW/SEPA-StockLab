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

查閱完整參數定義與說明，見 [trader_config.py](trader_config.py)。

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

完整定義見 [docs/stockguide.md](docs/stockguide.md)。

### VCP (Volatility Contraction Pattern) 波動收縮形態

逐步收緊的價格波動（T-2, T-3, T-4+），伴隨量能乾涸，在 pivot 附近突破。

---

## Documentation 文件

| Document | Description | 位置 |
|----------|-------------|------|
| **GUIDE.md** | 完整使用者指南（繁體中文 / English 雙語） | [docs/GUIDE.md](docs/GUIDE.md) |
| **stockguide.md** | Minervini SEPA 方法論完整參考（15 部分） | [docs/stockguide.md](docs/stockguide.md) |
| **START_TEACHING_NOW.md** | 實戰教學快速開始（新！） | [docs/START_TEACHING_NOW.md](docs/START_TEACHING_NOW.md) |
| **GUIDE_UPDATE_SUMMARY.md** | GUIDE 更新摘要 | [docs/GUIDE_UPDATE_SUMMARY.md](docs/GUIDE_UPDATE_SUMMARY.md) |
| **VCP_OPTIMIZATION_REPORT.md** | VCP 優化分析報告 | [docs/VCP_OPTIMIZATION_REPORT.md](docs/VCP_OPTIMIZATION_REPORT.md) |
| **BACKTEST_IMPROVEMENTS.md** | 回測功能改進說明 | [docs/BACKTEST_IMPROVEMENTS.md](docs/BACKTEST_IMPROVEMENTS.md) |
| **FILE_ORGANIZATION_REPORT.md** | 檔案整理完成報告 | [docs/FILE_ORGANIZATION_REPORT.md](docs/FILE_ORGANIZATION_REPORT.md) |
| **trader_config.py** | 所有參數定義及行內註解 | [trader_config.py](trader_config.py) |
| **.github/copilot-instructions.md** | GitHub Copilot AI 輔助開發指引 | [.github/copilot-instructions.md](.github/copilot-instructions.md) |
| **.github/instructions/** | 按檔案類型自動套用的編碼規則 | [.github/instructions/](.github/instructions/) |
| **.github/prompts/** | 可重用的開發工作流程斜線指令 | [.github/prompts/](.github/prompts/) |
| **.github/agents/** | 專業化 AI 代理（規劃、審查、交易邏輯） | [.github/agents/](.github/agents/) |
| **.github/hooks/** | 儲存後自動品質檢查 | [.github/hooks/](.github/hooks/) |

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

## Project Structure 專案結構

```
SEPA-StockLab/
├── app.py                       # Flask Web 應用入口
├── minervini.py                 # CLI 命令行入口
├── trader_config.py             # 所有 Minervini 參數配置
├── run_app.py                   # 應用啟動器
├── start_web.py                 # Web 啟動器
├── requirements.txt             # Python 依賴列表
│
├── modules/                     # 核心商業邏輯模組
│   ├── data_pipeline.py         # 唯一資料存取層
│   ├── db.py                    # DuckDB 持久層
│   ├── screener.py              # 3 階段掃描漏斗
│   ├── stock_analyzer.py        # 個股深度分析
│   ├── vcp_detector.py          # VCP 自動偵測
│   ├── rs_ranking.py            # 相對強度排名
│   ├── market_env.py            # 市場環境分類
│   ├── position_monitor.py      # 倉位追蹤
│   ├── watchlist.py             # 觀察清單管理
│   └── report.py                # 報告生成
│
├── templates/                   # Jinja2 HTML 模板
│   ├── base.html                # 基礎佈局
│   ├── dashboard.html           # 儀表板首頁
│   ├── scan.html                # 掃描頁面
│   ├── analyze.html             # 個股分析
│   ├── watchlist.html           # 觀察清單
│   ├── positions.html           # 倉位管理
│   ├── market.html              # 市場環境
│   ├── vcp.html                 # VCP 偵測結果
│   ├── backtest.html            # 回測分析
│   └── guide.html               # 使用指南
│
├── tests/                       # 單元測試與集成測試
│   ├── test_*.py
│   └── ...
│
├── scripts/                     # 維護與分析腳本
│   ├── verify_*.py              # 驗證指令碼
│   ├── fix_*.py                 # 修復工具
│   └── ...
│
├── docs/                        # 文檔和指南
│   ├── GUIDE.md                 # 使用者完整指南
│   ├── stockguide.md            # SEPA 方法論詳解
│   ├── START_TEACHING_NOW.md    # 實戰教學
│   ├── VCP_OPTIMIZATION_REPORT.md
│   ├── BACKTEST_IMPROVEMENTS.md
│   ├── FILE_ORGANIZATION_REPORT.md
│   └── ...
│
├── data/                        # 運行時資料（不提交）
│   ├── sepa_stock.duckdb        # DuckDB 主要存儲
│   ├── price_cache/             # 價格快取
│   ├── db_backups/              # 雙寫備份
│   └── ...
│
├── logs/                        # 執行日誌（不提交）
│   └── *.log
│
├── reports/                     # 分析報告（不提交）
│   └── *.csv
│
├── .github/                     # GitHub Copilot AI 配置
│   ├── copilot-instructions.md  # 始終開啟的指引
│   ├── instructions/            # 檔案類型規則
│   ├── prompts/                 # 斜線指令
│   ├── agents/                  # 自訂 AI 代理
│   └── hooks/                   # 品質檢查鉤子
│
└── README.md                    # 本檔案 ← 專案首頁
```

---

## Getting Started 開始使用

1. **安裝依賴**
   ```bash
   pip install -r requirements.txt
   ```

2. **啟動網頁介面**
   ```bash
   python app.py
   ```
   訪問 http://localhost:5000

3. **首次掃描**
   - 前往 `/scan` 頁面
   - 點擊「Run Scan」執行三階段篩選
   - 查看結果並點擊個股進行深度分析

4. **閱讀完整指南**
   - 訪問 `/guide` 查看完整 GUIDE.md
   - 查看 [docs/START_TEACHING_NOW.md](docs/START_TEACHING_NOW.md) 快速上手

---

## License

For personal use. Trading involves risk — this tool is for educational and research purposes only.

僅供個人使用。投資有風險，本工具僅供教育與研究用途。

---

**最後更新：2026-02-28**  
**版本：1.0**  
**維護者：GitHub Copilot**
