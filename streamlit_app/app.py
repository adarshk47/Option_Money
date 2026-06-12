"""
OptionMoney AI — Streamlit web dashboard (mobile-responsive, cloud-deployable).

Layout: one tab per instrument (NIFTY / BANKNIFTY / SENSEX / SBIN) with
chart, AI signal, option-chain analytics and a live OI-flow monitor
(per-minute OI change, build-up direction, unwinding / profit-booking
detection near the close), plus a separate Paper Trades tab.

Run locally:   streamlit run streamlit_app/app.py
Deploy:        see docs/STREAMLIT_DEPLOY.md
Login:         password = API_SECRET_KEY (.env locally, st.secrets on cloud)
"""
from __future__ import annotations

import os
import sys
import time
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import streamlit as st

# On Streamlit Cloud there is no .env file — copy st.secrets into the
# process environment BEFORE backend.config is imported, so Angel One
# credentials and API_SECRET_KEY work the same locally and on the cloud.
try:
    for _key in ("ANGEL_API_KEY", "ANGEL_CLIENT_ID", "ANGEL_PASSWORD",
                 "ANGEL_TOTP_SECRET", "API_SECRET_KEY", "TRADING_MODE",
                 "TELEGRAM_BOT_TOKEN", "TELEGRAM_CHAT_ID"):
        if _key in st.secrets and _key not in os.environ:
            os.environ[_key] = str(st.secrets[_key])
except Exception:
    pass  # no secrets configured — fall back to .env

import numpy as np
import pandas as pd
import plotly.graph_objects as go
import pytz
from plotly.subplots import make_subplots

from ai_engine.indicators import add_all_indicators
from ai_engine.option_chain_analysis import analyse_option_chain
from ai_engine.recommendation_engine import recommendation_engine
from backend.broker.angel_one import broker
from backend.broker.paper_broker import paper_broker
from backend.config import settings
from backend.data.option_chain import option_chain_fetcher
from database.db import db

IST = pytz.timezone("Asia/Kolkata")

st.set_page_config(page_title="OptionMoney AI", page_icon="📈",
                   layout="wide", initial_sidebar_state="expanded")


# ── Auth ────────────────────────────────────────────────────────────
def _secret() -> str:
    try:
        return st.secrets.get("API_SECRET_KEY", settings.api_secret_key)
    except Exception:
        return settings.api_secret_key


if "authed" not in st.session_state:
    st.session_state.authed = False
if not st.session_state.authed:
    st.title("📈 OptionMoney AI")
    pwd = st.text_input("Access key", type="password")
    if st.button("Login", type="primary"):
        if pwd == _secret():
            st.session_state.authed = True
            st.rerun()
        else:
            st.error("Wrong access key")
    st.stop()

# ── Sidebar ─────────────────────────────────────────────────────────
st.sidebar.title("⚙️ Controls")
timeframe = st.sidebar.selectbox("Timeframe", settings.timeframes, index=1)
refresh_s = st.sidebar.selectbox("Refresh rate (seconds)", [3, 5, 10, 30],
                                 index=0)
mode_badge = "🟢 PAPER" if settings.is_paper else "🔴 LIVE"
st.sidebar.markdown(f"**Mode:** {mode_badge}")

if st.sidebar.button("🔌 Connect broker"):
    with st.spinner("Logging in to Angel One..."):
        if broker.login():
            st.sidebar.success("Connected")
            st.cache_data.clear()   # drop any cached empty results
            st.rerun()
        else:
            st.sidebar.error("Login failed — check credentials in "
                             "Streamlit secrets (cloud) or .env (local)")
st.sidebar.markdown("🟢 Broker: **connected**" if broker.is_connected
                    else "🔴 Broker: **not connected**")

# UI refreshes every `refresh_s` seconds; candles update ~30 s,
# option chain ~55 s (cached to respect API limits).
try:
    from streamlit_autorefresh import st_autorefresh
    st_autorefresh(interval=refresh_s * 1000, key="auto_refresh")
except ImportError:
    pass


