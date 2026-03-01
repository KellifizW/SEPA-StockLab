# 改進的錯誤日誌記錄與偵錯能力

## 問題

當combined scan執行期間發生"The truth value of a DataFrame is ambiguous"錯誤時，該錯誤**沒有被記錄到log文件中**，導致無法診斷和追蹤。

## 根本原因分析

1. **多線程異常未被捕獲**: `ThreadPoolExecutor.result()`拋出的異常沒有被try/except包圍
2. **日誌處理器不完整**: 一些相關模塊（如`rs_ranking`, `market_env`）沒有被添加到日誌處理器
3. **日誌級別不一致**: 一些關鍵函數沒有足夠的DEBUG級日誌
4. **缺少堆棧跟蹤**: 異常被捕獲但未記錄完整的`exc_info`

## 實施的改進

### 1. **App.py 改進** (`app.py`)

#### 更完整的異常捕獲
```python
# 之前:
except Exception as exc:
    logging.exception("Combined scan thread error")

# 之後:
except Exception as exc:
    logging.exception("[CRITICAL] Combined scan thread encountered unhandled exception:")
    logging.error("[CRITICAL] Exception type: %s", type(exc).__name__)
    logging.error("[CRITICAL] Exception message: %s", str(exc))
    logging.error("[CRITICAL] Full traceback:\n%s", traceback.format_exc())
```

#### 更多日誌記錄點
- 標記 job 開始與參數
- 記錄每個主要處理階段（conversion, saving, mirroring）
- 在 finally 塊中記錄清理操作

#### 改進的日誌處理器配置
```python
log_handler = logging.FileHandler(combined_log_file, encoding="utf-8")
log_handler.setLevel(logging.DEBUG)
# 更詳細的format: 時間戳、級別、模塊、函數名、行號、消息
log_formatter = logging.Formatter(
    fmt="%(asctime)s [%(levelname)s] %(name)s | %(funcName)s:%(lineno)d | %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S"
)
```

#### 添加更多日誌模塊
```python
_COMBINED_LOGGERS = [
    "modules.combined_scanner", "modules.screener", "modules.qm_screener", 
    "modules.data_pipeline", "modules.rs_ranking", "modules.market_env",  # 新增
    "modules.qm_analyzer"  # 新增
]
```

### 2. **Combined Scanner 改進** (`modules/combined_scanner.py`)

#### 多線程結果捕獲
```python
# ThreadPoolExecutor結果處理 - 之前未包圍在try/except中
try:
    sepa_thread.result(timeout=600)
except Exception as e:
    logger.error("SEPA thread exception: %s", e, exc_info=True)
    sepa_error = str(e)
```

#### 詳細的執行日誌
```python
logger.info("Starting Stage 2 with %d tickers", len(s1_tickers))
s2_results = run_stage2(...)
logger.info("Stage 2 completed: %d passing TT1-TT10", len(s2_results))

# 在DataFrame操作前後記錄
logger.debug("Stripping 'df' column from %d s2_results", len(s2_results))
_safe_s2 = [{k: v for k, v in r.items() if k != "df"} for r in s2_results]
sepa_df_all = pd.DataFrame(_safe_s2) if _safe_s2 else pd.DataFrame()
logger.info("Assembled all_scored DataFrame: shape %s", sepa_df_all.shape)
```

### 3. **Data Pipeline 改進** (`modules/data_pipeline.py`)

#### 批量下載異常捕獲
```python
# 更詳細的錯誤記錄，包含完整traceback
except Exception as exc:
    logger.error(
        "[Batch %d] Download error: %s: %s",
        bi+1, type(exc).__name__, exc,
        exc_info=True  # 記錄完整堆棧跟蹤
    )
```

#### 下載流程的DEBUG日誌
```python
logger.debug(f"[Batch {bi+1}] Downloading {len(batch)} tickers: {batch}")
raw = yf.download(...)
logger.debug(f"[Batch {bi+1}] Download returned type {type(raw).__name__}")
# 明確檢查結果
if raw is None:
    logger.warning(f"[Batch {bi+1}] yf.download returned None")
elif raw.empty:
    logger.warning(f"[Batch {bi+1}] yf.download returned empty DataFrame")
```

