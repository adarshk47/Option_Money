"""
OptionMoney AI — Streamlit web dashboard.

Layout:
  • Global ticker strip (Indian indices + world markets) at the top
  • Live IST clock + which trading session is running right now
  • One tab per instrument (NIFTY / SENSEX / SBIN):
      chart with AI-recommendation pins (past + current), expiry info,
      one-click paper-trade buttons, OI flow monitor with selectable
      window (1–360 min) persisted to SQLite, OI-based trend verdict,
      option chain analytics, OI heatmap, historical pattern matcher
  • Separate Paper Trades tab (PnL, positions, signals, history)

No login — runs open. Credentials come from st.secrets / .env.
Run:  streamlit run streamlit_app/app.py
"""
from __future__ import annotations

import os
import sys
import time
from datetime import datetime, timedelta, time as dtime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import streamlit as st

# Copy st.secrets into the environment BEFORE backend.config is imported,
# so Angel One credentials work the same locally and on Streamlit Cloud.
try:
    for _key in ("ANGEL_API_KEY", "ANGEL_CLIENT_ID", "ANGEL_PASSWORD",
                 "ANGEL_TOTP_SECRET", "API_SECRET_KEY", "TRADING_MODE",
                 "TELEGRAM_BOT_TOKEN", "TELEGRAM_CHAT_ID"):
        if _key in st.secrets and _key not in os.environ:
            os.environ[_key] = str(st.secrets[_key])
except Exception:
    pass

import numpy as np
import pandas as pd
import plotly.graph_objects as go
import pytz
from plotly.subplots import make_subplots

from ai_engine.indicators import add_all_indicators
from ai_engine.option_chain_analysis import analyse_option_chain
from ai_engine.pattern_matcher import find_analog_days
from ai_engine.recommendation_engine import recommendation_engine
from backend.broker.angel_one import broker
from backend.broker.paper_broker import paper_broker
from backend.config import settings
from backend.data.global_markets import global_quotes
from backend.data.option_chain import option_chain_fetcher
from database.db import db
from engine.trade_manager import trade_manager

IST = pytz.timezone("Asia/Kolkata")
OI_WINDOWS = [1, 2, 5, 10, 30, 60, 120, 240, 360]   # minutes

st.set_page_config(page_title="OptionMoney AI", page_icon="📈",
                   layout="wide", initial_sidebar_state="expanded")

# one-time housekeeping per session: keep OI history ~2 days
if "oi_purged" not in st.session_state:
    db.purge_oi(keep_days=2)
    st.session_state.oi_purged = True

# ── Sidebar ─────────────────────────────────────────────────────────
st.sidebar.title("⚙️ Controls")
timeframe = st.sidebar.selectbox("Timeframe", settings.timeframes, index=1)
refresh_s = st.sidebar.selectbox("Refresh rate (seconds)", [3, 5, 10, 30],
                                 index=0)
oi_window = st.sidebar.selectbox("OI change window (minutes)", OI_WINDOWS,
                                 index=0)
mode_badge = "🟢 PAPER" if settings.is_paper else "🔴 LIVE"
st.sidebar.markdown(f"**Mode:** {mode_badge}")

if st.sidebar.button("🔌 Connect broker"):
    with st.spinner("Logging in to Angel One..."):
        if broker.login():
            st.sidebar.success("Connected")
            st.cache_data.clear()
            st.rerun()
        else:
            st.sidebar.error("Login failed — check credentials in "
                             "Streamlit secrets (cloud) or .env (local)")
st.sidebar.markdown("🟢 Broker: **connected**" if broker.is_connected
                    else "🔴 Broker: **not connected**")

try:
    from streamlit_autorefresh import st_autorefresh
    st_autorefresh(interval=refresh_s * 1000, key="auto_refresh")
except ImportError:
    pass


# ── Cached data access ──────────────────────────────────────────────
@st.cache_data(ttl=30, show_spinner=False)
def fetch_candles(name: str, tf: str, days: int = 5) -> pd.DataFrame:
    if not broker.is_connected and not broker.login():
        return pd.DataFrame()
    try:
        return broker.get_candles(name, tf, days=days)
    except Exception:
        return pd.DataFrame()


