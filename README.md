# 📈 OptionMoney AI — Indian Options Buying Trading Ecosystem

AI-powered intraday **options BUYING** system for **NIFTY · BANKNIFTY · SENSEX · SBIN**
with scalping + momentum engines, real-time multi-timeframe analysis, Angel One
SmartAPI integration, and three frontends: **Desktop (Tkinter) · Web (Streamlit) · Android (Flutter)**.

> ⚠️ **Risk disclaimer:** This is an educational/decision-support tool. Options
> trading carries substantial risk of loss. Signals are probabilistic and not
> financial advice. Always start in **PAPER** mode.

---

## Architecture

```
                      ┌────────────────────────────┐
                      │   Angel One SmartAPI       │
                      │  (REST + WebSocket V2)     │
                      └────────────┬───────────────┘
                                   │
       ┌───────────────────────────┴───────────────────────────┐
       │                  SHARED BACKEND (Python)               │
       │  backend/   broker, market data, option chain, config │
       │  ai_engine/ indicators, patterns, SMC, OI, ML, scorer │
       │  engine/    live multi-timeframe loop + trade manager │
       │  database/  SQLite (trades, signals, backtests)        │
       │  alerts/    Telegram, voice, desktop popups            │
       └───────┬───────────────┬───────────────┬───────────────┘
               │               │               │
        ┌──────┴─────┐  ┌──────┴──────┐  ┌─────┴──────┐
        │  Desktop   │  │  Streamlit  │  │  FastAPI   │
        │  (Tkinter) │  │  dashboard  │  │   api/     │
        └────────────┘  └─────────────┘  └─────┬──────┘
                                               │ REST + auth
                                        ┌──────┴──────┐
                                        │ Android app │
                                        │  (Flutter)  │
                                        └─────────────┘
```

## Folder structure & what every file does

| Path | Purpose |
|---|---|
| `main.py` | Entry point: `engine` / `desktop` / `api` / `backtest` / `train` |
| `backend/config.py` | All settings from `.env`; instrument metadata (tokens, lot sizes, strike steps) |
| `backend/logger.py` | Console + rotating-file logging for every module |
| `backend/broker/angel_one.py` | SmartAPI singleton: TOTP login, auto token refresh, retry/backoff, candles, LTP, orders, positions, holdings, order book, margin |
| `backend/broker/websocket_feed.py` | SmartWebSocketV2 live ticks with auto-reconnect |
| `backend/broker/paper_broker.py` | Paper trading: simulated fills, SL/T1/T2/trailing, daily loss & trade limits, persisted to SQLite |
| `backend/data/instruments.py` | Instrument master download/cache; ATM option contract resolver |
| `backend/data/option_chain.py` | NSE option chain fetcher (OI, IV, volume per strike) with caching |
| `ai_engine/indicators.py` | VWAP, EMA 9/20/50, RSI, MACD, Bollinger, Supertrend, ADX, ATR (pure pandas — no TA-Lib binary needed) |
| `ai_engine/candlestick_patterns.py` | Hammer, shooting star, engulfing, doji, morning/evening star, marubozu |
| `ai_engine/market_structure.py` | Swing HH/HL/LH/LL, trend, breakout/breakdown, reversal, S/R |
| `ai_engine/smart_money.py` | Liquidity sweeps, order blocks, fair value gaps, BOS, CHoCH |
| `ai_engine/option_chain_analysis.py` | PCR, max pain, OI buildup (long/short buildup, short covering), CE/PE dominance, OI S/R |
| `ai_engine/recommendation_engine.py` | **The brain** — weighted fusion of all analyses → STRONG BUY CE/PE, BUY CE/PE, SCALP, AVOID with entry/SL/T1/T2/confidence/risk/reasoning |
| `ai_engine/scalping_engine.py` | 5m scalps: VWAP reclaim, EMA ignition, ORB, liquidity-sweep reversal; ₹5–₹20 premium targets, tight SL, time stops, re-entry cooldown, expiry-day mode |
| `ai_engine/ml_model.py` | RandomForest direction classifier (train + predict overlay) |
| `engine/live_engine.py` | Always-on loop: scans 1/5/10/15/20-min TFs, MTF confirmation, auto square-off 15:15 IST |
| `engine/trade_manager.py` | Recommendation/scalp → ATM option BUY (paper or live), premium-space SL/target management |
| `strategies/` | Backtestable strategy classes (momentum breakout, VWAP scalp) |
| `backtesting/backtester.py` | Event-driven backtester with premium approximation, half-exit at T1, trail to breakeven; results saved to DB |
| `database/db.py` | SQLite schema + queries (trades, signals, backtests, settings) |
| `alerts/` | Telegram, voice (pyttsx3), desktop popup (plyer) via one `alert_manager` |
| `api/server.py` | FastAPI backend for the Android app (token auth, signals, candles, chain, PnL) |
| `streamlit_app/app.py` | Web dashboard: charts, OI heatmap, chain analysis, signals, PnL, login |
| `desktop/app.py` | Tkinter multi-tab dashboard: signals, chain, positions, backtest panel, logs |
| `android/` | Flutter app: login, watchlist, signals, charts, PnL, notifications, voice, dark mode |
| `docs/` | Deployment, EXE build, APK build, Streamlit Cloud guides |

