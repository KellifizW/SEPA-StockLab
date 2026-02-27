# Minervini SEPA StockLab — 完整使用指南 / Complete User Guide

> 本程式依照 Mark Minervini 的 **SEPA® 方法論**（Specific Entry Point Analysis）設計，
> 結合 `finvizfinance`、`yfinance` 及 `pandas_ta` 三個資料庫，實現完整的 SEPA 篩選流程。
> 所有歷史掃描、RS 排名、市場環境、倉位及觀察清單均持久化至 **DuckDB**（`data/sepa_stock.duckdb`）。

---

## 快速開始 / Quick Start

### 1. 安裝依賴套件

```bash
cd C:\Users\t-way\Desktop\stocklab
pip install -r requirements.txt
```

### 2. 啟動 Web 介面（推薦）

```bash
python app.py
```

瀏覽器開啟 → **http://localhost:5000**

### 3. 使用命令列（可選）

```bash
python minervini.py --help
```

---

## Web 介面各頁面說明

### 📊 Dashboard（首頁）

**路徑：** `http://localhost:5000/`

Dashboard 是你每天開盤前的一覽總覽頁面，顯示：

| 版塊 | 內容 |
|------|------|
| **指標卡片** | 持倉數量、A 級觀察清單數量、帳戶規模 |
| **持倉表格** | 所有開倉股票的入場價、止損、目標、R:R |
| **A 級觀察清單** | 最優質的候選股票快覽 |
| **快速操作** | 一鍵啟動掃描、市場評估、個股分析 |

**每日使用流程：**
1. 查看 Dashboard 確認目前倉位狀況
2. 點擊「Market Check」查看市場環境
3. 點擊「Run SEPA Scan」篩選候選股
4. 點擊「Generate Report」生成 HTML 報告

---

### 🔍 Scan（SEPA 三階段篩選）

**路徑：** `http://localhost:5000/scan`

這是系統核心功能。點擊 **Run Scan** 後自動執行三個階段：

#### Stage 1 — 粗篩（finvizfinance）

使用 finvizfinance 過濾全美上市股票。條件包括：
- 股價 > $10
- 日均成交量 > 200,000 股
- 美國本地上市股票
- 股價高於 SMA20、SMA50、SMA200
- EPS 季度增長 > 25%
- 銷售額增長 > 20%
- ROE（股東回報率） > 10%

> 通常從 8,000+ 隻股票縮窄至 100-400 隻候選股

#### Stage 2 — Minervini 趨勢模板（TT1-TT10）精確驗證

對每隻 Stage 1 通過的股票，下載 2 年日線數據，用 `pandas_ta` 計算，逐一驗證：

| 條件 | 說明 |
|------|------|
| **TT1** | 收盤價 > SMA150 |
| **TT2** | 收盤價 > SMA200 |
| **TT3** | SMA150 > SMA200 |
| **TT4** | SMA200 斜率向上（與 22 日前比較） |
| **TT5** | SMA50 > SMA150 且 SMA50 > SMA200 |
| **TT6** | 收盤價 > SMA50 |
| **TT7** | 股價比 52 週低位高 ≥ 25% |
| **TT8** | 股價在 52 週高位的 25% 以內 |
| **TT9** | RS 排名 ≥ 70（跨市場百分位排名） |
| **TT10** | 板塊在過去 1 週表現前 35% |

> **10 個條件必須同時全部通過**，才進入 Stage 3

#### Stage 3 — SEPA 五維度評分

通過 TT 的股票進行 0-100 分深度評分：

| 維度 | 權重 | 評估內容 |
|------|------|----------|
| **趨勢** | 30% | 10 個 TT 條件完成度 + RS 排名 |
| **基本面** | 25% | EPS 加速、盈利驚喜、機構持倉 |
| **催化劑** | 15% | 分析師評級、EPS 修訂 |
| **入場（VCP）** | 20% | VCP 形態評分 + 成交量乾涸 |
| **風險/回報** | 10% | ATR 止損比例、R:R 比率 |

**結果表格功能：**
- 點擊列頭排序
- 右上角篩選框過濾 Ticker / 板塊
- 點擊 📌 添加到觀察清單
- 點擊 📊 跳轉 VCP 分析
- 點擊 **CSV** 匯出 Excel

> ⚠️ 首次運行大約需要 3-8 分鐘（需下載大量價格數據）。
> 後續運行因本地 parquet 緩存加速，約 2-3 分鐘。

---

### 🔬 Analyze（個股深度分析）

**路徑：** `http://localhost:5000/analyze`

輸入任意美股 Ticker，執行完整 SEPA 分析。結果包括：

