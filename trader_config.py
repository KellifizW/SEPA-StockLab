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
FINVIZ_TIMEOUT_SEC    = 600.0      # 10 minutes max (finvizfinance needs ~2 sec per page × 464 pages = 15 min for full scan)
FINVIZ_MAX_PAGES      = 60         # If using pagination limiting (currently unused; finvizfinance loads all pages)
FINVIZ_MIN_TARGET_ROWS = 800       # Minimum rows before accepting results

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
# STAGE 1 SOURCE SELECTION
# ─────────────────────────────────────────────────────────────────────────────
# Controls which data source powers the Stage 1 coarse screener.
# Options:
#   "nasdaq_ftp"  — Free NASDAQ FTP ticker list + yfinance price/vol filter
#                   (~2-4 min, fully free, no API key needed)  [recommended]
#   "finviz"      — finvizfinance (~10-15 min, OTC stocks auto-removed, fewer listed stocks)
STAGE1_SOURCE = "nasdaq_ftp"   # Switch to "finviz" to use finvizfinance

# ─────────────────────────────────────────────────────────────────────────────
# NASDAQ FTP Universe  — used when STAGE1_SOURCE = "nasdaq_ftp"
# ─────────────────────────────────────────────────────────────────────────────
NASDAQ_TICKER_CACHE_DAYS = 1    # Re-download full ticker list every N days
NASDAQ_BATCH_SIZE        = 100  # Tickers per yfinance batch download
NASDAQ_BATCH_SLEEP       = 0.5  # Seconds between batches (be polite to yfinance)

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

# ─────────────────────────────────────────────────────────────────────────────
# QULLAMAGGIE BREAKOUT SWING TRADING — Configuration
# Reference: QullamaggieStockguide.md  (Kristjan Kullamägi methodology)
# Core philosophy: Buy the strongest stocks at the moment of breakout; pure
# technical — no fundamental filtering; ADR has independent veto power.
# ─────────────────────────────────────────────────────────────────────────────

# ── Scanner momentum filters (Section 4) ─────────────────────────────────────
# Three separate scans merged: which % gains over lookback windows
QM_MOMENTUM_1M_MIN_PCT   = 25.0    # ≥25% gain in ~22 trading days (1 month)
QM_MOMENTUM_3M_MIN_PCT   = 50.0    # ≥50% gain in ~67 trading days (3 months)
QM_MOMENTUM_6M_MIN_PCT   = 150.0   # ≥150% gain in ~126 trading days (6 months)

# ── ADR (Average Daily Range) filters (Section 5.3) ──────────────────────────
# ADR = avg of (High/Low - 1) over past 14 days
QM_ADR_PERIOD            = 14      # 14-day ADR calculation window
QM_MIN_ADR_PCT           = 5.0     # Hard veto: <5% → skip regardless of other scores
QM_IDEAL_ADR_PCT         = 8.0     # Ideal: ≥8% for maximum explosive potential
QM_SMALL_ACCT_ADR_PCT    = 8.0     # For accounts < $100K, prefer ≥8%
# ADR star-rating adjustments (Section 6.1 Dimension B)
QM_ADR_BONUS_HIGH        = 15.0    # ADR ≥15% → +1 star bonus
QM_ADR_BONUS_IDEAL       = 8.0     # ADR ≥8% → no adjustment (baseline)
QM_ADR_PENALTY_MARGINAL  = 5.0     # ADR 5-8% → -0.5 star penalty

# ── Dollar volume / liquidity filter ─────────────────────────────────────────
QM_MIN_DOLLAR_VOLUME     = 5_000_000   # Min daily $Volume (Close × Vol); $5M preferred
QM_MIN_DOLLAR_VOLUME_STRICT = 10_000_000  # Strict mode / live trading: $10M

