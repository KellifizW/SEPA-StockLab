"""
test_akshare_vs_yfinance.py
────────────────────────────────────────
Test AKShare US stock interfaces against yfinance for Stage 1 universe building.

Run: python test_akshare_vs_yfinance.py

Output: 速度对比、数据覆盖率、错误率统计
"""

import time
import sys
from pathlib import Path
from typing import Optional, Dict, List
import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

# ─────────────────────────────────────────────────────────────────────────────
# TEST 1: AKShare interface availability check
# ─────────────────────────────────────────────────────────────────────────────

def test_akshare_import():
    """检查 akshare 是否可安装"""
    print("\n" + "="*70)
    print("TEST 1: AKShare 库可用性")
    print("="*70)
    
    try:
        import akshare as ak
        print("✅ akshare 导入成功")
        print(f"   版本: {ak.__version__ if hasattr(ak, '__version__') else 'unknown'}")
        return ak
    except ImportError as e:
        print(f"❌ akshare 导入失败: {e}")
        print("   建议: pip install akshare --upgrade")
        return None


# ─────────────────────────────────────────────────────────────────────────────
# TEST 2: AKShare US stock list API
# ─────────────────────────────────────────────────────────────────────────────

def test_akshare_get_us_stock_list(ak):
    """测试 AKShare 美股列表接口"""
    print("\n" + "="*70)
    print("TEST 2: AKShare get_us_stock_name() — 获取美股列表")
    print("="*70)
    
    if ak is None:
        return None
    
    try:
        print("⏳ 正在获取美股列表... (首次可能较慢)")
        t0 = time.time()
        
        df_stocks = ak.get_us_stock_name()
        elapsed = time.time() - t0
        
        # Check structure
        print(f"✅ 成功获取美股列表")
        print(f"   耗时: {elapsed:.2f}s")
        print(f"   行数: {len(df_stocks)} (Nasdaq tickers: 6827)")
        print(f"   列: {list(df_stocks.columns)}")
        print(f"\n   样本行数据:\n{df_stocks.head(3)}")
        
        return df_stocks
        
    except Exception as e:
        print(f"❌ 获取美股列表失败: {e}")
        import traceback
        traceback.print_exc()
        return None


# ─────────────────────────────────────────────────────────────────────────────
# TEST 3: AKShare daily OHLCV (新浪财经)
# ─────────────────────────────────────────────────────────────────────────────

def test_akshare_stock_us_daily(ak, ticker: str = "AAPL", batch_size: int = 10):
    """测试 stock_us_daily() — 新浪财经接口"""
    print("\n" + "="*70)
    print(f"TEST 3: AKShare stock_us_daily() — 新浪财经接口")
    print("="*70)
    
    if ak is None:
        return None
    
    test_tickers = ["AAPL", "MSFT", "GOOGL", "AMZN", "TSLA", "NVDA", "META", "NFLX", "ADBE", "CRM"]
    results = {}
    success_count = 0
    
    print(f"⏳ 测试 {len(test_tickers)} 个美股 (使用 stock_us_daily)...")
    t0 = time.time()
    
    for i, tick in enumerate(test_tickers, 1):
        try:
            df = ak.stock_us_daily(symbol=tick, adjust="qfq")
            if not df.empty:
                results[tick] = {
                    'rows': len(df),
                    'columns': list(df.columns),
                    'latest_close': df.iloc[-1]['close'] if 'close' in df.columns else None,
                    'latest_vol': df.iloc[-1]['volume'] if 'volume' in df.columns else None,
                }
                success_count += 1
                print(f"  ✅ {tick:8s} → {len(df)} rows, latest close: {results[tick]['latest_close']}")
            else:
                print(f"  ⚠️  {tick:8s} → 空结果")
        except Exception as e:
            print(f"  ❌ {tick:8s} → 错误: {str(e)[:50]}")
        
        # 增加频率控制
        if i < len(test_tickers):
            time.sleep(0.5)
    
    elapsed = time.time() - t0
    print(f"\n✅ 完成测试")
    print(f"   耗时: {elapsed:.2f}s ({elapsed/len(test_tickers):.2f}s/ticker)")
    print(f"   成功率: {success_count}/{len(test_tickers)} ({100*success_count/len(test_tickers):.1f}%)")
    
    return results


# ─────────────────────────────────────────────────────────────────────────────
# TEST 4: AKShare daily OHLCV (东方财富)
# ─────────────────────────────────────────────────────────────────────────────

def test_akshare_stock_us_hist(ak, batch_size: int = 10):
    """测试 stock_us_hist() — 东方财富接口 (更快?)"""
    print("\n" + "="*70)
    print(f"TEST 4: AKShare stock_us_hist() — 东方财富接口")
    print("="*70)
    
    if ak is None:
        return None
    
    test_tickers = ["AAPL", "MSFT", "GOOGL", "AMZN", "TSLA"]
    results = {}
    success_count = 0
    
    print(f"⏳ 测试 {len(test_tickers)} 个美股 (使用 stock_us_hist)...")
    t0 = time.time()
    
    for i, tick in enumerate(test_tickers, 1):
        try:
            # 东方财富格式: symbol="105.AAPL"
            df = ak.stock_us_hist(symbol=f"105.{tick}")
            if not df.empty:
                results[tick] = {
                    'rows': len(df),
                    'columns': list(df.columns),
                }
                success_count += 1
                print(f"  ✅ {tick:8s} → {len(df)} rows")
            else:
                print(f"  ⚠️  {tick:8s} → 空结果")
        except Exception as e:
            print(f"  ❌ {tick:8s} → 错误: {str(e)[:50]}")
        
        if i < len(test_tickers):
            time.sleep(0.5)
    
    elapsed = time.time() - t0
    print(f"\n✅ 完成测试")
    print(f"   耗时: {elapsed:.2f}s ({elapsed/len(test_tickers):.2f}s/ticker)")
    print(f"   成功率: {success_count}/{len(test_tickers)} ({100*success_count/len(test_tickers):.1f}%)")
    
    return results


