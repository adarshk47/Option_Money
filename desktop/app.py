"""
Tkinter desktop dashboard — multi-tab: Live Signals, Option Chain,
Positions/PnL, Backtest panel, Logs. Auto-refreshes in a background
thread; builds to a single .exe with PyInstaller (docs/EXE_BUILD.md).

Run:  python main.py desktop
"""
from __future__ import annotations

import threading
import tkinter as tk
from datetime import datetime
from tkinter import messagebox, ttk

from ai_engine.option_chain_analysis import analyse_option_chain
from backend.broker.angel_one import broker
from backend.broker.paper_broker import paper_broker
from backend.config import settings
from backend.data.option_chain import option_chain_fetcher
from backend.logger import LOG_DIR, get_logger
from database.db import db
from engine.live_engine import live_engine

log = get_logger(__name__)

DARK_BG, PANEL_BG, FG = "#101418", "#1a2027", "#e6e6e6"
GREEN, RED, AMBER = "#26a269", "#e01b24", "#e5a50a"


class DesktopApp:
    def __init__(self) -> None:
        self.root = tk.Tk()
        self.root.title(f"OptionMoney AI — {settings.trading_mode} mode")
        self.root.geometry("1280x800")
        self.root.configure(bg=DARK_BG)
        self._style()
        self._build()
        self._refreshing = True

    def _style(self) -> None:
        s = ttk.Style(self.root)
        s.theme_use("clam")
        s.configure(".", background=DARK_BG, foreground=FG, fieldbackground=PANEL_BG)
        s.configure("Treeview", background=PANEL_BG, foreground=FG,
                    fieldbackground=PANEL_BG, rowheight=24)
        s.configure("TNotebook.Tab", padding=(14, 6))

    # ── Layout ─────────────────────────────────────────────────────

    def _build(self) -> None:
        top = tk.Frame(self.root, bg=DARK_BG)
        top.pack(fill="x", padx=8, pady=6)
        tk.Button(top, text="🔌 Connect", command=self._connect,
                  bg=PANEL_BG, fg=FG).pack(side="left", padx=4)
        tk.Button(top, text="▶ Start engine", command=self._start_engine,
                  bg=GREEN, fg="white").pack(side="left", padx=4)
        tk.Button(top, text="⏹ Stop engine", command=live_engine.stop,
                  bg=RED, fg="white").pack(side="left", padx=4)
        tk.Button(top, text="📤 Export logs", command=self._export_logs,
                  bg=PANEL_BG, fg=FG).pack(side="left", padx=4)
        self.status = tk.Label(top, text="Disconnected", bg=DARK_BG, fg=AMBER)
        self.status.pack(side="right", padx=8)

        nb = ttk.Notebook(self.root)
        nb.pack(fill="both", expand=True, padx=8, pady=4)

        # Tab 1: live signals
        self.sig_tree = self._tree(nb, "🚨 Live Signals", (
            ("underlying", 90), ("timeframe", 70), ("action", 150),
            ("confidence", 90), ("entry_price", 90), ("stop_loss", 90),
            ("target1", 90), ("target2", 90), ("risk_level", 80),
        ))

        # Tab 2: option chain
        frame2 = tk.Frame(nb, bg=DARK_BG)
        nb.add(frame2, text="🔗 Option Chain")
        bar = tk.Frame(frame2, bg=DARK_BG)
        bar.pack(fill="x", pady=4)
        self.oc_var = tk.StringVar(value="NIFTY")
        ttk.Combobox(bar, textvariable=self.oc_var,
                     values=list(settings.watchlist), width=12).pack(side="left", padx=6)
        self.oc_summary = tk.Label(bar, text="—", bg=DARK_BG, fg=FG)
        self.oc_summary.pack(side="left", padx=10)
        cols = (("strike", 80), ("ce_oi", 100), ("ce_chg_oi", 100),
                ("ce_ltp", 80), ("pe_ltp", 80), ("pe_chg_oi", 100), ("pe_oi", 100))
        self.oc_tree = self._raw_tree(frame2, cols)

        # Tab 3: positions / pnl
        frame3 = tk.Frame(nb, bg=DARK_BG)
        nb.add(frame3, text="💰 Positions & PnL")
        self.pnl_label = tk.Label(frame3, text="PnL —", bg=DARK_BG, fg=FG,
                                  font=("Segoe UI", 12, "bold"))
        self.pnl_label.pack(anchor="w", padx=8, pady=6)
        cols = (("symbol", 200), ("option_type", 60), ("entry_price", 90),
                ("ltp", 90), ("quantity", 80), ("stop_loss", 90), ("pnl", 100))
        self.pos_tree = self._raw_tree(frame3, cols)

        # Tab 4: backtest panel
        frame4 = tk.Frame(nb, bg=DARK_BG)
        nb.add(frame4, text="🧪 Backtest")
        row = tk.Frame(frame4, bg=DARK_BG)
        row.pack(fill="x", pady=6)
        self.bt_underlying = tk.StringVar(value="NIFTY")
        self.bt_strategy = tk.StringVar(value="momentum")
        ttk.Combobox(row, textvariable=self.bt_underlying,
                     values=list(settings.watchlist), width=12).pack(side="left", padx=6)
        ttk.Combobox(row, textvariable=self.bt_strategy,
                     values=["momentum", "scalp"], width=12).pack(side="left", padx=6)
        tk.Button(row, text="Run backtest", command=self._run_backtest,
                  bg=PANEL_BG, fg=FG).pack(side="left", padx=6)
        self.bt_output = tk.Text(frame4, bg=PANEL_BG, fg=FG, height=28)
        self.bt_output.pack(fill="both", expand=True, padx=8, pady=6)

        # Tab 5: logs
        frame5 = tk.Frame(nb, bg=DARK_BG)
        nb.add(frame5, text="📜 Logs")
        self.log_text = tk.Text(frame5, bg=PANEL_BG, fg=FG)
        self.log_text.pack(fill="both", expand=True, padx=8, pady=6)

    def _tree(self, nb: ttk.Notebook, title: str, cols) -> ttk.Treeview:
        frame = tk.Frame(nb, bg=DARK_BG)
        nb.add(frame, text=title)
        return self._raw_tree(frame, cols)

    @staticmethod
    def _raw_tree(parent, cols) -> ttk.Treeview:
        names = [c[0] for c in cols]
        tree = ttk.Treeview(parent, columns=names, show="headings")
        for name, width in cols:
            tree.heading(name, text=name.replace("_", " ").title())
            tree.column(name, width=width, anchor="center")
        tree.pack(fill="both", expand=True, padx=8, pady=6)
        return tree

    # ── Actions ────────────────────────────────────────────────────

    def _connect(self) -> None:
        def work():
            ok = broker.login()
            self.root.after(0, lambda: self.status.config(
                text="Connected ✅" if ok else "Login failed ❌",
                fg=GREEN if ok else RED))
        threading.Thread(target=work, daemon=True).start()

    def _start_engine(self) -> None:
        if settings.trading_mode == "LIVE":
            if not messagebox.askyesno(
                    "LIVE MODE", "Engine will place REAL orders. Continue?"):
                return
        live_engine.start()
        self.status.config(text=f"Engine running ({settings.trading_mode})", fg=GREEN)

    def _run_backtest(self) -> None:
        def work():
            from backtesting.backtester import Backtester, STRATEGIES
            try:
                if not broker.is_connected and not broker.login():
                    raise RuntimeError("Broker login failed")
                df = broker.get_candles(self.bt_underlying.get(), "5min", days=20)
                result = Backtester().run(df, STRATEGIES[self.bt_strategy.get()](),
                                          self.bt_underlying.get())
                text = "\n".join(f"{k}: {v}" for k, v in result.summary().items())
                text += "\n\nLast trades:\n" + "\n".join(
                    str(t) for t in result.trade_log[-10:])
            except Exception as exc:  # noqa: BLE001
                text = f"Backtest failed: {exc}"
            self.root.after(0, lambda: (
                self.bt_output.delete("1.0", "end"),
                self.bt_output.insert("1.0", text)))
        threading.Thread(target=work, daemon=True).start()

    def _export_logs(self) -> None:
        messagebox.showinfo("Logs", f"Log files are in:\n{LOG_DIR}")

    # ── Refresh loop ───────────────────────────────────────────────

    def _refresh(self) -> None:
        try:
            # signals
            self.sig_tree.delete(*self.sig_tree.get_children())
            for s in db.recent_signals(30):
                self.sig_tree.insert("", "end", values=(
                    s["underlying"], s["timeframe"], s["action"],
                    f"{s['confidence']:.0f}%", s["entry_price"], s["stop_loss"],
                    s["target1"], s["target2"], s["risk_level"]))
            # option chain
            chain, spot = option_chain_fetcher.fetch(self.oc_var.get())
            if not chain.empty:
                oc = analyse_option_chain(chain, spot)
                self.oc_summary.config(
                    text=f"Spot {spot:,.1f} | PCR {oc['pcr']} | "
                         f"Max pain {oc['max_pain']:,.0f} | {oc['dominance']} dominant")
                self.oc_tree.delete(*self.oc_tree.get_children())
                near = chain[(chain["strike"] - spot).abs() <= spot * 0.03]
                for _, r in near.iterrows():
                    self.oc_tree.insert("", "end", values=(
                        r["strike"], int(r["ce_oi"]), int(r["ce_chg_oi"]),
                        r["ce_ltp"], r["pe_ltp"], int(r["pe_chg_oi"]),
                        int(r["pe_oi"])))
            # positions
            snap = paper_broker.snapshot()
            self.pnl_label.config(
                text=f"Realised ₹{snap['realised_pnl_today']:,.0f}  |  "
                     f"Unrealised ₹{snap['unrealised_pnl']:,.0f}  |  "
                     f"Total ₹{snap['total_pnl_today']:,.0f}  |  "
                     f"Trades {snap['trades_today']}",
                fg=GREEN if snap["total_pnl_today"] >= 0 else RED)
            self.pos_tree.delete(*self.pos_tree.get_children())
            for p in snap["open_positions"]:
                self.pos_tree.insert("", "end", values=(
                    p["symbol"], p["option_type"], f"{p['entry_price']:.2f}",
                    f"{p['ltp']:.2f}", p["quantity"], f"{p['stop_loss']:.2f}",
                    f"{p['pnl']:.2f}"))
            # logs tail
            log_file = LOG_DIR / "trading.log"
            if log_file.exists():
                lines = log_file.read_text(errors="ignore").splitlines()[-40:]
                self.log_text.delete("1.0", "end")
                self.log_text.insert("1.0", "\n".join(lines))
        except Exception:
            log.exception("UI refresh failed")
        if self._refreshing:
            self.root.after(5000, self._refresh)

    def run(self) -> None:
        self.root.after(1000, self._refresh)
        self.root.mainloop()
        self._refreshing = False


if __name__ == "__main__":
    DesktopApp().run()