# ── Cached data access ──────────────────────────────────────────────
@st.cache_data(ttl=30, show_spinner=False)
def fetch_candles(name: str, tf: str) -> pd.DataFrame:
    if not broker.is_connected and not broker.login():
        return pd.DataFrame()
    try:
        return broker.get_candles(name, tf, days=5)
    except Exception:
        return pd.DataFrame()


@st.cache_data(ttl=55, show_spinner=False)
def fetch_chain(name: str):
    df, spot = option_chain_fetcher.fetch(name)
    return df, spot, option_chain_fetcher.last_expiry.get(name, "")


_DEMO_BASE = {"NIFTY": 25000.0, "BANKNIFTY": 56000.0,
              "SENSEX": 82000.0, "SBIN": 880.0}


def demo_candles(name: str, tf: str, bars: int = 300) -> pd.DataFrame:
    """Synthetic OHLCV so a tab is never blank while data loads."""
    rng = np.random.default_rng(abs(hash(name)) % 2**32)
    base = _DEMO_BASE.get(name, 1000.0)
    close = np.cumsum(rng.normal(0, base * 0.0006, bars)) + base
    openp = np.roll(close, 1)
    openp[0] = close[0]
    idx = pd.date_range(end=pd.Timestamp.now().floor("min"),
                        periods=bars, freq=tf)
    return pd.DataFrame({
        "open": openp,
        "high": np.maximum(openp, close) + rng.uniform(0, base * 0.0008, bars),
        "low": np.minimum(openp, close) - rng.uniform(0, base * 0.0008, bars),
        "close": close,
        "volume": rng.integers(10_000, 90_000, bars).astype(float),
    }, index=idx)


# ── OI flow monitor ─────────────────────────────────────────────────
def _record_oi_snapshot(name: str, chain: pd.DataFrame, spot: float) -> list:
    """Keep a rolling per-minute history of total CE/PE OI in the session."""
    hist = st.session_state.setdefault("oi_hist", {}).setdefault(name, [])
    if chain.empty:
        return hist
    now = time.time()
    if not hist or now - hist[-1]["ts"] >= 55:        # ~1 snapshot per minute
        hist.append({
            "ts": now,
            "time": datetime.now(IST).strftime("%H:%M"),
            "ce_oi": float(chain["ce_oi"].sum()),
            "pe_oi": float(chain["pe_oi"].sum()),
            "spot": spot,
        })
        del hist[:-45]                                 # keep last ~45 minutes
    return hist


def _oi_verdict(dce: float, dpe: float, closing_hour: bool) -> tuple[str, str]:
    """Classify the last 1-min OI flow. Returns (verdict, emoji-colour)."""
    if dce == 0 and dpe == 0:
        return "No fresh OI movement in the last minute.", "⚪"
    if dpe > 0 and dce <= 0:
        return ("PUT side building + CALL unwinding → writers defending "
                "downside — **bullish flow**"), "🟢"
    if dce > 0 and dpe <= 0:
        return ("CALL side building + PUT unwinding → writers capping "
                "upside — **bearish flow**"), "🔴"
    if dce < 0 and dpe < 0:
        base = ("**Both sides unwinding** — positions being wound up / "
                "profit booking in progress")
        if closing_hour:
            base += (". Closing time is near: writers typically square off "
                     "now — expect a **sell-off in premiums** (theta + "
                     "unwinding), avoid fresh option buying.")
        return base, "🟠"
    return ("Both sides adding OI → range-building / volatility expected — "
            "wait for one side to win"), "🟡"


