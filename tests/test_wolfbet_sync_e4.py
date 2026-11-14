"""WolfBet sync E4-S5: duckdice CLI stats and analytics.

Mirrors WolfBet v2.30.9 / v2.31.0 (date windows), v2.33.0 (time-of-day
window), v2.33.1 (wins / losses per session), v2.34.0 (time span, bet time,
run time) and v2.35.0 (lane filter, [LK] tags, "By lane"). Reference tests:
test_phase6d_reset_and_datestats.py, test_phase6e_analytics_dates.py,
test_phase8_analytics.py, test_phase9c_stats_window_avgs.py,
test_phase9d_durations.py, test_phase10_lanes.py (stats / analytics part).

DuckDice is dice only and records no faucet flag on sessions or bets, so
by-game has a single "Dice" row; strategies supply the second grouping.
"""

import json
import re
import sqlite3
import sys
import time

import pytest

import duckdice as dd

ANSI = re.compile(r"\x1b\[[0-9;]*m")

# id, started_at, ended_at, game, strategy, bets, wins, losses, profit, wagered, lane
SESSIONS = [
    (1, "2026-11-13T10:00:00", "2026-11-13T11:00:00", "dice", "Flat Bet",       10, 4, 6, -0.002, 0.01, 1),
    # 30 min pause between 2 and 3 (11:30 -> 12:00) is in the span, not bet time
    (2, "2026-11-13T11:00:00", "2026-11-13T11:30:00", "dice", "Martingale", 20, 12, 8, 0.004, 0.02, 1),
    (3, "2026-11-13T14:30:00", "2026-11-13T15:00:00", "dice", "Flat Bet",       30, 15, 15, 0.001, 0.03, 2),
]


def _plain(s):
    return ANSI.sub("", s)


@pytest.fixture
def seeded_db():
    dd.init_db()
    conn = sqlite3.connect(dd.DB_PATH)
    conn.executemany(
        "INSERT INTO sessions (id, started_at, ended_at, currency, game, strategy, "
        "base_bet, multiplier, total_bets, wins, losses, profit, wagered, lane) "
        "VALUES (?,?,?,'usdt',?,?,0.0001,2.0,?,?,?,?,?,?)",
        SESSIONS)
    conn.commit()
    conn.close()
    return dd.DB_PATH


@pytest.fixture
def wide_console(monkeypatch):
    """Rich tables wrap at the default 80 columns; widen for stable text checks."""
    monkeypatch.setattr(dd.console, "width", 200)


def _out(capsys):
    return _plain(capsys.readouterr().out)


# ── window (v2.30.9 / v2.33.0) ────────────────────────────
def test_time_of_day_window_with_space(seeded_db, capsys):
    dd.cmd_stats(since="2026-11-13 14:00")
    out = _out(capsys)
    assert "Session #3" in out
    assert "Session #1" not in out and "Session #2" not in out
    assert "Range Totals" in out
    assert "2026-11-13 14:00" in out


def test_invalid_bound_prints_error_and_no_rows(seeded_db, capsys):
    dd.cmd_stats(since="13/11/2026")
    out = _out(capsys)
    assert 'Invalid --since/--until "13/11/2026"' in out
    assert "Session #" not in out and "Totals" not in out

    dd.cmd_analytics(until="nope")
    out = _out(capsys)
    assert "Invalid --since/--until" in out
    assert "Lifetime analytics" not in out and "Range analytics" not in out
    assert "Overall" not in out


def test_invalid_lane_prints_error(seeded_db, capsys):
    dd.cmd_stats(lane=0)
    out = _out(capsys)
    assert "--lane must be 1-" in out
    assert "Session #" not in out


# ── totals (v2.33.1 / v2.34.0) ────────────────────────────
def test_wins_losses_per_session(seeded_db, capsys):
    dd.cmd_stats()
    out = _out(capsys)
    assert "Wins/Session:  Max 15  Avg 10.3" in out
    assert "Losses/Session: Max 15  Avg 9.7" in out


def test_wins_losses_per_session_window_aware(seeded_db, capsys):
    dd.cmd_stats(until="2026-11-13 12:00")
    out = _out(capsys)
    assert "Wins/Session:  Max 12  Avg 8.0" in out
    assert "Losses/Session: Max 8  Avg 7.0" in out


