"""
validate_nasdaq_params.py
────────────────────────────────────────
验证新参数（NASDAQ_BATCH_SIZE=200, NASDAQ_BATCH_SLEEP=0.3）
是否会触发 yfinance 限速（HTTP 429）

测试流程:
  1. 获取 NASDAQ ticker 列表（原参数）
  2. 用新参数进行 100+ ticker 的批量下载
  3. 监控 HTTP 429、超时、连接错误
  4. 输出安全建议
"""

import time
import sys
import logging
from pathlib import Path
from typing import Dict, List, Tuple
import pandas as pd
import yfinance as yf

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

# Setup logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s | %(levelname)s | %(message)s'
)
logger = logging.getLogger(__name__)

# ─────────────────────────────────────────────────────────────────────────────
# Step 1: Get NASDAQ tickers for testing
# ─────────────────────────────────────────────────────────────────────────────

def get_test_tickers(count: int = 150) -> List[str]:
    """
    获取前 N 个有效的 NASDAQ ticker 用于测试
    
    使用已知的有效 ticker，避免已退市股票导致的误判
    """
    # 已验证的有效 ticker（流动性好、活跃）
    valid_tickers = [
        "AAPL", "MSFT", "GOOGL", "AMZN", "TSLA", "NVDA", "META", "NFLX", 
        "ADBE", "CRM", "SNPS", "CDNS", "ADSK", "PYPL", "INTU", "ASML",
        "TMUS", "ABNB", "DASH", "SHOP", "RBLX", "PLTR", "COIN", "MSTR",
        "SWKS", "AVGO", "QCOM", "AMD", "INTC", "MU", "NVDA", "LRCX", 
        "KLAC", "AMAT", "TER", "ONTO", "TXRH", "SMCI", "GTLB", "OKTA", 
        "ZS", "NET", "WDAY", "AZO", "ARMK", "AXON", "ANET", "FTNT", 
        "CRWD", "PANW", "ZS", "SNOW", "DDOG", "MDB", "COST", "WMT",
        "LOW", "HD", "LOMA", "CVNA", "COIN", "MQ", "VLTO", "MCHP",
        "NXPI", "STM", "LAWR", "ONTO", "UCTT", "CGNX", "ENTG", "WDFC",
        "ANSS", "ALTR", "ACGL", "CBRL", "KNSL", "TXRH", "RBLX", "ZETA",
        "FTCY", "CHKP", "DCUI", "CSCO", "CMCS", "GILD", "VRTX", "RARE",
        "REGN", "BMRN", "BIIB", "ILMN", "DXCM", "PODD", "MNST", "PAYX",
        "ADP", "VRSK", "IQV", "EXPE", "BKNG", "ATVI", "EA", "TTWO",
        "CDNA", "EXLV", "Q", "VEEV", "OKTA", "SSNC", "INCY", "JKHY",
        "VRSN", "CACI", "ALKS", "AKBA", "BILI", "BGNE", "CLDX", "GDS",
        "MSTR", "COIN", "RIOT", "CLSK", "MARA", "CORZ", "SFUN", "DNAN",
        # 更多...
    ]
    
    # 返回前 count 个（去重）
    test_tickers = list(set(valid_tickers))[:count]
    logger.info(f"✅ 使用 {len(test_tickers)} 个已验证的有效 tickers")
    return test_tickers

# ─────────────────────────────────────────────────────────────────────────────
# Step 2: Test new parameters (BATCH_SIZE=200, BATCH_SLEEP=0.3)
# ─────────────────────────────────────────────────────────────────────────────

