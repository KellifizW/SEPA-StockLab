# Telegram Bot 整合 — 完整設置指南

> ✅ 已完成實施：Polling 方式 + 本地 Flask 運行

---

## 快速開始（3 步）

### 步驟 1️⃣ 準備 Telegram Bot Token 和 Chat ID

如果你還沒有，先看[前面的文檔](TELEGRAM_BOT_SETUP.md)：

1. 向 `@BotFather` 建立新 Bot，取得 **API Token**
   - 格式：`7123456789:AAGxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx`

2. 在瀏覽器開啟：`https://api.telegram.org/bot<TOKEN>/getUpdates`
   - 在 Telegram 對你的 Bot 輸入 `/start`
   - 在瀏覽器結果中找到 `"chat": {"id": 你的ChatID}`

### 步驟 2️⃣ 配置程式

編輯 `trader_config.py`，找到「TELEGRAM BOT」區塊（末尾附近）：

```python
# ─────────────────────────────────────────────────────────────────────────────
# TELEGRAM BOT  (Polling 方式通訊)
# ─────────────────────────────────────────────────────────────────────────────
TG_ENABLED        = True                # ⚠️ 改為 True
TG_BOT_TOKEN      = "7123456789:AAG..." # ← 改為你的 Token
TG_CHAT_ID        = "987654321"         # ← 改為你的 Chat ID
TG_POLL_INTERVAL  = 2                   # Polling 間隔（秒）
```

### 步驟 3️⃣ 完成！啟動程式

```bash
python start_web.py
```

或者用 Windows 任務排程器設定開機自動啟動。

---

## 驗證設定

在啟動程式前，測試一下配置是否正確：

```bash
python test_telegram_setup.py
```

輸出應該像這樣：
```
  [步驟 1] 檢查配置...
    TG_ENABLED:       True
    TG_BOT_TOKEN:     ✅ 已設定
    TG_CHAT_ID:       ✅ 已設定
    TG_POLL_INTERVAL: 2 秒

  [步驟 2] 測試 API 連線和訊息發送...
    ✅ 訊息已傳送

  [步驟 3] 監聽傳入訊息 (timeout 10 秒)...
    ℹ️  暫無新訊息
```

如果看到 ❌ 錯誤，檢查：
- Token 是否完整且正確
- Chat ID 是否正確
- 網路連線是否正常

---

## 可用的 Telegram 指令

| 指令 | 功能 |
|------|------|
| `/market` | 📊 評估市場環境（Regime、Breadth、領先/落後板塊） |
| `/help` | 📋 顯示幫助訊息 |

### 例子

**輸入：** `/market`

**回覆：**
```
📊 市場環境評估

Regime: CONFIRMED_UPTREND
SPY Trend: ↑ Strong Uptrend
Breadth: 65.3%
Distribution Days: 2
NH/NL Ratio: 1.25

💹 領先板塊:
Technology, Consumer Discretionary, Industrials

📉 落後板塊:
Energy, Financials, Utilities

評估時間: 2026-03-03 15:30:45
```

---

## 完整架構

### 資料流

```
[你在 Telegram 輸入]
        ↓
[Telegram 伺服器] 儲存訊息
        ↓
[程式的 Polling Thread]
  (每 2 秒檢查一次)
  ↓ getUpdates()
  ↓ 檢測新訊息 /market
  ↓ 呼叫 modules.market_env.assess()
  ↓ 格式化結果
  ↓ sendMessage()
        ↓
[Telegram 伺服器] 推送回覆
        ↓
[你的手機] 收到回覆
```

### 檔案變更摘要

| 檔案 | 改動 |
|------|------|
| `trader_config.py` | ✅ 新增 `TG_*` 設定區塊 |
| `modules/telegram_bot.py` | ✅ 新建（~280 行） |
| `requirements.txt` | ✅ 加入 `python-telegram-bot>=21.0` |
| `app.py` | ✅ Flask 啟動時自動啟動 Polling thread |

### 核心邏輯（簡化）

