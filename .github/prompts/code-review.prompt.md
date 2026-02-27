---
name: 'code-review'
description: 'Review code for SEPA-StockLab conventions, trading logic accuracy, and security'
agent: 'ask'
tools:
  - 'search'
  - 'codebase'
---
# Code Review for SEPA-StockLab

Review the provided code changes against SEPA-StockLab's conventions and trading logic requirements.

## Review Checklist

### 1. Data Integrity
- [ ] Market data accessed ONLY through `data_pipeline.py` — no direct yfinance/finviz/pandas_ta imports
- [ ] Cached data not modified directly — uses module APIs
- [ ] NaN/infinity values handled before JSON serialization (`_clean()`)

### 2. Trading Logic Accuracy
- [ ] Minervini parameter thresholds read from `trader_config.py` (not hardcoded)
- [ ] TT1-TT10 evaluation order preserved in `screener.py`
- [ ] VCP progressive contraction logic intact (each swing < previous)
- [ ] Score weights from `trader_config.py` (`C.W_TREND`, `C.W_FUNDAMENTAL`, etc.)
- [ ] Market regime state transitions follow hierarchy

### 3. Code Style
- [ ] `import trader_config as C` (not `from trader_config import ...`)
- [ ] Private functions use `_leading_underscore`
- [ ] Constants use `UPPER_SNAKE_CASE`
- [ ] External API calls wrapped in try/except with fallback
- [ ] Type hints on new function signatures

### 4. UI/UX
- [ ] Bootstrap 5 dark theme consistency (CSS custom properties from `base.html`)
- [ ] Bilingual text (Traditional Chinese + English)
- [ ] No external JS files — inline in templates

### 5. Security
- [ ] No hardcoded secrets or API keys
- [ ] Input validation on API endpoints
- [ ] No `eval()` or `exec()` on user input

## Output Format
For each issue found, provide:
- **Severity**: Critical / Warning / Suggestion
- **Location**: File and line
- **Issue**: What's wrong
- **Fix**: How to fix it
