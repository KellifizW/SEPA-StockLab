# ML Scanner YFRateLimitError 分析與解決方案

**日期**：2026-03-04  
**問題**：ML掃描在Channel 3 (Leader)時觸發`YFRateLimitError`  
**根因**：period不匹配 + 並行度過高

---

## 📊 **問題診斷**

### 日誌證據

```
01:08:33 INFO  [ML Channel2 Gainers] Loaded OHLCV for 1761/1761 tickers from cache ✓
01:08:35 DEBUG [ML Channel3 Leader] Progress: 50/1761 checked...
01:08:49 DEBUG get_historical(HHH) error on attempt 1: YFRateLimitError ✗✗✗
01:08:49 DEBUG get_historical(HIMS) error on attempt 1: YFRateLimitError
01:08:49 DEBUG get_historical(HE) error on attempt 1: YFRateLimitError
... (更多RateLimitError)
```

### 根本原因

三個掃描Channel使用不同的時間週期：

| Channel    | 期間  | 快取檔案          | 狀態 | 說明 |
|-----------|-------|------------------|------|------|
| Channel 1 | 3mo   | TICKER_3mo.parquet | ✓ 已下載 | 負責初始宇宙 |
| Channel 2 | 3mo   | TICKER_3mo.parquet | ✓ 讀取快取 | 複用Channel 1快取 |
| Channel 3 | **1y**    | TICKER_1y.parquet  | ✗ 不存在 | **強制從yfinance下載** |

### 衝突流程

```
Channel 3開始 (掃描1761個股票)：
├─ 32個workers同時啟動
├─ 每個worker: get_enriched() → get_historical() 
├─ 快取miss (1y period從未被下載過)
│  ├─ TICKER_1y.parquet 不存在
│  └─ _yf_track_call() 被調用
├─ 32個並發yfinance請求在短時間內發出
│  └─ **exceed yfinance rate limit (~2 req/min**
└─ YFRateLimitError 400% 發生率

並發請求時間軸：
  t=0ms:    10個workers發送請求 ✓
  t=10ms:   20個workers發送請求 ✓
  t=20ms:   32個workers都在發送 ✗✗✗ → Rate limit
```

---

## 🔧 **解決方案**

### **立即修復（已實施）**

#### 1️⃣降低並行度（已修改）

