# Live Log 修復 - 如何驗證與使用

## 快速驗證 (1 分鐘)

### Python 快速驗證
```bash
cd "c:\Users\kellywan\Saved Games\SEPA-StockLab"
python quick_verify.py
```

應該看到：
```
✓ SEPA module working
  Log lines captured: 1
  Sample: [11:22:18] [Test Stage] Testing message
✓ Fix verification successful!
```

## 完整測試 (5 分鐘)

### 1️⃣ 啟動 Web 服務器
```bash
python start_web.py
```

### 2️⃣ 打開 Combined Scan 頁面
在瀏覽器中訪問：
```
http://localhost:5000/combined_scan
```

### 3️⃣ 開始掃描
1. 點擊 "Run Combined Scan" 按鈕
2. Live log 區域會自動顯示並展開
3. 查看實時日誌輸出

### 4️⃣ 查看 SEPA Scan (可選)
訪問：
```
http://localhost:5000/scan
```

重複步驟 1-3

## 預期的日誌格式

### 日誌行示例
```
[11:22:18] [Stage 1 -- RS Rankings] Building / loading RS cache...
[11:22:18] [Stage 1 -- Coarse Filter] Querying finvizfinance screener...
[11:22:18] [Stage 1 -- Coarse Filter] 156 candidates
[11:22:18] [Stage 2 -- Batch Download] Downloaded 156 prices...
[11:22:18] [Stage 2 -- Trend Template] [1/156] Validating AAPL
[11:22:18] [Stage 2 -- Trend Template] AAPL: Validation passed
[11:22:18] [Stage 2 -- Trend Template] [50/156] Validating TSLA
[11:22:18] [Stage 3 -- SEPA Scoring] [1/50] Scoring AAPL
[11:22:18] [Stage 3 -- SEPA Scoring] AAPL: Scoring complete
...
[11:22:25] [Complete] Scan done: 42 final candidates
```

### 日誌格式說明
```
[時間戳] [階段名稱] [Ticker]: 消息

時間戳     — HH:MM:SS 格式，系統時間
階段名稱   — 掃描的當前階段
Ticker     — 正在處理的股票代碼（如果適用）
消息       — 詳細的進度或狀態信息
```

## UI 顏色編碼

觀察日誌顯示中的顏色：
- 🔵 **藍色** — 時間戳 `[HH:MM:SS]`
- 🔵 **淺藍** — 階段名稱 `[Stage]`
- 🟣 **紫色** — Ticker 代碼 `AAPL`, `TSLA` 等
- ⚪ **灰色** — 日誌消息文本

## 功能驗證檢查清單

| 項目 | 檢查 |
|------|------|
| Live log 自動顯示 | ✓ 掃描啟動時自動出現 |
| 實時更新 | ✓ 日誌每 2 秒更新一次 |
| 完整歷史 | ✓ 顯示從掃描開始的所有日誌 |
| 彩色編碼 | ✓ 時間戳、階段、ticker、消息有不同顏色 |
| 自動滾動 | ✓ 日誌自動滾動到最新條目 |
| 隱藏/顯示 | ✓ 點擊按鈕可切換日誌可見性 |
| 掃描完成 | ✓ 日誌顯示 "Complete" 和最終結果數 |

## 故障排除

### 日誌區域不顯示
- 確保 Web 服務器已重啟
- 清除瀏覽器快取（Ctrl+Shift+Delete）
- 刷新頁面

### 日誌顯示為空
- 檢查掃描是否實際在運行（進度條應該移動）
- 查看瀏覽器控制台是否有錯誤（F12）
- 檢查 Python 終端是否有錯誤信息

### 日誌截斷或丟失
- 這是預期的 — UI 顯示最後 50 行
- 完整的 200 行日誌保留在服務器內存
- 刷新頁面會丟失日誌（預期行為）

## 性能影響

✅ **最小化的性能開銷**
- 日誌記錄是異步的，不阻塞掃描
- UI 一次性渲染所有行（避免大量 DOM 操作）
- 內存使用受限於 200 行歷史

## 技術詳情

### API 端點
- `GET /api/scan/status/<job_id>` — 返回 SEPA 掃描進度
- `GET /api/combined/scan/status/<job_id>` — 返回組合掃描進度
- `GET /api/qm/scan/status/<job_id>` — 返回 QM 掃描進度
- `GET /api/ml/scan/status/<job_id>` — 返回 ML 掃描進度

所有端點的響應都包含 `progress.log_lines` 數組。

### 支持的掃描器
✅ SEPA Screener
✅ Combined Scanner
✅ QM Screener
✅ ML Screener

## 常見問題

**Q: 為什麼日誌之前不出現？**
A: 後端沒有收集或傳遞日誌行。現在它收集完整歷史並發送到前端。

**Q: 日誌會保存嗎？**
A: 不會。刷新頁面或關閉頁面會丟失日誌。這是預期的行為。

**Q: 我可以導出日誌嗎？**
A: 目前不能，但這是計劃中的未來改進。

**Q: 日誌有搜索功能嗎？**
A: 目前沒有，但可以使用瀏覽器的 "Find" 功能（Ctrl+F）在日誌中搜索。

**Q: 為什麼有些日誌行沒有 Ticker？**
A: 一些消息是關於階段整體進度的，而不是特定的股票。

## 支持的語言

日誌消息為英文，但 UI 按鈕和標籤支持雙語（繁体中文 + 英文）。

## 反饋和問題

如發現任何問題，請檢查：
1. Python 版本 (3.10+)
2. Flask 是否正在運行
3. 瀏覽器控制台是否有 JavaScript 錯誤
4. 網絡連接是否正常

---

**現在就試試 Live Log 功能！** 🚀
