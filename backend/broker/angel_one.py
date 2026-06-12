"""
Angel One SmartAPI wrapper.

Handles login with TOTP, automatic session refresh, retries with
exponential backoff, historical candles, LTP, option chain (via the
instrument master), orders, positions, holdings and margin.

All public methods are safe to call from any platform (desktop,
Streamlit, FastAPI) — the client is a process-wide singleton.
"""
from __future__ import annotations

import threading
import time
from datetime import datetime, timedelta
from typing import Any, Optional

import pandas as pd
import pyotp

from backend.config import INSTRUMENTS, settings
from backend.logger import get_logger

log = get_logger(__name__)

try:
    from SmartApi import SmartConnect  # smartapi-python
    SMARTAPI_AVAILABLE = True
except ImportError:  # pragma: no cover - dev machines without the SDK
    SMARTAPI_AVAILABLE = False
    log.warning("smartapi-python not installed — broker runs in offline mode")

_INTERVAL_MAP = {
    "1min": "ONE_MINUTE",
    "5min": "FIVE_MINUTE",
    "10min": "TEN_MINUTE",
    "15min": "FIFTEEN_MINUTE",
    "20min": "FIFTEEN_MINUTE",  # 20m is resampled from 5m candles
    "30min": "THIRTY_MINUTE",
    "1day": "ONE_DAY",
}


def _retry(max_attempts: int = 4, base_delay: float = 1.0):
    """Retry decorator with exponential backoff for flaky API calls."""
    def decorator(fn):
        def wrapper(*args, **kwargs):
            last_exc: Optional[Exception] = None
            for attempt in range(max_attempts):
                try:
                    return fn(*args, **kwargs)
                except Exception as exc:  # noqa: BLE001
                    last_exc = exc
                    delay = base_delay * (2 ** attempt)
                    log.warning(
                        "%s failed (attempt %d/%d): %s — retrying in %.1fs",
                        fn.__name__, attempt + 1, max_attempts, exc, delay,
                    )
                    time.sleep(delay)
            raise last_exc  # type: ignore[misc]
        return wrapper
    return decorator


