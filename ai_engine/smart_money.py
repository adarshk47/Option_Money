"""
Smart Money Concepts (SMC):
  - Liquidity sweeps (stop hunts beyond prior swing extremes)
  - Order blocks (last opposing candle before an impulsive move)
  - Fair value gaps (3-candle imbalances)
  - BOS  (break of structure — trend continuation)
  - CHoCH (change of character — early trend reversal)
"""
from __future__ import annotations

import pandas as pd

from ai_engine.market_structure import swing_points


def detect_liquidity_sweep(df: pd.DataFrame) -> dict | None:
    """Wick beyond prior swing high/low followed by a close back inside."""
    if len(df) < 20:
        return None
    c = df.iloc[-1]
    prior_high = df["high"].iloc[-15:-1].max()
    prior_low = df["low"].iloc[-15:-1].min()
    if c["high"] > prior_high and c["close"] < prior_high:
        return {"type": "SELL_SIDE_SWEEP", "bias": "bearish",
                "level": float(prior_high)}
    if c["low"] < prior_low and c["close"] > prior_low:
        return {"type": "BUY_SIDE_SWEEP", "bias": "bullish",
                "level": float(prior_low)}
    return None


def detect_order_blocks(df: pd.DataFrame, impulse_mult: float = 1.8) -> list[dict]:
    """Last bearish candle before a strong rally (bullish OB) and inverse."""
    blocks: list[dict] = []
    if len(df) < 25:
        return blocks
    body = (df["close"] - df["open"]).abs()
    avg_body = body.tail(20).mean() or 1e-9
    for i in range(len(df) - 10, len(df) - 1):
        nxt = df.iloc[i + 1]
        cur = df.iloc[i]
        impulse_up = (nxt["close"] - nxt["open"]) > impulse_mult * avg_body
        impulse_dn = (nxt["open"] - nxt["close"]) > impulse_mult * avg_body
        if cur["close"] < cur["open"] and impulse_up:
            blocks.append({"type": "BULLISH_OB", "bias": "bullish",
                           "low": float(cur["low"]), "high": float(cur["high"])})
        if cur["close"] > cur["open"] and impulse_dn:
            blocks.append({"type": "BEARISH_OB", "bias": "bearish",
                           "low": float(cur["low"]), "high": float(cur["high"])})
    return blocks[-3:]


def detect_fair_value_gaps(df: pd.DataFrame) -> list[dict]:
    """3-candle imbalance: candle1.high < candle3.low (bullish FVG) etc."""
    gaps: list[dict] = []
    if len(df) < 10:
        return gaps
    for i in range(len(df) - 8, len(df) - 1):
        a, c = df.iloc[i - 1], df.iloc[i + 1]
        if a["high"] < c["low"]:
            gaps.append({"type": "BULLISH_FVG", "bias": "bullish",
                         "low": float(a["high"]), "high": float(c["low"])})
        elif a["low"] > c["high"]:
            gaps.append({"type": "BEARISH_FVG", "bias": "bearish",
                         "low": float(c["high"]), "high": float(a["low"])})
    return gaps[-3:]


def detect_bos_choch(df: pd.DataFrame) -> dict | None:
    """Break of Structure vs Change of Character off the latest swings."""
    highs, lows = swing_points(df)
    if len(highs) < 2 or len(lows) < 2:
        return None
    close = float(df["close"].iloc[-1])
    last_high, prev_high = highs[-1][1], highs[-2][1]
    last_low, prev_low = lows[-1][1], lows[-2][1]
    uptrend = last_high > prev_high and last_low > prev_low
    downtrend = last_high < prev_high and last_low < prev_low

    if uptrend and close > last_high:
        return {"type": "BOS", "bias": "bullish", "level": last_high}
    if downtrend and close < last_low:
        return {"type": "BOS", "bias": "bearish", "level": last_low}
    if downtrend and close > last_high:
        return {"type": "CHOCH", "bias": "bullish", "level": last_high}
    if uptrend and close < last_low:
        return {"type": "CHOCH", "bias": "bearish", "level": last_low}
    return None


def analyse_smart_money(df: pd.DataFrame) -> dict:
    """Aggregate SMC view with a net bias score in [-1, 1]."""
    sweep = detect_liquidity_sweep(df)
    obs = detect_order_blocks(df)
    fvgs = detect_fair_value_gaps(df)
    bos = detect_bos_choch(df)

    score = 0.0
    for item in ([sweep] if sweep else []) + obs + fvgs + ([bos] if bos else []):
        weight = {"BOS": 0.4, "CHOCH": 0.5}.get(item.get("type"), 0.2)
        score += weight if item["bias"] == "bullish" else -weight
    return {
        "liquidity_sweep": sweep,
        "order_blocks": obs,
        "fair_value_gaps": fvgs,
        "bos_choch": bos,
        "smc_score": max(-1.0, min(1.0, score)),
    }
