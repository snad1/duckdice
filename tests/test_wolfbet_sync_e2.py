"""Duckdice TG parity with WolfBet TG 1.20.0.

Streak reset (v2.32.0), delay caps (v2.30.6), insufficient balance stays
terminal (v2.32.1), /analytics window fix (v2.33.0), recurring milestone
re-arm (v2.30.1), the insufficient-balance false positive, escalating 429
backoff (same as cryptogames TG) and faucet mode kept across a restart.
Modelled on wolfbet/tests/test_phase9_streak_reset.py (TG half).

Run: APP_ENV=testing .venv-test/bin/python -m pytest duckdice/tests -q
"""

import asyncio
import sqlite3
from types import SimpleNamespace

import pytest

import tg.handlers as h
from tg import engine as tg_engine
from tg.config import CONFIG_KEYS
from tg.database import user_db_path, load_user_config, save_user_config
from tg.formatter import format_full_config
from core.database import init_db, migrate_db

MARTINGALE = {"strategy": "Martingale", "strategy_key": "2", "loss_mult": 2.0}


def _session_ids(db):
    conn = sqlite3.connect(db)
    try:
        return [r[0] for r in conn.execute("SELECT id FROM sessions ORDER BY id")]
    finally:
        conn.close()


# ── engine: streak reset ─────────────────────────────────
def test_streak_reset_fires_on_breaking_win_at_profit(fake_casino, tg_engine_factory, run_tg):
    fake_casino.queue("loss", "loss", "loss", "win", "win")
    e = run_tg(tg_engine_factory(max_bets=10, streak_reset_losses=3, **MARTINGALE))

    assert e.total_bets == 4
    assert e.profit >= 0
    assert e.stop_reason == "Loss streak reset: 3 losses then win"
    assert e.stop_reason.startswith(tg_engine.STREAK_RESET_REASON)
    assert e.last_broken_loss_streak == 3
    ended = sqlite3.connect(e.db_path).execute(
        "SELECT ended_at FROM sessions WHERE id=?", (e.session_id,)).fetchone()[0]
    assert ended


def test_streak_reset_not_in_the_red(fake_casino, tg_engine_factory, run_tg):
    # Flat bet: 3 losses then a win leaves profit at -2 bets.
    fake_casino.queue("loss", "loss", "loss", "win", "win", "win")
    e = run_tg(tg_engine_factory(max_bets=6, streak_reset_losses=3))

    assert e.total_bets == 6
    assert e.stop_reason.startswith("Max bets")


def test_streak_reset_below_threshold_and_second_win(fake_casino, tg_engine_factory, run_tg):
    # 2 losses < 3 → no fire; the second win in a row never fires either.
    fake_casino.queue("loss", "loss", "win", "win", "win")
    e = run_tg(tg_engine_factory(max_bets=5, streak_reset_losses=3, **MARTINGALE))
    assert e.stop_reason.startswith("Max bets")


def test_streak_reset_off(fake_casino, tg_engine_factory, run_tg):
    fake_casino.queue("loss", "loss", "loss", "win", "win")
    e = run_tg(tg_engine_factory(max_bets=5, **MARTINGALE))
    assert e.stop_reason.startswith("Max bets")


def test_streak_reset_on_faucet_wallet(fake_casino, tg_engine_factory, run_tg):
    fake_casino.faucet_balance = 1.0
    fake_casino.queue("loss", "loss", "loss", "win")
    e = run_tg(tg_engine_factory(max_bets=10, streak_reset_losses=3, faucet=True,
                                 base_bet=0.001, **MARTINGALE))

    assert e.stop_reason.startswith(tg_engine.STREAK_RESET_REASON)
    assert all(b["faucet"] for b in fake_casino.bets)
    assert fake_casino.balance == 10.0                 # main wallet untouched


