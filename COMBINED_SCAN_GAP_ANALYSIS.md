# Combined Scan — 功能對等性分析報告

## 檢查日期：2026-03-01

---

## 🔴 **QM Scan 有但 Combined Scan 缺少的元素**

### 1. **過濾控制項** ⚠️ 關鍵
- `minStar` 下拉選單：★3 / ★3.5 / ★4 / ★4.5 / ★5+
- `topN` 下拉選單：25 / 50 / 100
- **Impact**: 用戶無法過濾結果，只能看全部

### 2. **結果摘要行** ⚠️ 重要
```html
<div class="metric-card">
  <div class="metric-val" id="metPassCount">—</div>
  <div class="metric-lbl">通過股票 Passed</div>
</div>
```
- metPassCount — 通過股票數目
- met5Star — 5★ 以上數目
- metAvgStar — 平均星級
- metScanTime — 掃描時間戳記
- summaryRow div 容器

### 3. **詳細診斷面板** 📋 增強
```
詳細診斷日誌 Detailed Diagnostics (toggleable)
├─ diagnosticsPanel (可展開 collapsible)
├─ diagnosticsContent (日誌文本)
└─ diagToggleIcon (勾選圖示)
```

---

## 🔴 **SEPA Scan 有但 Combined Scan 缺少的元素**

### 1. **高級過濾器面板** ⚠️ 關鍵
```html
<div id="filterPanel" class="card mb-3">
  <!-- SEPA Score min/max -->
  <!-- Trend Score min/max -->
  <!-- Fund Score min/max -->
  <!-- RS Rank min/max -->
  <!-- Price min/max -->
  <!-- Pivot min/max -->
  <!-- VCP Grade buttons (A/B/C/None) -->
  <!-- Trend Template buttons (Pass/Fail) -->
  <!-- Sector multi-select -->
</div>
```
**Impact**: 掃描後無法篩選結果，不符合 MVP 預期

### 2. **Cache 信息面板** ℹ️ 實用
- cacheInfoPanel 顯示：
  - RS Rankings 快取狀態
  - Price Cache 快取狀態
  - Fundamentals 快取狀態
  - Est. Speed 估計時間
  - cacheHints 提示文本

### 3. **API Rate Limits 信息面板** 📖 實用
- rateLimitInfo (可展開)
  - yfinance 速率限制說明
  - finvizfinance 速率限制說明
  - 使用建議與最佳實踐

### 4. **掫描摘要圖表** 📊 視覺化
```
sectorDonutChart — Sector 分布圓形圖
scoreHistChart — SEPA Score 分布直方圖
```
- 需要 Chart.js 庫
- scanSummaryCharts 容器

### 5. **"Show All Scored" 功能** 📈 增強
- 按鈕切換顯示全部評分股票（包含未通過）
- updateAllScoredBadge() 更新計數器
- _allScoredData 與 _scanData 並行追蹤
- rowBelow-threshold 樣式用於未通過的股票

### 6. **Live Log 面板** 🔍 偵錯
```html
<div id="liveLogWrap">
  <div id="liveLog"><!-- 逐行日誌輸出 --></div>
</div>
```
- 即時顯示掫描進度（逐股票）
- _lastLogTicker 追蹤去重

---

## ✅ **目前 Combined Scan 已有的功能**

| 功能 | SEPA | QM | 狀態 |
|-----|------|-----|------|
| 基本掃描按鈕 | ✔️ | ✔️ | ✅ 完整 |
| Source 選擇器 | ✔️ | ✔️ | ✅ 完整 |
| Refresh RS 核取方塊 | ✔️ | — | ✅ 完整 |
| 進度條 | ✔️ | ✔️ | ✅ 完整 |
| 計時藥丸 (S1/DL/Par/Total) | ✔️ | — | ✅ 完整 |
| SEPA 表格（12 列） | ✔️ | — | ✅ 完整 |
| QM 表格（12 列） | — | ✔️ | ✅ 完整 |
| Market 環境標籤 | ✔️ | — | ✅ 完整 |
| CSV 匯出 | ✔️ | ✔️ | ✅ 完整 |
| localStorage 持久化 | ✔️ | ✔️ | ✅ 完整 |
| 時鐘顯示 (HK/US) | ✔️ | — | ✅ 完整 |
| Add to Watchlist 按鈕 | ✔️ | — | ✅ 完整 |

---

## 📊 **功能對等性評分**

| 面向 | SEPA | QM | Overall |
|-----|------|-----|---------|
| 基本掃描 | 100% | 100% | ✅ |
| 結果過濾 | **50%** ⚠️ | **0%** ⚠️ | **25%** |
| 診斷資訊 | **70%** ⚠️ | **0%** ⚠️ | **35%** |
| 統計摘要 | **60%** ⚠️ | **100%** ✔️ | **80%** |
| 視覺化 | **0%** ⚠️ | **0%** ⚠️ | **0%** |
| **整體** | **76%** | **80%** | **78%** ⚠️ |

---

## 🎯 **建議修複優先級**

### **P0 — 必須** (阻止內部測試)
1. ✅ **QM 過濾器** — minStar + topN 下拉選單
2. ✅ **SEPA 高級過濾器** — Score/TT/Sector 等
3. ✅ **結果摘要統計** — 兩個 tab 都需要
4. ✅ **Show All Scored** — SEPA 應顯示全部評分

### **P1 — 重要** (使用便利性)
5. Cache 信息面板 — SEPA 的快取狀態指示
6. Diagnostics 面板 — QM 的詳細日誌
7. API Rate Limits 標籤頁

### **P2 — 增強** (可選)
8. 提交圖表 (Chart.js)
9. Live Log 面板

---

## ⚙️ **修複清單**

- [ ] 增加 QM tab 的 minStar / topN 過濾器
- [ ] 增加 SEPA tab 的高級過濾面板 (collapsible)
- [ ] 為 SEPA 表格添加 filterTable() 邏輯
- [ ] 為 QM 表格添加 filterResults() 邏輯
- [ ] 添加 updateSummaryCards() 顯示摘要統計
- [ ] 實現 "Show All" 按鈕切換（SEPA）
- [ ] 添加 Cache Info 面板 (SEPA)
- [ ] 添加 Diagnostics 面板 (QM)
- [ ] 添加 API Rate Limits 標籤頁
- [ ] （可選）添加 Chart.js 圖表

---

## 💡 **驗收標準**

**Combined Scan 應該能 100% 替代：**
- ✅ `/scan` — 執行 SEPA 掫描、顯示結果、過濾、導出
- ✅ `/qm/scan` — 執行 QM 掫描、顯示結果、過濾、導出
- ✅ 相同的過濾能力
- ✅ 相同的統計信息
- ✅ 相同的診斷信息
- ✅ 相同的 UX 體驗（如果有按鈕/面板，應該在 Combined 中也能找到）
