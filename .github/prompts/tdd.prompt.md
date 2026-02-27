---
name: 'tdd'
description: 'Test-driven development: write failing test → implement → refactor'
agent: 'agent'
tools:
  - 'search'
  - 'codebase'
  - 'editFiles'
  - 'createFile'
  - 'terminalCommand'
---
# TDD Workflow for SEPA-StockLab

Follow strict Test-Driven Development: RED → GREEN → REFACTOR.

## Setup
Tests use `pytest`. Test files go in `tests/` directory (create if not exists).
Test file naming: `test_{module_name}.py`

## Phase 1: RED — Write Failing Test
1. Create a test file for the target module
2. Write a test that describes the expected behavior
3. Run the test to confirm it fails: `python -m pytest tests/test_{module}.py -v`

## Phase 2: GREEN — Minimal Implementation
1. Write the minimum code to make the test pass
2. Run the test again to confirm it passes
3. Do NOT add extra functionality beyond what the test requires

## Phase 3: REFACTOR — Clean Up
1. Remove duplication
2. Add type hints
3. Ensure code follows project conventions (see `.github/copilot-instructions.md`)
4. Run all tests to confirm nothing broke: `python -m pytest tests/ -v`

## Testing Guidelines for SEPA-StockLab
- **Mock external APIs** — Never call yfinance/finvizfinance in tests. Use `unittest.mock.patch`
- **Test algorithmic logic** — Focus on:
  - TT1-TT10 validation (`screener.py`)
  - SEPA 5-pillar scoring (`screener.py`)
  - VCP detection logic (`vcp_detector.py`)
  - RS ranking calculation (`rs_ranking.py`)
  - Position health checks (`position_monitor.py`)
- **Use fixtures** — Create sample DataFrames with known values for predictable assertions
- **Test edge cases** — NaN values, empty DataFrames, missing columns, zero volume

## Example Test Structure
```python
import pytest
import pandas as pd
from unittest.mock import patch
from modules.screener import check_trend_template

class TestTrendTemplate:
    def test_tt1_price_above_sma50(self):
        """TT1: Price must be above SMA50"""
        # Arrange: create DataFrame where price > SMA50
        # Act: run check_trend_template
        # Assert: TT1 passes
        pass

    def test_tt1_fails_when_price_below_sma50(self):
        """TT1 should fail when price < SMA50"""
        pass
```