def test_should_stop_only_on_breaking_win(tg_engine_factory):
    e = tg_engine_factory(streak_reset_losses=5, max_bets=None)
    e.profit = 0.0
    e.current_streak, e.last_broken_loss_streak = 1, 5
    assert e._should_stop()[0]
    e.current_streak = 2
    assert not e._should_stop()[0]
    e.current_streak, e.profit = 1, -0.001
    assert not e._should_stop()[0]


def test_mutate_snapshot_and_status(tg_engine_factory):
    e = tg_engine_factory(streak_reset_losses=4, streak_reset_delay=900, faucet=True)
    assert "after 7 losses" in asyncio.run(e.mutate({"streak_reset_losses": 7}))
    assert e.streak_reset_losses == 7
    e.last_broken_loss_streak = 3
    snap = e.snapshot_state()
    assert snap["config"]["streak_reset_losses"] == 7
    assert snap["config"]["streak_reset_delay"] == 900
    assert snap["config"]["faucet"] is True

    e2 = tg_engine.BettingEngine(1, e.db_path, e.api_key, snap["config"])
    e2.restore_state(snap)
    assert (e2.streak_reset_losses, e2.last_broken_loss_streak) == (7, 3)
    assert "off" in asyncio.run(e2.mutate({"streak_reset_losses": None}))
    assert e2.streak_reset_losses is None

    s = e.get_status()
    assert (s["streak_reset_losses"], s["streak_reset_delay"]) == (7, 900)


def test_formatter_shows_sr_segment(tg_engine_factory):
    e = tg_engine_factory(streak_reset_losses=3)
    assert "SR 3L→600s" in "\n".join(format_full_config(e.get_status()))
    e = tg_engine_factory(streak_reset_losses=3, streak_reset_delay=120)
    assert "SR 3L→120s" in "\n".join(format_full_config(e.get_status()))
    e = tg_engine_factory()
    assert "SR " not in "\n".join(format_full_config(e.get_status()))


# ── insufficient balance match ───────────────────────────
def test_balance_message_is_not_insufficient(fake_casino, tg_engine_factory, run_tg):
    fake_casino.queue("win", "api_error_balance", "loss", "win")
    e = run_tg(tg_engine_factory(max_bets=3))

    assert not e._insufficient_balance
    assert e.total_bets == 3
    assert e.stop_reason.startswith("Max bets")
    assert not e.errors


def test_balance_exception_is_not_insufficient(tg_engine_factory):
    # The generic `except Exception` path used to match any "balance" text.
    e = tg_engine_factory()

    class _Boom:
        async def post(self, *a, **k):
            raise RuntimeError("balance lookup failed")
    e._http = _Boom()
    assert asyncio.run(e._api_place_bet(0.01)) is None
    assert not e._insufficient_balance
    assert "Insufficient" not in e.last_error


def test_insufficient_message_still_stops(fake_casino, tg_engine_factory, run_tg):
    fake_casino.queue("win", "insufficient_balance", "win")
    e = run_tg(tg_engine_factory(max_bets=5))
    assert e._insufficient_balance
    assert e.stop_reason.startswith("Insufficient balance")


# ── 429 backoff ──────────────────────────────────────────
@pytest.fixture
def rate_waits(monkeypatch):
    """Delays passed to the engine's interruptible 429 wait."""
    waits = []

    async def _wait(self, delay):
        waits.append(delay)
    monkeypatch.setattr(tg_engine.BettingEngine, "_wait_running", _wait)
    return waits


def test_429_backoff_escalates_and_resets(fake_casino, tg_engine_factory, run_tg, rate_waits):
    fake_casino.queue("http_429", "http_429", "http_429", "win", "http_429", "win")
    e = run_tg(tg_engine_factory(max_bets=2))

    assert e.total_bets == 2
    assert rate_waits == [1, 2, 4, 1]                  # 2^n, reset by the win
    assert (e._consecutive_429, e._rate_limit_total_wait) == (0, 0.0)
    assert e.stop_reason.startswith("Max bets")