#### 技術指標計算時的異常捕獲
```python
try:
    logger.debug(f"[Batch Single] {tkr} calling get_technicals()...")
    tech_df = get_technicals(df_t)
    logger.debug(f"[Batch Single] {tkr} get_technicals returned shape {tech_df.shape}")
    result[tkr] = tech_df
except Exception as tech_err:
    logger.error(
        "[Batch Single] %s get_technicals failed: %s: %s",
        tkr, type(tech_err).__name__, tech_err,
        exc_info=True
    )
```

## 日誌流程改進

### 之前的流程
```
scan execution
    ↓ (異常發生)
    ↓ (未被捕獲 或 被吃掉)
    ↓ (不出現在任何地方)
用戶看不到任何錯誤信息 ❌
```

### 改進後的流程
```
scan execution
    ↓
log "Starting combined scan..."
    ↓ (異常發生)
    ↓ (被try/except捕獲)
log "Exception type: DataFrame ambiguity"
log "Full traceback: ..."
    ↓
寫入log文件 ✓
返回error給用戶 ✓
用戶可以查看logs/combined_scan_*.log ✓
```

## 如何使用改進的日誌

### 查看日誌文件位置
- `logs/combined_scan_{job_id}_{timestamp}.log`
- 例: `logs/combined_scan_abc123_2026-03-01T14-35-42.log`

### 找出具體錯誤

1. **進行combined scan**後等待完成
2. 若發生錯誤，檢查log文件:
   ```bash
   tail -100 logs/combined_scan_*.log
   ```

3. 尋找關鍵字:
   - `[CRITICAL]` - 未被處理的異常
   - `[ERROR]` - 已捕獲但important的錯誤
   - `[WARNING]` - 可能的問題
   - `Full traceback:` - 完整的堆棧跟蹤

### 例子

```log
2026-03-01 14:35:42 [INFO] modules.combined_scanner | run_combined_scan:108 | Starting combined scan...
2026-03-01 14:35:45 [INFO] modules.combined_scanner | run_combined_scan:161 | [Combined S1] SEPA: 850 candidates
2026-03-01 14:36:12 [ERROR] modules.data_pipeline | batch_download_and_enrich:1047 | [Batch 3] Download error: ValueError: The truth value of a DataFrame is ...
2026-03-01 14:36:12 [ERROR] modules.data_pipeline | batch_download_and_enrich:1048 | Full traceback:
  Traceback (most recent call last):
    File "modules/data_pipeline.py", line 1030, in batch_download_and_enrich
      if df:  # <-- 這是問題!
  ValueError: The truth value of a DataFrame is ambiguous...
```

## 技術改進總結

| 改進項目 | 之前 | 之後 |
|---------|------|------|
| 多線程異常 | 不被捕獲 | 被try/except包圍並記錄 |
| 日誌模塊覆蓋 | 4個模塊 | 7個模塊 |
| 異常詳細程度 | 只有消息 | 完整堆棧跟蹤+類型+行號 |
| 執行進度記錄 | 無 | 詳細的階段和計數 |
| 日誌時間戳 | 無秒級精度 | 包含函數名和行號 |
| 錯誤頻率 | 隱性bug | 顯性記錄 |

## 對未來開發的指導

1. **所有外部API調用** 都應該被try/except包圍，並記錄完整的`exc_info`
2. **關鍵數據轉換** 應該在before/after記錄日誌
3. **異常中的DataFrame** 應該記錄其shape和columns
4. **多線程操作** 必須在`.result()`上進行異常處理
5. **較長的操作** 應該添加進度日誌點

## 立即檢查

下次執行combined scan時，可以在同一個終端窗口中：
```bash
tail -f logs/combined_scan_*.log
```
這樣可以實時看到所有日誌，包括任何異常。