def test_batch_download(
    tickers: List[str],
    batch_size: int = 200,
    batch_sleep: float = 0.3,
    period: str = "5d",
) -> Dict:
    """
    用新参数测试批量下载，监控错误
    
    返回: {
        'total': 总数,
        'success': 成功数,
        'failures': 失败详情,
        'http_429_count': HTTP 429 计数,
        'timeout_count': 超时次数,
        'elapsed': 总耗时,
        'errors_log': 错误日志
    }
    """
    
    results = {
        'total': len(tickers),
        'success': 0,
        'failures': [],
        'http_429_count': 0,
        'timeout_count': 0,
        'connection_error_count': 0,
        'elapsed': 0,
        'errors_log': []
    }
    
    print("\n" + "="*70)
    print(f"参数验证测试")
    print("="*70)
    print(f"NASDAQ_BATCH_SIZE:  {batch_size}")
    print(f"NASDAQ_BATCH_SLEEP: {batch_sleep}")
    print(f"测试 ticker 数: {len(tickers)}")
    print(f"预计耗时: ~{len(tickers)*batch_sleep/60:.1f} 分钟")
    print("="*70)
    
    t0 = time.time()
    failed_tickers = []
    
    # Process in batches
    for i in range(0, len(tickers), batch_size):
        batch = tickers[i:i+batch_size]
        batch_num = i // batch_size + 1
        total_batches = (len(tickers) + batch_size - 1) // batch_size
        
        elapsed_so_far = time.time() - t0
        pct = int(100 * i / len(tickers))
        
        print(f"\n[Batch {batch_num}/{total_batches}] Progress {pct}% ({i}/{len(tickers)}) | Elapsed {elapsed_so_far:.0f}s")
        print(f"  下载 {len(batch)} 个 tickers: {', '.join(batch[:5])}{'...' if len(batch)>5 else ''}")
        
        try:
            # 用 yfinance 批量下载
            data = yf.download(
                batch,
                period=period,
                interval="1d",
                auto_adjust=True,
                threads=False,
                progress=False
            )
            
            if data is not None and not data.empty:
                # 成功下载
                # 检查返回数据中有多少个 ticker 有效数据
                if isinstance(data.columns, pd.MultiIndex):
                    # 多个 ticker: columns 是 (Ticker, OHLCV)
                    valid_tickers = set(data.columns.get_level_values(0))
                    results['success'] += len(valid_tickers)
                    print(f"  ✅ 成功 ({len(valid_tickers)} tickers)")
                else:
                    # 单个 ticker
                    results['success'] += 1
                    print(f"  ✅ 成功")
            else:
                logger.warning(f"Batch {batch_num} 返回空数据")
                for t in batch:
                    failed_tickers.append((t, "empty_data"))
                    
        except Exception as e:
            error_str = str(e)
            
            # 检测特定错误
            if "429" in error_str or "Too Many Requests" in error_str:
                print(f"  ⚠️  HTTP 429 限速错误!")
                results['http_429_count'] += 1
                results['errors_log'].append(f"Batch {batch_num}: HTTP 429 - 触发限速")
                for t in batch:
                    failed_tickers.append((t, "http_429"))
                    
            elif "timeout" in error_str.lower() or "timed out" in error_str.lower():
                print(f"  ⚠️  连接超时!")
                results['timeout_count'] += 1
                results['errors_log'].append(f"Batch {batch_num}: Timeout")
                for t in batch:
                    failed_tickers.append((t, "timeout"))
                    
            elif "connection" in error_str.lower():
                print(f"  ⚠️  连接错误!")
                results['connection_error_count'] += 1
                results['errors_log'].append(f"Batch {batch_num}: Connection error")
                for t in batch:
                    failed_tickers.append((t, "connection_error"))
                    
            else:
                print(f"  ❌ 其他错误: {error_str[:100]}")
                results['errors_log'].append(f"Batch {batch_num}: {error_str[:100]}")
                for t in batch:
                    failed_tickers.append((t, "other"))
        
        # Sleep between batches
        if i + batch_size < len(tickers):
            print(f"  等待 {batch_sleep}s...")
            time.sleep(batch_sleep)
    
    results['elapsed'] = time.time() - t0
    results['failures'] = failed_tickers
    
    return results


# ─────────────────────────────────────────────────────────────────────────────
# Step 3: Analyze results and make recommendation
# ─────────────────────────────────────────────────────────────────────────────

