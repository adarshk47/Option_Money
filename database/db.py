"""
SQLite persistence layer (PostgreSQL-compatible SQL — switch by setting
DATABASE_URL). Stores trades, AI signals, backtest results and settings.

Thread-safe: every call opens a short-lived connection, which is the
recommended pattern for SQLite under multi-threaded servers.
"""
from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Optional

from backend.config import PROJECT_ROOT, settings
from backend.logger import get_logger

log = get_logger(__name__)

DB_PATH = PROJECT_ROOT / "database" / "trading.db"

_SCHEMA = """
CREATE TABLE IF NOT EXISTS trades (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts TEXT NOT NULL,
    symbol TEXT NOT NULL,
    underlying TEXT NOT NULL,
    option_type TEXT,
    side TEXT NOT NULL,
    entry_price REAL NOT NULL,
    exit_price REAL,
    quantity INTEGER NOT NULL,
    stop_loss REAL,
    target1 REAL,
    target2 REAL,
    pnl REAL,
    status TEXT DEFAULT 'OPEN',
    mode TEXT DEFAULT 'PAPER',
    reason TEXT
);

CREATE TABLE IF NOT EXISTS signals (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts TEXT NOT NULL,
    underlying TEXT NOT NULL,
    timeframe TEXT,
    action TEXT NOT NULL,
    confidence REAL,
    entry_price REAL,
    stop_loss REAL,
    target1 REAL,
    target2 REAL,
    risk_level TEXT,
    reasoning TEXT
);

CREATE TABLE IF NOT EXISTS backtest_results (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts TEXT NOT NULL,
    strategy TEXT NOT NULL,
    underlying TEXT NOT NULL,
    timeframe TEXT,
    trades INTEGER,
    win_rate REAL,
    total_pnl REAL,
    max_drawdown REAL,
    profit_factor REAL,
    params TEXT
);

CREATE TABLE IF NOT EXISTS user_settings (
    key TEXT PRIMARY KEY,
    value TEXT
);

CREATE TABLE IF NOT EXISTS oi_history (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts TEXT NOT NULL,
    underlying TEXT NOT NULL,
    ce_oi REAL,
    pe_oi REAL,
    ce_chg_oi REAL,
    pe_chg_oi REAL,
    spot REAL
);

CREATE TABLE IF NOT EXISTS predictions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts TEXT NOT NULL,
    underlying TEXT NOT NULL,
    timeframe TEXT,
    action TEXT,
    option_type TEXT,
    confidence REAL,
    spot REAL,
    entry_price REAL,
    stop_loss REAL,
    target1 REAL,
    target2 REAL,
    chart_pattern TEXT,
    pattern_bias TEXT,
    oi_bias TEXT,
    reasoning TEXT
);

CREATE INDEX IF NOT EXISTS idx_trades_ts ON trades(ts);
CREATE INDEX IF NOT EXISTS idx_signals_ts ON signals(ts);
CREATE INDEX IF NOT EXISTS idx_oi_hist ON oi_history(underlying, ts);
CREATE INDEX IF NOT EXISTS idx_pred ON predictions(underlying, ts);
"""


