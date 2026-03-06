"""
tests/test_position_controller.py
─────────────────────────────────
Unit tests for the 3-pool Position Control engine.

Tests cover:
  • Pool status snapshot with empty / populated positions
  • can_allocate gate — acceptance & rejection (count, heat, drawdown)
  • adjusted_position_size — normal, halved, stopped
  • get_pool_for_strategy mapping
  • Backward compat: missing pool field defaults to FREE
"""

import sys
import json
import pytest
from pathlib import Path
from unittest.mock import patch, MagicMock

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import trader_config as C


# ─────────────────────────────────────────────────────────────────────────────
# Fixtures
# ─────────────────────────────────────────────────────────────────────────────

def _make_position(buy_price=100, shares=50, stop_loss=92, pool="ML", **kw):
    """Create a minimal position dict."""
    pos = {
        "buy_price": buy_price,
        "shares": shares,
        "stop_loss": stop_loss,
        "stop_pct": round((buy_price - stop_loss) / buy_price * 100, 2),
        "target": round(buy_price + (buy_price - stop_loss) * 2, 2),
        "rr": 2.0,
        "risk_dollar": round((buy_price - stop_loss) * shares, 2),
        "buy_date": "2025-01-15",
        "days_held": 3,
        "max_price": buy_price,
        "note": "test",
        "pool": pool,
        "original_shares": shares,
        "partial_sells": [],
        "partial_sell_count": 0,
    }
    pos.update(kw)
    return pos


def _empty_data():
    return {"positions": {}, "closed": [], "account_high": 100_000}


def _data_with_positions(positions_dict, closed=None, account_high=100_000):
    return {
        "positions": positions_dict,
        "closed": closed or [],
        "account_high": account_high,
    }


# ─────────────────────────────────────────────────────────────────────────────
# get_pool_for_strategy
# ─────────────────────────────────────────────────────────────────────────────

def test_pool_for_strategy_ml():
    from modules.position_controller import get_pool_for_strategy
    assert get_pool_for_strategy("ML") == "ML"
    assert get_pool_for_strategy("ml") == "ML"


def test_pool_for_strategy_qm():
    from modules.position_controller import get_pool_for_strategy
    assert get_pool_for_strategy("QM") == "QM"


def test_pool_for_strategy_other():
    from modules.position_controller import get_pool_for_strategy
    assert get_pool_for_strategy("SEPA") == "FREE"
    assert get_pool_for_strategy("") == "FREE"
    assert get_pool_for_strategy("manual") == "FREE"


# ─────────────────────────────────────────────────────────────────────────────
# get_pool_status — empty
# ─────────────────────────────────────────────────────────────────────────────

@patch("modules.position_monitor.get_positions_by_pool",
       return_value={"ML": {}, "QM": {}, "FREE": {}})
@patch("modules.position_monitor._load", return_value=_empty_data())
def test_pool_status_empty(mock_load, mock_grouped):
    from modules.position_controller import get_pool_status
    s = get_pool_status(account_size=100_000)

    assert s["total_positions"] == 0
    assert s["total_heat_pct"] == 0
    assert s["total_used_pct"] == 0
    assert s["drawdown_pct"] == 0
    assert s["loss_streak"] == 0
    assert s["size_multiplier"] == 1.0

    for pool_name in ("ML", "QM", "FREE"):
        ps = s["pools"][pool_name]
        assert ps["positions"] == 0
        assert ps["used_pct"] == 0
        assert ps["heat_pct"] == 0


# ─────────────────────────────────────────────────────────────────────────────
# get_pool_status — with positions
# ─────────────────────────────────────────────────────────────────────────────

@patch("modules.position_monitor.get_positions_by_pool")
@patch("modules.position_monitor._load")
def test_pool_status_with_positions(mock_load, mock_grouped):
    positions = {
        "AAPL": _make_position(buy_price=150, shares=100, stop_loss=140, pool="ML"),
        "TSLA": _make_position(buy_price=200, shares=50, stop_loss=180, pool="QM"),
    }
    mock_load.return_value = _data_with_positions(positions)
    mock_grouped.return_value = {
        "ML":   {"AAPL": positions["AAPL"]},
        "QM":   {"TSLA": positions["TSLA"]},
        "FREE": {},
    }

    from modules.position_controller import get_pool_status
    s = get_pool_status(account_size=100_000)

    assert s["total_positions"] == 2
    # ML: AAPL = 150*100 = 15000 → 15% used, risk = (150-140)*100 = 1000 → 1% heat
    assert s["pools"]["ML"]["used_pct"] == 15.0
    assert s["pools"]["ML"]["heat_pct"] == 1.0
    # QM: TSLA = 200*50 = 10000 → 10% used, risk = (200-180)*50 = 1000 → 1% heat
    assert s["pools"]["QM"]["used_pct"] == 10.0
    assert s["pools"]["QM"]["heat_pct"] == 1.0

    assert s["total_heat_pct"] == 2.0


# ─────────────────────────────────────────────────────────────────────────────
# can_allocate — accepted
# ─────────────────────────────────────────────────────────────────────────────

