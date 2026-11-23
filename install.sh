#!/usr/bin/env bash
# ─────────────────────────────────────────────────────────
#  DuckDice AutoBot: Server Installer v3.2
#  Sets up: CLI bot + Telegram bot, venv, systemd, duckctl
# ─────────────────────────────────────────────────────────
set -euo pipefail

INSTALL_DIR="$HOME/duckdice-bot"
SERVICE_NAME="duckdice"
LANE_SERVICE_NAME="duckdice-lane"
TG_SERVICE_NAME="duckdice-tg"
SYSTEMD_DIR="$HOME/.config/systemd/user"
DUCKCTL="$HOME/.local/bin/duckctl"
ENV_FILE="$INSTALL_DIR/.env"

echo ""
echo "╔══════════════════════════════════════════════════╗"
echo "║   DuckDice AutoBot: Server Installer v3.2        ║"
echo "║   CLI Bot + Telegram Bot                          ║"
echo "╚══════════════════════════════════════════════════╝"
echo ""

# ── 1. System dependencies ─────────────────────────────
echo "[1/7] Installing system packages..."
sudo apt-get update -qq
sudo apt-get install -y -qq \
    python3 python3-pip python3-venv python3-dev \
    sqlite3 tmux curl

# ── 2. Bot directory ───────────────────────────────────
echo "[2/7] Setting up bot directory..."
mkdir -p "$INSTALL_DIR"
cp duckdice.py      "$INSTALL_DIR/"
cp requirements.txt "$INSTALL_DIR/"

# Copy core/ shared package
mkdir -p "$INSTALL_DIR/core"
cp core/__init__.py  "$INSTALL_DIR/core/"
cp core/strategy.py  "$INSTALL_DIR/core/"
cp core/database.py  "$INSTALL_DIR/core/"
cp core/engine.py    "$INSTALL_DIR/core/"

# Copy tg/ Telegram bot package
mkdir -p "$INSTALL_DIR/tg"
cp tg/__init__.py   "$INSTALL_DIR/tg/"
cp tg/config.py     "$INSTALL_DIR/tg/"
cp tg/database.py   "$INSTALL_DIR/tg/"
cp tg/engine.py     "$INSTALL_DIR/tg/"
cp tg/strategy.py   "$INSTALL_DIR/tg/"
cp tg/formatter.py  "$INSTALL_DIR/tg/"
cp tg/handlers.py   "$INSTALL_DIR/tg/"
cp tg/bot.py        "$INSTALL_DIR/tg/"


# ── 3. Environment file ───────────────────────────────
echo "[3/7] Setting up environment..."
if [ ! -f "$ENV_FILE" ]; then
    if [ -f ".env" ]; then
        cp .env "$ENV_FILE"
        echo "   Copied .env to $ENV_FILE"
    elif [ -f ".env.example" ]; then
        cp .env.example "$ENV_FILE"
        echo "   Created $ENV_FILE from .env.example"
        echo "   ⚠ EDIT THIS FILE: nano $ENV_FILE"
    else
        cat > "$ENV_FILE" << 'ENVEOF'
# DuckDice AutoBot: Environment
# Get your Telegram bot token from @BotFather
DUCKDICE_TG_TOKEN=
DUCKDICE_TG_DATA=~/.duckbot_tg
ENVEOF
        echo "   Created $ENV_FILE"
        echo "   ⚠ EDIT THIS FILE: nano $ENV_FILE"
    fi
else
    echo "   $ENV_FILE already exists (kept)"
fi
chmod 600 "$ENV_FILE"

# ── 4. Python virtual environment ─────────────────────
echo "[4/7] Creating Python virtual environment..."
python3 -m venv "$INSTALL_DIR/venv"
"$INSTALL_DIR/venv/bin/pip" install --quiet --upgrade pip
"$INSTALL_DIR/venv/bin/pip" install --quiet -r "$INSTALL_DIR/requirements.txt"

# ── 5. Systemd services ───────────────────────────────
echo "[5/7] Setting up systemd services..."
mkdir -p "$SYSTEMD_DIR"

