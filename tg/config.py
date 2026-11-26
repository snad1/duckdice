"""Configuration: environment variables, constants, tier definitions."""

import os
import logging

from shared.tg.redact import RedactingFormatter

TG_KEY_ID = "telegram"


def bot_token() -> str:
    """The bot token from the host key vault, else DUCKDICE_TG_TOKEN; never logged."""
    try:
        from shared.core.vault import vault_secret
    except ImportError:
        return os.environ.get("DUCKDICE_TG_TOKEN", "")
    token = vault_secret("duckdice", TG_KEY_ID, "telegram", log=logging.getLogger("duckbot"))
    return token or os.environ.get("DUCKDICE_TG_TOKEN", "")


# ── Environment ──────────────────────────────────────────
APP_ENV    = os.environ.get("APP_ENV", "production")
BOT_TOKEN  = bot_token()
DATA_DIR   = os.environ.get("DUCKDICE_TG_DATA", os.path.expanduser("~/.duckbot_tg"))
API_BASE   = os.environ.get("DUCKDICE_API_BASE", "https://duckdice.io/api")
WEB_URL    = os.environ.get("DUCKDICE_WEB_URL", "")          # e.g. http://IP:8002
WEB_SECRET = os.environ.get("DUCKDICE_WEB_SECRET_KEY", "")   # must match web app

# ── Rate limit tiers: seconds between bets ───────────────
TIERS = {
    "free":  1.0,    # 1 bet/sec
    "trial": 0.0,    # max speed
}

# ── Logging ──────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
_root = logging.getLogger()
for _handler in _root.handlers:
    _handler.setFormatter(RedactingFormatter("%(asctime)s [%(levelname)s] %(name)s: %(message)s"))
logger = logging.getLogger("duckbot")

# Suppress noisy httpx request logs (Telegram polling etc)
logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("httpcore").setLevel(logging.WARNING)

# ── Config keys persisted per user ───────────────────────
CONFIG_KEYS = [
    "api_key", "currency", "rule", "multiplier", "bet_value",
    "base_bet", "strategy", "strategy_key", "win_mult", "loss_mult",
    "bet_delay", "max_profit", "max_loss", "max_bets", "max_wins",
    "stop_on_balance", "custom_rules_text", "delay_martin_threshold",
    "milestone_bets", "milestone_wins", "milestone_losses", "milestone_profit",
    "profit_increment", "profit_threshold", "profit_increment_scope", "faucet",
    "streak_delay_loss", "streak_delay_win", "streak_delay_bets",
    "streak_reset_losses", "streak_reset_delay",
    "proxy",
]


def get_user_tier(user_id: int) -> str:
    """Return rate-limit tier for a user. Extensible for paid tiers."""
    return "trial"
