"""
tests/test_exit_engine.py
─────────────────────────
Unit tests for the automated Exit Engine.

Tests cover:
  • check_all_positions dispatches correctly by pool
  • ML action interpretation (STOP_HIT, SELL_ALL, TAKE_PARTIAL_3R/5R)
  • QM action interpretation (STOP_HIT, SELL_ALL, SELL_IMMEDIATELY, TAKE_PARTIAL_PROFIT)
  • Time stop detection (held too long with no gain)
  • Climax top detection (parabolic / extreme extension)
  • _extract_star_rating from note string
  • _build_exit_reason formatting
  • Max sells per cycle cap
  • Dry-run mode (no actual sell orders placed)
  • Stop update when recommended_stop > current_stop
"""

import sys
import pytest
from datetime import date, timedelta
from pathlib import Path
from unittest.mock import patch, MagicMock, call

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import trader_config as C


# ─────────────────────────────────────────────────────────────────────────────
# Fixtures
# ─────────────────────────────────────────────────────────────────────────────

def _pos(pool="ML", buy_price=100, shares=50, stop_loss=92, days_ago=3, note="Auto-ML 4.5★"):
    return {
        "buy_price": buy_price,
        "shares": shares,
        "stop_loss": stop_loss,
        "buy_date": (date.today() - timedelta(days=days_ago)).isoformat(),
        "note": note,
        "pool": pool,
        "original_shares": shares,
        "partial_sells": [],
        "partial_sell_count": 0,
    }


def _health_hold(close=110, gain_pct=10.0, r_multiple=1.2, recommended_stop=95):
    return {
        "primary_action": "HOLD",
        "stop_triggered": False,
        "recommended_stop": recommended_stop,
        "close": close,
        "gain_pct": gain_pct,
        "r_multiple": r_multiple,
        "current_phase": 3,
        "signals": [],
        "profit_action": {},
    }


def _health_stop_hit(close=88):
    return {
        "primary_action": "STOP_HIT",
        "stop_triggered": True,
        "recommended_stop": 0,
        "close": close,
        "gain_pct": -12.0,
        "r_multiple": -1.5,
        "current_phase": 2,
        "signals": [{"type": "STOP_HIT", "severity": "critical",
                     "msg_zh": "止損觸發", "msg_en": "Stop hit"}],
        "profit_action": {},
    }


def _health_partial_3r(close=130, shares_to_sell=8):
    return {
        "primary_action": "TAKE_PARTIAL_3R",
        "stop_triggered": False,
        "recommended_stop": 105,
        "close": close,
        "gain_pct": 30.0,
        "r_multiple": 3.2,
        "current_phase": 3,
        "signals": [{"type": "TAKE_PARTIAL_3R", "severity": "info",
                     "msg_zh": "3R部分止盈", "msg_en": "3R partial profit"}],
        "profit_action": {
            "action": "TAKE_PARTIAL_3R",
            "shares_to_sell": shares_to_sell,
            "reason_zh": "3R 部分止盈 15%",
        },
    }


def _health_sell_all(close=135, action="SELL_ALL"):
    return {
        "primary_action": action,
        "stop_triggered": True,
        "recommended_stop": 0,
        "close": close,
        "gain_pct": 35.0,
        "r_multiple": 4.0,
        "current_phase": 3,
        "signals": [{"type": action, "severity": "critical",
                     "msg_zh": "全部出場", "msg_en": "Sell all"}],
        "profit_action": {},
    }


def _data_with(positions: dict):
    return {"positions": positions, "closed": [], "account_high": 100_000}


# ─────────────────────────────────────────────────────────────────────────────
# _extract_star_rating
# ─────────────────────────────────────────────────────────────────────────────

def test_extract_star_rating_basic():
    from modules.exit_engine import _extract_star_rating
    assert _extract_star_rating("Auto-QM 4.5★") == 4.5
    assert _extract_star_rating("Auto-ML 3★ breakout") == 3.0
    assert _extract_star_rating("no rating here") == 3.0  # default


def test_extract_star_rating_decimal():
    from modules.exit_engine import _extract_star_rating
    assert _extract_star_rating("5.5★") == 5.5
    assert _extract_star_rating("2.0★") == 2.0


# ─────────────────────────────────────────────────────────────────────────────
# _check_time_stop
# ─────────────────────────────────────────────────────────────────────────────

def test_time_stop_triggers():
    from modules.exit_engine import _check_time_stop
    old_date = (date.today() - timedelta(days=7)).isoformat()
    result = _check_time_stop(old_date, gain_pct=0.5)
    assert result is not None
    assert result["type"] == "TIME_STOP"


def test_time_stop_no_trigger_good_gain():
    from modules.exit_engine import _check_time_stop
    old_date = (date.today() - timedelta(days=7)).isoformat()
    result = _check_time_stop(old_date, gain_pct=5.0)
    assert result is None


