"""Test sandbox + fake casino fixtures (E0-S2).

Module top runs before any test imports `duckdice` or `tg`: path constants
are bound at import from DUCKDICE_* / DUCKDICE_TG_DATA / HOME, so they
must point at a temp dir first. Each test then gets its own tmp paths, a fresh
CLI `state`, and non-waiting sleeps.
"""

import asyncio
import os
import sys
import tempfile
import time

import pytest

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(_HERE)
_CASINO = os.path.dirname(_ROOT)
for _p in (_ROOT, _CASINO):
    if _p not in sys.path:
        sys.path.insert(0, _p)

_SANDBOX_HOME = tempfile.mkdtemp(prefix="duckdice_home_")
os.environ["HOME"] = _SANDBOX_HOME
os.environ["APP_ENV"] = "testing"
os.environ.pop("DUCKDICE_API_BASE", None)
os.environ["DUCKDICE_TG_DATA"] = os.path.join(_SANDBOX_HOME, "tg")
os.environ["DUCKDICE_LOG_DIR"] = os.path.join(_SANDBOX_HOME, "logs")
for _name in ("DB_PATH", "CONFIG_PATH", "STATE_PATH", "PID_PATH", "PRESET_PATH"):
    os.environ[f"DUCKDICE_{_name}"] = os.path.join(_SANDBOX_HOME, _name.lower())

import duckdice as dd  # noqa: E402
from tg import engine as tg_engine, database as tg_database  # noqa: E402

from .fakes import FakeCasino  # noqa: E402

_real_async_sleep = asyncio.sleep


@pytest.fixture
def sleeps():
    """Durations the code asked to sleep (CLI time.sleep + TG asyncio.sleep)."""
    return []


@pytest.fixture(autouse=True)
def _sandbox(tmp_path, monkeypatch, sleeps):
    for name in ("DB_PATH", "CONFIG_PATH", "STATE_PATH", "PID_PATH", "PRESET_PATH"):
        monkeypatch.setattr(dd, name, str(tmp_path / f"cli_{name.lower()}"))
    monkeypatch.setattr(tg_database, "DATA_DIR", str(tmp_path / "tg"))
    monkeypatch.setattr(dd, "state", dd.BotState())

    async def fast_sleep(delay, *a, **k):
        sleeps.append(delay)
        await _real_async_sleep(0)

    monkeypatch.setattr(dd.time, "sleep", sleeps.append)
    monkeypatch.setattr(tg_engine.asyncio, "sleep", fast_sleep)
    yield


@pytest.fixture
def fake_casino():
    return FakeCasino(balance=10.0, symbol="USDT", api_key="test-key")


@pytest.fixture
def cli_state(fake_casino, monkeypatch):
    """Factory: configured CLI BotState with a started session on the fake."""
    def make(max_bets=10, **overrides):
        api = fake_casino.requests_api()
        monkeypatch.setattr(dd.requests, "get", api.get)
        monkeypatch.setattr(dd.requests, "post", api.post)
        dd.init_db()
        s = dd.state
        s.api_key = fake_casino.api_key
        s.currency = fake_casino.symbol.lower()
        s.game = "dice"
        s.rule = "over"
        s.bet_value = 49.5
        s.multiplier = 2.0
        s.faucet = False
        s.strategy = "Flat Bet"
        s.strategy_key = "1"
        s.base_bet = s.current_bet = 0.01
        s.max_bets = max_bets
        bal = fake_casino.balance
        s.start_balance = s.current_balance = s.highest_balance = s.lowest_balance = bal
        for k, v in overrides.items():
            setattr(s, k, v)
        s.session_id = dd._db_start_session()
        s.session_start = time.time()
        s.running = True
        return s
    return make


@pytest.fixture
def run_cli():
    """Run the real CLI betting loop on the main thread until it stops."""
    def run():
        dd.betting_loop()
        return dd.state
    return run


@pytest.fixture
def tg_engine_factory(fake_casino, tmp_path, monkeypatch):
    """Factory: real TG BettingEngine whose HTTP client is the fake casino."""
    async def fake_recreate(self):
        if self._http:
            await self._http.aclose()
        self._http = fake_casino.async_client()

    monkeypatch.setattr(tg_engine.BettingEngine, "_recreate_http", fake_recreate)

    def make(max_bets=10, **cfg):
        config = {
            "currency": fake_casino.symbol.lower(), "rule": "over", "multiplier": 2.0,
            "bet_value": 49.5,
            "base_bet": 0.01, "strategy": "Flat Bet", "strategy_key": "1",
            "max_bets": max_bets,
        }
        config.update(cfg)
        e = tg_engine.BettingEngine(user_id=1, db_path=str(tmp_path / "tg.db"),
                                    api_key=fake_casino.api_key, config=config)
        e.stops, e.errors = [], []
        e.on_stop = e.stops.append
        e.on_error = e.errors.append
        return e
    return make


@pytest.fixture
def run_tg():
    """Start the engine and wait for its betting task to finish."""
    def run(engine):
        async def _run():
            assert await engine.start(), engine.last_error
            await engine._task
        asyncio.run(_run())
        return engine
    return run
