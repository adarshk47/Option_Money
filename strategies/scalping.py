"""Backtestable version of the scalping triggers (VWAP cross + ORB)."""
from __future__ import annotations

from typing import Optional

import pandas as pd

from strategies.base import Strategy


class ScalpingStrategy(Strategy):
    name = "vwap_scalp"

    def on_bar(self, df: pd.DataFrame, i: int) -> Optional[dict]:
        if i < 40:
            return None
        row, prev = df.iloc[i], df.iloc[i - 1]
        vol_ok = row["vol_sma20"] > 0 and row["volume"] > 1.3 * row["vol_sma20"]
        if not vol_ok:
            return None
        if prev["close"] < prev["vwap"] and row["close"] > row["vwap"]:
            return {"direction": "CE", "sl_pct": 0.20, "t1_pct": 0.25,
                    "t2_pct": 0.50, "reason": "VWAP reclaim scalp"}
        if prev["close"] > prev["vwap"] and row["close"] < row["vwap"]:
            return {"direction": "PE", "sl_pct": 0.20, "t1_pct": 0.25,
                    "t2_pct": 0.50, "reason": "VWAP reject scalp"}
        return None
