# Watch Mode 诊断步骤

## 步骤 1: 在浏览器 Console 中检查关键变量

1. 打开 http://127.0.0.1:5000/ml/analyze?ticker=AEM
2. 按 **F12** 打开 DevTools，切换到 **Console** 标签
3. 依次运行以下命令（逐个复制粘贴）：

```javascript
// 检查URL参数
console.log('📌 URL ticker:', new URLSearchParams(location.search).get('ticker'));

// 检查LightweightCharts库是否加载
console.log('📌 LightweightCharts loaded:', typeof LightweightCharts !== 'undefined');

// 检查全局变量初始化
console.log('📌 _mlWatchChart:', _mlWatchChart);
console.log('📌 _mlWatchTicker:', _mlWatchTicker);
console.log('📌 _mlWatchData:', _mlWatchData);

// 检查容器是否存在
const container = document.getElementById('intradayChartContainer');
console.log('📌 Container exists:', !!container);
console.log('📌 Container visible:', container.offsetHeight > 0 && container.clientWidth > 0);
console.log('📌 Container width:', container.clientWidth, 'height:', container.clientHeight);

// 检查watchPanel是否可见
const watchPanel = document.getElementById('watchPanel');
console.log('📌 watchPanel has d-none:', watchPanel.classList.contains('d-none'));

// 检查是否有JavaScript错误
console.log('📌 Function analyzeStock exists:', typeof analyzeStock === 'function');
console.log('📌 Function switchMlMode exists:', typeof switchMlMode === 'function');
console.log('📌 Function loadIntradayChart exists:', typeof loadIntradayChart === 'function');
```

## 步骤 2: 手动触发 Watch Mode

1. 在Console中运行：
```javascript
// 清空前一个图表
if (_mlWatchChart) {
  try { _mlWatchChart.remove(); } catch(e) {}
  _mlWatchChart = null;
}

// 手动切换到watch mode
setTimeout(() => {
  console.log('🔄 Calling switchMlMode("watch")...');
  switchMlMode('watch');
}, 500);
```

2. 等待 1-2 秒，检查 Console 是否出现错误

## 步骤 3: 如果图表仍未出现

在 Console 中运行完整的API测试：

```javascript
// 测试API请求
fetch('/api/chart/intraday/AEM?interval=5m')
  .then(r => {
    console.log('📌 API response status:', r.status);
    return r.json();
  })
  .then(data => {
    console.log('📌 API response ok:', data.ok);
    console.log('📌 API data keys:', Object.keys(data));
    console.log('📌 Candles count:', data.candles?.length);
    console.log('📌 EMA9 count:', data.ema9?.length);
    console.log('📌 EMA21 count:', data.ema21?.length);
    console.log('📌 First candle:', data.candles?.[0]);
  })
  .catch(err => console.error('❌ API error:', err));
```

## 步骤 4: 手动初始化图表

如果API数据正常，尝试手动创建:

```javascript
const container = document.getElementById('intradayChartContainer');
console.log('Container:', container?.clientWidth, 'x', container?.clientHeight);

if (container && container.clientWidth > 0) {
  const LWC = window.LightweightCharts;
  console.log('Creating chart with dimensions:', container.clientWidth, 'x', 450);
  
  try {
    const chart = LWC.createChart(container, {
      width: container.clientWidth,
      height: 450,
      layout: {
        background: { color: '#0d1117' },
        textColor: '#8b949e'
      },
      timeScale: {
        timeVisible: true,
        secondsVisible: false,
      }
    });
    console.log('✅ Chart created successfully:', chart);
    _mlWatchChart = chart;
  } catch (err) {
    console.error('❌ Chart creation failed:', err);
  }
} else {
  console.error('❌ Container not ready - width:', container?.clientWidth);
}
```

## 预期结果

### 如果一切正常：
- ✅ LightweightCharts loaded: true
- ✅ Container visible: true (width > 0, height > 0)
- ✅ API response ok: true
- ✅ Candles count: > 0
- ✅ Chart created successfully

### 常见问题及解决方案

| 问题 | 症状 | 解决方案 |
|------|------|---------|
| LWC未加载 | `LightweightCharts loaded: false` | 刷新页面，检查CDN连接 |
| 容器宽度为0 | `Container width: 0` | 增加setTimeout延时到200ms |
| API无数据 | `Candles count: 0` | 检查股票代码是否有盘前数据 |
| 图表创建失败 | 错误信息提示chart创建失败 | 检查容器是否为null或undefined |

## 提供给开发者的信息

请截图 Console 输出结果，包括：
1. 所有变量检查结果
2. API响应状态
3. 任何错误信息
4. 容器尺寸信息

这将帮助快速定位问题。
