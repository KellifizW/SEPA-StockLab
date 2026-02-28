import sys; sys.path.insert(0, '..')
from modules import backtester

print('=== Testing CIEN with NEW VCP params (min_vcp_score=20, 120d) ===')
r = backtester.run_backtest('CIEN', min_vcp_score=20, outcome_days=120)

if r.get('ok'):
    s = r.get('summary', {})
    sigs = len(r.get('signals', []))
    eq = r.get('equity_curve', [])
    eq_end = eq[-1]['value'] if eq else 100.0
    
    print(f'Status: OK')
    print(f'Signals: {s.get("total_signals", 0)} (raw)')
    print(f'Breakouts: {s.get("breakouts", 0)}')
    print(f'Win rate: {s.get("win_rate_pct", "N/A")}%')
    print(f'Equity: ${eq_end:.2f}')
    print()
    print('Signal details (first 3):')
    for i, sig in enumerate(r.get('signals', [])[:3]):
        print(f"  {i+1}. Date {sig.get('signal_date', 'N/A')}: VCP={sig.get('vcp_score')}, outcome={sig.get('outcome')}, exit_gain={sig.get('exit_gain_pct')}%")
else:
    print(f'ERROR: {r.get("error")}')
