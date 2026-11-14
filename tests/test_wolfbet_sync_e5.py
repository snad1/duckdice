"""WolfBet sync E5-S5: duckdice CLI observability and tooling.

Mirrors WolfBet v2.17.0 / v2.30.3-v2.30.5 (metrics), v2.26.1 (--benchmark /
--diagnose), v2.26.0 (proxy, purge_days, --cleanup), v2.27.0 (async bet
writer), v2.18.0 (wizard Step 5b + presets), v2.19.0 (T tweak,
_apply_mutation), v2.22.1 / v2.32.1 (risky config + chain depth), v2.25.0
(--config-show / --config-set / --version) and v2.28.1 (monitor footer,
systemd-aware stop). Reference tests: test_phase3_metrics.py,
test_phase5i_diagnostics.py, test_phase5g_proxy.py, test_phase5h_purge_days.py,
test_phase5j_async_writer.py, test_phase4a_presets.py, test_phase4b_mutation.py,
test_phase5d_config_cli.py, test_phase7_polish.py, test_phase5m_monitor_ux.py,
test_phase5w/5x/5z. Plus: the API key (in every DuckDice URL query) never
reaches a log line, an error, the state file or printed output.
"""

import inspect
import io
import json
import os
import queue
import re
import sqlite3
import subprocess
import sys

import pytest
import requests
from rich.console import Console

import duckdice as cg

ANSI = re.compile(r"\x1b\[[0-9;]*m")
LONG_KEY = "ABCDEFGH1234567890SECRETKEY"


@pytest.fixture
def out(monkeypatch):
    """Capture rich console output as plain text."""
    buf = io.StringIO()
    monkeypatch.setattr(cg, "console", Console(file=buf, width=200, color_system=None))
    return buf


@pytest.fixture
def saved_config():
    """Write a minimal saved config; returns a writer for overrides."""
    def write(**over):
        data = {"api_key": LONG_KEY, "currency": "doge", "strategy": "Flat Bet",
                "strategy_key": "1", "base_bet": 0.01, "multiplier": 2.0}
        data.update(over)
        with open(cg.CONFIG_PATH, "w") as f:
            json.dump(data, f)
        return data
    return write


def _log_text():
    cg._log_handler.flush()
    path = os.path.join(cg.LOG_DIR, "duckdice.log")
    return open(path).read() if os.path.exists(path) else ""


# ── 1. Metrics ───────────────────────────────────────────
def test_metric_helpers_empty():
    assert cg._compute_api_avg() == 0
    assert cg._compute_api_p50() == 0
    assert cg._compute_api_p99() == 0
    assert cg._compute_on_target_pct() is None
    assert cg._compute_cycle_p50() == cg._compute_cycle_mean() == 0
    assert cg._compute_recent_bps() == 0
    assert cg._compute_theoretical_max_bps() == 0
    assert cg._compute_efficiency_pct() == 0


def test_record_api_ms_counters_and_on_target():
    cg.state.bet_delay = 0.1
    for ms in (50, 150, 80):
        cg._record_api_ms(ms)
    s = cg.state
    assert (s._last_api_ms, s._api_ms_count, s.peak_api_ms) == (80, 3, 150)
    assert cg._compute_api_avg() == pytest.approx(280 / 3)
    assert cg._compute_api_p50() == 80
    assert cg._compute_on_target_pct() == pytest.approx(200 / 3)


def test_api_window_saturates_at_500():
    for i in range(600):
        cg._record_api_ms(float(i))
    assert len(cg.state._api_samples) == 500
    assert cg._compute_api_p99() == 595   # window 100..599, index int(0.99*500)
    assert cg.state._api_ms_count == 600


def test_cycle_p50_resists_outlier_p99_catches_it():
    for _ in range(99):
        cg._record_cycle_ms(10.0)
    cg._record_cycle_ms(5000.0)
    assert cg._compute_cycle_p50() == 10
    assert cg._compute_cycle_p99() == 5000
    assert cg.state._cycle_ms == cg._compute_cycle_p50()


def test_overhead_floored_at_zero_and_uses_api_p50():
    cg.state.bet_delay = 0
    cg._record_api_ms(40)
    cg._record_cycle_ms(30)
    assert cg.state._cycle_overhead_ms == 0
    cg._record_cycle_ms(100)
    cg._record_cycle_ms(100)
    assert cg.state._cycle_overhead_ms == pytest.approx(60)


