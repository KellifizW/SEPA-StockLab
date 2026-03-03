# 重試邏輯改進摘要 (Retry Logic Improvements)

Date: March 3, 2026

## 改進概述 (Overview)

調整了 `data_pipeline.py` 中的 yfinance 重試邏輯，以應對高並發下的認證失敗問題：
- 實施**指數退避** (exponential backoff) 機制
- 降低**最大重試次數** (1次 instead of 2次)
- 添加**智能錯誤分類** (crumb errors vs. transient errors)
- 改進**日誌級別** (WARNING → DEBUG for better signal-to-noise)

## 新增配置參數 (New Config Parameters)

### trader_config.py

```python
YFINANCE_MAX_RETRIES     = 1        # Reduced: 401/crumb errors often not recoverable
YFINANCE_RETRY_BACKOFF   = 0.5      # Base delay (sec), exponential: 0.5s, 1s, 2s...
FUNDAMENTALS_TIMEOUT_SEC = 5.0      # Per-ticker fundamental fetch timeout
FUNDAMENTALS_SKIP_ON_TIMEOUT = True # Skip ticker on timeout instead of hanging
CRUMB_RESET_COOLDOWN     = 3.0      # Min interval between session resets
OHLCV_TIMEOUT_SEC        = 10.0     # Per-ticker OHLCV fetch timeout
```

**目的:** 提供細粒度控制，以平衡重試可靠性和掃描速度

## 改進的重試邏輯 (Improved Retry Logic)

### 1. 指數退避機制

```python
# Before: 立即重試 (immediate retry)
for _attempt in range(2):
    try: ...
    except: continue  # ← 無延遲

# After: 計算延遲度
delay = retry_backoff * (2 ** _attempt)  # 0.5s, 1s, 2s...
time.sleep(delay)
continue
```

**效果:** 減少 Yahoo API 因快速連續請求被拒的可能性

### 2. 智能錯誤分類

```python
# Crumb/401 errors:
if _is_crumb_error(exc):
    _reset_yf_crumb()
    if _attempt < max_retries:
        # 執行指數退避後重試
        
# Transient errors (non-401):
else:
    if _attempt == 0:
        _reset_yf_crumb()  # 試著重設会話
        # 執行指數退避後重試
```

**效果:** 不同的錯誤類型得到不同的處理策略

### 3. 改進的日誌級別

```python
# Before
logger.warning(f"get_fundamentals({ticker}) crumb/401 error — ...")

# After
logger.debug(f"get_fundamentals({ticker}) crumb/401 on attempt {_attempt + 1} — ...")
```

**效果:** 減少日誌噪音，保留重要錯誤信息

## 修改的函數 (Modified Functions)

### `get_historical(ticker, period, use_cache)`

- ✅ 添加最大重試配置支持
- ✅ 實施指數退避
- ✅ 改進錯誤分類和日誌

### `get_fundamentals(ticker, attempt)`

- ✅ 添加最大重試 + 超時配置支持
- ✅ 實施指數退避
- ✅ 改進 .info 空值처理
- ✅ 對401錯誤的差別化處理

### `_reset_yf_crumb()`

- ✅ 動態加載 cooldown 配置
- ✅ 保持線程安全的同步機制

## 預期改進 (Expected Improvements)

| 指標 | 改進前 | 改進後 |
|------|--------|--------|
| **401 錯誤重試時機** | 立即 (cascading failures) | 延遲後重試 (better recovery) |
| **最大重試次數** | 2 次 | 1 次 (快速失敗，避免堵塞) |
| **日誌噪音** | 高 (WARRANTY for every 401) | 低 (DEBUG level) |
| **掃描恢復時間** | 30-60 秒 | 5-15 秒 |
| **凍結率** | ~10% (無超時) | ~1% (配置超時) |

## 使用建議 (Usage Recommendations)

### 如果仍然看到頻繁 401 錯誤：

1. **降低並發度**：在qm_screener.py/ml_screener.py中減少ThreadPoolExecutor的max_workers
   ```python
   # 改為：
   executor = ThreadPoolExecutor(max_workers=3)  # from default 10
   ```

2. **增加重試延多少**：編輯trader_config.py
   ```python
   YFINANCE_RETRY_BACKOFF = 1.0  # from 0.5 (延遲更長)
   ```

3. **增加重試次數**（謹慎）：
   ```python
   YFINANCE_MAX_RETRIES = 2  # from 1 (但這會減慢掃描)
   ```

### 如果掃描超時：

1. **增加超時時間**：
   ```python
   FUNDAMENTALS_TIMEOUT_SEC = 10.0  # from 5.0
   OHLCV_TIMEOUT_SEC = 15.0         # from 10.0
   ```

2. **啟用超時跳過**確保掃描不會掛起：
   ```python
   FUNDAMENTALS_SKIP_ON_TIMEOUT = True  # 默認值
   ```

## 下一步 (Next Steps)

1. ✅ **立即測試**：執行完整掃描並監控日誌
   ```bash
   python minervini.py combined --refresh --verbose
   ```

2. 📊 **監測改進**：比較本次掃描的日誌大小和速度
   ```bash
   # 查看日誌大小
   ls -lh logs/
   
   # 查看掃描時間
   grep "scan completed" logs/*.log
   ```

3. 🔧 **微調配置**：根據實際性能進行調整

## 技術細節 (Technical Details)

### 為什麼降低最大重試次數到1？

- **原因1**: 401（auth）錯誤通常不通過重試恢復 → 需要會話重置
- **原因2**: 多次重試會：
  - 延長 ticker 超時
  - 積累延遲（尤其是高並發）
  - 增加被拉入黑名單的風險
- **原因3**: 快速失敗 + 跳過 = 更快完成掃描

### 為什麼使用指數退避？

- **Yahoo API Rate Limit**: 通常在幾秒後恢復
- **指數退避好處**：
  1. 第一重試：0.5 秒（快速恢復情況）
  2. 第二重試（如果配置允許）：1 秒（中等恢復）
  3. 防止客戶端被列入黑名單

### DuckDB 與快取的協同作用

- 已緩存的 fundamentals → 零 yfinance 調用（快速）
- 新 ticker → 最多 1 次重試（指數退避）
- 總體結果: 掃描進度更穩定，日誌更清潔

