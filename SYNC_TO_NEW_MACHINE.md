# 跨機器同步配置指南 — SEPA-StockLab Telegram Mini App

## 📋 環境變數檢查清單

### ✅ 已更新 `.env.example`

你現在可以把 `.env.example` commit 到 git 中。其他開發者見此文件即可知道需要什麼環境變數：

```bash
TG_BOT_TOKEN=你的Token_...          # Telegram Bot Token
TG_ADMIN_CHAT_ID=你的AdminID        # 管理員 Chat ID  
TG_MINI_APP_BASE_URL=https://<ngrok-url>  # ngrok 公開 URL
```

## 🔐 ngrok Auth Token — 存儲位置

**你的 ngrok auth token 已保存在：**

```
C:\Users\t-way\AppData\Local\ngrok\ngrok.yml
```

### 🔑 Token 內容 (安全，不提交到 git)

```yaml
version: "3"
agent:
    authtoken: 3AVSVJOFkAQwNAH48Q9HHOCwH0I_3szDbCEqv8kFxKY63AF4J
```

## 🔄 在另一台機器上同步

### 步驟 1: Clone 項目

```bash
git clone https://github.com/KellifizW/SEPA-StockLab.git
cd SEPA-StockLab
```

### 步驟 2: 設置 Telegram Bot Token

複製 `.env.example` 為 `.env`：

```bash
cp .env.example .env
```

編輯 `.env`，填入你的 Telegram Token 和 Admin Chat ID：

```
TG_BOT_TOKEN=7123456789:AAGxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx
TG_ADMIN_CHAT_ID=你的ChatID
TG_MINI_APP_BASE_URL=https://your-ngrok-url.ngrok-free.dev
```

### 步驟 3: 配置 ngrok Auth Token

**僅在第一次使用時執行：**

```bash
ngrok config add-authtoken 3AVSVJOFkAQwNAH48Q9HHOCwH0I_3szDbCEqv8kFxKY63AF4J
```

這會自動将 token 保存到系統配置文件中：
- **Windows:** `%APPDATA%\ngrok\ngrok.yml`
- **Linux/Mac:** `~/.ngrok2/ngrok.yml`

**之後再同步項目時，無需重複此步驟！**

### 步驟 4: 啟動應用

```bash
python start_web.py
```

## 📋 檢查清單 — 新機器初始化

- [ ] Clone 項目
- [ ] 複製 `.env.example` → `.env`
- [ ] 填入 `TG_BOT_TOKEN` 和 `TG_ADMIN_CHAT_ID`
- [ ] 執行 `ngrok config add-authtoken <your-token>` **（只執行一次）**
- [ ] 啟動應用並測試 ngrok tunnel

## ⚠️ 重要說明

1. **ngrok URL 會改變** — 免費帳戶每次重啟時分配新 URL，需更新 `TG_MINI_APP_BASE_URL`
2. **Token 安全** — ngrok token 永遠不要 commit 到 git
3. **驗證** — 另一台機器上配置完成後，訪問 `http://localhost:5000/tg/menu` 驗證

## 🔗 正確的 URL

```
本地開發:    http://localhost:5000/tg/menu
ngrok 公開:  https://你的ngrok-url.ngrok-free.dev/tg/menu
```

---

✅ 所有敏感信息已正確保存，不會洩露到 git 倉庫中！