#### SEPA 評分總覽
- 綜合評分（0-100）
- RS 排名（1-99，越高越好）
- VCP 等級（A/B/C/D）
- 行動建議（BUY / WATCH / MONITOR / AVOID）

#### Trend Template 核對表
所有 10 個 TT 條件逐一顯示通過/不通過狀態。

#### 五維度柱形圖
用進度條顯示每個 SEPA 維度的得分。

#### VCP 分析框
- 是否有效 VCP
- T 字形態數量（每個「T」代表一次收縮）
- 底部寬度（週數）和深度（跌幅%）
- Pivot（支點）買入價
- ATR 收縮、Bollinger Band 收縮、成交量乾涸三項信號

#### 基本面核對表
- F1: EPS 季度加速增長
- F2: 盈利驚喜（超過分析師預期）
- F3: 年度 EPS 增長
- F5: 銷售收入增長
- F7: 機構持倉增加
- F8: ROE / 利潤率

#### 倉位計算
基於 ATR 自動計算：
- **入場價**（Pivot 或當前價）
- **止損價**（ATR × 2.0）
- **建議股數**（最大風險 1.5% 帳戶）
- **倉位市值** 和 **風險金額**
- **目標價**（默認 R:R = 3:1）

點擊 **Add to Positions** 按鈕可直接跳轉至持倉頁面預填數據。

**行動建議邏輯：**

| 建議 | 條件 |
|------|------|
| **BUY** | 全部 TT 通過 + SEPA ≥ 75 + 有效 VCP + 在 Pivot 上方 3% 以內 |
| **WATCH** | 全部 TT 通過 + SEPA ≥ 60 |
| **MONITOR** | 大部分 TT 通過 + 正在整固 |
| **AVOID** | TT 失敗多項 或 SEPA < 40 |

---

### ⭐ Watchlist（觀察清單）

**路徑：** `http://localhost:5000/watchlist`

系統將股票分為三個等級，自動管理。

#### 自動等級劃分

| 等級 | 條件 | 最大數量 |
|------|------|---------|
| **Grade A** | 通過所有 TT + 有效 VCP + RS ≥ 80 | 8 隻 |
| **Grade B** | 通過所有 TT + RS ≥ 70 | 20 隻 |
| **Grade C** | 其他符合趨勢的股票 | 50 隻 |

#### 操作說明

**添加股票：**
1. 輸入 Ticker（例如 `NVDA`）
2. Grade 選擇「Auto」（系統自動評級）或手動指定
3. 可填入備注（例如「VCP breakout pending」）
4. 點擊 **Add** — 系統自動下載數據並分析（約 30-60 秒）

**升降等級（手動）：**
- ↑ 按鈕：升至更高等級
- ↓ 按鈕：降至更低等級

**刷新全部（Refresh All）：**
- 重新分析所有清單中的股票
- 根據最新數據自動重新分級
- 顯示等級變動摘要

**Tab 切換：**
- All / Grade A / Grade B / Grade C 分開顯示

---

### 💼 Positions（持倉監控）

**路徑：** `http://localhost:5000/positions`

#### 添加倉位

展開「Add New Position」表單，填入：
- **Ticker**：股票代號
- **Entry Price**：買入價格
- **Shares**：股數
- **Stop Loss**：初始止損價（絕對價格）
- **Target**：目標價（選填，默認 R:R = 3:1 自動計算）
- **Note**：備注（如「VCP breakout」）

#### 每日健康檢查（Daily Health Check）

點擊右上角 **Daily Health Check** 按鈕，系統對每個持倉進行：

1. **止損觸發檢查** — 當前價是否跌破止損
2. **SMA50 保護線** — 是否跌穿 SMA50（1% 緩衝）
3. **派發量成交** — 大量放量下跌是否出現
4. **過度擴張警告** — 股價超越 SMA50 逾 25%（頂部風險）
5. **勝利轉虧損** — 曾盈利後跌回成本
6. **時間止損** — 持倉 35 天仍未達 5% 收益

每個持倉顯示：
- 當前盤價和盈虧%
- 距止損的緩衝空間
- **追蹤止損建議** — 根據 Minervini 的盈利-回撤表自動計算最新止損位

#### 追蹤止損邏輯（Minervini 方法）

| 盈利達到 | 最大允許回撤 | 行動 |
|---------|------------|------|
| +5% | 0% | 止損移至成本 |
| +10% | 7% | 從最高點允許 7% 回撤 |
| +15% | 10% | 從最高點允許 10% 回撤 |
| +20% | 12% | 從最高點允許 12% 回撤 |
| +25% | 15% | 保守追蹤 |
| +50%+ | 自由裁量 | 緊追 SMA20 |