# CLI bot service
cat > "$SYSTEMD_DIR/${SERVICE_NAME}.service" << SERVICEEOF
[Unit]
Description=DuckDice AutoBot: Auto-Betting Engine (CLI)
After=network-online.target
Wants=network-online.target
StartLimitIntervalSec=0

[Service]
Type=simple
Nice=-5
IOSchedulingClass=best-effort
IOSchedulingPriority=0
WorkingDirectory=${INSTALL_DIR}
ExecStart=${INSTALL_DIR}/venv/bin/python3 ${INSTALL_DIR}/duckdice.py --resume --daemon
Restart=on-failure
RestartSec=10
StandardOutput=journal
StandardError=journal

KillSignal=SIGTERM
TimeoutStopSec=15

Environment=HOME=${HOME}
Environment=PYTHONUNBUFFERED=1
EnvironmentFile=-${ENV_FILE}

[Install]
WantedBy=default.target
SERVICEEOF

# Parallel lanes 2..N: same unit as lane 1 plus --lane %i (duckctl lanes N)
cat > "$SYSTEMD_DIR/${LANE_SERVICE_NAME}@.service" << LANESERVICEEOF
[Unit]
Description=DuckDice AutoBot - parallel lane %i (CLI)
After=network-online.target
Wants=network-online.target
StartLimitIntervalSec=0

[Service]
Type=simple
Nice=-5
IOSchedulingClass=best-effort
IOSchedulingPriority=0
WorkingDirectory=${INSTALL_DIR}
ExecStart=${INSTALL_DIR}/venv/bin/python3 ${INSTALL_DIR}/duckdice.py --resume --daemon --lane %i
Restart=on-failure
RestartSec=10
StandardOutput=journal
StandardError=journal

KillSignal=SIGTERM
TimeoutStopSec=15

Environment=HOME=${HOME}
Environment=PYTHONUNBUFFERED=1
EnvironmentFile=-${ENV_FILE}

[Install]
WantedBy=default.target
LANESERVICEEOF

# Telegram bot service
cat > "$SYSTEMD_DIR/${TG_SERVICE_NAME}.service" << TGSERVICEEOF
[Unit]
Description=DuckDice AutoBot: Telegram Bot
After=network-online.target
Wants=network-online.target
StartLimitIntervalSec=0

[Service]
Type=simple
WorkingDirectory=${INSTALL_DIR}
ExecStart=${INSTALL_DIR}/venv/bin/python3 -m tg.bot
Restart=on-failure
RestartSec=10
StandardOutput=journal
StandardError=journal

KillSignal=SIGTERM
TimeoutStopSec=15

Environment=HOME=${HOME}
Environment=PYTHONUNBUFFERED=1
EnvironmentFile=${ENV_FILE}

[Install]
WantedBy=default.target
TGSERVICEEOF

# Watchdog: restart TG bot if heartbeat file is stale (catches stuck-but-alive)
cat > "$SYSTEMD_DIR/${TG_SERVICE_NAME}-watchdog.service" << WDSERVICEEOF
[Unit]
Description=DuckDice Telegram Bot: Stall Watchdog
After=${TG_SERVICE_NAME}.service

[Service]
Type=oneshot
ExecStart=/bin/bash -c 'f=/tmp/${TG_SERVICE_NAME}.heartbeat; if [ ! -f "\$f" ] || [ \$(( \$(date +%%s) - \$(stat -c %%Y "\$f") )) -gt 180 ]; then echo "Heartbeat stale, restarting ${TG_SERVICE_NAME}"; systemctl --user restart ${TG_SERVICE_NAME}.service; fi'
WDSERVICEEOF

cat > "$SYSTEMD_DIR/${TG_SERVICE_NAME}-watchdog.timer" << WDTIMEREOF
[Unit]
Description=DuckDice Telegram Bot: Stall Watchdog Timer (every 2 min)

