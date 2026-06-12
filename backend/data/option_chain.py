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
        """Returns (chain_df, spot). Cached for `max_age` seconds.

        Source order: NSE public chain → Angel One quotes (works for
        SENSEX/BFO and when NSE blocks cloud IPs) → last cached copy.
        """
        cached = self._cache.get(underlying)
        if cached and time.time() - cached[0] < max_age:
            return cached[1], cached[2]

        df, spot = self._fetch_nse(underlying)
        if df.empty:
            df, spot = self._fetch_via_broker(underlying)
        if df.empty and cached:
            return cached[1], cached[2]
        if not df.empty:
            self._cache[underlying] = (time.time(), df, spot)
        return df, spot

    def _fetch_nse(self, underlying: str) -> tuple[pd.DataFrame, float]:
        url = _NSE_URL.get(underlying)
        if url is None:
            return pd.DataFrame(), 0.0
        try:
            self._warm_cookies()
            resp = self._session.get(url, timeout=15)
            resp.raise_for_status()
            payload = resp.json()
        except Exception as exc:  # noqa: BLE001
            log.warning("NSE option chain failed for %s: %s", underlying, exc)
            return pd.DataFrame(), 0.0

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
        if not rows:
            log.warning("NSE chain for %s came back empty", underlying)
            return pd.DataFrame(), spot
        df = pd.DataFrame(rows).sort_values("strike").reset_index(drop=True)
        return df, spot

    def _fetch_via_broker(self, underlying: str) -> tuple[pd.DataFrame, float]:
        """Build the chain from Angel One quotes (OI + LTP per strike)."""
        from backend.broker.angel_one import broker
        from backend.config import INSTRUMENTS
        from backend.data.instruments import instrument_master

        if not broker.is_connected:
            return pd.DataFrame(), 0.0
        try:
            spot = broker.get_ltp(underlying) or 0.0
            if spot <= 0:
                return pd.DataFrame(), 0.0
            opts = instrument_master.option_contracts(underlying)
            if opts.empty:
                return pd.DataFrame(), 0.0
            self.last_expiry[underlying] = str(opts.iloc[0]["expiry"])
            near = opts[(opts["strike"] - spot).abs() <= spot * 0.04]
            exchange = INSTRUMENTS[underlying]["option_exchange"]

            quotes: dict[str, dict] = {}
            tokens = near["token"].astype(str).tolist()
            for i in range(0, len(tokens), 40):   # API: ≤50 tokens/request
                data = broker.api.getMarketData(
                    "FULL", {exchange: tokens[i:i + 40]})
                for item in ((data or {}).get("data") or {}).get("fetched", []):
                    quotes[str(item.get("symbolToken"))] = item
                time.sleep(0.35)                  # respect rate limits

            recs: dict[float, dict] = {}
            for _, r in near.iterrows():
                strike = float(r["strike"])
                d = recs.setdefault(strike, {
                    "strike": strike,
                    "ce_oi": 0, "ce_chg_oi": 0, "ce_volume": 0,
                    "ce_iv": 0, "ce_ltp": 0,
                    "pe_oi": 0, "pe_chg_oi": 0, "pe_volume": 0,
                    "pe_iv": 0, "pe_ltp": 0,
                })
                q = quotes.get(str(r["token"]), {})
                side = "ce" if r["option_type"] == "CE" else "pe"
                d[f"{side}_ltp"] = float(q.get("ltp") or 0)
                d[f"{side}_oi"] = float(q.get("opnInterest")
                                        or q.get("openInterest") or 0)
                d[f"{side}_volume"] = float(q.get("tradeVolume")
                                            or q.get("tradedVolume") or 0)
            if not recs:
                return pd.DataFrame(), spot
            df = (pd.DataFrame(list(recs.values()))
                  .sort_values("strike").reset_index(drop=True))
            log.info("Option chain for %s built from broker quotes "
                     "(%d strikes)", underlying, len(df))
            return df, float(spot)
        except Exception:  # noqa: BLE001
            log.exception("Broker option chain failed for %s", underlying)
            return pd.DataFrame(), 0.0


option_chain_fetcher = OptionChainFetcher()
