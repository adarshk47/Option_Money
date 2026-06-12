"""
Event-driven backtester for the option-buying strategies.

Simulates option premium as a delta-approximation of the underlying
move (ATM delta ~0.5, leverage configurable) — good enough to compare
strategies and tune parameters without paid tick data.

Usage:
    python -m backtesting.backtester --underlying NIFTY --strategy momentum
"""
from __future__ import annotations

import argparse
from dataclasses import dataclass, field

import numpy as np
import pandas as pd

from ai_engine.indicators import add_all_indicators
from backend.logger import get_logger
from database.db import db
from strategies.base import Strategy
from strategies.momentum import MomentumStrategy
from strategies.scalping import ScalpingStrategy

log = get_logger(__name__)

STRATEGIES = {"momentum": MomentumStrategy, "scalp": ScalpingStrategy}


@dataclass
class BacktestResult:
    strategy: str
    underlying: str
    timeframe: str
    trades: int = 0
    wins: int = 0
    total_pnl_pct: float = 0.0
    max_drawdown_pct: float = 0.0
    profit_factor: float = 0.0
    trade_log: list = field(default_factory=list)

    @property
    def win_rate(self) -> float:
        return round(self.wins / self.trades * 100, 1) if self.trades else 0.0

    def summary(self) -> dict:
        return {
            "strategy": self.strategy, "underlying": self.underlying,
            "timeframe": self.timeframe, "trades": self.trades,
            "win_rate_%": self.win_rate,
            "total_pnl_%": round(self.total_pnl_pct, 2),
            "max_drawdown_%": round(self.max_drawdown_pct, 2),
            "profit_factor": round(self.profit_factor, 2),
        }


class Backtester:
    def __init__(self, premium_leverage: float = 12.0,
                 cost_pct: float = 0.05) -> None:
        """
        premium_leverage: 1% underlying move ≈ N% ATM premium move.
        cost_pct: round-trip slippage+brokerage in premium-% per trade.
        """
        self.leverage = premium_leverage
        self.cost = cost_pct

    def run(self, df: pd.DataFrame, strategy: Strategy, underlying: str,
            timeframe: str = "5min", max_hold_bars: int = 24) -> BacktestResult:
        res = BacktestResult(strategy.name, underlying, timeframe)
        if df is None or len(df) < 60:
            log.warning("Backtest skipped: insufficient data")
            return res

        df = add_all_indicators(df).dropna(subset=["ema50", "adx"])
        gross_win = gross_loss = 0.0
        equity, peak = 0.0, 0.0
        i = 50
        while i < len(df) - 1:
            plan = strategy.on_bar(df, i)
            if plan is None:
                i += 1
                continue
            entry = float(df["close"].iloc[i])
            sign = 1 if plan["direction"] == "CE" else -1
            pnl_pct, exit_i, outcome = self._simulate(df, i, entry, sign, plan,
                                                      max_hold_bars)
            premium_pnl = pnl_pct * self.leverage - self.cost
            res.trades += 1
            if premium_pnl > 0:
                res.wins += 1
                gross_win += premium_pnl
            else:
                gross_loss += -premium_pnl
            res.total_pnl_pct += premium_pnl
            equity += premium_pnl
            peak = max(peak, equity)
            res.max_drawdown_pct = max(res.max_drawdown_pct, peak - equity)
            res.trade_log.append({
                "entry_time": str(df.index[i]), "exit_time": str(df.index[exit_i]),
                "direction": plan["direction"], "entry": entry,
                "premium_pnl_%": round(premium_pnl, 2), "outcome": outcome,
                "reason": plan["reason"],
            })
            i = exit_i + 1  # no overlapping trades

        res.profit_factor = gross_win / gross_loss if gross_loss else float("inf")
        db.insert_backtest(
            strategy=strategy.name, underlying=underlying, timeframe=timeframe,
            trades=res.trades, win_rate=res.win_rate,
            total_pnl=round(res.total_pnl_pct, 2),
            max_drawdown=round(res.max_drawdown_pct, 2),
            profit_factor=round(res.profit_factor, 2),
            params=vars(strategy),
        )
        return res

    def _simulate(self, df: pd.DataFrame, i: int, entry: float, sign: int,
                  plan: dict, max_hold: int) -> tuple[float, int, str]:
        """Walk forward bar-by-bar applying SL/T1/T2 with half-exit at T1."""
        sl = entry * (1 - sign * plan["sl_pct"] / 100)
        t1 = entry * (1 + sign * plan["t1_pct"] / 100)
        t2 = entry * (1 + sign * plan["t2_pct"] / 100)
        realised, open_frac, t1_done = 0.0, 1.0, False

        for j in range(i + 1, min(i + 1 + max_hold, len(df))):
            hi, lo = float(df["high"].iloc[j]), float(df["low"].iloc[j])
            touched_sl = lo <= sl if sign == 1 else hi >= sl
            touched_t1 = hi >= t1 if sign == 1 else lo <= t1
            touched_t2 = hi >= t2 if sign == 1 else lo <= t2

            if touched_sl:  # conservative: SL checked first
                realised += open_frac * sign * (sl / entry - 1) * 100
                return realised, j, "SL" if not t1_done else "TRAIL_SL"
            if touched_t1 and not t1_done:
                realised += 0.5 * sign * (t1 / entry - 1) * 100
                open_frac, t1_done, sl = 0.5, True, entry  # trail to breakeven
            if touched_t2 and t1_done:
                realised += open_frac * sign * (t2 / entry - 1) * 100
                return realised, j, "T2"

        j = min(i + max_hold, len(df) - 1)
        last = float(df["close"].iloc[j])
        realised += open_frac * sign * (last / entry - 1) * 100
        return realised, j, "TIME_EXIT"


def main() -> None:
    parser = argparse.ArgumentParser(description="Run a backtest")
    parser.add_argument("--underlying", default="NIFTY")
    parser.add_argument("--strategy", default="momentum", choices=STRATEGIES)
    parser.add_argument("--timeframe", default="5min")
    parser.add_argument("--days", type=int, default=20)
    args = parser.parse_args()

    from backend.broker.angel_one import broker
    if not broker.login():
        print("Broker login failed — provide credentials in .env")
        return
    df = broker.get_candles(args.underlying, args.timeframe, days=args.days)
    bt = Backtester()
    result = bt.run(df, STRATEGIES[args.strategy](), args.underlying, args.timeframe)
    print(pd.Series(result.summary()).to_string())
    print(pd.DataFrame(result.trade_log).tail(15).to_string())


if __name__ == "__main__":
    main()
