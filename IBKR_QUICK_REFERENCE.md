# IBKR 快速參考 (Quick Reference)

## 環境變數 (.env)

```dotenv
# 選擇連線模式：TWS 或 GATEWAY
IBKR_CONNECTION_MODE=TWS

# TWS Trader Workstation 連線設定
IBKR_TWS_HOST=127.0.0.1
IBKR_TWS_PORT_PAPER=7497    # 紙交易 (Paper)
IBKR_TWS_PORT_LIVE=7496     # 真實交易 (Live)

# IB Gateway 連線設定（輕量級替代）
IBKR_GATEWAY_HOST=127.0.0.1
IBKR_GATEWAY_PORT_PAPER=4002  # Gateway 紙交易
IBKR_GATEWAY_PORT_LIVE=4001   # Gateway 真實交易

# 應用程式識別碼
IBKR_CLIENT_ID=1

# 指定帳號（留空使用預設）
IBKR_ACCOUNT=

# 唯讀模式（true 禁用所有下單功能）
IBKR_READONLY=false
```

## trader_config.py 常數

| 常數 | 預設值 | 說明 |
|------|--------|------|
| `IBKR_ENABLED` | `True` | 主開關 |
| `IBKR_CONNECTION_MODE` | `"TWS"` | 連線模式 |
| `IBKR_HOST` | `"127.0.0.1"` | 主機位址 |
| `IBKR_PORT_PAPER` | `7497` | 紙交易端口 |
| `IBKR_PORT_LIVE` | `7496` | 真實交易端口 |
| `IBKR_CLIENT_ID` | `1` | 應用應識別碼 |
| `IBKR_ACCOUNT` | `""` | 指定帳號 |
| `IBKR_READONLY` | `False` | 唯讀保護 |
| `IBKR_TIMEOUT_SEC` | `10` | 連線逾時（秒） |
| `IBKR_SYNC_INTERVAL` | `300` | 倉位同期間隔（秒） |
| `IBKR_QUOTE_CACHE_SEC` | `15` | 報價快取期限（秒） |

## API 端點

### 連線管理

```
POST /api/ibkr/connect
→ { ok, data: { success, state, message, account, nav } }

POST /api/ibkr/disconnect
→ { ok, data: { success, state, message } }

GET /api/ibkr/status
→ { ok, data: { state, connected, account, nav, buying_power, unrealized_pnl, cash } }
```

### 報價 & 資料

```
GET /api/ibkr/quote/<TICKER>
→ { ok, data: { ticker, bid, ask, last, volume, bid_size, ask_size } }

GET /api/ibkr/positions
→ { ok, data: { positions: [...], synced_count } }
  Position obj: { ticker, qty, avg_cost, market_value, unrealized_pnl, unrealized_pnl_pct, market_price }

GET /api/ibkr/orders
→ { ok, data: { orders: [...] } }
  Order obj: { order_id, ticker, action, qty, order_type, limit_price, aux_price, status }

GET /api/ibkr/trades?days=7
→ { ok, data: { executions: [...], db_orders: [...], count } }
  Execution obj: { exec_id, time, ticker, action, qty, price, commission }
```

### 訂單管理

```
POST /api/ibkr/order
Body: {
  "ticker": "AAPL",
  "action": "BUY" | "SELL",
  "qty": 100,
  "order_type": "MKT" | "LMT" | "STP" | "TRAIL",
  "limit_price": 150.00,      // 僅 LMT
  "aux_price": 145.00,        // 僅 STP
  "trail_pct": 2.5,           // 僅 TRAIL
  "note": "VCP breakout"
}
→ { ok, data: { success, order_id, message } }

DELETE /api/ibkr/order/<ORDER_ID>
→ { ok, data: { success, message } }
```

## JavaScript 全域函式

```javascript
// 開啟交易抽屜（可選預填 ticker）
openIBKRDrawer('AAPL')

// 快速平倉（預填賣出單）
ibkrQuickClosePos('AAPL', 100)

// 手動更新連線狀態
updateIbkrStatus()

// 搜尋報價
ibkrSearchQuote()

// 同期倉位
ibkrSyncPositions()
```

## 訂單類型一覽

| 類型 | 說明 | 必填參數 |
|------|------|---------|
| `MKT` | 市價（立即執行） | - |
| `LMT` | 限價（指定價格） | `limit_price` |
| `STP` | 止損市價（觸發後市價） | `aux_price` |
| `TRAIL` | 移動止損（百分比） | `trail_pct` |

## 模組層級函式 (modules/ibkr_client.py)

```python
from modules import ibkr_client

# 連線 / 斷開
ibkr_client.connect() → dict
ibkr_client.disconnect() → dict
ibkr_client.get_status() → dict

# 持倉
ibkr_client.get_positions() → list[dict]
ibkr_client.get_executions(days=7) → list[dict]
ibkr_client.get_open_orders() → list[dict]

# 訂單
ibkr_client.place_order(ticker, action, qty, order_type, ...) → dict
ibkr_client.cancel_order(order_id) → dict

# 報價
ibkr_client.get_quote(ticker) → dict
```

## 數據庫 (modules/db.py)

```python
from modules import db

# IBKR 訂單
db.append_ibkr_order(order_dict) → bool
db.query_ibkr_orders(days=30) → list[dict]
```

## 多語言支援

所有前端文本均採雙語：
- 中文（繁体）為主
- 英文為輔

| 術語 | 中文 | 英文 |
|------|------|------|
| 交易 | 交易面板 | Trading Panel |
| 連接 | 已連接 / 連接 | Connected / Connect |
| 持倉 | 持倉 | Positions |
| 歷史 | 歷史 | History |
| 買入 | 買入 | BUY |
| 賣出 | 賣出 | SELL |
| 下單 | 下單 | Place Order |
| 同期 | 已同期 | Synced |
| 平倉 | 平倉 | Close Position |

