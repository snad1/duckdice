# DuckDice AutoBot v1.13.0 + Telegram Bot v1.10.1

High-speed auto-betting engine for DuckDice.io Dice with a live dashboard, Telegram bot, and web admin panel. Runs on any server or local machine — leave it running and manage via CLI, Telegram, or browser.

---

## Architecture

```text
duckdice/
├── duckdice.py         CLI bot (TUI dashboard, wizard, monitor)
├── core/               Shared package (single source of truth)
│   ├── strategy.py     Strategies, rule engine, StrategyRule class
│   ├── database.py     DB schema, migrations, WAL connections
│   └── engine.py       Strategy computation, rule evaluation
├── tg/                 Telegram bot package
│   ├── bot.py          Entry point, command registration
│   ├── handlers.py     All /command handlers
│   ├── engine.py       Per-user BettingEngine (uses core/)
│   ├── database.py     Per-user DB/config paths
│   ├── formatter.py    Telegram message formatting
│   ├── config.py       Env-based configuration
│   └── strategy.py     Re-exports from core/
├── web/                FastAPI admin dashboard
│   ├── app.py          FastAPI factory
│   ├── auth.py         JWT auth, roles (admin/user)
│   ├── database.py     Web DB schema (users, audit log)
│   ├── bot_db.py       Bridge to bot SQLite
│   ├── services.py     Systemd service control
│   └── routes/         API, admin, user, auth, WebSocket routes
├── install.sh          Server installer (systemd, duckctl, venv)
├── .env.example        Environment variable template
└── requirements.txt    Python dependencies (includes casino-shared)
```

Both the CLI bot and Telegram bot import shared logic from `core/` — change once, applies everywhere. Cross-bot shared code (`shared/`) is installed as a pip package (`casino-shared`) from GitHub.

---

## Features

| Feature | Details |
|---|---|
| **Pure ANSI TUI** | Direct ANSI rendering, fits any terminal, refreshes 2x/sec |
| **Telegram bot** | Full control via Telegram — start/stop/pause/status/config |
| **Web dashboard** | FastAPI admin panel with JWT auth, CSV export, WebSocket live updates |
| **8 betting strategies** | Flat, Martingale, Anti-Martingale, D'Alembert, Paroli, WolfRider, Delay Martingale, Rule-Based |
| **21 rule actions** | Full action set: bet amount, win chance, payout multiplier, switch, stop, reset |
| **12 currencies** | BTC, ETH, LTC, DOGE, TRX, BCH, XRP, USDT, BNB, USDC, SOL, XLM |
| **Named presets** | Save & load strategy configs by name |
| **Env-based config** | API key, paths, Telegram token — all via environment variables |
| **Secure file handling** | Config files written with 0600 permissions, API keys masked in logs |
| **Session database** | SQLite log of every bet and session — survives restarts |
| **Auto data retention** | Old bet records auto-purged after 3 days; session stats preserved forever |
| **Faucet support** | Free bets via DuckDice faucet endpoint |
| **Daemon + monitor** | `--daemon` for background, `--monitor` to attach live TUI |
| **Performance tracking** | BPS, BPM, peak/low speed, peak/low balance, best win, worst loss |
| **Benchmark command** | `/benchmark` to test API response speed |
| **Daily log rotation** | Logs at `~/.duckdice_logs/`, rotates at midnight, 30-day retention |

---

## Quick Start

### Local

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt

# Run wizard (guides you through config)
python3 duckdice.py

# Get your API key from: https://duckdice.io/settings
```

### Remote Server

```bash
# Upload files
scp -r duckdice.py requirements.txt install.sh core/ tg/ .env.example user@your-server:~/duckdice/

# Install (sets up venv, systemd, duckctl)
ssh user@your-server
cd ~/duckdice && chmod +x install.sh && ./install.sh
```

### duckctl Management

```bash
duckctl setup             # Run wizard to configure
duckctl start             # Start CLI bot as daemon
duckctl stop              # Stop CLI bot
duckctl monitor           # Attach live TUI to running daemon
duckctl status            # Check running session status
duckctl stats             # Session statistics (--since/--until DATE, --lane K)
duckctl analytics         # Aggregates by game / strategy / lane, top 5 (same flags)
duckctl restart             # Restart the bot (every lane)
duckctl restart K --at-boundary | --cancel-boundary  # Restart lane K when its session ends
duckctl restart --rolling                            # Restart every running lane at its own session end; starting or restarting lane 1 also starts the enabled lanes that are not running
duckctl lanes [N] [--split none|budget|bet|both] [--max N]  # Show or set parallel lanes; only the difference starts or stops
duckctl lane add NAME [--from K] | remove K [--after-session] | rename K NAME  # Change one lane; the others keep betting
duckctl key list | add ID [--label L] [--kind key|proxy|telegram] | rotate ID | delete ID  # Host vault: API keys, proxies, the Telegram token (hidden prompt)
duckctl start|stop|restart K  # One lane alone (lane 1 is the duckdice service)
duckctl pause|resume|reload [K|all]  # Pause or resume betting, or reload the saved config (default: every lane)
duckctl logs                # Stream live logs
duckctl logs-full           # Show last 200 log lines
duckctl diagnose            # Bottleneck analysis of the running session
duckctl benchmark [N] [--lanes M]  # Time N probe requests
duckctl doctor [--fix] [--yes]  # Health check; --fix applies safe fixes
duckctl tmux                # Run monitor in a detachable tmux session
duckctl config-set KEY=VALUE ...  # Change saved keys; a running daemon reloads
duckctl session ID          # Full stats + streak distribution for a session
duckctl last-bets [N]       # Last N bets across all sessions (default 20)
duckctl presets             # List saved presets
duckctl export PATH [SID]   # Export a session (CSV, or JSON by .json extension)
duckctl backtest [args]     # Backtest / replay offline (no API calls, no real balance)
duckctl cleanup [DAYS]      # Purge bet rows of old sessions (default: purge_days)
duckctl reset [--yes]       # Archive the stats DB and start fresh (never deletes)
duckctl archives            # List archived stats DBs
duckctl run-reset [--yes]   # New run total for the run-wide profit increment
duckctl update              # Pull and reinstall; each lane restarts on the new code at its session end
duckctl update --critical   # Same, but restart every lane now
duckctl refresh             # Reinstall duckctl from the repo copy in this folder
duckctl version             # Bot and duckctl versions

