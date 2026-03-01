#!/usr/bin/env python3
"""
minervini.py
─────────────
Minervini SEPA Stock Screener & Monitor  —  CLI Entry Point

Usage examples:
  python minervini.py scan
  python minervini.py scan --refresh-rs
  python minervini.py analyze NVDA
  python minervini.py analyze NVDA --account 50000
  python minervini.py watchlist list
  python minervini.py watchlist add NVDA
  python minervini.py watchlist remove NVDA
  python minervini.py watchlist refresh
  python minervini.py watchlist promote NVDA
  python minervini.py watchlist demote NVDA
  python minervini.py positions list
  python minervini.py positions add NVDA 500.00 10 480.00
  python minervini.py positions close NVDA 560.00
  python minervini.py positions check
  python minervini.py positions update NVDA 510.00
  python minervini.py market
  python minervini.py report
  python minervini.py daily
  python minervini.py rs top
  python minervini.py rs top --min 85
  python minervini.py vcp NVDA

Qullamaggie (QM) commands:
  python minervini.py qm-scan
  python minervini.py qm-scan --min-star 4 --top-n 25
  python minervini.py qm-analyze NVDA
  python minervini.py qm-analyze NVDA --plan
  python minervini.py qm-analyze NVDA SMCI TSLA --plan

Martin Luk (ML) commands:
  python minervini.py ml-scan
  python minervini.py ml-scan --min-star 3 --top-n 30
  python minervini.py ml-analyze NVDA
  python minervini.py ml-analyze NVDA --plan
  python minervini.py ml-analyze NVDA AAPL --plan
"""

import sys
import argparse
import logging
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

import trader_config as C

# ─── Logging ─────────────────────────────────────────────────────────────────
logging.basicConfig(
    level=logging.WARNING,
    format="%(levelname)s  %(name)s  %(message)s",
)
logger = logging.getLogger("minervini")

_BOLD  = "\033[1m"
_RESET = "\033[0m"


# ═══════════════════════════════════════════════════════════════════════════════
# Sub-command handlers
# ═══════════════════════════════════════════════════════════════════════════════

def cmd_scan(args):
    """Stage 1 → 2 → 3 full SEPA scan."""
    from modules.screener import run_scan
    from modules.report   import print_scan_table, generate_csv

    print(f"\n{_BOLD}  Running SEPA 3-Stage Scan …{_RESET}")
    results = run_scan(refresh_rs=args.refresh_rs)

    if results is None or (hasattr(results, "__len__") and len(results) == 0):
        print("  No stocks passed the SEPA scan filters.")
        return

    print_scan_table(results, top_n=args.top)

    if args.csv or args.report:
        generate_csv(results)

    if args.report:
        from modules.report import generate_html_report
        generate_html_report(scan_results=results)


def cmd_analyze(args):
    """Deep-dive analysis of a single stock."""
    from modules.stock_analyzer import analyze

    for ticker in args.tickers:
        analyze(
            ticker.upper(),
            account_size=args.account,
            print_report=True,
        )


def cmd_watchlist(args):
    """Watchlist management."""
    from modules import watchlist as wl

    sub = args.sub
    if sub == "list":
        wl.list_all()
    elif sub == "add":
        if not args.ticker:
            print("  Usage: watchlist add TICKER [--grade A/B/C] [--note '...']")
            return
        wl.add(args.ticker.upper(), grade=args.grade, note=args.note or "")
    elif sub == "remove":
        if not args.ticker:
            print("  Usage: watchlist remove TICKER")
            return
        wl.remove(args.ticker.upper())
    elif sub == "refresh":
        wl.refresh()
    elif sub == "promote":
        if not args.ticker:
            print("  Usage: watchlist promote TICKER")
            return
        wl.promote(args.ticker.upper())
    elif sub == "demote":
        if not args.ticker:
            print("  Usage: watchlist demote TICKER")
            return
        wl.demote(args.ticker.upper())
    else:
        print(f"  Unknown watchlist sub-command: {sub}")
        print("  Available: list | add | remove | refresh | promote | demote")