[Timer]
OnBootSec=3min
OnUnitActiveSec=2min
Unit=${TG_SERVICE_NAME}-watchdog.service

[Install]
WantedBy=timers.target
WDTIMEREOF

# Enable lingering so user services run after logout
sudo loginctl enable-linger "$(whoami)" 2>/dev/null || true

systemctl --user daemon-reload
# Boot-enable only (no --now): a bot stopped by hand stays stopped until the next boot.
systemctl --user enable ${SERVICE_NAME} 2>/dev/null || true
systemctl --user enable --now ${TG_SERVICE_NAME}-watchdog.timer 2>/dev/null || true
echo "   Services installed:"
echo "     ${SERVICE_NAME}.service     (CLI bot)"
echo "     ${LANE_SERVICE_NAME}@.service (parallel lanes 2..N)"
echo "     ${TG_SERVICE_NAME}.service  (Telegram bot)"

# ── 6. Management CLI ─────────────────────────────────
echo "[6/7] Creating duckctl management command..."
mkdir -p "$HOME/.local/bin"

if [ -f "duckctl" ]; then
    install -m 0755 duckctl "$DUCKCTL"
else
    echo "   WARNING: duckctl not found in the repo; skipping the management CLI."
    exit 1
fi

# ── 7. Quick-run scripts ──────────────────────────────
echo "[7/7] Creating helper scripts..."

cat > "$INSTALL_DIR/run.sh" << 'EOF'
#!/usr/bin/env bash
DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
"$DIR/venv/bin/python3" "$DIR/duckdice.py" "$@"
EOF
chmod +x "$INSTALL_DIR/run.sh"

cat > "$INSTALL_DIR/run-tg.sh" << 'EOF'
#!/usr/bin/env bash
DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
set -a; source "$DIR/.env" 2>/dev/null; set +a
cd "$DIR" && "$DIR/venv/bin/python3" -m tg.bot "$@"
EOF
chmod +x "$INSTALL_DIR/run-tg.sh"

# ── Done ──────────────────────────────────────────────
echo ""
echo "════════════════════════════════════════════════════════"
echo "  Installation complete!"
echo "════════════════════════════════════════════════════════"
echo ""
echo "  PROJECT STRUCTURE:"
echo "    $INSTALL_DIR/"
echo "    ├── duckdice.py         CLI bot (TUI dashboard)"
echo "    ├── core/               Bot-specific: strategy, DB, engine"
echo "    ├── tg/                 Telegram bot package"
echo "    ├── shared/             Cross-bot shared library"
echo "    ├── .env                Environment config (tokens)"
echo "    └── venv/               Python virtual environment"
echo ""
echo "  ── STEP 1: CLI BOT SETUP ──"
echo "    duckctl setup           Configure API key, strategy"
echo "    duckctl start           Start CLI bot daemon"
echo "    duckctl monitor         Attach live TUI"
echo ""
echo "  ── STEP 2: TELEGRAM BOT SETUP ──"
echo "    1. Open Telegram → search @BotFather"
echo "    2. Send /newbot → choose name → get token"
echo "    3. duckctl tg env       Paste your token"
echo "    4. duckctl tg start     Start Telegram bot"
echo "    5. Open your bot in Telegram → /start"
echo ""
echo "  ── DAILY USE ──"
echo "    duckctl start-all       Start both bots"
echo "    duckctl stop-all        Stop both bots"
echo "    duckctl status-all      Check all services"
echo ""
echo "    duckctl tg status       Telegram bot status"
echo "    duckctl tg logs         Telegram bot logs"
echo "    duckctl tg restart      Restart after config change"
echo ""
echo "  ── FILES ──"
echo "    Bot:        $INSTALL_DIR/"
echo "    CLI Config: ~/.duckdice_autobot.json"
echo "    CLI DB:     ~/.duckdice_autobot.db"
echo "    TG Data:    ~/.duckbot_tg/<user_id>/"
echo "    Env:        $INSTALL_DIR/.env"
echo "    Logs:       duckctl logs / duckctl tg logs"
echo ""
