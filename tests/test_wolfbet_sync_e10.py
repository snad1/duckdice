"""WolfBet sync E10-S5: duckdice run-wide profit increment (CLI v1.12.0, TG v1.10.0).

Mirrors WolfBet v2.37.0 (`profit_increment_scope`, `0b7e71a`): scope "run"
measures the profit of the run's sessions (DB sum + current) and keeps the
bumps across restarts in a run file; the TG bot applies the same rules per
user (one slot at a time, since its sessions carry no slot). Reference test:
wolfbet/tests/test_phase10b_run_increment.py.

Run: APP_ENV=testing .venv-test/bin/python -m pytest duckdice/tests -q
"""

import io
import json
import os
import sqlite3
import asyncio
import inspect
from pathlib import Path
from types import SimpleNamespace

import pytest
from rich.console import Console

import duckdice as dd
from tg import database as tg_database, engine as tg_engine, formatter as tg_format
from tg import handlers as h

_HERE = os.path.dirname(os.path.abspath(__file__))
_CLI_SRC = open(os.path.join(os.path.dirname(_HERE), "duckdice.py")).read()


@pytest.fixture
def out(monkeypatch):
    buf = io.StringIO()
    monkeypatch.setattr(dd, "console", Console(file=buf, width=200, color_system=None))
    return buf


def _cfg(scope="run", base=0.01, inc=0.001, thr=0.05):
    s = dd.state
    s.profit_increment_scope, s.base_bet, s.current_bet = scope, base, base
    s.profit_increment, s.profit_threshold = inc, thr
    s.next_profit_milestone = thr
    s.profit = 0.0
    s.run_profit_done = 0.0
    return s


def _seed(rows):
    """rows: [(id, profit, lane)]"""
    conn = sqlite3.connect(dd.DB_PATH)
    for sid, profit, lane in rows:
        conn.execute(
            "INSERT INTO sessions (id, started_at, currency, game, strategy, base_bet, "
            "multiplier, total_bets, wins, losses, profit, wagered, lane) "
            "VALUES (?, '2026-11-15T10:00:00', 'usdt', 'dice', 'Flat Bet', 0.01, 2.0, "
            "4, 2, 2, ?, 0.04, ?)", (sid, profit, lane))
    conn.commit()
    conn.close()


@pytest.fixture
def db():
    dd.init_db()
    yield dd.DB_PATH


def _write_run(path, first, bumps=0, started="2026-11-15T09:00:00"):
    Path(path).write_text(json.dumps({"first_session": first, "started_at": started,
                                      "bumps": bumps}))


def _read_run(path):
    return json.loads(Path(path).read_text())


# ══════════════════════════════════════════════════════════
#  CLI : scope "session" unchanged
# ══════════════════════════════════════════════════════════
def test_session_scope_uses_session_profit():
    _cfg("session")
    dd.state.run_profit_done = 5.0          # ignored in session scope
    dd.state.profit = 0.04
    dd._check_profit_increment()
    assert dd.state.base_bet == pytest.approx(0.01)
    dd.state.profit = 0.05
    dd._check_profit_increment()
    assert dd.state.base_bet == pytest.approx(0.011)
    assert dd.state.next_profit_milestone == pytest.approx(0.10)


def test_init_run_noop_in_session_scope():
    _cfg("session")
    dd._init_run()
    dd._attach_run(7)
    assert dd.state.run_first_session is None
    assert not os.path.exists(dd.RUN_PATH)


# ══════════════════════════════════════════════════════════
#  CLI : scope "run"
# ══════════════════════════════════════════════════════════
def test_new_run_created_at_first_session():
    _cfg("run")
    dd._init_run()
    assert dd.state.run_first_session is None and dd.state.run_bumps == 0
    dd._attach_run(12)
    data = _read_run(dd.RUN_PATH)
    assert data["first_session"] == 12 and data["bumps"] == 0 and data["started_at"]