def cmd_positions(args):
    """Position management and daily health check."""
    from modules import position_monitor as pm

    sub = args.sub
    if sub == "list":
        pm.list_positions()
    elif sub == "check":
        pm.daily_check(account_size=args.account)
    elif sub == "add":
        # positions add TICKER ENTRY SHARES STOP [TARGET]
        if len(args.params) < 4:
            print("  Usage: positions add TICKER ENTRY SHARES STOP [TARGET]")
            print("  Example: positions add NVDA 500.00 10 480.00 560.00")
            return
        ticker    = args.params[0].upper()
        buy_price = float(args.params[1])
        shares    = int(args.params[2])
        stop      = float(args.params[3])
        target    = float(args.params[4]) if len(args.params) > 4 else None
        note      = args.note or ""
        pm.add_position(ticker, buy_price, shares, stop, target, note)
    elif sub == "close":
        # positions close TICKER EXIT_PRICE
        if len(args.params) < 2:
            print("  Usage: positions close TICKER EXIT_PRICE [--note reason]")
            return
        ticker = args.params[0].upper()
        exit_p = float(args.params[1])
        pm.close_position(ticker, exit_p, reason=args.note or "")
    elif sub == "update":
        # positions update TICKER NEW_STOP
        if len(args.params) < 2:
            print("  Usage: positions update TICKER NEW_STOP")
            return
        ticker    = args.params[0].upper()
        new_stop  = float(args.params[1])
        pm.update_stop(ticker, new_stop)
    else:
        print(f"  Unknown positions sub-command: {sub}")
        print("  Available: list | check | add | close | update")


def cmd_market(args):
    """Market environment assessment."""
    from modules.market_env import assess
    assess(verbose=True)


def cmd_report(args):
    """Generate HTML and/or CSV report without running a new scan."""
    from modules.report import generate_html_report, generate_csv
    import json

    # Load existing data
    watchlist_path = ROOT / C.DATA_DIR / "watchlist.json"
    positions_path = ROOT / C.DATA_DIR / "positions.json"

    wl_data  = {}
    pos_data = {}
    if watchlist_path.exists():
        wl_data  = json.loads(watchlist_path.read_text(encoding="utf-8"))
    if positions_path.exists():
        raw      = json.loads(positions_path.read_text(encoding="utf-8"))
        pos_data = raw.get("positions", {})

    generate_html_report(
        scan_results=None,
        market_env=None,
        watchlist=wl_data,
        positions=pos_data,
    )


def cmd_daily(args):
    """
    Full daily pipeline:
      1. Market environment
      2. Position health check
      3. Watchlist refresh
      4. SEPA scan (optional)
      5. HTML report
    """
    import json
    from modules.market_env     import assess       as mkt_assess
    from modules.position_monitor import daily_check
    from modules.watchlist       import refresh     as wl_refresh
    from modules.report          import generate_html_report, generate_csv

    print(f"\n{'═'*65}")
    print(f"{_BOLD}  MINERVINI SEPA  —  DAILY PIPELINE{_RESET}")
    print(f"{'═'*65}\n")

    # 1. Market
    print(f"{_BOLD}[1/4] Market Environment{_RESET}")
    mkt = mkt_assess(verbose=True)

    # 2. Position check
    print(f"\n{_BOLD}[2/4] Position Health Check{_RESET}")
    daily_check(account_size=args.account)

    # 3. Watchlist refresh
    print(f"\n{_BOLD}[3/4] Watchlist Refresh{_RESET}")
    wl_refresh()

    # 4. SEPA scan (only in bull market by default, or if forced)
    scan_results = None
    if args.scan or mkt.get("regime", "") in ("BULL_CONFIRMED", "BULL_UNCONFIRMED"):
        print(f"\n{_BOLD}[4/4] Running SEPA Scan{_RESET}")
        from modules.screener import run_scan
        scan_results = run_scan(refresh_rs=False)
    else:
        print(f"\n[4/4] Skipping scan (market regime: {mkt.get('regime')})")

    # 5. HTML report
    watchlist_path = ROOT / C.DATA_DIR / "watchlist.json"
    positions_path = ROOT / C.DATA_DIR / "positions.json"
    wl_data  = {}
    pos_data = {}
    if watchlist_path.exists():
        wl_data  = json.loads(watchlist_path.read_text(encoding="utf-8"))
    if positions_path.exists():
        raw      = json.loads(positions_path.read_text(encoding="utf-8"))
        pos_data = raw.get("positions", {})

    out = generate_html_report(
        scan_results=scan_results,
        market_env=mkt,
        watchlist=wl_data,
        positions=pos_data,
        account_size=args.account,
    )

    if scan_results is not None:
        generate_csv(scan_results)

    if out:
        print(f"\n  Open report: {out}")

    print(f"\n{'═'*65}")
    print(f"{_BOLD}  Daily pipeline complete.{_RESET}")
    print(f"{'═'*65}\n")


