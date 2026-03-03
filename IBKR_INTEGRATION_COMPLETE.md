# IBKR API 整合 — 實施完成摘要

**統計時間:** 2026-03-04  
**版本:** Phase 1 & 2 —— 後端 + 前端核心  
**狀態:** ✅ 完成，可測試

---

## 已完成的工作

### Phase 1 — 後端基礎設施 (Backend Foundation)

#### ✅ Step 1: 環境變數配置 (`.env` + `trader_config.py`)
- **文件修改:** `.env`
  - 新增 IBKR 完整設定群組
  - 支援 TWS 和 IB Gateway 選擇
  - 包括端口、連線模式、帳號、唯讀模式等
  
- **文件修改:** `trader_config.py` (Line ~966)
  - 新增 `IBKR_ENABLED` 主開關（預設 True）
  - 從 `.env` 讀取所有敏感設定
  - 根據 `IBKR_CONNECTION_MODE` 自動選擇正確的 host/port
  - 參數包括連線逾時、同期間隔、報價快取期限

#### ✅ Step 2: IBKR 客戶端模組 (`modules/ibkr_client.py`)  
- **新建模組:** `modules/ibkr_client.py` (~1,060 行)
- **功能清單:**
  - `connect() / disconnect()` — 連接/斷開 IBKR
  - `get_status()` — 帳號摘要（NET LIQ、淨值、未實現 P&L）
  - `get_positions()` — 取得所有持倉
  - `get_executions(days)` — 近期成交記錄
  - `get_open_orders()` — 待執行訂單
  - `place_order()` — 下單（MKT / LMT / STP / TRAIL）
  - `cancel_order()` — 取消訂單
  - `get_quote()` — 實時報價快照（Bid/Ask/Last）
- **設計特色:**
  - 執行緒安全（`_ib_lock`）
  - 異步事件迴圈整合（ib_insync asyncio）
  - 所有方法同步包裝（Flask 相容）
  - 報價快取（15秒）

#### ✅ Step 3: 相依套件 (`requirements.txt`)
- 新增 `ib_insync>=0.9.86`

#### ✅ Step 4: 資料庫持久化 (`modules/db.py`)
- **新增表:** `ibkr_orders` (14 欄)
  - order_id, order_time, ticker, action, order_type
  - qty, limit_price, aux_price, trail_pct
  - fill_price, status, commission, pnl, note
- **新增函式:**
  - `append_ibkr_order(order_dict)` — 記錄訂單
  - `query_ibkr_orders(days)` — 查詢歷史
- **索引:** `idx_ibkr_orders_ticker` (ticker × order_time)

#### ✅ Step 5: Flask API 路由 (`app.py`)
- **新增 8 個路由** (Line ~4780-4960)

| 路由 | 方法 | 功能 |
|------|------|------|
| `/api/ibkr/status` | GET | 連線狀態 + 帳號摘要 |
| `/api/ibkr/connect` | POST | 連接 IBKR |
| `/api/ibkr/disconnect` | POST | 斷開連接 |
| `/api/ibkr/positions` | GET | 取得倉位 + 同期本地 |
| `/api/ibkr/orders` | GET | 待執行訂單 |
| `/api/ibkr/trades` | GET | 成交記錄 |
| `/api/ibkr/order` | POST | 下單 |
| `/api/ibkr/order/<id>` | DELETE | 取消訂單 |
| `/api/ibkr/quote/<ticker>` | GET | 報價快照 |

### Phase 2 — 前端用戶介面 (Frontend UI)

#### ✅ Step 6: 全域交易抽屜 (`templates/base.html`)

**Navbar 按鈕** (Line ~205)
- IBKR 交易按鈕（`data-bs-toggle="offcanvas"`）
- 連線狀態指示燈（紅/綠圓點）
- 雙語文本：「交易 / Trade」

**Offcanvas HTML 結構** (~420 行)
- **Offcanvas Header**
  - 標題 + 連線狀態子標題
  - 連接 / 斷開按鈕
  - 關閉按鈕

- **Offcanvas Body（3 個 Tab）**

  **Tab 1: 交易 (Trade)**
  - 股票搜尋列（防抖 400ms）→ 實時報價卡片
  - 訂單表單：
    - 方向切換 (BUY / SELL 按鈕群組)
    - 股票代碼、股數、訂單類型（下拉）
    - 動態條件欄位：Limit Price / Stop Price / Trail %
    - 備註欄位
    - 有確認 Modal（下單前）
  - 提交按鈕（"雷電" 圖示 + 文本）

  **Tab 2: 持倉 (Positions)**
  - 「同期」按鈕 → 從 IBKR 拉取最新持倉
  - 持倉卡片列表（Ticker, 數量, 均價, 市值, 未實現 P&L）
  - 每列快速「平倉」按鈕 → 預填賣出單

  **Tab 3: 歷史 (History)**
  - 日期範圍選擇（7d / 30d / 90d）
  - 成交記錄表格（時間、股票、B/S、股數、成交價、手續費）

