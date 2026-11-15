"""BettingEngine: per-user betting instance running as async task."""

import asyncio
import json
import os
import time
import sqlite3
import httpx
from datetime import datetime
from collections import deque
from typing import Optional, List

from .config import API_BASE, APP_ENV, logger
from core.database import init_db, migrate_db, db_connect, cleanup_old_bets, cleanup_live_bets
from core.strategy import StrategyRule, load_rules_from_text
from core.engine import compute_next_bet, wolfrider_switch, apply_action, evaluate_rules
from shared.core.validation import InvalidNumber, finite_float
from shared.tg.redact import redact, register_secret
from shared.core.session_rules import (
    STREAK_RESET_REASON, record_outcome, streak_reset_triggered, streak_reset_stop_reason,
)
from shared.core.run_increment import (
    parse_scope, increment_on, load_run, save_run, run_profit_done,
    db_max_session_id, run_is_stale, resume_base_bet, next_milestone,
    check_profit_increment,
)

# ── Batching / periodic save constants ──────────────────
BET_BATCH_SIZE     = 50      # flush bets + session stats to DB every N bets
SESSION_SAVE_SECS  = 30      # fallback: save session stats every N seconds
REQUEST_TIMEOUT    = httpx.Timeout(15.0, connect=5.0)
MAX_TIMEOUT_STREAK = 3       # recreate HTTP session after this many consecutive timeouts
RATE_LIMIT_MAX_WAIT = 30.0   # cap on the 2^n 429 backoff (Retry-After overrides)
RATE_LIMIT_GIVE_UP  = 1800.0 # stop after this many cumulative seconds of 429 waits


MIN_BET_MAX_MULTIPLE = 10.0
MIN_BET_MAX_BALANCE_FRACTION = 0.25


def _bounded_min_bet(value, base_bet: float, balance: float) -> float | None:
    """Accept a server minimum only when it is finite and inside the configured caps."""
    try:
        amount = finite_float("minimum bet", value, minimum=0)
    except InvalidNumber:
        return None
    if amount <= 0:
        return None
    if base_bet > 0 and amount > base_bet * MIN_BET_MAX_MULTIPLE:
        return None
    if balance > 0 and amount > balance * MIN_BET_MAX_BALANCE_FRACTION:
        return None
    return amount


