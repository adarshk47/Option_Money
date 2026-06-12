"""Desktop popup notifications via plyer (Windows/macOS/Linux)."""
from __future__ import annotations

from backend.logger import get_logger

log = get_logger(__name__)


class DesktopAlert:
    def __init__(self) -> None:
        from plyer import notification  # ImportError -> channel skipped
        self._notification = notification

    def send(self, title: str, message: str, speak: str | None = None) -> None:
        try:
            self._notification.notify(
                title=title[:64],
                message=message[:256],
                app_name="OptionMoney AI",
                timeout=8,
            )
        except Exception:
            # headless servers (Streamlit Cloud) have no notification daemon
            log.debug("Desktop notification unavailable in this environment")