def test_theoretical_max_and_efficiency_capped():
    cg.state.bet_delay = 0.5
    cg._record_api_ms(100)
    assert cg._compute_theoretical_max_bps() == pytest.approx(2.0)   # delay bound
    cg.state.bet_delay = 0
    assert cg._compute_theoretical_max_bps() == pytest.approx(10.0)  # API bound
    cg._record_cycle_ms(100)
    assert cg._compute_recent_bps() == pytest.approx(10.0)
    assert cg._compute_efficiency_pct() == pytest.approx(100.0)
    cg._record_cycle_ms(10)                                          # faster than the API mean
    assert cg._compute_efficiency_pct() <= 100.0


def test_recent_bps_uses_cycle_mean():
    for ms in (100, 100, 400):
        cg._record_cycle_ms(ms)
    assert cg._compute_cycle_mean() == pytest.approx(200)
    assert cg._compute_recent_bps() == pytest.approx(5.0)
    assert "_compute_cycle_mean" in inspect.getsource(cg._compute_recent_bps)


def test_live_run_records_metrics_and_state_file(fake_casino, cli_state, run_cli):
    fake_casino.queue("win", "loss", "win", "loss", "win")
    cli_state(max_bets=5, db_write_mode="sync")
    run_cli()
    assert cg.state._api_ms_count == 5
    assert len(cg.state._cycle_samples) == 5
    cg._save_state_file()
    d = json.load(open(cg.STATE_PATH))
    for k in ("api_ms", "api_avg_ms", "api_peak_ms", "api_p50_ms", "api_p99_ms",
              "on_target_pct", "cycle_ms", "overhead_ms", "cycle_p50_ms", "cycle_p99_ms",
              "cycle_mean_ms", "cycle_sample_count", "bets_per_second_recent",
              "efficiency_pct", "theoretical_max_bps", "db_write_mode",
              "db_writer_stats", "db_writer_queue", "purge_days", "proxy"):
        assert k in d, k
    assert d["cycle_sample_count"] == 5
    assert d["proxy"] == "direct"


def test_dashboard_and_monitor_show_metric_rows(fake_casino, cli_state):
    cli_state()
    cg._record_api_ms(42)
    cg._record_cycle_ms(50)
    screen = ANSI.sub("", cg.build_dashboard_screen())
    assert "API last 42ms" in screen and "Efficiency" in screen and "ms/bet" in screen
    assert "[T]weak" in screen
    cg._save_state_file()
    d = json.load(open(cg.STATE_PATH))
    mon = ANSI.sub("", cg._build_monitor_screen(d, [], True))
    assert "API last 42ms" in mon and "Cycle" in mon


def test_status_shows_metrics_not_dead_rate_limit_row(fake_casino, cli_state, out):
    cli_state()
    cg._record_api_ms(42)
    cg._save_state_file()
    cg.cmd_status()
    text = out.getvalue()
    assert "API" in text and "Efficiency" in text and "of theoretical" in text
    assert "Rate Limit" not in text


# ── 2. --benchmark / --diagnose ──────────────────────────
def _stub_get(monkeypatch, status=200, fail=False):
    calls = []

    class R:
        status_code = status

    def get(url, timeout=None, **_):
        calls.append(url)
        if fail:
            raise requests.exceptions.ConnectionError(f"Max retries exceeded with url: {url}")
        return R()
    monkeypatch.setattr(cg._http_session, "get", get)
    return calls


def test_benchmark_probes_balance_endpoint_and_masks_key(monkeypatch, saved_config, out):
    saved_config()
    calls = _stub_get(monkeypatch)
    cg.cmd_benchmark(5)
    text = out.getvalue()
    assert len(calls) == 5
    assert all(u == f"{cg.API_BASE}/bot/stats/DOGE?api_key={LONG_KEY}" for u in calls)
    assert LONG_KEY not in text
    assert f"/bot/stats/DOGE?api_key={cg._mask_key(LONG_KEY)}" in text
    assert "API latency (ms)" in text and "5/5" in text
    assert "Theoretical max BPS from p50" in text
    assert "Network" in text


def test_benchmark_clamps_n(monkeypatch, saved_config, out):
    saved_config()
    calls = _stub_get(monkeypatch)
    cg.cmd_benchmark(1)
    assert len(calls) == 3
    calls.clear()
    cg.cmd_benchmark(999)
    assert len(calls) == 200
    calls.clear()
    cg.cmd_benchmark(None)
    assert len(calls) == 20


def test_benchmark_429_is_rate_limited(monkeypatch, saved_config, out):
    saved_config()
    _stub_get(monkeypatch, status=429)
    cg.cmd_benchmark(3)
    assert "Rate-limited" in out.getvalue()
    assert "DuckDice" in out.getvalue()