def render_oi_flow(name: str, chain: pd.DataFrame, spot: float,
                   expiry: str) -> None:
    st.subheader("📡 Live OI flow (1-min tracking)")
    if chain.empty:
        st.info("Option chain not available for this instrument right now "
                "(SENSEX/BFO chain needs broker data; NSE may also throttle "
                "after hours).")
        return
    st.caption(f"Latest expiry: **{expiry or '—'}** · spot {spot:,.1f} · "
               "snapshots every ~1 min")

    hist = _record_oi_snapshot(name, chain, spot)
    now_ist = datetime.now(IST).time()
    closing_hour = now_ist >= datetime.strptime("14:45", "%H:%M").time()

    if len(hist) >= 2:
        prev, cur = hist[-2], hist[-1]
        mins = max((cur["ts"] - prev["ts"]) / 60, 1e-9)
        dce = (cur["ce_oi"] - prev["ce_oi"]) / mins
        dpe = (cur["pe_oi"] - prev["pe_oi"]) / mins

        a, b, c = st.columns(3)
        a.metric("CE OI Δ / min", f"{dce:+,.0f}",
                 help="Positive = call writing building (bearish pressure)")
        b.metric("PE OI Δ / min", f"{dpe:+,.0f}",
                 help="Positive = put writing building (bullish support)")
        net = dpe - dce
        c.metric("Net flow (PE−CE)", f"{net:+,.0f}",
                 delta="bullish" if net > 0 else "bearish" if net < 0 else "flat")

        verdict, dot = _oi_verdict(dce, dpe, closing_hour)
        st.markdown(f"{dot} {verdict}")

        flow_df = pd.DataFrame(hist).set_index("time")[["ce_oi", "pe_oi"]]
        flow_df.columns = ["CE total OI", "PE total OI"]
        st.line_chart(flow_df, height=200)
    else:
        st.info("Collecting OI snapshots… first reading needs ~1 minute. "
                "The chart and Δ/min metrics appear from the 2nd snapshot.")

    if closing_hour:
        st.warning("⏰ **Closing hour (after 14:45 IST):** intraday writers "
                   "book profits and premiums decay fast. Fresh option "
                   "buying here is high-risk — prefer exits over entries.")


# ── Per-instrument tab ──────────────────────────────────────────────
def render_instrument(name: str, tf: str) -> None:
    df = fetch_candles(name, tf)
    chain, chain_spot, expiry = fetch_chain(name)

    is_demo = df.empty
    if is_demo:
        df = demo_candles(name, tf)
        if broker.is_connected:
            st.info("📡 Broker connected, but no candle data returned yet "
                    "(market closed / API busy). Showing **DEMO data** — "
                    "switches to live data automatically.")
        else:
            st.warning("🔌 Broker not connected — showing **DEMO data**. "
                       "Connect from the sidebar to load live candles.")

    dfi = add_all_indicators(df)
    rec = recommendation_engine.analyse(name, df, tf, option_chain=chain,
                                        chain_spot=chain_spot)

    colour = ("green" if rec.option_type == "CE"
              else "red" if rec.option_type == "PE" else "gray")
    c1, c2, c3, c4, c5 = st.columns(5)
    c1.metric("Spot", f"{rec.spot:,.1f}")
    c2.markdown(f"### :{colour}[{rec.action}]"
                + (" `DEMO`" if is_demo else ""))
    c3.metric("Confidence", f"{rec.confidence}%")
    c4.metric("Risk:Reward", rec.risk_reward if rec.option_type else "—")
    c5.metric("Risk level", rec.risk_level)

    if rec.option_type:
        st.success(f"**Entry (spot)** {rec.entry_price} | **SL** {rec.stop_loss} "
                   f"| **T1** {rec.target1} | **T2** {rec.target2}")
    with st.expander("🧠 AI reasoning", expanded=False):
        for r in rec.reasoning:
            st.write("•", r)

    # Chart
    fig = make_subplots(rows=3, cols=1, shared_xaxes=True,
                        row_heights=[0.6, 0.2, 0.2], vertical_spacing=0.03)
    fig.add_trace(go.Candlestick(x=dfi.index, open=dfi["open"],
                                 high=dfi["high"], low=dfi["low"],
                                 close=dfi["close"], name="Price"),
                  row=1, col=1)
    for col, dash in (("ema9", "dot"), ("ema20", "dash"), ("vwap", "solid")):
        fig.add_trace(go.Scatter(x=dfi.index, y=dfi[col], name=col.upper(),
                                 line=dict(width=1, dash=dash)), row=1, col=1)
    fig.add_trace(go.Scatter(x=dfi.index, y=dfi["supertrend"],
                             name="Supertrend", line=dict(width=1.5)),
                  row=1, col=1)
    fig.add_trace(go.Bar(x=dfi.index, y=dfi["volume"], name="Volume"),
                  row=2, col=1)
    fig.add_trace(go.Scatter(x=dfi.index, y=dfi["rsi"], name="RSI"),
                  row=3, col=1)
    fig.add_hline(y=70, line_dash="dot", row=3, col=1)
    fig.add_hline(y=30, line_dash="dot", row=3, col=1)
    fig.update_layout(height=600, xaxis_rangeslider_visible=False,
                      margin=dict(l=10, r=10, t=30, b=10), showlegend=True)
    st.plotly_chart(fig, use_container_width=True, key=f"chart_{name}")

    # OI flow + chain analytics + heatmap
    render_oi_flow(name, chain, chain_spot, expiry)

    left, right = st.columns([1, 1])
    with left:
        st.subheader("🔗 Option chain analysis")
        oc = analyse_option_chain(chain, chain_spot)
        if oc.get("available"):
            a, b, c = st.columns(3)
            a.metric("PCR", oc["pcr"])
            b.metric("Max pain", f"{oc['max_pain']:,.0f}")
            c.metric("Dominance", oc["dominance"])
            st.caption(f"OI support **{oc['oi_support']:,.0f}** · "
                       f"OI resistance **{oc['oi_resistance']:,.0f}** · "
                       f"{oc['oi_buildup']['pe_view']}")
        else:
            st.info("Option chain analytics unavailable right now.")
    with right:
        st.subheader("🔥 OI heatmap (near ATM)")
        if not chain.empty and chain_spot:
            near = chain[(chain["strike"] - chain_spot).abs()
                         <= chain_spot * 0.03]
            heat = go.Figure()
            heat.add_trace(go.Bar(x=near["strike"], y=near["ce_oi"],
                                  name="CE OI", marker_color="crimson"))
            heat.add_trace(go.Bar(x=near["strike"], y=-near["pe_oi"],
                                  name="PE OI", marker_color="seagreen"))
            heat.add_vline(x=chain_spot, line_dash="dash")
            heat.update_layout(barmode="relative", height=300,
                               margin=dict(l=10, r=10, t=10, b=10))
            st.plotly_chart(heat, use_container_width=True,
                            key=f"heat_{name}")


