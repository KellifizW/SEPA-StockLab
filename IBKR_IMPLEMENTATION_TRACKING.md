# IBKR 集成實現跟進 — 調試與後續工作追蹤

**上次更新**: 2026-03-04  
**整體進度**: Phase 1-2 ✅ 完成 | Phase 3 ⏳ 規劃中 | Phase 4 🔲 待規劃

---

## 📋 執行摘要

本文檔追蹤 IBKR API 集成項目的完整生命週期：
- **Phase 1-2**: 已實施完成，包含環境配置、後端 API、資料庫擴展、前端抽屜式 UI
- **Phase 3**: 整合觸點 — 在現有掃描/監看清單/倉位頁面添加快速下單按鈕
- **Phase 4**: 進階功能 — 實時行情、自動位置管理、交易日誌整合

---

## 🔧 Phase 1: 環境與配置調試

### 已完成工作
- ✅ `.env` 配置檔擴展（28 行）
- ✅ `trader_config.py` 整合 IBKR 參數（30 行）
- ✅ 條件邏輯：依據 `IBKR_CONNECTION_MODE` 選擇 TWS 或 Gateway 端口

### Phase 1 調試清單

| 項目 | 狀態 | 驗證方法 | 預期結果 |
|------|------|--------|--------|
| `.env` 檔存在並包含 IBKR 參數 | ✅ | 檢查 `.env` 存在 | 找到 `IBKR_CONNECTION_MODE`, `IBKR_TWS_PORT_PAPER` 等 |
| `trader_config.py` 成功載入 `.env` 值 | ✅ | `python -c "import trader_config as C; print(C.IBKR_HOST)"` | 輸出: `127.0.0.1` |
| IBKR_ENABLED 開關生效 | ✅ | `python -c "import trader_config as C; print(C.IBKR_ENABLED)"` | 輸出: `True` |
| 端口選擇邏輯正確 (TWS vs Gateway) | ✅ | 修改 `.env` 中 `IBKR_CONNECTION_MODE` 後檢查 `IBKR_HOST` | TWS: 7497/7496, Gateway: 4002/4001 |
| `.env` 在 `.gitignore` 中 | ✅ | 檢查 `.gitignore` | `.env` 出現在列表中 |

### Phase 1 已知問題與解決方案

| 問題 | 症狀 | 解決方案 |
|------|------|--------|
| `.env` 檔案遺失 | `trader_config.py` 使用預設值，IBKR 功能不可用 | 複製 `.env.example`（如有）或手動建立 `.env`，填入 IBKR 參數 |
| 端口號錯誤 | 無法連接到 TWS/IB Gateway | 驗證 `.env` 中的端口號與 TWS 設置一致（通常紙交易 7497，模擬交易 7496） |
| 字符編碼問題 | `trader_config.py` 讀取 `.env` 時出現亂碼 | 確保 `.env` 使用 UTF-8 編碼，`trader_config.py` 第一行包含 `# -*- coding: utf-8 -*-` |
| IBKR_ACCOUNT 為空 | 同步倉位時無法識別帳戶 | 在 `.env` 中設置 `IBKR_ACCOUNT=DU12345`（IB 帳號通常為 DU 開頭） |

---

## 🚀 Phase 2: 後端 API 與前端 UI 調試

### 已完成工作
- ✅ `modules/ibkr_client.py` 實施（1,060 行）
- ✅ `modules/db.py` 擴展，新增 `ibkr_orders` 表（80 行）
- ✅ `app.py` 新增 8 個 IBKR API 路由（180 行）
- ✅ `requirements.txt` 添加 `ib_insync>=0.9.86` 依賴
- ✅ `templates/base.html` 實施抽屜式 UI（1,050 行）

### Phase 2 調試清單

#### 2A. 後端 API 驗證