def test_benchmark_all_fail_exits_and_never_prints_key(monkeypatch, saved_config, out):
    saved_config()
    _stub_get(monkeypatch, fail=True)
    with pytest.raises(SystemExit) as e:
        cg.cmd_benchmark(3)
    assert e.value.code == 1
    assert "All requests failed" in out.getvalue()
    assert LONG_KEY not in out.getvalue()
    assert LONG_KEY not in _log_text()


def test_benchmark_without_config_exits(out):
    with pytest.raises(SystemExit) as e:
        cg.cmd_benchmark(3)
    assert e.value.code == 1


def test_diagnose_no_daemon(out):
    cg.cmd_diagnose()
    assert "No running daemon session found" in out.getvalue()


@pytest.mark.parametrize("state_extra, expect", [
    ({"api_p50_ms": 250, "overhead_ms": 5, "bet_delay": 0}, ["Network / API latency", "--benchmark 20"]),
    ({"api_p50_ms": 20, "overhead_ms": 5, "bet_delay": 0.5}, ["User-configured delay", "delay=0"]),
    ({"api_p50_ms": 20, "overhead_ms": 80, "bet_delay": 0, "db_write_mode": "sync"},
     ["CLI overhead", "dbmode=async"]),
    ({"api_p50_ms": 20, "overhead_ms": 80, "bet_delay": 0, "db_write_mode": "async"},
     ["shared-tenancy CPU/disk jitter"]),
])
def test_diagnose_advice(out, state_extra, expect):
    d = {"strategy": "Flat Bet", "bets_per_second": 3.0, "theoretical_max_bps": 4.0,
         "bets_per_second_recent": 5.0, "cycle_ms": 300, "cycle_p50_ms": 300,
         "cycle_p99_ms": 900, "cycle_sample_count": 50, "db_writer_stats": {}}
    d.update(state_extra)
    json.dump(d, open(cg.STATE_PATH, "w"))
    cg.cmd_diagnose()
    text = out.getvalue()
    for s in expect:
        assert s in text
    for s in ("Recent:", "Efficiency:", "median", "p99", "≈", "Measured cycle",
              "decomposition", "median of last", ">100%"):
        assert s in text
    assert "pipeline" not in text.lower()


# ── 3. Retention ─────────────────────────────────────────
def test_purge_days_default_clamp_off_alias():
    assert cg.state.purge_days == 3
    assert cg._TWEAK_ALIAS["purgedays"] == "purge_days"
    msgs = cg._apply_mutation({"purge_days": "90"})
    assert cg.state.purge_days == 30 and any(m.startswith("⚠") for m in msgs)
    cg._apply_mutation({"purge_days": "0"})
    assert cg.state.purge_days == 1
    cg._apply_mutation({"purge_days": "off"})
    assert cg.state.purge_days == 3
    with pytest.raises(ValueError):
        cg._apply_mutation({"purge_days": "abc"})


def test_cleanup_uses_shared_helper(monkeypatch, out):
    got = []
    monkeypatch.setattr(cg, "cleanup_old_bets", lambda path, bet_age_days: got.append(bet_age_days) or 7)
    cg.cmd_cleanup()
    cg.cmd_cleanup(10)
    cg.cmd_cleanup(99)
    assert got == [3, 10, 30]
    assert "Purged 7 bet rows" in out.getvalue()


def test_cleanup_purges_old_session_bets(out):
    cg.init_db()
    conn = sqlite3.connect(cg.DB_PATH)
    conn.execute("INSERT INTO sessions (id, started_at, ended_at) VALUES "
                 "(1, '2020-01-01T00:00:00', '2020-01-01T01:00:00')")
    conn.execute("INSERT INTO bets (session_id, timestamp) VALUES (1, '2020-01-01T00:10:00')")
    conn.commit()
    conn.close()
    cg.cmd_cleanup(3)
    assert sqlite3.connect(cg.DB_PATH).execute("SELECT COUNT(*) FROM bets").fetchone()[0] == 0
    assert sqlite3.connect(cg.DB_PATH).execute("SELECT COUNT(*) FROM sessions").fetchone()[0] == 1


def test_retention_worker_exits_when_stopped():
    cg.state.running = cg.state.paused = False
    cg._retention_worker()   # returns at once


# ── 4. Proxy ─────────────────────────────────────────────
def test_proxy_keys_everywhere():
    for coll in (cg._CONFIG_KEYS, cg._PRESET_KEYS, cg._LIVE_RELOAD_KEYS, cg._TWEAK_PARAMS):
        assert "proxy" in coll and "purge_days" in coll and "db_write_mode" in coll
    assert "proxy" in [f for f, _ in cg._CONFIG_SHOW_FIELDS]