def cmd_rs(args):
    """RS ranking utilities."""
    from modules.rs_ranking import get_rs_rank, get_rs_top, compute_rs_rankings
    from modules.report     import print_scan_table

    sub = args.sub
    if sub == "top":
        min_rs = args.min if args.min else C.TT9_MIN_RS_RANK
        print(f"\n  Fetching stocks with RS ≥ {min_rs} …")
        df = get_rs_top(min_rs)
        if df is not None and not df.empty:
            print_scan_table(df, top_n=args.limit or 50)
        else:
            print("  No results (RS cache may need refresh).")
    elif sub == "refresh":
        print("  Rebuilding RS universe and rankings …")
        compute_rs_rankings(force_refresh=True)
        print("  ✓ RS rankings refreshed.")
    elif sub == "get":
        if not args.ticker:
            print("  Usage: rs get TICKER")
            return
        rank = get_rs_rank(args.ticker.upper())
        if rank < 0:
            print(f"  {args.ticker.upper()}  RS Rank: N/A (not in RS universe)")
        else:
            print(f"  {args.ticker.upper()}  RS Rank: {rank:.0f}")
    else:
        print(f"  Unknown rs sub-command: {sub}")
        print("  Available: top | refresh | get")


def cmd_vcp(args):
    """VCP analysis for a single stock."""
    from modules.data_pipeline import get_enriched
    from modules.vcp_detector  import detect_vcp

    for ticker in args.tickers:
        ticker = ticker.upper()
        print(f"\n  Fetching data for {ticker} …")
        df = get_enriched(ticker, period="2y")
        if df is None or df.empty:
            print(f"  No data for {ticker}")
            continue

        result = detect_vcp(df)
        _print_vcp(ticker, result)


def cmd_qm_scan(args):
    """Run Qullamaggie 3-stage breakout swing scan."""
    from modules.qm_screener import run_qm_scan

    min_star = args.min_star
    top_n    = args.top_n

    print(f"\n{'═'*65}")
    print(f"{_BOLD}  QULLAMAGGIE 突破波段掃描  Breakout Swing Scan{_RESET}")
    print(f"{'═'*65}")
    print(f"  條件: 6M≥150%, 3M≥50%, 1M≥25%, ADR>5%, $Vol>$5M")
    print(f"  最低星級: {min_star}★  顯示數量: {top_n}\n")

    result = run_qm_scan(min_star=min_star, top_n=top_n)
    if isinstance(result, tuple):
        df_passed, _ = result
    else:
        df_passed = result

    if df_passed is None or (hasattr(df_passed, "empty") and df_passed.empty):
        print("  未找到符合條件的股票。No stocks passed QM filters.")
        return

    rows = df_passed.to_dict(orient="records") if hasattr(df_passed, "to_dict") else list(df_passed)
    if not rows:
        print("  未找到符合條件的股票。No stocks passed QM filters.")
        return

    _GREEN  = "\033[92m"
    _YELLOW = "\033[93m"
    _RED    = "\033[91m"
    _CYAN   = "\033[96m"
    _RESET  = "\033[0m"

    print(f"\n{'─'*80}")
    print(f"  {'#':<3} {'TICKER':<8} {'★':<6} {'ADR%':<7} {'$VOL':<8} "
          f"{'1M%':<8} {'3M%':<8} {'6M%':<8} {'SETUP':<10} {'VETO':<5}")
    print(f"{'─'*80}")

    for i, r in enumerate(rows, 1):
        star = float(r.get("qm_star") or r.get("stars") or 0)
        star_col = _GREEN if star >= 5 else _YELLOW if star >= 4 else _RED if star < 3 else ""
        veto = r.get("veto", "")
        adr  = r.get("adr")
        dvol = r.get("dollar_volume_m")
        m1   = r.get("mom_1m")
        m3   = r.get("mom_3m")
        m6   = r.get("mom_6m")
        setup = (r.get("setup_type") or "")[:8]

        print(f"  {i:<3} {_CYAN}{r.get('ticker',''):<8}{_RESET} "
              f"{star_col}{star:.1f}★{_RESET:<5} "
              f"{f'{adr:.1f}%' if adr else '—':<7} "
              f"{f'${dvol:.1f}M' if dvol else '—':<8} "
              f"{f'+{m1:.1f}%' if m1 and m1>=0 else f'{m1:.1f}%' if m1 else '—':<8} "
              f"{f'+{m3:.1f}%' if m3 and m3>=0 else f'{m3:.1f}%' if m3 else '—':<8} "
              f"{f'+{m6:.1f}%' if m6 and m6>=0 else f'{m6:.1f}%' if m6 else '—':<8} "
              f"{setup:<10} "
              f"{_RED+veto+_RESET if veto else '':<5}")

    print(f"{'─'*80}")
    print(f"  共 {len(rows)} 個高質量設置 | Total {len(rows)} setups found\n")