# ── Paper trades tab ────────────────────────────────────────────────
def render_paper_tab() -> None:
    st.subheader("💰 Paper trading PnL")
    snap = paper_broker.snapshot()
    p1, p2, p3, p4 = st.columns(4)
    p1.metric("Realised today", f"₹{snap['realised_pnl_today']:,.0f}")
    p2.metric("Unrealised", f"₹{snap['unrealised_pnl']:,.0f}")
    p3.metric("Total today", f"₹{snap['total_pnl_today']:,.0f}")
    p4.metric("Trades today", snap["trades_today"])

    st.subheader("📂 Open positions")
    if snap["open_positions"]:
        st.dataframe(pd.DataFrame(snap["open_positions"]),
                     use_container_width=True)
    else:
        st.info("No open paper positions.")

    st.subheader("🚨 Recent AI signals")
    sigs = db.recent_signals(25)
    if sigs:
        st.dataframe(pd.DataFrame(sigs), use_container_width=True)
    else:
        st.info("No signals stored yet — run the live engine "
                "(`python main.py engine`).")

    st.subheader("📒 Trade history")
    trades = db.recent_trades(25)
    if trades:
        st.dataframe(pd.DataFrame(trades), use_container_width=True)
    else:
        st.info("No trades yet.")


# ── Layout: one tab per instrument + paper trades ───────────────────
st.title("📈 OptionMoney AI — Trading Dashboard")

tab_labels = [f"📊 {n}" for n in settings.watchlist] + ["💰 Paper Trades"]
tabs = st.tabs(tab_labels)
for tab, name in zip(tabs[:-1], settings.watchlist):
    with tab:
        render_instrument(name, timeframe)
with tabs[-1]:
    render_paper_tab()

st.caption("⚠️ Educational tool. Options trading carries substantial risk of "
           "loss. Signals are probabilistic, not financial advice.")