# ── Consolidation / base pattern filters (Section 5.4) ───────────────────────
QM_NEAR_HIGH_PCT         = 15.0    # Price within 15% of 6-day (rolling) high
QM_NEAR_LOW_PCT          = 15.0    # Price within 15% of 6-day (rolling) low
QM_CONSOL_WINDOW_DAYS    = 6       # Rolling window for high/low nearness check
QM_CONSOL_MIN_DAYS       = 3       # Minimum consolidation length (3-15 ideal)
QM_CONSOL_MAX_DAYS       = 60      # Maximum: too long → momentum may dissipate
QM_HIGHER_LOWS_MIN       = 2       # Minimum number of higher lows to confirm pattern
QM_TIGHTNESS_THRESHOLD   = 0.5     # ATR-normalised range tightness (lower = tighter)

# ── Moving average alignment (Section 5.5) ───────────────────────────────────
QM_MA_PERIODS            = [10, 20, 50]    # Key SMAs: 10 (fast), 20 (golden), 50 (slow)
QM_SURFING_MA            = 20              # Primary "surfing" MA for most setups
QM_SURFING_TOLERANCE_PCT = 3.0             # Price can be within 3% of MA to count as surfing
QM_MA_RISING_MIN_DAYS    = 5               # MA must have been rising for at least N days

# ── Volumetric breakout entry (Section 5.6) ──────────────────────────────────
QM_MIN_BREAKOUT_VOL_MULT = 1.5    # Breakout volume must be ≥ 1.5× 20-day avg vol
QM_IDEAL_BREAKOUT_VOL_MULT = 2.5  # Ideal: ≥2.5× average
QM_MAX_ENTRY_ABOVE_BO_PCT = 3.0   # Don't chase: max 3% above breakout pivot

# ── Episodic Pivot (EP) setup parameters (Section 12) ────────────────────────
QM_EP_MIN_GAP_UP_PCT     = 5.0    # Minimum overnight gap-up for EP classification
QM_EP_MAX_GAP_UP_PCT     = 15.0   # Gaps >15% → usually skip (standard rule)
QM_EP_MIN_VOL_MULT       = 3.0    # EP must have ≥3× average volume

# ── Stop-loss rules — 3-phase system (Section 8) ─────────────────────────────
# Phase 1: Day 1 → stop = day's Low-of-Day (LOD)
QM_DAY1_STOP_BELOW_LOD_PCT = 0.5  # Buffer below LOD: stop = LOD × (1 - 0.5%)
# Phase 2: Day 2 → move to break-even (entry price)
QM_DAY2_BREAKEVEN_TRIGGER  = 2    # After N trading days, move to break-even
# Phase 3: Day 3+ → trail on 10 SMA (soft stop: close below 10SMA = warning)
QM_TRAIL_MA_PERIOD         = 10   # Trailing stop MA period (10 SMA)
QM_TRAIL_MA_CLOSE_BELOW_DAYS = 1  # Consecutive closes below trail MA to exit

# ── Profit-taking rules (Section 9) ──────────────────────────────────────────
QM_PROFIT_TAKE_DAY_MIN     = 3    # Start profit taking from Day 3 onwards
QM_PROFIT_TAKE_DAY_MAX     = 5    # Core rule: Day 3-5 take first profits
QM_PROFIT_TAKE_1ST_PCT     = 25.0 # First profit target: sell 25% of position
QM_PROFIT_TAKE_1ST_GAIN    = 10.0 # Or when unrealised gain reaches 10%+ — take 25-50%
QM_PROFIT_TAKE_5STAR_1ST   = 25.0 # 5+ star: only sell 25% first (give more room)
QM_PROFIT_TAKE_5STAR_GAIN  = 20.0 # 5+ star: let it run to 20%+ before selling more

# ── Position sizing by star rating (Section 6.2) ─────────────────────────────
# Values are (min_pct, max_pct) of total account equity
QM_POSITION_SIZING = {
    "5+": (20.0, 25.0),   # 5+ star → 20-25% of account
    "5":  (15.0, 25.0),   # 5 star  → 15-25% of account
    "4":  (10.0, 15.0),   # 4-4.5 star → 10-15%
    "3":  ( 5.0, 10.0),   # 3-3.5 star → ≤10%
    "0":  ( 0.0,  0.0),   # <3 star → 0% (do not trade)
}
QM_MIN_STAR_TO_TRADE       = 3.0  # Do not trade if star rating < 3.0

