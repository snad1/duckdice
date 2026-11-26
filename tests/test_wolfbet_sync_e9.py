"""Duckdice CLI lane money split (CLI v1.11.0).

Mirrors WolfBet v2.36.0 (`lane_split`, `6174b96`). Most of it shipped in
1.10.0 and is tested in test_wolfbet_sync_e5_lanes.py (modes, override kept,
round-to-0, save keeps the full bet, basebet_pct divided, live reload,
config-set / config-show, chain warning). This file adds the duckdice adapt:
a split bet below the minimum learned from a 422 (state.min_bet) stays full
(logged), for base_bet and basebet_pct. With no minimum learned yet (0) the
split divides as before. Reference test: wolfbet/tests/test_phase10_lanes.py.

Run: APP_ENV=testing .venv-test/bin/python -m pytest duckdice/tests -q
"""

import json
import logging
from pathlib import Path

import pytest

import duckdice as dd

LEARNED_MIN = 0.001


@pytest.fixture
def saved_config():
    data = {"api_key": "k" * 40, "currency": "doge", "strategy": "Flat Bet",
            "strategy_key": "1", "base_bet": 0.003, "multiplier": 2.0,
            "bet_value": 49.5, "max_loss": None, "faucet": False}
    Path(dd.CONFIG_PATH).write_text(json.dumps(data))   # 0.003 / 4 = 0.00075
    return Path(dd.CONFIG_PATH)


def _write_lanes(path, base_bet=None, **lanes):
    data = json.loads(Path(path).read_text())
    data["lanes"] = lanes
    if base_bet is not None:
        data["base_bet"] = base_bet
    Path(path).write_text(json.dumps(data))


def _fresh_load(lane=1, min_bet=0.0):
    dd.state = dd.BotState()
    dd.state.lane = lane
    dd.state.min_bet = min_bet
    assert dd.load_config()
    return dd.state


# ── base_bet split vs the learned minimum ──────────────────────────────

def test_no_learned_minimum_divides(saved_config, caplog):
    _write_lanes(saved_config, count=4, split="bet")
    with caplog.at_level(logging.WARNING, logger="duckdice"):
        s = _fresh_load(2)
    assert s.min_bet == 0.0 and s.base_bet == pytest.approx(0.00075)
    assert "below the minimum bet" not in caplog.text


@pytest.mark.parametrize("mode", ["bet", "both"])
def test_split_below_learned_min_keeps_full(saved_config, mode, caplog):
    _write_lanes(saved_config, count=4, split=mode)
    with caplog.at_level(logging.WARNING, logger="duckdice"):
        s = _fresh_load(2, LEARNED_MIN)
    assert s.base_bet == 0.003 and s.current_bet == 0.003
    assert "below the minimum bet" in caplog.text


def test_split_at_or_above_learned_min_divides(saved_config, caplog):
    _write_lanes(saved_config, count=3, split="bet")         # 0.003 / 3 = 0.001
    with caplog.at_level(logging.WARNING, logger="duckdice"):
        s = _fresh_load(1, LEARNED_MIN)
    assert s.base_bet == pytest.approx(0.001)
    assert "below the minimum bet" not in caplog.text


def test_below_min_full_bet_saved_unchanged(saved_config):
    _write_lanes(saved_config, count=4, split="bet")
    _fresh_load(1, LEARNED_MIN)
    dd.save_config()
    assert json.loads(saved_config.read_text())["base_bet"] == 0.003


def test_override_below_min_kept_as_is(saved_config):
    _write_lanes(saved_config, count=4, split="bet", overrides={"2": {"base_bet": 0.0005}})
    assert _fresh_load(2, LEARNED_MIN).base_bet == 0.0005


def test_live_reload_below_min_keeps_full_no_warning(saved_config, caplog):
    _write_lanes(saved_config, count=4, split="bet")
    s = _fresh_load(2, LEARNED_MIN)
    caplog.clear()
    with caplog.at_level(logging.WARNING, logger="duckdice"):
        applied = dd._reload_live_config()
    assert "base_bet" not in applied and s.base_bet == 0.003
    assert "skipped" not in caplog.text and "base_bet" not in caplog.text


# ── basebet_pct split vs the learned minimum ───────────────────────────

def test_basebet_pct_below_min_keeps_full(cli_state, caplog):
    # 10 x 0.0003 = 0.003; / 4 = 0.00075 < learned minimum
    s = cli_state(basebet_pct=0.0003, current_balance=10.0, lanes_count=4, split_lanes=4,
                  lane_split="both", min_bet=LEARNED_MIN)
    with caplog.at_level(logging.WARNING, logger="duckdice"):
        assert dd._apply_basebet_pct()
    assert s.base_bet == pytest.approx(0.003) and s.current_bet == pytest.approx(0.003)
    assert "below the minimum bet" in caplog.text


def test_basebet_pct_above_min_divided(cli_state):
    s = cli_state(basebet_pct=0.001, current_balance=10.0, lanes_count=2, split_lanes=2,
                  lane_split="bet", min_bet=LEARNED_MIN)
    assert dd._apply_basebet_pct()
    assert s.base_bet == pytest.approx(0.005)


@pytest.mark.parametrize("mode", ["none", "budget"])
def test_basebet_pct_not_divided_without_bet_split(cli_state, mode):
    s = cli_state(base_bet=0.02, basebet_pct=0.001, current_balance=10.0, lanes_count=2, split_lanes=2,
                  lane_split=mode)
    assert dd._apply_basebet_pct()
    assert s.base_bet == pytest.approx(0.01)


def test_basebet_pct_unsplit_below_min_still_floored(cli_state):
    s = cli_state(basebet_pct=0.00005, current_balance=10.0, lanes_count=1, split_lanes=1,
                  lane_split="bet", min_bet=LEARNED_MIN)
    assert dd._apply_basebet_pct()
    assert s.base_bet == pytest.approx(LEARNED_MIN)


# ── modes end to end ───────────────────────────────────────────────────

def test_none_default_full_bet_no_budget(saved_config):
    _write_lanes(saved_config, base_bet=0.01, count=2)
    s = _fresh_load(2)
    assert s.lane_split == "none" and s.base_bet == 0.01
    s.current_balance = 1.0
    dd._init_lane_budget()
    assert s.lane_share is None
    s.current_bet = 1e9
    assert dd.should_stop() == (False, "")


def test_both_splits_bet_and_budget(saved_config):
    _write_lanes(saved_config, base_bet=0.01, count=2, split="both")
    s = _fresh_load(2)
    assert s.base_bet == pytest.approx(0.005)
    s.current_balance = 4.0
    dd._init_lane_budget()
    assert s.lane_share == pytest.approx(2.0)
    s.current_bet = 2.5
    stop, reason = dd.should_stop()
    assert stop and reason.startswith("Lane budget exceeded")
