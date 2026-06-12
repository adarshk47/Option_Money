"""
Historical pattern matcher ("analog days").

Compares today's intraday path (5-min cumulative returns since open)
against the same time-window of past days, finds the most similar days,
and reports how those days moved from this time until the close.
Optionally restricts matching to past expiry days (same weekday) —
useful on expiry day when behaviour is structurally different.
"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd


@dataclass
class AnalogMatch:
    date: str
    similarity: float          # 0-100
    move_so_far_pct: float     # that day's move up to "now" bar
    rest_of_day_pct: float     # what happened from "now" until close
    day_close_pct: float       # full-day move


def _daily_paths(df: pd.DataFrame) -> dict:
    """date -> Series of cumulative % returns per bar (from day open)."""
    if df.empty or not isinstance(df.index, pd.DatetimeIndex):
        return {}
    out = {}
    for day, grp in df.groupby(df.index.date):
        closes = grp["close"].to_numpy(dtype=float)
        if len(closes) < 10 or closes[0] == 0:
            continue
        out[day] = (closes / closes[0] - 1) * 100
    return out


def find_analog_days(df: pd.DataFrame, top_n: int = 3,
                     expiry_weekday: int | None = None) -> dict:
    """
    df: 5-min candles covering today + ~30-60 past days.
    expiry_weekday: if set, match only past days with this weekday
                    (e.g. past weekly expiries).
    """
    paths = _daily_paths(df)
    if not paths:
        return {"available": False, "reason": "Not enough intraday history"}

    today = max(paths.keys())
    today_path = paths.pop(today)
    k = len(today_path)
    if k < 6:
        return {"available": False,
                "reason": "Too early in the day — need ~30 min of candles"}

    matches: list[AnalogMatch] = []
    for day, path in paths.items():
        if expiry_weekday is not None and day.weekday() != expiry_weekday:
            continue
        if len(path) <= k:
            continue
        prefix = path[:k]
        # similarity = blend of shape correlation and level distance
        if np.std(prefix) < 1e-9 or np.std(today_path) < 1e-9:
            continue
        corr = float(np.corrcoef(today_path, prefix)[0, 1])
        rmse = float(np.sqrt(np.mean((today_path - prefix) ** 2)))
        similarity = max(0.0, corr) * 100 * float(np.exp(-rmse))
        matches.append(AnalogMatch(
            date=str(day),
            similarity=round(similarity, 1),
            move_so_far_pct=round(float(prefix[-1]), 2),
            rest_of_day_pct=round(float(path[-1] - path[k - 1]), 2),
            day_close_pct=round(float(path[-1]), 2),
        ))

    if not matches:
        return {"available": False, "reason": "No comparable past days found"}

    matches.sort(key=lambda m: m.similarity, reverse=True)
    top = matches[:top_n]
    avg_rest = float(np.mean([m.rest_of_day_pct for m in top]))
    bias = ("BULLISH rest-of-day" if avg_rest > 0.08
            else "BEARISH rest-of-day" if avg_rest < -0.08
            else "FLAT rest-of-day")
    return {
        "available": True,
        "today_move_pct": round(float(today_path[-1]), 2),
        "matches": [vars(m) for m in top],
        "avg_rest_of_day_pct": round(avg_rest, 2),
        "bias": bias,
    }
