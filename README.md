# DuckDice AutoBot v1.0.5 + Telegram Bot v1.0.5

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
duckctl stats             # Show all-time session statistics

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
3. On your server: `duckctl tg env` and set `DUCKDICE_TG_TOKEN=<token>`
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

---

## Environment Variables

| Variable | Default | Description |
|---|---|---|
| `DUCKDICE_API_KEY` | *(none)* | API key from DuckDice settings |
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
python3 duckdice.py --last-bets N     # Last N bets across sessions
python3 duckdice.py --session-bets N  # Full stats + streaks for session N
python3 duckdice.py --verbose         # Enable DEBUG logging
```

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
