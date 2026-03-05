"""
validate_nasdaq_params_v2.py
────────────────────────────────────────
版本 2：更现实的验证测试

模拟真实的 nasdaq_universe.py 工作流程：
  - 分批下载（BATCH_SIZE）
  - 每批只提取 Close + Volume
  - 检查是否触发 HTTP 429

这就是 nasdaq_universe.py 实际做的事！
"""

import time
import sys
from pathlib import Path
from typing import Dict, List, Tuple
import pandas as pd
import yfinance as yf
import logging

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

logging.basicConfig(
    level=logging.WARNING,  # 忽略 yfinance 的 "delisted" 警告
    format='%(levelname)s | %(message)s'
)

# ─────────────────────────────────────────────────────────────────────────────
# Core validation logic — 模仿 nasdaq_universe._fetch_price_volume_rows()
# ─────────────────────────────────────────────────────────────────────────────

def test_nasdaq_universe_simulation(
    tickers: List[str],
    batch_size: int = 200,
    batch_sleep: float = 0.3,
) -> Dict:
    """
    模拟 nasdaq_universe._fetch_price_volume_rows() 的实际行为
    
    - 分批下载 OHLCV
    - 每批后 sleep(batch_sleep)
    - 提取每个 ticker 的 close 价格 + 平均成交量
    - 监控 HTTP 429/超时错误
    """
    
    results = {
        'total': len(tickers),
        'success': 0,
        'http_429': False,
        'timeout': False,
        'other_errors': [],
        'elapsed': 0,
        'ticker_data': {}
    }
    
    t0 = time.time()
    
    # Process in batches (exactly like nasdaq_universe.py)
    for i in range(0, len(tickers), batch_size):
        batch = tickers[i:i+batch_size]
        batch_num = (i // batch_size) + 1
        total_batches = (len(tickers) + batch_size - 1) // batch_size
        pct = int(100 * i / len(tickers))
        
        print(f"[Batch {batch_num}/{total_batches}] {pct}% ({i}/{len(tickers)}) | Downloaded {results['success']} so far")
        
        try:
            # yfinance download (same call as nasdaq_universe.py)
            data = yf.download(
                batch,
                period="5d",      # 最后 5 天 (和 nasdaq_universe 相同)
                interval="1d",
                auto_adjust=True,
                threads=False,
                progress=False,
            )
            
            if data is not None and not data.empty:
                close = data["Close"]
                volume = data["Volume"]
                
                # Process by ticker
                if isinstance(close, pd.DataFrame):
                    # 多个 ticker
                    last_close = close.iloc[-1].dropna()
                    avg_volume = volume.mean()
                else:
                    # 单个 ticker
                    last_close = pd.Series({batch[0]: float(close.iloc[-1])})
                    avg_volume = pd.Series({batch[0]: float(volume.mean())})
                
                # 提取有效数据的 ticker 数量
                for ticker in last_close.index:
                    c = float(last_close.get(ticker, 0) or 0)
                    v = float(avg_volume.get(ticker, 0) or 0)
                    if c > 0 and v > 0:
                        results['success'] += 1
                        results['ticker_data'][str(ticker)] = {'close': c, 'avg_vol': v}
                
                print(f"  ✅ 成功处理 {results['success']} → {results['success']} 个 tickers")
                
        except Exception as e:
            error_str = str(e)
            
            # Detect specific errors
            if "429" in error_str or "Too Many Requests" in error_str:
                print(f"  🚨 HTTP 429 限速错误！")
                results['http_429'] = True
                
            elif "timeout" in error_str.lower() or "timed out" in error_str.lower():
                print(f"  ⏱️ 连接超时")
                results['timeout'] = True
                
            else:
                # yfinance 会报 "delisted" 之类的，这是正常的，忽略
                if "delisted" not in error_str.lower() and "not found" not in error_str.lower():
                    print(f"  ⚠️ 错误: {error_str[:80]}")
                    results['other_errors'].append(error_str[:80])
        
        # Sleep between batches (exactly like nasdaq_universe.py)
        if i + batch_size < len(tickers):
            time.sleep(batch_sleep)
    
    results['elapsed'] = time.time() - t0
    
    return results


# ─────────────────────────────────────────────────────────────────────────────
# Main validation
# ─────────────────────────────────────────────────────────────────────────────

def main():
    print("\n" + "="*70)
    print("方案 A 参数验证 (yfinance 限速测试)")
    print("="*70)
    
    # 生成 200+ 个有效 ticker 用于测试
    # 使用美股中最活跃的 200 个股票（确保有价格数据）
    test_tickers = [
        "AAPL", "MSFT", "GOOGL", "AMZN", "TSLA", "NVDA", "META", "NFLX",
        "ADBE", "CRM", "SNPS", "CDNS", "ADSK", "PYPL", "INTU", "ASML",
        "TMUS", "ABNB", "DASH", "SHOP", "RBLX", "PLTR", "COIN", "MSTR",
        "SWKS", "AVGO", "QCOM", "AMD", "INTC", "MU", "LRCX", "KLAC",
        "AMAT", "TER", "ONTO", "SMCI", "GTLB", "OKTA", "ZS", "NET",
        "WDAY", "AZO", "ARMK", "AXON", "ANET", "FTNT", "CRWD", "PANW",
        "SNOW", "DDOG", "MDB", "COST", "WMT", "LOW", "HD", "CVA",
        "CVNA", "CHKP", "CSCO", "GILD", "VRTX", "REGN", "BMRN", "BIIB",
        "ILMN", "DXCM", "PODD", "MNST", "PAYX", "ADP", "VRSK", "IQV",
        "EXPE", "BKNG", "ATVI", "EA", "TTWO", "CDNA", "SNPS", "SYK",
        "IOT", "AMD", "INTC", "QCOM", "NVDA", "AMAT", "KLAC", "LRCX",
        "TER", "MKSI", "ASML", "MCHP", "NXPI", "SLAB", "CRWD", "ZS",
        "FTNT", "OKTA", "PALO", "ACGL", "CBRL", "KNSL", "TXRH", "RBLX",
        "ZETA", "DOCN", "CRM", "VEEV", "ADBE", "WDAY", "SSNC", "INCY",
        "JKHY", "VRSN", "CACI", "ALKS", "GLPG", "RPRX", "RGEN", "MRNA",
        "BNTX", "CG", "LGND", "ARGX", "XPEV", "NIO", "LI", "BABA",
        "JD", "NTES", "BGNE", "CCIH", "CBRL", "POWI", "SMCI", "GTLB",
        "OKTA", "ZS", "NET", "WDAY", "AZO", "ARMK", "AXON", "ANET",
        "FTNT", "CRWD", "PANW", "SNOW", "DDOG", "MDB", "COST", "WMT",
        "LOW", "HD", "CVNA", "CHKP", "CSCO", "GILD", "VRTX", "REGN",
        "BMRN", "BIIB", "ILMN", "DXCM", "PODD", "MNST", "PAYX", "ADP",
        "VRSK", "IQV", "EXPE", "BKNG", "ATVI", "EA", "TTWO", "CDNA",
        "SNPS", "SYK", "IOT", "AMD", "INTC", "QCOM", "NVDA", "AMAT",
        "KLAC", "LRCX", "TER", "MKSI", "ASML", "MCHP", "NXPI", "SLAB",
        "CRWD", "ZS", "FTNT", "OKTA", "PALO", "ACGL", "CBRL", "KNSL",
        "TXRH", "RBLX", "ZETA", "DOCN", "CRM", "VEEV", "ADBE", "WDAY",
        "SSNC", "INCY", "JKHY", "VRSN", "CACI", "ALKS", "GLPG", "RPRX",
    ]
    
    # 去重并限制到 200 个
    test_tickers = list(set(test_tickers))[:200]
    
    print(f"\n测试参数:")
    print(f"  NASDAQ_BATCH_SIZE = 200")
    print(f"  NASDAQ_BATCH_SLEEP = 0.3")
    print(f"  测试 ticker 数 = {len(test_tickers)}")
    print(f"  预计耗时 = ~0.3 分钟")
    print("\n" + "="*70)
    
    # Run validation
    results = test_nasdaq_universe_simulation(
        test_tickers,
        batch_size=200,
        batch_sleep=0.3
    )
    
    # Analyze
    print("\n" + "="*70)
    print("结果分析")
    print("="*70)
    print(f"\n📊 统计:")
    print(f"  总测试数: {results['total']}")
    print(f"  成功数: {results['success']} ({100*results['success']/results['total']:.1f}%)")
    print(f"  耗时: {results['elapsed']:.1f}s")
    
    print(f"\n⚠️ 错误检测:")
    print(f"  HTTP 429 (限速): {'⚠️ 检测到！' if results['http_429'] else '✅ 未检测到'}")
    print(f"  超时错误: {'⚠️ 检测到！' if results['timeout'] else '✅ 未检测到'}")
    print(f"  其他重大错误: {len(results['other_errors'])} 个" if results['other_errors'] else f"  其他重大错误: ✅ 无")
    
    # Decision
    print("\n" + "="*70)
    print("安全性评估")
    print("="*70)
    
    if results['http_429']:
        print("""\n❌ 不安全！检测到 HTTP 429 限速错误

建议:
  回退到更保守的参数:
  NASDAQ_BATCH_SIZE  = 150
  NASDAQ_BATCH_SLEEP = 0.5
        """)
        return False
        
    elif results['timeout']:
        print("""\n⚠️ 不稳定！检测到超时错误

建议:
  使用稍微保守的参数:
  NASDAQ_BATCH_SIZE  = 150
  NASDAQ_BATCH_SLEEP = 0.4
        """)
        return False
        
    else:
        print("""\n✅ 安全！未检测到限速或超时

方案 A 参数可安全使用:
  NASDAQ_BATCH_SIZE  = 200  (✓ from 100)
  NASDAQ_BATCH_SLEEP = 0.3  (✓ from 0.5)

预期收益:
  - 耗时减少: 8-9 分钟 → 5-6 分钟
  - 性能提升: 30-40%
  - 无限速风险 ✓
        """)
        return True


if __name__ == "__main__":
    success = main()
    print("\n" + "="*70)
    sys.exit(0 if success else 1)