def test_run_profit_sums_sessions_and_bumps_persist(db):
    _seed([(1, 0.5, 1), (5, 0.02, 1), (6, 0.01, 1), (7, 0.9, 2)])
    _write_run(dd.RUN_PATH, 5)
    _cfg("run")
    dd._init_run()
    assert dd.state.run_profit_done == pytest.approx(0.03)   # #1 before the run, #7 other lane
    dd.state.profit = 0.019
    dd._check_profit_increment()
    assert dd.state.base_bet == pytest.approx(0.01)          # run total 0.049 < 0.05
    dd.state.profit = 0.02                                   # run total 0.05
    dd._check_profit_increment()
    assert dd.state.base_bet == pytest.approx(0.011)
    assert dd.state.next_profit_milestone == pytest.approx(0.10)
    assert _read_run(dd.RUN_PATH)["bumps"] == 1


def test_resume_restores_bumps_on_base_bet(db):
    _seed([(3, 0.12, 1)])
    _write_run(dd.RUN_PATH, 3, bumps=2)
    _cfg("run")
    dd._init_run()
    assert dd.state.base_bet == pytest.approx(0.012)
    assert dd.state.current_bet == pytest.approx(0.012)
    assert dd.state.next_profit_milestone == pytest.approx(0.15)
    dd._attach_run(4)                       # an existing run keeps its start
    assert _read_run(dd.RUN_PATH)["first_session"] == 3


def test_resume_base_never_below_learned_minimum(db):
    _seed([(3, 0.12, 1)])
    _write_run(dd.RUN_PATH, 3, bumps=2)
    _cfg("run", base=0.0004, inc=0.0001, thr=0.05)
    dd.state.min_bet = 0.001
    dd._init_run()
    assert dd.state.base_bet == pytest.approx(0.001)


def test_stale_run_file_after_db_reset_starts_new_run(db):
    _seed([(1, 0.1, 1)])
    _write_run(dd.RUN_PATH, 5000, bumps=4)
    _cfg("run")
    dd._init_run()
    assert dd.state.run_first_session is None and dd.state.run_bumps == 0
    assert dd.state.base_bet == pytest.approx(0.01)


def test_lane_run_file_and_filter(db):
    _seed([(2, 0.02, 1), (3, 0.04, 2)])
    dd.state.lane = 2
    _write_run(dd._run_path(2), 2)
    _cfg("run")
    dd._init_run()
    assert dd.state.run_first_session == 2
    assert dd.state.run_profit_done == pytest.approx(0.04)


def test_faucet_session_counts_in_run(fake_casino, cli_state, run_cli):
    """Faucet bets accumulate in state.profit today, so the run total keeps them."""
    fake_casino.faucet_balance = 10.0
    fake_casino.queue("win", "win", "loss")
    cli_state(max_bets=3, faucet=True, profit_increment=0.001,
              profit_threshold=0.0000000001, profit_increment_scope="run",
              next_profit_milestone=0.0000000001)
    dd._init_run()
    dd._attach_run(dd.state.session_id)
    run_cli()
    assert all(b["faucet"] for b in fake_casino.bets)
    assert dd.state.run_first_session is not None
    assert dd.state.run_bumps >= 1


def test_recurring_keeps_run_milestone_and_total():
    body = _CLI_SRC[_CLI_SRC.index("def _reset_session_for_recurring"):
                    _CLI_SRC.index("def betting_loop")]
    assert "state.run_profit_done += state.profit" in body
    assert 'state.profit_increment_scope != "run"' in body
    assert "_attach_run(state.session_id)" in body


# ══════════════════════════════════════════════════════════
#  CLI : config, off values, reload, reset, display
# ══════════════════════════════════════════════════════════
def test_config_set_scope(monkeypatch, tmp_path, out):
    path = str(tmp_path / "cfg.json")
    Path(path).write_text(json.dumps({
        "api_key": "k" * 40, "currency": "usdt", "game": "dice", "rule": "over",
        "multiplier": 2.0, "bet_value": 49.5, "base_bet": 0.01}))
    monkeypatch.setattr(dd, "CONFIG_PATH", path)
    monkeypatch.setattr(dd, "_signal_base_daemon", lambda: None)
    monkeypatch.setattr(dd, "_signal_lane_daemons", lambda *a, **k: 0)
    dd.cmd_config_set(["piscope=run"])
    assert json.loads(Path(path).read_text())["profit_increment_scope"] == "run"
    with pytest.raises(SystemExit) as e:
        dd.cmd_config_set(["profit_increment_scope=forever"])
    assert e.value.code == 2