# ─────────────────────────────────────────────────────────────────────────────
# TEST 5: AKShare 实时行情
# ─────────────────────────────────────────────────────────────────────────────

def test_akshare_stock_us_spot_em(ak):
    """测试 stock_us_spot_em() — 实时行情"""
    print("\n" + "="*70)
    print("TEST 5: AKShare stock_us_spot_em() — 实时美股行情")
    print("="*70)
    
    if ak is None:
        return None
    
    try:
        print("⏳ 获取美股实时行情快照...")
        t0 = time.time()
        
        df = ak.stock_us_spot_em()
        elapsed = time.time() - t0
        
        print(f"✅ 成功获取实时行情")
        print(f"   耗时: {elapsed:.2f}s")
        print(f"   股票数: {len(df)}")
        print(f"   列: {list(df.columns)}")
        if len(df) > 0:
            print(f"\n   样本数据:\n{df.head(5)}")
        
        return df
        
    except Exception as e:
        print(f"❌ 获取实时行情失败: {e}")
        import traceback
        traceback.print_exc()
        return None


# ─────────────────────────────────────────────────────────────────────────────
# TEST 6: Speed comparison yfinance vs AKShare
# ─────────────────────────────────────────────────────────────────────────────

def test_speed_comparison(ak):
    """对比 yfinance vs AKShare 的下载速度"""
    print("\n" + "="*70)
    print("TEST 6: 速度对比 — yfinance vs AKShare")
    print("="*70)
    
    import yfinance as yf
    
    test_tickers = ["AAPL", "MSFT", "GOOGL", "AMZN", "TSLA", "NVDA", "META", "NFLX", "ADBE", "CRM"]
    
    # ── yfinance batch download ─────────────────────────────────────────────
    print(f"\n⏳ yfinance 批量下载 {len(test_tickers)} 个 tickers...")
    t0_yf = time.time()
    try:
        data_yf = yf.download(test_tickers, period="5d", interval="1d", progress=False)
        elapsed_yf = time.time() - t0_yf
        print(f"✅ yfinance: {elapsed_yf:.2f}s ({elapsed_yf/len(test_tickers):.2f}s/ticker)")
    except Exception as e:
        print(f"❌ yfinance 下载失败: {e}")
        elapsed_yf = None
    
    # ── AKShare sequential ──────────────────────────────────────────────────
    if ak is None:
        print("❌ AKShare 不可用，跳过对比")
        return
    
    print(f"\n⏳ AKShare 逐个下载 {len(test_tickers)} 个 tickers...")
    t0_ak = time.time()
    success_ak = 0
    
    for tick in test_tickers:
        try:
            df = ak.stock_us_daily(symbol=tick, adjust="qfq")
            if not df.empty:
                success_ak += 1
        except:
            pass
        time.sleep(0.5)  # 频率控制
    
    elapsed_ak = time.time() - t0_ak
    print(f"✅ AKShare: {elapsed_ak:.2f}s ({elapsed_ak/len(test_tickers):.2f}s/ticker, 成功: {success_ak}/{len(test_tickers)})")
    
    # ── Summary ─────────────────────────────────────────────────────────────
    print(f"\n📊 对比结果:")
    if elapsed_yf:
        ratio = elapsed_ak / elapsed_yf
        print(f"   yfinance: {elapsed_yf:.2f}s")
        print(f"   AKShare:  {elapsed_ak:.2f}s")
        print(f"   比值: AKShare vs yfinance = {ratio:.2f}x")
        if ratio < 1:
            print(f"   ✅ AKShare 快 {(1-ratio)*100:.0f}%")
        else:
            print(f"   ❌ AKShare 慢 {(ratio-1)*100:.0f}%")


# ─────────────────────────────────────────────────────────────────────────────
# MAIN
# ─────────────────────────────────────────────────────────────────────────────

def main():
    print("\n" + "="*70)
    print("AKShare 美股数据源可行性测试")
    print("="*70)
    print("目标: 评估 AKShare 是否能替代或补充 yfinance")
    print("时间: " + time.strftime("%Y-%m-%d %H:%M:%S"))
    
    ak = test_akshare_import()
    
    if ak:
        us_stocks_df = test_akshare_get_us_stock_list(ak)
        daily_results = test_akshare_stock_us_daily(ak)
        hist_results = test_akshare_stock_us_hist(ak)
        spot_results = test_akshare_stock_us_spot_em(ak)
        test_speed_comparison(ak)
    
    print("\n" + "="*70)
    print("测试总结")
    print("="*70)
    print("""
预期结论向量:
  1. 美股列表API (get_us_stock_name) 可行性: [✅/⚠️/❌]
  2. 日线数据接口 (stock_us_daily) 稳定性: [✅/⚠️/❌]
  3. 东方财富接口 (stock_us_hist) 速度: [快/中/慢]
  4. 实时行情接口 (stock_us_spot_em) 数据质量: [✅/⚠️/❌]
  5. 相对 yfinance 性价比: [强力推荐/可用/不推荐]
  
建议行动:
  - 如果全部✅ → 集成 AKShare 作为 yfinance 的备选方案
  - 如果部分✅ → 混合方案 (快速流程用AKShare, 关键数据用yfinance)
  - 如果多数❌ → 坚持 yfinance + 参数优化
    """)
    
    print("\n" + "="*70)
    print("✅ 测试完成")
    print("="*70)


if __name__ == "__main__":
    main()