def test_proxy_mutation_sets_and_clears_session():
    msgs = cg._apply_mutation({"proxy": "socks5://user:pw@10.0.0.1:1080"})
    assert cg._http_session.proxies == {"http": "socks5://user:pw@10.0.0.1:1080",
                                        "https": "socks5://user:pw@10.0.0.1:1080"}
    assert "pw" not in " ".join(msgs)
    cg._apply_mutation({"proxy": "off"})
    assert cg.state.proxy is None and cg._http_session.proxies == {}
    with pytest.raises(ValueError):
        cg._apply_mutation({"proxy": "ftp://x"})


def test_saved_proxy_applies_on_load_and_bad_one_is_dropped(saved_config):
    saved_config(proxy="http://u:p@1.2.3.4:8080")
    assert cg.load_config()
    assert cg._http_session.proxies["https"] == "http://u:p@1.2.3.4:8080"
    saved_config(proxy="gopher://nope")
    assert cg.load_config()
    assert cg.state.proxy is None and cg._http_session.proxies == {}


def test_fmt_config_value_masks_proxy():
    assert "pw" not in cg._fmt_config_value("proxy", "socks5://u:pw@h:1")
    assert "direct" in cg._fmt_config_value("proxy", None)


def test_no_bare_requests_calls():
    src = inspect.getsource(cg)
    assert not re.search(r"\brequests\.(get|post)\(", src)
    assert len(re.findall(r"_http_session\.(get|post)\(", src)) >= 5


# ── 5. Async bet writer ──────────────────────────────────
def _row(sid=1, i=0):
    return (sid, f"2026-11-14T00:00:{i % 60:02d}", 0.01, 2.0, "under", 0, 50.0, "win", 0.01, 10.0)


@pytest.fixture
def writer():
    cg.init_db()
    yield
    cg._stop_bet_writer(timeout=5.0)
    while not cg._bet_write_queue.empty():
        cg._bet_write_queue.get_nowait()


def _count_bets():
    return sqlite3.connect(cg.DB_PATH).execute("SELECT COUNT(*) FROM bets").fetchone()[0]


def test_writer_defaults_and_mutation():
    assert cg.state.db_write_mode == "async"
    assert cg._TWEAK_ALIAS["dbmode"] == "db_write_mode"
    cg._apply_mutation({"db_write_mode": "SYNC"})
    assert cg.state.db_write_mode == "sync"
    with pytest.raises(ValueError):
        cg._apply_mutation({"db_write_mode": "fast"})


def test_writer_pragmas():
    cg.init_db()
    conn = cg._db_conn()
    assert conn.execute("PRAGMA synchronous").fetchone()[0] == 1
    assert conn.execute("PRAGMA busy_timeout").fetchone()[0] == 5000


def test_writer_batches_land_and_stop_drains(writer):
    cg._start_bet_writer()
    for i in range(200):
        cg._bet_write_queue.put_nowait(_row(i=i))
    cg._stop_bet_writer(timeout=10.0)
    assert _count_bets() == 200
    assert cg._bet_writer_thread is None


def test_writer_queue_full_falls_back_to_sync(writer, monkeypatch):
    monkeypatch.setattr(cg, "_bet_write_queue", queue.Queue(maxsize=1))
    before = cg._bet_writer_stats["fallback_sync"]
    cg.state.session_id = 1
    cg._db_save_bet_async({"result_value": 1}, 0.01, 10.0)
    cg._db_save_bet_async({"result_value": 2}, 0.01, 10.0)   # full -> sync write
    assert cg._bet_writer_stats["fallback_sync"] == before + 1
    assert _count_bets() == 1


def test_dispatch_routing(writer, monkeypatch):
    seen = []
    monkeypatch.setattr(cg, "_db_save_bet", lambda *a: seen.append("sync"))
    monkeypatch.setattr(cg, "_db_save_bet_async", lambda *a: seen.append("async"))
    cg.state.db_write_mode = "async"
    cg._db_save_bet_dispatch({}, 0, 0)                 # writer not running -> sync
    cg._start_bet_writer()
    cg._db_save_bet_dispatch({}, 0, 0)                 # async
    cg.state.db_write_mode = "sync"
    cg._db_save_bet_dispatch({}, 0, 0)                 # sync
    assert seen == ["sync", "async", "sync"]


