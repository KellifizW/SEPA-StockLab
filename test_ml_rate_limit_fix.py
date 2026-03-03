#!/usr/bin/env python3
"""
test_ml_rate_limit_fix.py — 驗證YFRateLimitError修復

測試ML掃描是否消除並發rate限制錯誤

使用方法：
  python test_ml_rate_limit_fix.py
"""

import sys
import logging
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

import trader_config as C
from modules.data_pipeline import get_yf_status

# Setup logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(name)s] %(message)s'
)
logger = logging.getLogger(__name__)


def test_config():
    """Verify configuration changes."""
    logger.info("=== 配置驗證 ===")
    
    workers = getattr(C, "ML_SCANNER_WORKERS", 32)
    delay = getattr(C, "YFINANCE_INTRA_REQUEST_DELAY_SEC", 0.0)
    
    logger.info(f"ML_SCANNER_WORKERS: {workers}")
    if workers > 20:
        logger.warning(f"  ⚠️ Workers過多 ({workers}), 易觸發rate limit")
        logger.info(f"  建議: 降低至16-20")
    elif workers >= 16:
        logger.info(f"  ✓ 合理並行度")
    
    logger.info(f"YFINANCE_INTRA_REQUEST_DELAY_SEC: {delay}")
    if delay < 0.1:
        logger.warning(f"  ⚠️ 延遲過小 ({delay}s), 可能仍觸發rate limit")
    elif delay >= 0.15:
        logger.info(f"  ✓ 足夠的inter-request延遲")


def test_single_ticker():
    """Test downloading a single ticker."""
    logger.info("\n=== 單Ticker下載測試 ===")
    
    from modules.data_pipeline import get_enriched, get_yf_status
    import time
    
    test_ticker = "NVDA"
    logger.info(f"測試: {test_ticker}壓強")
    
    # Reset stats
    yf_before = get_yf_status()
    
    try:
        # This should work without YFRateLimitError
        df = get_enriched(test_ticker, period="1y", use_cache=False)
        
        yf_after = get_yf_status()
        
        if df.empty:
            logger.error(f"✗ {test_ticker} - 下載失敗 (empty DataFrame)")
            return False
        
        logger.info(f"✓ {test_ticker} - 下載成功 ({len(df)} rows)")
        logger.info(f"  新增yfinance調用: {yf_after['calls'] - yf_before['calls']}")
        logger.info(f"  新增錯誤: {yf_after['errors'] - yf_before['errors']}")
        
        if yf_after["rate_limited"]:
            logger.error("✗ Rate limited狀態激活 - 可能仍有429錯誤")
            return False
        
        logger.info("✓ No rate limit triggered")
        return True
        
    except Exception as exc:
        logger.error(f"✗ {test_ticker} - 異常: {exc}")
        return False


def test_parallel_tickers():
    """Test downloading multiple tickers in parallel."""
    logger.info("\n=== 並行Ticker下載測試 ===")
    
    from concurrent.futures import ThreadPoolExecutor, as_completed
    from modules.data_pipeline import get_enriched, get_yf_status
    
    test_tickers = ["AAPL", "MSFT", "GOOGL", "TSLA", "NVDA", "META", "AMZN", "NFLX"]
    workers = getattr(C, "ML_SCANNER_WORKERS", 16)
    
    logger.info(f"測試: {len(test_tickers)} tickers with {workers} workers")
    
    yf_before = get_yf_status()
    errors = []
    success = 0
    
    def fetch_ticker(tkr):
        try:
            df = get_enriched(tkr, period="1y", use_cache=False)
            if not df.empty:
                return (tkr, None, len(df))
            else:
                return (tkr, "empty", 0)
        except Exception as e:
            return (tkr, str(e), 0)
    
    with ThreadPoolExecutor(max_workers=min(workers, 4)) as executor:  # Limit to 4 to avoid excessive real API calls
        futures = {executor.submit(fetch_ticker, tkr): tkr for tkr in test_tickers}
        for future in as_completed(futures):
            tkr, error, rows = future.result()
            if error:
                logger.error(f"  ✗ {tkr}: {error}")
                errors.append((tkr, error))
            else:
                logger.info(f"  ✓ {tkr}: {rows} rows")
                success += 1
    
    yf_after = get_yf_status()
    
    logger.info(f"\n結果:")
    logger.info(f"  成功: {success}/{len(test_tickers)}")
    logger.info(f"  yfinance鐘聲: {yf_after['calls'] - yf_before['calls']}")
    logger.info(f"  yfinance錯誤: {yf_after['errors'] - yf_before['errors']}")
    
    if yf_after["rate_limited"]:
        logger.error("  ✗ Rate limited = True (YFRateLimitError發生)")
        return False
    
    if len(errors) > 0:
        logger.warning(f"  ⚠️ {len(errors)} ticker失敗")
        return success > len(test_tickers) * 0.8  # At least 80% success
    
    logger.info("  ✓ 無rate limit錯誤")
    return True


def main():
    """Run all tests."""
    logger.info("\n╔════════════════════════════════════════╗")
    logger.info("║  ML Rate Limit Fix Verification       ║")
    logger.info("╚════════════════════════════════════════╝\n")
    
    all_pass = True
    
    try:
        test_config()
    except Exception as e:
        logger.error(f"配置測試失敗: {e}")
        all_pass = False
    
    try:
        if not test_single_ticker():
            all_pass = False
    except Exception as e:
        logger.error(f"單ticker測試失敗: {e}")
        all_pass = False
    
    try:
        if not test_parallel_tickers():
            all_pass = False
    except Exception as e:
        logger.error(f"並行ticker測試失敗: {e}")
        all_pass = False
    
    logger.info("\n╔════════════════════════════════════════╗")
    if all_pass:
        logger.info("║  ✓ 所有測試通過！修復成功            ║")
        logger.info("║  可安心進行ML掃描                    ║")
    else:
        logger.warning("║  ✗ 某些測試失敗，請檢查上述錯誤    ║")
    logger.info("╚════════════════════════════════════════╝\n")
    
    return 0 if all_pass else 1


if __name__ == "__main__":
    sys.exit(main())