class AngelOneClient:
    """Thread-safe singleton around SmartConnect."""

    _instance: Optional["AngelOneClient"] = None
    _lock = threading.Lock()

    def __new__(cls) -> "AngelOneClient":
        with cls._lock:
            if cls._instance is None:
                cls._instance = super().__new__(cls)
                cls._instance._initialised = False
            return cls._instance

    def __init__(self) -> None:
        if self._initialised:
            return
        self._initialised = True
        self.api: Optional[Any] = None
        self.session: dict = {}
        self.feed_token: str = ""
        self._session_time: Optional[datetime] = None
        self._api_lock = threading.Lock()

    # ── Session ────────────────────────────────────────────────────

    @property
    def is_connected(self) -> bool:
        return self.api is not None and bool(self.session)

    def login(self) -> bool:
        """Login with API key + client id + PIN + TOTP."""
        if not SMARTAPI_AVAILABLE:
            log.error("Cannot login: smartapi-python is not installed")
            return False
        if not all([settings.angel_api_key, settings.angel_client_id,
                    settings.angel_password, settings.angel_totp_secret]):
            log.error("Cannot login: missing Angel One credentials in .env")
            return False
        try:
            self.api = SmartConnect(api_key=settings.angel_api_key)
            totp = pyotp.TOTP(settings.angel_totp_secret).now()
            data = self.api.generateSession(
                settings.angel_client_id, settings.angel_password, totp
            )
            if not data or not data.get("status"):
                log.error("Login rejected: %s", data)
                return False
            self.session = data["data"]
            self.feed_token = self.api.getfeedToken()
            self._session_time = datetime.now()
            log.info("Angel One login successful for %s", settings.angel_client_id)
            return True
        except Exception:
            log.exception("Angel One login failed")
            return False

    def ensure_session(self) -> bool:
        """Re-login automatically when the session is stale (>6h)."""
        if not self.is_connected:
            return self.login()
        if self._session_time and datetime.now() - self._session_time > timedelta(hours=6):
            log.info("Session older than 6h — refreshing tokens")
            try:
                refresh = self.session.get("refreshToken", "")
                data = self.api.generateToken(refresh)
                if data and data.get("status"):
                    self.session.update(data["data"])
                    self._session_time = datetime.now()
                    return True
            except Exception:
                log.warning("Token refresh failed — doing a fresh login")
            return self.login()
        return True

    def logout(self) -> None:
        if self.is_connected:
            try:
                self.api.terminateSession(settings.angel_client_id)
            except Exception:
                pass
        self.api, self.session = None, {}

    # ── Market data ────────────────────────────────────────────────

    @_retry()
    def get_ltp(self, name: str) -> Optional[float]:
        """Last traded price for a watchlist instrument."""
        self.ensure_session()
        meta = INSTRUMENTS[name]
        with self._api_lock:
            data = self.api.ltpData(meta["exchange"], meta["symbol"], meta["token"])
        if data and data.get("status"):
            return float(data["data"]["ltp"])
        return None

    @_retry()
    def get_candles(self, name: str, interval: str = "5min",
                    days: int = 5) -> pd.DataFrame:
        """
        Historical OHLCV candles as a DataFrame indexed by timestamp.
        20min candles are resampled from 5min data.
        """
        self.ensure_session()
        meta = INSTRUMENTS[name]
        fetch_interval = "5min" if interval == "20min" else interval
        to_dt = datetime.now()
        from_dt = to_dt - timedelta(days=days)
        params = {
            "exchange": meta["exchange"],
            "symboltoken": meta["token"],
            "interval": _INTERVAL_MAP[fetch_interval],
            "fromdate": from_dt.strftime("%Y-%m-%d %H:%M"),
            "todate": to_dt.strftime("%Y-%m-%d %H:%M"),
        }
        with self._api_lock:
            data = self.api.getCandleData(params)
        if not data or not data.get("data"):
            return pd.DataFrame()
        df = pd.DataFrame(
            data["data"],
            columns=["timestamp", "open", "high", "low", "close", "volume"],
        )
        df["timestamp"] = pd.to_datetime(df["timestamp"])
        df = df.set_index("timestamp").astype(float)
        if interval == "20min":
            df = (
                df.resample("20min")
                .agg({"open": "first", "high": "max", "low": "min",
                      "close": "last", "volume": "sum"})
                .dropna()
            )
        return df

    @_retry()
    def get_candles_range(self, name: str, interval: str,
                          from_dt: datetime, to_dt: datetime) -> pd.DataFrame:
        """Historical candles for an explicit date range (for backfills)."""
        self.ensure_session()
        meta = INSTRUMENTS[name]
        params = {
            "exchange": meta["exchange"],
            "symboltoken": meta["token"],
            "interval": _INTERVAL_MAP[interval],
            "fromdate": from_dt.strftime("%Y-%m-%d %H:%M"),
            "todate": to_dt.strftime("%Y-%m-%d %H:%M"),
        }
        with self._api_lock:
            data = self.api.getCandleData(params)
        if not data or not data.get("data"):
            return pd.DataFrame()
        df = pd.DataFrame(
            data["data"],
            columns=["timestamp", "open", "high", "low", "close", "volume"],
        )
        df["timestamp"] = pd.to_datetime(df["timestamp"])
        return df.set_index("timestamp").astype(float)

    # ── Orders / portfolio ─────────────────────────────────────────

    @_retry(max_attempts=2)
    def place_order(self, tradingsymbol: str, symboltoken: str, exchange: str,
                    transaction: str, quantity: int,
                    order_type: str = "MARKET", price: float = 0.0) -> Optional[str]:
        """Place an order. Returns the broker order id."""
        self.ensure_session()
        params = {
            "variety": "NORMAL",
            "tradingsymbol": tradingsymbol,
            "symboltoken": symboltoken,
            "transactiontype": transaction,        # BUY / SELL
            "exchange": exchange,                  # NFO / BFO
            "ordertype": order_type,               # MARKET / LIMIT
            "producttype": "INTRADAY",
            "duration": "DAY",
            "price": str(price),
            "squareoff": "0",
            "stoploss": "0",
            "quantity": str(quantity),
        }
        with self._api_lock:
            order_id = self.api.placeOrder(params)
        log.info("Order placed: %s %s x%d -> id=%s",
                 transaction, tradingsymbol, quantity, order_id)
        return order_id

    @_retry()
    def get_positions(self) -> list[dict]:
        self.ensure_session()
        data = self.api.position()
        return data.get("data") or []

    @_retry()
    def get_holdings(self) -> list[dict]:
        self.ensure_session()
        data = self.api.holding()
        return data.get("data") or []

    @_retry()
    def get_order_book(self) -> list[dict]:
        self.ensure_session()
        data = self.api.orderBook()
        return data.get("data") or []

    @_retry()
    def get_margin(self) -> dict:
        self.ensure_session()
        data = self.api.rmsLimit()
        return data.get("data") or {}


broker = AngelOneClient()
