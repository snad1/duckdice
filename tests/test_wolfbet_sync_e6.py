"""duckdice CLI exit reason, session export and offline backtest (CLI v1.13.0).

Mirrors WolfBet CLI v2.20.0 export, v2.21.0 backtest and v2.37.1 exit reason
(reference: wolfbet/tests/test_phase5a_export.py, test_phase5c_backtest.py).

Run: APP_ENV=testing .venv-test/bin/python -m pytest duckdice/tests -q
"""

import csv
import inspect
import json
import logging
import os
import socket
import stat
import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest

import duckdice as go


def _bt_args(**kw):
    base = {"preset": None, "replay_session": None, "simulate": None, "seed": None}
    base.update(kw)
    return SimpleNamespace(**base)


def _played_session(fake_casino, cli_state, run_cli, *outcomes):
    """A finished session on the fake casino with one bet per outcome."""
    fake_casino.queue(*outcomes)
    cli_state(max_bets=len(outcomes))
    s = run_cli()
    go._db_save_session()
    return s.session_id


# ── exit reason ──────────────────────────────────────────
def test_summary_shows_the_stop_reason(capsys):
    go.state.status = "STOPPED: Max loss hit: -0.00500000"
    go._print_summary()
    out = capsys.readouterr().out
    assert "Stop reason" in out and "Max loss hit" in out


def test_summary_shows_a_dash_without_a_status(capsys):
    go.state.status = ""
    go._print_summary()
    line = next(l for l in capsys.readouterr().out.splitlines() if "Stop reason" in l)
    assert line.split("Stop reason", 1)[1].strip() == "-"


def test_daemon_exit_logs_the_reason_before_the_final_state_write():
    src = inspect.getsource(go.main)
    daemon = src[src.index("#  DAEMON MODE"):src.index("#  TUI MODE")]
    exit_at = daemon.index("DAEMON EXIT")
    assert "reason=%s" in daemon and "state.status" in daemon[exit_at:exit_at + 200]
    assert daemon.index("_db_save_session()") < exit_at < daemon.index("_save_state_file()", exit_at)


def test_saved_state_status_matches_the_logged_reason(fake_casino, cli_state, run_cli):
    cli_state(max_bets=1)
    fake_casino.queue("win")
    run_cli()
    go._save_state_file()
    saved = json.loads(Path(go.STATE_PATH).read_text())
    assert saved["status"] == go.state.status
    assert saved["status"].startswith("STOPPED: Max bets reached")


# ── export ───────────────────────────────────────────────
def test_export_csv_shape_and_mode(fake_casino, cli_state, run_cli, tmp_path):
    sid = _played_session(fake_casino, cli_state, run_cli, "win", "loss", "win")
    path = tmp_path / "s.csv"

    n, fmt = go._export_session(sid, str(path))

    assert (n, fmt) == (3, "csv")
    assert stat.S_IMODE(os.stat(path).st_mode) == 0o600
    lines = path.read_text().splitlines()
    assert lines[0] == f"# id: {sid}"
    rows = list(csv.DictReader(l for l in lines if not l.startswith("#")))
    assert [r["state"] for r in rows] == ["win", "loss", "win"]
    assert {"amount", "multiplier", "rule", "bet_value", "result_value", "profit",
            "balance_after"} <= set(rows[0])


def test_export_json_shape(fake_casino, cli_state, run_cli, tmp_path):
    sid = _played_session(fake_casino, cli_state, run_cli, "loss", "win")
    path = tmp_path / "s.json"

    n, fmt = go._export_session(sid, str(path))

    data = json.loads(path.read_text())
    assert (n, fmt) == (2, "json")
    assert data["session"]["id"] == sid
    assert [b["state"] for b in data["bets"]] == ["loss", "win"]
    assert stat.S_IMODE(os.stat(path).st_mode) == 0o600


def test_export_missing_session_fails_cleanly(fake_casino, cli_state, run_cli, tmp_path, capsys):
    _played_session(fake_casino, cli_state, run_cli, "win")
    assert go.cmd_export(str(tmp_path / "x.csv"), 999) == 1
    out = capsys.readouterr().out
    assert "Session #999 not found" in out and "Traceback" not in out
    assert not (tmp_path / "x.csv").exists()


def test_export_defaults_to_the_latest_session(fake_casino, cli_state, run_cli, tmp_path):
    _played_session(fake_casino, cli_state, run_cli, "win")
    latest = _played_session(fake_casino, cli_state, run_cli, "loss", "loss")
    path = tmp_path / "latest.json"

    assert go.cmd_export(str(path)) == 0
    assert json.loads(path.read_text())["session"]["id"] == latest


def test_export_with_an_empty_db_says_so(capsys, tmp_path):
    assert go.cmd_export(str(tmp_path / "x.csv")) == 1
    assert "No sessions in the DB yet" in capsys.readouterr().out


# ── backtest ─────────────────────────────────────────────
def _summary_numbers():
    s = go.state
    return (s.total_bets, s.wins, s.losses, round(s.profit, 8), round(s.current_balance, 8))


def test_backtest_is_deterministic_by_seed():
    go._run_backtest(_bt_args(simulate=500, seed=42))
    first = _summary_numbers()
    go.state = go.BotState()
    go._run_backtest(_bt_args(simulate=500, seed=42))
    assert _summary_numbers() == first
    go.state = go.BotState()
    go._run_backtest(_bt_args(simulate=500, seed=7))
    assert _summary_numbers() != first


