"""
Central configuration — loads everything from .env so no credential
ever lives in source code. Import `settings` everywhere.
"""
import os
from dataclasses import dataclass, field
from pathlib import Path

from dotenv import load_dotenv

PROJECT_ROOT = Path(__file__).resolve().parent.parent
load_dotenv(PROJECT_ROOT / ".env")


def _bool(key: str, default: str = "false") -> bool:
    return os.getenv(key, default).strip().lower() in ("1", "true", "yes", "on")


@dataclass(frozen=True)
class Settings:
    # Broker
    angel_api_key: str = os.getenv("ANGEL_API_KEY", "")
    angel_client_id: str = os.getenv("ANGEL_CLIENT_ID", "")
    angel_password: str = os.getenv("ANGEL_PASSWORD", "")
    angel_totp_secret: str = os.getenv("ANGEL_TOTP_SECRET", "")

    # Mode
    trading_mode: str = os.getenv("TRADING_MODE", "PAPER").upper()

    # Risk
    max_capital_per_trade: float = float(os.getenv("MAX_CAPITAL_PER_TRADE", 10000))
    max_trades_per_day: int = int(os.getenv("MAX_TRADES_PER_DAY", 10))
    max_daily_loss: float = float(os.getenv("MAX_DAILY_LOSS", 5000))
    default_sl_percent: float = float(os.getenv("DEFAULT_SL_PERCENT", 20))
    default_target1_percent: float = float(os.getenv("DEFAULT_TARGET1_PERCENT", 25))
    default_target2_percent: float = float(os.getenv("DEFAULT_TARGET2_PERCENT", 50))

    # Alerts
    telegram_bot_token: str = os.getenv("TELEGRAM_BOT_TOKEN", "")
    telegram_chat_id: str = os.getenv("TELEGRAM_CHAT_ID", "")
    voice_alerts: bool = _bool("VOICE_ALERTS", "true")

    # Database
    database_url: str = os.getenv(
        "DATABASE_URL", f"sqlite:///{PROJECT_ROOT / 'database' / 'trading.db'}"
    )

    # API server
    api_host: str = os.getenv("API_HOST", "0.0.0.0")
    api_port: int = int(os.getenv("API_PORT", 8000))
    api_secret_key: str = os.getenv("API_SECRET_KEY", "change_me")

    # Misc
    log_level: str = os.getenv("LOG_LEVEL", "INFO")
    refresh_seconds: int = int(os.getenv("REFRESH_SECONDS", 5))

    # Markets we trade (options buying only)
    watchlist: tuple = ("NIFTY", "SENSEX", "SBIN")
    timeframes: tuple = ("1min", "5min", "10min", "15min", "20min")

    @property
    def is_paper(self) -> bool:
        return self.trading_mode != "LIVE"


settings = Settings()

# Index/stock metadata used across the system.
INSTRUMENTS = {
    "NIFTY": {
        "exchange": "NSE", "token": "99926000", "symbol": "Nifty 50",
        "lot_size": 75, "strike_step": 50, "option_exchange": "NFO",
        "weekly_expiry": True,
    },
    "BANKNIFTY": {
        "exchange": "NSE", "token": "99926009", "symbol": "Nifty Bank",
        "lot_size": 35, "strike_step": 100, "option_exchange": "NFO",
        "weekly_expiry": False,
    },
    "SENSEX": {
        "exchange": "BSE", "token": "99919000", "symbol": "SENSEX",
        "lot_size": 20, "strike_step": 100, "option_exchange": "BFO",
        "weekly_expiry": True,
    },
    "SBIN": {
        "exchange": "NSE", "token": "3045", "symbol": "SBIN-EQ",
        "lot_size": 750, "strike_step": 10, "option_exchange": "NFO",
        "weekly_expiry": False,
    },
}