def test_make_bet_row_has_ten_columns():
    cg.state.session_id = 4
    row = cg._make_bet_row({"amount": 0.01, "multiplier": 2.0, "bet_value": 49.5,
                            "result_value": 12.5, "state": "loss"}, -0.01, 9.99)
    assert len(row) == 10 and row[0] == 4 and row[5] == 49.5 and row[6] == 12.5
    assert row[7] == "loss"


def test_live_run_async_writer_lands_all_rows(fake_casino, cli_state, run_cli, writer):
    fake_casino.queue("win", "loss", "win", "win", "loss", "win", "loss", "win")
    s = cli_state(max_bets=8)
    cg._start_bet_writer()
    run_cli()
    cg._stop_bet_writer(timeout=10.0)
    rows = sqlite3.connect(cg.DB_PATH).execute(
        "SELECT COUNT(*) FROM bets WHERE session_id=?", (s.session_id,)).fetchone()[0]
    assert rows == 8
    assert cg._bet_writer_stats["flushed"] >= 8


def test_writer_drained_before_session_save_on_exit():
    src = inspect.getsource(cg.main)
    for block in ("def _shutdown", "if args.daemon:\n        console.print"):
        tail = src[src.index(block):]
        assert tail.index("_stop_bet_writer") < tail.index("_db_save_session")


# ── 6. Live editing: presets, mutation, risky config ─────
def test_preset_round_trip_advanced_fields():
    s = cg.state
    s.recurring, s.recurring_delay = True, 120
    s.streak_reset_losses, s.streak_reset_delay = 7, 300
    s.streak_delay_loss, s.streakbet_loss = (3, 0.5), (5, 0.25)
    s.basebet_pct, s.milestone_bets = 0.0, 250
    s.proxy, s.purge_days, s.db_write_mode = "https://h:1", 9, "sync"
    cg.save_preset("adv")
    cg.state = cg.BotState()
    assert cg.load_preset("adv")
    s = cg.state
    assert (s.recurring, s.recurring_delay, s.streak_reset_losses) == (True, 120, 7)
    assert s.streak_delay_loss == (3, 0.5) and s.streakbet_loss == (5, 0.25)
    assert s.streak_delay_win == (0, 0.0)
    assert s.basebet_pct == 0.0 and s.milestone_bets == 250
    assert (s.proxy, s.purge_days, s.db_write_mode) == ("https://h:1", 9, "sync")
    raw = json.load(open(cg.PRESET_PATH))["adv"]
    assert raw["streak_delay_loss"] == "3:0.5" and raw["streak_delay_win"] is None


@pytest.mark.parametrize("param, value, attr, expect", [
    ("delay", "0.25", "bet_delay", 0.25),
    ("maxprofit", "0.5", "max_profit", 0.5),
    ("maxprofit", "off", "max_profit", None),
    ("maxbets", "100", "max_bets", 100),
    ("minbalance", "none", "stop_on_balance", None),
    ("sdloss", "3:0.5", "streak_delay_loss", (3, 0.5)),
    ("sbloss", "off", "streakbet_loss", (0, 0.0)),
    ("msbets", "0", "milestone_bets", 0),
    ("pi", "0.001", "profit_increment", 0.001),
    ("recurring", "on", "recurring", True),
    ("recdelay", "90000", "recurring_delay", 86400),
    ("srloss", "12", "streak_reset_losses", 12),
    ("srloss", "off", "streak_reset_losses", 0),
    ("lossmult", "2.5", "loss_mult", 2.5),
    ("mult", "3", "multiplier", 3.0),
])
def test_apply_mutation_fields(param, value, attr, expect):
    canonical = cg._TWEAK_ALIAS.get(param, param)
    assert canonical in cg._TWEAK_PARAMS
    msgs = cg._apply_mutation({canonical: value})
    assert getattr(cg.state, attr) == expect
    assert msgs


def test_apply_mutation_base_bet_updates_current_bet():
    cg._apply_mutation({"base_bet": "0.02"})
    assert cg.state.base_bet == cg.state.current_bet == 0.02


@pytest.mark.parametrize("change", [
    {"bet_delay": "x"}, {"srloss": "-1"}, {"streak_reset_losses": "x"}, {"multiplier": "1"},
    {"base_bet": "0"}, {"max_bets": "lots"},
])
def test_apply_mutation_rejects_bad_input(change):
    change = {cg._TWEAK_ALIAS.get(k, k): v for k, v in change.items()}
    with pytest.raises(ValueError):
        cg._apply_mutation(change)


