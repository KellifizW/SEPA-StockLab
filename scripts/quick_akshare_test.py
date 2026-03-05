"""
quick_akshare_test.py
────────────────────
快速测试 AKShare 核心功能（跳过慢速列表下载）
"""

import time
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

print("\n" + "="*70)
print("AKShare 快速可行性测试")
print("="*70)

# TEST 1: Import
print("\nTEST 1: AKShare 导入")
try:
    import akshare as ak
    print(f"✅ 成功 | 版本: {getattr(ak, '__version__', 'unknown')}")
except Exception as e:
    print(f"❌ 失败: {e}")
    sys.exit(1)

# TEST 2: 单个股票日线数据（新浪财经）
print("\nTEST 2: stock_us_daily() - 获取单个股票数据")
test_tickers = ["AAPL", "MSFT", "GOOGL"]
success = 0

for tick in test_tickers:
    try:
        t0 = time.time()
        df = ak.stock_us_daily(symbol=tick, adjust="qfq")
        elapsed = time.time() - t0
        if not df.empty:
            print(f"✅ {tick:8s} | {len(df):5d} rows | {elapsed:.2f}s | Close: {df.iloc[-1]['close']:.2f}")
            success += 1
        else:
            print(f"⚠️  {tick:8s} | 空结果")
    except Exception as e:
        print(f"❌ {tick:8s} | {str(e)[:50]}")
    time.sleep(0.5)

print(f"\n成功率: {success}/{len(test_tickers)}")

# TEST 3: 实时行情快照
print("\nTEST 3: stock_us_spot_em() - 实时美股行情")
try:
    t0 = time.time()
    df_spot = ak.stock_us_spot_em()
    elapsed = time.time() - t0
    print(f"✅ 成功 | 行数: {len(df_spot)} | {elapsed:.2f}s")
    if len(df_spot) > 0:
        print(f"   样本: {df_spot['symbol'].iloc[0]}")
except Exception as e:
    print(f"❌ 失败: {e}")

# TEST 4: yfinance 对比（小规模）
print("\nTEST 4: 速度对比 yfinance vs AKShare")
import yfinance as yf

test_tickers_10 = ["AAPL", "MSFT", "GOOGL", "AMZN", "TSLA"]

# yfinance
print(f"\nyfinance 批量下载 5 个 tickers...")
t0 = time.time()
try:
    data = yf.download(test_tickers_10, period="5d", interval="1d", progress=False)
    yf_time = time.time() - t0
    print(f"✅ yfinance: {yf_time:.2f}s ({yf_time/len(test_tickers_10):.2f}s/ticker)")
except Exception as e:
    print(f"❌ 失败: {e}")
    yf_time = None

# AKShare
print(f"\nAKShare 逐个下载 5 个 tickers...")
t0 = time.time()
ak_success = 0
for tick in test_tickers_10:
    try:
        df = ak.stock_us_daily(symbol=tick, adjust="qfq")
        if not df.empty:
            ak_success += 1
    except:
        pass
    time.sleep(0.5)

ak_time = time.time() - t0
print(f"✅ AKShare: {ak_time:.2f}s ({ak_time/len(test_tickers_10):.2f}s/ticker, 成功: {ak_success}/{len(test_tickers_10)})")

# 对比
print(f"\n📊 结论:")
if yf_time:
    ratio = ak_time / yf_time
    print(f"   yfinance: {yf_time:.2f}s")
    print(f"   AKShare:  {ak_time:.2f}s")
    print(f"   AKShare / yfinance = {ratio:.2f}x")
    if ratio < 0.8:
        print(f"   ✅ AKShare 快约 {(1-ratio)*100:.0f}%")
    elif ratio > 1.2:
        print(f"   ❌ AKShare 慢约 {(ratio-1)*100:.0f}%")
    else:
        print(f"   ⚠️  速度相当")

print("\n" + "="*70)
print("快速测试完成")
print("="*70)
