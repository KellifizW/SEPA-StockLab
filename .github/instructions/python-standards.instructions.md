---
name: 'Python Standards'
description: 'Python coding conventions for SEPA-StockLab modules'
applyTo: '**/*.py'
---
# Python Coding Standards for SEPA-StockLab

## Import Order
1. Standard library (`sys`, `os`, `json`, `pathlib`, `datetime`, `threading`)
2. Third-party (`pandas`, `numpy`, `flask`, `yfinance`)
3. Project modules (`trader_config as C`, `modules.*`)

## trader_config Import
Always import as `C` and access parameters as `C.PARAM_NAME`:
```python
import trader_config as C
# Good:  C.TT9_MIN_RS_RANK
# Bad:   from trader_config import TT9_MIN_RS_RANK
```

## Function Naming
- Private: `_leading_underscore` (e.g., `_clean()`, `_load()`, `_save()`)
- Public API: `snake_case` (e.g., `run_scan()`, `detect_vcp()`)
- Constants: `UPPER_SNAKE_CASE`

## Type Hints
Add return type annotations to all new functions:
```python
def run_scan(refresh_rs: bool = False) -> pd.DataFrame: ...
def _clean(obj: Any) -> Any: ...
```

## Error Handling
Wrap all external API calls (yfinance, finvizfinance) in try/except with graceful fallback.
Never let an API failure crash the entire scan — log the error and continue.

## Data Access Rule
**Never import yfinance, finvizfinance, or pandas_ta directly.**
Always use `modules/data_pipeline.py` functions for market data access.

## Docstrings
Use a triple-quoted docstring for all public functions. Include:
- Brief description
- Minervini reference (TT, F, D codes) if relevant
- Parameter and return type descriptions

## NaN / Infinity Handling
Before JSON serialization, always sanitize with `_clean()`:
- `numpy.nan` → `None`
- `numpy.inf` → `None`
- `pd.DataFrame` → `dict` or `list`