@st.cache_data(ttl=55, show_spinner=False)
def fetch_chain(name: str):
    df, spot = option_chain_fetcher.fetch(name)
    return df, spot, option_chain_fetcher.last_expiry.get(name, "")


@st.cache_data(ttl=120, show_spinner=False)
def fetch_global():
    return global_quotes()


@st.cache_data(ttl=300, show_spinner=False)
def fetch_history_30d(name: str) -> pd.DataFrame:
    """30 days of 5-min candles for the pattern matcher."""
    if not broker.is_connected:
        return pd.DataFrame()
    try:
        return broker.get_candles(name, "5min", days=30)
    except Exception:
        return pd.DataFrame()


_DEMO_BASE = {"NIFTY": 25000.0, "SENSEX": 82000.0, "SBIN": 880.0}


def demo_candles(name: str, tf: str, bars: int = 300) -> pd.DataFrame:
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


# ── Header: global ticker + session clock ───────────────────────────
def render_header() -> None:
    quotes = fetch_global()
    cols = st.columns(len(quotes))
    for col, (name, q) in zip(cols, quotes.items()):
        if q:
            col.metric(name, f"{q['price']:,.0f}", f"{q['chg_pct']:+.2f}%")
        else:
            col.metric(name, "—")

    now = datetime.now(IST)
    wd, t = now.weekday(), now.time()
    if wd >= 5:
        nse = "🔴 CLOSED (weekend)"
    elif dtime(9, 0) <= t < dtime(9, 15):
        nse = "🟡 PRE-OPEN auction"
    elif dtime(9, 15) <= t <= dtime(15, 30):
        nse = "🟢 OPEN — intraday session" + (
            " · ⏰ closing hour (writers book profits)"
            if t >= dtime(14, 45) else "")
    else:
        nse = "🔴 CLOSED"

    live_now = []
    if wd < 5:
        if dtime(5, 30) <= t <= dtime(11, 30):
            live_now.append("Asia (Nikkei/Hang Seng)")
        if dtime(12, 30) <= t <= dtime(21, 0):
            live_now.append("Europe")
        if t >= dtime(19, 0) or t <= dtime(1, 30):
            live_now.append("US (Dow/Nasdaq)")
    extra = f" · Abhi live: **{', '.join(live_now)}**" if live_now else ""
    st.markdown(f"🕒 **{now.strftime('%d %b %Y, %H:%M:%S IST')}** · "
                f"NSE: **{nse}**{extra}")


# ── OI flow with persistent history ─────────────────────────────────
def _store_oi_snapshot(name: str, chain: pd.DataFrame, spot: float) -> None:
    """Persist ~1 snapshot/minute to SQLite (survives reruns/restarts)."""
    if chain.empty:
        return
    key = f"last_oi_store_{name}"
    now = time.time()
    if now - st.session_state.get(key, 0) >= 55:
        db.insert_oi(
            underlying=name,
            ce_oi=float(chain["ce_oi"].sum()),
            pe_oi=float(chain["pe_oi"].sum()),
            ce_chg_oi=float(chain["ce_chg_oi"].sum()),
            pe_chg_oi=float(chain["pe_chg_oi"].sum()),
            spot=spot,
        )
        st.session_state[key] = now


def _window_delta(rows: list[dict], minutes: int) -> tuple[float, float] | None:
    """(ΔCE, ΔPE) between the latest snapshot and one `minutes` ago."""
    if len(rows) < 2:
        return None
    latest = rows[-1]
    target = datetime.fromisoformat(latest["ts"]) - timedelta(minutes=minutes)
    base = None
    for r in rows:
        if datetime.fromisoformat(r["ts"]) <= target:
            base = r
        else:
            break
    base = base or rows[0]
    if base["ts"] == latest["ts"]:
        return None
    return (latest["ce_oi"] - base["ce_oi"], latest["pe_oi"] - base["pe_oi"])