def test_time_stop_no_trigger_too_early():
    from modules.exit_engine import _check_time_stop
    recent = (date.today() - timedelta(days=2)).isoformat()
    result = _check_time_stop(recent, gain_pct=0.0)
    assert result is None


def test_time_stop_invalid_date():
    from modules.exit_engine import _check_time_stop
    result = _check_time_stop("bad-date", gain_pct=0.0)
    assert result is None


# ─────────────────────────────────────────────────────────────────────────────
# _check_climax
# ─────────────────────────────────────────────────────────────────────────────

def test_climax_parabolic():
    from modules.exit_engine import _check_climax
    health = {"profit_action": {"action": "SELL_ALL_PARABOLIC", "reason_zh": "拋物線加速"}}
    result = _check_climax(health)
    assert result is not None
    assert result["type"] == "CLIMAX_TOP"


def test_climax_extreme_extension():
    from modules.exit_engine import _check_climax
    health = {
        "profit_action": {},
        "extended": {"status": "EXTREME", "pct_above_sma": 55, "warning_zh": "超買"},
    }
    result = _check_climax(health)
    assert result is not None
    assert result["type"] == "CLIMAX_EXTENDED"


def test_climax_normal():
    from modules.exit_engine import _check_climax
    health = {"profit_action": {}, "extended": {"status": "OK"}}
    result = _check_climax(health)
    assert result is None


# ─────────────────────────────────────────────────────────────────────────────
# _build_exit_reason
# ─────────────────────────────────────────────────────────────────────────────

def test_build_exit_reason():
    from modules.exit_engine import _build_exit_reason
    signals = [{"msg_zh": "止損觸發"}, {"msg_en": "Below 10SMA"}]
    health = {"r_multiple": 2.5}
    reason = _build_exit_reason("STOP_HIT", signals, health)
    assert "STOP_HIT" in reason
    assert "止損觸發" in reason
    assert "R=2.5" in reason


# ─────────────────────────────────────────────────────────────────────────────
# check_all_positions — ML STOP_HIT
# ─────────────────────────────────────────────────────────────────────────────

@patch("modules.exit_engine._update_position_stop")
@patch("modules.exit_engine._execute_exit", return_value="FULL_EXIT")
@patch("modules.exit_engine._check_ml", return_value=_health_stop_hit())
@patch("modules.position_monitor._load", return_value=_data_with({"AAPL": _pos("ML")}))
def test_ml_stop_hit_full_exit(mock_load, mock_ml, mock_exec, mock_upd):
    from modules.exit_engine import check_all_positions
    results = check_all_positions(dry_run=False)
    assert len(results) == 1
    r = results[0]
    assert r["ticker"] == "AAPL"
    assert r["primary_action"] == "STOP_HIT"
    assert r["action_taken"] == "FULL_EXIT"
    mock_exec.assert_called_once()


# ─────────────────────────────────────────────────────────────────────────────
# check_all_positions — ML HOLD (no exit)
# ─────────────────────────────────────────────────────────────────────────────

@patch("modules.exit_engine._update_position_stop")
@patch("modules.exit_engine._check_ml", return_value=_health_hold())
@patch("modules.position_monitor._load", return_value=_data_with({"TSLA": _pos("ML")}))
def test_ml_hold_no_exit(mock_load, mock_ml, mock_upd):
    from modules.exit_engine import check_all_positions
    results = check_all_positions(dry_run=True)
    assert len(results) == 1
    assert results[0]["action_taken"] == "HOLD"


# ─────────────────────────────────────────────────────────────────────────────
# check_all_positions — ML partial 3R
# ─────────────────────────────────────────────────────────────────────────────

@patch("modules.exit_engine._update_position_stop")
@patch("modules.exit_engine._execute_exit", return_value="DRY_RUN")
@patch("modules.exit_engine._check_ml", return_value=_health_partial_3r(shares_to_sell=8))
@patch("modules.position_monitor._load", return_value=_data_with({"NVDA": _pos("ML", shares=50)}))
def test_ml_partial_3r(mock_load, mock_ml, mock_exec, mock_upd):
    from modules.exit_engine import check_all_positions
    results = check_all_positions(dry_run=True)
    assert len(results) == 1
    r = results[0]
    assert r["primary_action"] == "TAKE_PARTIAL_3R"
    assert r["exit_type"] == "PARTIAL"
    assert r["shares_sold"] == 8


# ─────────────────────────────────────────────────────────────────────────────
# check_all_positions — QM SELL_IMMEDIATELY
# ─────────────────────────────────────────────────────────────────────────────