@pytest.mark.parametrize("key,val", [("profit_increment", "0.0"),
                                     ("profit_increment", "-1"),
                                     ("profit_threshold", "0.0"),
                                     ("profit_threshold", "none")])
def test_mutation_off_values(key, val):
    _cfg("run")
    dd._apply_mutation({key: val})
    assert getattr(dd.state, key) is None
    assert dd._increment_on() is False


def test_mutation_rejects_garbage():
    _cfg("run")
    with pytest.raises(ValueError):
        dd._apply_mutation({"profit_increment": "abc"})


@pytest.mark.parametrize("inc,thr,scope", [(None, 0.05, "run"), (0.001, None, "run"),
                                           (0.001, 0.05, "session")])
def test_init_run_off_removes_run_file(db, inc, thr, scope):
    _seed([(3, 0.5, 1)])
    _write_run(dd.RUN_PATH, 3, bumps=2)
    _cfg(scope, inc=inc, thr=thr)
    dd._init_run()
    dd._attach_run(4)
    assert not os.path.exists(dd.RUN_PATH)
    assert dd.state.run_first_session is None and dd.state.run_bumps == 0
    assert dd.state.base_bet == pytest.approx(0.01)


def _live_cfg(monkeypatch, tmp_path, **vals):
    path = str(tmp_path / "cfg.json")
    Path(path).write_text(json.dumps({"base_bet": 0.01,
                                      "profit_increment_scope": "run", **vals}))
    monkeypatch.setattr(dd, "CONFIG_PATH", path)


def test_live_reload_increment_off_ends_run(monkeypatch, tmp_path):
    _cfg("run")
    dd._attach_run(5)
    assert os.path.exists(dd.RUN_PATH)
    _live_cfg(monkeypatch, tmp_path, profit_increment=None, profit_threshold=0.05)
    dd._reload_live_config()
    assert not os.path.exists(dd.RUN_PATH) and dd.state.run_first_session is None


def test_live_reload_increment_on_starts_run(monkeypatch, tmp_path):
    _cfg("run", inc=None)
    dd.state.session_id = 8
    _live_cfg(monkeypatch, tmp_path, profit_increment=0.001, profit_threshold=0.05)
    dd._reload_live_config()
    assert dd.state.run_first_session == 8
    assert _read_run(dd.RUN_PATH)["first_session"] == 8


def test_run_reset_removes_files(out):
    for k in (1, 3):
        Path(dd._run_path(k)).write_text("{}")
    assert dd.cmd_run_reset() == 2
    assert not os.path.exists(dd.RUN_PATH)
    Path(dd._run_path(2)).write_text("{}")
    assert dd.cmd_run_reset(2) == 1


def test_progress_row_uses_run_total():
    row = dd._build_config_progress_row({
        "profit_threshold": 0.05, "profit_increment": 0.001,
        "next_profit_milestone": 0.10, "profit": 0.0,
        "profit_increment_scope": "run", "run_profit_total": 0.075})
    assert "RunMs" in row and "50%" in row


def test_status_shows_run_profit_row(monkeypatch, tmp_path, out):
    Path(dd.STATE_PATH).write_text(json.dumps({
        "session_id": 4, "profit": 0.01, "wins": 1, "total_bets": 2,
        "profit_increment": 0.001, "profit_threshold": 0.05,
        "profit_increment_scope": "run", "run_first_session": 1, "run_bumps": 1,
        "run_profit_total": 0.06, "next_profit_milestone": 0.10}))
    dd.cmd_status()
    text = out.getvalue()
    assert "Run Profit" in text and "since #1" in text and "bumps 1" in text