def test_tweak_params_cover_aliases():
    assert set(cg._TWEAK_ALIAS.values()) <= cg._TWEAK_PARAMS
    assert "pipeline_depth" not in cg._TWEAK_PARAMS          # N/A: no pipelining


def test_show_tweak_applies_and_restores_pause(monkeypatch, out):
    monkeypatch.setattr("builtins.input", lambda *_: "delay 0.3")
    cg.state.paused = False
    cg.show_tweak()
    assert cg.state.bet_delay == 0.3 and cg.state.paused is False
    assert "Delay: 0.300s" in out.getvalue()


def _risky(**kw):
    s = cg.state
    s.strategy_key, s.loss_mult, s.multiplier = "1", 2.0, 2.0
    s.base_bet, s.current_balance = 0.0001, 10.0
    s.max_loss = s.max_profit = s.max_bets = None
    s.recurring, s.streak_reset_losses, s.basebet_pct, s.proxy = False, 0, 0.0, None
    for k, v in kw.items():
        setattr(s, k, v)
    return " | ".join(cg._check_risky_config())


def test_risky_config_clean_for_safe_flat():
    assert _risky() == ""


@pytest.mark.parametrize("kw, needle", [
    ({"strategy_key": "2", "loss_mult": 3.0, "max_loss": 1.0}, "Aggressive Martingale"),
    ({"strategy_key": "2"}, "No `max_loss` set"),
    ({"strategy_key": "7"}, "No `max_loss` set"),
    ({"strategy_key": "3"}, "Anti-Martingale"),
    ({"base_bet": 0.5}, "Base bet is 5.0%"),
    ({"multiplier": 20.0}, "Multiplier 20.0x"),
    ({"recurring": True}, "Recurring restart is on"),
    ({"strategy_key": "8"}, "Custom rules (Strategy 8)"),
    ({"basebet_pct": 0.1}, "basebet_pct = 10.0%"),
    ({"proxy": "http://u:p@h:1"}, "proxy scheme is http://"),
])
def test_risky_config_heuristics(kw, needle):
    assert needle in _risky(**kw)


def test_risky_chain_depth_warning():
    # base 1, mult 2, balance 10 -> 1+2+4 = 7 <= 10 < 15: a 3-loss chain
    txt = _risky(strategy_key="2", base_bet=1.0, current_balance=10.0, streak_reset_losses=5)
    assert "covers a 3-loss chain" in txt and "Loss 4 in a row" in txt
    assert "covers a" not in _risky(strategy_key="6", base_bet=1.0, streak_reset_losses=5)
    assert "covers a" not in _risky(strategy_key="2", base_bet=1.0, streak_reset_losses=5,
                                    max_loss=1.0)


def test_wizard_has_step_5b_before_presets():
    src = inspect.getsource(cg.setup_wizard)
    assert "Step 5b / 7 — Advanced session tuning (optional)" in src
    assert "step_stop_conditions, step_advanced_tuning, step_preset" in src
    assert "Heads-up — review before starting" in src
    assert "pipeline_depth" not in src


# ── config-show / config-set / version ───────────────────
def test_parse_config_set_pairs():
    assert cg._parse_config_set_pairs(["DELAY=0.1", "maxloss=2"]) == {"bet_delay": "0.1", "max_loss": "2"}
    for bad in (["delay"], ["nope=1"]):
        with pytest.raises(ValueError):
            cg._parse_config_set_pairs(bad)


@pytest.mark.parametrize("field, val, expect", [
    ("max_profit", None, "off"), ("recurring", True, "on"), ("streak_delay_loss", (3, 0.5), "3:0.5"),
    ("streak_delay_win", (0, 0.0), "off"), ("base_bet", 0.01, "0.01000000"), ("bet_delay", 0.25, "0.25"),
    ("custom_rules_text", "x" * 80, "…"),
])
def test_fmt_config_value(field, val, expect):
    assert expect in cg._fmt_config_value(field, val)


def test_config_show_masks_key(saved_config, out):
    saved_config()
    cg.cmd_config_show()
    text = out.getvalue()
    assert LONG_KEY not in text and cg._mask_key(LONG_KEY) in text and "(SET)" in text
    assert "purge_days" in text and "db_write_mode" in text


def test_config_show_without_config_exits(out):
    with pytest.raises(SystemExit) as e:
        cg.cmd_config_show()
    assert e.value.code == 1