```python
# 每 2 秒執行一次
updates = getUpdates()  # 詢問 Telegram 是否有新訊息

for update in updates:
    if update['text'] == '/market':
        result = assess()  # 呼叫市場評估
        text = format_market(result)  # 格式化
        sendMessage(text)  # 回覆給使用者
```

---

## 常見問題 (FAQ)

### Q1: 電腦關機後，Telegram 無法聯動
**A:** 這是預期行為。本地 Polling 只在程式執行時有效。

解決方案：
- 保持電腦開著（或用 Windows 工作排程器開機自動啟動）
- 或升級到 VPS/Oracle Cloud（見下文）

### Q2: 程式執行了，但 Telegram 沒有回應
**請檢查：**
1. `TG_ENABLED` 是否改為 `True`
2. Token 和 Chat ID 是否正確（用 `test_telegram_setup.py` 驗證）
3. 查看程式的 console 日誌有沒有錯誤
4. 網路連線是否正常

### Q3: 訊息發送延遲很大
**原因：**
- Polling 間隔是 2 秒（`TG_POLL_INTERVAL`），所以最快 2 秒才能檢測新訊息
- 市場環估計算要 5-10 秒

**改善方法：**
- 減小 `TG_POLL_INTERVAL`（但會增加 CPU 使用和 API 調用）
- 升級到 Webhook 方式（需要公開 IP）

### Q4: Token 不小心洩漏了怎麼辦？
**立即操作：**
1. 在 BotFather 輸入 `/revoke`，撤銷舊 Token
2. 向 BotFather 輸入 `/newbot`，重新產生新 Token
3. 更新 `trader_config.py` 的 `TG_BOT_TOKEN`

### Q5: 能否從 Telegram 觸發掃描？
**目前不支持。** 下一階段可以加入 `/scan sepa` 等指令，但風險是：
- 掃描耗時 10-30 分鐘
- 程式有出錯風險
- 需要額外的錯誤處理

---

## 進階：隱藏敏感資訊

如果你擔心 `TG_BOT_TOKEN` 被 git commit，改用環境變數：

### 方法 1: `.env` 檔案（推薦 — 本地開發）

建立 `.env` 文件（根目錄）：
```
TG_BOT_TOKEN=7123456789:AAGxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx
TG_CHAT_ID=987654321
```

加入 `.gitignore`：
```
.env
```

改 `trader_config.py`：
```python
import os
from dotenv import load_dotenv

load_dotenv()

TG_BOT_TOKEN  = os.getenv("TG_BOT_TOKEN", "")
TG_CHAT_ID    = os.getenv("TG_CHAT_ID", "")
```

### 方法 2: 環境變數（系統級）

Windows：
```powershell
$env:TG_BOT_TOKEN = "7123456789:AAG..."
$env:TG_CHAT_ID = "987654321"
python start_web.py
```

Linux/Mac：
```bash
export TG_BOT_TOKEN="7123456789:AAG..."
export TG_CHAT_ID="987654321"
python start_web.py
```

---

## 下一步（可選）

### 增加更多指令

在 `modules/telegram_bot.py` 的 `_process_update()` 中加入：

```python
if cmd == "/positions":
    from modules.position_monitor import get_positions
    positions = get_positions()
    reply = format_positions(positions)
    send_message(reply)

elif cmd == "/watchlist":
    from modules.watchlist import load_watchlist
    wl = load_watchlist()
    reply = format_watchlist(wl)
    send_message(reply)
```

### 升級到 24/7 雲端（未來）

如果需要 24 小時可用性，升級路線：
1. **VPS（最簡單）** — $2-5/月，用同樣的 Polling 代碼
2. **Webhook**（最快速） — 改用 Webhook 方式，需要公開 IP
3. **完整雲端架構** — 容器化 + CI/CD（複雜度高）

---

## 支持

有問題？
- 檢查 `modules/telegram_bot.py` 的日誌輸出
- 執行 `test_telegram_setup.py` 驗證基礎設定
- 查看 Telegram 官方文檔: https://core.telegram.org/bots/api

---

**完成！🎉 你現在可以透過 Telegram 查詢市場環境了。**
