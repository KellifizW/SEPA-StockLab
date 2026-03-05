# Telegram Bot Mini App 實施指南

## 完成內容

本次實施為 SEPA StockLab 添加了 **Telegram Bot Mini App 支持**，包括：

### 1️⃣ **Flask 後端路由** (app.py)

#### 新增路由：

| 路由 | 方法 | 功能 | 備註 |
|------|------|------|------|
| `/tg/app` | GET | Mini App 主目錄及殼層 | 驗證 initData，顯示 UI |
| `/api/tg/init` | POST | 用戶初始化 & 會話管理 | 驗證 Telegram 簽名 |
| `/api/tg/analyze/<ticker>` | POST | Mini App 分析端點 | 支持 SEPA/QM/ML |

#### 簽名驗證函數：

```python
def _verify_tg_init_data(init_data: str) -> dict:
    """
    驗證 Telegram WebApp initData 簽名
    - 使用 HMAC-SHA256
    - 密鑰 = SHA256(TG_BOT_TOKEN)
    - 返回: {ok: bool, user: dict, chat_id: int, error: str}
    """
```

### 2️⃣ **前端模板** (templates/)

#### 新增模板：

| 文件 | 描述 |
|------|------|
| `tg_app_shell.html` | Mini App 主界面 (445 行) |
| `tg_app_error.html` | 認證失敗頁面 |

#### Mini App 功能：

- ✅ Telegram WebApp SDK 初始化
- ✅ 實時 ticker 輸入與分析
- ✅ SEPA/QM/ML 三策略支持
- ✅ 評分視覺化 (星級、維度、統計)
- ✅ 響應式移動UI (Bootstrap 5 深色主題)
- ✅ 觸覺反饋 (HapticFeedback)
- ✅ 會話存儲 (sessionStorage)

### 3️⃣ **Telegram Bot 集成** (modules/telegram_bot.py)

#### 更新命令：

- `/analyze <ticker>` - 添加 webAppInfo 按鈕選項
- `/qm <ticker>` - 支持 Mini App 分析
- `/ml <ticker>` - 支持 Mini App 分析

### 4️⃣ **配置系統** (trader_config.py)

#### 新增配置項：

```python
# Telegram Mini App 配置
TG_MINI_APP_ENABLED = False              # 啟用/禁用 Mini App
TG_MINI_APP_BASE_URL = "http://localhost:5000"  # Mini App 根 URL
TG_MINI_APP_SHOW_BUTTON = True           # 顯示 Mini App 按鈕
```

### 5️⃣ **測試套件** (tests/test_tg_mini_app.py)

#### 測試覆蓋：

- ✅ HMAC-SHA256 簽名驗證
- ✅ Flask 路由檢查
- ✅ 配置驗證

---

## 使用流程

### 前置準備

1. **設置 Telegram Bot Token**

```bash
# .env
TG_BOT_TOKEN=123456:ABC-DEF1234ghIkl-zyx57W2v1u123ew11
TG_ADMIN_CHAT_ID=520073103
```

2. **啟用 Mini App**

編輯 `trader_config.py`:
```python
TG_MINI_APP_ENABLED = True
TG_MINI_APP_BASE_URL = "https://your-domain.com"  # 須為 HTTPS（Telegram 要求）
```

3. **重啟應用**

```bash
python app.py
```

### 用戶流程

#### 場景 1：文字命令 (當前)

```
用戶: /analyze NVDA
Bot: ⏳ 分析中...
Bot: [文字分析結果]
```

#### 場景 2：Mini App (新)

```
用戶: /analyze NVDA
Bot: [帶 Mini App 按鈕的訊息]
用戶: [點擊 "打開 Mini App" 按鈕]
Telegram: [在行內瀏覽器中打開 Mini App]
Mini App: 
  1. 讀取 Telegram.WebApp.initData
  2. 驗證簽名 (HMAC-SHA256)
  3. 獲取會話 token
  4. 顯示分析結果
```

---

## 技術細節

### 簽名驗證流程

```
1. Telegram 客戶端生成 initData:
   user={"id":520073103,...}&auth_date=1700000000&hash=ABC123...

2. Mini App 發起請求:
   POST /api/tg/init
   {initData: "user=...&hash=ABC123..."}

3. 後端驗證:
   a. 提取 hash (ABC123...)
   b. 提取其他參數 (user, auth_date)
   c. 構建簽名字符串: "auth_date=1700000000\nuser=..."
   d. 計算: HMAC-SHA256(key=SHA256(bot_token), msg=簽名字符串)
   e. 比較: 計算值 == 提取的 hash
   f. 如果匹配 → ✅ 驗證成功

4. 返回會話 token:
   {ok: true, token: "...", user: {...}}
```