class BettingEngine:
    """Self-contained betting engine for one user. Runs in its own thread."""

    def __init__(self, user_id: int, db_path: str, api_key: str, config: dict):
        self.user_id = user_id
        self.db_path = db_path

        # ── config (use `or` to handle None from presets) ──
        self.api_key        = api_key
        register_secret(api_key)
        self.currency       = config.get("currency") or "usdt"
        self.rule           = config.get("rule") or "over"
        self.multiplier     = float(config.get("multiplier") or 2.01)
        self.initial_multiplier = self.multiplier
        self.bet_value      = float(config.get("bet_value") or 49.25)
        self.base_bet       = float(config.get("base_bet") or 0.0001)
        self.min_bet = 0.0    # server minimum learned from a 422
        self.current_bet    = self.base_bet
        self.strategy       = config.get("strategy") or "Martingale"
        self.strategy_key   = config.get("strategy_key") or "2"
        self.win_mult       = float(config.get("win_mult") or 1.0)
        self.loss_mult      = float(config.get("loss_mult") or 2.0)
        self.bet_delay      = float(config.get("bet_delay") or 0)
        self._last_bet_start_time: float = 0.0  # monotonic ts for bet_delay pacing
        # ── streak delay: pause after N consecutive wins/losses ──
        self.streak_delay_loss = self._parse_streak_delay(config.get("streak_delay_loss"))
        self.streak_delay_win  = self._parse_streak_delay(config.get("streak_delay_win"))
        # streak_delay_bets "N:seconds": every N total bets, delay X seconds (regardless of result)
        self.streak_delay_bets = self._parse_streak_delay(config.get("streak_delay_bets"))
        # ── adaptive base bet ──
        self.basebet_pct       = float(config.get("basebet_pct") or 0)
        self.streakbet_loss    = self._parse_streak_delay(config.get("streakbet_loss"))
        self.proxy          = config.get("proxy") or None
        self.delay_martin_threshold = int(config.get("delay_martin_threshold") or 3)
        self.faucet         = bool(config.get("faucet", False))

        # ── rules ──
        self.custom_rules: List[StrategyRule] = []
        rules_text = config.get("custom_rules_text", "")
        if rules_text:
            self.custom_rules = load_rules_from_text(rules_text)

        # ── stop conditions ──
        self.max_profit      = config.get("max_profit")
        self.max_loss        = config.get("max_loss")
        self.max_bets        = config.get("max_bets")
        self.max_wins        = config.get("max_wins")
        self.stop_on_balance = config.get("stop_on_balance")
        # Streak reset (WolfBet v2.32.0): stop on the first win after this many
        # consecutive losses (profit >= 0 only); handlers restart after
        # streak_reset_delay. The delay is kept here for /status only.
        self.streak_reset_losses = config.get("streak_reset_losses")
        self.streak_reset_delay  = config.get("streak_reset_delay")

        # ── session state ──
        self.running         = False
        self.paused          = False
        self.session_id      = None
        self.session_start   = 0.0
        self.session_bet_ended_at: Optional[float] = None
        self.session_pause_started_at: Optional[float] = None
        self.total_bets      = 0
        self.wins            = 0
        self.losses          = 0
        self.profit          = 0.0
        self.wagered         = 0.0
        self.start_balance   = 0.0
        self.current_balance = 0.0
        self.current_streak  = 0
        self.max_win_streak  = 0
        self.max_loss_streak = 0
        # Length of the loss streak the latest win just broke (0 if none).
        self.last_broken_loss_streak = 0
        self.highest_bet     = 0.0
        self.highest_win     = 0.0
        self.biggest_loss    = 0.0
        self.highest_balance = 0.0
        self.lowest_balance  = float("inf")
        self.bets_per_second = 0.0
        self.bets_per_minute = 0.0
        self.peak_bps        = 0.0
        self.low_bps         = float("inf")
        self.peak_bpm        = 0.0
        self.low_bpm         = float("inf")
        self._bets_this_sec  = 0
        self._bets_this_min  = 0
        self._current_sec    = 0
        self._current_min    = 0
        # Sliding 60s window: what the bot is doing RIGHT NOW (vs. session average above)
        self.recent_bps      = 0.0
        self.recent_bpm      = 0.0
        self._recent_bet_times: deque = deque(maxlen=2000)
        # Per-cycle overhead (engine + event-loop scheduler), exp-smoothed ms
        # Answers: "is something blocking the bot or is this normal Python overhead?"
        self._cycle_overhead_ms: float = 0.0
        self._iter_start_mono: float = 0.0  # set at top of loop iteration
        # Full per-bet cycle time (T_start_N - T_start_N-1), exp-smoothed ms.
        # This is the bps reciprocal: Cycle*bps ≈ 1000. Honest about scheduler jitter.
        self._cycle_ms: float = 0.0
        # get_status() cache: milestone notifier + /status often called within ms
        # of each other; cache the dict briefly to avoid rebuilding 50 fields twice
        self._status_cache: Optional[dict] = None
        self._status_cache_ts: float = 0.0
        self.profit_history  = deque(maxlen=40)
        self.profit_history.append(0.0)
        self.recent_bets     = deque(maxlen=5)
        self._chart_snapshots: list = []    # [[bet_num, profit, balance], ...]
        self.status          = "Idle"
        self.last_error      = ""
        self.stop_reason     = ""

        # ── rate limiting ──
        # 429: Retry-After if sent, else 2^n s capped at 30 s; give up after
        # 1800 s of cumulative waiting (same as cryptogames TG).
        self._was_rate_limited      = False
        self._rate_limit_wait       = 0.0
        self._consecutive_429       = 0
        self._rate_limit_total_wait = 0.0
        self.consecutive_errors = 0
        self._consecutive_timeouts = 0
        self.backoff_delay   = 1.0
        self._insufficient_balance = False
        self._last_api_ms          = 0.0
        self._api_ms_total         = 0.0
        self._api_ms_count         = 0
        self.peak_api_ms: float    = 0.0  # max single-bet API response time this session
        self._api_samples: deque   = deque(maxlen=200)  # ring buffer for p50/median calc
        self._on_target_count: int = 0  # bets where api_ms ≤ bet_delay (the happy path)

        # ── strategy internals ──
        self.dalembert_unit  = 0
        self.paroli_count    = 0

        # ── profit-based base bet increment ──
        self.profit_increment     = config.get("profit_increment")    # amount to add to base_bet
        self.profit_threshold     = config.get("profit_threshold")    # profit interval that triggers
        self.next_profit_milestone = float(self.profit_threshold) if self.profit_threshold else 0.0
        # v1.10.0 (WolfBet v2.37.0): run-wide scope + its persisted run file
        self.profit_increment_scope = config.get("profit_increment_scope") or "session"
        self.run_first_session: Optional[int] = None
        self.run_since: Optional[str] = None
        self.run_bumps: int = 0
        self.run_profit_done: float = 0.0
        # Bumps already inside the base bet this engine was built with (a
        # recurring restart carries the bumped base): never added twice.
        self._run_bumps_in_base = int(config.get("_run_bumps_in_base") or 0)

        # ── milestone config ──
        self.milestone_bets   = int(config.get("milestone_bets")) if config.get("milestone_bets") is not None else 100
        self.milestone_wins   = int(config.get("milestone_wins") or 0)
        self.milestone_losses = int(config.get("milestone_losses") or 0)
        self.milestone_profit = float(config.get("milestone_profit") or 0)
        self._last_profit_milestone = 0.0  # track last profit threshold crossed

        # ── callbacks ──
        self.on_stop         = None   # called with (reason: str)
        self.on_milestone    = None   # called every N bets/wins/losses/profit
        self.on_error        = None

        # ── batching state ──
        self._bet_queue: list = []             # pending bet rows
        self._last_session_save = 0.0
        self._last_cleanup = 0.0
        self.purge_days = int(config.get("purge_days") or 1)          # last time session was saved
        self._db_connection: Optional[sqlite3.Connection] = None  # persistent connection

        # ── async HTTP client (reuses TCP/TLS connections) ──
        self._http: Optional[httpx.AsyncClient] = None

        # ── async task ──
        self._task: Optional[asyncio.Task] = None

    # ── API ──────────────────────────────────────────────
    def _api_url(self, endpoint: str) -> str:
        """Build full API URL with api_key query parameter."""
        return f"{API_BASE}{endpoint}?api_key={self.api_key}"

    async def _recreate_http(self):
        """Close and recreate the HTTP client to recover from stale connections."""
        try:
            if self._http:
                await self._http.aclose()
        except Exception:
            pass
        self._http = httpx.AsyncClient(
            headers=self._headers(), timeout=REQUEST_TIMEOUT,
            http2=False, follow_redirects=False,
            proxy=self.proxy if self.proxy else None,
        )
        logger.info("User %d: HTTP client recreated", self.user_id)

    def _headers(self) -> dict:
        return {
            "Content-Type": "application/json",
        }

    async def api_get_balances(self) -> list:
        if not self._http:
            await self._recreate_http()
        symbol = self.currency.upper()
        r = await self._http.get(self._api_url(f"/bot/stats/{symbol}"))
        r.raise_for_status()
        data = r.json()
        balances_data = data.get("balances", {})
        main_bal = float(balances_data.get("main", 0))
        faucet_bal = float(balances_data.get("faucet", 0))
        return [{"currency": symbol.lower(), "amount": main_bal, "faucet_amount": faucet_bal}]

    @staticmethod
    def _parse_streak_delay(value) -> tuple:
        """Parse 'N:seconds' streak delay config. Returns (streak_count, delay_seconds) or (0, 0)."""
        if not value:
            return (0, 0.0)
        try:
            parts = str(value).split(":")
            return (int(parts[0]), float(parts[1]))
        except (IndexError, ValueError):
            return (0, 0.0)

    def _apply_basebet_pct(self) -> bool:
        """If basebet_pct configured, recompute base_bet from current balance. Returns True if changed."""
        if self.basebet_pct <= 0 or self.current_balance <= 0:
            return False
        new_base = round(self.current_balance * self.basebet_pct, 8)
        if new_base <= 0:
            return False
        if abs(new_base - self.base_bet) < 1e-12:
            return False
        self.base_bet = max(self.min_bet, new_base)
        self.current_bet = self.base_bet
        return True

    def _get_streakbet_loss_factor(self) -> float:
        """Return current_bet multiplier when on a qualifying loss streak. 1.0 = no change."""
        streak = self.current_streak
        count, mult = self.streakbet_loss
        if count > 0 and streak < 0 and abs(streak) > 0 and abs(streak) % count == 0:
            return mult
        return 1.0

    def _get_streak_delay(self) -> float:
        """Check if current streak/total-bets triggers a delay. Returns max applicable delay or 0."""
        streak = self.current_streak
        delay = 0.0
        if streak < 0 and self.streak_delay_loss[0] > 0:
            count, d = self.streak_delay_loss
            if abs(streak) > 0 and abs(streak) % count == 0:
                delay = max(delay, d)
        if streak > 0 and self.streak_delay_win[0] > 0:
            count, d = self.streak_delay_win
            if streak % count == 0:
                delay = max(delay, d)
        if self.streak_delay_bets[0] > 0 and self.total_bets > 0:
            count, d = self.streak_delay_bets
            if self.total_bets % count == 0:
                delay = max(delay, d)
        return delay

    def _set_error(self, friendly: str, technical: str = ""):
        """Set last_error: friendly message in production, technical details in dev."""
        if APP_ENV != "production" and technical:
            self.last_error = technical
        else:
            self.last_error = friendly
        if technical:
            logger.warning("User %d: %s", self.user_id, redact(technical))

    async def _api_place_bet(self, amount: float) -> Optional[dict]:
        if self.current_balance > 0 and amount > self.current_balance:
            self._set_error("Insufficient balance", f"bet {amount:.8f} > balance {self.current_balance:.8f}")
            self._insufficient_balance = True
            return None
        if not self._http:
            await self._recreate_http()
        payload = {
            "symbol": self.currency.upper(),
            "chance": str(self.bet_value),
            "isHigh": self.rule == "over",
            "amount": str(amount),
            "faucet": self.faucet,
        }
        try:
            t0 = time.time()
            r = await self._http.post(self._api_url("/dice/play"), json=payload)
            self._last_api_ms = (time.time() - t0) * 1000
            if self._last_api_ms > self.peak_api_ms:
                self.peak_api_ms = self._last_api_ms
            self._api_samples.append(self._last_api_ms)
            if self.bet_delay > 0 and self._last_api_ms <= self.bet_delay * 1000:
                self._on_target_count += 1
            self._consecutive_timeouts = 0
            if r.status_code == 200:
                data = r.json()
                if "error" in data:
                    err_msg = data["error"] if isinstance(data["error"], str) else data["error"].get("message", "")
                    if "insufficient balance" in err_msg.lower():
                        self._set_error("Insufficient balance")
                        self._insufficient_balance = True
                        return None
                    self._set_error("Bet rejected by API", f"API error: {str(err_msg)[:120]}")
                    return None
                self._api_ms_total += self._last_api_ms
                self._api_ms_count += 1
                bet = data.get("bet", {})
                user = data.get("user", {})
                is_win = bet.get("result", False)
                bet_amount = float(bet.get("betAmount", amount))
                raw_profit = float(bet.get("profit", 0)) if is_win else -bet_amount
                return {
                    "bet": {
                        "state": "win" if is_win else "loss",
                        "result_value": float(bet.get("number", 0)),
                        "multiplier": float(bet.get("payout", 0)),
                        "amount": bet_amount,
                        "bet_value": self.bet_value,
                        "profit": raw_profit,
                    },
                    "userBalance": {
                        "amount": float(user.get("balance", self.current_balance)),
                    },
                }
            elif r.status_code == 422:
                try:
                    err_data = r.json()
                    err_msg = err_data.get("error", "")
                    params = err_data.get("params", {})
                    min_amount = params.get("amount")
                    if min_amount and "minimum bet" in err_msg.lower():
                        min_bet = _bounded_min_bet(min_amount, self.base_bet, self.current_balance)
                        if min_bet is None:
                            self._set_error("Server minimum bet is out of range")
                            self.running = False
                            self.stop_reason = "API minimum bet rejected as out of range"
                            return None
                        self.min_bet = min_bet
                        self.base_bet = min_bet
                        self.current_bet = min_bet
                        self._set_error(f"Min bet adjusted to {min_bet}")
                        return None
                except (ValueError, KeyError):
                    pass
                self._set_error("Validation error", f"Validation error: {r.text[:120]}")
                self.running = False
                self.stop_reason = f"API validation error: {r.text[:120]}"
            elif r.status_code == 429:
                self._was_rate_limited = True
                try:
                    self._rate_limit_wait = float(r.headers.get("Retry-After", 0))
                except (TypeError, ValueError):
                    self._rate_limit_wait = 0.0
                self._set_error("Rate limit reached: cooling down",
                                f"HTTP 429 on bet #{self.total_bets + 1} "
                                f"(Retry-After={r.headers.get('Retry-After', 'n/a')})")
            else:
                body = r.text or ""
                if "insufficient" in body.lower():
                    self._set_error("Insufficient balance")
                    self._insufficient_balance = True
                else:
                    self._set_error("Bet request failed", f"HTTP {r.status_code}: {body[:120]}")
        except (httpx.TimeoutException, httpx.ConnectError):
            self._consecutive_timeouts += 1
            self._set_error("Connection timeout: retrying")
            if self._consecutive_timeouts >= MAX_TIMEOUT_STREAK:
                logger.warning("User %d: %d consecutive timeouts: recreating HTTP client",
                               self.user_id, self._consecutive_timeouts)
                await self._recreate_http()
                self._consecutive_timeouts = 0
        except Exception as e:
            err_str = str(e).lower()
            if "insufficient" in err_str:
                self._set_error("Insufficient balance")
                self._insufficient_balance = True
            else:
                self._set_error("Unexpected error", str(e)[:120])
        return None

    # ── DB ───────────────────────────────────────────────
    def _get_conn(self) -> sqlite3.Connection:
        """Return persistent DB connection, creating if needed.

        Opens with check_same_thread=False directly (not via shared db_connect)
        so the engine survives a stale casino-shared install. The connection
        is reused across asyncio.to_thread() calls; the engine owns it.
        """
        if self._db_connection is None:
            self._db_connection = sqlite3.connect(
                self.db_path, timeout=5, check_same_thread=False)
            self._db_connection.execute("PRAGMA journal_mode=WAL")
            # v1.7.0: mirrors wolfbet v2.27.0 tuning. WAL-safe.
            self._db_connection.execute("PRAGMA synchronous = NORMAL")
            self._db_connection.execute("PRAGMA busy_timeout = 5000")
            self._db_connection.execute("PRAGMA cache_size = -32000")
            self._db_connection.execute("PRAGMA temp_store = MEMORY")
        return self._db_connection

    def _close_conn(self):
        """Close the persistent connection."""
        if self._db_connection:
            try:
                self._db_connection.close()
            except Exception:
                pass
            self._db_connection = None

    def _build_config_snapshot(self) -> str:
        """Build a JSON snapshot of session config for history."""
        snap = {
            "strategy_key": self.strategy_key,
            "loss_mult": self.loss_mult,
            "win_mult": self.win_mult,
            "bet_delay": self.bet_delay,
            "rule": self.rule,
            "faucet": self.faucet,
            "bet_value": self.bet_value,
            "basebet_pct": self.basebet_pct or None,
            "streak_delay_loss": f"{self.streak_delay_loss[0]}:{self.streak_delay_loss[1]}" if self.streak_delay_loss[0] > 0 else None,
            "streak_delay_win": f"{self.streak_delay_win[0]}:{self.streak_delay_win[1]}" if self.streak_delay_win[0] > 0 else None,
            "streak_delay_bets": f"{self.streak_delay_bets[0]}:{self.streak_delay_bets[1]}" if self.streak_delay_bets[0] > 0 else None,
            "streakbet_loss": f"{self.streakbet_loss[0]}:{self.streakbet_loss[1]}" if self.streakbet_loss[0] > 0 else None,
            "milestone_bets": self.milestone_bets,
        }
        if self.strategy_key == "7":  # Delay Martingale
            snap["delay_threshold"] = self.delay_martin_threshold
        stops = {}
        if self.max_profit is not None: stops["max_profit"] = self.max_profit
        if self.max_loss is not None:   stops["max_loss"] = self.max_loss
        if self.max_bets is not None:   stops["max_bets"] = self.max_bets
        if self.max_wins is not None:   stops["max_wins"] = self.max_wins
        if self.stop_on_balance is not None: stops["min_balance"] = self.stop_on_balance
        if self.streak_reset_losses: stops["streak_reset_losses"] = self.streak_reset_losses
        if stops:
            snap["stops"] = stops
        if self.profit_threshold and self.profit_increment:
            snap["profit_threshold"] = self.profit_threshold
            snap["profit_increment"] = self.profit_increment
        if self.custom_rules:
            snap["rules"] = [r.to_dict() for r in self.custom_rules]
        return json.dumps(snap)

    def _db_start_session(self) -> int:
        # Use a temporary connection: start() runs in the main thread,
        # but the persistent connection must be created in the betting thread.
        snapshot = self._build_config_snapshot()
        conn = db_connect(self.db_path)
        c = conn.cursor()
        c.execute("""
            INSERT INTO sessions (started_at, currency, strategy, base_bet, multiplier,
                                  start_balance, config_snapshot)
            VALUES (?,?,?,?,?,?,?)
        """, (datetime.now().isoformat(), self.currency, self.strategy,
              self.base_bet, self.multiplier, self.start_balance, snapshot))
        conn.commit()
        sid = c.lastrowid
        conn.close()
        return sid

    # ── RUN-WIDE PROFIT INCREMENT (WolfBet v2.37.0) ─────
    # One run per user in run.json next to the user's DB; one slot at a time,
    # since these sessions carry no slot to filter the sum by.
    def _run_path(self) -> str:
        return os.path.join(os.path.dirname(self.db_path) or ".", "run.json")

    def _increment_on(self) -> bool:
        return increment_on(self.profit_increment, self.profit_threshold)

    def run_profit_total(self) -> float:
        return self.run_profit_done + self.profit

    def _save_run(self) -> None:
        if self.run_first_session is None:
            return
        try:
            save_run(self._run_path(), {"first_session": self.run_first_session,
                                        "started_at": self.run_since,
                                        "bumps": self.run_bumps})
        except OSError as e:
            logger.warning("User %d: run file save failed: %s", self.user_id, e)

    def _run_profit_done_from_db(self, first_sid: int) -> float:
        try:
            conn = db_connect(self.db_path)
            try:
                return run_profit_done(conn, first_sid)
            finally:
                conn.close()
        except Exception as e:
            logger.warning("User %d: run profit query failed: %s", self.user_id, e)
            return 0.0

    def _db_max_session_id(self) -> int:
        try:
            conn = db_connect(self.db_path)
            try:
                return db_max_session_id(conn)
            finally:
                conn.close()
        except Exception as e:
            logger.warning("User %d: session id query failed: %s", self.user_id, e)
            return 0

    def _init_run(self) -> None:
        """Restore (or prepare) the run before the first session starts."""
        self.run_first_session = None
        self.run_since = None
        self.run_bumps = 0
        self.run_profit_done = 0.0
        if self.profit_increment_scope != "run" or not self._increment_on():
            self._end_run("increment off" if self.profit_increment_scope == "run"
                          else "scope session")
            return
        run = load_run(self._run_path())
        if run and run_is_stale(run, self._db_max_session_id()):
            logger.warning("User %d: RUN file points past the stats DB (reset?); "
                           "starting a new run", self.user_id)
            run = None
        if run:
            self.run_first_session = run["first_session"]
            self.run_since = run["started_at"]
            self.run_bumps = run["bumps"]
            self.run_profit_done = self._run_profit_done_from_db(run["first_session"])
            # A recurring restart already carries the bumped base in config.
            missing = max(0, self.run_bumps - self._run_bumps_in_base)
            if missing:
                self.base_bet = resume_base_bet(self.base_bet, missing,
                                                self.profit_increment)
                self.current_bet = self.base_bet
        if self.profit_threshold:
            self.next_profit_milestone = next_milestone(self.run_bumps, self.profit_threshold)
        logger.info("User %d: RUN %s since #%s bumps=%d run profit=%+.8f base_bet=%.8f",
                    self.user_id, "resumed" if run else "new",
                    self.run_first_session or "next", self.run_bumps,
                    self.run_profit_done, self.base_bet)

    def _end_run(self, reason: str) -> None:
        had = self.run_first_session is not None
        if os.path.exists(self._run_path()):
            try:
                os.remove(self._run_path())
                had = True
            except OSError as e:
                logger.warning("User %d: run file remove failed: %s", self.user_id, e)
        self.run_first_session = None
        self.run_since = None
        self.run_bumps = 0
        self.run_profit_done = 0.0
        if had:
            logger.info("User %d: RUN ended (%s); the next run starts fresh",
                        self.user_id, reason)

    def _attach_run(self, session_id: int) -> None:
        if (self.profit_increment_scope == "run" and self._increment_on()
                and self.run_first_session is None):
            self.run_first_session = session_id
            self.run_since = datetime.now().isoformat()
            self._save_run()

    def reset_run(self) -> None:
        """Drop the run now: base bet back by the bumps, a fresh run on this session."""
        bumps = self.run_bumps
        self._end_run("run reset by user")
        if bumps and self.profit_increment:
            self.base_bet = max(0.0, round(self.base_bet - bumps * self.profit_increment, 8))
            self.current_bet = self.base_bet
        if self.profit_threshold:
            self.next_profit_milestone = self.profit_threshold
        self._attach_run(self.session_id)

    def _check_profit_increment(self) -> None:
        """Bump base_bet when profit reaches the next milestone (session or run scope)."""
        if not self._increment_on():
            return
        run = self.profit_increment_scope == "run"
        bumped, base_bet, next_ms, bumps = check_profit_increment(
            self.run_profit_total() if run else self.profit,
            self.next_profit_milestone, self.profit_threshold,
            self.profit_increment, self.base_bet, self.run_bumps)
        if not bumped:
            return
        # basebet_pct sizes the base from the balance; keep that rule here.
        if self.basebet_pct > 0 and self.current_balance > 0:
            base_bet = round(self.current_balance * self.basebet_pct, 8)
        self.base_bet, self.next_profit_milestone = base_bet, next_ms
        if run:
            self.run_bumps = bumps
            self._save_run()
        logger.info("User %d: %s base_bet -> %.8f (next at %.8f profit)",
                    self.user_id, "RUN PROFIT INCREMENT" if run else "PROFIT INCREMENT",
                    self.base_bet, self.next_profit_milestone)

    def _queue_bet(self, bet_data: dict, profit: float, balance: float):
        """Queue a bet row for batched insertion.

        Flush is intentionally NOT triggered here even when the queue passes
        BET_BATCH_SIZE: _periodic_save (called from the async loop) handles
        flushing via asyncio.to_thread so DB I/O never blocks the event loop.
        Queue growing past 50 between saves is harmless (~6 KB per 50 rows;
        even 1000 queued ≈ 128 KB).
        """
        self._bet_queue.append((
            self.session_id, datetime.now().isoformat(),
            float(bet_data.get("amount", 0)), float(bet_data.get("multiplier", 0)),
            self.rule, float(bet_data.get("bet_value", 0)),
            float(bet_data.get("result_value", 0)), bet_data.get("state", ""),
            profit, balance,
        ))

    def _flush_bets(self):
        """Flush all queued bets to DB and save session stats in one go."""
        if not self._bet_queue:
            return
        try:
            conn = self._get_conn()
            conn.executemany("""
                INSERT INTO bets (session_id, timestamp, amount, multiplier, rule,
                    bet_value, result_value, state, profit, balance_after)
                VALUES (?,?,?,?,?,?,?,?,?,?)
            """, self._bet_queue)
            conn.commit()
            self._bet_queue.clear()
        except Exception as e:
            logger.error("User %d: DB flush_bets failed: %s", self.user_id, e)
        # always save session stats when flushing bets: keeps both in sync
        self._db_save_session()

    def _db_save_session(self, final: bool = False):
        """Save session stats. If final=True, also set ended_at."""
        try:
            conn = self._get_conn()
            lo_bal = self.lowest_balance if self.lowest_balance != float("inf") else self.current_balance
            if final:
                conn.execute("""
                    UPDATE sessions SET
                        ended_at=?, total_bets=?, wins=?, losses=?,
                        profit=?, wagered=?, end_balance=?,
                        max_win_streak=?, max_loss_streak=?,
                        highest_balance=?, lowest_balance=?,
                        highest_win=?, biggest_loss=?,
                        bets_per_minute=?, bets_per_second=?,
                        peak_bps=?, low_bps=?, peak_bpm=?, low_bpm=?,
                        chart_snapshots=?
                    WHERE id=?
                """, (
                    # v1.7.0: freeze ended_at during pauses.
                    (datetime.fromtimestamp(self.session_bet_ended_at).isoformat()
                     if self.session_bet_ended_at else datetime.now().isoformat()),
                    self.total_bets, self.wins, self.losses,
                    self.profit, self.wagered, self.current_balance,
                    self.max_win_streak, self.max_loss_streak,
                    self.highest_balance, lo_bal,
                    self.highest_win, self.biggest_loss,
                    self.bets_per_second * 60 if self.bets_per_second else self.bets_per_minute,
                    self.bets_per_second,
                    self.peak_bps,
                    self.low_bps if self.low_bps != float("inf") else 0,
                    self.peak_bpm,
                    self.low_bpm if self.low_bpm != float("inf") else 0,
                    json.dumps(self._chart_snapshots[-2000:]),
                    self.session_id,
                ))
            else:
                conn.execute("""
                    UPDATE sessions SET
                        total_bets=?, wins=?, losses=?,
                        profit=?, wagered=?, end_balance=?,
                        max_win_streak=?, max_loss_streak=?,
                        highest_balance=?, lowest_balance=?,
                        highest_win=?, biggest_loss=?,
                        bets_per_minute=?, bets_per_second=?,
                        peak_bps=?, low_bps=?, peak_bpm=?, low_bpm=?,
                        chart_snapshots=?
                    WHERE id=?
                """, (
                    self.total_bets, self.wins, self.losses,
                    self.profit, self.wagered, self.current_balance,
                    self.max_win_streak, self.max_loss_streak,
                    self.highest_balance, lo_bal,
                    self.highest_win, self.biggest_loss,
                    self.bets_per_second * 60 if self.bets_per_second else self.bets_per_minute,
                    self.bets_per_second,
                    self.peak_bps,
                    self.low_bps if self.low_bps != float("inf") else 0,
                    self.peak_bpm,
                    self.low_bpm if self.low_bpm != float("inf") else 0,
                    json.dumps(self._chart_snapshots[-2000:]),
                    self.session_id,
                ))
            conn.commit()
            self._last_session_save = time.time()
        except Exception as e:
            logger.error("User %d: DB save_session failed: %s", self.user_id, e)

    def _update_speed_stats(self):
        """Update BPS/BPM tracking: called after each bet result.

        Maintains both:
          - bets_per_second / bets_per_minute: cumulative session average
          - recent_bps / recent_bpm: sliding 60s window (what's happening NOW)
        """
        now = time.time()
        elapsed = now - self.session_start
        if elapsed > 0:
            self.bets_per_second = self.total_bets / elapsed
            self.bets_per_minute = self.bets_per_second * 60

        # Sliding 60s window: drop entries older than 60s and recompute
        mono = time.monotonic()
        self._recent_bet_times.append(mono)
        cutoff = mono - 60.0
        while self._recent_bet_times and self._recent_bet_times[0] < cutoff:
            self._recent_bet_times.popleft()
        self.recent_bps = len(self._recent_bet_times) / 60.0
        self.recent_bpm = self.recent_bps * 60

        sec_key = int(now)
        min_key = int(now) // 60
        if sec_key != self._current_sec:
            if self._current_sec > 0 and self._bets_this_sec > 0:
                if self._bets_this_sec > self.peak_bps:
                    self.peak_bps = self._bets_this_sec
                if self._bets_this_sec < self.low_bps:
                    self.low_bps = self._bets_this_sec
            self._current_sec = sec_key
            self._bets_this_sec = 1
        else:
            self._bets_this_sec += 1
        if min_key != self._current_min:
            if self._current_min > 0 and self._bets_this_min > 0:
                if self._bets_this_min > self.peak_bpm:
                    self.peak_bpm = self._bets_this_min
                if self._bets_this_min < self.low_bpm:
                    self.low_bpm = self._bets_this_min
            self._current_min = min_key
            self._bets_this_min = 1
        else:
            self._bets_this_min += 1

    async def _periodic_save(self):
        """Flush bets and save session stats periodically.

        DB calls run in a worker thread so the event loop stays free for other
        engines to keep betting: without this, 6 concurrent sessions
        serialize on each other's flushes and bps collapses to ~1/N.
        """
        now = time.time()
        if now - self._last_session_save >= SESSION_SAVE_SECS:
            if self._bet_queue:
                await asyncio.to_thread(self._flush_bets)
            else:
                await asyncio.to_thread(self._db_save_session)

        # Live cleanup: purge old bets every hour to prevent DB bloat
        if now - self._last_cleanup >= 3600:
            try:
                deleted = await asyncio.to_thread(
                    cleanup_live_bets, self.db_path, self.purge_days)
                if deleted > 0:
                    logger.info("User %d: Live cleanup: deleted %d old bets (>%dd)",
                                self.user_id, deleted, self.purge_days)
                self._last_cleanup = now
            except Exception as e:
                logger.warning("User %d: Live cleanup failed: %s", self.user_id, e)

    # ── STRATEGY (delegates to core.engine) ────────────────
    def _compute_next_bet(self, last_result: str) -> float:
        nxt, self.dalembert_unit, self.paroli_count = compute_next_bet(
            self.strategy_key, self.base_bet, self.current_bet,
            self.loss_mult, self.win_mult, last_result,
            self.current_streak, self.delay_martin_threshold,
            self.dalembert_unit, self.paroli_count,
        )
        return nxt

    def _wolfrider_switch(self):
        new_rule, new_bv = wolfrider_switch(self.total_bets, self.rule, self.multiplier)
        if new_rule is not None:
            self.rule = new_rule
            self.bet_value = new_bv

    def _apply_rules(self, bet_state: str):
        triggered = evaluate_rules(
            self.custom_rules, bet_state, self.wins, self.losses,
            self.total_bets, self.current_streak, self.profit,
            self.current_balance, self.current_bet, self.multiplier,
        )
        for rule in triggered:
            mutations = apply_action(
                rule, self.current_bet, self.base_bet,
                self.rule, self.multiplier, self.current_streak,
                initial_multiplier=self.initial_multiplier,
            )
            for k, v in mutations.items():
                setattr(self, k, v)

    def _should_stop(self):
        p = self.profit
        b = self.current_balance
        if self.max_profit is not None and p >= self.max_profit:
            return True, f"Profit target: {p:+.8f}"
        if self.max_loss is not None and p <= -abs(self.max_loss):
            return True, f"Max loss: {p:+.8f}"
        if self.max_bets is not None and self.total_bets >= self.max_bets:
            return True, f"Max bets: {self.total_bets}"
        if self.max_wins is not None and self.wins >= self.max_wins:
            return True, f"Max wins: {self.wins}"
        if self.stop_on_balance is not None and b <= self.stop_on_balance:
            return True, f"Balance floor: {b:.8f}"
        # Streak reset: only on the win that broke an N+ loss streak, not in the red.
        if streak_reset_triggered(self.current_streak, self.last_broken_loss_streak,
                                  self.streak_reset_losses, p):
            return True, streak_reset_stop_reason(self.last_broken_loss_streak)
        return False, ""

    def _check_milestone(self, last_state: str) -> str:
        """Check if any milestone threshold was hit. Returns reason or empty string."""
        # Every N bets
        if self.milestone_bets > 0 and self.total_bets % self.milestone_bets == 0:
            return f"{self.total_bets} bets"
        # Every N wins
        if self.milestone_wins > 0 and last_state == "win" and self.wins % self.milestone_wins == 0:
            return f"{self.wins} wins"
        # Every N losses
        if self.milestone_losses > 0 and last_state != "win" and self.losses % self.milestone_losses == 0:
            return f"{self.losses} losses"
        # Every X profit increment
        if self.milestone_profit > 0 and self.profit > 0:
            threshold = self.milestone_profit
            current_level = int(self.profit / threshold)
            last_level = int(self._last_profit_milestone / threshold)
            if current_level > last_level:
                self._last_profit_milestone = self.profit
                return f"profit +{self.profit:.8f}"
        return ""

    # ── LIFECYCLE ────────────────────────────────────────
    async def start(self) -> bool:
        """Connect, fetch balance, start betting task. Returns True on success."""
        if not self._http:
            await self._recreate_http()
        try:
            balances = await self.api_get_balances()
        except Exception as e:
            self._set_error("Connection failed", f"Connection failed: {e}")
            return False

        bal = 0.0
        for b in balances:
            if b.get("currency", "").lower() == self.currency.lower():
                bal = float(b.get("amount", 0))
                break
        if bal <= 0 and self.base_bet > 0:
            self._set_error(f"No {self.currency.upper()} balance found")
            return False

        init_db(self.db_path)
        migrate_db(self.db_path)
        cleanup_old_bets(self.db_path)
        self.start_balance   = bal
        self.current_balance = bal
        self.highest_balance = bal
        self.lowest_balance  = bal
        if self._apply_basebet_pct():
            logger.info("User %d: basebet_pct=%.6f → base_bet=%.8f (balance=%.8f)",
                        self.user_id, self.basebet_pct, self.base_bet, bal)
        self._init_run()
        self.session_start   = time.time()
        self._last_session_save = time.time()
        self.session_id      = self._db_start_session()
        self._attach_run(self.session_id)
        self.running         = True
        self.status          = "Starting..."

        self._task = asyncio.create_task(self._betting_loop())
        return True

    def snapshot_state(self) -> dict:
        """Capture full engine state for resume after restart."""
        rules_ser = json.dumps([r.to_dict() for r in self.custom_rules]) if self.custom_rules else ""
        config = {
            "api_key": self.api_key,
            "currency": self.currency,
            "rule": self.rule,
            "multiplier": self.multiplier,
            "bet_value": self.bet_value,
            "base_bet": self.base_bet,
            "strategy": self.strategy,
            "strategy_key": self.strategy_key,
            "win_mult": self.win_mult,
            "loss_mult": self.loss_mult,
            "bet_delay": self.bet_delay,
            "delay_martin_threshold": self.delay_martin_threshold,
            "faucet": self.faucet,
            "custom_rules_text": rules_ser,
            "max_profit": self.max_profit,
            "max_loss": self.max_loss,
            "max_bets": self.max_bets,
            "max_wins": self.max_wins,
            "stop_on_balance": self.stop_on_balance,
            "streak_reset_losses": self.streak_reset_losses,
            "streak_reset_delay": self.streak_reset_delay,
            "profit_increment": self.profit_increment,
            "profit_threshold": self.profit_threshold,
            "profit_increment_scope": self.profit_increment_scope,
            "proxy": self.proxy,
            "milestone_bets": self.milestone_bets,
            "milestone_wins": self.milestone_wins,
            "milestone_losses": self.milestone_losses,
            "milestone_profit": self.milestone_profit,
            "streak_delay_loss": f"{self.streak_delay_loss[0]}:{self.streak_delay_loss[1]}" if self.streak_delay_loss[0] > 0 else None,
            "streak_delay_win":  f"{self.streak_delay_win[0]}:{self.streak_delay_win[1]}" if self.streak_delay_win[0] > 0 else None,
            "streak_delay_bets": f"{self.streak_delay_bets[0]}:{self.streak_delay_bets[1]}" if self.streak_delay_bets[0] > 0 else None,
            "streakbet_loss":    f"{self.streakbet_loss[0]}:{self.streakbet_loss[1]}" if self.streakbet_loss[0] > 0 else None,
            "basebet_pct":       self.basebet_pct or None,
        }
        return {
            "user_id": self.user_id,
            "db_path": self.db_path,
            "config": config,
            "session_id": self.session_id,
            "session_start": self.session_start,
            "initial_multiplier": self.initial_multiplier,
            "current_bet": self.current_bet,
            "total_bets": self.total_bets,
            "wins": self.wins,
            "losses": self.losses,
            "profit": self.profit,
            "wagered": self.wagered,
            "start_balance": self.start_balance,
            "current_balance": self.current_balance,
            "current_streak": self.current_streak,
            "max_win_streak": self.max_win_streak,
            "max_loss_streak": self.max_loss_streak,
            "last_broken_loss_streak": self.last_broken_loss_streak,
            "highest_bet": self.highest_bet,
            "highest_win": self.highest_win,
            "biggest_loss": self.biggest_loss,
            "highest_balance": self.highest_balance,
            "lowest_balance": self.lowest_balance if self.lowest_balance != float("inf") else self.current_balance,
            "dalembert_unit": self.dalembert_unit,
            "paroli_count": self.paroli_count,
            "next_profit_milestone": self.next_profit_milestone,
            "_last_profit_milestone": self._last_profit_milestone,
            "run_first_session": self.run_first_session,
            "run_since": self.run_since,
            "run_bumps": self.run_bumps,
            "run_profit_done": self.run_profit_done,
        }

    def restore_state(self, snap: dict):
        """Restore runtime state from a snapshot dict."""
        self.session_id        = snap["session_id"]
        self.session_start     = snap["session_start"]
        self.initial_multiplier = snap.get("initial_multiplier", self.multiplier)
        self.current_bet       = snap["current_bet"]
        self.total_bets        = snap["total_bets"]
        self.wins              = snap["wins"]
        self.losses            = snap["losses"]
        self.profit            = snap["profit"]
        self.wagered           = snap["wagered"]
        self.start_balance     = snap["start_balance"]
        self.current_balance   = snap["current_balance"]
        self.current_streak    = snap["current_streak"]
        self.max_win_streak    = snap["max_win_streak"]
        self.max_loss_streak   = snap["max_loss_streak"]
        self.last_broken_loss_streak = snap.get("last_broken_loss_streak", 0)
        self.highest_bet       = snap["highest_bet"]
        self.highest_win       = snap["highest_win"]
        self.biggest_loss      = snap["biggest_loss"]
        self.highest_balance   = snap["highest_balance"]
        self.lowest_balance    = snap.get("lowest_balance", float("inf"))
        self.dalembert_unit    = snap.get("dalembert_unit", 0)
        self.paroli_count      = snap.get("paroli_count", 0)
        self.next_profit_milestone  = snap.get("next_profit_milestone", 0.0)
        self._last_profit_milestone = snap.get("_last_profit_milestone", 0.0)
        # v1.10.0: the restored base bet already holds the run's bumps.
        self.run_first_session = snap.get("run_first_session")
        self.run_since         = snap.get("run_since")
        self.run_bumps         = int(snap.get("run_bumps") or 0)
        self.run_profit_done   = float(snap.get("run_profit_done") or 0.0)
        self._run_bumps_in_base = self.run_bumps

    async def start_resumed(self) -> bool:
        """Resume a saved session. Tests API connection but skips balance fetch / new DB session."""
        if not self._http:
            await self._recreate_http()
        init_db(self.db_path)

        try:
            await self.api_get_balances()
        except Exception as e:
            self._set_error("Resume connection failed", f"Resume connection failed: {e}")
            return False

        self.running = True
        self.paused = False
        self.status = "Resumed"
        self._last_session_save = time.time()
        elapsed = time.time() - self.session_start if self.session_start else 0
        if elapsed > 0 and self.total_bets > 0:
            self.bets_per_second = self.total_bets / elapsed
            self.bets_per_minute = self.bets_per_second * 60
        self._task = asyncio.create_task(self._betting_loop())
        logger.info("User %d: Resumed session #%d", self.user_id, self.session_id)
        return True

    def stop(self):
        self.running = False

    async def mutate(self, changes: dict) -> str:
        """Apply config changes to a running session. Returns status message."""
        msgs = []
        if "bet_delay" in changes:
            self.bet_delay = float(changes["bet_delay"])
            msgs.append(f"Delay: {self.bet_delay}s")
        if "streak_delay_loss" in changes:
            self.streak_delay_loss = self._parse_streak_delay(changes["streak_delay_loss"])
            msgs.append(f"Streak delay loss: {self.streak_delay_loss[0]}x → {self.streak_delay_loss[1]:.3f}s")
        if "streak_delay_win" in changes:
            self.streak_delay_win = self._parse_streak_delay(changes["streak_delay_win"])
            msgs.append(f"Streak delay win: {self.streak_delay_win[0]}x → {self.streak_delay_win[1]:.3f}s")
        if "streak_delay_bets" in changes:
            self.streak_delay_bets = self._parse_streak_delay(changes["streak_delay_bets"])
            if self.streak_delay_bets[0] > 0:
                msgs.append(f"Streak delay bets: every {self.streak_delay_bets[0]} bets → {self.streak_delay_bets[1]:.3f}s")
            else:
                msgs.append("Streak delay bets: off")
        if "streakbet_loss" in changes:
            self.streakbet_loss = self._parse_streak_delay(changes["streakbet_loss"])
            if self.streakbet_loss[0] > 0:
                msgs.append(f"Streak bet loss: every {self.streakbet_loss[0]} losses → x{self.streakbet_loss[1]:.3f}")
            else:
                msgs.append("Streak bet loss: off")
        if "basebet_pct" in changes:
            v = changes["basebet_pct"]
            self.basebet_pct = float(v) if v is not None else 0.0
            if self.basebet_pct > 0 and self.current_balance > 0:
                self._apply_basebet_pct()
                msgs.append(f"Basebet pct: {self.basebet_pct*100:.4f}% → base={self.base_bet:.8f}")
            else:
                msgs.append("Basebet pct: off")
        if "max_profit" in changes:
            v = changes["max_profit"]
            self.max_profit = float(v) if v is not None else None
            msgs.append(f"Max profit: {float(v):.8f}" if v is not None else "Max profit: off")
        if "max_loss" in changes:
            v = changes["max_loss"]
            self.max_loss = float(v) if v is not None else None
            msgs.append(f"Max loss: {float(v):.8f}" if v is not None else "Max loss: off")
        if "max_bets" in changes:
            v = changes["max_bets"]
            self.max_bets = int(v) if v is not None else None
            msgs.append(f"Max bets: {v}" if v is not None else "Max bets: off")
        if "max_wins" in changes:
            v = changes["max_wins"]
            self.max_wins = int(v) if v is not None else None
            msgs.append(f"Max wins: {v}" if v is not None else "Max wins: off")
        if "stop_on_balance" in changes:
            v = changes["stop_on_balance"]
            self.stop_on_balance = float(v) if v is not None else None
            msgs.append(f"Min balance: {float(v):.8f}" if v is not None else "Min balance: off")
        if "streak_reset_losses" in changes:
            v = changes["streak_reset_losses"]
            self.streak_reset_losses = int(v) if v is not None else None
            msgs.append(f"Streak reset: after {v} losses" if v is not None else "Streak reset: off")
        if "milestone_bets" in changes:
            self.milestone_bets = int(changes["milestone_bets"])
            msgs.append(f"Milestone bets: {self.milestone_bets}")
        if "milestone_wins" in changes:
            self.milestone_wins = int(changes["milestone_wins"])
            msgs.append(f"Milestone wins: {self.milestone_wins}")
        if "profit_increment" in changes:
            self.profit_increment = changes["profit_increment"]
            msgs.append(f"Profit increment: {float(self.profit_increment):.8f}" if self.profit_increment else "Profit increment: off")
        if "profit_threshold" in changes:
            self.profit_threshold = changes["profit_threshold"]
            if self.profit_threshold:
                # Run scope counts the bumps already made into the next milestone.
                done = self.run_bumps if self.profit_increment_scope == "run" else 0
                self.next_profit_milestone = (done + 1) * float(self.profit_threshold)
            msgs.append(f"Profit threshold: {float(self.profit_threshold):.8f}" if self.profit_threshold else "Profit threshold: off")
        if self.profit_increment_scope == "run" and (
                "profit_increment" in changes or "profit_threshold" in changes):
            # Off ends the run; on with no run starts one at the current session.
            if not self._increment_on():
                self._end_run("increment off")
            elif self.run_first_session is None and self.session_id:
                self._attach_run(self.session_id)
                self.next_profit_milestone = self.profit_threshold
        if "base_bet" in changes:
            self.base_bet = float(changes["base_bet"])
            # Don't reset current_bet during loss streak: applies on next win
            msgs.append(f"Base bet: {self.base_bet:.8f} (applies on next win)")
        if "multiplier" in changes:
            self.multiplier = float(changes["multiplier"])
            msgs.append(f"Multiplier: {self.multiplier:.2f}")
        if "loss_mult" in changes:
            self.loss_mult = float(changes["loss_mult"])
            msgs.append(f"Loss mult: {self.loss_mult:.2f}")
        if "win_mult" in changes:
            self.win_mult = float(changes["win_mult"])
            msgs.append(f"Win mult: {self.win_mult:.2f}")
        return "; ".join(msgs) if msgs else "No changes applied"

    def pause(self):
        # v1.7.0: freeze uptime (mirrors wolfbet v2.28.4).
        if not self.paused:
            now = time.time()
            self.session_bet_ended_at = now
            self.session_pause_started_at = now
        self.paused = True

    def resume(self):
        self.session_bet_ended_at = None
        self.session_pause_started_at = None
        self.paused = False

    def get_status(self) -> dict:
        # Cache for ~250ms: milestone notifier and /status often fire within ms
        # of each other; avoid rebuilding the 50-field dict every time.
        now_mono = time.monotonic()
        if self._status_cache is not None and (now_mono - self._status_cache_ts) < 0.25:
            return self._status_cache
        # v1.7.0: freeze uptime at bet-end during pauses.
        now = time.time()
        ref = self.session_bet_ended_at if self.session_bet_ended_at else now
        elapsed = ref - self.session_start if self.session_start else 0
        h, rem = divmod(int(elapsed), 3600)
        m, s = divmod(rem, 60)
        wr = f"{self.wins/self.total_bets*100:.1f}%" if self.total_bets > 0 else "\u2014"

        # stop conditions
        stops = {}
        if self.max_profit is not None: stops["max_profit"] = self.max_profit
        if self.max_loss is not None:   stops["max_loss"] = self.max_loss
        if self.max_bets is not None:   stops["max_bets"] = self.max_bets
        if self.max_wins is not None:   stops["max_wins"] = self.max_wins
        if self.stop_on_balance is not None: stops["min_balance"] = self.stop_on_balance

        # strategy detail
        strat_detail = ""
        if self.strategy_key in ("2", "6", "7"):
            strat_detail = f"loss_mult: {self.loss_mult:.2f}x"
        elif self.strategy_key in ("3", "5"):
            strat_detail = f"win_mult: {self.win_mult:.2f}x"

        # rule descriptions
        rule_descs = []
        for r in self.custom_rules:
            d = r.to_dict()
            rule_descs.append(f"{d.get('trigger','')} {d.get('condition','')} {d.get('threshold','')}: {d.get('action','')} {d.get('value','')}")

        status = {
            "running": self.running,
            "paused": self.paused,
            "session_id": self.session_id,
            "uptime": f"{h}h {m}m {s}s",
            "uptime_sec": int(elapsed),
            "pause_remaining_sec": 0,
            "bets": self.total_bets,
            "wins": self.wins,
            "losses": self.losses,
            "win_rate": wr,
            "profit": self.profit,
            "wagered": self.wagered,
            "balance": self.current_balance,
            "start_balance": self.start_balance,
            "current_bet": self.current_bet,
            "highest_bet": self.highest_bet,
            "streak": self.current_streak,
            "max_win_streak": self.max_win_streak,
            "max_loss_streak": self.max_loss_streak,
            "highest_balance": self.highest_balance,
            "lowest_balance": self.lowest_balance if self.lowest_balance != float("inf") else self.current_balance,
            "highest_win": self.highest_win,
            "biggest_loss": self.biggest_loss,
            "bps": self.bets_per_second,
            "bpm": self.bets_per_minute,
            "recent_bps": self.recent_bps,
            "recent_bpm": self.recent_bpm,
            "peak_bps": self.peak_bps,
            "low_bps": self.low_bps if self.low_bps != float("inf") else 0,
            "peak_bpm": self.peak_bpm,
            "low_bpm": self.low_bpm if self.low_bpm != float("inf") else 0,
            "base_bet": self.base_bet,
            "strategy": self.strategy,
            "strategy_key": self.strategy_key,
            "strategy_detail": strat_detail,
            "multiplier": self.multiplier,
            "currency": self.currency.upper(),
            "bet_delay": self.bet_delay,
            "stop_conditions": stops,
            "profit_increment": self.profit_increment,
            "profit_threshold": self.profit_threshold,
            "profit_increment_scope": self.profit_increment_scope,
            "run_first_session": self.run_first_session,
            "run_bumps": self.run_bumps,
            "run_profit_total": self.run_profit_total() if self.profit_increment_scope == "run" else None,
            "next_profit_milestone": self.next_profit_milestone,
            "milestone_bets": self.milestone_bets,
            "streak_delay_loss": self.streak_delay_loss,
            "streak_delay_win": self.streak_delay_win,
            "streak_delay_bets": self.streak_delay_bets,
            "streakbet_loss": self.streakbet_loss,
            "basebet_pct": self.basebet_pct,
            "streak_reset_losses": self.streak_reset_losses,
            "streak_reset_delay": self.streak_reset_delay,
            "rule": self.rule,
            "bet_value": self.bet_value,
            "win_mult": self.win_mult,
            "loss_mult": self.loss_mult,
            "rules": rule_descs,
            "status": self.status,
            "last_error": self.last_error,
            "api_ms": self._last_api_ms,
            "api_avg_ms": (self._api_ms_total / self._api_ms_count) if self._api_ms_count > 0 else 0,
            "api_peak_ms": self.peak_api_ms,
            "overhead_ms": self._cycle_overhead_ms,
            "cycle_ms": self._cycle_ms,
        }
        # API median (p50) and on-target % (api ≤ bet_delay). Explains the
        # Efficiency ceiling: 95% on-target → ~95% efficiency is the upper bound.
        p50 = (sorted(self._api_samples)[len(self._api_samples) // 2]
               if self._api_samples else 0)
        on_target_pct = ((self._on_target_count / self._api_ms_count) * 100
                         if self._api_ms_count > 0 and self.bet_delay > 0 else None)
        status["api_p50_ms"] = p50
        status["on_target_pct"] = on_target_pct
        # Efficiency = recent bps / theoretical max bps (capped at 100%).
        # theoretical max = 1 / max(bet_delay, api_avg): whichever bounds the cycle.
        api_avg_s = ((self._api_ms_total / self._api_ms_count) / 1000.0) if self._api_ms_count > 0 else 0
        cycle_floor = max(self.bet_delay, api_avg_s)
        theoretical_max_bps = (1.0 / cycle_floor) if cycle_floor > 0 else 0
        efficiency_pct = (self.recent_bps / theoretical_max_bps * 100) if theoretical_max_bps > 0 else 0
        status["theoretical_max_bps"] = theoretical_max_bps
        status["efficiency_pct"] = min(100.0, efficiency_pct)
        self._status_cache = status
        self._status_cache_ts = now_mono
        return status

    # ── BETTING LOOP ─────────────────────────────────────
    async def _wait_running(self, delay: float):
        """Sleep `delay` s in 0.25 s slices so /stop is not held by a long 429 wait."""
        remaining = float(delay)
        while remaining > 0 and self.running:
            step = min(0.25, remaining)
            await asyncio.sleep(step)
            remaining -= step

    async def _betting_loop(self):
        last_result = "none"
        logger.info("User %d: Session #%d started", self.user_id, self.session_id)

        while self.running:
            if self.paused:
                self.status = "PAUSED"
                await asyncio.sleep(0.3)
                continue

            # stop conditions
            stop, reason = self._should_stop()
            if stop:
                self.running = False
                self.stop_reason = reason
                self.status = f"STOPPED: {reason}"
                await asyncio.to_thread(self._flush_bets)
                await asyncio.to_thread(self._db_save_session, True)
                break

            # bet pacing: minimum interval since the previous bet started
            if self.bet_delay > 0:
                wait = (self._last_bet_start_time + self.bet_delay) - time.monotonic()
                if wait > 0:
                    await asyncio.sleep(wait)
            prev_bet_start = self._last_bet_start_time
            self._last_bet_start_time = time.monotonic()
            self._iter_start_mono = self._last_bet_start_time
            # Full cycle = T_start_N - T_start_N-1 (= 1/bps). Skip first iter.
            if prev_bet_start > 0:
                self._cycle_ms = (0.9 * self._cycle_ms
                                  + 0.1 * (self._last_bet_start_time - prev_bet_start) * 1000)

            # place bet
            self.status = f"Placing bet #{self.total_bets + 1}..."
            result = await self._api_place_bet(self.current_bet)

            if result is None:
                if self._insufficient_balance:
                    if self.on_error:
                        try:
                            self.on_error(f"Insufficient balance for bet {self.current_bet:.8f}")
                        except Exception:
                            pass
                    self.running = False
                    self.stop_reason = f"Insufficient balance for bet {self.current_bet:.8f}"
                    self.status = f"STOPPED: {self.stop_reason}"
                    await asyncio.to_thread(self._flush_bets)
                    await asyncio.to_thread(self._db_save_session, True)
                    break
                if self._was_rate_limited:
                    self._was_rate_limited = False
                    self._consecutive_429 += 1
                    if self._rate_limit_total_wait >= RATE_LIMIT_GIVE_UP:
                        self.running = False
                        self.stop_reason = "Rate limit persisted >30min: giving up"
                        self.status = f"STOPPED: {self.stop_reason}"
                        await asyncio.to_thread(self._flush_bets)
                        await asyncio.to_thread(self._db_save_session, True)
                        break
                    delay = (self._rate_limit_wait if self._rate_limit_wait > 0
                             else min(2 ** (self._consecutive_429 - 1), RATE_LIMIT_MAX_WAIT))
                    self._rate_limit_total_wait += delay
                    self.status = (f"Rate limited (429 #{self._consecutive_429}): "
                                   f"waiting {delay:.0f}s (total {self._rate_limit_total_wait:.0f}s)")
                    logger.warning("User %d: 429 #%d: waiting %.1fs (cumulative %.0fs/%.0fs)",
                                   self.user_id, self._consecutive_429, delay,
                                   self._rate_limit_total_wait, RATE_LIMIT_GIVE_UP)
                    await self._wait_running(delay)
                    continue
                self.consecutive_errors += 1
                self.backoff_delay = min(self.backoff_delay * 2, 30.0)
                await asyncio.sleep(self.backoff_delay)
                continue

            bet_data     = result.get("bet", {})
            balance_data = result.get("userBalance", {})
            bet_state    = bet_data.get("state", "loss")
            result_value = float(bet_data.get("result_value", 0))
            amount_used  = float(bet_data.get("amount", self.current_bet))
            new_balance  = float(balance_data.get("amount", self.current_balance))
            raw_profit   = float(bet_data.get("profit", 0))

            self.consecutive_errors = 0
            self._consecutive_429 = 0
            self._rate_limit_total_wait = 0.0
            self.backoff_delay = 1.0
            self.total_bets += 1
            self.wagered    += amount_used
            self.profit     += raw_profit
            self.current_balance = new_balance

            is_win = bet_state == "win"
            if is_win:
                self.wins += 1
                if raw_profit > self.highest_win: self.highest_win = raw_profit
            else:
                self.losses += 1
                if amount_used > self.biggest_loss: self.biggest_loss = amount_used
            self.current_streak, broken = record_outcome(self.current_streak, is_win)
            if broken is not None:
                self.last_broken_loss_streak = broken
            if is_win:
                self.max_win_streak = max(self.max_win_streak, self.current_streak)
            else:
                self.max_loss_streak = max(self.max_loss_streak, abs(self.current_streak))

            if amount_used > self.highest_bet: self.highest_bet = amount_used
            if new_balance > self.highest_balance: self.highest_balance = new_balance
            if new_balance < self.lowest_balance: self.lowest_balance = new_balance

            # Streak delay
            streak_pause = self._get_streak_delay()
            if streak_pause > 0:
                self.status = f"Streak delay {streak_pause:.3f}s (streak {self.current_streak})"
                await asyncio.sleep(streak_pause)

            if self.total_bets % 5 == 0:
                self.profit_history.append(self.profit)

            # Chart snapshot every 100 bets (survives bet cleanup)
            if self.total_bets % 100 == 0:
                self._chart_snapshots.append([self.total_bets, round(self.profit, 10), round(new_balance, 10)])

            self.recent_bets.append({
                "n": self.total_bets, "time": datetime.now().isoformat(),
                "amt": amount_used, "roll": result_value,
                "state": bet_state, "pnl": raw_profit, "bal": new_balance,
            })

            self._update_speed_stats()

            # Per-cycle overhead = full iteration time minus the API call.
            # Excludes bet_delay sleep (we measure from start of bet, after sleep).
            # Exp-smoothed so transient spikes don't dominate the reading.
            iter_ms = (time.monotonic() - self._iter_start_mono) * 1000
            overhead = max(0.0, iter_ms - self._last_api_ms)
            self._cycle_overhead_ms = 0.9 * self._cycle_overhead_ms + 0.1 * overhead

            sign  = "+" if raw_profit >= 0 else ""
            emoji = "W" if bet_state == "win" else "L"
            self.status = f"{emoji} {'WIN' if bet_state=='win' else 'LOSS'} | Roll: {result_value:.2f} | P/L: {sign}{raw_profit:.8f}"
            self.last_error = ""

            # queue bet for batched DB write
            self._queue_bet(bet_data, raw_profit, new_balance)

            # periodic session save (every 60s)
            await self._periodic_save()

            # profit-based base bet increment
            self._check_profit_increment()

            if self.strategy_key == "6":
                self._wolfrider_switch()
            if self.strategy_key == "8" and self.custom_rules:
                self._apply_rules(bet_state)

            raw_next = self._compute_next_bet(bet_state)
            self.current_bet = max(self.base_bet, raw_next)
            sbl_factor = self._get_streakbet_loss_factor()
            if sbl_factor != 1.0:
                self.current_bet = self.current_bet * sbl_factor
            last_result = bet_state

            # milestone callbacks
            if self.on_milestone:
                reason = self._check_milestone(bet_state)
                if reason:
                    try:
                        s = self.get_status()
                        s["milestone_reason"] = reason
                        self.on_milestone(s)
                        logger.info("User %d: Milestone fired: %s", self.user_id, reason)
                    except Exception as e:
                        logger.error("User %d: Milestone callback failed: %s", self.user_id, e)

            # periodic progress log (every 500 bets)
            if self.total_bets % 500 == 0:
                logger.info("User %d: #%d bets=%-6d bps=%.1f P/L=%+.8f bal=%.8f",
                            self.user_id, self.session_id,
                            self.total_bets, self.bets_per_second, self.profit, self.current_balance)

        # session ended: cleanup
        await asyncio.to_thread(self._flush_bets)
        if self.session_id:
            await asyncio.to_thread(self._db_save_session, True)
        self._close_conn()
        if self._http:
            try:
                await self._http.aclose()
            except Exception:
                pass
        logger.info("User %d: Session #%d ended: %s (bets=%d, P/L=%+.8f)",
                    self.user_id, self.session_id, self.stop_reason or "stopped",
                    self.total_bets, self.profit)
        if self.on_stop:
            try:
                self.on_stop(self.stop_reason or "Stopped by user")
            except Exception:
                pass