class Database:
    def __init__(self, path: Path = DB_PATH) -> None:
        self.path = path
        self.path.parent.mkdir(exist_ok=True)
        with self._conn() as conn:
            conn.executescript(_SCHEMA)
        log.info("Database ready at %s", self.path)

    @contextmanager
    def _conn(self):
        conn = sqlite3.connect(self.path, timeout=30)
        conn.row_factory = sqlite3.Row
        try:
            yield conn
            conn.commit()
        finally:
            conn.close()

    # ── Trades ─────────────────────────────────────────────────────

    def insert_trade(self, **kw: Any) -> int:
        with self._conn() as conn:
            cur = conn.execute(
                """INSERT INTO trades
                   (ts, symbol, underlying, option_type, side, entry_price,
                    quantity, stop_loss, target1, target2, mode, reason)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?,?)""",
                (datetime.now().isoformat(), kw["symbol"], kw["underlying"],
                 kw.get("option_type"), kw["side"], kw["entry_price"],
                 kw["quantity"], kw.get("stop_loss"), kw.get("target1"),
                 kw.get("target2"), kw.get("mode", "PAPER"), kw.get("reason")),
            )
            return int(cur.lastrowid)

    def update_trade(self, trade_id: int, status: Optional[str] = None,
                     exit_price: Optional[float] = None,
                     pnl: Optional[float] = None) -> None:
        sets, vals = [], []
        if status is not None:
            sets.append("status=?"); vals.append(status)
        if exit_price is not None:
            sets.append("exit_price=?"); vals.append(exit_price)
        if pnl is not None:
            sets.append("pnl=?"); vals.append(pnl)
        if not sets:
            return
        vals.append(trade_id)
        with self._conn() as conn:
            conn.execute(f"UPDATE trades SET {', '.join(sets)} WHERE id=?", vals)

    def recent_trades(self, limit: int = 50) -> list[dict]:
        with self._conn() as conn:
            rows = conn.execute(
                "SELECT * FROM trades ORDER BY id DESC LIMIT ?", (limit,)
            ).fetchall()
        return [dict(r) for r in rows]

    # ── Signals ────────────────────────────────────────────────────

    def insert_signal(self, **kw: Any) -> int:
        with self._conn() as conn:
            cur = conn.execute(
                """INSERT INTO signals
                   (ts, underlying, timeframe, action, confidence, entry_price,
                    stop_loss, target1, target2, risk_level, reasoning)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
                (datetime.now().isoformat(), kw["underlying"], kw.get("timeframe"),
                 kw["action"], kw.get("confidence"), kw.get("entry_price"),
                 kw.get("stop_loss"), kw.get("target1"), kw.get("target2"),
                 kw.get("risk_level"), kw.get("reasoning")),
            )
            return int(cur.lastrowid)

    def recent_signals(self, limit: int = 50) -> list[dict]:
        with self._conn() as conn:
            rows = conn.execute(
                "SELECT * FROM signals ORDER BY id DESC LIMIT ?", (limit,)
            ).fetchall()
        return [dict(r) for r in rows]

    # ── OI history (intraday snapshots, kept ~2 days) ──────────────

    def insert_oi(self, underlying: str, ce_oi: float, pe_oi: float,
                  ce_chg_oi: float, pe_chg_oi: float, spot: float) -> None:
        with self._conn() as conn:
            conn.execute(
                """INSERT INTO oi_history
                   (ts, underlying, ce_oi, pe_oi, ce_chg_oi, pe_chg_oi, spot)
                   VALUES (?,?,?,?,?,?,?)""",
                (datetime.now().isoformat(), underlying, ce_oi, pe_oi,
                 ce_chg_oi, pe_chg_oi, spot),
            )

    def oi_history(self, underlying: str, since_iso: str) -> list[dict]:
        with self._conn() as conn:
            rows = conn.execute(
                "SELECT * FROM oi_history WHERE underlying=? AND ts>=? "
                "ORDER BY ts", (underlying, since_iso),
            ).fetchall()
        return [dict(r) for r in rows]

    def purge_oi(self, keep_days: int = 2) -> None:
        cutoff = (datetime.now() - timedelta(days=keep_days)).isoformat()
        with self._conn() as conn:
            conn.execute("DELETE FROM oi_history WHERE ts < ?", (cutoff,))

    # ── Predictions (timestamped, kept ~2 days) ────────────────────

    def insert_prediction(self, **kw: Any) -> None:
        with self._conn() as conn:
            conn.execute(
                """INSERT INTO predictions
                   (ts, underlying, timeframe, action, option_type, confidence,
                    spot, entry_price, stop_loss, target1, target2,
                    chart_pattern, pattern_bias, oi_bias, reasoning)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (datetime.now().isoformat(), kw["underlying"], kw.get("timeframe"),
                 kw.get("action"), kw.get("option_type"), kw.get("confidence"),
                 kw.get("spot"), kw.get("entry_price"), kw.get("stop_loss"),
                 kw.get("target1"), kw.get("target2"), kw.get("chart_pattern"),
                 kw.get("pattern_bias"), kw.get("oi_bias"), kw.get("reasoning")),
            )

    def todays_predictions(self, underlying: str, limit: int = 60) -> list[dict]:
        today = datetime.now().date().isoformat()
        with self._conn() as conn:
            rows = conn.execute(
                "SELECT * FROM predictions WHERE underlying=? AND ts>=? "
                "ORDER BY id DESC LIMIT ?", (underlying, today, limit),
            ).fetchall()
        return [dict(r) for r in rows]

    def purge_predictions(self, keep_days: int = 2) -> None:
        cutoff = (datetime.now() - timedelta(days=keep_days)).isoformat()
        with self._conn() as conn:
            conn.execute("DELETE FROM predictions WHERE ts < ?", (cutoff,))

    # ── Backtests / settings ───────────────────────────────────────

    def insert_backtest(self, **kw: Any) -> None:
        with self._conn() as conn:
            conn.execute(
                """INSERT INTO backtest_results
                   (ts, strategy, underlying, timeframe, trades, win_rate,
                    total_pnl, max_drawdown, profit_factor, params)
                   VALUES (?,?,?,?,?,?,?,?,?,?)""",
                (datetime.now().isoformat(), kw["strategy"], kw["underlying"],
                 kw.get("timeframe"), kw.get("trades"), kw.get("win_rate"),
                 kw.get("total_pnl"), kw.get("max_drawdown"),
                 kw.get("profit_factor"), str(kw.get("params", {}))),
            )

    def set_setting(self, key: str, value: str) -> None:
        with self._conn() as conn:
            conn.execute(
                "INSERT INTO user_settings(key, value) VALUES(?,?) "
                "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                (key, value),
            )

    def get_setting(self, key: str, default: str = "") -> str:
        with self._conn() as conn:
            row = conn.execute(
                "SELECT value FROM user_settings WHERE key=?", (key,)
            ).fetchone()
        return row["value"] if row else default


db = Database()
