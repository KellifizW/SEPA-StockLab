#!/usr/bin/env python
"""Fix format string errors in backtester.py"""

with open('modules/backtester.py', 'r', encoding='utf-8') as f:
    content = f.read()

# Replace the problematic section
old_block = '''        breakout_date  = sig.get("breakout_date") or "─"
        breakout_price = sig.get("breakout_price") or "─"
        g10 = sig.get("gain_10d_pct") or 0.0
        g20 = sig.get("gain_20d_pct") or 0.0
        g60 = sig.get("gain_60d_pct") or 0.0
        max_g = sig.get("max_gain_pct") or 0.0
        outcome = sig.get("outcome", "?")

        # Format each field for table alignment
        row = (
            f"{sig_date} │ {score:3} │{grade} │ T-{t_count} │"
            f"{base_depth:5.1f}% │{base_weeks:5.1f}w │"
            f" {sig_close:7.2f} │{pivot:7.2f} │"
            f"{breakout_date} │{breakout_price:7.2f} │"
            f"{g10:6.1f}% │{g20:6.1f}% │{g60:6.1f}% │"
            f"{max_g:6.1f}% │{outcome}"
        )
        logger.info(row)'''

new_block = '''        breakout_date  = sig.get("breakout_date") or "─"
        breakout_price = sig.get("breakout_price")
        g10 = sig.get("gain_10d_pct") or 0.0
        g20 = sig.get("gain_20d_pct") or 0.0
        g60 = sig.get("gain_60d_pct") or 0.0
        max_g = sig.get("max_gain_pct") or 0.0
        outcome = sig.get("outcome", "?")

        # Safe numeric conversions
        try:
            score_int = int(score) if isinstance(score, (int, float, str)) and score != "?" else 0
        except (ValueError, TypeError):
            score_int = 0
        try:
            sig_close_f = float(sig_close) if sig_close != "?" else 0.0
        except (ValueError, TypeError):
            sig_close_f = 0.0
        try:
            pivot_f = float(pivot) if pivot != "?" else 0.0
        except (ValueError, TypeError):
            pivot_f = 0.0
        try:
            bp_f = float(breakout_price) if breakout_price is not None else None
        except (ValueError, TypeError):
            bp_f = None

        # Format breakout_price_str
        if bp_f is not None:
            bp_str = f"{bp_f:7.2f}"
        else:
            bp_str = "     ─"

        # Format row
        row = (
            f"{sig_date} │ {score_int:3} │{grade} │ T-{t_count} │"
            f"{base_depth:5.1f}% │{base_weeks:5.1f}w │"
            f" {sig_close_f:7.2f} │{pivot_f:7.2f} │"
            f"{breakout_date} │{bp_str} │"
            f"{g10:6.1f}% │{g20:6.1f}% │{g60:6.1f}% │"
            f"{max_g:6.1f}% │{outcome}"
        )
        logger.info(row)'''

if old_block in content:
    content = content.replace(old_block, new_block)
    with open('modules/backtester.py', 'w', encoding='utf-8') as f:
        f.write(content)
    print("✅ backtester.py fixed - format strings safe")
else:
    print("⚠️ Old block not found - checking...")
    if "breakout_price:7.2f" in content:
        print("Found the error location!")
    else:
        print("Already fixed or different format")