# ── Star rating dimension weights (Section 6.1) ───────────────────────────────
# Used for automated scoring; base score = 3 stars, each dimension adjusts it
QM_STAR_BASE               = 3.0  # Starting baseline score (stars)
QM_STAR_DIM_A_WEIGHT       = 0.25 # A: Momentum quality weight in final score
QM_STAR_DIM_B_WEIGHT       = 0.20 # B: ADR level (also has veto power)
QM_STAR_DIM_C_WEIGHT       = 0.25 # C: Consolidation quality
QM_STAR_DIM_D_WEIGHT       = 0.15 # D: MA alignment
QM_STAR_DIM_E_WEIGHT       = 0.10 # E: Stock type (institutional vs retail)
QM_STAR_DIM_F_WEIGHT       = 0.05 # F: Market timing / macro environment

# ── QM scan output controls ───────────────────────────────────────────────────
QM_SCAN_TOP_N              = 50   # Max candidates to return from QM scan
QM_SCAN_MIN_STAR           = 3.0  # Minimum star rating to appear in results
QM_SCAN_MIN_DOLLAR_VOL     = 5_000_000  # Min $Volume gate for scan output
QM_SCAN_RESULTS_KEEP       = 30   # Max CSV files to keep per label in scan_results/

# ── QM scan performance tuning ─────────────────────────────────────────────────
# Stage 2: Historical data and batch download optimization
# (Stage 1 remains simple: Price > $5, Volume > 300K, USA only)
QM_STAGE2_MAX_WORKERS      = 12   # Parallel threads for Stage 2 historical enrichment
QM_STAGE2_BATCH_SIZE       = 60   # Tickers per yf.download() batch (increased from 40)
QM_STAGE2_BATCH_SLEEP      = 1.0  # Seconds between batch downloads (reduced from 1.5)

# ── Market environment gate for QM ───────────────────────────────────────────
# QM breakout trades are blocked in confirmed bear markets
QM_BLOCK_IN_BEAR           = True # Block all QM breakout entries when market = DOWNTREND
QM_REDUCE_IN_CORRECTION    = True # Reduce position sizing in MARKET_IN_CORRECTION

# ─────────────────────────────────────────────────────────────────────────────
# QULLAMAGGIE SUPPLEMENT RULES — From live teaching transcripts
# Reference: QullamaggieStockguideMorePart1.md + QullamaggieStockguideMorePart2.md
# ─────────────────────────────────────────────────────────────────────────────

# ── Supplement 1 + 31: ATR-based entry gate and stop distance validation ─────
# ATR (Average True Range) = intraday range only (High - Low), excludes gaps
# This is DIFFERENT from ADR which includes overnight gaps
# Rule: "I usually don't buy the stock if it's up more on the day than its ATR"
# Rule: "The stop shouldn't be higher than the average true range"
# Ideal stop = "usually around half of average true range"
QM_ATR_PERIOD              = 14    # ATR calculation window (intraday H-L only)
QM_ATR_ENTRY_MAX_MULT      = 1.0   # Hard max: if intraday gain > 1.0 × ATR → too late
QM_ATR_ENTRY_IDEAL_MAX_MULT= 0.67  # Ideal entry: intraday gain ≤ 2/3 ATR
QM_ATR_ENTRY_EARLY_MULT    = 0.33  # Early entry: gain ≤ 1/3 ATR (very good)
QM_ATR_STOP_MAX_MULT       = 1.0   # Hard max: stop distance (entry − LOD) must not exceed ATR
QM_ATR_STOP_IDEAL_MULT     = 0.5   # Ideal stop distance = ~half ATR from LOD to entry

# ── Supplement 2: Earnings proximity blackout ─────────────────────────────────
# Rule: "Never buy stocks within 3 days of earnings"
# Even a 4.5-star setup should be passed if earnings are within 3 days
QM_EARNINGS_BLACKOUT_DAYS  = 3     # Skip entry if days_to_earnings ≤ 3; lower star by 1