def test_time_span_vs_bet_time(seeded_db, capsys):
    dd.cmd_stats()
    out = _out(capsys)
    # span 10:00 -> 15:00 = 5h; bet = 1h + 30m + 30m = 2h (pauses excluded)
    assert "Time Span:     5h 0m 0s   Bet Time: 2h 0m 0s" in out
    assert "Sessions:      3  (2026-11-13 10:00:00 → 2026-11-13 15:00:00)" in out


def test_all_time_header_without_window(seeded_db, capsys):
    dd.cmd_stats()
    assert "All-Time Totals" in _out(capsys)


# ── lanes (v2.35.0) ───────────────────────────────────────
def test_lane_filter(seeded_db, capsys):
    dd.cmd_stats(lane=2)
    out = _out(capsys)
    assert "Session #3" in out
    assert "Session #1" not in out and "Session #2" not in out
    assert "[lane 2]" in out
    assert "Range Totals" in out


def test_lane_tag_only_above_lane_one(seeded_db, capsys):
    dd.cmd_stats()
    out = _out(capsys)
    assert "Session #3 [L2]" in out
    assert "Session #2 [L" not in out and "Session #1 [L" not in out


def test_lane_1_includes_old_null_rows(seeded_db, capsys):
    conn = sqlite3.connect(seeded_db)
    conn.execute("UPDATE sessions SET lane=NULL WHERE id=1")
    conn.commit()
    conn.close()
    dd.cmd_stats(lane=1)
    out = _out(capsys)
    assert "Session #1" in out and "Session #2" in out
    assert "Session #3" not in out


def test_init_db_adds_lane_to_old_db():
    """migrate_db (core/database.py, shared with TG) adds the column."""
    conn = sqlite3.connect(dd.DB_PATH)
    conn.execute("CREATE TABLE sessions (id INTEGER PRIMARY KEY AUTOINCREMENT, "
                 "started_at TEXT, ended_at TEXT, profit REAL)")
    conn.execute("INSERT INTO sessions (started_at) VALUES ('2026-01-01T00:00:00')")
    conn.commit()
    conn.close()
    dd.init_db()
    dd.init_db()  # idempotent
    conn = sqlite3.connect(dd.DB_PATH)
    cols = {r[1] for r in conn.execute("PRAGMA table_info(sessions)")}
    lane = conn.execute("SELECT lane FROM sessions").fetchone()[0]
    conn.close()
    assert "lane" in cols
    assert lane == 1


# ── analytics (v2.23.0 / v2.31.0 / v2.34.0 / v2.35.0) ────
def test_analytics_blocks(seeded_db, capsys, wide_console):
    dd.cmd_analytics()
    out = _out(capsys)
    assert "Lifetime analytics" in out
    assert "3 sessions, 60 bets" in out
    for block in ("Overall", "By game", "By strategy",
                  "Top 5 best sessions", "Top 5 worst sessions"):
        assert block in out
    assert "Time range:" in out and "2026-11-13 10:00:00 → 2026-11-13 15:00:00" in out
    assert "Time span:" in out and "5h 0m 0s" in out
    assert "Bet time:" in out and "2h 0m 0s" in out
    assert "Sessions in profit:  2" in out
    assert "Sessions in loss:    1" in out
    assert "Total P/L:           +0.00300000" in out
    # one game (dice), strategy names as stored
    assert "Dice" in out
    assert "Martingale" in out and "Flat Bet" in out


def test_analytics_by_game_and_strategy_totals(seeded_db):
    games = dd._analytics_by_game()
    assert [g["key"] for g in games] == ["Dice"]
    assert games[0]["sessions"] == 3 and games[0]["bets"] == 60
    assert games[0]["profit"] == pytest.approx(0.003)
    strats = {r["key"]: r for r in dd._analytics_by_strategy()}
    assert list(strats) == ["Martingale", "Flat Bet"]  # profit DESC
    assert strats["Flat Bet"]["sessions"] == 2 and strats["Flat Bet"]["bets"] == 40


