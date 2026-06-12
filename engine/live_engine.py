"""
Live analysis engine — the always-on orchestrator.

Every cycle it:
  1. refreshes candles for NIFTY / BANKNIFTY / SENSEX / SBIN
  2. runs the AI recommendation engine on 1m, 5m, 10m, 15m and 20m
  3. runs the scalping engine on 5m
  4. requires multi-timeframe agreement before auto-trading
  5. manages open positions (SL / targets / trailing / time stops)
  6. auto squares-off at 15:15 IST

Run directly:  python main.py engine
"""
from __future__ import annotations

import threading
import time
from datetime import datetime, time as dtime

import pytz

from ai_engine.ml_model import ml_model
from ai_engine.recommendation_engine import recommendation_engine
from ai_engine.scalping_engine import scalping_engine
from alerts.alert_manager import alert_manager
from backend.broker.angel_one import broker
from backend.config import settings
from backend.data.option_chain import option_chain_fetcher
from backend.logger import get_logger
from database.db import db
from engine.trade_manager import trade_manager

log = get_logger(__name__)

IST = pytz.timezone("Asia/Kolkata")
MARKET_OPEN = dtime(9, 15)
MARKET_CLOSE = dtime(15, 30)
SQUAREOFF_AT = dtime(15, 15)

# How often each timeframe re-runs analysis (seconds)
SCAN_INTERVALS = {"1min": 60, "5min": 120, "10min": 300,
                  "15min": 450, "20min": 600}


class LiveEngine:
    def __init__(self, auto_trade: bool = True, min_confidence: float = 72.0):
        self.auto_trade = auto_trade
        self.min_confidence = min_confidence
        self.latest: dict[str, dict[str, dict]] = {}   # {name: {tf: rec_dict}}
        self.running = False
        self._last_scan: dict[tuple[str, str], float] = {}
        self._squared_off = False

    # ── Market hours ───────────────────────────────────────────────

    @staticmethod
    def market_is_open() -> bool:
        now = datetime.now(IST)
        return (now.weekday() < 5
                and MARKET_OPEN <= now.time() <= MARKET_CLOSE)

    # ── Main loop ──────────────────────────────────────────────────

    def start(self) -> None:
        if not broker.login():
            log.error("Engine not started: broker login failed")
            return
        self.running = True
        threading.Thread(target=self._loop, daemon=True).start()
        log.info("Live engine started (auto_trade=%s, mode=%s)",
                 self.auto_trade, settings.trading_mode)

    def stop(self) -> None:
        self.running = False

    def _loop(self) -> None:
        while self.running:
            try:
                if not self.market_is_open():
                    time.sleep(30)
                    continue
                now_ist = datetime.now(IST).time()
                if now_ist >= SQUAREOFF_AT and not self._squared_off:
                    trade_manager.square_off_all()
                    self._squared_off = True
                elif now_ist < SQUAREOFF_AT:
                    self._squared_off = False

                self._scan_cycle()
                trade_manager.manage_positions()
            except Exception:
                log.exception("Engine cycle failed — continuing")
            time.sleep(settings.refresh_seconds)

    def _scan_cycle(self) -> None:
        for name in settings.watchlist:
            # option chain once per instrument per cycle (cached 60s inside)
            chain, chain_spot = option_chain_fetcher.fetch(name)

            for tf in settings.timeframes:
                key = (name, tf)
                if time.time() - self._last_scan.get(key, 0) < SCAN_INTERVALS[tf]:
                    continue
                self._last_scan[key] = time.time()
                df = broker.get_candles(name, tf, days=5 if tf != "1min" else 2)
                if df.empty:
                    continue

                rec = recommendation_engine.analyse(
                    name, df, tf, option_chain=chain, chain_spot=chain_spot,
                    ml_score=ml_model.predict_score(df) if tf == "5min" else 0.0,
                )
                self.latest.setdefault(name, {})[tf] = rec.to_dict()

                if rec.option_type and rec.confidence >= 60:
                    db.insert_signal(
                        underlying=name, timeframe=tf, action=rec.action,
                        confidence=rec.confidence, entry_price=rec.entry_price,
                        stop_loss=rec.stop_loss, target1=rec.target1,
                        target2=rec.target2, risk_level=rec.risk_level,
                        reasoning="; ".join(rec.reasoning[:5]),
                    )
                    alert_manager.signal_alert(rec)

                # auto-trade only on 5m signals confirmed by 15m direction
                if (self.auto_trade and tf == "5min"
                        and rec.confidence >= self.min_confidence
                        and self._mtf_agrees(name, rec.option_type)):
                    trade_manager.execute_recommendation(rec, self.min_confidence)

                # scalping engine rides on fresh 5m data
                if tf == "5min":
                    scalp = scalping_engine.scan(name, df)
                    if scalp and scalp.confidence >= 70:
                        alert_manager.send(
                            f"⚡ SCALP {scalp.direction} — {name}",
                            f"{scalp.trigger}: {scalp.note} (spot {scalp.spot:.1f})",
                            speak=f"Scalping opportunity on {name}",
                        )
                        if self.auto_trade:
                            trade_manager.execute_scalp(scalp)

    def _mtf_agrees(self, name: str, option_type: str | None) -> bool:
        """Higher-timeframe filter: 15m view must not oppose the 5m trade."""
        higher = self.latest.get(name, {}).get("15min")
        if not higher or higher.get("option_type") is None:
            return True  # no opposing view yet
        return higher["option_type"] == option_type


live_engine = LiveEngine()
