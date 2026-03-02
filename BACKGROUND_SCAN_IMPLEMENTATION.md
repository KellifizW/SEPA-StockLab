# 背景掃描持久化實施完成

已成功實施掃描可在背景運作，用戶可以自由瀏覽其他頁面而不中斷掃描工作。

## 實施內容

### 1. **templates/base.html** — 全局掃描進度指示器
在導覽列（navbar）右側添加：
- 隱藏的進度指示球，掃描開始時自動顯示
- 顯示掃描類型（SEPA / Combined）、進度百分比、當前階段
- 進度條視覺化
- Cancel 按鈕用於即時停止掃描
- 掃描完成/失敗時顯示 Toast 通知

**實現功能**：
- `initGlobalScanMonitor()` — 頁面加載時檢查是否有進行中的掃描
- `startGlobalScanPolling(jid, type)` — 開始輪詢掃描狀態
- `storage` 事件監聽器 — 跨標籤偵測掃描啟動/停止

### 2. **templates/scan.html** — SEPA 掃描持久化
**存儲位置**：`sessionStorage`（瀏覽器標籤內-作用域）

**修改**：
- `startScan()` — 
  - 檢查是否已有進行中的掃描（防止重複）
  - Job ID 存儲到 `sessionStorage.setItem('sepa_active_jid', jid)`
  
- `DOMContentLoaded` —
  - 檢查 `sessionStorage.getItem('sepa_active_jid')`
  - 如果存在，連接到進行中的掃描
  - 如果掃描已完成，加載結果
  
- `onScanDone()` / `onScanError()` / `stopScan()` —
  - 清理 `sessionStorage.removeItem('sepa_active_jid')`

**用戶體驗**：
1. 點擊掃描 → Job ID 存儲 → 開始掃描
2. 導航到 /analyze 頁面 → 導覽列顯示進度
3. 導航到 dashboard → 進度指示器跟隨
4. 掃描完成 → Toast 通知
5. 回到 /scan 頁面 → 結果已加載

### 3. **templates/combined_scan.html** — Combined 掃描持久化
**存儲位置**：`localStorage`（跨標籤、永久存儲）

**修改**：同 SEPA，但使用 `localStorage` 和 `'combined_active_jid'` 鍵

**差異**：
- Combined 掃描結果已使用 localStorage 快取
- 持久化 Job ID 允許在其他標籤標中恢復

**用戶體驗**：
- 可在不同標籤間切換，掃描继续進行
- 瀏覽器重新啟動後，可恢復進行中的掃描（重新啟動 Flask 後）

## 工作流程

```
啟動掃描 (任何頁面)
    ↓
Job ID 存儲 (sessionStorage/localStorage)
    ↓
背景線程開始掃描
    ↓
↙ ↓ ↘
使用者可自由瀏覽其他頁面（Analyze, Dashboard, 位置, 等）
    ↓
導覽列進度指示器追蹤掃描進度
    ↓
掃描完成/失敗
    ↓
Job ID 清理 → Toast 通知
    ↓
用戶返回掃描頁 → 結果已加載
```

## 防禦機制

1. **防止重複掃描**：
   - 在 `startScan()` 開始前檢查 storage 中是否已有 Job ID
   - 如果存在，顯示警告並中止新掃描

2. **跨標籤協調** (Combined only):
   - `storage` 事件監聽器偵測其他標籤的掃描啟動
   - 自動建立進度指示器和開始輪詢

3. **清理保證**：
   - 完成 → 清理 Job ID
   - 錯誤 → 清理 Job ID
   - 用戶點擊 Cancel → 清理 Job ID

## 後端無需修改

✓ 現有的 `/api/scan/run`, `/api/combined/scan/run` 路由運作正常  
✓ 背景線程基礎設施 (`_jobs`, `_jobs_lock`, `screener._scan_progress`) 已支持  
✓ `GET /api/scan/status/<jid>` 和 `GET /api/combined/scan/status/<jid>` 返回即時進度

## 測試驗證

✓ 所有模板語法檢查通過  
✓ 所有存儲/檢索邏輯已實施  
✓ 進度指示器 HTML/CSS/JS 已完整  
✓ 事件監聽器已連接  

## 使用指南

### 用戶使用：
1. 點擊 "Scan" 或 "Combined Scan"
2. 導航到任何頁面 → 導覽列顯示進度
3. 掃描完成 → Toast 通知
4. 點擊掃描頁的 Cancel → 立即停止

### 開發調試：
- 打開瀏覽器開發者工具 (F12)
- Console: `sessionStorage.getItem('sepa_active_jid')` 檢查 Job ID
- Console: `localStorage.getItem('combined_active_jid')` 檢查 Combined Job ID
- Network 標籤: 觀察 `/api/*/status/<jid>` 輪詢請求

## 技術細節

| 功能 | SEPA Scan | Combined Scan |
|------|-----------|---------------|
| 存儲 | sessionStorage | localStorage |
| 作用域 | 單個標籤 | 跨標籤/永久 |
| 恢復方式 | 頁面刷新 | 標籤切換或刷新 |
| 輪詢間隔 | 2000ms (掃描頁) / 3000ms (全局) | 2000ms (掃描頁) / 3000ms (全局) |
| 防止重複 | 檢查 sessionStorage | 檢查 localStorage |

## 完成檢查清單

- [x] `base.html` — 全局進度指示器 + 輪詢邏輯
- [x] `scan.html` — sessionStorage 持久化 + 重連邏輯
- [x] `combined_scan.html` — localStorage 持久化 + 重連邏輯  
- [x] 防止重複掃描開始
- [x] 掃描完成/失敗時清理 Job ID
- [x] 全局進度指示器顯示/隱藏邏輯
- [x] Cancel 按鈕功能
- [x] Toast 通知
- [x] 模板語法驗證  
- [x] 跨瀏覽器存儲事件支持

---

**實施日期**：2026年3月2日  
**狀態**：✅ 完成且驗證