def test_analytics_null_game_reads_as_dice(seeded_db):
    conn = sqlite3.connect(seeded_db)
    conn.execute("UPDATE sessions SET game=NULL WHERE id=1")
    conn.commit()
    conn.close()
    assert [g["key"] for g in dd._analytics_by_game()] == ["Dice"]
    assert {r["game"] for r in dd._analytics_top_sessions("profit_desc", 5)} == {"Dice"}


def test_analytics_top_sessions_order(seeded_db):
    best = [r["id"] for r in dd._analytics_top_sessions("profit_desc", 5)]
    worst = [r["id"] for r in dd._analytics_top_sessions("profit_asc", 5)]
    assert best == [2, 3, 1]
    assert worst == [1, 3, 2]


def test_analytics_by_lane_totals(seeded_db, capsys, wide_console):
    rows = dd._analytics_by_lane()
    assert [(r["key"], r["sessions"], r["bets"]) for r in rows] == \
        [("Lane 1", 2, 30), ("Lane 2", 1, 30)]
    dd.cmd_analytics()
    assert "By lane" in _out(capsys)


def test_analytics_by_lane_hidden_with_one_lane(seeded_db, capsys, wide_console):
    dd.cmd_analytics(lane=1)
    out = _out(capsys)
    assert "Range analytics" in out and "[lane 1]" in out
    assert "By lane" not in out
    assert "2 sessions, 30 bets" in out


def test_analytics_window(seeded_db, capsys, wide_console):
    dd.cmd_analytics(since="2026-11-13 14:00")
    out = _out(capsys)
    assert "Range analytics" in out
    assert "1 sessions, 30 bets" in out


def test_stats_and_analytics_without_db(capsys):
    dd.cmd_stats()
    assert "No database found" in _out(capsys)
    dd.cmd_analytics()
    assert "No database found" in _out(capsys)


def test_analytics_empty_db(capsys):
    dd.init_db()
    dd.cmd_analytics()
    assert "No sessions in the DB yet" in _out(capsys)


# ── run time (v2.34.0) ────────────────────────────────────
def test_tui_header_run_time():
    dd.state.session_start = time.time() - 60
    dd.state.run_started_at = time.time() - 3725
    head = _plain(dd.build_dashboard_screen()).splitlines()[0]
    assert "Run 1h 2m 5s" in head


def test_monitor_header_run_time():
    d = {"session_id": 4, "uptime_sec": 60, "run_uptime_sec": 90061}
    head = _plain(dd._build_monitor_screen(d, [], True)).splitlines()[0]
    assert "Run 1d 1h 1m 1s" in head


def test_monitor_header_old_state_file_has_no_run():
    d = {"session_id": 4, "uptime_sec": 60}
    head = _plain(dd._build_monitor_screen(d, [], True)).splitlines()[0]
    assert "Run " not in head


def test_status_running_for(capsys):
    with open(dd.STATE_PATH, "w") as f:
        json.dump({"session_id": 1, "uptime_sec": 30, "run_uptime_sec": 3600}, f)
    dd.cmd_status()
    assert re.search(r"Running for\s+1h 0m 0s", _out(capsys))

    with open(dd.STATE_PATH, "w") as f:
        json.dump({"session_id": 1, "uptime_sec": 30}, f)
    dd.cmd_status()
    assert "Running for" not in _out(capsys)


# ── argparse ──────────────────────────────────────────────
def test_main_dispatches_analytics_with_window_and_lane(monkeypatch):
    seen = {}
    monkeypatch.setattr(dd, "cmd_analytics", lambda **kw: seen.update(kw))
    monkeypatch.setattr(sys, "argv", ["duckdice.py", "--analytics", "--since",
                                      "2026-11-13 14:00", "--lane", "2"])
    dd.main()
    assert seen == {"since": "2026-11-13 14:00", "until": None, "lane": 2}


def test_main_dispatches_stats_lane(monkeypatch):
    seen = {}
    monkeypatch.setattr(dd, "cmd_stats", lambda **kw: seen.update(kw))
    monkeypatch.setattr(sys, "argv", ["duckdice.py", "--stats", "--lane", "3"])
    dd.main()
    assert seen == {"since": None, "until": None, "lane": 3}


def test_stats_and_analytics_are_exclusive(monkeypatch):
    monkeypatch.setattr(sys, "argv", ["duckdice.py", "--stats", "--analytics"])
    with pytest.raises(SystemExit):
        dd.main()