# ── Supplement 3: Sector momentum amplifier ──────────────────────────────────
# Rule: "In a leading sector a 3.5-star setup counts like a 5-star setup"
# Sector rank = percentile of sector's momentum vs all sectors
QM_SECTOR_LEADER_BONUS     = 1.0   # +1.0 star: sector rank ≥ 90th percentile (top ~2)
QM_SECTOR_STRONG_BONUS     = 0.5   # +0.5 star: sector rank ≥ 70th percentile (top 3)
QM_SECTOR_WEAK_PENALTY     = -0.25 # −0.25 star: sector rank < 30th percentile

# ── Supplement 4 + 33: Extended stock thresholds ─────────────────────────────
# Rule: "When price is ~60% above 10-day MA, I just sold it — didn't even trail"
# Rule: "It's my biggest weakness — letting things ride out"
# Extended means price is too far from 10SMA to safely trail
QM_EXTENDED_SMA10_PCT      = 40.0  # >40% above 10SMA → "EXTENDED" → consider trimming
QM_EXTENDED_SMA10_EXTREME  = 60.0  # >60% above 10SMA → "EXTREME" → sell immediately
QM_EXTENDED_SMA20_PCT      = 25.0  # >25% above 20SMA → flag as extended on slower stocks

# ── Supplement 5: NASDAQ 10/20SMA environment filter ─────────────────────────
# Rule: "NASDAQ is the relevant index — you don't need to look at anything else"
# Rule: "10-day sloping higher + 20-day sloping higher → full power mode"
QM_NASDAQ_PROXY            = "QQQ" # ETF proxy for NASDAQ composite
QM_NASDAQ_BULL_BONUS       = 0.5   # +0.5 Dim F: QQQ 10SMA > 20SMA AND both rising
QM_NASDAQ_BEAR_PENALTY     = -0.5  # −0.5 Dim F: QQQ 10SMA < 20SMA OR MA declining

# ── Supplement 27: MA slope (45° minimum angle rule) ─────────────────────────
# Rule: "The 10, 20, 50-day MAs need to go straight up — at least 45 degrees"
# Rule: "It needs to be at least 45°. But the faster the better."
# Slope measured as % change per bar over lookback window, mapped to angle approx
QM_MA_MIN_SLOPE_PCT        = 0.25  # Min slope: MA must rise ≥ 0.25% per bar (≈45° proxy)
QM_MA_SLOPE_IDEAL_PCT      = 0.45  # Ideal slope: ≥ 0.45% per bar (steeper = better)
QM_MA_SLOPE_LOOKBACK       = 20    # Bars over which to measure MA slope
QM_MA_SLOPE_FAST_BONUS     = 0.5   # +0.5 Dim D: slope > ideal (very steep / fast)
QM_MA_SLOPE_PASS_BONUS     = 0.25  # +0.25 Dim D: slope ≥ minimum (45° equivalent)
QM_MA_SLOPE_SLOW_PENALTY   = -0.25 # −0.25 Dim D: slope between 0 and minimum
QM_MA_SLOPE_DOWN_PENALTY   = -0.75 # −0.75 Dim D: MA pointing down

# ── Supplement 28: 6-star setup (beyond 5-star scale) ────────────────────────
# Rule: "This is a six-star setup on a five-star scale — you don't get many"
# A 6-star requires simultaneously perfect: bounce off 20SMA, extreme tightness,
# 45°+ slope, ≥3 higher lows, and confirmed bull market environment
QM_STAR_MAX                = 6.0   # Allow 6-star rating for perfect setups
QM_SIX_STAR_THRESHOLD      = 5.75  # Raw score floor for 6-star designation
QM_POSITION_SIZING_6STAR_MIN = 25.0  # 6-star minimum position: 25% of account
QM_POSITION_SIZING_6STAR_MAX = 30.0  # 6-star maximum position: 30% of account

# ── Supplement 35: RS rank hard filter (strict screener mode) ────────────────
# Rule: "It needs to be like 90 plus, 95% plus" (for percentage rank filter)
QM_RS_STRICT_MIN_RANK      = 90.0  # Strict mode: RS rank must be ≥ 90th percentile