def cmd_qm_analyze(args):
    """Qullamaggie deep analysis for one or more tickers (star rating + trade plan)."""
    from modules.qm_analyzer import analyze_qm

    for ticker in args.tickers:
        ticker = ticker.upper()
        print(f"\n  Analyzing {ticker} …")
        try:
            result = analyze_qm(ticker, print_report=True)
            if result and args.plan:
                plan = result.get("trade_plan") or {}
                if plan:
                    print(f"\n  {'─'*40}")
                    print(f"  交易方案 Trade Plan for {ticker}")
                    print(f"  {'─'*40}")
                    d1 = plan.get("day1_stop")
                    d2 = plan.get("day2_stop")
                    d3 = plan.get("day3_stop")
                    p1 = plan.get("profit_step1")
                    p2 = plan.get("profit_step2")
                    if d1: print(f"  Day 1 Stop: ${d1:.2f}  (日低位 LOD)")
                    if d2: print(f"  Day 2 Stop: ${d2:.2f}  (成本 Break-even)")
                    if d3: print(f"  Day 3+ Trl: ${d3:.2f}  (10MA 追蹤)")
                    if p1: print(f"  利潤目標1:  ${p1:.2f}  (25% 漲幅，賣 1/3)")
                    if p2: print(f"  利潤目標2:  ${p2:.2f}  (50% 漲幅，再賣 1/3)")
        except Exception as exc:
            print(f"  Error analyzing {ticker}: {exc}")
            if args.debug:
                import traceback
                traceback.print_exc()


