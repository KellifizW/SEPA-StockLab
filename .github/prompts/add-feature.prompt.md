---
name: 'add-feature'
description: 'Plan and implement a new feature following SEPA-StockLab conventions'
agent: 'agent'
tools:
  - 'search'
  - 'codebase'
  - 'editFiles'
  - 'createFile'
  - 'terminalCommand'
---
# Add New Feature

You are implementing a new feature for SEPA-StockLab. Follow the project's established patterns.

## Planning Phase
1. **Identify affected modules** — Which of the 9 modules under `modules/` need changes?
2. **Check data flow** — Does this feature need new data? If so, it MUST go through `data_pipeline.py`.
3. **Check trader_config.py** — Are new parameters needed? Follow `UPPER_SNAKE_CASE` with inline comments.
4. **Plan the UI** — Web UI changes go in `templates/` (Bootstrap 5 dark theme, bilingual text). CLI changes go in `minervini.py`.

## Implementation Rules
- Import `trader_config as C` — never hardcode thresholds
- Wrap external API calls in try/except with fallbacks
- Use `_clean()` before JSON responses to sanitize NaN/DataFrame values
- For stateful features, use `_load()` / `_save()` pattern with JSON in `data/`
- For long-running tasks, use background thread pattern:
  ```python
  POST /api/{feature}/run   → starts thread, returns {"job_id": jid}
  GET  /api/{feature}/status/<jid>  → polls status
  ```

## Checklist Before Completion
- [ ] New parameters in `trader_config.py` with inline comments
- [ ] Data access only through `data_pipeline.py`
- [ ] Error handling with try/except on all external calls
- [ ] Bilingual user-facing text (Traditional Chinese + English)
- [ ] JSON responses sanitized with `_clean()`
- [ ] CLI subcommand added to `minervini.py` (if applicable)
- [ ] Web API route added to `app.py` (if applicable)