# ── Supplement 8: Narrow Range Day — K-line quality check ────────────────────
# Rule: "First day of breakout should be a very narrow range — inside bar, NR7, NR4"
# Rule: "If last candle before breakout is wide, it's a bad setup"
QM_NARROW_RANGE_RATIO      = 0.5   # (High-Low)/ATR < 0.5 = narrow range day (good)
QM_WIDE_RANGE_RATIO        = 1.5   # (High-Low)/ATR > 1.5 = wide range day (bad)
QM_NARROW_RANGE_BONUS      = 0.3   # Dim C: +0.3 for single narrow range day
QM_NARROW_SEQ_BONUS        = 0.5   # Dim C: +0.5 for 2+ consecutive narrow range days
QM_WIDE_RANGE_PENALTY      = -0.3  # Dim C: -0.3 for wide range day before breakout

# ── Supplement 9: First Bounce Detection ─────────────────────────────────────
# Rule: "The first pull back to the 20-day is the best pull back to buy — not the 3rd"
# Rule: "First bounces are more powerful than 2nd or 3rd bounces"
QM_FIRST_BOUNCE_20_BONUS   = 0.5   # Dim D: +0.5 for confirmed first bounce off 20SMA
QM_FIRST_BOUNCE_10_BONUS   = 0.3   # Dim D: +0.3 for confirmed first bounce off 10SMA
QM_FIRST_BOUNCE_50_BONUS   = 0.2   # Dim D: +0.2 for confirmed first bounce off 50SMA
QM_BOUNCE_TOUCH_PCT        = 1.5   # % distance to consider price "touching" a MA

# ── Supplement 14: Price below 50SMA hard penalty ────────────────────────────
# Rule: "I simply don't buy stocks that are below the 50-day moving average"
# Rule: "The 50-day is not absolute but it filters 99% of bad setups"
QM_BELOW_50SMA_PENALTY     = -1.5  # Dim D: -1.5 if price below 50SMA
QM_BELOW_50SMA_DECLINING   = -0.5  # Additional -0.5 if 50SMA itself is declining

# ── Supplement 16: Sub-setup label (3.0-3.5 star) ────────────────────────────
# Rule: "When I see a 3-star, I give it a tiny position — it's a sub-setup"
# Rule: "It's not your best, but it's still worth watching with less size"
QM_SUB_SETUP_MIN_STARS     = 3.0   # Stars ≥ 3.0 and ≤ 3.5 → sub-setup flag
QM_SUB_SETUP_MAX_STARS     = 3.5
QM_SUB_SETUP_POSITION_MAX  = 5.0   # Max position % for sub-setup: 5% (half normal)

# ── Supplement 20: Rocket Fuel — extreme earnings growth ─────────────────────
# Rule: "When I see +100% earnings AND +100% revenue — that's rocket fuel"
# Rule: "I look for hyper-growth stocks. 100% EPS growth changes the story"
QM_ROCKET_FUEL_EPS_MIN     = 100.0 # EPS growth ≥ 100% YoY → rocket fuel threshold
QM_ROCKET_FUEL_REV_MIN     = 100.0 # Revenue growth ≥ 100% YoY → rocket fuel
QM_ROCKET_FUEL_BONUS       = 0.25  # Dim A: +0.25 for true rocket fuel (both criteria)

# ── Supplement 26: Follow-through detection ──────────────────────────────────
# Rule: "After day 1 breakout, I want to see follow-through — higher prices next day"
# Rule: "If stock closes up next day too, that confirms the move"
QM_FOLLOW_THROUGH_MIN_DAYS = 2     # Min consecutive higher-close days to confirm FT
QM_FOLLOW_THROUGH_LOOKBACK = 3     # Days to look back for follow-through signal

# ── Supplement 30: Close strength signal (alternative entry) ─────────────────
# Rule: "If stock closes in the top 10% of its range, that's a very strong close"
# Rule: "A strong close near HOD is better than closing in middle of range"
QM_CLOSE_STRENGTH_STRONG   = 0.90  # Close in top 10% of range = strong close signal
QM_CLOSE_STRENGTH_WEAK     = 0.40  # Close in bottom 40% = weak close signal

