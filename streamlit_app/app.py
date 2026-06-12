"""
OptionMoney AI — Streamlit web dashboard (mobile-responsive, cloud-deployable).

Run locally:   streamlit run streamlit_app/app.py
Deploy:        see docs/STREAMLIT_DEPLOY.md

Login: password = API_SECRET_KEY (.env locally, st.secrets on cloud).
"""
from __future__ import annotations

import os
import sys
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

import pandas as pd
import plotly.graph_objects as go
from plotly.subplots import make_subplots

from ai_engine.indicators import add_all_indicators
from ai_engine.option_chain_analysis import analyse_option_chain
from ai_engine.recommendation_engine import recommendation_engine
from backend.broker.angel_one import broker
from backend.broker.paper_broker import paper_broker
from backend.config import settings
from backend.data.option_chain import option_chain_fetcher
from database.db import db

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

# ── Auto refresh ────────────────────────────────────────────────────
try:
    from streamlit_autorefresh import st_autorefresh
    st_autorefresh(interval=30_000, key="auto_refresh")
except ImportError:
    pass

# ── Sidebar ─────────────────────────────────────────────────────────
st.sidebar.title("⚙️ Controls")
underlying = st.sidebar.selectbox("Instrument", settings.watchlist)
timeframe = st.sidebar.selectbox("Timeframe", settings.timeframes, index=1)
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

st.title(f"📈 {underlying} — AI Trading Dashboard")

# ── Data fetch (cached) ─────────────────────────────────────────────
@st.cache_data(ttl=60, show_spinner=False)
def fetch_candles(name: str, tf: str) -> pd.DataFrame:
    if not broker.is_connected and not broker.login():
        return pd.DataFrame()
    try:
        return broker.get_candles(name, tf, days=5)
    except Exception:
        return pd.DataFrame()


@st.cache_data(ttl=60, show_spinner=False)
def fetch_chain(name: str):
    return option_chain_fetcher.fetch(name)


_DEMO_BASE = {"NIFTY": 25000.0, "BANKNIFTY": 56000.0,
              "SENSEX": 82000.0, "SBIN": 880.0}


def demo_candles(name: str, tf: str, bars: int = 300) -> pd.DataFrame:
    """Synthetic OHLCV so the dashboard is never blank while data loads."""
    import numpy as np
    rng = np.random.default_rng(abs(hash(name)) % 2**32)
    base = _DEMO_BASE.get(name, 1000.0)
    close = np.cumsum(rng.normal(0, base * 0.0006, bars)) + base
    openp = np.roll(close, 1)
    openp[0] = close[0]
    idx = pd.date_range(end=pd.Timestamp.now().floor("min"),
                        periods=bars, freq=tf.replace("min", "min"))
    return pd.DataFrame({
        "open": openp,
        "high": np.maximum(openp, close) + rng.uniform(0, base * 0.0008, bars),
        "low": np.minimum(openp, close) - rng.uniform(0, base * 0.0008, bars),
        "close": close,
        "volume": rng.integers(10_000, 90_000, bars).astype(float),
    }, index=idx)


df = fetch_candles(underlying, timeframe)
chain, chain_spot = fetch_chain(underlying)

is_demo = df.empty
if is_demo:
    # don't cache the failure — retry on the next auto-refresh (30 s)
    fetch_candles.clear()
    df = demo_candles(underlying, timeframe)
    if broker.is_connected:
        st.info("📡 Broker connected, but no candle data returned yet "
                "(market closed / API busy). Showing **DEMO data** — the page "
                "auto-refreshes every 30 s and will switch to live data "
                "automatically.")
    else:
        st.warning("🔌 Broker not connected — showing **DEMO data**. Connect "
                   "from the sidebar (Angel One credentials in Streamlit "
                   "secrets / .env) to load live candles.")

dfi = add_all_indicators(df)
rec = recommendation_engine.analyse(underlying, df, timeframe,
                                    option_chain=chain, chain_spot=chain_spot)

# ── Signal banner ───────────────────────────────────────────────────
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

# ── Chart ───────────────────────────────────────────────────────────
fig = make_subplots(rows=3, cols=1, shared_xaxes=True,
                    row_heights=[0.6, 0.2, 0.2], vertical_spacing=0.03)
fig.add_trace(go.Candlestick(x=dfi.index, open=dfi["open"], high=dfi["high"],
                             low=dfi["low"], close=dfi["close"], name="Price"),
              row=1, col=1)
for col, dash in (("ema9", "dot"), ("ema20", "dash"), ("vwap", "solid")):
    fig.add_trace(go.Scatter(x=dfi.index, y=dfi[col], name=col.upper(),
                             line=dict(width=1, dash=dash)), row=1, col=1)
fig.add_trace(go.Scatter(x=dfi.index, y=dfi["supertrend"], name="Supertrend",
                         line=dict(width=1.5)), row=1, col=1)
fig.add_trace(go.Bar(x=dfi.index, y=dfi["volume"], name="Volume"), row=2, col=1)
fig.add_trace(go.Scatter(x=dfi.index, y=dfi["rsi"], name="RSI"), row=3, col=1)
fig.add_hline(y=70, line_dash="dot", row=3, col=1)
fig.add_hline(y=30, line_dash="dot", row=3, col=1)
fig.update_layout(height=650, xaxis_rangeslider_visible=False,
                  margin=dict(l=10, r=10, t=30, b=10), showlegend=True)
st.plotly_chart(fig, use_container_width=True)

# ── Option chain + OI heatmap ───────────────────────────────────────
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
        st.info("Option chain unavailable for this instrument right now.")

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
        st.plotly_chart(heat, use_container_width=True)

# ── PnL + signals + trades ──────────────────────────────────────────
st.subheader("💰 PnL (paper)")
snap = paper_broker.snapshot()
p1, p2, p3, p4 = st.columns(4)
p1.metric("Realised today", f"₹{snap['realised_pnl_today']:,.0f}")
p2.metric("Unrealised", f"₹{snap['unrealised_pnl']:,.0f}")
p3.metric("Total today", f"₹{snap['total_pnl_today']:,.0f}")
p4.metric("Trades today", snap["trades_today"])

tab1, tab2 = st.tabs(["🚨 Recent AI signals", "📒 Trade history"])
with tab1:
    sigs = db.recent_signals(25)
    if sigs:
        st.dataframe(pd.DataFrame(sigs), use_container_width=True)
    else:
        st.info("No signals stored yet — run the live engine.")
with tab2:
    trades = db.recent_trades(25)
    if trades:
        st.dataframe(pd.DataFrame(trades), use_container_width=True)
    else:
        st.info("No trades yet.")

st.caption("⚠️ Educational tool. Options trading carries substantial risk of "
           "loss. Signals are probabilistic, not financial advice.")
