# Changelog

## v1.0.3 — DRY Shared Library Extraction (2026-03-29)

### Infrastructure

- **Shared library extraction** — Extracted ~6,600 lines of duplicated code into `shared/` library used across all 6 casino bots
  - `web/websocket.py`, `web/database.py`, `web/auth.py`, `web/services.py`, `web/bot_db.py`, `web/routes/auth_routes.py` — thin shims delegating to shared modules
  - `tg/database.py` — thin shim binding `DATA_DIR` to shared persistence
  - `core/strategy.py` — keeps local STRATEGIES dict, delegates rule engine to shared
  - `core/database.py` — keeps local `init_db`, delegates utilities to shared
- Zero downstream import changes — all existing imports continue working

### Security

- **Shell injection fix** — Replaced `create_subprocess_shell` with `shutil.copy2` in update flow
- **Deprecated datetime fix** — Replaced `datetime.utcnow()` with `datetime.now(timezone.utc)`
- **Narrowed exception handling** — Replaced broad `except Exception` with specific types
- **Removed unused imports** — Cleaned up unused `import os` from shims

### Testing

- **114-test suite** added covering all shared modules

---

## v1.0.2 — Security Hardening (2026-03-29)

### TG Bot v1.0.2

#### Security

- **ENV-controlled error messages** — Error handlers now log full details server-side and show user-friendly messages in production. Controlled via `APP_ENV` environment variable
- **`duckctl` self-update safety** — Script wrapped in `{ }` block to prevent parse errors when `cmd_update` replaces the file mid-run

---

## v1.0.1 — Data Retention & Cleanup (2026-03-24)

### TG Bot v1.0.1

#### Added

- **Automatic bet cleanup** — Old bet records (3+ days) from ended sessions are automatically purged when a new session starts. Session statistics (profit, bets, streaks, balance extremes, speed) are preserved — only raw bet rows are deleted. Disk space is reclaimed via VACUUM.
- **`/cleanup [days]`** — Manually purge bet records older than N days (default 3). Session stats remain intact. Usage: `/cleanup` or `/cleanup 1`.
- **`/delsession <id>`** — Delete a specific session and all its bets by session ID. Blocks deletion of currently running sessions. Shows bet count and profit before confirming.
- **`bets_purged` flag** — Sessions table gains a `bets_purged` column to track which sessions have already been cleaned, avoiding redundant queries on subsequent cleanups.

## v1.0.0 — Initial Release (2026-03-15)

### Full Stack

- **CLI bot** (`duckdice.py`) — Interactive wizard, Pure ANSI TUI dashboard, daemon mode, monitor mode, presets.
- **Telegram bot** (`tg/`) — 27 commands, per-user isolation, multi-session (up to 5 concurrent), inline keyboards, auto-command registration.
- **Web dashboard** (`web/`) — FastAPI admin panel with JWT auth, per-user session views, CSV export, WebSocket live updates, audit log.
- **Shared core** (`core/`) — Single source of truth for strategies, database schema, and bet computation.
- **Server installer** (`install.sh`) — Systemd services, venv, `duckctl` management CLI.

### Features

- **8 strategies** — Flat, Martingale, Anti-Martingale, D'Alembert, Paroli, WolfRider, Delay Martingale, Rule-Based.
- **21 rule actions** — Bet amount, win chance, payout multiplier, switch, stop, reset.
- **12 currencies** — BTC, ETH, LTC, DOGE, TRX, BCH, XRP, USDT, BNB, USDC, SOL, XLM.
- **Dice game** — Over/Under prediction with customizable payout multipliers.
- **Smart stop conditions** — Max profit, max loss, max bets, max wins, min balance.
- **Named presets** — Save & load strategy configs by name.
- **Performance tracking** — BPS, BPM, peak/low speed, peak/low balance, best win, worst loss.
- **Multi-session support** — Up to 5 concurrent sessions per Telegram user.
- **Zero-downtime updates** — `duckctl update` with session auto-resume.
- **Benchmark command** — `/benchmark` to test API response speed.
- **Min bet error handling** — Auto-adjust base bet on 422 min bet errors instead of infinite retry.
- **Faucet support** — Free bets via DuckDice faucet endpoint.

### Post-Release Fixes

- Fix faucet balance display and add DECOY currency.
- Remove DECOY from currencies list.
- Handle 422 min bet error — auto-adjust base bet instead of infinite retry.
- Fix `AttributeError`: `delay_threshold` to `delay_martin_threshold` in TG engine.
- Add `/benchmark` command, API timing, and change password feature.
- Fix accessibility: add label associations and placeholders to change password form.