### 數據流

```
[Telegram WebApp SDK]
        ↓
   [Mini App HTML]
        ↓
   [POST /api/tg/init] ← 驗證簽名
        ↓
   [獲取 token]
        ↓
   [POST /api/tg/analyze/<ticker>] ← 執行分析
        ↓
   [JSON 分析結果]
        ↓
   [UI 渲染 (SEPA/QM/ML)]
```

---

## 已知限制 & 待處理項目

### 🔴 限制

1. **HTTPS 要求**
   - Telegram Mini App 要求 HTTPS 公開 URL
   - 本地開發環境需要使用 ngrok/tunnel 進行測試

2. **白名單驗證**
   - Mini App 仍需檢查 `approved_chat_ids.json`
   - 未批准的用戶會被拒絕

3. **會話過期**
   - Token 目前無過期時間
   - 建議添加 JWT 與過期時間

### 🟡 建議改進

| 優先級 | 項目 | 描述 |
|--------|------|------|
| 高 | WebSocket 支持 | 實時掃描進度推送 |
| 高 | 會話持久化 | 保存用戶偏好設置 |
| 中 | 圖表集成 | 內嵌 TradingView Lightweight Charts |
| 中 | 多語言支持 | 繁体中文 / 英文切換 |
| 低 | Webhook 遷移 | 用 Webhook 替換 Polling (更快回應) |

---

## 測試

### 本地測試

```bash
# 1. 啟動應用
python app.py

# 2. 運行單元測試
python tests/test_tg_mini_app.py

# 3. 驗證路由
curl http://localhost:5000/api/tg/init -X POST \
  -H "Content-Type: application/json" \
  -d '{"initData":"..."}'
```

### 生產部署 (使用 ngrok)

```bash
# 1. 安裝 ngrok
# https://ngrok.com/

# 2. 暴露本地端口
ngrok http 5000

# 输出: https://random-id.ngrok.io

# 3. 更新配置
TG_MINI_APP_BASE_URL=https://random-id.ngrok.io

# 4. 重啟應用並在 Telegram 中測試
```

---

## 文件變更摘要

### 新增文件
- ✅ `templates/tg_app_shell.html` (445 行)
- ✅ `templates/tg_app_error.html` (50 行)
- ✅ `tests/test_tg_mini_app.py` (150 行)

### 修改文件
- ✅ `app.py` (+180 行) - 添加 3 個新路由
- ✅ `trader_config.py` (+5 行) - 添加 Mini App 配置
- ✅ `modules/telegram_bot.py` (+30 行) - webAppInfo 支持

### 總計
- **+680 行代碼**
- **0 個破壞性更改**
- **向後兼容** (可選啟用)

---

## 下一步行動

### 立即可做

1. ✅ **測試簽名驗證**
   ```bash
   python tests/test_tg_mini_app.py
   ```

2. ✅ **檢查路由**
   ```bash
   python app.py
   # 訪問 http://localhost:5000/tg/app?initData=test
   ```

3. ✅ **驗證配置**
   - 確認 `.env` 中有 `TG_BOT_TOKEN` 和 `TG_ADMIN_CHAT_ID`

### 部署前

1. **配置 HTTPS**
   - 使用 ngrok/Cloudflare Tunnel 或真實域名

2. **啟用 Mini App**
   - 設置 `TG_MINI_APP_ENABLED = True`
   - 更新 `TG_MINI_APP_BASE_URL` 為實際 HTTPS 地址

3. **測試完整流程**
   - 在 Telegram 中訪問 `/analyze NVDA`
   - 點擊 Mini App 按鈕
   - 驗證簽名與分析結果

---

## 支持

如有問題，請檢查：

1. **logs/** 目錄的錯誤日誌
2. **Telegram Bot API** 文檔: https://core.telegram.org/bots/webapps
3. **此項目的 GitHub Issues**

---

## 許可

本項目遵循原項目許可。Telegram Mini App 部分由 GitHub Copilot 實施。

**最後更新**: 2024 年 (根據當前日期)

