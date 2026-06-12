"""
Project-wide logging. Every module does:

    from backend.logger import get_logger
    log = get_logger(__name__)

Logs go to console and to a rotating file in logs/.
"""
import logging
import sys
from logging.handlers import RotatingFileHandler
from pathlib import Path

from backend.config import PROJECT_ROOT, settings

LOG_DIR = PROJECT_ROOT / "logs"
LOG_DIR.mkdir(exist_ok=True)

_FORMAT = "%(asctime)s | %(levelname)-7s | %(name)s | %(message)s"
_configured = False


def _configure_root() -> None:
    global _configured
    if _configured:
        return
    root = logging.getLogger()
    root.setLevel(settings.log_level)

    console = logging.StreamHandler(sys.stdout)
    console.setFormatter(logging.Formatter(_FORMAT))
    root.addHandler(console)

    file_handler = RotatingFileHandler(
        LOG_DIR / "trading.log", maxBytes=5_000_000, backupCount=5
    )
    file_handler.setFormatter(logging.Formatter(_FORMAT))
    root.addHandler(file_handler)
    _configured = True


def get_logger(name: str) -> logging.Logger:
    _configure_root()
    return logging.getLogger(name)