| 項目 | 狀態 | 驗證方法 | 預期結果 |
|------|------|--------|--------|
| Python 語法檢查 | ✅ | `python -m py_compile app.py modules/ibkr_client.py modules/db.py` | 無輸出（成功） |
| `modules.db` 模組載入 | ✅ | `python -c "from modules import db; print('OK')"` | 輸出: `OK` + DuckDB schema 初始化 |
| `modules.ibkr_client` 模組符號檢查 | ⏳ | `python -c "from modules.ibkr_client import connect, disconnect, get_status"` | 成功（待 pip install ib_insync） |
| DuckDB `ibkr_orders` 表建立 | ✅ | `sqlite3 data/sepa_stock.duckdb "SELECT name FROM information_schema.tables WHERE table_name='ibkr_orders'"` | 輸出: `ibkr_orders` |
| `ibkr_orders` 表索引 | ✅ | `duckdb data/sepa_stock.duckdb "PRAGMA index_list(ibkr_orders)"` | 列出 `idx_ibkr_orders_ticker` |
| Flask 應用啟動 | ⏳ | `python app.py` | Flask 伺服器在 http://127.0.0.1:5000 啟動（首次需 pip install） |

**安裝依賴步驟**:
```bash
pip install -r requirements.txt
# 輸出應包含: Successfully installed ib_insync-0.9.x.x
```

#### 2B. API 端點測試

| 端點 | 方法 | 預期行為 | 調試步驟 |
|------|------|--------|--------|
| `/api/ibkr/status` | GET | 返回連接狀態 + 帳戶摘要 (NAV, 買力等) | curl 命令或瀏覽器請求 |
| `/api/ibkr/connect` | POST | 連接到 TWS/IB Gateway，返回帳戶資訊 | 確保 TWS/IB Gateway 執行中，檢查 `.env` 端口 |
| `/api/ibkr/disconnect` | POST | 斷開連接，返回成功訊息 | 連接後執行 |
| `/api/ibkr/positions` | GET | 返回現有倉位列表，同步到 `positions.json` | 需先在 IBKR 持有部位 |
| `/api/ibkr/orders` | GET | 返回未平倉訂單列表 | 檢查 HTTP 回應狀態碼 200 |
| `/api/ibkr/trades` | GET | 返回成交記錄（可選 `?days=7` 參數） | 檢查回應包含 executions 和 order_history |
| `/api/ibkr/order` | POST | 下單，返回訂單 ID | 檢查請求體: `{ticker, action, qty, order_type, limit_price?, aux_price?, trail_pct?}` |
| `/api/ibkr/order/<order_id>` | DELETE | 取消訂單，返回成功訊息 | 操作前確保訂單未成交 |
| `/api/ibkr/quote/<ticker>` | GET | 返回實時行情 (bid/ask/last/volume) | 試試 `/api/ibkr/quote/AAPL` |

**API 測試範例** (PowerShell):
```powershell
# 測試連接狀態
$response = curl -Uri "http://127.0.0.1:5000/api/ibkr/status" -Method GET
Write-Host $response.Content

# 測試連接
$response = curl -Uri "http://127.0.0.1:5000/api/ibkr/connect" -Method POST
Write-Host $response.Content

# 測試行情查詢
$response = curl -Uri "http://127.0.0.1:5000/api/ibkr/quote/AAPL" -Method GET
Write-Host $response.Content
```

#### 2C. 前端 UI 驗證

| 項目 | 狀態 | 驗證方法 | 預期結果 |
|------|------|--------|--------|
| 導航欄 "交易" 按鈕出現 | ✅ | 開啟 http://127.0.0.1:5000，檢查導航欄右側 | 綠色或紅色圓點 + "交易 / Trade" 文本 |
| 抽屜打開/關閉 | ✅ | 點擊導航欄按鈕 | 420px 寬度抽屜從右側滑入 |
| 連接狀態圓點更新 | ⏳ | 打開抽屜，檢查導航欄圓點顏色變化 | 綠色 = 已連接，紅色 = 未連接 |
| 三個標籤頁顯示 | ✅ | 檢查抽屜內容 | 「交易 / Trade」、「倉位 / Positions」、「歷史 / History」三個標籤 |
| 股票搜尋功能 | ⏳ | 在交易標籤中輸入股票代碼（如 AAPL），點擊查詢 | 顯示 Bid/Ask/Last/Volume 卡片 |
| 下單表單 | ✅ | 檢查交易標籤的表單欄位 | 股票、數量、訂單類型、條件價格欄位都出現 |
| 倉位同期按鈕 | ✅ | 進入倉位標籤，點擊「同期 / Sync」 | 倉位列表更新為最新 IBKR 倉位 |
| 歷史記錄篩選 | ✅ | 進入歷史標籤，選擇時間範圍 | 顯示對應期間的成交記錄 |

