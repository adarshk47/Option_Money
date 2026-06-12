"""
Real-time tick feed over Angel One SmartWebSocketV2.

Usage:
    feed = LiveFeed(on_tick=my_callback)
    feed.subscribe(["NIFTY", "SBIN"])
    feed.start()

Ticks are also cached in `feed.latest` ({name: {"ltp": .., "ts": ..}})
so pollers (Streamlit) can read the last price without a callback.
Reconnects automatically with backoff if the socket drops.
"""
from __future__ import annotations

import threading
import time
from datetime import datetime
from typing import Callable, Optional

from backend.broker.angel_one import broker
from backend.config import INSTRUMENTS, settings
from backend.logger import get_logger

log = get_logger(__name__)

try:
    from SmartApi.smartWebSocketV2 import SmartWebSocketV2
    WS_AVAILABLE = True
except ImportError:  # pragma: no cover
    WS_AVAILABLE = False

_EXCHANGE_TYPE = {"NSE": 1, "NFO": 2, "BSE": 3, "BFO": 4}


class LiveFeed:
    def __init__(self, on_tick: Optional[Callable[[str, dict], None]] = None):
        self.on_tick = on_tick
        self.latest: dict[str, dict] = {}
        self._token_to_name: dict[str, str] = {}
        self._subscriptions: list[dict] = []
        self._ws = None
        self._running = False
        self._thread: Optional[threading.Thread] = None

    def subscribe(self, names: list[str]) -> None:
        groups: dict[int, list[str]] = {}
        for name in names:
            meta = INSTRUMENTS[name]
            ex = _EXCHANGE_TYPE[meta["exchange"]]
            groups.setdefault(ex, []).append(meta["token"])
            self._token_to_name[meta["token"]] = name
        self._subscriptions = [
            {"exchangeType": ex, "tokens": tokens} for ex, tokens in groups.items()
        ]

    def start(self) -> None:
        if not WS_AVAILABLE:
            log.warning("SmartWebSocketV2 unavailable — live feed disabled")
            return
        if not broker.ensure_session():
            log.error("Cannot start feed: broker session unavailable")
            return
        self._running = True
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._running = False
        if self._ws:
            try:
                self._ws.close_connection()
            except Exception:
                pass

    # ── internals ──────────────────────────────────────────────────

    def _run(self) -> None:
        backoff = 2
        while self._running:
            try:
                self._connect()
                backoff = 2  # reset after a clean run
            except Exception:
                log.exception("Websocket crashed")
            if self._running:
                log.info("Reconnecting websocket in %ds", backoff)
                time.sleep(backoff)
                backoff = min(backoff * 2, 60)

    def _connect(self) -> None:
        self._ws = SmartWebSocketV2(
            auth_token=broker.session.get("jwtToken", ""),
            api_key=settings.angel_api_key,
            client_code=settings.angel_client_id,
            feed_token=broker.feed_token,
        )

        def on_data(_ws, message: dict) -> None:
            token = str(message.get("token", "")).strip('"')
            name = self._token_to_name.get(token)
            if not name:
                return
            tick = {
                "ltp": message.get("last_traded_price", 0) / 100.0,
                "volume": message.get("volume_trade_for_the_day", 0),
                "oi": message.get("open_interest", 0),
                "ts": datetime.now().isoformat(),
            }
            self.latest[name] = tick
            if self.on_tick:
                try:
                    self.on_tick(name, tick)
                except Exception:
                    log.exception("on_tick callback failed")

        def on_open(_ws) -> None:
            log.info("Websocket connected — subscribing %d groups",
                     len(self._subscriptions))
            self._ws.subscribe("trading-feed", 1, self._subscriptions)

        def on_error(_ws, error) -> None:
            log.error("Websocket error: %s", error)

        self._ws.on_data = on_data
        self._ws.on_open = on_open
        self._ws.on_error = on_error
        self._ws.connect()
