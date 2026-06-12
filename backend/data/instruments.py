"""
Angel One instrument master — downloaded once a day and cached locally.
Used to resolve option contract symbols/tokens for order placement and
option-chain construction.
"""
from __future__ import annotations

import json
import time
from datetime import datetime
from pathlib import Path
from typing import Optional

import pandas as pd
import requests

from backend.config import INSTRUMENTS, PROJECT_ROOT
from backend.logger import get_logger

log = get_logger(__name__)

MASTER_URL = (
    "https://margincalculator.angelbroking.com/OpenAPI_File/files/"
    "OpenAPIScripMaster.json"
)
CACHE_FILE = PROJECT_ROOT / "database" / "instrument_master.json"
CACHE_TTL = 24 * 3600


class InstrumentMaster:
    def __init__(self) -> None:
        self._df: Optional[pd.DataFrame] = None

    def load(self, force: bool = False) -> pd.DataFrame:
        if self._df is not None and not force:
            return self._df
        if (not force and CACHE_FILE.exists()
                and time.time() - CACHE_FILE.stat().st_mtime < CACHE_TTL):
            log.info("Loading instrument master from cache")
            data = json.loads(CACHE_FILE.read_text())
        else:
            log.info("Downloading instrument master (~100k rows)")
            resp = requests.get(MASTER_URL, timeout=60)
            resp.raise_for_status()
            data = resp.json()
            CACHE_FILE.parent.mkdir(exist_ok=True)
            CACHE_FILE.write_text(json.dumps(data))
        self._df = pd.DataFrame(data)
        return self._df

    def option_contracts(self, underlying: str,
                         expiry: Optional[str] = None) -> pd.DataFrame:
        """
        All option contracts for an underlying, nearest expiry by default.
        Returns columns: symbol, token, strike, option_type, expiry, lotsize.
        """
        meta = INSTRUMENTS[underlying]
        df = self.load()
        base = underlying if underlying != "SBIN" else "SBIN"
        opts = df[
            (df["exch_seg"] == meta["option_exchange"])
            & (df["name"] == base)
            & (df["instrumenttype"].isin(["OPTIDX", "OPTSTK"]))
        ].copy()
        if opts.empty:
            return opts
        opts["expiry_dt"] = pd.to_datetime(opts["expiry"], format="%d%b%Y",
                                           errors="coerce")
        opts = opts[opts["expiry_dt"] >= pd.Timestamp(datetime.now().date())]
        if expiry:
            opts = opts[opts["expiry"] == expiry]
        else:
            nearest = opts["expiry_dt"].min()
            opts = opts[opts["expiry_dt"] == nearest]
        opts["strike"] = opts["strike"].astype(float) / 100.0
        opts["option_type"] = opts["symbol"].str[-2:]  # CE / PE
        return opts[["symbol", "token", "strike", "option_type",
                     "expiry", "lotsize"]].reset_index(drop=True)

    def find_atm_option(self, underlying: str, spot: float,
                        option_type: str) -> Optional[dict]:
        """Nearest ATM contract of the requested type (for fast order entry)."""
        meta = INSTRUMENTS[underlying]
        step = meta["strike_step"]
        atm_strike = round(spot / step) * step
        opts = self.option_contracts(underlying)
        if opts.empty:
            return None
        match = opts[(opts["strike"] == atm_strike)
                     & (opts["option_type"] == option_type)]
        if match.empty:
            # fall back to the closest available strike
            typed = opts[opts["option_type"] == option_type].copy()
            if typed.empty:
                return None
            typed["dist"] = (typed["strike"] - atm_strike).abs()
            match = typed.nsmallest(1, "dist")
        return match.iloc[0].to_dict()


instrument_master = InstrumentMaster()