**JavaScript 代碼** (~800 行)
- `openIBKRDrawer(ticker)` — 開啟抽屜 + 可選預填 ticker
- `initIbkrDrawer()` — 事件監聽器 + 初始化
- `updateIbkrStatus()` — 每 30 秒輪詢狀態
- `ibkrConnect() / ibkrDisconnect()` — 連線管理
- `ibkrSearchQuote()` — 報價搜尋 + 自動填表
- `ibkrPlaceOrder()` — 訂單驗證 + 確認 + 提交
- `ibkrSyncPositions()` — 倉位同期
- `ibkrQuickClosePos(ticker, qty)` — 快速平倉

---

## 文件變更彙整

| 文件 | 操作 | 行數 | 摘要 |
|------|------|------|------|
| `.env` | 修改 | +28 | 新增 IBKR 設定群組 |
| `trader_config.py` | 修改 | +30 | IBKR 參數 + 條件邏輯 |
| `modules/ibkr_client.py` | 新建 | 1,060 | IBKR 連線核心 |
| `requirements.txt` | 修改 | +1 | ib_insync 相依 |
| `modules/db.py` | 修改 | +80 | ibkr_orders 表 + API |
| `app.py` | 修改 | +180 | 8 個 IBKR 路由 |
| `templates/base.html` | 修改 | +1,050 | Offcanvas + 800 行 JS |

**總新增代碼:** ~2,400 行

---

## 快速開始 (Quick Start)

### 前置條件

1. **安裝 ib_insync:**
   ```bash
   pip install -r requirements.txt
   ```

2. **配置 `.env` 檔案:**
   ```bash
   # 選擇連線方式
   IBKR_CONNECTION_MODE=TWS    # 或 GATEWAY
   
   # TWS Paper Trading (default)
   IBKR_TWS_HOST=127.0.0.1
   IBKR_TWS_PORT_PAPER=7497
   IBKR_CLIENT_ID=1
   ```

3. **啟動 TWS 或 IB Gateway:**
   - 確保已設定接受來自本機的客戶端連接
   - 確保啟用「允許 API 連接」

### 運行

```bash
# 啟動 Flask 伺服器
python app.py

# 瀏覽器開啟
http://localhost:5000
```

### 使用流程

1. **在任何頁面點擊 Navbar 的「交易」按鈕**
   - Offcanvas 抽屜從右側滑出

2. **Tab 1: 交易**
   - 輸入股票代碼 (e.g., AAPL) → 點搜尋 → 顯示 Bid/Ask/Last
   - 選擇 BUY/SELL
   - 輸入股數、訂單類型 → 如需要輸入限價/止損/移動止損
   - 點「下單」→ 確認對話框 → 確認
   - ✅ 訂單已提交

3. **Tab 2: 持倉**
   - 點「同期」→ 拉取 IBKR 真實持倉
   - 顯示市值、未實現 P&L
   - 點「平倉」快速預填賣單

4. **Tab 3: 歷史**
   - 選擇時間範圍 → 顯示成交記錄

---

## 下一步 (Future Enhancements)

### Phase 3 — 掃描結果整合
- [ ] 在 `templates/positions.html` 每列添加「IBKR 下單」按鈕
- [ ] 在 `templates/watchlist.html` 每列添加「IBKR 下單」按鈕
- [ ] 在 `templates/scan.html` 掃描結果表每列添加「快速交易」按鈕
- [ ] `openIBKRDrawer(ticker)` 全頁面呼叫點

### Phase 4 — 高級功能
- [ ] 即時串流報價（如 IBKR 支援）
- [ ] Position Manager — 自動追蹤移動止損
- [ ] Trade Journal — 與 SEPA 分析整合
- [ ] CSV / Excel 匯出成交記錄
- [ ] Webhook 整合 TradingView 警報

---

## 已驗證的項目

✅ Python 語法檢查通過  
✅ app.py 可正確導入（含新路由）  
✅ trader_config.py IBKR 設定正確加載  
✅ db.py ibkr_orders 表成功創建  
✅ base.html HTML + JS 語法有效  
✅ `.env` 檔案保護（已在 .gitignore）  

---

## 故障排除

### 連接失敗
- **檢查:** TWS/IB Gateway 已啟動且路由為 `127.0.0.1:7497` (TWS) 或 `127.0.0.1:4002` (Gateway)
- **檢查:** 日誌中是否有 `"Connecting to IBKR ..."`
- **檢查:** `trader_config.IBKR_ENABLED = True`

### 下單不起作用
- **檢查:** `IBKR_READONLY=false` 在 `.env`
- **檢查:** 帳號是否有足夠的買購力
- **檢查:** 訂單驗證（限價 > 0, 止損價合理等）

### 報價搜尋空白
- **檢查:** 網路連接
- **檢查:** IBKR 報價訂閱權限（紙質賬戶通常有延遲報價）

---

## 相關文檔

- **Copilot Instructions:** `.github/copilot-instructions.md`
- **Python Standards:** `.github/instructions/python-standards.instructions.md`
- **Trading Logic:** `.github/instructions/trading-logic.instructions.md`
- **Templates Guide:** `.github/instructions/templates.instructions.md`

---

**下一步:** 在本地連接 IBKR Paper Trading 帳號進行完整功能測試，然後由用戶根據實際需求進行微調和擴展。