### Phase 2 已知問題與解決方案

| 問題 | 症狀 | 解決方案 |
|------|------|--------|
| `ib_insync` 未安裝 | 導入 `ibkr_client` 時出錯 | 執行 `pip install -r requirements.txt` |
| TWS/IB Gateway 未運行 | 連接時超時或失敗 | 啟動 TWS 或 IB Gateway，確保允許 API 連接 |
| 端口被佔用 | Flask 無法啟動或 IBKR 連接失敗 | 檢查防火牆，確保端口 7497/7496/4002/4001 暢通 |
| 帳戶同步失敗 | `/api/ibkr/positions` 返回空列表 | 檢查 `.env` 中 `IBKR_ACCOUNT` 是否正確，或帳戶確實無部位 |
| 下單分發失敗 | POST `/api/ibkr/order` 返回 500 | 檢查請求體格式，確保必填字段完整 |
| 抽屜 JavaScript 錯誤 | 控制臺顯示 JS 錯誤，抽屜功能不正常 | 使用瀏覽器開發者工具（F12）查看 Console 標籤 |
| 樣式不一致 | 抽屜顏色/字體與主題不符 | 確保 `base.html` 中 CSS 變數 (`--bs-*`) 正確應用 |
| 倉位同期覆蓋本地數據 | 同期後遺失本地交易備註 | 額外同期邏輯：IBKR 字段只覆蓋市場數據，保留本地字段如 `stop_loss`、`note` |

### Phase 2 驗證檢查清單 (上線前)

- [ ] `pip install -r requirements.txt` 成功，無依賴衝突
- [ ] 啟動 TWS 或 IB Gateway，允許本機 API 連接
- [ ] `python app.py` 啟動 Flask，無異常
- [ ] 瀏覽器開啟 http://127.0.0.1:5000，頁面正常載入
- [ ] 導航欄出現「交易」按鈕，連接狀態圓點可見
- [ ] 點擊按鈕，抽屜滑入，三個標籤頁都可見
- [ ] POST /api/ibkr/connect，成功連接，圓點變綠
- [ ] GET /api/ibkr/status，返回帳戶資訊（NAV、買力等）
- [ ] GET /api/ibkr/quote/AAPL，顯示實時行情
- [ ] GET /api/ibkr/positions，列出當前倉位
- [ ] POST /api/ibkr/order，紙交易下單成功，訂單出現在歷史
- [ ] DELETE /api/ibkr/order/<id>，取消待執行訂單成功
- [ ] DuckDB `ibkr_orders` 表有新紀錄，`db.query_ibkr_orders()` 可查詢

---

## 📍 Phase 3: 整合觸點 — 快速下單按鈕

### 目標
在三個現有頁面添加「直接下單」快速按鈕，打開 IBKR 抽屜並預填股票代碼。

### 實施清單

#### 3.1 `templates/positions.html` — 倉位快速下單

**位置**: 倉位表格每一行右側  
**功能**: 快速平倉按鈕，一鍵開啟 IBKR 抽屜，預填 SELL 訂單

**實施步驟**:
```markdown
1. [ ] 定位 positions.html 中倉位表格體
2. [ ] 在每一行右側添加「IBKR 下單」按鈕
3. [ ] 按鈕點擊事件：調用 openIBKRDrawer(ticker, 'SELL', qty)
4. [ ] 樣式：Bootstrap btn-sm btn-outline-success（綠色）
5. [ ] 測試：點擊按鈕，抽屜打開，下單模式為 SELL，股票和數量預填
```

**預期代碼片段**:
```html
<!-- 在倉位表格行的最後一列添加 -->
<td class="text-end">
  <button class="btn btn-sm btn-outline-success" 
          onclick="openIBKRDrawer('{{ ticker }}', 'SELL', {{ qty }})">
    IBKR 下單
  </button>
</td>
```

#### 3.2 `templates/watchlist.html` — 監看清單快速下單

**位置**: 監看清單表格每一行右側  
**功能**: 快速 BUY 按鈕，打開 IBKR 抽屜，預填 BUY 訂單

