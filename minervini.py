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

    return parser


# ═══════════════════════════════════════════════════════════════════════════════
# Main
# ═══════════════════════════════════════════════════════════════════════════════

_DISPATCH = {
    "scan":      cmd_scan,
    "analyze":   cmd_analyze,
    "watchlist": cmd_watchlist,
    "wl":        cmd_watchlist,
    "positions": cmd_positions,
    "pos":       cmd_positions,
    "market":    cmd_market,
    "report":    cmd_report,
    "daily":     cmd_daily,
    "rs":        cmd_rs,
    "vcp":       cmd_vcp,
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