@pytest.mark.parametrize("chance,rule", [(49.5, "over"), (49.5, "under"), (33.0, "over"), (33.0, "under")])
def test_simulated_win_rate_matches_the_chance(chance, rule):
    import random
    s = go.state
    s.bet_value, s.multiplier, s.rule, s.current_bet = chance, round(99.0 / chance, 4), rule, 0.01
    rng = random.Random(1)
    wins = sum(go._simulate_bet_outcome(rng)["bet"]["state"] == "win" for _ in range(10_000))
    assert wins / 100 == pytest.approx(chance, abs=1.5)


def test_simulated_bet_has_the_api_shape_and_payout():
    import random
    s = go.state
    s.bet_value, s.multiplier, s.rule = 49.5, 2.0, "over"
    s.current_bet, s.current_balance = 0.01, 1.0
    rng = random.Random(3)
    results = [go._simulate_bet_outcome(rng) for _ in range(200)]
    wins = [r for r in results if r["bet"]["state"] == "win"]
    losses = [r for r in results if r["bet"]["state"] == "loss"]
    assert wins and losses
    assert all(r["bet"]["profit"] == pytest.approx(0.01) for r in wins)
    assert all(r["bet"]["profit"] == pytest.approx(-0.01) for r in losses)
    assert all(r["bet"]["result_value"] > 50.5 for r in wins)
    assert all(r["userBalance"]["amount"] == pytest.approx(1.0 + r["bet"]["profit"])
               for r in results)


def test_wolfrider_runs_on_each_backtest_bet(monkeypatch):
    switches = []
    real = go.wolfrider_switch
    monkeypatch.setattr(go, "wolfrider_switch", lambda: switches.append(1) or real())
    go.state.strategy_key = "6"
    monkeypatch.setattr(go, "load_config", lambda *a, **k: False)
    go._run_backtest(_bt_args(simulate=50, seed=1))
    assert len(switches) == 50


def test_faucet_mode_still_runs_offline(monkeypatch, capsys):
    def no_network(*a, **k):
        raise AssertionError("backtest must not touch the network")
    monkeypatch.setattr(go, "_http_session", SimpleNamespace(post=no_network, get=no_network,
                                                             request=no_network))
    monkeypatch.setattr(go, "load_config", lambda *a, **k: False)
    go.state.faucet = True
    assert go._run_backtest(_bt_args(simulate=100, seed=4)) == 0
    assert go.state.total_bets == 100
    assert "faucet: virtual" in capsys.readouterr().out


def test_backtest_honours_max_bets():
    go.state.max_bets = 25
    go._run_backtest(_bt_args(simulate=1000, seed=1))
    assert go.state.total_bets == 25
    assert go.state.status.startswith("[BACKTEST] STOPPED: Max bets reached")


def test_backtest_uses_the_live_next_bet_code(monkeypatch):
    calls = []
    real = go._next_bet_after
    monkeypatch.setattr(go, "_next_bet_after", lambda b: calls.append(b) or real(b))
    go._run_backtest(_bt_args(simulate=50, seed=1))
    assert len(calls) == 50


def test_replay_uses_the_stored_outcomes(fake_casino, cli_state, run_cli):
    sid = _played_session(fake_casino, cli_state, run_cli, "win", "loss", "win")
    go.state = go.BotState()
    outcomes = []
    real = go._record_bet_outcome

    def spy(bet_state, amount, profit, balance):
        outcomes.append(bet_state == "win")
        return real(bet_state, amount, profit, balance)

    go._record_bet_outcome = spy
    try:
        assert go._run_backtest(_bt_args(replay_session=sid)) == 0
    finally:
        go._record_bet_outcome = real
    assert outcomes == [True, False, True]


def test_replay_of_an_empty_session_fails_cleanly(capsys):
    go.init_db()
    assert go._run_backtest(_bt_args(replay_session=42)) == 1
    assert "has no bets to replay" in capsys.readouterr().out


def test_backtest_makes_no_network_call_and_writes_no_bets(monkeypatch):
    def no_network(*a, **k):
        raise AssertionError("backtest must not touch the network")
    monkeypatch.setattr(socket.socket, "connect", no_network)
    monkeypatch.setattr(socket, "create_connection", no_network)
    monkeypatch.setattr(go, "_http_session", SimpleNamespace(post=no_network, get=no_network,
                                                             request=no_network))
    monkeypatch.setattr(go, "api_get_balances", no_network)
    monkeypatch.setattr(go, "_db_save_bet_dispatch", no_network)
    monkeypatch.setattr(go, "_db_start_session", no_network)

    assert go._run_backtest(_bt_args(simulate=300, seed=5)) == 0
    assert go.state.total_bets == 300


def test_cli_flags_wired():
    src = inspect.getsource(go.main)
    for flag in ('"--export"', '"--export-session-id"', '"--backtest"',
                 '"--replay-session"', '"--simulate"', '"--seed"'):
        assert flag in src
    assert "cmd_export(args.export, args.export_session_id)" in src
    assert "_run_backtest(args)" in src


def test_export_hotkey_wired():
    assert 'show_export()' in inspect.getsource(go.input_handler)
    assert "[E]xport" in inspect.getsource(go)


# ── duckctl ────────────────────────────────────────────
_CTL = Path(go.__file__).parent / "duckctl"


def test_ctl_syntax():
    assert subprocess.run(["bash", "-n", str(_CTL)]).returncode == 0


def test_ctl_pass_throughs():
    src = _CTL.read_text()
    assert '--export "$path" --export-session-id "$sid"' in src
    assert '--backtest "$@"' in src
    assert "export)" in src and "backtest)" in src