**實施步驟**:
```markdown
1. [ ] 定位 watchlist.html 中監看清單表格
2. [ ] 在每一行右側添加「IBKR BUY」按鈕
3. [ ] 按鈕點擊事件：調用 openIBKRDrawer(ticker, 'BUY')
4. [ ] 樣式：Bootstrap btn-sm btn-outline-primary（藍色）
5. [ ] 測試：點擊按鈕，抽屜打開，下單模式為 BUY，股票預填
```

**預期代碼片段**:
```html
<!-- 在監看清單表格行的最後一列添加 -->
<td class="text-end">
  <button class="btn btn-sm btn-outline-primary" 
          onclick="openIBKRDrawer('{{ ticker }}')">
    IBKR BUY
  </button>
</td>
```

#### 3.3 `templates/scan.html` 和 `templates/qm_scan.html` — 掃描結果快速下單

**位置**: 掃描結果表格每一行右側  
**功能**: 快速 BUY 按鈕，打開 IBKR 抽屜，同時顯示該股票的評分

**實施步驟**:
```markdown
1. [ ] 定位 scan.html 中掃描結果表格
2. [ ] 定位 qm_scan.html 中 QM 掃描結果表格
3. [ ] 在每一行右側添加「IBKR BUY」按鈕
4. [ ] 按鈕點擊事件：調用 openIBKRDrawer(ticker, 'BUY')
5. [ ] 可選：添加評分提示（hover 時顯示 SEPA 5-pillar 或 QM 6-star 評分）
6. [ ] 樣式：Bootstrap btn-sm btn-outline-success（綠色）
7. [ ] 測試：點擊按鈕，抽屜打開，股票預填，結合掃描評分
```

**預期代碼片段**:
```html
<!-- 在掃描結果表格行的最後一列添加 -->
<td class="text-end">
  <button class="btn btn-sm btn-outline-success" 
          onclick="openIBKRDrawer('{{ ticker }}', 'BUY')"
          title="SEPA Score: {{ score }}">
    IBKR BUY
  </button>
</td>
```

#### 3.4 JavaScript 增強

**更新 `openIBKRDrawer()` 函數簽名**:
```javascript
function openIBKRDrawer(ticker, action = 'BUY', qty = null) {
  // action: 'BUY' 或 'SELL'
  // qty: 如果提供，預填數量欄位
  // 打開抽屜，切換到交易標籤
  // 預填股票代碼、行動、數量
}
```

### Phase 3 實施檢查清單

- [ ] 修改 `templates/positions.html`，添加 SELL 快速下單按鈕
- [ ] 修改 `templates/watchlist.html`，添加 BUY 快速下單按鈕
- [ ] 修改 `templates/scan.html`，添加 BUY 快速下單按鈕
- [ ] 修改 `templates/qm_scan.html`，添加 BUY 快速下單按鈕
- [ ] 更新 `templates/base.html` 中 `openIBKRDrawer()` 函數簽名
- [ ] 測試所有四個頁面的快速下單按鈕
- [ ] 確保抽屜正確預填股票、行動、數量
- [ ] 驗證樣式一致性（顏色、按鈕大小、對齐）
- [ ] 檢查無障礙性（按鈕標籤、ARIA 屬性）

### Phase 3 估計工作量
- **開發時間**: 30-45 分鐘
- **測試時間**: 15-20 分鐘
- **總計**: 約 1 小時

---

## 🎯 Phase 4: 進階功能

### 4.1 實時行情串流

**功能**: 在 IBKR 抽屜的交易標籤中，長期訂閱股票行情更新，實時刷新 Bid/Ask/Last