#### 平倉

點擊「Close」按鈕，輸入退出價格和原因（如「Stop hit」、「Target reached」、「Climax top」）。
系統自動計算盈虧金額並記錄到交易歷史。

---

### 📈 Market（市場環境）

**路徑：** `http://localhost:5000/market`

點擊 **Assess Market** 分析四大指數（SPY、QQQ、IWM），輸出：

#### 市場環境等級

| 等級 | 說明 | 行動 |
|------|------|------|
| 🟢 **BULL_CONFIRMED** | 全面牛市 | 積極全倉做多 |
| 🟢 **BULL_UNCONFIRMED** | 牛市但需確認 | 選擇性買入 |
| 🟡 **TRANSITION** | 轉型期 | 縮小倉位，只買最強設置 |
| 🔵 **BOTTOM_FORMING** | 底部形成中 | 試探性小倉，等跟進日 |
| 🟡 **BEAR_RALLY** | 熊市反彈 | 防性守，短期操作 |
| 🔴 **BEAR_CONFIRMED** | 確認熊市 | 不做新多，管理/退出現有倉位 |

#### 關鍵指標說明

**派發日（Distribution Days）：**
- 在過去 25 個交易日，S&P500 收盤下跌 ≥ 0.2% 且成交量高於前一日的天數
- ≥ 5 個派發日 → 警告，市場承壓
- ≥ 6 個派發日 → 嚴重警告

**市場廣度（Breadth）：**
- 測量有多少%美股股價在 SMA200 之上
- ≥ 60% → 健康牛市
- 40-60% → 中性
- < 40% → 市場弱勢

**新高/新低比率（NH/NL Ratio）：**
- 創 52 週新高的股票數 ÷ (新高 + 新低)
- > 60% → 新高主導，強勢
- < 40% → 新低主導，弱勢

---

### 📉 VCP（波動收縮形態分析）

**路徑：** `http://localhost:5000/vcp`

輸入任意 Ticker，分析 VCP（Volatility Contraction Pattern）形態。

#### VCP 評分系統（0-100 分）

| 項目 | 分值 |
|------|------|
| T 字數量（每個 +15，最多 30 分） | 0-30 |
| 收縮序列確認 | +20 |
| ATR 收縮（後半段 < 前半段 85%） | +15 |
| Bollinger Band 收縮 | +15 |
| 成交量乾涸（近 5 日 < 50 日均量 50%） | +15 |
| 底部深度（10-25% 理想） | +5 |
| 底部寬度（適當週數） | +5 |
| 最終收縮確認 | +5 |

#### VCP 等級對應

| 等級 | 分數 | 描述 |
|------|------|------|
| **A** | ≥ 75 | 完美 VCP，高確信度突破候選 |
| **B** | 55-74 | 良好 VCP，可考慮建倉 |
| **C** | 35-54 | 初步形成，繼續觀察 |
| **D** | < 35 | 不符合 VCP，避免交易 |

#### Pivot 買入點

系統自動計算 Pivot（支點）= 底部最後 25% 區域的最高價。
買入區間 = Pivot 至 Pivot + 3%（不超過此範圍追買）。

---

## 命令列使用說明（CLI）

如果不想使用 Web 介面，可以直接使用命令列：

```bash
# 查看幫助
python minervini.py --help

# 執行 SEPA 掃描（顯示前 30 名）
python minervini.py scan

# 執行掃描 + 同時強制刷新 RS 排名（每週做一次）
python minervini.py scan --refresh-rs

# 深度分析單隻股票
python minervini.py analyze NVDA

# 以指定帳戶規模分析（影響倉位計算）
python minervini.py analyze AAPL --account 50000

# 市場環境評估
python minervini.py market

# 觀察清單：列出全部
python minervini.py watchlist list

# 觀察清單：添加股票
python minervini.py watchlist add NVDA

# 觀察清單：刷新全部分析
python minervini.py watchlist refresh

# 觀察清單：升/降等級
python minervini.py watchlist promote NVDA
python minervini.py watchlist demote NVDA

# 持倉：列出全部
python minervini.py positions list

# 持倉：添加新倉位（格式：TICKER 買入價 股數 止損 [目標]）
python minervini.py positions add NVDA 500.00 10 480.00 560.00

# 持倉：每日健康檢查
python minervini.py positions check

# 持倉：更新追蹤止損
python minervini.py positions update NVDA 510.00

# 持倉：平倉
python minervini.py positions close NVDA 560.00

# VCP 分析
python minervini.py vcp NVDA

# RS 排名：顯示前 80 名
python minervini.py rs top --min 80

# 每日完整流程（市場評估 + 持倉檢查 + 清單刷新 + 報告）
python minervini.py daily
```