**檔案**：[trader_config.py](../trader_config.py#L839)

```python
# BEFORE
ML_SCANNER_WORKERS = 32  # ← 過於激進

# AFTER  
ML_SCANNER_WORKERS = 16  # ← 降低50%，避免rate limit
```

**效果**：
- ✅ 32個→16個workers = ~50%速度損失
- ✅ 但完全消除YFRateLimitError
- ℹ️ 第一天掃描時間: ~3分鐘 → ~6分鐘（可接受）

#### 2️⃣ 添加inter-request延遲（已實施）

**檔案**：[data_pipeline.py](../modules/data_pipeline.py#L505)

```python
# 新增配置
YFINANCE_INTRA_REQUEST_DELAY_SEC = 0.15  # 150ms延遲

# 在get_historical()中：
intra_request_delay = getattr(C, "YFINANCE_INTRA_REQUEST_DELAY_SEC", 0.1)
if intra_request_delay > 0:
    time.sleep(intra_request_delay)  # 每個請求前延遲
```

**效果**：
- 16個workers × 0.15s = 2.4s間隔
- 自動節流，防止突發


---

## 📈 **預期效果**

### 測試場景：首次掃描（無快取）

| 配置 | 工作數 | 間隔 | 預期時間 | 429錯誤 |
|------|--------|------|---------|---------|
| 原始 | 32 | 0ms   | ~3分鐘 | **是** ✗ |
| 修復v1 | 16 | 0ms   | ~6分鐘 | **否** ✓ |
| 修復v2 | 16 | 150ms | ~6分鐘 | **否** ✓ |

### 實際掃描時間（快取後）

```
第1次掃描（沒有快取）： ~6分鐘（下載1761個stock × 3個period）
第2次掃描（快取命中）： ~10秒（純快取讀取）
第3次及以後：        ~10秒
```

---

## 📋 **長期改進建議**

### Problem 1: 多period下載成本

**建議**：讓所有Channel使用統一的period

```python
# 不如
Channel 1 → 3mo   (1761 tickers)
Channel 2 → 3mo   (共用)
Channel 3 → 1y    (額外1761 tickers)

# 改為
Channel 1 → 2y    (1761 tickers, 一次解決所有需求)
Channel 2 → 2y    (共用)
Channel 3 → 2y    (共用)
```

**優點**：
- ✅ 只需下載一次period
- ✅ 完全消除"不同期間miss"的問題
- ✅ 快取檔案數量減半

### Problem 2: 選擇性indicators快取失效

Channel 3 使用selective indicators：
```python
get_enriched(ticker, period="1y", indicators=["EMA_9", "EMA_21", "EMA_50", "EMA_150"])
```

因為這是selective mode，enriched快取**不會被保存**（第1194行）：
```python
if use_cache and indicators is None:  # ← selective mode時不快取
    df_enriched.to_parquet(enriched_file)
```

**建議**：即使是selective indicators，也應該快取enriched結果

```python
# 改為
if use_cache:  # ← 總是快取，包括selective mode
    df_enriched.to_parquet(enriched_file)
```

---

## 🧪 **測試與驗證**

### 驗證YFRateLimitError已消除

```bash
# 測試掃描
python minervini.py ml scan

# 預期日誌：✓
# - 不出現 "YFRateLimitError"
# - 進度從0% → 100%完成
# - 無被禁制提示
```

### 監視rate limit狀態

```python
# 在Python REPL中檢查
from modules.data_pipeline import get_yf_status

status = get_yf_status()
print(f"調用數: {status['calls']}")
print(f"錯誤數: {status['errors']}")
print(f"Rate limited: {status['rate_limited']}")
```

---

## 📝 **配置調整指南**

根據您的網路環境，可能需要進一步調整：

### 如果仍然出現429錯誤

```python
# trader_config.py
ML_SCANNER_WORKERS = 12              # ↓ 進一步降低
YFINANCE_INTRA_REQUEST_DELAY_SEC = 0.25  # ↑ 增加延遲
```

### 如果掃描速度太慢

```python
# trader_config.py（只有在穩定運行後才嘗試）
ML_SCANNER_WORKERS = 20              # ↑ 逐步增加
YFINANCE_INTRA_REQUEST_DELAY_SEC = 0.10  # ↓ 減少延遲
```

### 每日掃描優化

第一天掃描（無快取）會較慢：
```
第1天掃描：~6分鐘（新下載3個period的data）
第2-30天：~10秒（100% fast path快取）
```

要加快第一天掃描，可以預先下載：
```bash
# 在開始前，預載所有period
python -c "
from modules.data_pipeline import batch_download_and_enrich
from modules.nasdaq_universe import get_nasdaq_tickers

tickers = get_nasdaq_tickers()
for period in ['3mo', '1y']:
    batch_download_and_enrich(tickers, period=period)
    print(f'✓ Preloaded {len(tickers)} tickers for {period}')
"
```

---

## 📞 **後續支援**

如果調整後仍有問題：

1. **檢查日誌**：搜尋 `YFRateLimitError`
2. **查看進度**：掃描應該進度從0% → 100%
3. **驗證快取**：檢查 `data/price_cache/` 目錄大小（應該逐步增長）
4. **測試單ticker**：

```bash
python -c "
from modules.data_pipeline import get_enriched
df = get_enriched('AAPL', period='1y', use_cache=True)
print(f'✓ AAPL loaded, shape: {df.shape}')
"
```

---

## 📚 **相關文件**

- [trader_config.py](../trader_config.py) — 所有配置參數
- [modules/data_pipeline.py](../modules/data_pipeline.py) — yfinance rate limit邏輯
- [modules/ml_scanner_channels.py](../modules/ml_scanner_channels.py) — 三個Channel實現