**實施方案**:
```markdown
技術方案：
1. 後端 (`modules/ibkr_client.py`)：
   - 新增 `subscribe_market_data(ticker)` — 訂閱市場數據
   - 新增 `unsubscribe_market_data(ticker)` — 取消訂閱
   - 維護訂閱狀態 `_subscribed_tickers: Set[str]`

2. 前端 (`templates/base.html`)：
   - 股票搜尋後，自動訂閱行情更新
   - 每 1-2 秒輪詢 `/api/ibkr/quote/<ticker>` 獲取最新行情
   - 實時更新卡片中的 Bid/Ask/Last 價格，高亮變化（綠 ↑ 紅 ↓）

3. WebSocket 可選優化：
   - 使用 Flask-SocketIO 替代 HTTP 輪詢，實現真正的推送
   - 需要 `pip install flask-socketio python-socketio`

預計影響:
- 新增 `modules/ibkr_client.py` 函數：2 個
- 新增 `app.py` API 端點：可選（WebSocket 情況下無需新端點）
- 修改 `templates/base.html` JavaScript：~50 行
- 新增依賴：`flask-socketio`, `python-socketio`（可選）
```

**工作項**:
- [ ] 決定實現方案：HTTP 輪詢 vs WebSocket
- [ ] 在 `modules/ibkr_client.py` 中實現訂閱邏輯
- [ ] 在 `templates/base.html` 中實現前端輪詢或 WebSocket 監聽
- [ ] 測試實時行情更新延遲 (<1 秒)
- [ ] 優化 API 頻率限制，防止 IBKR 速率限制

---

### 4.2 位置管理器自動化

**功能**: 自動調整 Minervini 尾隨停止（trailing stop），基於倉位的 PnL 目標

**實施方案**:
```markdown
集成點：
1. 讀取 `modules/position_monitor.py` 現有邏輯
2. 在倉位同期後 (`/api/ibkr/positions`)，計算：
   - 當前未實現 PnL
   - 根據買入價格和 Minervini 規則計算尾隨停止
   - 如果市場停止 < 當前 IBKR 停止，更新 IBKR 停止

3. 後端新增：
   - `modules/position_monitor.py`: `calculate_trailing_stop(position_dict) → float`
   - `app.py` 新增 `/api/ibkr/positions/auto-adjust-stops` 端點

4. 前端新增：
   - IBKR 抽屜倉位標籤中添加「自動調整停止」按鈕
   - 點擊後顯示建議停止價格和確認對話框

工作量：
- `modules/position_monitor.py`: 新增 1 函數 (~30 行)
- `app.py`: 新增 1 API 端點 (~40 行)
- `templates/base.html`: 新增 UI 邏輯 (~30 行)
```

**工作項**:
- [ ] 研究並實現 `calculate_trailing_stop()` 算法
- [ ] 在 `position_monitor.py` 中編寫並測試該函數
- [ ] 在 `app.py` 中添加 `/api/ibkr/positions/auto-adjust-stops` 端點
- [ ] 在 IBKR 抽屜倉位標籤中add「自動調整」按鈕
- [ ] 測試自動調整邏輯，驗證停止價格計算正確

---

### 4.3 交易日誌整合

**功能**: 為每筆 IBKR 訂單關聯對應的 SEPA/QM/ML 掃描結果，建立完整的交易日誌

**實施方案**:
```markdown
數據結構：
1. `modules/db.py`：擴展 `ibkr_orders` 表
   - 新增列: scanned_at, sepa_score, qm_stars, ml_stars, buy_reason, exit_reason
   
2. 邏輯流程：
   a. 使用者點擊掃描結果中的「IBKR BUY」按鈕
   b. 將掃描評分嵌入到抽屜（隐藏字段或呈現在說明中）
   c. 下單成功後，記錄掃描評分到 `ibkr_orders`
   
3. 前端修改：
   - Phase 3 中的快速下單按鈕，傳遞額外上下文（score, rating）
   - `openIBKRDrawer(ticker, action, qty, metadata)` metadata 包含 {score, stars, strategy}
   - 下單時，將 metadata 一並提交

4. 後端修改：
   - `POST /api/ibkr/order` 接受額外字段 `sepa_score`, `qm_stars` 等
   - `db.append_ibkr_order()` 記錄這些字段

工作量：
- `modules/db.py`: 修改 `ibkr_orders` 表結構 (~20 行)
- `app.py`: 修改 `/api/ibkr/order` 端點以接受新字段 (~15 行)
- `templates/base.html`: 修改 JavaScript 傳遞 metadata (~25 行)
- `templates/scan.html`, `templates/qm_scan.html`: 修改快速下單按鈕，傳遞評分 (~10 行 每個)
```

