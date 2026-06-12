"""
AI Recommendation Engine — the brain of the system.

Fuses, per instrument and per timeframe:
  indicators • candlestick patterns • market structure • smart money
  concepts • option chain sentiment • volume/volatility/momentum • ML

into a single weighted probability score, then emits one of:
  STRONG BUY CE | BUY CE | STRONG BUY PE | BUY PE |
  SCALPING OPPORTUNITY | SIDEWAYS - AVOID | AVOID TRADE

Every recommendation carries entry/SL/T1/T2, confidence %, risk level,
timeframe and human-readable reasoning. Options BUYING only — a bearish
view buys PE, never sells anything.
"""
from __future__ import annotations

from dataclasses import dataclass, field, asdict
from datetime import datetime
from typing import Optional

import pandas as pd

from ai_engine.candlestick_patterns import detect_patterns
from ai_engine.indicators import add_all_indicators
from ai_engine.market_structure import analyse_structure
from ai_engine.option_chain_analysis import analyse_option_chain
from ai_engine.smart_money import analyse_smart_money
from backend.config import settings
from backend.logger import get_logger

log = get_logger(__name__)

# Weights of each analysis family in the final score (sum = 1.0)
WEIGHTS = {
    "trend": 0.20,
    "indicators": 0.20,
    "patterns": 0.12,
    "structure": 0.13,
    "smart_money": 0.13,
    "option_chain": 0.12,
    "volume": 0.05,
    "ml": 0.05,
}


@dataclass
class Recommendation:
    underlying: str
    timeframe: str
    action: str                       # e.g. "STRONG BUY CE"
    option_type: Optional[str]        # CE / PE / None
    confidence: float                 # 0-100
    entry_price: float                # underlying spot reference
    stop_loss: float
    target1: float
    target2: float
    risk_level: str                   # LOW / MEDIUM / HIGH
    risk_reward: float
    reasoning: list[str] = field(default_factory=list)
    score: float = 0.0                # raw -1..1
    spot: float = 0.0
    generated_at: str = field(default_factory=lambda: datetime.now().isoformat())

    def to_dict(self) -> dict:
        return asdict(self)