duckctl tg env            # Edit .env (set Telegram token)
duckctl tg start          # Start Telegram bot
duckctl tg stop           # Stop Telegram bot
duckctl tg logs           # Stream Telegram bot logs

duckctl web install       # Install web dashboard
duckctl web start         # Start web dashboard
duckctl web stop          # Stop web dashboard
duckctl web logs          # Stream web dashboard logs

duckctl start-all         # Start all services
duckctl stop-all          # Stop all services
duckctl status-all        # Check all services
```

---

## Telegram Bot

The Telegram bot provides full betting control from your phone. Each Telegram user gets isolated storage (DB, config, presets).

### Setup

1. Open Telegram, search **@BotFather**, `/newbot`
2. Choose a name and username, copy the token
3. On your server: `duckctl key add telegram --kind telegram` (hidden prompt; the host vault keeps it), or `duckctl tg env` and set `DUCKDICE_TG_TOKEN=<token>`
4. `duckctl tg start`
5. Open your bot in Telegram and `/start`

### Commands

| Command | Description |
|---|---|
| `/start` | Welcome and quick start guide |
| `/help` | Full command reference |
| `/setkey` | Set DuckDice API key |
| `/balance` | Check balances |
| `/config` | View current config |
| `/set <param> <value>` | Set a parameter |
| `/strategies` | List available strategies |
| `/bet` | Start betting session (up to 5 concurrent) |
| `/stop [slot\|all]` | Stop session |
| `/pause [slot\|all]` / `/resume [slot\|all]` | Pause/resume betting |
| `/status [slot]` | Live status — summary when multiple sessions |
| `/monitor [slot] [interval]` | Auto-updating status (3-60s) |
| `/stats` | Session history |
| `/diagnose [slot]` | Bottleneck analysis (top blocker + advice) |
| `/analytics [SINCE] [UNTIL]` | Analytics (per-game, per-strategy, top-5), date-windowed (e.g. 2026-08-14) |
| `/session <id>` | Full session report with config snapshot + streaks |
| `/lastbets [N]` | Recent bets |
| `/rules` | List current rules |
| `/addrule <JSON>` | Add a rule |
| `/delrule <N>` | Delete rule by number |
| `/editrule <N> <JSON>` | Edit rule by number (JSON patch) |
| `/clearrules` | Clear all rules |
| `/presets` | List presets |
| `/savepreset <name>` | Save current config |
| `/loadpreset <name>` | Load a preset |
| `/web` | Open web dashboard |
| `/benchmark` | Test API response speed |
| `/cleanup [days]` | Purge old bet records (default 3 days), keep session stats |
| `/delsession <id>` | Delete a specific session and its bets |
| `/update` | Pull the latest code and restart the bot (owner only) |
| `/runreset` | Start a new run total for `piscope run` (asks to confirm; stop sessions first) |
| `/tweak <param> <value>` | Change a setting of a running session (e.g. `/tweak delay 0.1`) |

---

## Environment Variables

| Variable | Default | Description |
|---|---|---|
| `DUCKDICE_API_KEY` | *(none)* | API key from DuckDice settings. Read once: lane 1 moves it into the host vault on start; then remove it from `~/duckdice-bot/.env` |
| `DUCKDICE_API_BASE` | `https://duckdice.io/api` | API base URL |
| `DUCKDICE_DB_PATH` | `~/.duckdice_autobot.db` | SQLite database path |
| `DUCKDICE_CONFIG_PATH` | `~/.duckdice_autobot.json` | Saved config path |
| `DUCKDICE_LOG_DIR` | `~/.duckdice_logs` | Log directory |
| `DUCKDICE_TG_TOKEN` | *(none)* | Telegram bot token |
| `DUCKDICE_TG_DATA` | `~/.duckbot_tg` | Per-user Telegram data dir |
| `DUCKDICE_WEB_SECRET_KEY` | *(none)* | JWT secret for web dashboard |

