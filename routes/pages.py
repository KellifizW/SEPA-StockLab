"""Page (HTML) routes — serve Jinja2 templates for every top-level page."""

from datetime import date
from flask import Blueprint, render_template, request

from routes.helpers import (
    ROOT, _load_watchlist, _load_positions, _get_account_size,
    _load_currency_setting, _load_combined_last,
    _latest_report, _load_market_last,
)

bp = Blueprint("pages", __name__)


@bp.route("/")
def dashboard():
    wl = _load_watchlist()
    pos = _load_positions()
    wl_counts = {g: len(v) for g, v in wl.items()}
    total_watchlist = sum(wl_counts.values())

    watchlist_rows = []
    for strategy in ("SEPA", "QM", "ML"):
        for ticker, data in (wl.get(strategy, {}) or {}).items():
            if strategy == "QM":
                analyze_url = f"/qm/analyze?ticker={ticker}"
            elif strategy == "ML":
                analyze_url = f"/ml/analyze?ticker={ticker}"
            else:
                analyze_url = f"/analyze?ticker={ticker}"
            watchlist_rows.append({
                "strategy": strategy,
                "ticker": ticker,
                "rs_rank": data.get("rs_rank"),
                "vcp_grade": data.get("vcp_grade"),
                "pivot_price": data.get("pivot_price"),
                "analyze_url": analyze_url,
            })
    watchlist_rows.sort(key=lambda r: r["ticker"])

    account_size, nav_sync_time, nav_sync_status = _get_account_size()
    _, usd_hkd_rate = _load_currency_setting()
    market_cached = _load_market_last()
    market_summary = market_cached.get("result") if isinstance(market_cached, dict) else {}
    market_cached_at = market_cached.get("saved_at") if isinstance(market_cached, dict) else ""

    # Dashboard baseline: treat NAV as HKD native amount for display toggle.
    display_currency = "HKD"
    currency_symbol = "HK$"
    account_size_display = f"HK${account_size:,.2f}"

    return render_template(
        "dashboard.html",
        wl=wl, wl_counts=wl_counts,
        total_watchlist=total_watchlist,
        watchlist_rows=watchlist_rows,
        market_summary=market_summary or {},
        market_cached_at=market_cached_at,
        positions=pos, account_size=account_size,
        account_size_display=account_size_display,
        currency=display_currency, currency_symbol=currency_symbol,
        usd_hkd_rate=usd_hkd_rate,
        nav_sync_time=nav_sync_time,
        nav_sync_status=nav_sync_status,
        today=date.today().isoformat(),
    )


@bp.route("/scan")
def scan_page():
    return render_template("scan.html")


@bp.route("/combined")
def combined_scan_page():
    return render_template("combined_scan.html")


@bp.route("/analyze")
def analyze_page():
    ticker = request.args.get("ticker", "")
    return render_template("analyze.html", prefill=ticker)


@bp.route("/watchlist")
def watchlist_page():
    wl = _load_watchlist()
    return render_template("watchlist.html", wl=wl)


@bp.route("/positions")
def positions_page():
    pos = _load_positions()
    account_size, nav_sync_time, nav_sync_status = _get_account_size()
    return render_template(
        "positions.html", positions=pos,
        account_size=account_size,
        nav_sync_time=nav_sync_time,
        nav_sync_status=nav_sync_status,
    )


@bp.route("/market")
def market_page():
    return render_template("market.html")


@bp.route("/vcp")
def vcp_page():
    ticker = request.args.get("ticker", "")
    return render_template("vcp.html", prefill=ticker)


@bp.route("/guide")
def guide_page():
    guide_path = ROOT / "docs" / "GUIDE.md"
    content = guide_path.read_text(encoding="utf-8") if guide_path.exists() else "Guide not found."
    return render_template("guide.html", content=content)


@bp.route("/calc")
def calc_page():
    account_size, nav_sync_time, nav_sync_status = _get_account_size()
    return render_template(
        "calc.html", account_size=account_size,
        nav_sync_time=nav_sync_time,
        nav_sync_status=nav_sync_status,
    )


# ─── Qullamaggie (QM) pages ──────────────────────────────────────────────────

@bp.route("/qm/scan")
def qm_scan_page():
    return render_template("qm_scan.html")


@bp.route("/qm/analyze")
def qm_analyze_page():
    ticker = request.args.get("ticker", "")
    return render_template("qm_analyze.html", prefill=ticker)


@bp.route("/qm/guide")
def qm_guide_page():
    guide_path = ROOT / "docs" / "QullamaggieStockguide.md"
    content = guide_path.read_text(encoding="utf-8") if guide_path.exists() else "QM Guide not found."
    return render_template("qm_guide.html", content=content)


# ─── Martin Luk (ML) pages ───────────────────────────────────────────────────

@bp.route("/ml/scan")
def ml_scan_page():
    return render_template("ml_scan.html")


@bp.route("/ml/analyze")
def ml_analyze_page():
    ticker = request.args.get("ticker", "")
    return render_template("ml_analyze.html", prefill=ticker)


@bp.route("/ml/guide")
def ml_guide_page():
    guide_path = ROOT / "docs" / "MartinLukStockGuidePart1.md"
    content = guide_path.read_text(encoding="utf-8") if guide_path.exists() else "ML Guide not found."
    return render_template("ml_guide.html", content=content)


# ─── Backtest pages ──────────────────────────────────────────────────────────

@bp.route("/backtest")
def page_backtest():
    return render_template("backtest.html")


@bp.route("/qm/backtest")
def page_qm_backtest():
    return render_template("qm_backtest.html")


@bp.route("/auto-trade")
def page_auto_trade():
    return render_template("auto_trade.html")


# ─── Serve latest HTML report inline ─────────────────────────────────────────

@bp.route("/latest-report")
def latest_report_page():
    rpt = _latest_report()
    if rpt is None:
        return "<h3>No report generated yet. Go to Dashboard → Generate Report.</h3>", 404
    return rpt.read_text(encoding="utf-8"), 200, {"Content-Type": "text/html"}
