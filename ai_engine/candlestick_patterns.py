"""
Candlestick pattern recognition (pure pandas, last-bar focused).

`detect_patterns(df)` returns a list of
    {"pattern": str, "bias": "bullish"|"bearish"|"neutral", "strength": 0-1}
found on the most recent completed candles.
"""
from __future__ import annotations

import pandas as pd


def _body(c) -> float:
    return abs(c["close"] - c["open"])


def _range(c) -> float:
    return max(c["high"] - c["low"], 1e-9)


def _upper_wick(c) -> float:
    return c["high"] - max(c["close"], c["open"])


def _lower_wick(c) -> float:
    return min(c["close"], c["open"]) - c["low"]


def _is_bull(c) -> bool:
    return c["close"] > c["open"]


def detect_patterns(df: pd.DataFrame) -> list[dict]:
    if len(df) < 5:
        return []
    patterns: list[dict] = []
    c0 = df.iloc[-1]   # latest candle
    c1 = df.iloc[-2]
    c2 = df.iloc[-3]
    avg_body = df["close"].sub(df["open"]).abs().tail(20).mean() or 1e-9

    # Doji — indecision
    if _body(c0) <= 0.1 * _range(c0):
        patterns.append({"pattern": "Doji", "bias": "neutral", "strength": 0.4})

    # Hammer — bullish reversal after a dip
    if (_lower_wick(c0) >= 2 * _body(c0)
            and _upper_wick(c0) <= 0.3 * _body(c0) + 1e-9
            and c0["low"] < df["low"].tail(5).iloc[:-1].min()):
        patterns.append({"pattern": "Hammer", "bias": "bullish", "strength": 0.7})

    # Shooting star — bearish reversal after a pop
    if (_upper_wick(c0) >= 2 * _body(c0)
            and _lower_wick(c0) <= 0.3 * _body(c0) + 1e-9
            and c0["high"] > df["high"].tail(5).iloc[:-1].max()):
        patterns.append({"pattern": "Shooting Star", "bias": "bearish", "strength": 0.7})

    # Engulfing
    if (_is_bull(c0) and not _is_bull(c1)
            and c0["close"] > c1["open"] and c0["open"] < c1["close"]
            and _body(c0) > avg_body):
        patterns.append({"pattern": "Bullish Engulfing", "bias": "bullish", "strength": 0.8})
    if (not _is_bull(c0) and _is_bull(c1)
            and c0["close"] < c1["open"] and c0["open"] > c1["close"]
            and _body(c0) > avg_body):
        patterns.append({"pattern": "Bearish Engulfing", "bias": "bearish", "strength": 0.8})

    # Morning star — three-bar bullish reversal
    if (not _is_bull(c2) and _body(c2) > avg_body
            and _body(c1) < 0.5 * avg_body
            and _is_bull(c0)
            and c0["close"] > (c2["open"] + c2["close"]) / 2):
        patterns.append({"pattern": "Morning Star", "bias": "bullish", "strength": 0.85})

    # Evening star — three-bar bearish reversal
    if (_is_bull(c2) and _body(c2) > avg_body
            and _body(c1) < 0.5 * avg_body
            and not _is_bull(c0)
            and c0["close"] < (c2["open"] + c2["close"]) / 2):
        patterns.append({"pattern": "Evening Star", "bias": "bearish", "strength": 0.85})

    # Marubozu — strong momentum candle
    if _body(c0) >= 0.9 * _range(c0) and _body(c0) > 1.5 * avg_body:
        bias = "bullish" if _is_bull(c0) else "bearish"
        patterns.append({"pattern": f"{bias.title()} Marubozu", "bias": bias, "strength": 0.75})

    return patterns
