"""
Option chain analytics: PCR, max pain, OI buildup classification,
CE/PE dominance, and OI-based support/resistance.
"""
from __future__ import annotations

import numpy as np
import pandas as pd


def pcr(chain: pd.DataFrame) -> float:
    ce_oi = chain["ce_oi"].sum()
    return round(float(chain["pe_oi"].sum() / ce_oi), 2) if ce_oi else 0.0


def max_pain(chain: pd.DataFrame) -> float:
    """Strike where total option-writer payout is minimised."""
    strikes = chain["strike"].values
    pain = []
    for s in strikes:
        ce_loss = np.maximum(s - strikes, 0) @ chain["ce_oi"].values
        pe_loss = np.maximum(strikes - s, 0) @ chain["pe_oi"].values
        pain.append(ce_loss + pe_loss)
    return float(strikes[int(np.argmin(pain))])


def oi_buildup(chain: pd.DataFrame, spot: float, price_up: bool) -> dict:
    """
    Classify the dominant OI behaviour near ATM:
      long buildup, short buildup, short covering, long unwinding.
    Heuristic: combine change-in-OI direction with price direction.
    """
    near = chain[(chain["strike"] - spot).abs() <= spot * 0.02]
    if near.empty:
        near = chain
    ce_chg = float(near["ce_chg_oi"].sum())
    pe_chg = float(near["pe_chg_oi"].sum())

    if price_up:
        ce_view = "SHORT_COVERING" if ce_chg < 0 else "CALL_WRITING_ABSORBED"
        pe_view = "PUT_WRITING (bullish)" if pe_chg > 0 else "PUT_UNWINDING"
        bias = "bullish" if pe_chg > 0 or ce_chg < 0 else "neutral"
    else:
        ce_view = "CALL_WRITING (bearish)" if ce_chg > 0 else "CALL_UNWINDING"
        pe_view = "SHORT_COVERING" if pe_chg < 0 else "PUT_WRITING_ABSORBED"
        bias = "bearish" if ce_chg > 0 or pe_chg < 0 else "neutral"
    return {"ce_chg_oi": ce_chg, "pe_chg_oi": pe_chg,
            "ce_view": ce_view, "pe_view": pe_view, "bias": bias}


def oi_support_resistance(chain: pd.DataFrame) -> dict:
    """Highest PE OI strike = support; highest CE OI strike = resistance."""
    if chain.empty:
        return {"oi_support": None, "oi_resistance": None}
    return {
        "oi_support": float(chain.loc[chain["pe_oi"].idxmax(), "strike"]),
        "oi_resistance": float(chain.loc[chain["ce_oi"].idxmax(), "strike"]),
    }


def analyse_option_chain(chain: pd.DataFrame, spot: float,
                         price_up: bool = True) -> dict:
    """Full chain analysis with a sentiment score in [-1, 1]."""
    if chain.empty or spot <= 0:
        return {"available": False, "sentiment_score": 0.0}

    _pcr = pcr(chain)
    buildup = oi_buildup(chain, spot, price_up)
    sr = oi_support_resistance(chain)
    mp = max_pain(chain)

    score = 0.0
    if _pcr > 1.2:
        score += 0.3          # heavy put writing — bullish
    elif _pcr < 0.7:
        score -= 0.3          # heavy call writing — bearish
    score += {"bullish": 0.3, "bearish": -0.3}.get(buildup["bias"], 0.0)
    if mp > spot * 1.002:
        score += 0.15         # price tends to gravitate toward max pain
    elif mp < spot * 0.998:
        score -= 0.15

    ce_vol = chain["ce_volume"].sum()
    pe_vol = chain["pe_volume"].sum()
    dominance = "CE" if ce_vol > pe_vol * 1.2 else "PE" if pe_vol > ce_vol * 1.2 else "BALANCED"

    return {
        "available": True,
        "pcr": _pcr,
        "max_pain": mp,
        "oi_buildup": buildup,
        "dominance": dominance,
        **sr,
        "sentiment_score": max(-1.0, min(1.0, score)),
    }