def _oi_trend(dce: float, dpe: float) -> tuple[str, str]:
    net = dpe - dce
    if abs(net) < 1:
        return "SIDEWAYS", "⚪"
    if dpe > 0 and dce <= 0:
        return "BULLISH — PUT writing + CALL unwinding", "🟢"
    if dce > 0 and dpe <= 0:
        return "BEARISH — CALL writing + PUT unwinding", "🔴"
    if dce < 0 and dpe < 0:
        return "WIND-UP — both sides unwinding (profit booking)", "🟠"
    if net > 0:
        return "BULLISH tilt — PUT side building faster", "🟢"
    return "BEARISH tilt — CALL side building faster", "🔴"


def render_oi_flow(name: str, chain: pd.DataFrame, spot: float,
                   expiry: str) -> None:
    st.subheader("📡 OI flow & market trend")
    if chain.empty:
        st.info("Option chain not available for this instrument "
                "(SENSEX/BFO needs broker chain data).")
        return

    market_open = (datetime.now(IST).weekday() < 5
                   and dtime(9, 15) <= datetime.now(IST).time() <= dtime(15, 30))
    st.caption(f"Weekly expiry: **{expiry or '—'}** · spot {spot:,.1f}"
               + ("" if market_open
                  else " · 🌙 market offline — showing **last available** NSE data"))

    _store_oi_snapshot(name, chain, spot)
    since = (datetime.now() - timedelta(hours=30)).isoformat()
    rows = db.oi_history(name, since)

    delta = _window_delta(rows, oi_window)
    if delta:
        dce, dpe = delta
        a, b, c = st.columns(3)
        a.metric(f"CE OI Δ ({oi_window}m)", f"{dce:+,.0f}",
                 help="Positive = call writing building (bearish pressure)")
        b.metric(f"PE OI Δ ({oi_window}m)", f"{dpe:+,.0f}",
                 help="Positive = put writing building (bullish support)")
        trend, dot = _oi_trend(dce, dpe)
        c.metric("Net flow (PE−CE)", f"{(dpe - dce):+,.0f}")
        st.markdown(f"### {dot} Current market trend (OI-based): **{trend}**")
    else:
        # market offline / first run: fall back to NSE's day change-in-OI
        dce, dpe = float(chain["ce_chg_oi"].sum()), float(chain["pe_chg_oi"].sum())
        a, b, c = st.columns(3)
        a.metric("CE OI Δ (day)", f"{dce:+,.0f}")
        b.metric("PE OI Δ (day)", f"{dpe:+,.0f}")
        trend, dot = _oi_trend(dce, dpe)
        c.metric("Net flow (PE−CE)", f"{(dpe - dce):+,.0f}")
        st.markdown(f"### {dot} Last session's trend (day OI change): **{trend}**")
        st.caption("Live 1-min tracking starts automatically when snapshots "
                   "accumulate (collected every minute while this app runs; "
                   "saved to the database until night cleanup).")

    if rows:
        hist_df = pd.DataFrame(rows)
        hist_df["time"] = pd.to_datetime(hist_df["ts"]).dt.strftime("%H:%M")
        chart_df = hist_df.set_index("time")[["ce_oi", "pe_oi"]]
        chart_df.columns = ["CE total OI", "PE total OI"]
        st.line_chart(chart_df, height=200)

    if market_open and datetime.now(IST).time() >= dtime(14, 45):
        st.warning("⏰ **Closing hour:** writers typically square off now — "
                   "expect unwinding and fast premium decay. Prefer exits "
                   "over fresh option buying.")


# ── Pattern matcher panel ───────────────────────────────────────────
def render_pattern_match(name: str, expiry: str) -> None:
    st.subheader("🔮 History pattern match (analog days)")
    hist = fetch_history_30d(name)
    if hist.empty:
        st.info("Needs broker connection + 30 days of 5-min history.")
        return

    expiry_weekday = None
    note = "matched against the last ~30 trading days"
    try:
        exp_date = pd.to_datetime(expiry, format="%d-%b-%Y").date()
        if exp_date == datetime.now(IST).date():
            expiry_weekday = exp_date.weekday()
            note = ("**EXPIRY DAY** — matched only against past expiry-weekday "
                    "days (same structural behaviour)")
    except (ValueError, TypeError):
        pass

    result = find_analog_days(hist, top_n=3, expiry_weekday=expiry_weekday)
    if not result.get("available"):
        st.info(result.get("reason", "No match available yet."))
        return

    st.caption(f"Today so far: **{result['today_move_pct']:+.2f}%** · {note}")
    for m in result["matches"]:
        st.markdown(
            f"• **{m['date']}** — similarity {m['similarity']}% · that day was "
            f"{m['move_so_far_pct']:+.2f}% at this point, then moved "
            f"**{m['rest_of_day_pct']:+.2f}%** till close "
            f"(day total {m['day_close_pct']:+.2f}%)")
    bias_colour = ("🟢" if "BULL" in result["bias"]
                   else "🔴" if "BEAR" in result["bias"] else "⚪")
    st.markdown(f"{bias_colour} **Historical bias:** {result['bias']} "
                f"(avg {result['avg_rest_of_day_pct']:+.2f}% rest-of-day "
                f"across matches)")