def test_429_backoff_capped_at_30s(fake_casino, tg_engine_factory, run_tg, rate_waits):
    fake_casino.queue(*(["http_429"] * 7), "win")
    run_tg(tg_engine_factory(max_bets=1))
    assert rate_waits == [1, 2, 4, 8, 16, 30, 30]


def test_429_uses_retry_after(fake_casino, tg_engine_factory, run_tg, rate_waits):
    fake_casino.retry_after = 7
    fake_casino.queue("http_429", "http_429", "win")
    run_tg(tg_engine_factory(max_bets=1))
    assert rate_waits == [7.0, 7.0]


def test_429_gives_up_after_1800s(fake_casino, tg_engine_factory, run_tg, rate_waits):
    fake_casino.retry_after = 600
    fake_casino.queue(*(["http_429"] * 5), "win")
    e = run_tg(tg_engine_factory(max_bets=1))

    assert rate_waits == [600.0, 600.0, 600.0]         # 4th 429: 1800 s already waited
    assert e.total_bets == 0
    assert e.stop_reason == "Rate limit persisted >30min: giving up"


def test_wait_running_returns_on_stop(tg_engine_factory, sleeps):
    e = tg_engine_factory()
    e.running = True
    asyncio.run(e._wait_running(1.0))
    assert sum(sleeps) == pytest.approx(1.0)
    sleeps.clear()
    e.running = False
    asyncio.run(e._wait_running(30.0))
    assert sleeps == []


# ── handlers: restart rules ──────────────────────────────
@pytest.fixture
def notify(monkeypatch):
    """Drive _notify_stop with a stub engine; return the captured restarts."""
    async def _send(*a, **k):
        return None
    monkeypatch.setattr(h, "_safe_send", _send)

    def run(reason, live_cfg, profit=0.1, base_bet=0.002, rec_cfg=None):
        uid, slot = 7, 1
        save_user_config(uid, live_cfg)
        calls = []

        async def _restart(chat_id, user_id, old_slot, delay, config, app):
            calls.append((delay, dict(config)))
        monkeypatch.setattr(h, "_recurring_restart", _restart)

        engine = SimpleNamespace(profit=profit, base_bet=base_bet, session_id=1,
                                 get_status=lambda: {})
        monkeypatch.setitem(h.active_engines, uid, {slot: engine})
        monkeypatch.setitem(h.recurring_state, uid, {slot: {
            "delay": 60, "chat_id": 1,
            "config": dict(rec_cfg or {"streak_reset_losses": 5}), "timer_task": None,
        }})

        async def _go():
            await h._notify_stop(1, uid, slot, reason, None)
            await asyncio.sleep(0)
        asyncio.run(_go())
        return calls
    return run


SR_REASON = "Loss streak reset: 5 losses then win"


def test_streak_reset_restarts_without_recurring(notify):
    calls = notify(SR_REASON, {"recurring": False, "streak_reset_losses": 5,
                               "streak_reset_delay": 300})
    assert len(calls) == 1
    delay, cfg = calls[0]
    assert delay == 300
    assert cfg["base_bet"] == 0.002            # bumped base carried


def test_streak_reset_restart_keeps_faucet(notify):
    calls = notify(SR_REASON, {"streak_reset_losses": 5, "faucet": True},
                   rec_cfg={"streak_reset_losses": 5, "faucet": True})
    assert calls[0][1]["faucet"] is True


def test_streak_reset_default_delay(notify):
    calls = notify(SR_REASON, {"streak_reset_losses": 5})
    assert calls[0][0] == 600


def test_streak_reset_off_no_restart(notify):
    assert notify(SR_REASON, {"recurring": True, "streak_reset_losses": None}) == []


def test_live_srloss_carried_into_next_cycle(notify):
    calls = notify(SR_REASON, {"streak_reset_losses": 8, "streak_reset_delay": 30})
    assert calls[0][1]["streak_reset_losses"] == 8
    assert calls[0][1]["streak_reset_delay"] == 30


