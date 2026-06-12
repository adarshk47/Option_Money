"""
Option chain builder.

Primary source: NSE public option-chain endpoint (free, includes OI,
change-in-OI, volume, IV per strike). Falls back gracefully when NSE
throttles. SENSEX uses Angel One market data for BFO contracts.

Output is a normalised DataFrame:
    strike | ce_oi | ce_chg_oi | ce_volume | ce_iv | ce_ltp
           | pe_oi | pe_chg_oi | pe_volume | pe_iv | pe_ltp
"""
from __future__ import annotations

import time
from typing import Optional

import pandas as pd
import requests

from backend.logger import get_logger

log = get_logger(__name__)

NSE_HEADERS = {
    "User-Agent": ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                   "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120 Safari/537.36"),
    "Accept": "application/json",
    "Accept-Language": "en-US,en;q=0.9",
}
_NSE_URL = {
    "NIFTY": "https://www.nseindia.com/api/option-chain-indices?symbol=NIFTY",
    "BANKNIFTY": "https://www.nseindia.com/api/option-chain-indices?symbol=BANKNIFTY",
    "SBIN": "https://www.nseindia.com/api/option-chain-equities?symbol=SBIN",
}


class OptionChainFetcher:
    def __init__(self) -> None:
        self._session = requests.Session()
        self._session.headers.update(NSE_HEADERS)
        self._cookies_ts = 0.0
        self._cache: dict[str, tuple[float, pd.DataFrame, float]] = {}
        self.last_expiry: dict[str, str] = {}   # underlying -> nearest expiry

    def _warm_cookies(self) -> None:
        if time.time() - self._cookies_ts < 300:
            return
        try:
            self._session.get("https://www.nseindia.com", timeout=10)
            self._cookies_ts = time.time()
        except requests.RequestException as exc:
            log.warning("NSE cookie warmup failed: %s", exc)

    def fetch(self, underlying: str,
              max_age: float = 60.0) -> tuple[pd.DataFrame, float]:
        """Returns (chain_df, spot). Cached for `max_age` seconds."""
        cached = self._cache.get(underlying)
        if cached and time.time() - cached[0] < max_age:
            return cached[1], cached[2]

        url = _NSE_URL.get(underlying)
        if url is None:
            log.info("No NSE chain for %s (use Angel One BFO data)", underlying)
            return pd.DataFrame(), 0.0
        try:
            self._warm_cookies()
            resp = self._session.get(url, timeout=15)
            resp.raise_for_status()
            payload = resp.json()
        except Exception as exc:  # noqa: BLE001
            log.warning("Option chain fetch failed for %s: %s", underlying, exc)
            return (cached[1], cached[2]) if cached else (pd.DataFrame(), 0.0)

        records = payload.get("records", {})
        spot = float(records.get("underlyingValue") or 0.0)
        nearest_expiry = (records.get("expiryDates") or [None])[0]
        if nearest_expiry:
            self.last_expiry[underlying] = nearest_expiry
        rows = []
        for item in records.get("data", []):
            if item.get("expiryDate") != nearest_expiry:
                continue
            ce, pe = item.get("CE", {}), item.get("PE", {})
            rows.append({
                "strike": item["strikePrice"],
                "ce_oi": ce.get("openInterest", 0),
                "ce_chg_oi": ce.get("changeinOpenInterest", 0),
                "ce_volume": ce.get("totalTradedVolume", 0),
                "ce_iv": ce.get("impliedVolatility", 0),
                "ce_ltp": ce.get("lastPrice", 0),
                "pe_oi": pe.get("openInterest", 0),
                "pe_chg_oi": pe.get("changeinOpenInterest", 0),
                "pe_volume": pe.get("totalTradedVolume", 0),
                "pe_iv": pe.get("impliedVolatility", 0),
                "pe_ltp": pe.get("lastPrice", 0),
            })
        df = pd.DataFrame(rows).sort_values("strike").reset_index(drop=True)
        self._cache[underlying] = (time.time(), df, spot)
        return df, spot


option_chain_fetcher = OptionChainFetcher()