# ── Per-instrument tab ──────────────────────────────────────────────
def _save_dashboard_signal(name: str, tf: str, rec) -> None:
    """Persist actionable recommendations once per candle (for chart pins)."""
    if not rec.option_type or rec.confidence < 60:
        return
    key = f"saved_sig_{name}_{tf}"
    stamp = (rec.action, str(rec.spot))
    if st.session_state.get(key) == stamp:
        return
    db.insert_signal(
        underlying=name, timeframe=tf, action=rec.action,
        confidence=rec.confidence, entry_price=rec.entry_price,
        stop_loss=rec.stop_loss, target1=rec.target1, target2=rec.target2,
        risk_level=rec.risk_level, reasoning="; ".join(rec.reasoning[:5]),
    )
    st.session_state[key] = stamp


def _signal_pins(name: str, dfi: pd.DataFrame) -> pd.DataFrame:
    """Past signals for this underlying that fall inside the chart window."""
    sigs = pd.DataFrame(db.recent_signals(200))
    if sigs.empty:
        return sigs
    sigs = sigs[(sigs["underlying"] == name)
                & sigs["action"].str.contains("CE|PE", na=False)]
    if sigs.empty:
        return sigs
    sigs["ts"] = pd.to_datetime(sigs["ts"])
    start, end = dfi.index.min(), dfi.index.max()
    return sigs[(sigs["ts"] >= start) & (sigs["ts"] <= end)]


