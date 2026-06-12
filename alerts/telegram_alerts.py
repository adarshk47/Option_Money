"""Telegram alert channel — plain HTTPS call, no long-running bot needed."""
from __future__ import annotations

import requests

from backend.config import settings
from backend.logger import get_logger

log = get_logger(__name__)


class TelegramAlert:
    URL = "https://api.telegram.org/bot{token}/sendMessage"

    def send(self, title: str, message: str, speak: str | None = None) -> None:
        if not settings.telegram_bot_token or not settings.telegram_chat_id:
            return
        resp = requests.post(
            self.URL.format(token=settings.telegram_bot_token),
            json={"chat_id": settings.telegram_chat_id,
                  "text": f"{title}\n\n{message}"},
            timeout=10,
        )
        if not resp.ok:
            log.warning("Telegram send failed: %s", resp.text[:200])
