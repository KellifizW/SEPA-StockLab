"""
trader_config.py  ─  Minervini SEPA System Configuration
All Minervini parameters live here. Edit this file to tune the system.
"""

# ─────────────────────────────────────────────────────────────────────────────
# ACCOUNT & RISK PARAMETERS
# ─────────────────────────────────────────────────────────────────────────────
ACCOUNT_SIZE = 100_000          # Total account equity (USD)
MAX_RISK_PER_TRADE_PCT = 1.5    # Max loss per trade as % of account (1-2%)
MAX_POSITION_SIZE_PCT = 20.0    # Max single position as % of account
MAX_OPEN_POSITIONS = 6          # Concentrated portfolio (4-8 ideal)
MIN_RISK_REWARD = 2.0           # Minimum R:R ratio to consider a trade
IDEAL_RISK_REWARD = 3.0         # Ideal R:R ratio

# ─────────────────────────────────────────────────────────────────────────────
# STOP LOSS RULES  (Minervini: max 7-8%, ideal 5-6%)
# ─────────────────────────────────────────────────────────────────────────────
MAX_STOP_LOSS_PCT   = 8.0       # Absolute maximum stop (%) from entry
IDEAL_STOP_LOSS_PCT = 5.5       # Ideal stop target
QUICK_EXIT_PCT      = 3.5       # Fast exit if multiple negative signals appear
ATR_STOP_MULTIPLIER = 2.0       # Stop = entry - (ATR_14 × multiplier)

# ─────────────────────────────────────────────────────────────────────────────
# TREND TEMPLATE PARAMETERS  (Minervini TT1-TT10)
# ─────────────────────────────────────────────────────────────────────────────
TT_SMA_PERIODS = [50, 150, 200]     # SMA periods used in trend template

# TT7: price must be at least this % above 52-week low
TT7_MIN_ABOVE_52W_LOW_PCT = 25.0

# TT8: price must be within this % below 52-week high
TT8_MAX_BELOW_52W_HIGH_PCT = 25.0   # Standard: ≤25%; Ideal: ≤15%
TT8_IDEAL_BELOW_52W_HIGH_PCT = 15.0

# TT4: SMA200 must have been rising for this many trading days (≈1 month)
TT4_SMA200_RISING_DAYS = 22

# TT9: Relative Strength rating minimum (0-99 percentile)
TT9_MIN_RS_RANK = 70    # Standard minimum
TT9_IDEAL_RS_RANK = 80  # Ideal

# ─────────────────────────────────────────────────────────────────────────────
# FUNDAMENTAL FILTER THRESHOLDS  (Minervini F1-F9)
# ─────────────────────────────────────────────────────────────────────────────
# F1: Recent quarter EPS growth (year-over-year)
F1_MIN_EPS_QOQ_GROWTH = 25.0        # %

# F3: Annual EPS growth
F3_MIN_EPS_ANNUAL_GROWTH = 25.0     # %

# F5: Revenue/Sales growth
F5_MIN_SALES_GROWTH = 20.0          # %

# F8: Return on Equity
F8_MIN_ROE = 17.0                   # %

# Coarse screener ROE (less strict for initial filter)
COARSE_MIN_ROE = 8.0    # LOOSENED: Reduced from 10% to allow more diverse fundamentals
                        # Backtests show VCP-timing can overcome below-target ROE via breakout capture

# Minimum stock price for liquidity
MIN_STOCK_PRICE = 10.0

# Minimum average daily volume
MIN_AVG_VOLUME = 200_000

# ─────────────────────────────────────────────────────────────────────────────
# ENTRY RULES  (Minervini D1-D11)
# ─────────────────────────────────────────────────────────────────────────────
MAX_CHASEUP_PCT   = 5.0    # D2: Don't buy if price > pivot + 5%
MIN_BREAKOUT_VOL_MULT = 1.5  # D3: Min breakout volume vs 50-day average (150%)
IDEAL_BREAKOUT_VOL_MULT = 2.0  # D4: Ideal 200%+

