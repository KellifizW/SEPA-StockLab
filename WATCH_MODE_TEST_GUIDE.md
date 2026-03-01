# 🎯 Watch Mode 测试 & 诊断指南

## ✅ 已验证完成

- ✅ Flask 应用运行中
- ✅ ML 分析页面正常加载 (84.4KB)
- ✅ LightweightCharts 库已加载
- ✅ watchPanel 元素存在
- ✅ API 返回数据 (331 条蜡烛线 + EMA + VWAP)

---

## 🧪 浏览器测试步骤

### 1️⃣ 打开页面
```
http://127.0.0.1:5000/ml/analyze?ticker=AEM
```

### 2️⃣ 打开 DevTools 并观看 Console

按 **F12** → 切换到 **Console** 标签

你应该看到页面加载时的日志。

### 3️⃣ 点击看盘模式按钮

找到：**"📡 盯盤模式 Watch Market"** 按钮并点击

### 4️⃣ 观察 Console 输出

在点击按钮后，你应该看到这样的日志序列：

```javascript
🔄 switchMlMode: watch
✅ Switched to watch mode, watchPanel d-none removed
📌 RequestAnimFrame - ticker: AEM
✅ Chart created successfully
📐 Container clientWidth: [某个数字]
📐 Final width for chart: [某个数字]
✅ API data received: {candles: 331, ema9: 331}
```

---

## 🔍 关键检查清单

| 检查项 | 预期结果 | 位置 |
|-------|--------|------|
| **Container 宽度** | > 0 (例如 720, 800) | `📐 Container clientWidth:` |
| **API 数据** | candles: 331, ema9: 331 | `✅ API data received:` |
| **图表创建** | "success" 消息出现 | `✅ Chart created successfully` |
| **无错误消息** | 没有红色 ❌ 符号 | Console 整体检查 |

---

## ❌ 常见问题排查

### 问题 1: "Container clientWidth: 0"
**症状**: 宽度为 0

**解决方案**:
```javascript
// 在 Console 中运行：
console.log('watchPanel visible:', !document.getElementById('watchPanel').classList.contains('d-none'));
console.log('watchPanel width:', document.getElementById('watchPanel').clientWidth);
console.log('container width:', document.getElementById('intradayChartContainer').clientWidth);
```

如果容器宽度仍为 0：
- 刷新页面后重新测试
- 检查浏览器窗口是否足够宽 (>800px)

---

### 问题 2: "LightweightCharts not loaded"
**症状**: 错误信息说 LWC 没有加载

**检查方法**:
```javascript
console.log('LWC loaded:', typeof LightweightCharts !== 'undefined');
console.log('Window.LWC:', window.LightweightCharts);
```

**解决方案**:
- 刷新页面 (Ctrl+R)
- 检查网络标签看 CDN 资源是否加载成功

---

### 问题 3: 图表显示区域为空
**症状**: 容器可见但没有图表

**检查方法**:
```javascript
// 手动触发重新加载
await loadIntradayChart('AEM', '5m');

// 检查全局变量
console.log('_mlWatchChart:', _mlWatchChart);
console.log('_mlWatchData:', _mlWatchData);
```

---

## 📝 完整的 Console 诊断脚本

如果出现问题，在 Console 中逐行运行这个：

```javascript
// 1. 检查全局变量
console.log('=== GLOBAL STATE ===');
console.log('_mlWatchChart:', _mlWatchChart);
console.log('_mlWatchTicker:', _mlWatchTicker);
console.log('_mlWatchData:', _mlWatchData);

// 2. 检查 DOM 元素
console.log('\n=== DOM ELEMENTS ===');
const container = document.getElementById('intradayChartContainer');
const watchPanel = document.getElementById('watchPanel');
console.log('Container exists:', !!container);
console.log('Container width:', container?.clientWidth);
console.log('Container height:', container?.clientHeight);
console.log('WatchPanel visible:', !watchPanel?.classList.contains('d-none'));

// 3. 检查库
console.log('\n=== LIBRARIES ===');
console.log('LightweightCharts loaded:', typeof LightweightCharts !== 'undefined');
console.log('Bootstrap loaded:', typeof bootstrap !== 'undefined');

// 4. 测试 API
console.log('\n=== API TEST ===');
fetch('/api/chart/intraday/AEM?interval=5m')
  .then(r => r.json())
  .then(data => {
    console.log('API ok:', data.ok);
    console.log('Candles:', data.candles?.length);
    console.log('EMA9:', data.ema9?.length);
  })
  .catch(err => console.error('API error:', err));

// 5. 重新初始化 watch mode
console.log('\n=== CALLING switchMlMode ===');
switchMlMode('watch');
```

---

## 📸  截图要求

如果仍然有问题，请截图：

1. **整个 Console 输出** (包括所有日志)
2. **Elements 标签** - 显示 intradayChartContainer 的 HTML
3. **Network 标签** - 显示 `/api/chart/intraday/AEM` 请求的响应

---

## ✨ 预期的成功结果

✅ 看到完整的蜡烛图表，包括：
- K线 (绿色涨、红色跌)
- EMA 9 线 (蓝色)
- EMA 21 线 (绿色)
- 下方体积柱
- 左侧价格刻度

✅ 按钮能工作：
- 5分、15分、1小时切换
- 图表实时更新

---

## 💡 调试建议

如果你有编程经验，可以在 `ml_analyze.html` 的浏览器 DevTools 中：

1. 打开 **Sources** 标签
2. 在 `loadIntradayChart` 函数第一行设置断点
3. 点击 Watch Market 按钮
4. 逐步执行，观察变量变化

这样可以精确看到哪一行出问题。

---

## 🆘 提交问题时

如果修复后仍不工作，请提供：

1. ✓ 完整的 Console 输出截图
2. ✓ 浏览器版本 (Chrome/Firefox/Safari)
3. ✓ 浏览器窗口宽度
4. ✓ 是否有自定义 CSS/JavaScript 插件
5. ✓ Flask 启动时的输出日志
