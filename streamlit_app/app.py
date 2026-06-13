"""
OptionMoney AI — Streamlit web dashboard.

Layout:
  • Global ticker (Indian indices + world markets + US futures) with a
    live open/closed dot per market, refreshed every 30 s
  • IST clock + which trading session is running right now
  • One tab per instrument (NIFTY / SENSEX / SBIN):
      one-line AI prediction, chart with price hover/crosshair, current
      price line and recommendation pins (past + current), one-click
      paper-trade buttons, OI flow with selectable window (1–360 min,
      persisted to SQLite, offline fallback), option chain analytics,
      OI heatmap, and a multi-year history pattern matcher backed by a
      local cache file that backfills toward 5 years automatically
  • Separate Paper Trades tab

No login. Credentials come from st.secrets / .env.
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

from ai_engine.chart_patterns import detect_chart_pattern
from ai_engine.indicators import add_all_indicators
from ai_engine.option_chain_analysis import analyse_option_chain
from ai_engine.pattern_matcher import find_analog_days
from ai_engine.recommendation_engine import recommendation_engine
from backend.broker.angel_one import broker
from backend.broker.paper_broker import paper_broker
from backend.config import settings
from backend.data.global_markets import MARKET_GROUP, global_quotes
from backend.data.history_store import coverage_days, get_history, load_cached
from backend.data.option_chain import option_chain_fetcher
from database.db import db
from engine.trade_manager import trade_manager

IST = pytz.timezone("Asia/Kolkata")
OI_WINDOWS = [1, 2, 5, 10, 15, 30, 60]   # minutes (multi-window OI table)

st.set_page_config(page_title="OptionMoney AI", page_icon="📈",
                   layout="wide", initial_sidebar_state="expanded")

# one-time housekeeping per session
if "oi_purged" not in st.session_state:
    db.purge_oi(keep_days=2)
    db.purge_predictions(keep_days=2)
    st.session_state.oi_purged = True
if broker.is_connected is False and "auto_login_tried" not in st.session_state:
    st.session_state.auto_login_tried = True
    broker.login()   # try auto-connect from secrets/.env on first load

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
    try:
        df, spot = option_chain_fetcher.fetch(name)
    except Exception:
        df, spot = pd.DataFrame(), 0.0
    return df, spot, option_chain_fetcher.last_expiry.get(name, "")


@st.cache_data(ttl=30, show_spinner=False)   # live ticker refresh
def fetch_global():
    return global_quotes()


@st.cache_data(ttl=300, show_spinner=False)
def fetch_history_long(name: str) -> pd.DataFrame:
    """Multi-year 5-min history (cache file backfills 2 chunks per call)."""
    try:
        return get_history(name, years=5, backfill_chunks=2)
    except Exception:
        return pd.DataFrame()


def _parse_expiry(s: str):
    for fmt in ("%d-%b-%Y", "%d%b%Y", "%d-%b-%y"):
        try:
            return datetime.strptime(str(s).strip(), fmt).date()
        except ValueError:
            continue
    return None


# ── Header: global ticker with open/closed dots + session clock ─────
def _market_open(group: str, now: datetime) -> bool:
    wd, t = now.weekday(), now.time()
    if group == "FUT":   # US index futures: ~23h Mon–Fri (IST view)
        return wd < 5 or (wd == 5 and t <= dtime(2, 30))
    if wd >= 5:
        return False
    if group == "IN":
        return dtime(9, 15) <= t <= dtime(15, 30)
    if group == "US":    # NYSE 09:30–16:00 ET ≈ 19:00–01:30 IST
        return t >= dtime(19, 0) or t <= dtime(1, 30)
    if group == "JP":    # Tokyo ≈ 05:45–11:30 IST
        return dtime(5, 45) <= t <= dtime(11, 30)
    if group == "HK":    # Hong Kong ≈ 06:45–13:30 IST
        return dtime(6, 45) <= t <= dtime(13, 30)
    return False


def render_header() -> None:
    quotes = fetch_global()
    now = datetime.now(IST)
    items = list(quotes.items())
    cols = st.columns(len(items))               # all tickers on one line
    for col, (name, q) in zip(cols, items):
        dot = "🟢" if _market_open(MARKET_GROUP.get(name, ""), now) else "🔴"
        if q:
            arrow = "🔺" if q["chg_pct"] >= 0 else "🔻"
            col.markdown(
                f"<div style='font-size:11px;line-height:1.25'>"
                f"{dot} <b>{name}</b><br>"
                f"<span style='font-size:15px'>{q['price']:,.0f}</span><br>"
                f"<span style='color:{'#1a9850' if q['chg_pct'] >= 0 else '#d73027'}'>"
                f"{arrow} {q['chg_pct']:+.2f}%</span></div>",
                unsafe_allow_html=True)
        else:
            col.markdown(f"<div style='font-size:11px'>{dot} <b>{name}</b><br>—"
                         "</div>", unsafe_allow_html=True)

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
    if _market_open("JP", now):
        live_now.append("Asia (Nikkei)")
    if _market_open("HK", now):
        live_now.append("Hong Kong")
    if _market_open("US", now):
        live_now.append("US (Dow/Nasdaq)")
    if not live_now and _market_open("FUT", now):
        live_now.append("US futures")
    extra = f" · Abhi live: **{', '.join(live_now)}**" if live_now else ""
    st.markdown(f"🕒 **{now.strftime('%d %b %Y, %H:%M:%S IST')}** · "
                f"NSE: **{nse}**{extra}")


# ── OI flow with persistent history ─────────────────────────────────
def _store_oi_snapshot(name: str, chain: pd.DataFrame, spot: float) -> None:
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


def _window_delta(rows: list[dict], minutes: int):
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
                   expiry: str) -> str:
    """Multi-window OI delta table + overall verdict. Returns the OI bias."""
    st.subheader("📡 OI delta by window — trend direction")
    if chain.empty:
        st.info("Option chain unavailable — connect the broker (sidebar). "
                "SENSEX/SBIN chains load from Angel One quotes once connected.")
        return "—"

    market_open = _market_open("IN", datetime.now(IST))
    st.caption(f"Current expiry: **{expiry or '—'}** · spot {spot:,.1f}"
               + ("" if market_open
                  else " · 🌙 market offline — intraday windows fill in once "
                       "live OI starts moving"))

    _store_oi_snapshot(name, chain, spot)
    since = (datetime.now() - timedelta(hours=30)).isoformat()
    rows = db.oi_history(name, since)

    # Build a row per window: ΔCE, ΔPE, net, trend
    table, votes = [], []
    for win in OI_WINDOWS:
        delta = _window_delta(rows, win)
        if delta is None:
            table.append({"Window": f"{win} min", "CE ΔOI": "—",
                          "PE ΔOI": "—", "Net (PE−CE)": "—",
                          "Trend": "⏳ collecting"})
            continue
        dce, dpe = delta
        trend, dot = _oi_trend(dce, dpe)
        votes.append((win, trend))
        table.append({
            "Window": f"{win} min",
            "CE ΔOI": f"{dce:+,.0f}",
            "PE ΔOI": f"{dpe:+,.0f}",
            "Net (PE−CE)": f"{(dpe - dce):+,.0f}",
            "Trend": f"{dot} {trend.split(' — ')[0]}",
        })
    st.dataframe(pd.DataFrame(table), use_container_width=True, hide_index=True)

    # Day fallback row (always available from NSE change-in-OI)
    day_dce = float(chain["ce_chg_oi"].sum())
    day_dpe = float(chain["pe_chg_oi"].sum())
    day_trend, day_dot = _oi_trend(day_dce, day_dpe)
    st.caption(f"Whole-day OI change → CE {day_dce:+,.0f} · PE {day_dpe:+,.0f} "
               f"→ {day_dot} {day_trend}")

    # Overall verdict (weight longer windows more; fall back to day)
    if votes:
        score = 0.0
        for win, trend in votes:
            w = win        # longer window = more weight
            if trend.startswith("BULLISH"):
                score += w
            elif trend.startswith("BEARISH"):
                score -= w
            elif trend.startswith("WIND-UP"):
                score -= w * 0.3
        bias = ("BULLISH" if score > 0 else "BEARISH" if score < 0 else "SIDEWAYS")
        dot = "🟢" if score > 0 else "🔴" if score < 0 else "⚪"
        bull_n = sum(1 for _, t in votes if t.startswith("BULLISH"))
        bear_n = sum(1 for _, t in votes if t.startswith("BEARISH"))
        st.markdown(f"### {dot} Overall OI trend: **{bias}** "
                    f"({bull_n} bullish / {bear_n} bearish across "
                    f"{len(votes)} windows)")
    else:
        bias = day_trend.split(" — ")[0]
        st.markdown(f"### {day_dot} Overall (last-session OI): **{bias}**")
        st.caption("Per-minute windows fill in automatically while the app "
                   "runs during market hours (snapshots saved every minute).")

    if rows and len(rows) >= 2:
        hist_df = pd.DataFrame(rows)
        hist_df["time"] = pd.to_datetime(hist_df["ts"]).dt.strftime("%H:%M")
        chart_df = hist_df.set_index("time")[["ce_oi", "pe_oi"]]
        chart_df.columns = ["CE total OI", "PE total OI"]
        st.line_chart(chart_df, height=180)

    if market_open and datetime.now(IST).time() >= dtime(14, 45):
        st.warning("⏰ **Closing hour:** writers typically square off now — "
                   "expect unwinding and fast premium decay. Prefer exits "
                   "over fresh option buying.")
    return bias


# ── Pattern matcher panel ───────────────────────────────────────────
def render_pattern_match(name: str, expiry: str) -> None:
    st.subheader("🔮 History pattern match (analog days)")
    hist = fetch_history_long(name)
    if hist.empty:
        st.info("Needs the broker connected — history cache builds "
                "automatically once connected (target ~5 years of 5-min data).")
        return

    days = coverage_days(hist)
    expiry_weekday = None
    mode_note = f"history cache: **{days} trading days** (target ~5 years, backfills automatically)"
    exp_date = _parse_expiry(expiry)
    if exp_date and exp_date == datetime.now(IST).date():
        expiry_weekday = exp_date.weekday()
        mode_note = ("**EXPIRY DAY** — matching only past expiry-weekday days · "
                     + mode_note)

    result = find_analog_days(hist, top_n=3, expiry_weekday=expiry_weekday)
    if not result.get("available"):
        st.info(result.get("reason", "No match available yet."))
        st.caption(mode_note)
        return

    st.caption(f"Today so far: **{result['today_move_pct']:+.2f}%** · {mode_note}")
    for m in result["matches"]:
        match_day = pd.Timestamp(m["date"]).strftime("%a %d %b %Y")
        st.markdown(
            f"• **{match_day}** — similarity {m['similarity']}% · that day was "
            f"{m['move_so_far_pct']:+.2f}% at this point, then moved "
            f"**{m['rest_of_day_pct']:+.2f}%** till close "
            f"(day total {m['day_close_pct']:+.2f}%)")
    bias_colour = ("🟢" if "BULL" in result["bias"]
                   else "🔴" if "BEAR" in result["bias"] else "⚪")
    st.markdown(f"{bias_colour} **Historical bias:** {result['bias']} "
                f"(avg {result['avg_rest_of_day_pct']:+.2f}% rest-of-day "
                f"across matches)")


# ── Per-instrument tab ──────────────────────────────────────────────
def _resample(df5: pd.DataFrame, tf: str) -> pd.DataFrame:
    """Resample cached 5-min candles to the selected timeframe."""
    if tf in ("1min", "5min") or df5.empty:
        return df5
    return (df5.resample(tf)
            .agg({"open": "first", "high": "max", "low": "min",
                  "close": "last", "volume": "sum"})
            .dropna())


def get_chart_df(name: str, tf: str) -> tuple[pd.DataFrame, str]:
    """Live candles first; fall back to the local history cache file."""
    df = fetch_candles(name, tf)
    if not df.empty:
        if tf == "5min":          # persist live 5-min candles for resilience
            try:
                from backend.data.history_store import merge_and_save
                merge_and_save(name, df)
            except Exception:
                pass
        return df, "live"
    hist = fetch_history_long(name)
    if hist.empty:
        hist = load_cached(name)
    if hist.empty:
        return pd.DataFrame(), "none"
    return _resample(hist, tf).tail(400), "cache"


def render_oi_table(name: str, chain: pd.DataFrame, spot: float) -> None:
    """ATM ±5 strikes OI table — always available when a chain exists."""
    st.subheader("🎯 Option chain — ATM ±5 strikes")
    if chain.empty or not spot:
        st.info("Chain data unavailable — connect the broker (sidebar).")
        return
    pos = chain.index.get_loc((chain["strike"] - spot).abs().idxmin())
    atm = float(chain.iloc[pos]["strike"])
    sub = chain.iloc[max(0, pos - 5):pos + 6][
        ["ce_oi", "ce_chg_oi", "ce_ltp", "strike",
         "pe_ltp", "pe_chg_oi", "pe_oi"]].copy()
    ce_sum, pe_sum = sub["ce_oi"].sum(), sub["pe_oi"].sum()
    for c in ("ce_oi", "ce_chg_oi", "pe_chg_oi", "pe_oi"):
        sub[c] = sub[c].map(lambda v: f"{v:+,.0f}" if "chg" in c
                            else f"{v:,.0f}")
    sub["strike"] = sub["strike"].map(
        lambda s: f"⭐ {s:,.0f}" if s == atm else f"{s:,.0f}")
    sub.columns = ["CE OI", "CE ΔOI", "CE LTP", "Strike",
                   "PE LTP", "PE ΔOI", "PE OI"]
    st.dataframe(sub, use_container_width=True, hide_index=True)
    side = ("CALL side heavier → resistance above (bearish lean)"
            if ce_sum > pe_sum * 1.1
            else "PUT side heavier → support below (bullish lean)"
            if pe_sum > ce_sum * 1.1 else "balanced")
    st.caption(f"±5 strikes total — CE OI **{ce_sum:,.0f}** vs PE OI "
               f"**{pe_sum:,.0f}** → {side}")


def render_chain_panels(name: str, chain: pd.DataFrame,
                        chain_spot: float) -> None:
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


def _save_dashboard_signal(name: str, tf: str, rec) -> None:
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


def _save_prediction(name: str, tf: str, rec, pattern: dict,
                     oi_bias: str) -> None:
    """Persist a timestamped prediction snapshot (throttled, kept daily).

    Saves when the action/pattern changes, or at most once every 3 min,
    so the daily log captures every meaningful shift without flooding.
    """
    key = f"pred_{name}_{tf}"
    last = st.session_state.get(key, {})
    changed = (last.get("action") != rec.action
               or last.get("pattern") != pattern["pattern"])
    fresh = time.time() - last.get("ts", 0) > 180
    if not (changed or fresh):
        return
    db.insert_prediction(
        underlying=name, timeframe=tf, action=rec.action,
        option_type=rec.option_type, confidence=rec.confidence, spot=rec.spot,
        entry_price=rec.entry_price, stop_loss=rec.stop_loss,
        target1=rec.target1, target2=rec.target2,
        chart_pattern=pattern["pattern"], pattern_bias=pattern["bias"],
        oi_bias=oi_bias, reasoning="; ".join(rec.reasoning[:4]),
    )
    st.session_state[key] = {"action": rec.action,
                             "pattern": pattern["pattern"], "ts": time.time()}


def render_predictions_log(name: str) -> None:
    st.subheader("🗒️ Today's prediction history")
    preds = db.todays_predictions(name, limit=60)
    if not preds:
        st.info("No predictions logged yet today — they accumulate as the "
                "app runs (kept for the day).")
        return
    df = pd.DataFrame(preds)
    df["time"] = pd.to_datetime(df["ts"]).dt.strftime("%H:%M:%S")
    show = df[["time", "action", "confidence", "spot", "entry_price",
               "stop_loss", "target1", "chart_pattern", "oi_bias"]].copy()
    show.columns = ["Time", "Signal", "Conf%", "Spot", "Entry", "SL",
                    "T1", "Chart pattern", "OI bias"]
    st.dataframe(show, use_container_width=True, hide_index=True)


def _signal_pins(name: str, dfi: pd.DataFrame) -> pd.DataFrame:
    sigs = pd.DataFrame(db.recent_signals(300))
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
    df, source = get_chart_df(name, tf)
    chain, chain_spot, expiry = fetch_chain(name)

    if df.empty:
        if broker.is_connected:
            st.warning(f"⚠️ {name} candle API not responding (rate limit / "
                       "off-hours) — retrying every refresh. OI analysis "
                       "below still works.")
        else:
            st.error(f"❌ No {name} data — connect the broker from the "
                     "sidebar (credentials in Streamlit secrets / .env).")
        # candles missing — but render every chain-based analysis anyway
        render_oi_table(name, chain, chain_spot)
        render_oi_flow(name, chain, chain_spot, expiry)
        render_chain_panels(name, chain, chain_spot)
        render_pattern_match(name, expiry)
        render_predictions_log(name)
        return

    if source == "cache":
        st.info(f"📁 Live candle API unavailable — chart from the local "
                f"history cache (last candle {df.index.max():%d %b %H:%M}). "
                "Switches back to live automatically.")

    dfi = add_all_indicators(df)
    rec = recommendation_engine.analyse(name, df, tf, option_chain=chain,
                                        chain_spot=chain_spot)
    pattern = detect_chart_pattern(df)
    last_close = float(dfi["close"].iloc[-1])

    # ── Metrics + ONE-LINE prediction ──────────────────────────────
    c1, c2, c3, c4, c5 = st.columns(5)
    c1.metric(f"{name} spot", f"{last_close:,.1f}")
    c2.metric("Confidence", f"{rec.confidence}%")
    c3.metric("Risk:Reward", rec.risk_reward if rec.option_type else "—")
    c4.metric("Risk level", rec.risk_level)
    c5.metric("Current expiry", expiry or "—")

    now_str = datetime.now(IST).strftime("%H:%M:%S")
    emoji = ("🟢" if rec.option_type == "CE"
             else "🔴" if rec.option_type == "PE" else "⚪")
    if rec.option_type:
        st.markdown(
            f"### {emoji} **{rec.action}** · Entry(spot) **{rec.entry_price}** "
            f"| SL **{rec.stop_loss}** | T1 **{rec.target1}** "
            f"| T2 **{rec.target2}** · Confidence **{rec.confidence}%** "
            f"· [{tf}] · 🕒 {now_str}")
    else:
        top_reason = rec.reasoning[0] if rec.reasoning else ""
        st.markdown(f"### {emoji} **{rec.action}** — {top_reason} "
                    f"· [{tf}] · 🕒 {now_str}")

    # ── Chart pattern line (W / M / H&S / triangle …) ──────────────
    p_emoji = ("🟢" if pattern["bias"] == "bullish"
               else "🔴" if pattern["bias"] == "bearish" else "🔵")
    st.markdown(f"{p_emoji} **📐 Chart pattern:** {pattern['pattern']} "
                f"({pattern['status']}) — {pattern['bias']} · "
                f"{pattern['description']}")

    # ── Chart: hover price, current-price line, signal pins ────────
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

    # current price line with label
    fig.add_hline(y=last_close, line_dash="dot", line_color="gray",
                  annotation_text=f"  {last_close:,.1f}",
                  annotation_position="right", row=1, col=1)

    # past signal pins from the database
    pins = _signal_pins(name, dfi)
    if not pins.empty:
        is_ce = pins["action"].str.contains("CE")
        fig.add_trace(go.Scatter(
            x=pins["ts"], y=pins["entry_price"], mode="markers",
            name="AI signals",
            marker=dict(size=14,
                        symbol=np.where(is_ce, "triangle-up", "triangle-down"),
                        color=np.where(is_ce, "lime", "red"),
                        line=dict(width=1, color="black")),
            hovertemplate=("<b>%{customdata[0]}</b><br>%{x|%d %b %H:%M}<br>"
                           "Entry %{y:.1f} | SL %{customdata[1]} | "
                           "T1 %{customdata[2]}<br>Confidence %{customdata[3]}%"
                           "<extra></extra>"),
            customdata=pins[["action", "stop_loss", "target1",
                             "confidence"]].to_numpy(),
        ), row=1, col=1)

    # current recommendation pin (star on the latest candle)
    if rec.option_type:
        fig.add_trace(go.Scatter(
            x=[dfi.index[-1]], y=[rec.entry_price], mode="markers+text",
            name="Now", text=[rec.action], textposition="top center",
            marker=dict(size=16, symbol="star",
                        color="lime" if rec.option_type == "CE" else "red",
                        line=dict(width=1, color="black")),
            hovertemplate=(f"<b>{rec.action}</b> (current)<br>Entry "
                           f"{rec.entry_price} | SL {rec.stop_loss} | "
                           f"T1 {rec.target1}<extra></extra>"),
        ), row=1, col=1)

    fig.add_trace(go.Bar(x=dfi.index, y=dfi["volume"], name="Volume"),
                  row=2, col=1)
    fig.add_trace(go.Scatter(x=dfi.index, y=dfi["rsi"], name="RSI"),
                  row=3, col=1)
    fig.add_hline(y=70, line_dash="dot", row=3, col=1)
    fig.add_hline(y=30, line_dash="dot", row=3, col=1)

    # hide nights/weekends so candles join up like a trading terminal
    fig.update_xaxes(rangebreaks=[
        dict(bounds=["sat", "mon"]),
        dict(bounds=[15.6, 9.25], pattern="hour"),
    ])
    fig.update_layout(height=620, xaxis_rangeslider_visible=False,
                      hovermode="x unified",
                      margin=dict(l=10, r=60, t=30, b=10), showlegend=True)
    fig.update_xaxes(showspikes=True, spikemode="across", spikethickness=1)
    fig.update_yaxes(showspikes=True, spikethickness=1)
    st.plotly_chart(fig, use_container_width=True, key=f"chart_{name}")

    # ── One-click paper trade ──────────────────────────────────────
    t1, t2, t3 = st.columns([1, 1, 3])
    if t1.button(f"🟢 Buy {name} CE (paper)", key=f"buyce_{name}",
                 use_container_width=True):
        ok, msg = trade_manager.manual_buy(name, "CE", last_close,
                                           reason="One-click chart trade")
        (st.success if ok else st.error)(msg)
    if t2.button(f"🔴 Buy {name} PE (paper)", key=f"buype_{name}",
                 use_container_width=True):
        ok, msg = trade_manager.manual_buy(name, "PE", last_close,
                                           reason="One-click chart trade")
        (st.success if ok else st.error)(msg)
    t3.caption("Buys 1 ATM lot within your capital limit, with default "
               "SL/T1/T2 — manage in the Paper Trades tab.")

    with st.expander("🧠 AI reasoning", expanded=False):
        for r in rec.reasoning:
            st.write("•", r)

    render_oi_table(name, chain, chain_spot)
    oi_bias = render_oi_flow(name, chain, chain_spot, expiry)
    render_chain_panels(name, chain, chain_spot)
    render_pattern_match(name, expiry)

    # store this prediction (with pattern + OI bias) — throttled, daily
    _save_dashboard_signal(name, tf, rec)
    _save_prediction(name, tf, rec, pattern, oi_bias)
    render_predictions_log(name)


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
