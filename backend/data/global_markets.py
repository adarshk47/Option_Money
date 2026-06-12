"""
Global market ticker — Indian indices + world markets via Yahoo Finance's
public chart endpoint (no API key). Fetched in parallel, cached by caller.
"""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor

import requests

from backend.logger import get_logger

log = get_logger(__name__)

TICKERS = {
    "NIFTY 50": "^NSEI",
    "BANKNIFTY": "^NSEBANK",
    "SENSEX": "^BSESN",
    "INDIA VIX": "^INDIAVIX",
    "S&P 500": "^GSPC",
    "DOW": "^DJI",
    "NASDAQ": "^IXIC",
    "NIKKEI": "^N225",
    "HANG SENG": "^HSI",
}

_HEADERS = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"}
_URL = "https://query1.finance.yahoo.com/v8/finance/chart/{sym}?range=5d&interval=1d"


def _fetch_one(name: str, sym: str) -> tuple[str, dict | None]:
    try:
        resp = requests.get(_URL.format(sym=sym), headers=_HEADERS, timeout=8)
        resp.raise_for_status()
        meta = resp.json()["chart"]["result"][0]["meta"]
        price = float(meta["regularMarketPrice"])
        prev = float(meta.get("chartPreviousClose")
                     or meta.get("previousClose") or price)
        return name, {"price": price,
                      "chg_pct": (price / prev - 1) * 100 if prev else 0.0}
    except Exception as exc:  # noqa: BLE001
        log.debug("Global quote failed for %s: %s", sym, exc)
        return name, None


def global_quotes() -> dict[str, dict | None]:
    """{'NIFTY 50': {'price': .., 'chg_pct': ..}, ...} — None on failure."""
    with ThreadPoolExecutor(max_workers=6) as pool:
        results = pool.map(lambda kv: _fetch_one(*kv), TICKERS.items())
    return dict(results)
