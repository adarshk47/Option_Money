"""
Paper trading engine — identical interface to live order flow, but all
fills are simulated and persisted to SQLite so PnL survives restarts.

Tracks: open positions, realised/unrealised PnL, SL/target hits,
trailing stoploss, daily loss limits and trades-per-day limits.
"""
from __future__ import annotations

import threading
from dataclasses import dataclass, field
from datetime import datetime, date
from typing import Optional

from backend.config import settings
from backend.logger import get_logger
from database.db import db

log = get_logger(__name__)


@dataclass
class PaperPosition:
    trade_id: int
    symbol: str
    underlying: str
    option_type: str            # CE / PE
    entry_price: float
    quantity: int
    stop_loss: float
    target1: float
    target2: float
    entry_time: datetime = field(default_factory=datetime.now)
    trail_sl: Optional[float] = None
    t1_done: bool = False
    ltp: float = 0.0

    @property
    def pnl(self) -> float:
        return (self.ltp - self.entry_price) * self.quantity


class PaperBroker:
    def __init__(self) -> None:
        self.positions: dict[int, PaperPosition] = {}
        self.realised_pnl_today: float = 0.0
        self.trades_today: int = 0
        self._day: date = date.today()
        self._lock = threading.Lock()

    def _roll_day(self) -> None:
        if date.today() != self._day:
            self._day = date.today()
            self.realised_pnl_today = 0.0
            self.trades_today = 0

    # ── Risk gates ─────────────────────────────────────────────────

    def can_trade(self) -> tuple[bool, str]:
        self._roll_day()
        if self.trades_today >= settings.max_trades_per_day:
            return False, "Daily trade limit reached"
        if self.realised_pnl_today <= -settings.max_daily_loss:
            return False, "Daily loss limit hit — trading halted"
        return True, "OK"

    # ── Order flow ─────────────────────────────────────────────────

    def buy(self, symbol: str, underlying: str, option_type: str,
            price: float, quantity: int, stop_loss: float,
            target1: float, target2: float, reason: str = "") -> Optional[PaperPosition]:
        with self._lock:
            ok, msg = self.can_trade()
            if not ok:
                log.warning("Trade blocked: %s", msg)
                return None
            trade_id = db.insert_trade(
                symbol=symbol, underlying=underlying, option_type=option_type,
                side="BUY", entry_price=price, quantity=quantity,
                stop_loss=stop_loss, target1=target1, target2=target2,
                mode="PAPER", reason=reason,
            )
            pos = PaperPosition(
                trade_id=trade_id, symbol=symbol, underlying=underlying,
                option_type=option_type, entry_price=price, quantity=quantity,
                stop_loss=stop_loss, target1=target1, target2=target2, ltp=price,
            )
            self.positions[trade_id] = pos
            self.trades_today += 1
            log.info("[PAPER] BUY %s x%d @ %.2f (SL %.2f, T1 %.2f, T2 %.2f)",
                     symbol, quantity, price, stop_loss, target1, target2)
            return pos

    def update_price(self, trade_id: int, ltp: float) -> Optional[str]:
        """
        Feed a new price into an open position. Applies exit logic:
        - SL / trailing SL hit -> full exit
        - Target1 hit          -> exit half, trail SL to entry
        - Target2 hit          -> full exit
        Returns an event string when something happened.
        """
        with self._lock:
            pos = self.positions.get(trade_id)
            if not pos:
                return None
            pos.ltp = ltp
            effective_sl = max(pos.stop_loss, pos.trail_sl or 0)

            if ltp <= effective_sl:
                return self._close(pos, ltp, "STOPLOSS")

            if not pos.t1_done and ltp >= pos.target1:
                half = max(pos.quantity // 2, 1)
                self.realised_pnl_today += (ltp - pos.entry_price) * half
                pos.quantity -= half
                pos.t1_done = True
                pos.trail_sl = pos.entry_price  # risk-free runner
                db.update_trade(pos.trade_id, status="T1_HIT", exit_price=ltp)
                log.info("[PAPER] T1 hit %s — booked half, SL trailed to entry", pos.symbol)
                if pos.quantity <= 0:
                    return self._close(pos, ltp, "TARGET1")
                return "TARGET1_PARTIAL"

            if pos.t1_done and ltp >= pos.target2:
                return self._close(pos, ltp, "TARGET2")

            # Trail SL upward once in profit (scalping style)
            if pos.t1_done:
                new_trail = ltp * 0.95
                if new_trail > (pos.trail_sl or 0):
                    pos.trail_sl = new_trail
            return None

    def exit_all(self, ltp_lookup: dict[str, float]) -> None:
        """Square off everything (e.g. 3:15 PM auto-exit)."""
        with self._lock:
            for pos in list(self.positions.values()):
                ltp = ltp_lookup.get(pos.symbol, pos.ltp)
                self._close(pos, ltp, "EOD_SQUAREOFF")

    def _close(self, pos: PaperPosition, exit_price: float, event: str) -> str:
        pnl = (exit_price - pos.entry_price) * pos.quantity
        self.realised_pnl_today += pnl
        db.update_trade(pos.trade_id, status=event, exit_price=exit_price, pnl=pnl)
        self.positions.pop(pos.trade_id, None)
        log.info("[PAPER] EXIT %s @ %.2f (%s) PnL=%.2f", pos.symbol, exit_price, event, pnl)
        return event

    # ── Reporting ──────────────────────────────────────────────────

    def snapshot(self) -> dict:
        self._roll_day()
        unrealised = sum(p.pnl for p in self.positions.values())
        return {
            "open_positions": [vars(p) | {"pnl": p.pnl} for p in self.positions.values()],
            "realised_pnl_today": round(self.realised_pnl_today, 2),
            "unrealised_pnl": round(unrealised, 2),
            "total_pnl_today": round(self.realised_pnl_today + unrealised, 2),
            "trades_today": self.trades_today,
        }


paper_broker = PaperBroker()
