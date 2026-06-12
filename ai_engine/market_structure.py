"""
Market structure analysis — swing highs/lows, HH/HL vs LH/LL trends,
breakouts/breakdowns, and support/resistance zones.
"""
from __future__ import annotations

import numpy as np
import pandas as pd


def swing_points(df: pd.DataFrame, lookback: int = 3) -> tuple[list, list]:
    """Confirmed swing highs and lows as [(index, price), ...]."""
    highs, lows = [], []
    h, l = df["high"].values, df["low"].values
    for i in range(lookback, len(df) - lookback):
        if h[i] == max(h[i - lookback:i + lookback + 1]):
            highs.append((df.index[i], float(h[i])))
        if l[i] == min(l[i - lookback:i + lookback + 1]):
            lows.append((df.index[i], float(l[i])))
    return highs, lows


def analyse_structure(df: pd.DataFrame) -> dict:
    """
    Returns:
        trend: UPTREND / DOWNTREND / SIDEWAYS
        structure events: higher_high, lower_low, breakout, breakdown, reversal
        support / resistance levels
    """
    result = {
        "trend": "SIDEWAYS", "higher_high": False, "higher_low": False,
        "lower_high": False, "lower_low": False, "breakout": False,
        "breakdown": False, "reversal": None,
        "support": None, "resistance": None,
    }
    if len(df) < 30:
        return result

    highs, lows = swing_points(df)
    if len(highs) >= 2:
        result["higher_high"] = highs[-1][1] > highs[-2][1]
        result["lower_high"] = highs[-1][1] < highs[-2][1]
    if len(lows) >= 2:
        result["higher_low"] = lows[-1][1] > lows[-2][1]
        result["lower_low"] = lows[-1][1] < lows[-2][1]

    if result["higher_high"] and result["higher_low"]:
        result["trend"] = "UPTREND"
    elif result["lower_high"] and result["lower_low"]:
        result["trend"] = "DOWNTREND"

    close = float(df["close"].iloc[-1])

    # Nearest S/R from swing clusters
    res_levels = sorted({round(p, 1) for _, p in highs[-6:] if p > close})
    sup_levels = sorted({round(p, 1) for _, p in lows[-6:] if p < close}, reverse=True)
    result["resistance"] = res_levels[0] if res_levels else float(df["high"].tail(30).max())
    result["support"] = sup_levels[0] if sup_levels else float(df["low"].tail(30).min())

    # Breakout / breakdown of recent consolidation range (excluding last bar)
    recent = df.iloc[-21:-1]
    range_high, range_low = recent["high"].max(), recent["low"].min()
    result["breakout"] = close > range_high
    result["breakdown"] = close < range_low

    # Reversal: trend was down but two consecutive higher lows printed (or inverse)
    if result["trend"] == "DOWNTREND" and result["higher_low"]:
        result["reversal"] = "BULLISH_REVERSAL_FORMING"
    elif result["trend"] == "UPTREND" and result["lower_high"]:
        result["reversal"] = "BEARISH_REVERSAL_FORMING"

    return result
