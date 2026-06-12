"""Momentum breakout strategy — EMA stack + VWAP + volume confirmation."""
from __future__ import annotations

from typing import Optional

import pandas as pd

from strategies.base import Strategy


class MomentumStrategy(Strategy):
    name = "momentum_breakout"

    def __init__(self, adx_min: float = 22.0, vol_mult: float = 1.4):
        self.adx_min = adx_min
        self.vol_mult = vol_mult

    def on_bar(self, df: pd.DataFrame, i: int) -> Optional[dict]:
        if i < 50:
            return None
        row, prev = df.iloc[i], df.iloc[i - 1]
        vol_ok = row["vol_sma20"] > 0 and row["volume"] > self.vol_mult * row["vol_sma20"]
        if row["adx"] < self.adx_min or not vol_ok:
            return None

        bull = (row["ema9"] > row["ema20"] > row["ema50"]
                and row["close"] > row["vwap"]
                and prev["close"] <= prev["ema9"] and row["close"] > row["ema9"])
        bear = (row["ema9"] < row["ema20"] < row["ema50"]
                and row["close"] < row["vwap"]
                and prev["close"] >= prev["ema9"] and row["close"] < row["ema9"])

        if bull:
            return {"direction": "CE", "sl_pct": 0.35, "t1_pct": 0.45,
                    "t2_pct": 0.90, "reason": "Momentum breakout long"}
        if bear:
            return {"direction": "PE", "sl_pct": 0.35, "t1_pct": 0.45,
                    "t2_pct": 0.90, "reason": "Momentum breakdown short"}
        return None
