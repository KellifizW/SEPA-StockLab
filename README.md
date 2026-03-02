# SEPA-StockLab

**多策略選股系統 — Multi-Strategy Stock Screening & Portfolio Management Tool**

整合三套獨立交易方法論，對全美股市進行系統性篩選與分析：

| 策略 | 方法論 | 類型 |
|------|--------|------|
| **SEPA (Minervini)** | Mark Minervini《Trade Like a Stock Market Wizard》| 三階段漏斗篩選 + 五大支柱評分 |
| **QM (Qullamaggie)** | Kristjan Kullamägi 突破擺盪交易系統 | 純技術，ADR 獨立否決，6 維星級評分 |
| **ML (Martin Luk)** | Martin Luk 回調擺盪交易系統 | EMA 結構，AVWAP 支撐/阻力，7 維星級評分 |

A full-stack stock screening tool implementing three independent trading strategies, VCP detection & backtesting, position tracking, and market regime classification. Dual interface: Web UI (Flask) + CLI.

---

## Features 功能

| Feature | Description |
|---------|-------------|
| **SEPA 3-Stage Scan** | finvizfinance/NASDAQ FTP 粗篩 → TT1-TT10 趨勢模板驗證 → SEPA 五大支柱評分 |
| **QM 3-Stage Scan** | ADR 獨立否決 + 動能確認 → 整理形態/MA 排列/更高低點 → 6★ 星級評分 |
| **ML 3-Stage Scan** | EMA 結構篩選 + AVWAP 確認 → 回調深度/量能乾涸 → 7★ 星級評分 |
| **Combined Scanner** | SEPA + QM 平行掃描，共用單次 universe 抓取，節省 ~40-60% 時間 |
| **VCP Detection** | 自動偵測 Volatility Contraction Pattern（波動收縮形態） |
| **VCP Backtester** | 2 年歷史資料走前向回測，零前瞻偏差 |
| **Single-Stock Analysis** | SEPA: BUY/WATCH/AVOID；QM: 6★；ML: 7★ 深度分析 |
| **Interactive Charts** | 每日/每週/盤中 K 線 + 技術指標疊加（SMA/EMA/RSI/ATR/BBands） |
| **Position Monitor** | 14 項健康信號檢查、Minervini 移動停損 |
| **Watchlist** | A/B/C 評級自動分級、HTMX 局部更新 |
| **Market Environment** | SPY/QQQ/IWM 市場制度分類、分佈日計數、板塊輪動 |
| **RS Ranking** | IBD 風格相對強度百分位排名 |
| **DuckDB Persistence** | 歷史掃描/RS/市場/倉位/觀察清單持久化及趨勢圖表 |
| **Dual Interface** | Web UI (Flask port 5000) + CLI (argparse) |

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
# Opens http://localhost:5000 automatically
```

### Run CLI 命令列操作

```bash
# SEPA scan
python minervini.py scan

# QM (Qullamaggie) scan
python minervini.py qm-scan

# ML (Martin Luk) scan
python minervini.py ml-scan

# Analyze a single stock (SEPA)
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

# VCP backtest
python minervini.py backtest NVDA

# RS ranking
python minervini.py rs top --min 85

# Daily routine (scan + market + positions)
python minervini.py daily