---

## 系統配置（trader_config.py）

打開 `trader_config.py` 可修改所有參數：

```python
# 帳戶設置
ACCOUNT_SIZE         = 100_000    # 帳戶規模（美元）
MAX_RISK_PER_TRADE   = 1.5        # 每筆最大風險（帳戶%）
MAX_POSITION_SIZE    = 20.0       # 單倉最大比例（帳戶%）
MAX_OPEN_POSITIONS   = 6          # 最多同時開倉數

# RS 排名權重
RS_WEIGHTS = {
    "3m":  0.40,   # 最近 3 個月 40% 權重
    "6m":  0.20,   # 6 個月 20%
    "9m":  0.20,   # 9 個月 20%
    "12m": 0.20,   # 12 個月 20%
}

# VCP 識別參數
VCP_MIN_BASE_WEEKS    = 4      # 最短底部週數
VCP_MAX_BASE_WEEKS    = 65     # 最長底部週數
VCP_MAX_BASE_DEPTH    = 40.0   # 最大底部深度（%）
VCP_VOLUME_DRY_THRESHOLD = 0.50  # 成交量乾涸閾值（50 日均量的 50%）

# SEPA 評分五個維度的權重
SEPA_WEIGHTS = {
    "trend":        0.30,
    "fundamental":  0.25,
    "catalyst":     0.15,
    "entry":        0.20,
    "risk_reward":  0.10,
}
```

---

## 數據說明

### 緩存機制

為避免重複請求導致速率限制，系統採用三層緩存：

| 類型 | 路徑 | 有效期 |
|------|------|--------|
| 股票日線數據 | `data/price_cache/*.parquet` | 1 天 |
| RS 排名 | `data/rs_cache.csv` | 1 天 |
| **觀察清單** | **DuckDB `watchlist_store`**（備份：`data/watchlist.json`） | 永久（主機資料庫） |
| **持倉記錄** | **DuckDB `open_positions`**（備份：`data/positions.json`） | 永久（直至平倉） |
| **掃描歷史** | **DuckDB `scan_history`** | 永久（用於趨勢圖表） |
| **RS 歷史** | **DuckDB `rs_history`** | 永久（用於排名趨勢） |
| **市場環境歷史** | **DuckDB `market_env_history`** | 永久（用於制度追蹤） |
| 日 JSON 備份 | `data/db_backups/` | 永久（DuckDB 雙寫安全層） |

### 數據來源

| 功能 | 數據來源 |
|------|----------|
| 初篩（Stage 1） | finvizfinance |
| 歷史價格、計算 SMA | yfinance |
| SMA、ATR、RSI、BBands | pandas_ta（本地計算）|
| RS 宇宙建立 | finvizfinance screener |
| 基本面數據 | yfinance（seasonal financials）|
| 板塊排名 | finvizfinance Group |

---

## 每日操作流程建議

### 開盤前（9:00 AM）

```
1. python app.py            ← 啟動 Web 介面
2. 開啟 Market 頁面 → Assess Market
3. 確認市場環境等級
4. 開啟 Positions → Daily Health Check
5. 執行觀察清單 Refresh
```

### 收盤後（4:30 PM）

```
1. 開啟 Scan 頁面 → Run Scan
2. 對高分股票使用 VCP 頁面深入分析
3. 符合 Grade-A 條件的加入 Watchlist
4. 點擊 Dashboard → Generate Report 保存記錄
```

### 每週一次

```
python minervini.py scan --refresh-rs
```
（重建 RS 排名資料庫，約 8-12 分鐘）

---

## 常見問題

**Q: 掃描需要多久？**
A: 首次約 5-8 分鐘（需下載大量數據）。之後因 parquet 緩存，約 2-3 分鐘。

**Q: 有每日或每小時的 API 抓取限制嗎？**
A: 是的。系統使用外部免費 API，各有速率限制：

| API 來源 | 主要用途 | 限制策略 | 建議用法 |
|---------|--------|---------|--------|
| **yfinance** | 股價、基本面 | 每小時上限（不公開） | 利用每日快取；避免同時多個掃描 |
| **finvizfinance** | 篩選器、產業 | 1.2 秒 polite delay | 4 小時快取 + 自動降速 |
| **pandas_ta** | 技術指標 | 無限制（本地運算） | 未受限 |