def test_recurring_keeps_recurring_delay(notify):
    calls = notify("Max bets: 100", {"recurring": True, "recurring_delay": 45,
                                     "streak_reset_losses": 5, "streak_reset_delay": 300})
    assert calls[0][0] == 45


def test_recurring_delay_capped_at_stop(notify):
    calls = notify("Max bets: 100", {"recurring": True, "recurring_delay": 200000})
    assert calls[0][0] == 86400


@pytest.mark.parametrize("reason", ["Insufficient balance for bet 0.00100000",
                                    "Stopped by user"])
def test_terminal_stops_never_restart(notify, reason):
    assert notify(reason, {"recurring": True, "streak_reset_losses": 5}) == []


def test_streak_reset_delay_helper():
    assert h._streak_reset_delay({}) == 600
    assert h._streak_reset_delay({"streak_reset_delay": 1}) == 5
    assert h._streak_reset_delay({"streak_reset_delay": 999999}) == 86400
    assert h._streak_reset_delay({"streak_reset_delay": 120}) == 120


def test_recurring_restart_opens_new_session_and_rearms_milestone(
        fake_casino, tg_engine_factory, monkeypatch):
    tg_engine_factory()   # installs the fake HTTP client on BettingEngine
    async def _send(*a, **k):
        return None
    monkeypatch.setattr(h, "_safe_send", _send)
    uid, slot = 9, 1
    first = tg_engine.BettingEngine(uid, user_db_path(uid), fake_casino.api_key,
                                    {"base_bet": 0.001})
    init_db(first.db_path)
    migrate_db(first.db_path)
    first_id = first._db_start_session()

    fake_casino.faucet_balance = 1.0
    config = {"api_key": fake_casino.api_key, "currency": "usdt", "rule": "over",
              "multiplier": 2.0, "bet_value": 49.5,
              "strategy": "Flat Bet", "strategy_key": "1", "max_bets": 1,
              "faucet": True,
              "base_bet": 0.003,              # bumped by profit_increment last cycle
              "profit_increment": 0.001, "profit_threshold": 0.05}
    fake_casino.queue("win")
    monkeypatch.setitem(h.recurring_state, uid, {slot: {
        "delay": 5, "chat_id": 1, "config": config, "timer_task": None}})
    monkeypatch.setattr(h, "_notify_stop", lambda *a, **k: _send())

    async def _go():
        await h._recurring_restart(1, uid, slot, 5, config, None)
        eng = h.active_engines[uid][slot]
        await eng._task
        return eng
    eng = asyncio.run(_go())
    h.active_engines.pop(uid, None)

    assert eng.session_id != first_id
    assert _session_ids(eng.db_path)[-1] == eng.session_id
    assert eng.base_bet == 0.003
    assert eng.next_profit_milestone == pytest.approx(0.05)
    assert eng.faucet and fake_casino.bets[-1]["faucet"]


# ── handlers: /set, /tweak, /config ──────────────────────
def _update(uid, replies):
    async def reply_text(text, **k):
        replies.append(text)
    return SimpleNamespace(effective_user=SimpleNamespace(id=uid),
                           message=SimpleNamespace(reply_text=reply_text))


def _cmd(fn, uid, *args):
    replies = []
    asyncio.run(fn(_update(uid, replies), SimpleNamespace(args=list(args))))
    return replies


def test_set_recdelay_clamps_with_message():
    r = _cmd(h.cmd_set, 3, "recdelay", "100000")
    assert load_user_config(3)["recurring_delay"] == 86400
    assert "clamped from 100000 to 86400" in r[0]
    r = _cmd(h.cmd_set, 3, "recurringdelay", "1")
    assert load_user_config(3)["recurring_delay"] == 5
    assert "clamped" in r[0]
    r = _cmd(h.cmd_set, 3, "recdelay", "90")
    assert load_user_config(3)["recurring_delay"] == 90
    assert "clamped" not in r[0]


