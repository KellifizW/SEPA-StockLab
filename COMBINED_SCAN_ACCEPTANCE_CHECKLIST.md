# Combined Scan — 功能驗收檢查清單

## ✅ 新功能驗收標準

### **SEPA Tab 檢查**
- [ ] 掃描完成後，看到結果摘要行：
  - [ ] Passed 計數（通過股票數）
  - [ ] All Scored 計數（全部評分數）
  - [ ] Avg Score（平均分數）
  - [ ] Scan Time（時間戳記）

- [ ] "Advanced Filters" 按鈕可展開/摺疊
  - [ ] SEPA Score min/max 輸入框
  - [ ] Trend min/max 輸入框
  - [ ] RS Rank min/max 輸入框
  - [ ] VCP Grade 按鈕組 (A/B/C)
  - [ ] Trend Template 按鈕組 (Pass/Fail)

- [ ] 過濾後，表格行數實時更新

- [ ] "Show All Scored" 按鈕出現（當有未通過的評分股票時）
  - [ ] 點擊後顯示全部股票（包含 row-below-threshold 樣式的未通過行）
  - [ ] 按鈕文本切換為 "Show Passed Only"

- [ ] CSV export 按鈕正常工作

### **QM Tab 檢查**
- [ ] 點擊 QM tab 時，在控制欄看到新增的兩個下拉菜單：
  - [ ] "最低星級" (minStar) — 選項 3/3.5/4/4.5/5
  - [ ] "Top N" (topN) — 選項 25/50/100

- [ ] 掃描完成後，看到結果摘要行：
  - [ ] Passed 計數
  - [ ] 5★+ 計數
  - [ ] Avg Stars（帶 ★ 符號）
  - [ ] Scan Time

- [ ] 改變星級 / Top N 過濾器後，表格即時更新

- [ ] CSV export 按鈕正常工作

### **共同功能檢查**
- [ ] Clock 顯示 (HK / ET)
- [ ] Trading Date 顯示
- [ ] Progress Bar 顯示正確
- [ ] Source 選擇器 (NASDAQ FTP / Finviz)
- [ ] Refresh RS 核取方塊
- [ ] Run/Stop 按鈕
- [ ] Timing Pills 顯示 (S1/DL/Par/Total 時間)
- [ ] Market Tab 顯示市場狀態
- [ ] localStorage 持久化（重新整理頁面後結果不消失）

### **完全性檢查**
- [ ] **SEPA** — 12 列表格（Ticker, SEPA Score, Trend, Fund, RS, VCP, Pivot, Price, TT, Sector, 按鈕）
- [ ] **QM** — 12 列表格（Ticker, Stars, ADR%, $Vol, 1M%, 3M%, 6M%, Setup, MA, HL, Tight, 按鈕）
- [ ] 兩個 tab 都能完全替代原本的 `/scan` 和 `/qm/scan` 頁面

---

## 🐛 **如有問題報告**

若有任何元素缺失或功能異常，請記錄：
- [ ] 缺失的元素名稱
- [ ] 預期位置 (Top/Bottom/Tab)
- [ ] 在哪個 tab 發生
- [ ] 瀏覽器控制台是否有 JavaScript 錯誤

---

## 📌 **後續計劃（P1 功能，可選）**

- [ ] Cache 信息面板（SEPA）— 顯示快取狀態
- [ ] API Rate Limits 信息（SEPA）— 展開式說明面板
- [ ] Diagnostics 面板（QM）— 詳細掃描日誌
- [ ] Chart.js 圖表（Sector 分布 + Score 分布）

---

## ✨ **目標達成**

**Combined Scan 現在應該 100% 能替代：**
- ✅ `/scan` — SEPA 掃描頁面
- ✅ `/qm/scan` — QM 掃描頁面

所有原本的過濾器、統計信息、按鈕現在都在 Combined Scan 中！
