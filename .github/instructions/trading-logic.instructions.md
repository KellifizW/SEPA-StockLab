---
name: 'Trading Logic Rules'
description: 'Minervini SEPA methodology rules for trading modules'
applyTo: 'modules/**'
---
# Trading Logic Rules for SEPA-StockLab Modules

## Absolute Rules
- **NEVER change Minervini parameter thresholds** without explicit user instruction
- **ALWAYS read parameters from `trader_config.py`** (imported as `C`) — never hardcode thresholds
- **ALWAYS preserve TT1-TT10 evaluation order** in `screener.py`
- **ALWAYS maintain progressive contraction logic** in VCP detection (each swing < previous)

## Trend Template (TT1-TT10) in screener.py
The 10 criteria must be evaluated in order. Key thresholds come from `trader_config.py`:
- `C.TT_SMA_PERIODS` = [50, 150, 200]
- `C.TT7_MIN_ABOVE_52W_LOW_PCT` = 25.0
- `C.TT8_MAX_BELOW_52W_HIGH_PCT` = 25.0
- `C.TT4_SMA200_RISING_DAYS` = 22
- `C.TT9_MIN_RS_RANK` = 70

## SEPA 5-Pillar Scoring in screener.py
Score weights must use `C.W_TREND`, `C.W_FUNDAMENTAL`, etc. from trader_config.
When modifying scoring logic, verify that:
1. All 5 pillar scores sum to 100%
2. Individual pillar scores are clamped 0-100
3. Bonus points follow existing patterns

## VCP Detection in vcp_detector.py
Must verify:
- Minimum `C.VCP_MIN_CONTRACTIONS` contractions (T-count)
- Each successive contraction range is smaller than the previous
- Volume decreases toward the pivot point
- Base width is within `C.VCP_MIN_BASE_WEEKS` to `C.VCP_MAX_BASE_WEEKS`

## Data Pipeline in data_pipeline.py
This is the ONLY module that may import yfinance, finvizfinance, pandas_ta.
All other modules must call data_pipeline functions for:
- Price data: `get_price_data(ticker, period)`
- Fundamentals: `get_fundamentals(ticker)`
- Technical indicators: computed internally using pandas_ta

## Market Environment in market_env.py
Regime states must follow this hierarchy:
CONFIRMED_UPTREND → UPTREND_UNDER_PRESSURE → MARKET_IN_CORRECTION → DOWNTREND
Never skip a state transition.