# ─────────────────────────────────────────────────────────────────────────────
# VCP DETECTION PARAMETERS
# ─────────────────────────────────────────────────────────────────────────────
VCP_MIN_BASE_WEEKS   = 4        # Minimum base width in weeks
VCP_MIN_BASE_WEEKS_IDEAL = 6    # Ideal minimum
VCP_MAX_BASE_WEEKS   = 65       # Maximum base width (~15 months)
VCP_MIN_CONTRACTIONS = 1        # LOOSENED: Allow T-1+ (simple pullback base) to capture more candidates
                                # Backtests show relaxing from 2→1 finds more high-gain stocks (CIEN, AAMI)
VCP_MAX_BASE_DEPTH   = 45.0     # LOOSENED: Increased from 40% to allow slightly deeper bases
VCP_MIN_BASE_DEPTH   = 8.0      # LOOSENED: Decreased from 10% to allow shallower consolidations
VCP_FINAL_CONTRACTION_MAX = 12.0  # LOOSENED: Increased from 10% to 12% to catch more final breakouts
VCP_VOLUME_DRY_THRESHOLD = 0.55   # Slightly relaxed from 0.50 (still requires final vol drying)

# ─────────────────────────────────────────────────────────────────────────────
# RS RANKING PARAMETERS
# ─────────────────────────────────────────────────────────────────────────────
# Weighted performance periods (IBD-style, heavier on recent performance)
RS_WEIGHTS = {
    "3m": 0.40,   # 3-month return
    "6m": 0.20,   # 6-month return
    "9m": 0.20,   # 9-month return
    "12m": 0.20,  # 12-month return
}
RS_UNIVERSE_MIN_PRICE  = 5.0       # Min price for RS universe stocks
RS_UNIVERSE_MIN_VOLUME = 100_000   # Min avg volume for RS universe
RS_CACHE_DAYS = 1                  # How many days before refreshing RS cache
RS_BATCH_SIZE = 200                # Tickers per yfinance batch download (larger = fewer batches)
RS_BATCH_SLEEP = 0.5               # Seconds between RS batch downloads
RS_PARALLEL_BATCHES = 3            # Number of concurrent RS batch downloads

# ─────────────────────────────────────────────────────────────────────────────
# SCAN OUTPUT QUALITY GATE
# ─────────────────────────────────────────────────────────────────────────────
SCAN_MIN_SCORE = 50.0              # Minimum total SEPA score to include in results
SCAN_TOP_N     = 100               # Max stocks to return (top N by score)

# ─────────────────────────────────────────────────────────────────────────────
# SCAN PERFORMANCE TUNING
# ─────────────────────────────────────────────────────────────────────────────
STAGE2_MAX_WORKERS    = 16         # Parallel threads for Stage 2 TT validation
STAGE2_BATCH_SIZE     = 50         # Tickers per yf.download() batch in Stage 2
STAGE2_BATCH_SLEEP    = 1.5        # Sleep between Stage 2 download batches (sec)
STAGE3_MAX_WORKERS    = 6          # Parallel threads for Stage 3 SEPA scoring
FUNDAMENTALS_CACHE_DAYS = 1        # How many days before re-fetching fundamentals
FINVIZ_CACHE_TTL_HOURS  = 4        # Cache finviz screener results for N hours

# ─────────────────────────────────────────────────────────────────────────────
# ACCOUNT DRAWDOWN ALERT LEVELS  (Minervini H1-H5)
# ─────────────────────────────────────────────────────────────────────────────
DRAWDOWN_LEVELS = [
    (3.0,  "tighten_stops"),     # H2: Tighten stops, no new positions
    (5.0,  "reduce_half"),       # H3: Reduce positions to 50%
    (7.0,  "minimal_exposure"),  # H4: Keep only 1-2 best positions
    (10.0, "full_exit"),         # H5: Full exit, stop trading, review
]

# ─────────────────────────────────────────────────────────────────────────────
# MARKET ENVIRONMENT - INDEX ETFs
# ─────────────────────────────────────────────────────────────────────────────
MARKET_INDICES = {
    "SPY":  "S&P 500",
    "QQQ":  "NASDAQ 100",
    "IWM":  "Russell 2000",
    "DIA":  "Dow Jones",
}
DISTRIBUTION_DAY_DROP_PCT = 0.2    # Day must fall >0.2% on higher volume
DISTRIBUTION_DAYS_WINDOW  = 25     # Look back this many trading days
EXCESS_DISTRIBUTION_DAYS  = 5      # ≥5 distribution days = market under pressure

