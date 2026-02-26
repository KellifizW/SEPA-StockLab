"""
modules/report.py
──────────────────
Report Generator  (HTML + CSV + Terminal)

Generates:
  • HTML report (Jinja2 template) saved to reports/
  • CSV export of scan results
  • Formatted terminal scan table (tabulate)
"""

import sys
import logging
from datetime import datetime
from pathlib import Path
from typing import Optional

import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
import trader_config as C

logger = logging.getLogger(__name__)

REPORTS_DIR = ROOT / C.REPORTS_DIR

_GREEN  = "\033[92m"
_YELLOW = "\033[93m"
_RED    = "\033[91m"
_CYAN   = "\033[96m"
_BOLD   = "\033[1m"
_RESET  = "\033[0m"

# ─── Jinja2 HTML template (inline) ───────────────────────────────────────────
_HTML_TEMPLATE = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Minervini SEPA Report — {{ date }}</title>
<style>
  body { font-family: 'Segoe UI', Arial, sans-serif; background: #0f1117;
         color: #e0e0e0; margin: 0; padding: 20px; }
  h1   { color: #00d4ff; border-bottom: 2px solid #00d4ff; padding-bottom: 8px; }
  h2   { color: #aad4ff; margin-top: 28px; }
  .regime-bull  { background: #0a2e1a; border-left: 5px solid #00c851; padding: 12px; }
  .regime-bear  { background: #2e0a0a; border-left: 5px solid #e53935; padding: 12px; }
  .regime-trans { background: #2e2a0a; border-left: 5px solid #ffd600; padding: 12px; }
  table  { border-collapse: collapse; width: 100%; font-size: 13px; }
  th     { background: #1e2333; color: #00d4ff; padding: 8px 10px;
           text-align: left; position: sticky; top: 0; }
  tr:nth-child(even) { background: #161a26; }
  tr:nth-child(odd)  { background: #1a2030; }
  tr:hover { background: #253060; }
  td { padding: 6px 10px; }
  .score-a { color: #00e676; font-weight: bold; }
  .score-b { color: #69f0ae; }
  .score-c { color: #ffd740; }
  .score-d { color: #ff5252; }
  .tag-vcp  { background: #004d40; color: #64ffda; border-radius: 4px;
              padding: 2px 6px; font-size: 11px; }
  .tag-buy  { background: #1b5e20; color: #b9f6ca; border-radius: 4px;
              padding: 2px 6px; font-size: 11px; }
  .tag-watch{ background: #4a148c; color: #ea80fc; border-radius: 4px;
              padding: 2px 6px; font-size: 11px; }
  .footer   { color: #666; font-size: 12px; margin-top: 40px; }
  .metric   { display: inline-block; background: #1e2333; border-radius: 8px;
              padding: 12px 20px; margin: 6px; min-width: 120px; text-align: center; }
  .metric .val { font-size: 22px; font-weight: bold; color: #00d4ff; }
  .metric .lbl { font-size: 11px; color: #888; margin-top: 4px; }
  .section { margin-bottom: 40px; }
</style>
</head>
<body>
<h1>🚀 Minervini SEPA Daily Report</h1>
<p style="color:#888">Generated: {{ date }}  &nbsp;|&nbsp;  Account: ${{ "{:,.0f}".format(account_size) }}</p>

{% if market %}
<div class="section">
<h2>📊 Market Environment</h2>
<div class="regime-{{ 'bull' if 'BULL' in market.regime else 'bear' if 'BEAR' in market.regime else 'trans' }}">
  <strong>Regime:</strong> {{ market.regime.replace('_', ' ') }}
  &nbsp;&nbsp;|&nbsp;&nbsp;
  <strong>Distribution Days:</strong> {{ market.distribution_days }}
  {% if market.breadth_pct is not none %}
  &nbsp;&nbsp;|&nbsp;&nbsp;
  <strong>Breadth (% above SMA200):</strong> {{ market.breadth_pct }}%
  {% endif %}
  <br><em>Action: {{ market.action_matrix.note }}</em>
</div>
</div>
{% endif %}

{% if scan_results is not none and scan_results|length > 0 %}
<div class="section">
<h2>🔍 SEPA Scan Results  ({{ scan_results|length }} stocks)</h2>

<div>
  <div class="metric"><div class="val">{{ scan_results|length }}</div><div class="lbl">Total Stocks</div></div>
  <div class="metric">
    <div class="val">{{ scan_results | selectattr('vcp_grade', 'in', ['A','B']) | list | length }}</div>
    <div class="lbl">VCP A/B Grade</div>
  </div>
  <div class="metric">
    <div class="val">{{ scan_results | selectattr('sepa_score', 'ge', 75) | list | length }}</div>
    <div class="lbl">Score ≥75</div>
  </div>
</div>

<table>
<thead>
<tr>
  <th>#</th><th>Ticker</th><th>SEPA Score</th>
  <th>RS Rank</th><th>VCP</th><th>Pivot</th>
  <th>Price</th><th>SMA50</th><th>SMA150</th><th>SMA200</th>
  <th>TT Pass</th><th>Sector</th><th>Note</th>
</tr>
</thead>
<tbody>
{% for row in scan_results %}
<tr>
  <td>{{ loop.index }}</td>
  <td><strong>{{ row.ticker }}</strong></td>
  <td class="{{ 'score-a' if row.sepa_score >= 80 else 'score-b' if row.sepa_score >= 65 else 'score-c' if row.sepa_score >= 50 else 'score-d' }}">
    {{ "%.1f"|format(row.sepa_score) }}
  </td>
  <td>{{ row.rs_rank | int }}</td>
  <td>{% if row.is_valid_vcp %}<span class="tag-vcp">VCP {{ row.vcp_grade }}</span>{% else %}—{% endif %}</td>
  <td>{% if row.pivot_price %}${{ "%.2f"|format(row.pivot_price) }}{% else %}—{% endif %}</td>
  <td>${{ "%.2f"|format(row.price) }}</td>
  <td>${{ "%.2f"|format(row.sma50) if row.sma50 else '—' }}</td>
  <td>${{ "%.2f"|format(row.sma150) if row.sma150 else '—' }}</td>
  <td>${{ "%.2f"|format(row.sma200) if row.sma200 else '—' }}</td>
  <td>{{ '✅' if row.tt_pass else '❌' }}</td>
  <td>{{ row.sector or '—' }}</td>
  <td>{{ row.note or '' }}</td>
</tr>
{% endfor %}
</tbody>
</table>
</div>
{% endif %}

{% if watchlist and (watchlist.A or watchlist.B or watchlist.C) %}
<div class="section">
<h2>📋 Watchlist</h2>
{% for grade, stocks in watchlist.items() %}
{% if stocks %}
<h3>Grade {{ grade }} — {{ stocks|length }} stocks</h3>
<table>
<thead>
<tr><th>Ticker</th><th>RS Rank</th><th>VCP</th><th>Pivot</th><th>Added</th><th>Note</th></tr>
</thead>
<tbody>
{% for ticker, data in stocks.items() %}
<tr>
  <td><strong>{{ ticker }}</strong></td>
  <td>{{ data.rs_rank | int if data.rs_rank else '—' }}</td>
  <td>{{ data.vcp_grade or '—' }}</td>
  <td>{{ "$%.2f"|format(data.pivot_price) if data.pivot_price else '—' }}</td>
  <td>{{ data.added_date or '—' }}</td>
  <td>{{ data.note or '' }}</td>
</tr>
{% endfor %}
</tbody>
</table>
{% endif %}
{% endfor %}
</div>
{% endif %}

{% if positions %}
<div class="section">
<h2>💼 Open Positions</h2>
<table>
<thead>
<tr><th>Ticker</th><th>Entry</th><th>Stop</th><th>Target</th><th>R:R</th><th>Shares</th><th>Risk $</th><th>Days</th></tr>
</thead>
<tbody>
{% for ticker, pos in positions.items() %}
<tr>
  <td><strong>{{ ticker }}</strong></td>
  <td>${{ "%.2f"|format(pos.buy_price) }}</td>
  <td>${{ "%.2f"|format(pos.stop_loss) }}</td>
  <td>${{ "%.2f"|format(pos.target) }}</td>
  <td>{{ "%.1f"|format(pos.rr) }}:1</td>
  <td>{{ "{:,}".format(pos.shares) }}</td>
  <td>${{ "{:,.0f}".format(pos.risk_dollar) }}</td>
  <td>{{ pos.days_held }}</td>
</tr>
{% endfor %}
</tbody>
</table>
</div>
{% endif %}

<p class="footer">Minervini SEPA Screener &nbsp;|&nbsp; Data: finvizfinance + yfinance + pandas_ta
&nbsp;|&nbsp; Not investment advice. For educational use only.</p>
</body>
</html>
"""


# ═══════════════════════════════════════════════════════════════════════════════
# Public API
# ═══════════════════════════════════════════════════════════════════════════════

def generate_html_report(
    scan_results=None,
    market_env: dict = None,
    watchlist: dict = None,
    positions: dict = None,
    account_size: float = None,
) -> Path:
    """
    Render full HTML report with Jinja2 and save to reports/ directory.
    Returns the output file path.
    """
    try:
        from jinja2 import Environment, BaseLoader
    except ImportError:
        logger.error("Jinja2 not installed. Run: pip install jinja2")
        return None

    REPORTS_DIR.mkdir(parents=True, exist_ok=True)

    # Normalise scan results
    if scan_results is not None and hasattr(scan_results, "to_dict"):
        scan_list = scan_results.to_dict(orient="records")
    elif isinstance(scan_results, list):
        scan_list = scan_results
    else:
        scan_list = []

    context = {
        "date":         datetime.now().strftime("%Y-%m-%d %H:%M"),
        "account_size": account_size or C.ACCOUNT_SIZE,
        "market":       market_env or {},
        "scan_results": scan_list,
        "watchlist":    watchlist or {"A": {}, "B": {}, "C": {}},
        "positions":    positions or {},
    }

    env      = Environment(loader=BaseLoader())
    template = env.from_string(_HTML_TEMPLATE)
    html     = template.render(**context)

    ts       = datetime.now().strftime("%Y%m%d_%H%M")
    out_path = REPORTS_DIR / f"sepa_report_{ts}.html"
    out_path.write_text(html, encoding="utf-8")
    print(f"  ✓ HTML report saved: {out_path}")
    return out_path


def generate_csv(scan_results, filename: str = None) -> Path:
    """
    Export scan results DataFrame to CSV.
    Returns output file path.
    """
    REPORTS_DIR.mkdir(parents=True, exist_ok=True)

    if isinstance(scan_results, list):
        df = pd.DataFrame(scan_results)
    elif isinstance(scan_results, pd.DataFrame):
        df = scan_results
    else:
        print("  No data to export")
        return None

    ts       = datetime.now().strftime("%Y%m%d_%H%M")
    fname    = filename or f"sepa_scan_{ts}.csv"
    out_path = REPORTS_DIR / fname
    df.to_csv(out_path, index=False)
    print(f"  ✓ CSV saved: {out_path}  ({len(df)} rows)")
    return out_path


def print_scan_table(scan_results, top_n: int = 50):
    """
    Print formatted scan results table to terminal.
    """
    try:
        from tabulate import tabulate
    except ImportError:
        # Fallback: plain print
        if isinstance(scan_results, pd.DataFrame):
            print(scan_results.head(top_n).to_string())
        return

    if isinstance(scan_results, list):
        df = pd.DataFrame(scan_results)
    elif isinstance(scan_results, pd.DataFrame):
        df = scan_results.copy()
    else:
        print("  No scan results.")
        return

    if df.empty:
        print("  No stocks passed the SEPA scan.")
        return

    # Select display columns
    display_cols = [c for c in [
        "ticker", "sepa_score", "rs_rank", "vcp_grade", "is_valid_vcp",
        "pivot_price", "price", "tt_pass", "sector"
    ] if c in df.columns]

    subset = df[display_cols].head(top_n).copy()

    # Colour coding based on SEPA score (terminal ANSI)
    rows = []
    for _, row in subset.iterrows():
        score = row.get("sepa_score", 0)
        vcp   = row.get("is_valid_vcp", False)
        tt    = row.get("tt_pass", False)

        colour = _GREEN if (score >= 75 and vcp) else \
                 _CYAN  if score >= 65 else \
                 _YELLOW if score >= 50 else _RESET

        r = []
        for col in display_cols:
            val = row.get(col, "")
            if col == "sepa_score":
                val = f"{colour}{val:.1f}{_RESET}"
            elif col == "pivot_price" and val:
                val = f"${float(val):.2f}"
            elif col == "price" and val:
                val = f"${float(val):.2f}"
            elif col == "rs_rank":
                val = f"{int(val)}" if val else "—"
            elif col == "is_valid_vcp":
                val = "✓" if val else "—"
            elif col == "tt_pass":
                val = f"{_GREEN}✓{_RESET}" if val else f"{_RED}✗{_RESET}"
            r.append(val)
        rows.append(r)

    headers = [c.replace("_", " ").title() for c in display_cols]

    print(f"\n{'═'*78}")
    print(f"{_BOLD}  SEPA SCAN RESULTS  —  {datetime.now().strftime('%Y-%m-%d %H:%M')}{_RESET}")
    print(f"{'─'*78}")
    print(tabulate(rows, headers=headers, tablefmt="plain"))
    print(f"{'═'*78}")
    print(f"  {len(subset)} stocks shown"
          + (f"  (of {len(df)} total)" if len(df) > top_n else ""))
    print()