# ══════════════════════════════════════════════════════════
#  TG : engine run scope
# ══════════════════════════════════════════════════════════
def _tg_seed(db_path, rows):
    tg_engine.init_db(db_path)
    tg_engine.migrate_db(db_path)
    conn = sqlite3.connect(db_path)
    for sid, profit in rows:
        conn.execute(
            "INSERT INTO sessions (id, started_at, currency, strategy, base_bet, "
            "multiplier, total_bets, wins, losses, profit, wagered) "
            "VALUES (?, '2026-11-15T10:00:00', 'usdt', 'Flat Bet', 0.01, 2.0, 4, 2, 2, ?, 0.04)",
            (sid, profit))
    conn.commit()
    conn.close()


def test_tg_new_run_created_at_first_session(tg_engine_factory):
    e = tg_engine_factory(profit_increment=0.001, profit_threshold=0.05,
                          profit_increment_scope="run")
    e._init_run()
    e._attach_run(12)
    data = json.loads(Path(e._run_path()).read_text())
    assert data["first_session"] == 12 and data["bumps"] == 0


def test_tg_run_profit_sums_sessions_and_bumps_persist(tg_engine_factory):
    e = tg_engine_factory(profit_increment=0.001, profit_threshold=0.05,
                          profit_increment_scope="run")
    _tg_seed(e.db_path, [(1, 0.5), (5, 0.02), (6, 0.01)])
    Path(e._run_path()).write_text(json.dumps(
        {"first_session": 5, "started_at": "x", "bumps": 0}))
    e._init_run()
    assert e.run_profit_done == pytest.approx(0.03)
    e.profit = 0.019
    e._check_profit_increment()
    assert e.base_bet == pytest.approx(0.01)
    e.profit = 0.02
    e._check_profit_increment()
    assert e.base_bet == pytest.approx(0.011)
    assert e.next_profit_milestone == pytest.approx(0.10)
    assert json.loads(Path(e._run_path()).read_text())["bumps"] == 1


def test_tg_recurring_carry_does_not_double_bump(tg_engine_factory):
    """A recurring restart carries the bumped base: the bumps must not be re-added."""
    e = tg_engine_factory(profit_increment=0.001, profit_threshold=0.05,
                          profit_increment_scope="run",
                          base_bet=0.012, _run_bumps_in_base=2)
    _tg_seed(e.db_path, [(1, 0.1)])
    Path(e._run_path()).write_text(json.dumps(
        {"first_session": 1, "started_at": "x", "bumps": 2}))
    e._init_run()
    assert e.base_bet == pytest.approx(0.012)
    assert e.run_bumps == 2


def test_tg_stale_run_file_starts_new_run(tg_engine_factory):
    e = tg_engine_factory(profit_increment=0.001, profit_threshold=0.05,
                          profit_increment_scope="run")
    _tg_seed(e.db_path, [(1, 0.1)])
    Path(e._run_path()).write_text(json.dumps(
        {"first_session": 5000, "started_at": "x", "bumps": 4}))
    e._init_run()
    assert e.run_first_session is None and e.run_bumps == 0
    assert e.base_bet == pytest.approx(0.01)


def test_tg_snapshot_restore_keeps_run(tg_engine_factory):
    e = tg_engine_factory(profit_increment=0.001, profit_threshold=0.05,
                          profit_increment_scope="run")
    e.run_first_session, e.run_since, e.run_bumps, e.run_profit_done = 3, "x", 2, 0.12
    snap = e.snapshot_state()
    assert snap["config"]["profit_increment_scope"] == "run"
    assert snap["run_first_session"] == 3 and snap["run_bumps"] == 2
    e2 = tg_engine_factory(profit_increment=0.001, profit_threshold=0.05,
                           profit_increment_scope="run")
    e2.restore_state(snap)
    assert e2.run_first_session == 3
    assert e2.run_bumps == 2 and e2.run_profit_done == pytest.approx(0.12)
    assert e2._run_bumps_in_base == 2