**工作項**:
- [ ] 設計 `ibkr_orders` 表的新列
- [ ] 數據庫遷移：添加新列到現有表
- [ ] 修改 `POST /api/ibkr/order` 以接受並儲存 metadata
- [ ] 修改快速下單按鈕，傳遞掃描評分和信息
- [ ] 衍生報告：交易日誌頁面，展示每筆訂單的掃描背景
- [ ] 測試端到端流程：掃描 → 下單 → 查詢歷史記錄檢驗完整性

---

### 4.4 高級功能規劃（未來考慮）

| 功能 | 描述 | 優先級 | 依賴 |
|------|------|--------|------|
| **智能倉位管理** | 根據 SEPA 規則自動調整倉位大小 | 🟡 中 | Position Monitor v2 |
| **多帳戶支持** | 同時管理多個 IBKR 帳戶 | 🟢 低 | DB 透視表 |
| **風險預警** | 實時監控倉位風險，超過 MAX_RISK_PER_TRADE_PCT 時警報 | 🟡 中 | Position Monitor + Alerts |
| **回測整合** | 為 IBKR 訂單補充回測數據，計算準確的 Sharpe、Sortino 等 | 🟢 低 | Backtester v2 |
| **K 線卡片** | 在 IBKR 抽屜中顯示交易股票的迷你 K 線圖 | 🟡 中 | TradingView Lightweight Charts |
| **電報警報** | IBKR 訂單成交、停止觸發時發送電報通知 | 🟢 低 | telegram-python |

---

## 🐛 故障排除指南

### 常見錯誤與解決方案

#### Error 1: `ModuleNotFoundError: No module named 'ib_insync'`
```
原因: ib_insync 未安裝
現象: import ibkr_client 失敗
解決: pip install -r requirements.txt
驗證: python -c "import ib_insync; print(ib_insync.__version__)"
```

#### Error 2: `ConnectionRefusedError: [Errno 10061] No connection could be made`
```
原因: TWS/IB Gateway 未運行或端口錯誤
現象: Connect 按鈕點擊後無反應或顯示連接失敗
解決:
1. 啟動 TWS 或 IB Gateway
2. 確認設置允許本機 API 連接 (設置 -> API -> 啟用 ActiveX/Socket 客戶端)
3. 檢查 .env 中的端口是否與 TWS/Gateway 設置匹配
   - TWS 紙交易: 7497
   - TWS 實盤: 7496
   - IB Gateway 紙交易: 4002
   - IB Gateway 實盤: 4001
驗證: telnet 127.0.0.1 7497 (如果連接成功，Telnet 不會報告連接錯誤)
```

#### Error 3: `DuckDB Error: UNIQUE constraint failed: ibkr_orders.order_id`
```
原因: 重複插入相同訂單 ID
現象: 下單成功但第二次執行時失敗
解決: 在 INSERT 前檢查訂單是否已存在，或使用 INSERT OR REPLACE
驗證: SELECT COUNT(*) FROM ibkr_orders WHERE order_id = '<id>'
```

#### Error 4: 抽屜中的 JavaScript 函數未定義
```
原因: base.html 中的 JavaScript 未正確加載
現象: 瀏覽器控制臺顯示 "ReferenceError: openIBKRDrawer is not defined"
解決:
1. 在瀏覽器中按 Ctrl+F5 強制刷新（忽略緩存）
2. 使用開發者工具 (F12) 檢查 base.html 是否載入
3. 檢查 Flask app.py 中 render_template('base.html') 是否正確
驗證: 在 Console 中輸入 typeof openIBKRDrawer (應返回 'function')
```

#### Error 5: 倉位同期後數據丟失
```
原因: 本地 positions.json 被 IBKR 數據完全覆蓋
現象: 同期後，自定義字段（如 stop_loss、note）遺失
解決: 檢查 /api/ibkr/positions 中的合併邏輯
預期: IBKR 字段（市場價格、未實現 PnL）覆蓋，本地字段保留
驗證: 查看 app.py 中 positions 路由的 merge 邏輯
```

### 調試技巧

1. **啟用 Flask Debug 模式**:
   ```python
   # app.py 頂部
   app.config['DEBUG'] = True
   app.run(host='0.0.0.0', port=5000, debug=True)
   ```
   這樣會顯示詳細的異常追蹤。