def test_config_set_writes_disk_and_sighups_daemon(saved_config, out, monkeypatch):
    saved_config()
    with open(cg.PID_PATH, "w") as f:
        f.write("4242")
    sent = []
    monkeypatch.setattr(cg.os, "kill", lambda pid, sig: sent.append((pid, sig)))
    cg.cmd_config_set(["delay=0.2", "maxloss=0.5", "recdelay=99999"])
    data = json.load(open(cg.CONFIG_PATH))
    assert data["bet_delay"] == 0.2 and data["max_loss"] == 0.5 and data["recurring_delay"] == 86400
    assert data["api_key"] == LONG_KEY
    assert sent == [(4242, cg.signal.SIGHUP)]
    text = out.getvalue()
    assert "Config updated" in text and "Daemon reloaded live config" in text and "⚠" in text


def test_config_set_never_persists_env_key(saved_config, out, monkeypatch):
    saved_config()
    monkeypatch.setenv("DUCKDICE_API_KEY", "ENVKEY-0000000000000000")
    cg.cmd_config_set(["delay=0"])
    assert json.load(open(cg.CONFIG_PATH))["api_key"] == LONG_KEY


def test_config_set_restart_note_for_unsafe_keys(saved_config, out):
    saved_config()
    cg.cmd_config_set(["basebet=0.02"])
    assert "need a full restart" in out.getvalue()
    assert "Daemon not running" in out.getvalue()


@pytest.mark.parametrize("pairs", [["api_key=abc"], ["nope=1"], ["delay"], ["delay=abc"]])
def test_config_set_refuses(saved_config, out, pairs):
    saved_config()
    with pytest.raises(SystemExit) as e:
        cg.cmd_config_set(pairs)
    assert e.value.code == 2
    assert json.load(open(cg.CONFIG_PATH)).get("bet_delay") is None


def _run_cli(*args):
    env = dict(os.environ, APP_ENV="testing",
               PYTHONPATH=os.pathsep.join([os.path.dirname(os.path.dirname(cg.__file__)),
                                           os.path.dirname(cg.__file__)]))
    return subprocess.run([sys.executable, cg.__file__, *args], capture_output=True,
                          text=True, env=env, timeout=60)


def test_version_flag():
    r = _run_cli("--version")
    assert r.returncode == 0 and f"DuckDice AutoBot v{cg.VERSION}" in r.stdout


def test_help_has_examples_epilog():
    r = _run_cli("--help")
    assert r.returncode == 0 and "Examples:" in r.stdout and "--config-show" in r.stdout


def test_live_reload_applies_proxy_and_starts_writer(saved_config, writer):
    saved_config()
    cg.load_config()
    cg.state.running = True
    cg.state.db_write_mode = "sync"
    saved_config(proxy="https://h:3128", db_write_mode="async")
    applied = cg._reload_live_config()
    assert "proxy" in applied and cg._http_session.proxies["https"] == "https://h:3128"
    assert cg._bet_writer_thread is not None


# ── Monitor footer + systemd-aware stop ──────────────────
def test_monitor_footer_hints():
    src = inspect.getsource(cg._build_monitor_screen) + inspect.getsource(cg.cmd_monitor)
    assert "[P]ause [R]esume [Q]uit/Detach [S]top daemon" in src
    assert "--config-set KEY=VALUE" in src and "Keys:" in src
    assert "_stop_daemon_pid" in src


def test_is_systemd_managed(monkeypatch, tmp_path):
    assert cg._is_systemd_managed(None) is False
    assert cg._is_systemd_managed(999999999) is False
    real_open = open

    def fake_open(path, *a, **k):
        if str(path).startswith("/proc/"):
            p = tmp_path / "cgroup"
            p.write_text(content[0])
            return real_open(p, *a, **k)
        return real_open(path, *a, **k)
    content = ["0::/user.slice/app.slice/duckdice.service\n"]
    monkeypatch.setattr("builtins.open", fake_open)
    assert cg._is_systemd_managed(123) is True
    content[0] = "0::/user.slice/app.slice/duckdice-tg.service\n"
    assert cg._is_systemd_managed(123) is False
    content[0] = "0::/user.slice/session-1.scope\n"
    assert cg._is_systemd_managed(123) is False


def test_stop_uses_systemctl_when_managed(monkeypatch):
    ran, killed = [], []
    monkeypatch.setattr(cg, "_is_systemd_managed", lambda pid: True)
    monkeypatch.setattr(subprocess, "run", lambda cmd, **k: ran.append(cmd))
    monkeypatch.setattr(cg.os, "kill", lambda *a: killed.append(a))
    cg._stop_daemon_pid(55)
    assert ran == [["systemctl", "--user", "stop", "duckdice"]] and not killed
    monkeypatch.setattr(cg, "_is_systemd_managed", lambda pid: False)
    cg._stop_daemon_pid(55)
    assert killed == [(55, cg.signal.SIGTERM)]