@patch("modules.exit_engine._update_position_stop")
@patch("modules.exit_engine._execute_exit", return_value="FULL_EXIT")
@patch("modules.exit_engine._check_qm", return_value=_health_sell_all(action="SELL_IMMEDIATELY"))
@patch("modules.position_monitor._load", return_value=_data_with({"AMZN": _pos("QM")}))
def test_qm_sell_immediately(mock_load, mock_qm, mock_exec, mock_upd):
    from modules.exit_engine import check_all_positions
    results = check_all_positions(dry_run=True)
    assert len(results) == 1
    r = results[0]
    assert r["primary_action"] == "SELL_IMMEDIATELY"
    assert r["action_taken"] == "FULL_EXIT"


# ─────────────────────────────────────────────────────────────────────────────
# check_all_positions — time stop override
# ─────────────────────────────────────────────────────────────────────────────

@patch("modules.exit_engine._update_position_stop")
@patch("modules.exit_engine._execute_exit", return_value="DRY_RUN")
@patch("modules.exit_engine._check_ml")
@patch("modules.position_monitor._load")
def test_time_stop_overrides_hold(mock_load, mock_ml, mock_exec, mock_upd):
    # Position held 10 days with only 0.2% gain
    pos = _pos("ML", days_ago=10)
    mock_load.return_value = _data_with({"SLOW": pos})
    mock_ml.return_value = _health_hold(close=100.2, gain_pct=0.2, r_multiple=0.02)

    from modules.exit_engine import check_all_positions
    results = check_all_positions(dry_run=True)
    assert len(results) == 1
    r = results[0]
    assert r["primary_action"] == "TIME_STOP"


# ─────────────────────────────────────────────────────────────────────────────
# check_all_positions — empty positions
# ─────────────────────────────────────────────────────────────────────────────

@patch("modules.position_monitor._load", return_value=_data_with({}))
def test_empty_positions(mock_load):
    from modules.exit_engine import check_all_positions
    results = check_all_positions(dry_run=True)
    assert results == []


# ─────────────────────────────────────────────────────────────────────────────
# Max sells per cycle cap
# ─────────────────────────────────────────────────────────────────────────────

@patch("modules.exit_engine._update_position_stop")
@patch("modules.exit_engine._execute_exit", return_value="FULL_EXIT")
@patch("modules.exit_engine._check_ml", return_value=_health_stop_hit())
@patch("modules.position_monitor._load")
def test_max_sells_cap(mock_load, mock_ml, mock_exec, mock_upd):
    # 5 positions all hitting stop, but cap is 3
    positions = {f"TICK{i}": _pos("ML") for i in range(5)}
    mock_load.return_value = _data_with(positions)

    from modules.exit_engine import check_all_positions
    original_max = C.EXIT_MAX_SELLS_PER_CYCLE
    C.EXIT_MAX_SELLS_PER_CYCLE = 3
    try:
        results = check_all_positions(dry_run=True)
        exits = [r for r in results if r.get("action_taken") != "HOLD"]
        assert len(exits) <= 3
    finally:
        C.EXIT_MAX_SELLS_PER_CYCLE = original_max


# ─────────────────────────────────────────────────────────────────────────────
# Stop update when higher recommended
# ─────────────────────────────────────────────────────────────────────────────

@patch("modules.exit_engine._update_position_stop")
@patch("modules.exit_engine._check_ml", return_value=_health_hold(recommended_stop=96))
@patch("modules.position_monitor._load", return_value=_data_with({"MSFT": _pos("ML", stop_loss=92)}))
def test_stop_updated_when_higher(mock_load, mock_ml, mock_upd):
    from modules.exit_engine import check_all_positions
    results = check_all_positions(dry_run=True)
    assert len(results) == 1
    # Should have called _update_position_stop since 96 > 92
    mock_upd.assert_called_once_with("MSFT", 96)


@patch("modules.exit_engine._update_position_stop")
@patch("modules.exit_engine._check_ml", return_value=_health_hold(recommended_stop=90))
@patch("modules.position_monitor._load", return_value=_data_with({"MSFT": _pos("ML", stop_loss=92)}))
def test_stop_not_lowered(mock_load, mock_ml, mock_upd):
    from modules.exit_engine import check_all_positions
    results = check_all_positions(dry_run=True)
    # Should NOT lower the stop from 92 to 90
    mock_upd.assert_not_called()


# ─────────────────────────────────────────────────────────────────────────────
# get_exit_status
# ─────────────────────────────────────────────────────────────────────────────

def test_get_exit_status():
    from modules.exit_engine import get_exit_status
    status = get_exit_status()
    assert "last_check_at" in status
    assert "positions_checked" in status
    assert "results" in status


# ─────────────────────────────────────────────────────────────────────────────
# reset_daily_counters
# ─────────────────────────────────────────────────────────────────────────────

def test_reset_daily_counters():
    from modules import exit_engine
    exit_engine._exit_status["total_exits_today"] = 5
    exit_engine.reset_daily_counters()
    assert exit_engine._exit_status["total_exits_today"] == 0