# ── Supplement 34: Compression energy score ──────────────────────────────────
# Rule: "The longer it consolidates without breaking, the more energy builds"
# Rule: "A tight 6-week base with higher lows = charged spring"
QM_COMPRESSION_BONUS_THRESH = 50.0 # compression_score threshold for bonus
QM_COMPRESSION_BONUS        = 0.25 # Dim C: +0.25 for high compression score

# ── Supplement 12: Green-to-Red stop ─────────────────────────────────────────
# Rule: "If a stock opens above prior close (gap up) and then goes RED — sell immediately"
# Rule: "A green-to-red is almost always a sign of distribution"
QM_GREEN_TO_RED_STOP       = True  # Enable G2R stop monitoring

# ── Supplement 15 + 22: Stopped-out / Revenge trade warning ──────────────────
# Rule: "Do not re-enter a stock you just got stopped out of on same day or next few days"
# Rule: "Revenge trading is your worst enemy — wait for a proper re-setup"
QM_REVENGE_TRADE_LOOKBACK  = 7     # Days back to check for prior stop-out on same ticker

# ── Supplement 7: Scan result count warning ──────────────────────────────────
# Rule: "If I'm getting hundreds of setups, my criteria are too loose"
# Rule: "In a good market there should be manageable number of setups"
QM_SCAN_MAX_RESULTS_WARN   = 50    # Warn if scan produces more than 50 results


# ─────────────────────────────────────────────────────────────────────────────
# MARTIN LUK (ML) — Systematic Swing Trading Configuration
# Reference: MartinLukStockGuidePart1.md + MartinLukStockGuidePart2.md
# Core philosophy: Pullback buying on EMA structure; tight stops (<2.5%);
# formula-based position sizing (risk% / stop%); 22% win rate offset by
# large R:R (20-30R winners); AVWAP as primary S/R indicator.
# ─────────────────────────────────────────────────────────────────────────────

# ── EMA structure (Chapter 5, 12) ────────────────────────────────────────────
# Martin uses EMA (not SMA) — faster reaction to price changes
ML_EMA_PERIODS           = [9, 21, 50, 150]   # Core EMAs: 9 (fast), 21 (primary), 50 (slow), 150 (trend)
ML_EMA_MIN_SLOPE_PCT     = 0.20    # Min slope for rising EMA (% per bar)
ML_EMA_SLOPE_IDEAL_PCT   = 0.40    # Ideal slope for strong uptrend

# ── Pullback classification (Chapter 5.3-5.5) ────────────────────────────────
# Primary: Pullback to 21 EMA is the "golden zone"
# Hierarchy: 9 EMA (strongest) > 21 EMA (primary) > 50 EMA (deepest acceptable)
ML_PULLBACK_TOLERANCE_PCT = 3.0    # Price within 3% of EMA counts as "at EMA"
ML_EXTENDED_EMA21_PCT     = 20.0   # >20% above 21 EMA = too extended, skip
ML_EXTENDED_EMA9_PCT      = 15.0   # >15% above 9 EMA = very extended

# ── Anchored VWAP (Chapter 6) ────────────────────────────────────────────────
# Martin's core indicator: AVWAP from swing high = overhead supply
# AVWAP from swing low = dynamic support; price reclaiming AVWAP = bullish
ML_AVWAP_SWING_LOOKBACK      = 5      # Bars on each side for swing detection
ML_AVWAP_SEARCH_BARS         = 120    # How far back to search for anchor points (~6M)
ML_AVWAP_RECLAIM_CONFIRM_BARS = 2     # Bars of close above AVWAP to confirm reclaim

# ── Risk management (Chapter 4) ──────────────────────────────────────────────
# 🔴 Hard Rules — never violated
ML_MAX_STOP_LOSS_PCT      = 2.5    # Absolute max stop: 2.5% from entry
ML_IDEAL_STOP_LOSS_PCT    = 1.5    # Ideal stop: 1.0-1.5% (tight)
ML_RISK_PER_TRADE_PCT     = 0.50   # Risk 0.50% of account per trade
ML_MAX_RISK_PER_TRADE_PCT = 0.75   # Hard ceiling: never risk > 0.75% per trade
# Position sizing: shares = (account × risk%) / (entry - stop)
# Example: $100K × 0.5% = $500 risk; entry $50, stop $49 → 500 shares