@patch("modules.position_controller.get_pool_status")
def test_can_allocate_ok(mock_status):
    mock_status.return_value = {
        "account_size": 100_000,
        "total_positions": 1,
        "total_used_pct": 10.0,
        "total_heat_pct": 1.0,
        "pools": {
            "ML": {"positions": 1, "max_positions": 4, "used_pct": 10.0,
                    "cap_pct": 40, "heat_pct": 0.5, "max_heat": 2.0, "tickers": ["AAPL"]},
            "QM": {"positions": 0, "max_positions": 4, "used_pct": 0,
                    "cap_pct": 40, "heat_pct": 0, "max_heat": 3.0, "tickers": []},
            "FREE": {"positions": 0, "max_positions": 2, "used_pct": 0,
                      "cap_pct": 20, "heat_pct": 0, "max_heat": 1.5, "tickers": []},
        },
        "drawdown_pct": 0.0,
        "loss_streak": 0,
        "size_multiplier": 1.0,
    }

    from modules.position_controller import can_allocate
    result = can_allocate("ML", entry_price=100, shares=50, stop_price=92, account_size=100_000)

    assert result["allowed"] is True
    assert result["reason"] == "OK"


# ─────────────────────────────────────────────────────────────────────────────
# can_allocate — blocked by pool position count
# ─────────────────────────────────────────────────────────────────────────────

@patch("modules.position_controller.get_pool_status")
def test_can_allocate_blocked_max_positions(mock_status):
    mock_status.return_value = {
        "account_size": 100_000,
        "total_positions": 4,
        "total_used_pct": 30.0,
        "total_heat_pct": 2.0,
        "pools": {
            "ML": {"positions": 4, "max_positions": 4, "used_pct": 30.0,
                    "cap_pct": 40, "heat_pct": 1.8, "max_heat": 2.0, "tickers": ["A", "B", "C", "D"]},
            "QM": {"positions": 0, "max_positions": 4, "used_pct": 0,
                    "cap_pct": 40, "heat_pct": 0, "max_heat": 3.0, "tickers": []},
            "FREE": {"positions": 0, "max_positions": 2, "used_pct": 0,
                      "cap_pct": 20, "heat_pct": 0, "max_heat": 1.5, "tickers": []},
        },
        "drawdown_pct": 0.0,
        "loss_streak": 0,
        "size_multiplier": 1.0,
    }

    from modules.position_controller import can_allocate
    result = can_allocate("ML", entry_price=100, shares=50, stop_price=92)

    assert result["allowed"] is False
    assert "ML倉持倉" in result["reason"]


# ─────────────────────────────────────────────────────────────────────────────
# can_allocate — blocked by total heat
# ─────────────────────────────────────────────────────────────────────────────

@patch("modules.position_controller.get_pool_status")
def test_can_allocate_blocked_total_heat(mock_status):
    mock_status.return_value = {
        "account_size": 100_000,
        "total_positions": 2,
        "total_used_pct": 20.0,
        "total_heat_pct": 4.5,
        "pools": {
            "ML": {"positions": 1, "max_positions": 4, "used_pct": 10.0,
                    "cap_pct": 40, "heat_pct": 1.0, "max_heat": 2.0, "tickers": ["AAPL"]},
            "QM": {"positions": 1, "max_positions": 4, "used_pct": 10.0,
                    "cap_pct": 40, "heat_pct": 1.0, "max_heat": 3.0, "tickers": ["TSLA"]},
            "FREE": {"positions": 0, "max_positions": 2, "used_pct": 0,
                      "cap_pct": 20, "heat_pct": 0, "max_heat": 1.5, "tickers": []},
        },
        "drawdown_pct": 0.0,
        "loss_streak": 0,
        "size_multiplier": 1.0,
    }

    from modules.position_controller import can_allocate
    # This would add 1% heat → total = 5.5% > 5.0%
    result = can_allocate("QM", entry_price=100, shares=100, stop_price=90)

    assert result["allowed"] is False
    assert "全帳戶風險" in result["reason"]


# ─────────────────────────────────────────────────────────────────────────────
# can_allocate — blocked by drawdown hard stop
# ─────────────────────────────────────────────────────────────────────────────

@patch("modules.position_controller.get_pool_status")
def test_can_allocate_blocked_drawdown(mock_status):
    mock_status.return_value = {
        "account_size": 100_000,
        "total_positions": 0,
        "total_used_pct": 0,
        "total_heat_pct": 0,
        "pools": {
            "ML":   {"positions": 0, "max_positions": 4, "used_pct": 0, "cap_pct": 40, "heat_pct": 0, "max_heat": 2.0, "tickers": []},
            "QM":   {"positions": 0, "max_positions": 4, "used_pct": 0, "cap_pct": 40, "heat_pct": 0, "max_heat": 3.0, "tickers": []},
            "FREE": {"positions": 0, "max_positions": 2, "used_pct": 0, "cap_pct": 20, "heat_pct": 0, "max_heat": 1.5, "tickers": []},
        },
        "drawdown_pct": 12.0,
        "loss_streak": 0,
        "size_multiplier": 0.0,
    }

    from modules.position_controller import can_allocate
    result = can_allocate("ML", entry_price=100, shares=50, stop_price=92)

    assert result["allowed"] is False
    assert "回撤" in result["reason"]


