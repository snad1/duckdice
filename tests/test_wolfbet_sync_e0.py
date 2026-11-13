"""E0-S2 — fake casino drives the real CLI and TG bet loops (dice only, main wallet).

Run: APP_ENV=testing .venv-test/bin/python -m pytest duckdice/tests -q
"""

import sqlite3

import pytest

import duckdice as dd

SIX_FOUR = ["win", "loss", "win", "win", "loss", "win", "loss", "win", "win", "loss"]


def _bet_rows(db, session_id):
    conn = sqlite3.connect(db)
    try:
        return conn.execute(
            "SELECT amount, multiplier, rule, bet_value, result_value, state, profit,"
            " balance_after FROM bets WHERE session_id=? ORDER BY id",
            (session_id,),
        ).fetchall()
    finally:
        conn.close()


def _session_row(db, session_id):
    conn = sqlite3.connect(db)
    try:
        return conn.execute(
            "SELECT ended_at, total_bets, wins, losses, profit FROM sessions WHERE id=?",
            (session_id,),
        ).fetchone()
    finally:
        conn.close()


def _assert_rows_match(rows, fake):
    assert len(rows) == len(fake.bets)
    for row, bet in zip(rows, fake.bets):
        amount, mult, rule, _bv, roll, state, profit, bal = row
        assert amount == pytest.approx(bet["amount"])
        assert mult == pytest.approx(2.0)
        assert rule == "over"
        assert _bv == pytest.approx(49.5)
        assert roll == pytest.approx(bet["number"])
        assert state == bet["outcome"]
        assert profit == pytest.approx(bet["profit"])
        assert bal == pytest.approx(bet["balance"])


# ── CLI ───────────────────────────────────────────────────
def test_cli_ten_bets(fake_casino, cli_state, run_cli):
    fake_casino.queue(*SIX_FOUR)
    cli_state()
    s = run_cli()

    assert not fake_casino.exhausted
    assert s.status == "STOPPED: Max bets reached: 10"
    assert (s.total_bets, s.wins, s.losses) == (10, 6, 4)
    assert s.profit == pytest.approx(fake_casino.profit)
    assert s.current_balance == pytest.approx(fake_casino.balance)
    _assert_rows_match(_bet_rows(dd.DB_PATH, s.session_id), fake_casino)
    ended, total, wins, losses, profit = _session_row(dd.DB_PATH, s.session_id)
    assert ended and (total, wins, losses) == (10, 6, 4)
    assert profit == pytest.approx(fake_casino.profit)


def test_cli_insufficient_balance_stops(fake_casino, cli_state, run_cli):
    fake_casino.queue("win", "loss", "win", "insufficient_balance", "win")
    cli_state()
    s = run_cli()

    assert s.status == "STOPPED: Insufficient balance"
    assert s.total_bets == 3
    assert fake_casino.bet_posts == 4           # no retry after the refusal
    assert len(_bet_rows(dd.DB_PATH, s.session_id)) == 3
    assert _session_row(dd.DB_PATH, s.session_id)[0]


def test_cli_recovers_from_429_and_timeout(fake_casino, cli_state, run_cli, sleeps):
    script = list(SIX_FOUR)
    script.insert(2, "http_429")
    script.insert(5, "timeout")
    fake_casino.queue(*script)
    cli_state()
    s = run_cli()

    assert not fake_casino.exhausted
    assert (s.total_bets, s.wins, s.losses) == (10, 6, 4)
    assert fake_casino.bet_posts == 12
    # 429: error backoff 2s + rate-limit probe wait 2s; timeout: error backoff 2s
    assert sleeps.count(2.0) == 3
    assert sum(1 for m, u, _ in fake_casino.calls if m == "GET") == 1   # the 429 probe
    assert s.current_balance == pytest.approx(fake_casino.balance)


# ── TG ────────────────────────────────────────────────────
def test_tg_ten_bets(fake_casino, tg_engine_factory, run_tg):
    fake_casino.queue(*SIX_FOUR)
    e = run_tg(tg_engine_factory())

    assert not fake_casino.exhausted
    assert e.stops == ["Max bets: 10"]
    assert (e.total_bets, e.wins, e.losses) == (10, 6, 4)
    assert e.profit == pytest.approx(fake_casino.profit)
    assert e.current_balance == pytest.approx(fake_casino.balance)
    _assert_rows_match(_bet_rows(e.db_path, e.session_id), fake_casino)
    ended, total, wins, losses, _ = _session_row(e.db_path, e.session_id)
    assert ended and (total, wins, losses) == (10, 6, 4)


def test_tg_insufficient_balance_stops(fake_casino, tg_engine_factory, run_tg):
    fake_casino.queue("win", "loss", "win", "insufficient_balance", "win")
    e = run_tg(tg_engine_factory())

    assert e.total_bets == 3
    assert "Insufficient balance" in e.stop_reason
    assert e.errors and "Insufficient balance" in e.errors[0]
    assert fake_casino.bet_posts == 4
    assert len(_bet_rows(e.db_path, e.session_id)) == 3


def test_tg_recovers_from_429_and_timeout(fake_casino, tg_engine_factory, run_tg, sleeps):
    script = list(SIX_FOUR)
    script.insert(2, "http_429")
    script.insert(5, "timeout")
    fake_casino.queue(*script)
    e = run_tg(tg_engine_factory())

    assert not fake_casino.exhausted
    assert (e.total_bets, e.wins, e.losses) == (10, 6, 4)
    assert fake_casino.bet_posts == 12
    assert sleeps.count(2.0) == 2               # error backoff after the 429 and the timeout
    assert sleeps.count(0.25) == 8              # fixed 2 s rate-limit wait (E2-S5 changes this)