# ── Account management (Chapter 4.3) ─────────────────────────────────────────
ML_MAX_OPEN_POSITIONS       = 6      # Max open positions at once
ML_MAX_PORTFOLIO_HEAT_PCT   = 3.0    # Total open risk ≤ 3% of account
ML_MAX_SINGLE_POSITION_PCT  = 25.0   # No single position > 25% of account
ML_MIN_POSITION_SIZE_USD    = 1000   # Minimum position size to be meaningful

# ── Scanner momentum filters (Chapter 5.1) ───────────────────────────────────
# Martin scans for "stocks that have already moved" — proven leaders
ML_MOMENTUM_3M_MIN_PCT   = 30.0    # ≥30% gain in 3 months (proven mover)
ML_MOMENTUM_6M_MIN_PCT   = 80.0    # ≥80% gain in 6 months (very strong)
ML_MIN_PRICE              = 5.0     # Minimum price (avoid penny stocks)
ML_MIN_AVG_VOLUME         = 300_000 # Min 20-day avg volume (liquidity)
ML_MIN_DOLLAR_VOLUME      = 5_000_000  # Min daily $ volume ($5M)

# ── ADR / ATR filters ────────────────────────────────────────────────────────
ML_ADR_PERIOD             = 14      # 14-day ADR window
ML_MIN_ADR_PCT            = 4.0     # 4%+ ADR for swing trading
ML_IDEAL_ADR_PCT          = 7.0     # 7%+ ADR is ideal

# ── Volume analysis (Chapter 5.6, 7) ─────────────────────────────────────────
ML_VOLUME_DRY_UP_RATIO     = 0.50   # Volume < 50% of 20-day avg = dry-up (bullish on PB)
ML_VOLUME_SURGE_MULT       = 1.5    # Volume > 1.5× avg on bounce = confirmation
ML_IDEAL_VOLUME_SURGE_MULT = 2.0    # 2× avg on bounce = strong confirmation

# ── Intraday entry rules (Chapter 7) ─────────────────────────────────────────
# Martin uses 1-min chart for precise entries
ML_ENTRY_CONFIRM_1MIN      = True   # Require 1-min chart hammer/engulf at EMA
ML_MAX_CHASE_ABOVE_EMA_PCT = 1.5    # Don't chase > 1.5% above target EMA
ML_LOD_STOP_BUFFER_PCT     = 0.3    # Stop = LOD - 0.3% buffer

# ── Sell rules (Chapter 8) ───────────────────────────────────────────────────
# Martin's 3R/5R partial sell system + 9 EMA trailing
ML_PARTIAL_SELL_1_R       = 3.0     # First partial: sell 15% at 3R profit
ML_PARTIAL_SELL_1_PCT     = 15.0    # Sell 15% of position at 3R
ML_PARTIAL_SELL_2_R       = 5.0     # Second partial: sell 15% at 5R profit
ML_PARTIAL_SELL_2_PCT     = 15.0    # Sell 15% of position at 5R
ML_TRAIL_EMA              = 9       # Trail remaining 70% on 9 EMA (daily close)
ML_TRAIL_EMA_CLOSE_BELOW_DAYS = 1   # Close below 9 EMA → sell all on next day
ML_SELL_INTO_STRENGTH      = True   # Sell into strength, not weakness (Chapter 8.2)

# ── Weekly chart strategic rules (Chapter 12) ────────────────────────────────
ML_WEEKLY_EMA_PERIODS     = [10, 40]  # Weekly 10 EMA ≈ daily 50, Weekly 40 ≈ daily 200
ML_WEEKLY_UPTREND_CHECK   = True      # Require weekly chart in uptrend for entry