def test_set_srloss_and_srdelay():
    _cmd(h.cmd_set, 4, "srloss", "6")
    assert load_user_config(4)["streak_reset_losses"] == 6
    r = _cmd(h.cmd_set, 4, "srdelay", "2")
    assert load_user_config(4)["streak_reset_delay"] == 5
    assert "clamped" in r[0]
    _cmd(h.cmd_set, 4, "streakresetdelay", "700")
    assert load_user_config(4)["streak_reset_delay"] == 700
    _cmd(h.cmd_set, 4, "srloss", "off")
    assert load_user_config(4)["streak_reset_losses"] is None
    r = _cmd(h.cmd_set, 4, "srloss", "-1")
    assert ">= 1" in r[0]


def test_config_shows_streak_reset():
    save_user_config(5, {"streak_reset_losses": 4, "streak_reset_delay": 120})
    r = _cmd(h.cmd_config, 5)
    assert "after 4 losses, pause 120s" in "\n".join(r)


def test_tweak_srloss_and_srdelay(tg_engine_factory, monkeypatch):
    uid = 6
    e = tg_engine_factory()
    monkeypatch.setitem(h.active_engines, uid, {1: e})
    e.running = True
    r = _cmd(h.cmd_tweak, uid, "srloss", "4")
    assert e.streak_reset_losses == 4 and "after 4 losses" in r[0]
    r = _cmd(h.cmd_tweak, uid, "srloss", "off")
    assert e.streak_reset_losses is None
    r = _cmd(h.cmd_tweak, uid, "srdelay", "999999")
    assert load_user_config(uid)["streak_reset_delay"] == 86400 and "clamped" in r[0]
    _cmd(h.cmd_tweak, uid, "recdelay", "30")
    assert load_user_config(uid)["recurring_delay"] == 30


def test_config_keys():
    assert {"streak_reset_losses", "streak_reset_delay"} <= set(CONFIG_KEYS)


# ── /analytics window ────────────────────────────────────
def _seed(uid, started):
    db = user_db_path(uid)
    init_db(db)
    conn = sqlite3.connect(db)
    for ts, profit in started:
        conn.execute("INSERT INTO sessions (started_at, game, strategy, total_bets,"
                     " wins, losses, profit, wagered) VALUES (?,?,?,?,?,?,?,?)",
                     (ts, "dice", "Flat Bet", 10, 5, 5, profit, 1.0))
    conn.commit()
    conn.close()


def test_analytics_bounds_join_time_tokens():
    assert h._analytics_bounds([]) == (None, None)
    assert h._analytics_bounds(["2026-11-13", "14:00"]) == ("2026-11-13 14:00", None)
    assert h._analytics_bounds(["2026-11-13", "14:00", "2026-11-14"]) == (
        "2026-11-13 14:00", "2026-11-14")
    assert h._analytics_bounds(["2026-11-13", "2026-11-14", "09:30"]) == (
        "2026-11-13", "2026-11-14 09:30")
    assert h._analytics_bounds(["a", "b", "c"]) is None


def test_analytics_space_time_excludes_earlier_rows():
    uid = 11
    _seed(uid, [("2026-11-13T13:00:00", 1.0), ("2026-11-13T15:00:00", 2.0)])
    text = "\n".join(_cmd(h.cmd_analytics, uid, "2026-11-13", "14:00"))
    assert "Sessions: `1`" in text
    assert "Range analytics" in text

    text = "\n".join(_cmd(h.cmd_analytics, uid, "2026-11-13", "2026-11-13"))
    assert "Sessions: `2`" in text            # bare UNTIL is inclusive

    text = "\n".join(_cmd(h.cmd_analytics, uid))
    assert "Sessions: `2`" in text and "Lifetime analytics" in text


def test_analytics_bad_bound_shows_usage():
    uid = 12
    _seed(uid, [("2026-11-13T13:00:00", 1.0)])
    r = _cmd(h.cmd_analytics, uid, "yesterday")
    assert r and "Usage" in r[0]