class RecommendationEngine:
    def analyse(self, underlying: str, df: pd.DataFrame, timeframe: str,
                option_chain: Optional[pd.DataFrame] = None,
                chain_spot: float = 0.0,
                ml_score: float = 0.0) -> Recommendation:
        """df = raw OHLCV; everything else optional."""
        reasons: list[str] = []
        if df is None or len(df) < 50:
            return self._avoid(underlying, timeframe, "Insufficient data")

        df = add_all_indicators(df)
        last = df.iloc[-1]
        spot = float(last["close"])
        score = 0.0

        # ── 1. Trend (EMA stack + supertrend + VWAP) ───────────────
        t = 0.0
        if last["ema9"] > last["ema20"] > last["ema50"]:
            t += 0.5; reasons.append("EMA 9>20>50 bullish stack")
        elif last["ema9"] < last["ema20"] < last["ema50"]:
            t -= 0.5; reasons.append("EMA 9<20<50 bearish stack")
        if last["st_dir"] == 1:
            t += 0.3; reasons.append("Supertrend bullish")
        else:
            t -= 0.3; reasons.append("Supertrend bearish")
        if spot > last["vwap"]:
            t += 0.2; reasons.append("Price above VWAP")
        else:
            t -= 0.2; reasons.append("Price below VWAP")
        score += WEIGHTS["trend"] * max(-1, min(1, t))

        # ── 2. Momentum indicators (RSI/MACD/ADX/BB) ───────────────
        m = 0.0
        if 55 < last["rsi"] < 75:
            m += 0.4; reasons.append(f"RSI {last['rsi']:.0f} — bullish momentum")
        elif 25 < last["rsi"] < 45:
            m -= 0.4; reasons.append(f"RSI {last['rsi']:.0f} — bearish momentum")
        elif last["rsi"] >= 75:
            m -= 0.15; reasons.append(f"RSI {last['rsi']:.0f} overbought — caution")
        elif last["rsi"] <= 25:
            m += 0.15; reasons.append(f"RSI {last['rsi']:.0f} oversold — bounce risk")
        if last["macd_hist"] > 0 and last["macd"] > last["macd_signal"]:
            m += 0.3; reasons.append("MACD bullish crossover")
        elif last["macd_hist"] < 0:
            m -= 0.3; reasons.append("MACD bearish")
        adx_strong = last["adx"] > 22
        if adx_strong:
            reasons.append(f"ADX {last['adx']:.0f} — trending market")
        else:
            reasons.append(f"ADX {last['adx']:.0f} — weak trend")
        score += WEIGHTS["indicators"] * max(-1, min(1, m)) * (1.0 if adx_strong else 0.5)

        # ── 3. Candlestick patterns ────────────────────────────────
        pats = detect_patterns(df)
        p = sum(x["strength"] if x["bias"] == "bullish"
                else -x["strength"] if x["bias"] == "bearish" else 0
                for x in pats)
        for x in pats:
            reasons.append(f"Pattern: {x['pattern']} ({x['bias']})")
        score += WEIGHTS["patterns"] * max(-1, min(1, p))

        # ── 4. Market structure ────────────────────────────────────
        struct = analyse_structure(df)
        s = 0.0
        if struct["trend"] == "UPTREND":
            s += 0.5; reasons.append("Structure: higher highs & higher lows")
        elif struct["trend"] == "DOWNTREND":
            s -= 0.5; reasons.append("Structure: lower highs & lower lows")
        if struct["breakout"]:
            s += 0.5; reasons.append("Range BREAKOUT confirmed")
        if struct["breakdown"]:
            s -= 0.5; reasons.append("Range BREAKDOWN confirmed")
        if struct["reversal"] == "BULLISH_REVERSAL_FORMING":
            s += 0.3; reasons.append("Bullish reversal forming")
        elif struct["reversal"] == "BEARISH_REVERSAL_FORMING":
            s -= 0.3; reasons.append("Bearish reversal forming")
        score += WEIGHTS["structure"] * max(-1, min(1, s))

        # ── 5. Smart money concepts ────────────────────────────────
        smc = analyse_smart_money(df)
        score += WEIGHTS["smart_money"] * smc["smc_score"]
        if smc["bos_choch"]:
            reasons.append(f"SMC: {smc['bos_choch']['type']} {smc['bos_choch']['bias']}")
        if smc["liquidity_sweep"]:
            reasons.append(f"SMC: {smc['liquidity_sweep']['type']}")

        # ── 6. Option chain sentiment ──────────────────────────────
        price_up = spot >= float(df["close"].iloc[-5])
        if option_chain is not None and not option_chain.empty:
            oc = analyse_option_chain(option_chain, chain_spot or spot, price_up)
            score += WEIGHTS["option_chain"] * oc["sentiment_score"]
            if oc["available"]:
                reasons.append(f"PCR {oc['pcr']} | Max pain {oc['max_pain']:.0f} "
                               f"| {oc['dominance']} dominant")
                reasons.append(f"OI: {oc['oi_buildup']['pe_view']}")

        # ── 7. Volume confirmation ─────────────────────────────────
        vol_surge = (last["volume"] > 1.5 * last["vol_sma20"]
                     if last["vol_sma20"] > 0 else False)
        if vol_surge:
            direction = 1 if last["close"] > last["open"] else -1
            score += WEIGHTS["volume"] * direction
            reasons.append("Volume surge confirms the move")

        # ── 8. ML overlay ──────────────────────────────────────────
        if ml_score:
            score += WEIGHTS["ml"] * max(-1, min(1, ml_score))
            reasons.append(f"ML model score {ml_score:+.2f}")

        # ── Volatility gate: dead markets get filtered ─────────────
        atr_pct = float(last["atr"]) / spot * 100
        if atr_pct < 0.03 and not vol_surge:
            return self._avoid(underlying, timeframe,
                               "Volatility too low — sideways market",
                               spot=spot)

        return self._build(underlying, timeframe, score, spot, last, struct, reasons)

    # ── Recommendation construction ────────────────────────────────

    def _build(self, underlying: str, timeframe: str, score: float,
               spot: float, last: pd.Series, struct: dict,
               reasons: list[str]) -> Recommendation:
        confidence = round(min(abs(score) * 100 / 0.55, 99), 1)
        atr_val = float(last["atr"])

        if score >= 0.35:
            action, opt = "STRONG BUY CE", "CE"
        elif score >= 0.18:
            action, opt = "BUY CE", "CE"
        elif score <= -0.35:
            action, opt = "STRONG BUY PE", "PE"
        elif score <= -0.18:
            action, opt = "BUY PE", "PE"
        elif abs(score) >= 0.10 and last["adx"] < 20:
            action, opt = "SCALPING OPPORTUNITY", "CE" if score > 0 else "PE"
        elif abs(score) < 0.08:
            return self._avoid(underlying, timeframe,
                               "No edge — sideways market", spot=spot,
                               reasons=reasons, score=score)
        else:
            return self._avoid(underlying, timeframe,
                               "Mixed signals — avoid trade", spot=spot,
                               reasons=reasons, score=score)

        # Levels on the UNDERLYING (option SL/targets derived in trade mgr)
        if opt == "CE":
            stop = spot - 1.2 * atr_val
            t1, t2 = spot + 1.5 * atr_val, spot + 3.0 * atr_val
            stop = max(stop, struct["support"] or stop)
        else:
            stop = spot + 1.2 * atr_val
            t1, t2 = spot - 1.5 * atr_val, spot - 3.0 * atr_val
            stop = min(stop, struct["resistance"] or stop)

        rr = round(abs(t1 - spot) / max(abs(spot - stop), 1e-9), 2)
        risk = ("LOW" if confidence > 75 and rr >= 1.2
                else "MEDIUM" if confidence > 55 else "HIGH")

        return Recommendation(
            underlying=underlying, timeframe=timeframe, action=action,
            option_type=opt, confidence=confidence, entry_price=round(spot, 2),
            stop_loss=round(stop, 2), target1=round(t1, 2), target2=round(t2, 2),
            risk_level=risk, risk_reward=rr, reasoning=reasons,
            score=round(score, 3), spot=round(spot, 2),
        )

    def _avoid(self, underlying: str, timeframe: str, why: str,
               spot: float = 0.0, reasons: Optional[list] = None,
               score: float = 0.0) -> Recommendation:
        return Recommendation(
            underlying=underlying, timeframe=timeframe,
            action="SIDEWAYS - AVOID" if "sideways" in why.lower() else "AVOID TRADE",
            option_type=None, confidence=0.0, entry_price=spot, stop_loss=0.0,
            target1=0.0, target2=0.0, risk_level="N/A", risk_reward=0.0,
            reasoning=[why] + (reasons or []), score=round(score, 3), spot=spot,
        )


recommendation_engine = RecommendationEngine()