# Generate HTML report
python minervini.py report
```

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
| `nasdaq_universe.py` | **免費替代宇宙** — NASDAQ FTP 無需 API key，24h 快取，~2-4 min |
| `screener.py` | 三階段 SEPA 掃描漏斗（Stage 1→2→3）、TT 驗證、五大支柱評分 |
| `stock_analyzer.py` | 深度 SEPA 個股分析 → BUY/WATCH/AVOID |
| `qm_screener.py` | 三階段 QM 突破掃描漏斗 — 純技術，ADR 否決 |
| `qm_analyzer.py` | QM 6 維星級評分引擎（Qullamaggie 方法論第 6 節） |
| `qm_setup_detector.py` | QM 形態偵測輔助函式 |
| `qm_position_rules.py` | QM 倉位管理規則 |
| `ml_screener.py` | 三階段 ML 回調掃描漏斗 — EMA + AVWAP |
| `ml_analyzer.py` | ML 7 維星級評分引擎（EMA 結構、回調品質、AVWAP 匯合） |
| `ml_setup_detector.py` | ML 形態偵測輔助函式 |
| `ml_position_rules.py` | ML 倉位管理規則（最大停損 2.5%） |
| `combined_scanner.py` | 合併掃描：SEPA + QM 平行，單次抓取宇宙 |
| `rs_ranking.py` | IBD 相對強度百分位排名引擎 |
| `vcp_detector.py` | VCP 自動偵測 — 波動收縮、ATR/BBands、量能乾涸 |
| `backtester.py` | 走前向 VCP 回測引擎（2 年歷史，無前瞻偏差） |
| `market_env.py` | 市場制度分類（確認上漲 → 上漲受壓 → 修正 → 下跌） |
| `position_monitor.py` | 倉位追蹤、14 項健康信號、Minervini 移動停損 |
| `watchlist.py` | A/B/C 等級觀察名單 |
| `report.py` | HTML 報告、CSV 匯出、終端表格 |

---

## Configuration 配置

All strategy parameters are centralized in `trader_config.py`:

所有策略參數集中在 `trader_config.py`：

| Group | Prefix | Examples |
|-------|--------|----------|
| Account & Risk | — | `ACCOUNT_SIZE`, `MAX_RISK_PER_TRADE_PCT` |
| Stop Loss | — | `MAX_STOP_LOSS_PCT`, `ATR_STOP_MULTIPLIER` |
| Trend Template | `TT_` | `TT_SMA_PERIODS`, `TT9_MIN_RS_RANK` |
| Fundamentals | `F1_`–`F8_` | `F1_MIN_EPS_QOQ_GROWTH`, `F8_MIN_ROE` |
| VCP | `VCP_` | `VCP_MIN_CONTRACTIONS`, `VCP_MIN_BASE_WEEKS` |
| RS Ranking | `RS_` | `RS_WEIGHT_3M`, `RS_WEIGHT_12M` |
| Database (DuckDB) | `DB_` | `DB_FILE`, `DB_JSON_BACKUP_ENABLED` |
| SEPA Weights | `W_` | `W_TREND`, `W_FUNDAMENTAL` |
| QM (Qullamaggie) | `QM_` | `QM_MIN_ADR_PCT`, `QM_MIN_DOLLAR_VOL` |
| ML (Martin Luk) | `ML_` | `ML_MAX_STOP_PCT`, `ML_MAX_RISK_PCT` |

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

## Web Routes 網頁路由

| Route | Page | Description |
|-------|------|-------------|
| `/` | Dashboard | 首頁儀表板 |
| `/scan` | SEPA Scan | Minervini 三階段掃描 |
| `/qm/scan` | QM Scan | Qullamaggie 突破掃描 |
| `/ml/scan` | ML Scan | Martin Luk 回調掃描 |
| `/combined` | Combined Scan | SEPA + QM 平行掃描 |
| `/analyze` | SEPA Analysis | SEPA 個股深度分析 |
| `/qm/analyze` | QM Analysis | QM 6★ 星級分析 |
| `/ml/analyze` | ML Analysis | ML 7★ 星級分析 |
| `/backtest` | VCP Backtest | VCP 走前向回測 |
| `/watchlist` | Watchlist | 觀察名單（HTMX 更新） |
| `/positions` | Positions | 倉位監控 |
| `/market` | Market Env | 市場環境 |
| `/vcp` | VCP Detect | VCP 即時偵測 |
| `/guide` | User Guide | GUIDE.md |
| `/qm/guide` | QM Guide | Qullamaggie 方法論 |
| `/ml/guide` | ML Guide | Martin Luk 方法論 |

---

## Methodology 方法論

### SEPA (Minervini) 三階段漏斗

1. **Stage 1 — Coarse Filter**：finvizfinance/NASDAQ FTP 篩選（price > $10, volume > 200K, ROE > 10%）
2. **Stage 2 — Trend Template**：TT1-TT10 驗證（SMA 排列、52 週位置、RS 排名）
3. **Stage 3 — SEPA Scoring**：五大支柱加權評分 → 最終排名清單

#### Trend Template (TT1-TT10) 趨勢模板

- TT1: Price > SMA50 > SMA150 > SMA200
- TT4: SMA200 上升 ≥22 天
- TT7: 價格 ≥ 52 週低點 25% 以上
- TT8: 價格距 52 週高點 25% 以內
- TT9: RS ≥ 70 百分位

完整定義見 [docs/stockguide.md](docs/stockguide.md)。

### QM (Qullamaggie) 突破擺盪

- 純技術，ADR 獨立否決權（低於 `QM_MIN_ADR_PCT` 則拒絕）
- 6 維星級評分（Momentum Quality, ADR Level, Consolidation Quality, MA Alignment, Stock Type, Market Timing）
- 星級 → 倉位大小：5+★ → 20-25%, 5★ → 15-25%, 4-4.5★ → 10-15%, 3-3.5★ ≤10%, <3★ → PASS

完整方法論見 [docs/QullamaggieStockguide.md](docs/QullamaggieStockguide.md)。

### ML (Martin Luk) 回調擺盪

- EMA 結構（9>21>50>150 排列）+ AVWAP 支撐/阻力
- 7 維星級評分（EMA Structure, Pullback Quality, AVWAP Confluence, Volume Pattern, Risk/Reward, RS, Market Env）
- 最大停損 2.5%，風險/易 0.50%

完整方法論見 [docs/MartinLukStockGuidePart1.md](docs/MartinLukStockGuidePart1.md)。

### VCP (Volatility Contraction Pattern) 波動收縮形態

逐步收緊的價格波動（T-2, T-3, T-4+），伴隨量能乾涸，在 pivot 附近突破。支持走前向回測驗證信號有效性。

---

## Documentation 文件

| Document | Description | 位置 |
|----------|-------------|------|
| **GUIDE.md** | 完整使用者指南（繁體中文 / English 雙語） | [docs/GUIDE.md](docs/GUIDE.md) |
| **stockguide.md** | Minervini SEPA 方法論完整參考（15 部分） | [docs/stockguide.md](docs/stockguide.md) |
| **QullamaggieStockguide.md** | Qullamaggie 突破交易方法論 | [docs/QullamaggieStockguide.md](docs/QullamaggieStockguide.md) |
| **MartinLukStockGuidePart1/2.md** | Martin Luk 回調交易方法論 | [docs/MartinLukStockGuidePart1.md](docs/MartinLukStockGuidePart1.md) |
| **trader_config.py** | 所有策略參數定義及行內註解 | [trader_config.py](trader_config.py) |
| **.github/copilot-instructions.md** | GitHub Copilot AI 輔助開發指引 | [.github/copilot-instructions.md](.github/copilot-instructions.md) |

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
| `/trading-verify` | Verify all three strategy logic correctness (SEPA/QM/ML) |

### Custom Agents 自訂代理

| Agent | Role |
|-------|------|
| **Planner** | Read-only feature planning — creates implementation blueprints |
| **Code Reviewer** | Reviews code quality, security, trading logic accuracy |
| **Trading Expert** | SEPA / QM / ML methodology specialist |

---

## Tech Stack 技術棧

- **Python 3.10+** — Core language
- **Flask 3.0** — Web server + JSON API + Jinja2
- **Bootstrap 5.3** — Dark theme UI (CDN, no build step)
- **HTMX** — Partial-page updates for watchlist/positions (CDN)
- **finvizfinance** — Coarse stock screening (primary)
- **NASDAQ FTP** — Free alternative universe (no API key, 24h cache)
- **yfinance** — OHLCV & fundamentals
- **pandas_ta** — Technical indicators (SMA, EMA, RSI, ATR, BBands, AVWAP)
- **pandas / numpy** — Data processing
- **pyarrow** — Parquet I/O
- **duckdb** — Analytical SQL for persistent historical data
- **tabulate** — Terminal table formatting
- **ThreadPoolExecutor** — Parallel scan execution

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
   - QM 方法論：訪問 `/qm/guide`
   - ML 方法論：訪問 `/ml/guide`

---

## License

For personal use. Trading involves risk — this tool is for educational and research purposes only.

僅供個人使用。投資有風險，本工具僅供教育與研究用途。

---

**最後更新：2026-03-02**  
**版本：2.0（三策略版）**
