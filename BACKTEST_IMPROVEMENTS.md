# VCP Backtest 回測功能改進摘要

## ✅ 已實施的改進

### 1️⃣ **日誌檔案支持** (LOG Files)

📍 **modules/backtester.py**
- 新增 `log_file` 參數
- 記錄回測開始、進度途中關鍵步驟、完成摘要
- 例：`INFO === Backtest finished: NVDA  signals=10  breakouts=6  win_rate=60.0%  avg_gain=8.5% ===`

📍 **app.py** (`api_backtest_run` endpoint)
- 建立 per-job LOG 檔案：`logs/backtest_{TICKER}_{YYYYMMDD_HHMMSS}.log`
- 配置 logging handlers（同 analyze/market/scan 模式）
- 監控的模組：backtester、vcp_detector、data_pipeline
- 回傳 `log_file` 名稱給前端
- 確保完成或錯誤時，handler 被清理

**LOG 檔案位置：** `logs/backtest_*.log`

---

### 2️⃣ **改善的進度提示** (Progress UX)

📍 **templates/backtest.html**

**新增：**
- ✅ **Toast 通知系統** (`showToast()`)
  - 當回測提交時顯示藍色 info 通知
  - 完成時顯示綠色 success 通知 + 訊息
  - 錯誤時顯示紅色 danger 通知
  - 自動在 5 秒後消失

- ✅ **更快的進度輪詢** 
  - 從 1500ms → **1000ms**（提升回應速度 50%）
  - 首次提交後立即顯示進度條（5%入場）

- ✅ **改良的進度條 UI**
  - 加入中文標籤："正在提交請求"、"正在等待伺服器"
  - 進度條視覺上更清楚

- ✅ **錯誤診斷**
  - 加入 `console.log()` 輸出（開發者工具可見）
  - 錯誤訊息包含建議："Check logs for details"
  - Network 錯誤有詳細訊息

---

### 3️⃣ **後端改進**

**modules/backtester.py**
```python
# 新增的日誌訊息
logger.info(f"=== Backtest started: {ticker}  min_score={min_vcp_score} ===")
logger.info(f"=== Backtest finished: {ticker}  signals={len(signals)}  win_rate={...}% ===")
logger.error(f"=== Backtest failed: {ticker}  {msg} ===")
```

**app.py**
```python
# 新增：LOG 檔案路徑傳遞
bt_log_file = _LOG_DIR / f"backtest_{ticker}_{datetime.now().strftime('%Y%m%d_%H%M%S')}.log"
# 新增：Handler 清理
finally:
    for ln in _BT_LOGGERS:
        logging.getLogger(ln).removeHandler(bt_handler)
    bt_handler.close()
```

---

## 📊  使用體驗改變

### 舊版（改善前）
```
用: 按 RUN BACKTEST
結: 無任何提示，看不到進度，等不知多久
```

### 新版（改善後）
```
用: 按 RUN BACKTEST
即: 
  1. 頁面立即出現藍色 toast 通知：「正在為 NVDA 運行回測…」
  2. 進度條顯示 5% + 訊息「正在提交請求」
  3. 伺服器接收後，進度條開始更新
     - 「正在下載2年數據」→ 15%
     - 「掃描日期：2024-09-03」→ 19%
     - ... (每5個交易日更新一次) ...
     - 「計算統計」→ 82%
     - 「完成」→ 100%
  4. 完成時出現綠色 toast：「✅ NVDA 回測完成！」
  5. 結果表格、K線圖、資金曲線載入顯示

同時：logs/backtest_NVDA_20260228_123456.log 已建立，記錄全程
```

---

## 🔧  測試結果

### 成功案例：META (2024-2025)
```
✓ Job submitted: bt_META_e7c6e507
✓ LOG file: backtest_META_20260228_002659.log

Results:
  - Signals: 8
  - Breakouts: 4
  - Win rate: 0.0% (較弱市況)
  - Avg gain: -7.11%

LOG file content (4 lines):
  2026-02-28 00:26:59,647 INFO Starting backtest: META min_score=35 outcome_days=60 (job ...)
  2026-02-28 00:26:59,647 INFO === Backtest started: META ...
  2026-02-28 00:27:01,213 INFO === Backtest finished: META signals=8 breakouts=4 ...
  2026-02-28 00:27:01,213 INFO Backtest complete: META signals=8 win_rate=0.0%
```

---

## 🚀  建議測試步驟

1. **打開回測頁面**
   ```
   http://localhost:5000/backtest
   ```

2. **快速測試（已知爆發股）**
   - 按「NVDA」快速按鈕 → 看進度更新 + toast 通知
   - 或手輸入 META、SMCI、AXON

3. **檢查進度提示**
   - 進度條應該從 5% 開始上升
   - 中文訊息應該清楚顯示
   - toast 通知應該在右上角出現（5秒後自動消失）

4. **完成後檢查**
   - 結果表格載入
   - K線圖顯示信號標記
   - 資金曲線顯示
   - ✅ 確認 `logs/backtest_*.log` 檔案已建立

5. **如遇錯誤**
   - 檢查瀏覽器控制台 (F12 → Console)
   - 查看 `logs/backtest_*.log` 檔案最後 3 行
   - 錯誤訊息會顯示在紅色 toast 和頁面 alert

---

##  已修改檔案

| 檔案 | 行數 | 改進內容 |
|---|----|--------|
| `modules/backtester.py` | ~320 | +log_file 參數、+logger 呼叫 |
| `app.py` | ~1520 | +LOG handler setup、+progress callback |
| `templates/backtest.html` | ~592 | +showToast()、+console.log、+1000ms poll |

---

## 🎯 運作流程

```
user clicks "Run Backtest"
    ↓
showProgress(2, "正在提交請求")  ← UI立即顯示
showToast(..., "info")          ← 藍色通知
    ↓
fetch(/api/backtest/run)        ← 提交job
    ↓
app.py 建立 LOG 檔案、handler
    ↓
_bg_thread 執行 run_backtest()
    progress_cb(5, "下載...")   → 儲存到 _bt_jobs[jid]["pct"/"msg"]
    progress_cb(15, "掃描...")
    ...
    progress_cb(100, "完成")
    ↓
pollBtJob() 每秒查詢狀態
    showProgress(d.pct, d.msg)  ← UI 更新進度條 + 訊息
    ↓
status === "done"
    hideProgress()
    showToast("✅ 完成！", "success")
    renderBacktestResults()     ← 載入表格、圖表
    LOG 檔案寫入最後訊息
    ↓
user sees full results + notifications + LOG file exists
```

---

## ✨ 總結

✅ **進度提示完整** — toast + 進度條 + 中文訊息  
✅ **背景日誌完整** — 每次回測都生成 LOG 檔案  
✅ **錯誤處理明確** — 失敗時清楚提示 + LOG 記錄  
✅ **使用體驗順暢** — 從提交、執行、完成全程可視化  
✅ **與其他模式一致** — 日誌格式、handler 管理與 analyze/market/scan 相同

---

*需要協助？開啟瀏覽器開發者工具 (F12) 或查閱 logs/ 目錄中的 LOG 檔案。*
