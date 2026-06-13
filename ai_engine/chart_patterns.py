"""
Classic chart-pattern recognition from swing pivots.

detect_chart_pattern(df) -> {
    "pattern": str,        # e.g. "Double Bottom (W)"
    "bias": str,           # bullish / bearish / neutral
    "confidence": float,   # 0-100
    "status": str,         # forming / confirmed / ongoing
    "description": str,    # human-readable explanation
}

Detects: Double Bottom (W), Double Top (M), Head & Shoulders,
Inverse Head & Shoulders, Ascending/Descending/Symmetrical Triangle,
Rising/Falling Channel, and Range/Sideways as the fallback.
"""
from __future__ import annotations

import numpy as np
import pandas as pd


def _pivots(values: np.ndarray, left: int = 2, right: int = 2,
            kind: str = "high") -> list[tuple[int, float]]:
    out: list[tuple[int, float]] = []
    n = len(values)
    for i in range(left, n - right):
        seg = values[i - left:i + right + 1]
        if kind == "high" and values[i] == seg.max():
            out.append((i, float(values[i])))
        elif kind == "low" and values[i] == seg.min():
            out.append((i, float(values[i])))
    return out


def detect_chart_pattern(df: pd.DataFrame, lookback: int = 70) -> dict:
    if df is None or len(df) < 25:
        return {"pattern": "—", "bias": "neutral", "confidence": 0.0,
                "status": "", "description": "Not enough candles yet."}

    w = df.tail(lookback).reset_index(drop=True)
    highs = w["high"].to_numpy(float)
    lows = w["low"].to_numpy(float)
    close = float(w["close"].iloc[-1])
    ph = _pivots(highs, kind="high")
    pl = _pivots(lows, kind="low")

    lo, hi = float(lows.min()), float(highs.max())
    band = (hi - lo) or 1.0
    # zones: a bottom reversal must sit in the lower third, a top in the upper
    low_zone = lo + 0.40 * band
    high_zone = hi - 0.40 * band

    out: list[tuple[str, str, float, str, str]] = []

    # ── Double Bottom (W) — only near the lows ─────────────────────
    if len(pl) >= 2:
        (i1, l1), (i2, l2) = pl[-2], pl[-1]
        if i2 - i1 >= 3 and max(l1, l2) <= low_zone:
            avg = (l1 + l2) / 2
            if avg and abs(l1 - l2) / avg < 0.012:
                neck = float(highs[i1:i2 + 1].max())
                if (neck - avg) / avg > 0.004:
                    if close > neck:
                        out.append(("Double Bottom (W)", "bullish", 84,
                                    "confirmed",
                                    f"Two equal lows ~{avg:,.0f} formed a W; price "
                                    f"broke the neckline {neck:,.0f} → bullish breakout."))
                    elif close > avg:
                        out.append(("Double Bottom (W)", "bullish", 62, "forming",
                                    f"W building on twin lows ~{avg:,.0f}; a close "
                                    f"above neckline {neck:,.0f} confirms upside."))

    # ── Double Top (M) — only near the highs ───────────────────────
    if len(ph) >= 2:
        (i1, h1), (i2, h2) = ph[-2], ph[-1]
        if i2 - i1 >= 3 and min(h1, h2) >= high_zone:
            avg = (h1 + h2) / 2
            if avg and abs(h1 - h2) / avg < 0.012:
                neck = float(lows[i1:i2 + 1].min())
                if (avg - neck) / avg > 0.004:
                    if close < neck:
                        out.append(("Double Top (M)", "bearish", 84, "confirmed",
                                    f"Twin tops ~{avg:,.0f}; price broke neckline "
                                    f"{neck:,.0f} → bearish breakdown."))
                    elif close < avg:
                        out.append(("Double Top (M)", "bearish", 62, "forming",
                                    f"M building on twin tops ~{avg:,.0f}; a close "
                                    f"below neckline {neck:,.0f} confirms downside."))

    # ── Head & Shoulders (bearish) ─────────────────────────────────
    if len(ph) >= 3:
        (ia, ha), (ib, hb), (ic, hc) = ph[-3], ph[-2], ph[-1]
        if hb > ha and hb > hc and abs(ha - hc) / ((ha + hc) / 2) < 0.02:
            neck = float(lows[ia:ic + 1].min())
            status = "confirmed" if close < neck else "forming"
            out.append(("Head & Shoulders", "bearish",
                        78 if status == "confirmed" else 58, status,
                        f"Three peaks with a higher head {hb:,.0f}; "
                        f"neckline {neck:,.0f}."))

    # ── Inverse Head & Shoulders (bullish) ─────────────────────────
    if len(pl) >= 3:
        (ia, la), (ib, lb), (ic, lc) = pl[-3], pl[-2], pl[-1]
        if lb < la and lb < lc and abs(la - lc) / ((la + lc) / 2) < 0.02:
            neck = float(highs[ia:ic + 1].max())
            status = "confirmed" if close > neck else "forming"
            out.append(("Inverse Head & Shoulders", "bullish",
                        78 if status == "confirmed" else 58, status,
                        f"Three troughs with a lower head {lb:,.0f}; "
                        f"neckline {neck:,.0f}."))

    # ── Triangles / channels from pivot slopes ─────────────────────
    if len(ph) >= 2 and len(pl) >= 2:
        hx = np.array([p[0] for p in ph[-3:]], float)
        hy = np.array([p[1] for p in ph[-3:]], float)
        lx = np.array([p[0] for p in pl[-3:]], float)
        ly = np.array([p[1] for p in pl[-3:]], float)
        rng = (highs.max() - lows.min()) or 1.0
        hs = (np.polyfit(hx, hy, 1)[0] if len(hx) >= 2 else 0.0) / rng
        ls = (np.polyfit(lx, ly, 1)[0] if len(lx) >= 2 else 0.0) / rng
        if hs < -0.002 and abs(ls) < 0.0012:
            out.append(("Descending Triangle", "bearish", 56, "forming",
                        "Lower highs pressing flat support — breakdown risk."))
        elif ls > 0.002 and abs(hs) < 0.0012:
            out.append(("Ascending Triangle", "bullish", 56, "forming",
                        "Higher lows pressing flat resistance — breakout risk."))
        elif hs < -0.0012 and ls > 0.0012:
            out.append(("Symmetrical Triangle", "neutral", 46, "forming",
                        "Converging highs and lows — breakout pending."))
        elif hs > 0.0012 and ls > 0.0012:
            out.append(("Rising Channel / Uptrend", "bullish", 52, "ongoing",
                        "Higher highs and higher lows — trend up."))
        elif hs < -0.0012 and ls < -0.0012:
            out.append(("Falling Channel / Downtrend", "bearish", 52, "ongoing",
                        "Lower highs and lower lows — trend down."))

    # ── Trend fallback via close regression (R²-weighted) ──────────
    closes = w["close"].to_numpy(float)
    x = np.arange(len(closes), dtype=float)
    slope, intercept = np.polyfit(x, closes, 1)
    fitted = slope * x + intercept
    ss_res = float(((closes - fitted) ** 2).sum())
    ss_tot = float(((closes - closes.mean()) ** 2).sum()) or 1.0
    r2 = max(0.0, 1 - ss_res / ss_tot)
    drift_pct = slope * len(closes) / close * 100 if close else 0
    if abs(drift_pct) > 0.25 and r2 > 0.55:
        conf = 50 + r2 * 25
        if drift_pct > 0:
            out.append(("Uptrend", "bullish", conf, "ongoing",
                        f"Steady higher prices (+{drift_pct:.1f}% over window, "
                        f"R²={r2:.2f}) — trend up."))
        else:
            out.append(("Downtrend", "bearish", conf, "ongoing",
                        f"Steady lower prices ({drift_pct:.1f}% over window, "
                        f"R²={r2:.2f}) — trend down."))

    if not out:
        span = (highs.max() - lows.min()) / close * 100 if close else 0
        return {"pattern": "Range / Sideways", "bias": "neutral",
                "confidence": 40.0, "status": "ongoing",
                "description": f"No clean pattern — price ranging "
                               f"(~{span:.1f}% band). Wait for a breakout."}

    best = max(out, key=lambda t: t[2])
    return {"pattern": best[0], "bias": best[1], "confidence": float(best[2]),
            "status": best[3], "description": best[4]}