# ── Setup classification (Chapter 5, Part 2 patterns) ────────────────────────
# Martin's primary setups — scored in ml_setup_detector.py
# PB_EMA   : Pullback to rising EMA with volume dry-up → bounce
# BR_RETEST: Breakout then retest of breakout level + AVWAP confluence
# BREAKOUT : Classic breakout above resistance on volume
# EP       : Episodic Pivot — gap up on catalyst (earnings, news)
# CHAR_CHG : Character Change — stock emerging from Stage 1 to Stage 2
# PARABOLIC: Parabolic move → risky, only for experienced (Chapter 8.5)
ML_SETUP_CONFIDENCE_MIN   = 0.40    # Min confidence to classify a setup type

# ── Pullback Buy Quality Scorecard (Chapter 5.7) ─────────────────────────────
# 7 dimensions for scoring pullback quality (replaces QM's star system)
# Score: 0-5 stars (not 6 like QM — Martin keeps it simpler)
ML_STAR_BASE              = 2.5     # Starting baseline score
ML_STAR_MAX               = 5.0     # Maximum star rating

# Dimension weights (must sum to ~1.0)
ML_DIM_A_WEIGHT           = 0.20    # A: EMA Structure (stacking + rising)
ML_DIM_B_WEIGHT           = 0.20    # B: Pullback Quality (depth + volume dry-up)
ML_DIM_C_WEIGHT           = 0.15    # C: AVWAP Confluence (support/reclaim)
ML_DIM_D_WEIGHT           = 0.15    # D: Volume Pattern (dry-up on PB, surge on bounce)
ML_DIM_E_WEIGHT           = 0.15    # E: Risk/Reward (stop distance + R:R ratio)
ML_DIM_F_WEIGHT           = 0.10    # F: Relative Strength (vs market)
ML_DIM_G_WEIGHT           = 0.05    # G: Market Environment

# ── Scan output controls ─────────────────────────────────────────────────────
ML_SCAN_TOP_N             = 40      # Max candidates from ML scan
ML_SCAN_MIN_STAR          = 2.5     # Minimum star to appear in results
ML_SCAN_MIN_DOLLAR_VOL    = 5_000_000  # $Volume gate for output
ML_SCAN_RESULTS_KEEP      = 30      # Max CSV files to keep in scan_results/

# ── Scan performance tuning ──────────────────────────────────────────────────
ML_STAGE2_MAX_WORKERS     = 12      # Parallel threads for Stage 2
ML_STAGE2_BATCH_SIZE      = 60      # Tickers per yf.download() batch
ML_STAGE2_BATCH_SLEEP     = 1.0     # Seconds between batch downloads

# ── Market environment gate ──────────────────────────────────────────────────
# Martin reduces activity in corrections but doesn't fully block
# (22% win rate means most trades lose anyway — survival mode in bear)
ML_BLOCK_IN_BEAR          = True    # Block all entries in DOWNTREND
ML_REDUCE_IN_CORRECTION   = True    # Reduce sizing in CORRECTION
ML_CORRECTION_SIZE_MULT   = 0.50    # Cut position size by 50% in correction

# ── Martin Luk specific: December drawdown awareness (Chapter 10) ────────────
# Martin had a -11.3% drawdown in Dec 2024; his rule: after 3 consecutive
# losing trades, reduce size by 50%; after 5, go to paper trading
ML_CONSECUTIVE_LOSS_REDUCE = 3      # After N consecutive losses → reduce size 50%
ML_CONSECUTIVE_LOSS_PAUSE  = 5      # After N consecutive losses → paper trade only

# ── SEPA 5-Pillar scoring weights for ML strategy ────────────────────────────
# Martin's system is 95% technical, but still acknowledges:
# "I won't buy a stock if the company is going bankrupt next week"
ML_W_TREND          = 0.35   # Trend (EMA structure) — highest weight
ML_W_PULLBACK       = 0.25   # Pullback quality + AVWAP
ML_W_VOLUME         = 0.15   # Volume pattern (dry-up + surge)
ML_W_RISK_REWARD    = 0.15   # Risk/Reward quality
ML_W_MARKET         = 0.10   # Market environment