## Quick start

```bash
# 1. Setup
python -m venv .venv && source .venv/bin/activate   # Windows: .venv\Scripts\activate
pip install -r requirements.txt
cp .env.example .env          # then fill in Angel One credentials

# 2. Run (pick one)
python main.py engine         # headless live engine — signals + paper trades
python main.py desktop        # desktop dashboard
streamlit run streamlit_app/app.py   # web dashboard
python main.py api            # backend for the Android app

# 3. Extras
python main.py backtest       # quick NIFTY momentum backtest
python main.py train          # train the ML overlay model
python -m backtesting.backtester --underlying SBIN --strategy scalp --days 30
```

### Angel One credentials
1. Create an app at https://smartapi.angelbroking.com/ → get **API key**
2. Enable TOTP at https://smartapi.angelbroking.com/enable-totp → save the **TOTP secret**
3. Fill `ANGEL_API_KEY`, `ANGEL_CLIENT_ID`, `ANGEL_PASSWORD` (PIN), `ANGEL_TOTP_SECRET` in `.env`

### Going live
Set `TRADING_MODE=LIVE` in `.env` **only after** you are satisfied with paper
results. Risk limits (`MAX_DAILY_LOSS`, `MAX_TRADES_PER_DAY`,
`MAX_CAPITAL_PER_TRADE`) are enforced in code.

## How a signal is generated

Each scan fuses 8 weighted analysis families into a score in [-1, +1]:

| Family | Weight | Inputs |
|---|---|---|
| Trend | 20% | EMA stack, Supertrend, VWAP position |
| Indicators | 20% | RSI zone, MACD cross, ADX gate |
| Patterns | 12% | Candlestick reversals/continuations |
| Structure | 13% | HH/HL trend, breakout/breakdown, reversal |
| Smart money | 13% | BOS/CHoCH, sweeps, OBs, FVGs |
| Option chain | 12% | PCR, max pain, OI buildup, dominance |
| Volume | 5% | Surge confirmation |
| ML | 5% | RandomForest direction probability |

Score ≥ +0.35 → **STRONG BUY CE** · ≤ −0.35 → **STRONG BUY PE** ·
|score| < 0.08 → **SIDEWAYS — AVOID**. A 5-minute auto-trade additionally
requires the 15-minute view to agree (multi-timeframe confirmation), and a
volatility gate filters dead markets.

## Docs
- [docs/DEPLOYMENT.md](docs/DEPLOYMENT.md) — full setup & cloud deployment
- [docs/EXE_BUILD.md](docs/EXE_BUILD.md) — Windows .exe with PyInstaller
- [docs/APK_BUILD.md](docs/APK_BUILD.md) — Android APK with Flutter
- [docs/STREAMLIT_DEPLOY.md](docs/STREAMLIT_DEPLOY.md) — Streamlit Cloud