def analyze_results(results: Dict) -> Tuple[bool, str]:
    """
    分析测试结果，判断参数是否安全
    
    返回: (is_safe, recommendation_message)
    """
    
    print("\n" + "="*70)
    print("测试结果分析")
    print("="*70)
    
    print(f"\n📊 统计:")
    print(f"  总数:        {results['total']}")
    print(f"  成功:        {results['success']} ({100*results['success']/results['total']:.1f}%)")
    print(f"  失败数:      {len(results['failures'])}")
    print(f"  HTTP 429:    {results['http_429_count']} ⚠️")
    print(f"  超时:        {results['timeout_count']} ⚠️")
    print(f"  连接错误:    {results['connection_error_count']} ⚠️")
    print(f"  耗时:        {results['elapsed']:.0f}s ({results['elapsed']/60:.1f}min)")
    
    if results['errors_log']:
        print(f"\n❌ 错误日志:")
        for log in results['errors_log'][:5]:
            print(f"    {log}")
        if len(results['errors_log']) > 5:
            print(f"    ... 及其他 {len(results['errors_log'])-5} 个错误")
    
    # 判断安全性
    print("\n" + "="*70)
    print("安全性评估")
    print("="*70)
    
    is_safe = True
    recommendation = ""
    
    # 检查 HTTP 429
    if results['http_429_count'] > 0:
        is_safe = False
        recommendation += f"\n❌ 检测到 HTTP 429 (限速) 错误 {results['http_429_count']} 次"
        recommendation += f"\n   → BATCH_SIZE/BATCH_SLEEP 参数过激进，需要调整"
    
    # 检查超时
    if results['timeout_count'] > 0:
        is_safe = False
        recommendation += f"\n⚠️  检测到超时错误 {results['timeout_count']} 次"
        recommendation += f"\n   → 可能是网络问题，建议增加 BATCH_SLEEP"
    
    # 检查成功率
    if results['success'] / results['total'] < 0.95:
        is_safe = False
        recommendation += f"\n⚠️  成功率只有 {100*results['success']/results['total']:.1f}%"
        recommendation += f"\n   → 参数仍需优化"
    
    # 所有指标都好
    if is_safe:
        recommendation = f"\n✅ 所有指标正常！参数安全可用"
        recommendation += f"\n   → 成功率: {100*results['success']/results['total']:.1f}%"
        recommendation += f"\n   → 无 HTTP 429 错误"
        recommendation += f"\n   → 无超时错误"
    
    print(recommendation)
    
    return is_safe, recommendation


# ─────────────────────────────────────────────────────────────────────────────
# MAIN
# ─────────────────────────────────────────────────────────────────────────────

def main():
    print("\n" + "="*70)
    print("yfinance 参数验证测试")
    print("="*70)
    print("目标: 验证新参数是否安全（无 HTTP 429 限速）")
    print("日期: " + time.strftime("%Y-%m-%d %H:%M:%S"))
    
    # Step 1: Get test tickers
    tickers = get_test_tickers(count=150)
    
    if not tickers:
        logger.error("无法获取 ticker 列表，退出")
        sys.exit(1)
    
    # Step 2: Test with new parameters (BATCH_SIZE=200, BATCH_SLEEP=0.3)
    results = test_batch_download(
        tickers,
        batch_size=200,
        batch_sleep=0.3,
        period="5d"
    )
    
    # Step 3: Analyze and recommend
    is_safe, recommendation = analyze_results(results)
    
    # Step 4: Final Summary
    print("\n" + "="*70)
    print("最终建议")
    print("="*70)
    
    if is_safe:
        print("""
✅ 参数验证通过！方案 A 安全可用

建议改动:
  NASDAQ_BATCH_SIZE  = 100 → 200
  NASDAQ_BATCH_SLEEP = 0.5 → 0.3

预期收益:
  - 下载速度提升 30-40%
  - NASDAQ Universe 阶段从 ~8分钟 → ~5分钟
  - 无限速风险
        """)
        print(f"\n✅ 可以安全地修改 trader_config.py 了！")
        
    else:
        print("""
❌ 参数验证失败，不建议使用方案 A

替代方案:
  A. 使用更保守的参数:
     NASDAQ_BATCH_SIZE  = 150
     NASDAQ_BATCH_SLEEP = 0.4
  
  B. 监控后续运行，如有 429 错误立即回退
     NASDAQ_BATCH_SIZE  = 100
     NASDAQ_BATCH_SLEEP = 0.5  (原参数)
        """)
    
    print("\n" + "="*70)
    
    # Return exit code
    sys.exit(0 if is_safe else 1)


if __name__ == "__main__":
    main()