# ─────────────────────────────────────────────────────────────────────────────
# PROFIT-TAKING RULES  (Minervini I1-I5)
# ─────────────────────────────────────────────────────────────────────────────
TRAILING_STOP_TABLE = [
    # (min_profit_pct, max_allowed_pullback_pct_from_high)
    # NOTE: The 5% tier is handled specially in backtester._measure_outcome() as a TRUE
    # break-even stop (exit at ENTRY PRICE, not at current_max).  The value here is kept
    # only as a placeholder so the table length stays consistent.
    (5.0,  0.0),     # I1: Move stop to BREAK-EVEN (entry price) at 5-10% profit
                     #     Implemented in code as: stop_price = max(stop, breakout_price)
                     #     NOT as current_max × (1-0%) which would mean "exit at all-time high"
    (10.0, 10.0),    # I2: Allow 10% pullback from high at 10-15% profit
                     #     Widened from 7%: 23-basket sweep shows 10% gives $200 vs $172 at 7%;
                     #     lets the stock consolidate a normal 1-2 week pause without stopping out
    (15.0, 10.0),    # I3: Allow 10% pullback from high at 15-20% profit
    (20.0, 15.0),    # I4: Allow 15% pullback from high at 20-30%+ profit
]
BREAKEVEN_TRIGGER_PCT = 5.0   # At this profit %, stop moves to entry price (break-even)
                               # Minervini I1: "never let a winner turn into a loser"
BACKTEST_OUTCOME_DAYS = 120   # Default outcome window for backtests
                               # Extended from 90: 20-stock sweep shows 120d gives +55% more equity
                               # vs 90d by allowing slower-moving leaders to fully develop

QUICK_PROFIT_WEEKS     = 3     # If target reached in fewer weeks
QUICK_PROFIT_TARGET_PCT = 15.0  # Take 50% off at this profit in QUICK_PROFIT_WEEKS weeks
TIME_STOP_WEEKS_FLAT   = 4     # Time stop: sell if flat after this many weeks
                               # Increased 3→4: Minervini describes 3-4 weeks as the range;
                               # testing shows 3 weeks prematurely exits valid Stage 2 leaders
TIME_STOP_WEEKS_MIN    = 6     # Time stop: sell if <2% gain after this weeks
                               # Increased 5→6: gives slow-breakout leadership stocks one extra week
TREND_RIDER_MIN_GAIN_PCT = 10.0  # Trend-Rider mode: if unrealized gain >= this %, skip ALL time
                                 # stops and let the winner run. Minervini: "never cut a leader"
                                 # on a technicality. Time stops are for stocks doing nothing.
                                 # Set to 10.0 to align with TRAILING_STOP_TABLE tier-2 threshold.

# ─────────────────────────────────────────────────────────────────────────────
# REPORTING
# ─────────────────────────────────────────────────────────────────────────────
REPORTS_DIR   = "reports"
DATA_DIR      = "data"
PRICE_CACHE_DIR = "data/price_cache"

# ─────────────────────────────────────────────────────────────────────────────
# DATABASE  (DuckDB historical storage)
# ─────────────────────────────────────────────────────────────────────────────
DB_FILE = "data/sepa_stock.duckdb"      # DuckDB historical store location
DB_ENABLED = True                        # Set False to disable all DB writes
DB_JSON_BACKUP_ENABLED = True            # Dual-write to JSON for safety (Phase 2)
DB_JSON_BACKUP_DIR = "data/db_backups"  # Backup JSON location if DuckDB fails

# ─────────────────────────────────────────────────────────────────────────────
# SEPA SCORING WEIGHTS  (relative importance of each pillar)
# ─────────────────────────────────────────────────────────────────────────────
SEPA_WEIGHTS = {
    "trend":       0.30,
    "fundamental": 0.25,
    "catalyst":    0.15,
    "entry":       0.20,
    "risk_reward": 0.10,
}
