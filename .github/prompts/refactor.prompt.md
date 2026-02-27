---
name: 'refactor'
description: 'Refactor code — focus on DRY, type hints, and known technical debt'
agent: 'agent'
tools:
  - 'search'
  - 'codebase'
  - 'editFiles'
  - 'createFile'
---
# Refactor SEPA-StockLab Code

Refactor the specified code following project conventions. Focus on reducing technical debt.

## Known Technical Debt (Priority Order)

### 1. ANSI Color Constants (DRY violation)
`_GREEN`, `_RED`, `_BOLD`, `_RESET` are copy-pasted in 6+ modules.
**Fix:** Extract to `modules/utils.py`:
```python
# modules/utils.py
_GREEN = "\033[92m"
_RED   = "\033[91m"
_YELLOW = "\033[93m"
_BOLD  = "\033[1m"
_RESET = "\033[0m"
```
Then `from modules.utils import _GREEN, _RED, _BOLD, _RESET` in each module.

### 2. ROOT Path Boilerplate (DRY violation)
Every module repeats:
```python
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
```
**Fix:** Add to `modules/__init__.py` or convert to proper package with `pyproject.toml`.

### 3. Missing Type Annotations
Add return types to all public functions. Priority:
- `screener.py`: `run_scan() -> pd.DataFrame`
- `vcp_detector.py`: `detect_vcp() -> dict`
- `stock_analyzer.py`: `analyze() -> dict`
- `data_pipeline.py`: all public functions

### 4. Thread Safety
`_finviz_cache` in `data_pipeline.py` needs `threading.Lock`.

### 5. Inline HTML in report.py
Move HTML template string to `templates/report_export.html` Jinja2 template.

## Refactoring Rules
- **Never change behavior** — refactoring must be behavior-preserving
- **One concern at a time** — don't mix DRY fixes with feature changes
- **Maintain imports** — keep `import trader_config as C` convention
- **Test after refactor** — if tests exist, run them. If not, manually verify affected features.
