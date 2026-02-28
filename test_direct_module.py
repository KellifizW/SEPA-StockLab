#!/usr/bin/env python3
import sys
sys.path.insert(0, '.')

# Direct function test
from modules.qm_analyzer import _build_trade_plan, analyze_qm

test_row = {
    "adr": 18.55,
    "close": 6.3,
    "sma_10": 6.11,
    "sma_20": 5.5,
    "low": 6.0,
}

result = _build_trade_plan(stars=4.5, row=test_row)
print("_build_trade_plan() direct call result:")
print(f"  day2_stop value: {result['day2_stop']}")
print(f"  day2_stop type: {type(result['day2_stop'])}")
print(f"  day3plus_stop value: {result['day3plus_stop']}")
print(f"  day3plus_stop type: {type(result['day3plus_stop'])}")

# Now test full analyze_qm
result2 = analyze_qm("ASTI", print_report=False)
tp = result2['trade_plan']
print("\nanalyze_qm() result:")
print(f"  day2_stop value: {tp['day2_stop']}")
print(f"  day2_stop type: {type(tp['day2_stop'])}")
print(f"  day3plus_stop value: {tp['day3plus_stop']}")
print(f"  day3plus_stop type: {type(tp['day3plus_stop'])}")