def cmd_ml_scan(args):
    """Run Martin Luk 3-stage pullback swing scan."""
    from modules.ml_screener import run_ml_scan

    min_star = args.min_star
    top_n    = args.top_n

    print(f"\n{'═'*65}")
    print(f"{_BOLD}  MARTIN LUK 回調波段掃描  Pullback Swing Scan{_RESET}")
    print(f"{'═'*65}")
    print(f"  條件: ADR≥4%, $Vol≥$5M, EMA stacking, 3M≥30%")
    print(f"  最低星級: {min_star}★  顯示數量: {top_n}\n")

    result = run_ml_scan(min_star=min_star, top_n=top_n)
    if isinstance(result, tuple):
        df_passed, _ = result
    else:
        df_passed = result

    if df_passed is None or (hasattr(df_passed, "empty") and df_passed.empty):
        print("  未找到符合條件的股票。No stocks passed ML filters.")
        return

    rows = df_passed.to_dict(orient="records") if hasattr(df_passed, "to_dict") else list(df_passed)
    if not rows:
        print("  未找到符合條件的股票。No stocks passed ML filters.")
        return

    _GREEN  = "\033[92m"
    _YELLOW = "\033[93m"
    _RED    = "\033[91m"
    _CYAN   = "\033[96m"
    _RESET  = "\033[0m"

    print(f"\n{'─'*85}")
    print(f"  {'#':<3} {'TICKER':<8} {'★':<6} {'ADR%':<7} {'$VOL':<8} "
          f"{'3M%':<8} {'6M%':<8} {'SETUP':<12} {'EMA':<6} {'PB':<6} {'AVWAP':<6}")
    print(f"{'─'*85}")

    for i, r in enumerate(rows, 1):
        star = float(r.get("ml_star") or r.get("stars") or 0)
        star_col = _GREEN if star >= 4 else _YELLOW if star >= 3 else _RED if star < 2 else ""
        adr  = r.get("adr")
        dvol = r.get("dollar_volume_m")
        m3   = r.get("mom_3m")
        m6   = r.get("mom_6m")
        setup = (r.get("setup_type") or "")[:10]
        ema  = r.get("ema_structure", "")
        pb   = r.get("pullback_quality", "")
        avwap = r.get("avwap_score", "")

        print(f"  {i:<3} {_CYAN}{r.get('ticker',''):<8}{_RESET} "
              f"{star_col}{star:.1f}★{_RESET:<5} "
              f"{f'{adr:.1f}%' if adr else '—':<7} "
              f"{f'${dvol:.1f}M' if dvol else '—':<8} "
              f"{f'+{m3:.1f}%' if m3 and m3>=0 else f'{m3:.1f}%' if m3 else '—':<8} "
              f"{f'+{m6:.1f}%' if m6 and m6>=0 else f'{m6:.1f}%' if m6 else '—':<8} "
              f"{setup:<12} "
              f"{ema:<6} "
              f"{pb:<6} "
              f"{avwap:<6}")

    print(f"{'─'*85}")
    print(f"  共 {len(rows)} 個回調設置 | Total {len(rows)} pullback setups found\n")


def cmd_ml_analyze(args):
    """Martin Luk deep analysis for one or more tickers (star rating + trade plan)."""
    from modules.ml_analyzer import analyze_ml

    for ticker in args.tickers:
        ticker = ticker.upper()
        print(f"\n  Analyzing {ticker} (Martin Luk) …")
        try:
            result = analyze_ml(ticker, print_report=True)
            if result and args.plan:
                plan = result.get("trade_plan") or {}
                if plan:
                    print(f"\n  {'─'*45}")
                    print(f"  交易方案 Trade Plan for {ticker} (ML)")
                    print(f"  {'─'*45}")
                    stop  = plan.get("stop_price")
                    shares = plan.get("shares")
                    pos_val = plan.get("position_value")
                    t3r   = plan.get("target_3r")
                    t5r   = plan.get("target_5r")
                    trail = plan.get("trail_ema")
                    if stop:   print(f"  Day 1 Stop: ${stop:.2f}  (LOD - 0.3%)")
                    if shares: print(f"  Shares:     {shares}")
                    if pos_val: print(f"  Position:   ${pos_val:,.0f}")
                    if t3r:    print(f"  3R Target:  ${t3r:.2f}  (sell 15%)")
                    if t5r:    print(f"  5R Target:  ${t5r:.2f}  (sell 15%)")
                    if trail:  print(f"  Trail:      {trail} EMA (remaining position)")
        except Exception as exc:
            print(f"  Error analyzing {ticker}: {exc}")
            if args.debug:
                import traceback
                traceback.print_exc()


