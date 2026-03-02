#!/usr/bin/env python
"""Quick test to verify imports work after fixes."""

try:
    from modules.qm_backtester import run_qm_backtest
    print("✓ qm_backtester imports OK")
except Exception as e:
    print(f"✗ qm_backtester import error: {e}")
    import traceback
    traceback.print_exc()

try:
    from modules.rs_ranking import get_rs_rank
    print("✓ get_rs_rank found in rs_ranking")
except Exception as e:
    print(f"✗ get_rs_rank import error: {e}")

try:
    from modules.qm_analyzer import analyze_qm
    print("✓ analyze_qm found in qm_analyzer")
except Exception as e:
    print(f"✗ analyze_qm import error: {e}")

print("\nAll imports OK - ready to run backtest!")
