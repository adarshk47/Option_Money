"""
Technical indicators in pure pandas/numpy — no TA-Lib binary required,
so the same code runs on Streamlit Cloud, Android backends and Windows.

`add_all_indicators(df)` appends every indicator column to an OHLCV frame.
"""
from __future__ import annotations

import numpy as np
import pandas as pd


def ema(series: pd.Series, period: int) -> pd.Series:
    return series.ewm(span=period, adjust=False).mean()


def vwap(df: pd.DataFrame) -> pd.Series:
    """Session VWAP — resets each trading day."""
    typical = (df["high"] + df["low"] + df["close"]) / 3
    day = df.index.date if isinstance(df.index, pd.DatetimeIndex) else 0
    pv = (typical * df["volume"]).groupby(day).cumsum()
    vol = df["volume"].groupby(day).cumsum().replace(0, np.nan)
    return pv / vol


def rsi(series: pd.Series, period: int = 14) -> pd.Series:
    delta = series.diff()
    gain = delta.clip(lower=0).ewm(alpha=1 / period, adjust=False).mean()
    loss = (-delta.clip(upper=0)).ewm(alpha=1 / period, adjust=False).mean()
    rs = gain / loss.replace(0, np.nan)
    return (100 - 100 / (1 + rs)).fillna(50)


def macd(series: pd.Series, fast: int = 12, slow: int = 26,
         signal: int = 9) -> tuple[pd.Series, pd.Series, pd.Series]:
    line = ema(series, fast) - ema(series, slow)
    sig = ema(line, signal)
    return line, sig, line - sig


def bollinger(series: pd.Series, period: int = 20,
              mult: float = 2.0) -> tuple[pd.Series, pd.Series, pd.Series]:
    mid = series.rolling(period).mean()
    std = series.rolling(period).std()
    return mid + mult * std, mid, mid - mult * std


def atr(df: pd.DataFrame, period: int = 14) -> pd.Series:
    hl = df["high"] - df["low"]
    hc = (df["high"] - df["close"].shift()).abs()
    lc = (df["low"] - df["close"].shift()).abs()
    tr = pd.concat([hl, hc, lc], axis=1).max(axis=1)
    return tr.ewm(alpha=1 / period, adjust=False).mean()


def supertrend(df: pd.DataFrame, period: int = 10,
               mult: float = 3.0) -> tuple[pd.Series, pd.Series]:
    """Returns (supertrend_line, direction) — direction +1 bullish, -1 bearish."""
    _atr = atr(df, period)
    hl2 = (df["high"] + df["low"]) / 2
    upper = hl2 + mult * _atr
    lower = hl2 - mult * _atr

    st = pd.Series(np.nan, index=df.index)
    direction = pd.Series(1, index=df.index)
    fu, fl = upper.copy(), lower.copy()

    for i in range(1, len(df)):
        fu.iloc[i] = (upper.iloc[i]
                      if upper.iloc[i] < fu.iloc[i - 1]
                      or df["close"].iloc[i - 1] > fu.iloc[i - 1]
                      else fu.iloc[i - 1])
        fl.iloc[i] = (lower.iloc[i]
                      if lower.iloc[i] > fl.iloc[i - 1]
                      or df["close"].iloc[i - 1] < fl.iloc[i - 1]
                      else fl.iloc[i - 1])
        if df["close"].iloc[i] > fu.iloc[i - 1]:
            direction.iloc[i] = 1
        elif df["close"].iloc[i] < fl.iloc[i - 1]:
            direction.iloc[i] = -1
        else:
            direction.iloc[i] = direction.iloc[i - 1]
        st.iloc[i] = fl.iloc[i] if direction.iloc[i] == 1 else fu.iloc[i]
    return st, direction


def adx(df: pd.DataFrame, period: int = 14) -> pd.Series:
    up = df["high"].diff()
    down = -df["low"].diff()
    plus_dm = np.where((up > down) & (up > 0), up, 0.0)
    minus_dm = np.where((down > up) & (down > 0), down, 0.0)
    _atr = atr(df, period).replace(0, np.nan)
    plus_di = 100 * pd.Series(plus_dm, index=df.index).ewm(
        alpha=1 / period, adjust=False).mean() / _atr
    minus_di = 100 * pd.Series(minus_dm, index=df.index).ewm(
        alpha=1 / period, adjust=False).mean() / _atr
    dx = 100 * (plus_di - minus_di).abs() / (plus_di + minus_di).replace(0, np.nan)
    return dx.ewm(alpha=1 / period, adjust=False).mean().fillna(0)


def add_all_indicators(df: pd.DataFrame) -> pd.DataFrame:
    """Append every indicator the recommendation engine consumes."""
    if df.empty or len(df) < 30:
        return df
    out = df.copy()
    out["ema9"] = ema(out["close"], 9)
    out["ema20"] = ema(out["close"], 20)
    out["ema50"] = ema(out["close"], 50)
    out["vwap"] = vwap(out)
    out["rsi"] = rsi(out["close"])
    out["macd"], out["macd_signal"], out["macd_hist"] = macd(out["close"])
    out["bb_upper"], out["bb_mid"], out["bb_lower"] = bollinger(out["close"])
    out["atr"] = atr(out)
    out["supertrend"], out["st_dir"] = supertrend(out)
    out["adx"] = adx(out)
    out["vol_sma20"] = out["volume"].rolling(20).mean()
    return out