def _print_vcp(ticker: str, r: dict):
    """Pretty-print a VCP result."""
    _GREEN  = "\033[92m"
    _YELLOW = "\033[93m"
    _RED    = "\033[91m"
    _BOLD   = "\033[1m"
    _RESET  = "\033[0m"

    if not r:
        print(f"  {ticker}: VCP analysis failed")
        return

    valid  = r.get("is_valid_vcp", False)
    score  = r.get("vcp_score", 0)
    grade  = r.get("grade", "D")
    t_cnt  = r.get("t_count", 0)
    weeks  = r.get("base_weeks", 0)
    depth  = r.get("base_depth_pct", 0)
    pivot  = r.get("pivot_price")

    g_clr  = _GREEN if grade in ("A", "B") else _YELLOW if grade == "C" else _RED

    print(f"\n{'─'*50}")
    print(f"{_BOLD}  VCP Analysis: {ticker}{_RESET}")
    print(f"{'─'*50}")
    print(f"  Valid VCP:    {'YES ✓' if valid else 'NO ✗'}")
    print(f"  Grade:        {g_clr}{grade}{_RESET}   Score: {score}/100")
    print(f"  T-count:      {t_cnt}  (contractions)")
    print(f"  Base width:   {weeks:.0f} weeks")
    print(f"  Base depth:   {depth:.1f}%")
    if pivot:
        print(f"  Pivot price:  ${pivot:.2f}")
    print(f"  ATR contracting: {'✓' if r.get('atr_contracting') else '✗'}")
    print(f"  BB contracting:  {'✓' if r.get('bb_contracting') else '✗'}")
    print(f"  Volume dry-up:   {'✓' if r.get('vol_dry') else '✗'}")
    if r.get("notes"):
        print(f"\n  Notes:")
        for note in r["notes"]:
            print(f"    • {note}")
    print()


# ═══════════════════════════════════════════════════════════════════════════════
# Argument parser
# ═══════════════════════════════════════════════════════════════════════════════

def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="minervini",
        description="Minervini SEPA Stock Screener & Monitor",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument("--debug", action="store_true", help="Enable debug logging")
    sub = parser.add_subparsers(dest="command", metavar="COMMAND")

    # ── scan ──────────────────────────────────────────────────────────────────
    p_scan = sub.add_parser("scan", help="Run full SEPA 3-stage screen")
    p_scan.add_argument("--refresh-rs", action="store_true",
                        help="Force rebuild RS rankings (slow, ~10 min)")
    p_scan.add_argument("--top",    type=int, default=30,
                        help="Show top N results (default: 30)")
    p_scan.add_argument("--csv",    action="store_true", help="Export results to CSV")
    p_scan.add_argument("--report", action="store_true", help="Generate HTML report")

    # ── analyze ───────────────────────────────────────────────────────────────
    p_an = sub.add_parser("analyze", help="Deep SEPA analysis for one or more tickers")
    p_an.add_argument("tickers", nargs="+", metavar="TICKER")
    p_an.add_argument("--account", type=float, default=C.ACCOUNT_SIZE,
                      help=f"Account size in $ (default: {C.ACCOUNT_SIZE:,.0f})")

    # ── watchlist ─────────────────────────────────────────────────────────────
    p_wl = sub.add_parser("watchlist", help="Manage the SEPA watchlist",
                           aliases=["wl"])
    p_wl.add_argument("sub",   choices=["list","add","remove","refresh","promote","demote"])
    p_wl.add_argument("ticker", nargs="?", default=None)
    p_wl.add_argument("--grade", choices=["A","B","C"], default=None,
                      help="Override auto-grading")
    p_wl.add_argument("--note", default="")

    # ── positions ─────────────────────────────────────────────────────────────
    p_pos = sub.add_parser("positions", help="Track and manage open positions",
                            aliases=["pos"])
    p_pos.add_argument("sub", choices=["list","check","add","close","update"])
    p_pos.add_argument("params", nargs="*", metavar="PARAM")
    p_pos.add_argument("--account", type=float, default=C.ACCOUNT_SIZE)
    p_pos.add_argument("--note", default="")

    # ── market ────────────────────────────────────────────────────────────────
    sub.add_parser("market", help="Market environment assessment (SPY/QQQ/IWM)")

    # ── report ────────────────────────────────────────────────────────────────
    sub.add_parser("report", help="Generate HTML report from saved data")

    # ── daily ─────────────────────────────────────────────────────────────────
    p_day = sub.add_parser("daily", help="Full daily pipeline (market + positions + watchlist + report)")
    p_day.add_argument("--account", type=float, default=C.ACCOUNT_SIZE)
    p_day.add_argument("--scan", action="store_true",
                       help="Force running scan regardless of market regime")

    # ── rs ────────────────────────────────────────────────────────────────────
    p_rs = sub.add_parser("rs", help="RS ranking utilities")
    p_rs.add_argument("sub", choices=["top","refresh","get"])
    p_rs.add_argument("ticker", nargs="?", default=None)
    p_rs.add_argument("--min",   type=float, default=None,
                      help="Minimum RS rank for 'top' sub-command")
    p_rs.add_argument("--limit", type=int, default=50,
                      help="Max rows to display for 'top'")

    # ── vcp ───────────────────────────────────────────────────────────────────
    p_vcp = sub.add_parser("vcp", help="VCP pattern analysis for one or more tickers")
    p_vcp.add_argument("tickers", nargs="+", metavar="TICKER")

    # ── qm-scan ───────────────────────────────────────────────────────────────
    p_qms = sub.add_parser("qm-scan",
                            help="Qullamaggie 突破波段掃描 Breakout Swing Scan")
    p_qms.add_argument("--min-star", type=float, default=getattr(C, "QM_SCAN_MIN_STAR", 3.0),
                       metavar="STARS",
                       help="最低星級過濾 Minimum star rating to display (default: 3.0)")
    p_qms.add_argument("--top-n", type=int, default=getattr(C, "QM_SCAN_TOP_N", 50),
                       metavar="N",
                       help="顯示前 N 名結果 Show top N results (default: 50)")

    # ── qm-analyze ────────────────────────────────────────────────────────────
    p_qma = sub.add_parser("qm-analyze",
                            help="Qullamaggie 個股星級分析 Single-stock star rating & trade plan")
    p_qma.add_argument("tickers", nargs="+", metavar="TICKER",
                       help="One or more stock tickers to analyze")
    p_qma.add_argument("--plan", action="store_true",
                       help="Print trade plan (stops + profit targets) after analysis")

    # ── ml-scan ───────────────────────────────────────────────────────────────
    p_mls = sub.add_parser("ml-scan",
                            help="Martin Luk 回調波段掃描 Pullback Swing Scan")
    p_mls.add_argument("--min-star", type=float, default=getattr(C, "ML_SCAN_MIN_STAR", 2.5),
                       metavar="STARS",
                       help="最低星級過濾 Minimum star rating to display (default: 2.5)")
    p_mls.add_argument("--top-n", type=int, default=getattr(C, "ML_SCAN_TOP_N", 50),
                       metavar="N",
                       help="顯示前 N 名結果 Show top N results (default: 50)")

    # ── ml-analyze ────────────────────────────────────────────────────────────
    p_mla = sub.add_parser("ml-analyze",
                            help="Martin Luk 個股星級分析 Single-stock star rating & trade plan")
    p_mla.add_argument("tickers", nargs="+", metavar="TICKER",
                       help="One or more stock tickers to analyze")
    p_mla.add_argument("--plan", action="store_true",
                       help="Print trade plan (stops + profit targets) after analysis")

    return parser


