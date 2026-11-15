"""Application settings loaded from environment / .env file."""

import sys
import os
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from pydantic import model_validator  # noqa: E402
from pydantic_settings import BaseSettings  # noqa: E402
from shared.web.settings import (  # noqa: E402
    DEFAULT_ADMIN_PASS, DEFAULT_SECRET_KEY, validate_web_credentials,
)

_env = os.getenv("APP_ENV", "production")
_env_file = ".env.testing" if _env == "testing" else ".env"


class Settings(BaseSettings):
    # ── App ──
    app_name: str = "DuckDice Admin"
    app_env: str = _env
    debug: bool = False
    host: str = "127.0.0.1"
    port: int = 8003

    # ── Auth ──
    secret_key: str = DEFAULT_SECRET_KEY
    admin_user: str = "admin"
    admin_pass: str = DEFAULT_ADMIN_PASS
    jwt_algorithm: str = "HS256"
    jwt_expire_hours: int = 24
    tg_token_expire_minutes: int = 5

    # ── Paths ──
    db_path: str = os.path.expanduser("~/.duckdice_web.db")
    bot_data_dir: str = os.path.expanduser("~/.duckbot_tg")
    install_dir: str = os.path.expanduser("~/duckdice-bot")
    repo_dir: str = os.path.expanduser("~/duckdice")

    # ── Services ──
    service_cli: str = "duckdice"
    service_tg: str = "duckdice-tg"
    service_web: str = "duckdice-web"

    # ── Telegram ──
    tg_bot_token: str = ""

    model_config = {"env_prefix": "DUCKDICE_WEB_", "env_file": _env_file, "extra": "ignore"}

    @model_validator(mode="after")
    def _reject_weak_auth(self):
        validate_web_credentials(self.secret_key, self.admin_pass, "DUCKDICE_WEB_")
        return self


settings = Settings()

# Resolved paths
BASE_DIR = Path(__file__).resolve().parent
TEMPLATES_DIR = BASE_DIR / "templates"
STATIC_DIR = BASE_DIR / "static"