def test_tg_reset_run_drops_bumps_and_base(tg_engine_factory):
    e = tg_engine_factory(profit_increment=0.001, profit_threshold=0.05,
                          profit_increment_scope="run", base_bet=0.012)
    e.session_id = 9
    e.run_first_session, e.run_bumps = 3, 2
    Path(e._run_path()).write_text("{}")
    e.reset_run()
    assert e.run_first_session == 9 and e.run_bumps == 0
    assert e.base_bet == pytest.approx(0.01)
    assert e.next_profit_milestone == pytest.approx(0.05)


def test_tg_status_shows_run_line(tg_engine_factory):
    e = tg_engine_factory(profit_increment=0.001, profit_threshold=0.05,
                          profit_increment_scope="run")
    e.run_first_session, e.run_bumps, e.run_profit_done = 3, 1, 0.06
    text = tg_format.format_status(e.get_status())
    assert "Run profit" in text and "#3" in text


# ══════════════════════════════════════════════════════════
#  TG : /set, /tweak, /runreset, one-slot rule
# ══════════════════════════════════════════════════════════
class _Msg:
    def __init__(self):
        self.texts = []
        self.markups = []

    async def reply_text(self, text, **kw):
        self.texts.append(text)
        self.markups.append(kw.get("reply_markup"))


def _update(uid=1):
    msg = _Msg()
    return SimpleNamespace(effective_user=SimpleNamespace(id=uid), message=msg,
                           effective_chat=SimpleNamespace(id=1)), msg


def _run(coro):
    return asyncio.run(coro)


def test_set_piscope_and_off_values():
    upd, msg = _update()
    _run(h.cmd_set(upd, SimpleNamespace(args=["piscope", "run"])))
    assert tg_database.load_user_config(1)["profit_increment_scope"] == "run"
    _run(h.cmd_set(upd, SimpleNamespace(args=["pi", "0.0"])))
    _run(h.cmd_set(upd, SimpleNamespace(args=["pt", "off"])))
    cfg = tg_database.load_user_config(1)
    assert cfg["profit_increment"] is None and cfg["profit_threshold"] is None
    _run(h.cmd_set(upd, SimpleNamespace(args=["piscope", "forever"])))
    assert tg_database.load_user_config(1)["profit_increment_scope"] == "run"
    assert any("session" in t and "run" in t for t in msg.texts)


def test_tweak_piscope_saves_for_next_start(monkeypatch):
    engine = SimpleNamespace(running=True, session_id=1)
    monkeypatch.setitem(h.active_engines, 1, {1: engine})
    upd, msg = _update()
    _run(h.cmd_tweak(upd, SimpleNamespace(args=["piscope", "run"])))
    assert tg_database.load_user_config(1)["profit_increment_scope"] == "run"
    assert any("next start" in t for t in msg.texts)


def test_bet_refused_when_run_scope_already_running(monkeypatch):
    tg_database.save_user_config(1, {"api_key": "k" * 40, "currency": "usdt"})
    running = SimpleNamespace(running=True, profit_increment_scope="run",
                              profit_increment=0.001, profit_threshold=0.05)
    monkeypatch.setitem(h.active_engines, 1, {1: running})
    upd, msg = _update()
    _run(h.cmd_bet(upd, SimpleNamespace(args=[])))
    assert len(msg.texts) == 1 and "one slot" in msg.texts[0]


def test_runreset_removes_file_without_session():
    path = Path(h._run_file_path(1))
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("{}")
    msg = _run(h._do_run_reset(1, None))
    assert "Run reset" in msg and not path.exists()
    assert "No run to reset" in _run(h._do_run_reset(1, None))


def test_runreset_confirm_resets_running_engine(monkeypatch):
    calls = []
    engine = SimpleNamespace(running=True, session_id=4,
                             reset_run=lambda: calls.append(True))
    monkeypatch.setitem(h.active_engines, 1, {1: engine})
    _run(h._do_run_reset(1, 1))
    assert calls == [True]


def test_increment_block_is_one_shared_call():
    src = inspect.getsource(tg_engine.BettingEngine._check_profit_increment)
    assert "check_profit_increment(" in src
    assert "self.base_bet += self.profit_increment" not in src