**最佳實踐：**
- ✅ **每日掃描多次**: 同日重複掃描 → 利用快取 → 2-5 分鐘
- ✅ **首日掃描**: 允許 7-12 分鐘（一次性下載）
- ⚠️ **避免並行掃描**: 連續掃描無妨，但同時多個掃描可能觸發限制
- ❌ **遇到 HTTP 429**: 系統自動降速重試，稍候後再試
- 💡 **利用快取**: 掃描完成後，同日再掃描通常毋須重新下載

**Q: 如果收到 HTTP 429 錯誤怎麼辦？**
A: 這表示 API 臨時限速。系統會自動延遲重試。
你可以：
1. 稍候 2-3 分鐘後重新掃描
2. 搭配 `--refresh-rs` 重建 RS 排名快取
3. 檢查 logs/ 資料夾的掃描日誌，確認是否有其他錯誤

**Q: RS 排名怎麼計算？**
A: 系統從 finvizfinance 獲取約 3,000-5,000 隻美股，批量下載一年收盤價，
用加權公式計算相對強度（40% 近 3 月 + 各 20% 近 6/9/12 月），
然後轉換為百分位排名（1-99）。與 IBD 的方法原理相同。

**Q: VCP 的「T」是什麼意思？**
A: 「T」代表一次收縮（Tightening，即波幅縮小的過程）。
理想 VCP 有 3-4 個 T：每個 T 的波動幅度比上一個 T 更小，
成交量也逐步乾涸。Minervini 稱之為「3T」或「4T」形態。

**Q: 系統顯示 TT10 失敗但股票明明是強勢板塊？**
A: TT10 使用 finvizfinance 的過去 1 週板塊表現作為基準。
可以在 `trader_config.py` 修改 `TT10_SECTOR_TOP_PCT = 0.35` 放寬條件。

**Q: 如何在不同電腦使用？**
A: 所有數據保存在 `data/` 目錄（DuckDB + JSON + Parquet），
直接複製整個 `stocklab` 資料夾即可遷移。
注意：`data/sepa_stock.duckdb` 是主要資料庫，包含所有觀察清單、持倉及歷史記錄；
`data/watchlist.json` 和 `data/positions.json` 為安全備份副本。

**Q: 現有 JSON 資料如何遷移到 DuckDB？**
A: 首次升級後執行一次性遷移腳本：
```bash
python scripts/migrate_phase2.py --migrate
```
此指令會將現有 `watchlist.json` 和 `positions.json` 匯入 DuckDB，
並自動在 `data/db_backups/` 存放備份。可用 `--verify` 確認遷移成功，
或 `--rollback` 撤回至 JSON 模式。新安裝用戶無需執行此步驟。

**Q: Can I use this from another device on my network?**
A: Yes. The web server listens on `0.0.0.0:5000`, so from another device on
the same WiFi use `http://<YOUR_PC_IP>:5000`.

---

## Minervini SEPA 方法論簡介

SEPA® = **Specific Entry Point Analysis**

Mark Minervini 在其著作《Trade Like a Stock Market Wizard》和
《Think & Trade Like a Champion》中描述了這套系統。

### 五大支柱

| 支柱 | 英文 | 核心要求 |
|------|------|----------|
| 1. 趨勢（Trend） | Stage 2 Uptrend | 四條 SMA 對齊向上 |
| 2. 基本面（Fundamentals） | Earnings Acceleration | EPS 加速成長 |
| 3. 催化劑（Catalyst） | New Product / Expansion | 業務新突破 |
| 4. 入場（Entry）| VCP Breakout | 波動收縮後突破 |
| 5. 風險/回報（Risk/Reward） | Risk Management | 最小 3:1 R:R |

### Stage 2 定義（趨勢模板核心）

```
價格 > SMA50 > SMA150 > SMA200
SMA200 向上斜坡（過去 22 個交易日）
股價在 52 週高點的 25% 以內（不能太遠）
股價比 52 週低點高出 25% 以上（有足夠基礎）
RS 排名 ≥ 70（應領先市場）
```

### 風險管理原則

- **每筆最大風險**: 帳戶的 1-2%
- **單倉最大持股**: 帳戶的 20%
- **止損移至成本**: 當盈利達 +5%
- **絕不讓贏家變輸家**: Minervini 核心原則
- **帳戶內 7% 回撤**: 停止新買入，審查持倉

---

*本程式僅供教育用途。所有分析結果不構成投資建議。*
*股票市場存在風險，投資前請充分了解相關風險。*

---

**版本**: 2.0 &nbsp;|&nbsp; **日期**: 2026-02-27 &nbsp;|&nbsp;
**數據**: finvizfinance + yfinance + pandas_ta + DuckDB
