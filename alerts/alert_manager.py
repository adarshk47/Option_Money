"""
Unified alert dispatcher — fans one message out to Telegram, desktop
popup, voice and (via FastAPI push registry) mobile notifications.
Each channel fails independently without breaking the others.
"""
from __future__ import annotations

import threading

from backend.config import settings
from backend.logger import get_logger

log = get_logger(__name__)


class AlertManager:
    def __init__(self) -> None:
        self._channels = []
        try:
            from alerts.telegram_alerts import TelegramAlert
            if settings.telegram_bot_token:
                self._channels.append(TelegramAlert())
        except Exception:
            log.exception("Telegram channel unavailable")
        try:
            from alerts.voice_alerts import VoiceAlert
            if settings.voice_alerts:
                self._channels.append(VoiceAlert())
        except Exception:
            log.warning("Voice channel unavailable (pyttsx3 missing?)")
        try:
            from alerts.desktop_alerts import DesktopAlert
            self._channels.append(DesktopAlert())
        except Exception:
            log.warning("Desktop notification channel unavailable")

    def send(self, title: str, message: str, speak: str | None = None) -> None:
        """Dispatch async so trading loops are never blocked by I/O."""
        def _dispatch():
            for ch in self._channels:
                try:
                    ch.send(title, message, speak)
                except Exception:
                    log.exception("Alert channel %s failed", type(ch).__name__)
        threading.Thread(target=_dispatch, daemon=True).start()

    def signal_alert(self, rec) -> None:
        """Format a Recommendation into a trade alert."""
        if rec.action in ("AVOID TRADE", "SIDEWAYS - AVOID"):
            return
        msg = (
            f"📊 {rec.underlying} [{rec.timeframe}]\n"
            f"🎯 {rec.action} | Confidence {rec.confidence}%\n"
            f"Entry(spot): {rec.entry_price} | SL: {rec.stop_loss}\n"
            f"T1: {rec.target1} | T2: {rec.target2}\n"
            f"R:R {rec.risk_reward} | Risk: {rec.risk_level}\n"
            f"Reason: {'; '.join(rec.reasoning[:3])}"
        )
        speak = f"{rec.action} on {rec.underlying}, confidence {rec.confidence:.0f} percent"
        self.send(f"🚨 {rec.action} — {rec.underlying}", msg, speak)


alert_manager = AlertManager()