def render_instrument(name: str, tf: str) -> None:
    df = fetch_candles(name, tf)
    chain, chain_spot, expiry = fetch_chain(name)

    is_demo = df.empty
    if is_demo:
        df = demo_candles(name, tf)
        st.info("📡 No live candles right now (broker not connected / market "
                "closed / API busy) — showing **DEMO data**, switches to live "
                "automatically." if broker.is_connected else
                "🔌 Broker not connected — **DEMO data**. Connect from the sidebar.")

    dfi = add_all_indicators(df)
    rec = recommendation_engine.analyse(name, df, tf, option_chain=chain,
                                        chain_spot=chain_spot)
    if not is_demo:
        _save_dashboard_signal(name, tf, rec)

    # ── Price + signal strip ────────────────────────────────────────
    colour = ("green" if rec.option_type == "CE"
              else "red" if rec.option_type == "PE" else "gray")
    c1, c2, c3, c4, c5, c6 = st.columns(6)
    c1.metric(f"{name} spot", f"{rec.spot:,.1f}")
    c2.markdown(f"### :{colour}[{rec.action}]" + (" `DEMO`" if is_demo else ""))
    c3.metric("Confidence", f"{rec.confidence}%")
    c4.metric("Risk:Reward", rec.risk_reward if rec.option_type else "—")
    c5.metric("Risk level", rec.risk_level)
    c6.metric("Weekly expiry", expiry or "—")

    if rec.option_type:
        st.success(f"**Entry (spot)** {rec.entry_price} | **SL** {rec.stop_loss} "
                   f"| **T1** {rec.target1} | **T2** {rec.target2}")

    # ── Chart with recommendation pins ─────────────────────────────
    fig = make_subplots(rows=3, cols=1, shared_xaxes=True,
                        row_heights=[0.6, 0.2, 0.2], vertical_spacing=0.03)
    fig.add_trace(go.Candlestick(x=dfi.index, open=dfi["open"],
                                 high=dfi["high"], low=dfi["low"],
                                 close=dfi["close"], name=name), row=1, col=1)
    for col, dash in (("ema9", "dot"), ("ema20", "dash"), ("vwap", "solid")):
        fig.add_trace(go.Scatter(x=dfi.index, y=dfi[col], name=col.upper(),
                                 line=dict(width=1, dash=dash)), row=1, col=1)
    fig.add_trace(go.Scatter(x=dfi.index, y=dfi["supertrend"],
                             name="Supertrend", line=dict(width=1.5)),
                  row=1, col=1)

    pins = pd.DataFrame() if is_demo else _signal_pins(name, dfi)
    if not pins.empty:
        is_ce = pins["action"].str.contains("CE")
        fig.add_trace(go.Scatter(
            x=pins["ts"], y=pins["entry_price"], mode="markers",
            name="AI signals",
            marker=dict(
                size=13,
                symbol=np.where(is_ce, "triangle-up", "triangle-down"),
                color=np.where(is_ce, "lime", "red"),
                line=dict(width=1, color="black"),
            ),
            hovertemplate=("<b>%{customdata[0]}</b><br>%{x|%d %b %H:%M}<br>"
                           "Entry %{y:.1f} | SL %{customdata[1]} | "
                           "T1 %{customdata[2]}<br>Confidence %{customdata[3]}%"
                           "<extra></extra>"),
            customdata=pins[["action", "stop_loss", "target1",
                             "confidence"]].to_numpy(),
        ), row=1, col=1)

    fig.add_trace(go.Bar(x=dfi.index, y=dfi["volume"], name="Volume"),
                  row=2, col=1)
    fig.add_trace(go.Scatter(x=dfi.index, y=dfi["rsi"], name="RSI"),
                  row=3, col=1)
    fig.add_hline(y=70, line_dash="dot", row=3, col=1)
    fig.add_hline(y=30, line_dash="dot", row=3, col=1)
    fig.update_layout(height=620, xaxis_rangeslider_visible=False,
                      margin=dict(l=10, r=10, t=30, b=10), showlegend=True)
    st.plotly_chart(fig, use_container_width=True, key=f"chart_{name}")

    # ── One-click paper trade at the chart ─────────────────────────
    t1, t2, t3 = st.columns([1, 1, 3])
    if t1.button(f"🟢 Buy {name} CE (paper)", key=f"buyce_{name}",
                 use_container_width=True):
        ok, msg = trade_manager.manual_buy(name, "CE", rec.spot,
                                           reason="One-click chart trade")
        (st.success if ok else st.error)(msg)
    if t2.button(f"🔴 Buy {name} PE (paper)", key=f"buype_{name}",
                 use_container_width=True):
        ok, msg = trade_manager.manual_buy(name, "PE", rec.spot,
                                           reason="One-click chart trade")
        (st.success if ok else st.error)(msg)
    t3.caption("Buys 1 ATM lot within your capital limit, with default "
               "SL/T1/T2 — manage it in the Paper Trades tab.")

    with st.expander("🧠 AI reasoning", expanded=False):
        for r in rec.reasoning:
            st.write("•", r)

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
            st.plotly_chart(heat, use_container_width=True, key=f"heat_{name}")

    render_pattern_match(name, expiry)


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
        st.info("No open paper positions — use the buy buttons under any chart.")

    st.subheader("🚨 Recent AI signals")
    sigs = db.recent_signals(25)
    if sigs:
        st.dataframe(pd.DataFrame(sigs), use_container_width=True)
    else:
        st.info("No signals stored yet.")

    st.subheader("📒 Trade history")
    trades = db.recent_trades(25)
    if trades:
        st.dataframe(pd.DataFrame(trades), use_container_width=True)
    else:
        st.info("No trades yet.")


# ── Page ────────────────────────────────────────────────────────────
render_header()
st.divider()

tab_labels = [f"📊 {n}" for n in settings.watchlist] + ["💰 Paper Trades"]
tabs = st.tabs(tab_labels)
for tab, name in zip(tabs[:-1], settings.watchlist):
    with tab:
        render_instrument(name, timeframe)
with tabs[-1]:
    render_paper_tab()

st.caption("⚠️ Educational tool. Options trading carries substantial risk of "
           "loss. Signals are probabilistic, not financial advice.")
