import pandas as pd

df = pd.read_csv('../reports/vcp_profit_space_analysis.csv')

# Extract winners (with breakouts and positive returns)
winners = df[df['equity_end'] > 130].copy()
breakout_cohort = df[df['breakouts'] > 0].copy()

print('=== FULL COHORT ANALYSIS (88 VCP Candidates) ===')
print(f'Total tested: {len(df)}')
print(f'With breakouts: {len(breakout_cohort)} ({len(breakout_cohort)/len(df)*100:.0f}%)')
print(f'Avg signals: {df["signals"].mean():.1f}')
print(f'Avg breakouts: {df["breakouts"].mean():.1f}')
print(f'Avg equity_end: {df["equity_end"].mean():.2f}')
print()

print('=== HIGH-WIN SUBSET (equity > 130, e.g., >30% gain) ===')
print(f'Count: {len(winners)}')
print(f'Avg signals: {winners["signals"].mean():.1f}')
print(f'Avg breakouts: {winners["breakouts"].mean():.1f}')
print(f'Avg max_gain: {winners["avg_max_gain"].mean():.1f}%')
print(f'Avg headroom: {winners["avg_headroom"].mean():.1f}%')
print()

print('=== BREAKOUT COHORT STATS ===')
print(f'Avg signals: {breakout_cohort["signals"].mean():.1f}')
print(f'Avg breakouts: {breakout_cohort["breakouts"].mean():.1f}')
print(f'Avg win rate: {breakout_cohort["win_rate"].mean():.1f}%')
print(f'Avg max_gain: {breakout_cohort["avg_max_gain"].mean():.1f}%')
print(f'Avg capture: {breakout_cohort["capture_ratio"].mean():.1f}%')
print()

print('=== TOP 5 WINNERS ===')
print(winners[['ticker', 'signals', 'breakouts', 'avg_max_gain', 'equity_end']].head().to_string(index=False))