# ═══════════════════════════════════════════════════════════════════════════════
# Main
# ═══════════════════════════════════════════════════════════════════════════════

_DISPATCH = {
    "scan":        cmd_scan,
    "analyze":     cmd_analyze,
    "watchlist":   cmd_watchlist,
    "wl":          cmd_watchlist,
    "positions":   cmd_positions,
    "pos":         cmd_positions,
    "market":      cmd_market,
    "report":      cmd_report,
    "daily":       cmd_daily,
    "rs":          cmd_rs,
    "vcp":         cmd_vcp,
    "qm-scan":     cmd_qm_scan,
    "qm-analyze":  cmd_qm_analyze,
    "ml-scan":     cmd_ml_scan,
    "ml-analyze":  cmd_ml_analyze,
}


def main():
    parser = build_parser()
    args   = parser.parse_args()

    if args.debug:
        logging.getLogger().setLevel(logging.DEBUG)

    if not args.command:
        parser.print_help()
        return

    handler = _DISPATCH.get(args.command)
    if handler:
        try:
            handler(args)
        except KeyboardInterrupt:
            print("\n  Interrupted by user.")
        except Exception as exc:
            if args.debug:
                import traceback
                traceback.print_exc()
            else:
                print(f"\n  Error: {exc}")
                print("  Run with --debug for full traceback.")
    else:
        print(f"  Unknown command: {args.command}")
        parser.print_help()


if __name__ == "__main__":
    main()