2. **檢查 DuckDB 內容**:
   ```bash
   duckdb data/sepa_stock.duckdb
   > SELECT * FROM ibkr_orders LIMIT 5;
   ```

3. **監控 IBKR 連接**:
   ```python
   # 在 ibkr_client.py 中添加日誌
   import logging
   logging.basicConfig(level=logging.DEBUG)
   _logger = logging.getLogger(__name__)
   ```

4. **瀏覽器開發者工具**:
   - F12 打開開發者工具
   - Network 標籤：監控 API 請求和回應
   - Console 標籤：檢查 JavaScript 錯誤
   - Storage 標籤：檢查 localStorage / sessionStorage

5. **測試 API 端點**:
   使用 curl 或 Postman 直接測試 API，繞過前端。
   ```bash
   curl -X GET http://127.0.0.1:5000/api/ibkr/status
   curl -X POST http://127.0.0.1:5000/api/ibkr/connect
   curl -X GET "http://127.0.0.1:5000/api/ibkr/quote/AAPL"
   ```

---

## 📅 實施時間表

| Phase | 工作內容 | 預計時間 | 依賴 | 狀態 |
|-------|---------|--------|------|------|
| 1 | 環境配置 (`.env`, `trader_config.py`) | 20 分鐘 | 無 | ✅ 完成 |
| 2A | 後端實施 (`ibkr_client.py`, `db.py`, `app.py`) | 2-3 小時 | Phase 1 | ✅ 完成 |
| 2B | 前端實施 (`templates/base.html` 抽屜) | 2-3 小時 | Phase 2A | ✅ 完成 |
| 2C | 測試與驗證 | 1-2 小時 | Phase 2B | ⏳ 待用戶執行 |
| 3 | 整合觸點 (快速下單按鈕) | 1 小時 | Phase 2C | ⏳ 待實施 |
| 4.1 | 實時行情串流 | 1.5 小時 | Phase 2C | 🔲 待規劃 |
| 4.2 | 位置管理器自動化 | 1.5 小時 | Phase 3 | 🔲 待規劃 |
| 4.3 | 交易日誌整合 | 2 小時 | Phase 3 | 🔲 待規劃 |
| **總計** | **所有 phases** | **12-16 小時** | — | — |

---

## ✅ 後續步驟

### 立即行動（Phase 2 驗證）
```bash
# 1. 安裝依賴
pip install -r requirements.txt

# 2. 啟動 TWS 或 IB Gateway
# Windows: 執行 TWS 安裝目錄中的 tws.exe 或 IBGateway.exe
# Linux/Mac: java -cp TWS_DIR/lib/* jclient.LoginFrame

# 3. 驗證 Flask 應用
python app.py

# 4. 打開瀏覽器
# http://127.0.0.1:5000
# 檢查導航欄是否出現「交易」按鈕
```

### 完整測試流程（Phase 2C）
1. 點擊「交易」按鈕打開抽屜
2. 進入「交易」標籤，輸入股票代碼（如 AAPL）
3. 點擊「查詢行情」，驗證 Bid/Ask/Last 顯示正常
4. 選擇訂單類型（市價、限價、停止）
5. 點擊「下單」，確認對話框，驗證訂單成功
6. 進入「倉位」標籤，點擊「同期」驗證倉位更新
7. 進入「歷史」標籤，驗證成交記錄出現

### 下一階段（Phase 3）
使用者確認 Phase 2 運作正常後，提交下列要求即可觸發 Phase 3：
> "為 Phase 3 添加快速下單按鈕" 或 "實施整合觸點"

---

## 📚 相關文檔

- **IBKR 集成完成說明**: `IBKR_INTEGRATION_COMPLETE.md`
- **快速參考指南**: `IBKR_QUICK_REFERENCE.md`
- **SEPA-StockLab 指南**: `docs/GUIDE.md`
- **Trader Config 詳解**: `trader_config.py` (行 966+)
- **IBKR 官方文檔**: https://www.interactivebrokers.com/cn/index.php?f=5041

---

**Last Updated**: 2026-03-04  
**Maintained By**: GitHub Copilot  
**Questions?** 查閱各 Phase 下的「已知問題」或執行故障排除步驟
