"""
Trade manager — converts an AI Recommendation or ScalpSignal into an
actual option BUY (paper or live), then manages SL/T1/T2/trailing exits
on the option premium.

Options BUYING only: every order this module places is a BUY; exits are
SELLs of an existing long. It never writes options.
"""
from __future__ import annotations

from datetime import datetime
from typing import Optional

from ai_engine.recommendation_engine import Recommendation
from ai_engine.scalping_engine import ScalpSignal, scalping_engine
from alerts.alert_manager import alert_manager
from backend.broker.angel_one import broker
from backend.broker.paper_broker import paper_broker
from backend.config import INSTRUMENTS, settings
from backend.data.instruments import instrument_master
from backend.logger import get_logger

log = get_logger(__name__)


class TradeManager:
    def __init__(self) -> None:
        self.live_positions: dict[str, dict] = {}  # symbol -> meta (LIVE mode)

    # ── Entry ──────────────────────────────────────────────────────

    def execute_recommendation(self, rec: Recommendation,
                               min_confidence: float = 70.0) -> bool:
        if rec.option_type is None or rec.confidence < min_confidence:
            return False
        return self._enter(
            underlying=rec.underlying, option_type=rec.option_type,
            spot=rec.spot, sl_pct=settings.default_sl_percent,
            t1_pct=settings.default_target1_percent,
            t2_pct=settings.default_target2_percent,
            reason=f"{rec.action} ({rec.confidence}%) "
                   f"[{rec.timeframe}] {'; '.join(rec.reasoning[:2])}",
        )

    def execute_scalp(self, sig: ScalpSignal) -> bool:
        contract = instrument_master.find_atm_option(
            sig.underlying, sig.spot, sig.direction)
        if not contract:
            return False
        premium = self._option_ltp(contract)
        if not premium:
            return False
        # ₹-based scalp targets converted to percents of premium
        t1_pct = sig.premium_t1_rs / premium * 100
        t2_pct = sig.premium_t2_rs / premium * 100
        return self._enter(
            underlying=sig.underlying, option_type=sig.direction,
            spot=sig.spot, sl_pct=sig.premium_sl_pct,
            t1_pct=t1_pct, t2_pct=t2_pct,
            reason=f"SCALP {sig.trigger}: {sig.note}",
            contract=contract, premium=premium,
        )

    def manual_buy(self, underlying: str, option_type: str, spot: float,
                   reason: str = "Manual chart trade") -> tuple[bool, str]:
        """One-click BUY from the dashboard (paper or live per settings)."""
        try:
            ok = self._enter(
                underlying=underlying, option_type=option_type, spot=spot,
                sl_pct=settings.default_sl_percent,
                t1_pct=settings.default_target1_percent,
                t2_pct=settings.default_target2_percent, reason=reason,
            )
            if ok:
                return True, f"BUY {underlying} {option_type} placed ({settings.trading_mode})"
            return False, ("Trade not placed — broker connection needed to "
                           "price the ATM option (or risk limits hit)")
        except Exception:  # noqa: BLE001
            log.exception("Manual buy failed")
            return False, ("Trade failed — connect the broker first (option "
                           "contract/premium data needed). See logs for detail.")

    def _enter(self, underlying: str, option_type: str, spot: float,
               sl_pct: float, t1_pct: float, t2_pct: float, reason: str,
               contract: Optional[dict] = None,
               premium: Optional[float] = None) -> bool:
        contract = contract or instrument_master.find_atm_option(
            underlying, spot, option_type)
        if not contract:
            log.warning("No ATM %s contract found for %s", option_type, underlying)
            return False
        premium = premium or self._option_ltp(contract)
        if not premium or premium <= 0:
            log.warning("No premium quote for %s", contract["symbol"])
            return False

        lot = int(contract.get("lotsize") or INSTRUMENTS[underlying]["lot_size"])
        lots = max(int(settings.max_capital_per_trade // (premium * lot)), 0)
        if lots == 0:
            log.warning("Premium %.2f x lot %d exceeds capital per trade",
                        premium, lot)
            return False
        qty = lots * lot
        sl = premium * (1 - sl_pct / 100)
        t1 = premium * (1 + t1_pct / 100)
        t2 = premium * (1 + t2_pct / 100)

        if settings.is_paper:
            pos = paper_broker.buy(
                symbol=contract["symbol"], underlying=underlying,
                option_type=option_type, price=premium, quantity=qty,
                stop_loss=sl, target1=t1, target2=t2, reason=reason,
            )
            ok = pos is not None
        else:
            order_id = broker.place_order(
                tradingsymbol=contract["symbol"],
                symboltoken=str(contract["token"]),
                exchange=INSTRUMENTS[underlying]["option_exchange"],
                transaction="BUY", quantity=qty,
            )
            ok = order_id is not None
            if ok:
                self.live_positions[contract["symbol"]] = {
                    "order_id": order_id, "entry": premium, "qty": qty,
                    "sl": sl, "t1": t1, "t2": t2, "token": contract["token"],
                    "underlying": underlying, "t1_done": False,
                    "entry_time": datetime.now(),
                }
        if ok:
            alert_manager.send(
                f"✅ BUY {contract['symbol']}",
                f"{reason}\nEntry ₹{premium:.2f} x{qty} | SL ₹{sl:.2f} "
                f"| T1 ₹{t1:.2f} | T2 ₹{t2:.2f}",
                speak=f"Bought {underlying} {option_type}",
            )
        return ok

    # ── Position management (call every few seconds) ───────────────

    def manage_positions(self) -> None:
        if settings.is_paper:
            for trade_id, pos in list(paper_broker.positions.items()):
                ltp = self._symbol_ltp(pos.symbol)
                if ltp:
                    event = paper_broker.update_price(trade_id, ltp)
                    if event:
                        scalping_engine.record_exit(pos.underlying)
                        alert_manager.send(f"🔔 {event} — {pos.symbol}",
                                           f"Exited at ₹{ltp:.2f}")
        else:
            for symbol, meta in list(self.live_positions.items()):
                ltp = self._symbol_ltp(symbol, meta.get("token"))
                if not ltp:
                    continue
                exit_reason = None
                if ltp <= meta["sl"]:
                    exit_reason = "STOPLOSS"
                elif not meta["t1_done"] and ltp >= meta["t1"]:
                    meta["t1_done"], meta["sl"] = True, meta["entry"]
                elif meta["t1_done"] and ltp >= meta["t2"]:
                    exit_reason = "TARGET2"
                if exit_reason:
                    broker.place_order(
                        tradingsymbol=symbol, symboltoken=str(meta["token"]),
                        exchange=INSTRUMENTS[meta["underlying"]]["option_exchange"],
                        transaction="SELL", quantity=meta["qty"],
                    )
                    scalping_engine.record_exit(meta["underlying"])
                    self.live_positions.pop(symbol, None)
                    alert_manager.send(f"🔔 {exit_reason} — {symbol}",
                                       f"Squared off at ₹{ltp:.2f}")

    def square_off_all(self) -> None:
        """EOD auto square-off (run at 15:15 IST)."""
        if settings.is_paper:
            ltps = {p.symbol: self._symbol_ltp(p.symbol) or p.ltp
                    for p in paper_broker.positions.values()}
            paper_broker.exit_all(ltps)
        else:
            for symbol, meta in list(self.live_positions.items()):
                broker.place_order(
                    tradingsymbol=symbol, symboltoken=str(meta["token"]),
                    exchange=INSTRUMENTS[meta["underlying"]]["option_exchange"],
                    transaction="SELL", quantity=meta["qty"],
                )
                self.live_positions.pop(symbol, None)
        alert_manager.send("🏁 EOD square-off", "All positions closed")

    # ── Quotes ─────────────────────────────────────────────────────

    @staticmethod
    def _option_ltp(contract: dict) -> Optional[float]:
        try:
            broker.ensure_session()
            data = broker.api.ltpData(
                "NFO" if not str(contract["symbol"]).startswith("SENSEX") else "BFO",
                contract["symbol"], str(contract["token"]))
            if data and data.get("status"):
                return float(data["data"]["ltp"])
        except Exception:
            log.exception("Option LTP fetch failed for %s", contract.get("symbol"))
        return None

    def _symbol_ltp(self, symbol: str, token: Optional[str] = None) -> Optional[float]:
        if token is None:
            opts = instrument_master.load()
            row = opts[opts["symbol"] == symbol]
            if row.empty:
                return None
            token = str(row.iloc[0]["token"])
        return self._option_ltp({"symbol": symbol, "token": token})


trade_manager = TradeManager()
