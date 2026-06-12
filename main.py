"""
OptionMoney AI — single entry point.

    python main.py engine      # headless live engine (signals + auto paper trades)
    python main.py desktop     # Tkinter desktop dashboard
    python main.py api         # FastAPI server for the Android app
    python main.py backtest    # quick momentum backtest on NIFTY
    python main.py train       # train the ML model

Streamlit dashboard:  streamlit run streamlit_app/app.py
"""
from __future__ import annotations

import sys
import time

from backend.logger import get_logger

log = get_logger("main")


def run_engine() -> None:
    from engine.live_engine import live_engine
    live_engine.start()
    log.info("Engine running — Ctrl+C to stop")
    try:
        while True:
            time.sleep(5)
    except KeyboardInterrupt:
        live_engine.stop()
        log.info("Engine stopped")


def run_desktop() -> None:
    from desktop.app import DesktopApp
    DesktopApp().run()


def run_api() -> None:
    import uvicorn
    from backend.config import settings
    uvicorn.run("api.server:app", host=settings.api_host,
                port=settings.api_port, reload=False)


def run_backtest() -> None:
    from backtesting.backtester import main as bt_main
    sys.argv = [sys.argv[0]]
    bt_main()


def run_train() -> None:
    from ai_engine.ml_model import ml_model
    from backend.broker.angel_one import broker
    if broker.login():
        df = broker.get_candles("NIFTY", "5min", days=30)
        print(ml_model.train(df))
    else:
        print("Login failed — check .env credentials")


COMMANDS = {
    "engine": run_engine,
    "desktop": run_desktop,
    "api": run_api,
    "backtest": run_backtest,
    "train": run_train,
}

if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else "desktop"
    if cmd not in COMMANDS:
        print(__doc__)
        sys.exit(1)
    COMMANDS[cmd]()