---

## Strategies

| Nr | Name | How it works | Risk |
|---|---|---|---|
| 1 | **Flat Bet** | Same amount every time | Very Low |
| 2 | **Martingale** | Multiply on loss, reset on win | High |
| 3 | **Anti-Martingale** | Multiply on win, reset on loss | Medium |
| 4 | **D'Alembert** | +1 unit on loss, -1 on win | Low-Medium |
| 5 | **Paroli (3-step)** | Multiply on win up to 3x then reset | Medium |
| 6 | **WolfRider** | Multiply on loss + switch over/under on bet 2/6/9 | High |
| 7 | **Delay Martingale** | Flat for N losses, then multiply | High |
| 8 | **Rule-Based** | Custom IF/THEN conditions & actions | Variable |

---

## Rule-Based Strategy (Strategy 8)

### Condition Modes

| Mode | Behavior |
|---|---|
| **Every N** | Fires at every Nth total win/loss/bet |
| **Every streak of N** | Fires at N, 2N, 3N within a streak (modulus) |
| **First streak of N** | Fires once when streak first hits N (resets on flip) |
| **Streak above N** | Fires every bet past N in streak |
| **Streak below N** | Fires when streak is below N |

### Actions

**Bet Amount:** Reset, Increase by %, Decrease by %, Add fixed, Deduct fixed, Set exact
**Win Chance:** Reset, Set, Increase by %, Decrease by %, Add, Deduct
**Payout:** Reset, Set, Increase by %, Decrease by %, Add, Deduct
**Other:** Switch over/under, Stop, Reset game

---

## CLI Options

```bash
python3 duckdice.py                   # Interactive wizard + TUI
python3 duckdice.py --resume          # Skip wizard, reuse saved config
python3 duckdice.py --preset X        # Load preset X and start
python3 duckdice.py --daemon          # Background (no TUI, implies --resume)
python3 duckdice.py --monitor         # Attach live TUI to running daemon
python3 duckdice.py --status          # Check running session status
python3 duckdice.py --stop            # Stop a running daemon
python3 duckdice.py --setup-only      # Wizard only, don't start
python3 duckdice.py --stats           # Session history + totals
python3 duckdice.py --stats --since 2026-08-01 --until 2026-08-15
                                      # Date-windowed stats (bare date = inclusive)
python3 duckdice.py --stats --since "2026-11-13 14:00"  # Window from a time of day
python3 duckdice.py --analytics       # Aggregates: by game, strategy, lane, top 5 best/worst
python3 duckdice.py --analytics --since 2026-11-01 --lane 2  # Windowed, one lane
python3 duckdice.py --last-bets N     # Last N bets across sessions
python3 duckdice.py --session-bets N  # Full stats + streaks for session N
python3 duckdice.py --verbose         # Enable DEBUG logging

# Parallel lanes (v1.10.0): N bot processes on one account, config and DB
python3 duckdice.py --config-set lanes=3 lane_split=budget  # count + money split
python3 duckdice.py --config-set lane2.maxloss=1 lane3.mult=3  # per-lane overrides
python3 duckdice.py --daemon --lane 2 # Run lane 2 (lane 1 = classic paths)
python3 duckdice.py --status --lane 2 # --status / --monitor / --stop per lane
python3 duckdice.py --lanes-status    # One row per lane + total bps / P/L
python3 duckdice.py --benchmark 20 --lanes 4  # Read-only probe at 1..4 clients
python3 duckdice.py --version           # Print the version
python3 duckdice.py --list-presets      # Show saved presets
python3 duckdice.py --config-show       # Print the saved config (secrets masked)
python3 duckdice.py --config-set piscope=run pi=0.0001 pt=0.001  # Run-wide profit increment (piscope=session keeps it per session)
python3 duckdice.py --diagnose          # Bottleneck analysis of the running daemon
python3 duckdice.py --cleanup [DAYS]    # Purge bet rows of sessions ended > DAYS ago (default purge_days)
python3 duckdice.py --run-reset [--lane K]  # New run total for the run-wide profit increment
python3 duckdice.py --export ~/s12.csv --export-session-id 12  # Export a session (.json = JSON, else CSV; default latest)
python3 duckdice.py --backtest --simulate 10000 --seed 42  # Offline backtest: no API calls, no real balance
python3 duckdice.py --backtest --replay-session 12  # Replay a stored session's outcomes offline
```

Faucet mode can't run with lanes > 1 (the faucet balance can't be split): `--config-set lanes=N` and the start refuse it.

---

## API

| Endpoint | Purpose |
|---|---|
| `POST /dice/play` | Place a Dice bet |

Base URL: `https://duckdice.io/api`

Auth: API key passed as query parameter.

---

## Disclaimer

This software is provided as-is for educational purposes. Gambling involves real financial risk. Never bet more than you can afford to lose. Use the stop conditions. Be responsible.
