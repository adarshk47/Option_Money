"""
Dedicated Scalping Engine — 1m/5m candles, quick premium moves.

Designed for ₹5–₹20 option-premium targets with tight SLs, fast exits,
re-entry logic and expiry-day mode. Entry triggers:

  1. VWAP reclaim with volume       (momentum)
  2. EMA9/EMA20 cross + ADX rising  (trend ignition)
  3. Opening-range breakout         (first 15 minutes)
  4. Liquidity sweep reversal       (expiry-day favourite)
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, time as dtime
from typing import Optional

import pandas as pd

from ai_engine.indicators import add_all_indicators
from ai_engine.smart_money import detect_liquidity_sweep
from backend.logger import get_logger

log = get_logger(__name__)


@dataclass
class ScalpSignal:
    underlying: str
    direction: str          # CE / PE
    trigger: str
    spot: float
    confidence: float
    # premium-space plan (applied to the option price at entry)
    premium_sl_pct: float = 12.0      # tight SL
    premium_t1_rs: float = 8.0        # ₹ move target 1
    premium_t2_rs: float = 18.0       # ₹ move target 2
    trail_after_t1: bool = True
    max_hold_minutes: int = 12        # fast exit — time stop
    allow_reentry: bool = True
    note: str = ""


class ScalpingEngine:
    def __init__(self) -> None:
        self._last_exit: dict[str, datetime] = {}
        self._reentry_cooldown = 180  # seconds

    def is_expiry_day(self, underlying: str, expiry_str: str = "") -> bool:
        if not expiry_str:
            return False
        try:
            return (pd.to_datetime(expiry_str, format="%d%b%Y").date()
                    == datetime.now().date())
        except ValueError:
            return False

    def can_reenter(self, underlying: str) -> bool:
        last = self._last_exit.get(underlying)
        return (last is None
                or (datetime.now() - last).total_seconds() > self._reentry_cooldown)

    def record_exit(self, underlying: str) -> None:
        self._last_exit[underlying] = datetime.now()

    def scan(self, underlying: str, df5: pd.DataFrame,
             expiry_day: bool = False) -> Optional[ScalpSignal]:
        """Scan 5-minute candles for a scalp trigger."""
        if df5 is None or len(df5) < 40:
            return None
        if not self.can_reenter(underlying):
            return None
        df = add_all_indicators(df5)
        last, prev = df.iloc[-1], df.iloc[-2]
        spot = float(last["close"])
        vol_ok = last["vol_sma20"] > 0 and last["volume"] > 1.3 * last["vol_sma20"]

        # 1. VWAP reclaim with volume
        if prev["close"] < prev["vwap"] and last["close"] > last["vwap"] and vol_ok:
            return self._signal(underlying, "CE", "VWAP_RECLAIM", spot, 72,
                                expiry_day, "Reclaimed VWAP on volume")
        if prev["close"] > prev["vwap"] and last["close"] < last["vwap"] and vol_ok:
            return self._signal(underlying, "PE", "VWAP_REJECT", spot, 72,
                                expiry_day, "Lost VWAP on volume")

        # 2. EMA ignition
        if (prev["ema9"] <= prev["ema20"] and last["ema9"] > last["ema20"]
                and last["adx"] > df["adx"].iloc[-4] and last["rsi"] > 52):
            return self._signal(underlying, "CE", "EMA_CROSS_UP", spot, 68,
                                expiry_day, "EMA9 crossed above EMA20, ADX rising")
        if (prev["ema9"] >= prev["ema20"] and last["ema9"] < last["ema20"]
                and last["adx"] > df["adx"].iloc[-4] and last["rsi"] < 48):
            return self._signal(underlying, "PE", "EMA_CROSS_DOWN", spot, 68,
                                expiry_day, "EMA9 crossed below EMA20, ADX rising")

        # 3. Opening range breakout (09:30–10:30 window)
        now_t = datetime.now().time()
        if dtime(9, 30) <= now_t <= dtime(10, 30):
            today = df[df.index.date == datetime.now().date()] \
                if isinstance(df.index, pd.DatetimeIndex) else df.tail(15)
            orb = today.head(3)  # first 15 minutes on 5m candles
            if len(orb) >= 3:
                if spot > orb["high"].max() and vol_ok:
                    return self._signal(underlying, "CE", "ORB_BREAKOUT", spot, 75,
                                        expiry_day, "Opening range breakout")
                if spot < orb["low"].min() and vol_ok:
                    return self._signal(underlying, "PE", "ORB_BREAKDOWN", spot, 75,
                                        expiry_day, "Opening range breakdown")

        # 4. Liquidity sweep reversal (great on expiry day)
        sweep = detect_liquidity_sweep(df)
        if sweep and (expiry_day or vol_ok):
            direction = "CE" if sweep["bias"] == "bullish" else "PE"
            return self._signal(underlying, direction, "LIQUIDITY_SWEEP", spot,
                                70 + (8 if expiry_day else 0), expiry_day,
                                f"{sweep['type']} at {sweep['level']:.1f}")
        return None

    def _signal(self, underlying: str, direction: str, trigger: str,
                spot: float, confidence: float, expiry_day: bool,
                note: str) -> ScalpSignal:
        sig = ScalpSignal(
            underlying=underlying, direction=direction, trigger=trigger,
            spot=spot, confidence=confidence, note=note,
        )
        if expiry_day:
            # expiry day: cheaper premiums, faster gamma — tighter & quicker
            sig.premium_sl_pct = 18.0
            sig.premium_t1_rs = 5.0
            sig.premium_t2_rs = 12.0
            sig.max_hold_minutes = 8
            sig.note += " | EXPIRY DAY MODE"
        log.info("Scalp signal %s %s (%s) conf=%.0f%%",
                 underlying, direction, trigger, confidence)
        return sig

    @staticmethod
    def manage_exit(entry_premium: float, current_premium: float,
                    minutes_held: float, sig: ScalpSignal,
                    t1_done: bool) -> Optional[str]:
        """Fast scalp exit logic in premium space. Returns exit reason or None."""
        move = current_premium - entry_premium
        if current_premium <= entry_premium * (1 - sig.premium_sl_pct / 100):
            return "SCALP_SL"
        if not t1_done and move >= sig.premium_t1_rs:
            return "SCALP_T1"
        if t1_done and move >= sig.premium_t2_rs:
            return "SCALP_T2"
        if t1_done and move <= sig.premium_t1_rs * 0.4:
            return "TRAIL_EXIT"          # give back protection
        if minutes_held >= sig.max_hold_minutes and move < sig.premium_t1_rs * 0.5:
            return "TIME_STOP"           # momentum failed to follow through
        return None


scalping_engine = ScalpingEngine()