# ─────────────────────────────────────────────────────────────────────────────
# adjusted_position_size
# ─────────────────────────────────────────────────────────────────────────────

@patch("modules.position_controller.get_pool_status")
def test_adjusted_size_normal(mock_status):
    mock_status.return_value = {
        "account_size": 100_000,
        "total_positions": 0,
        "total_used_pct": 0,
        "total_heat_pct": 0,
        "pools": {"ML": {}, "QM": {}, "FREE": {}},
        "drawdown_pct": 0.0,
        "loss_streak": 0,
        "size_multiplier": 1.0,
    }

    from modules.position_controller import adjusted_position_size
    result = adjusted_position_size(100)
    assert result["shares"] == 100
    assert result["multiplier"] == 1.0


@patch("modules.position_controller.get_pool_status")
def test_adjusted_size_halved(mock_status):
    mock_status.return_value = {
        "account_size": 100_000,
        "total_positions": 0,
        "total_used_pct": 0,
        "total_heat_pct": 0,
        "pools": {"ML": {}, "QM": {}, "FREE": {}},
        "drawdown_pct": 6.0,
        "loss_streak": 3,
        "size_multiplier": 0.5,
    }

    from modules.position_controller import adjusted_position_size
    result = adjusted_position_size(100)
    assert result["shares"] == 50
    assert result["multiplier"] == 0.5


@patch("modules.position_controller.get_pool_status")
def test_adjusted_size_stopped(mock_status):
    mock_status.return_value = {
        "account_size": 100_000,
        "total_positions": 0,
        "total_used_pct": 0,
        "total_heat_pct": 0,
        "pools": {"ML": {}, "QM": {}, "FREE": {}},
        "drawdown_pct": 11.0,
        "loss_streak": 6,
        "size_multiplier": 0.0,
    }

    from modules.position_controller import adjusted_position_size
    result = adjusted_position_size(100)
    assert result["shares"] == 0
    assert result["multiplier"] == 0.0


# ─────────────────────────────────────────────────────────────────────────────
# Internal helpers
# ─────────────────────────────────────────────────────────────────────────────

def test_calc_drawdown_pct():
    from modules.position_controller import _calc_drawdown_pct
    # hwm = 100k, current = 100k → 0%
    assert _calc_drawdown_pct({"account_high": 100_000}, 100_000) == 0.0
    # hwm = 100k, current = 90k → 10%
    assert _calc_drawdown_pct({"account_high": 100_000}, 90_000) == 10.0
    # hwm = 0 → safe
    assert _calc_drawdown_pct({"account_high": 0}, 50_000) == 0.0


def test_calc_loss_streak():
    from modules.position_controller import _calc_loss_streak
    # No closed positions
    assert _calc_loss_streak({"closed": []}) == 0
    # 3 consecutive losses at the end
    closed = [
        {"pnl_pct": 5.0},
        {"pnl_pct": -2.0},
        {"pnl_pct": -1.5},
        {"pnl_pct": -3.0},
    ]
    assert _calc_loss_streak({"closed": closed}) == 3
    # Last trade is a win → streak = 0
    closed_win = [{"pnl_pct": -2.0}, {"pnl_pct": 1.0}]
    assert _calc_loss_streak({"closed": closed_win}) == 0


def test_size_multiplier():
    from modules.position_controller import _size_multiplier
    assert _size_multiplier(0, 0) == 1.0
    assert _size_multiplier(5.0, 0) == 0.5  # drawdown reduce
    assert _size_multiplier(0, 3) == 0.5    # loss streak halve
    assert _size_multiplier(10.0, 0) == 0.0  # drawdown stop
    assert _size_multiplier(0, 5) == 0.0     # loss streak stop
    assert _size_multiplier(12.0, 6) == 0.0  # both


# ─────────────────────────────────────────────────────────────────────────────
# Backward compat: _load fills missing pool fields
# ─────────────────────────────────────────────────────────────────────────────

def test_load_backward_compat(tmp_path):
    """Old positions without pool field should default to FREE."""
    positions_file = tmp_path / "positions.json"
    old_data = {
        "positions": {
            "NVDA": {
                "buy_price": 500,
                "shares": 20,
                "stop_loss": 460,
                "stop_pct": 8.0,
                "target": 580,
                "rr": 2.0,
                "risk_dollar": 800,
                "buy_date": "2025-01-01",
                "days_held": 10,
                "max_price": 520,
                "note": "manual buy",
            }
        },
        "closed": [],
        "account_high": 100_000,
    }
    positions_file.write_text(json.dumps(old_data), encoding="utf-8")

    # Patch POSITIONS_FILE to our temp file
    with patch("modules.position_monitor.POSITIONS_FILE", positions_file):
        from modules.position_monitor import _load
        data = _load()

    pos = data["positions"]["NVDA"]
    assert pos["pool"] == "FREE"
    assert pos["original_shares"] == 20
    assert pos["partial_sells"] == []
    assert pos["partial_sell_count"] == 0