# ── API key masking ──────────────────────────────────────
def test_redact_masks_key():
    cg.state.api_key = LONG_KEY
    assert cg._redact(f"url /dice/play?api_key={LONG_KEY}") == \
        f"url /dice/play?api_key={cg._mask_key(LONG_KEY)}"
    cg.state.api_key = ""
    assert cg._redact("nothing") == "nothing"


def test_bet_connection_error_never_leaks_key(fake_casino, cli_state, run_cli):
    fake_casino.api_key = LONG_KEY
    fake_casino.queue("win")
    s = cli_state(max_bets=1, db_write_mode="sync")
    real_post = cg._http_session.post
    calls = [0]

    def flaky(url, json=None, timeout=None, **k):
        calls[0] += 1
        if calls[0] == 1:
            raise requests.exceptions.ConnectionError(
                f"HTTPSConnectionPool(host='duckdice.io'): Max retries exceeded with url: {url}")
        return real_post(url, json=json, timeout=timeout)
    cg._http_session.post = flaky
    run_cli()
    assert s.total_bets == 1
    cg._save_state_file()
    assert LONG_KEY not in open(cg.STATE_PATH).read()
    log = _log_text()
    assert "UNEXPECTED ERROR placing bet" in log
    assert LONG_KEY not in log
    assert cg._mask_key(LONG_KEY) in log


def test_load_and_connect_failure_never_prints_key(saved_config, out, monkeypatch):
    saved_config()

    def boom(url, timeout=None, **_):
        raise requests.exceptions.ConnectionError(f"Max retries exceeded with url: {url}")
    monkeypatch.setattr(cg._http_session, "get", boom)
    with pytest.raises(SystemExit):
        cg._load_and_connect()
    assert "Connection failed" in out.getvalue()
    assert LONG_KEY not in out.getvalue()


def test_log_formatter_redacts_tracebacks():
    cg.state.api_key = LONG_KEY
    try:
        raise RuntimeError(f"boom {LONG_KEY}")
    except RuntimeError:
        cg.logger.exception("failure %s", LONG_KEY)
    log = _log_text()
    assert "boom" in log and LONG_KEY not in log


# ── DuckDice specifics ───────────────────────────────────
def test_tweak_base_bet_never_below_casino_minimum():
    cg.state.min_bet = 0.05
    msgs = cg._apply_mutation({"base_bet": "0.01"})
    assert cg.state.base_bet == 0.05 and cg.state.current_bet == 0.05
    assert any("minimum" in m for m in msgs)
    cg._apply_mutation({"base_bet": "0.1"})
    assert cg.state.base_bet == 0.1


def test_tweak_multiplier_rederives_chance():
    cg._apply_mutation({"multiplier": "3"})
    assert cg.state.multiplier == 3.0 and cg.state.bet_value == 33.0
    with pytest.raises(ValueError):
        cg._apply_mutation({"multiplier": "1"})


def test_faucet_not_tweakable():
    assert "faucet" not in cg._TWEAK_PARAMS
    assert "faucet" not in cg._TWEAK_ALIAS.values()


def test_stats_url_is_shared_probe():
    cg.state.api_key = LONG_KEY
    cg.state.currency = "btc"
    assert cg._stats_url() == f"{cg.API_BASE}/bot/stats/BTC?api_key={LONG_KEY}"
    for fn in (cg.api_get_balances, cg.cmd_benchmark):
        assert "_stats_url()" in inspect.getsource(fn)


def test_balance_probe_through_session_keeps_faucet(monkeypatch):
    cg.state.api_key = LONG_KEY
    cg.state.currency = "doge"
    seen = []

    class R:
        status_code = 200
        def raise_for_status(self): pass
        def json(self): return {"balances": {"main": "1.5", "faucet": "0.2"}}

    def get(url, headers=None, timeout=None, **_):
        seen.append(url)
        return R()
    monkeypatch.setattr(cg._http_session, "get", get)
    assert cg.api_get_balances() == [
        {"currency": "doge", "amount": 1.5, "faucet_amount": 0.2}]
    assert seen == [cg._stats_url()]


def test_http_error_body_with_key_is_redacted():
    cg.state.api_key = LONG_KEY
    msg = cg._redact(f"404 Client Error for url: {cg.API_BASE}/dice/play?api_key={LONG_KEY}")
    assert LONG_KEY not in msg and cg._mask_key(LONG_KEY) in msg
