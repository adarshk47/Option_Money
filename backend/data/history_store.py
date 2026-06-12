"""
Multi-year intraday history store.

Keeps a local CSV per instrument (database/history_<NAME>_5min.csv) and
grows it automatically:
  • forward:  merges the latest days on every call
  • backward: backfills a few ~85-day chunks per call until ~5 years
              of 5-min candles are cached

So the pattern matcher gets deeper history every time the dashboard
runs, without ever re-downloading what's already on disk.
"""
from __future__ import annotations

import time
from datetime import datetime, timedelta

import pandas as pd

from backend.config import PROJECT_ROOT
from backend.logger import get_logger

log = get_logger(__name__)

STORE_DIR = PROJECT_ROOT / "database"
CHUNK_DAYS = 85          # Angel One allows ~100 days per 5-min request
TARGET_YEARS = 5


def _file(name: str):
    return STORE_DIR / f"history_{name}_5min.csv"


def load_cached(name: str) -> pd.DataFrame:
    f = _file(name)
    if not f.exists():
        return pd.DataFrame()
    try:
        df = pd.read_csv(f, index_col=0, parse_dates=True)
        df.index.name = "timestamp"
        return df
    except Exception:
        log.exception("Corrupt history cache for %s — rebuilding", name)
        return pd.DataFrame()


def _save(name: str, df: pd.DataFrame) -> None:
    STORE_DIR.mkdir(exist_ok=True)
    df.to_csv(_file(name))


def get_history(name: str, years: int = TARGET_YEARS,
                backfill_chunks: int = 2) -> pd.DataFrame:
    """Cached 5-min history, extended forward + backfilled gradually."""
    from backend.broker.angel_one import broker

    df = load_cached(name)
    if not broker.is_connected:
        return df

    changed = False
    # 1. extend forward with the latest week
    try:
        recent = broker.get_candles(name, "5min", days=7)
        if not recent.empty:
            df = pd.concat([df, recent])
            changed = True
    except Exception:
        log.warning("History forward-fetch failed for %s", name)

    # 2. backfill a few chunks toward the multi-year target
    target_start = datetime.now() - timedelta(days=years * 365)
    for _ in range(backfill_chunks):
        oldest = (df.index.min().to_pydatetime() if len(df)
                  else datetime.now())
        if oldest <= target_start:
            break
        to_dt = oldest - timedelta(minutes=5)
        from_dt = max(to_dt - timedelta(days=CHUNK_DAYS), target_start)
        try:
            chunk = broker.get_candles_range(name, "5min", from_dt, to_dt)
        except Exception:
            log.warning("History backfill failed for %s (%s → %s)",
                        name, from_dt.date(), to_dt.date())
            break
        if chunk.empty:        # broker has no older data — stop trying
            break
        df = pd.concat([chunk, df])
        changed = True
        time.sleep(0.4)        # stay inside API rate limits

    if changed and not df.empty:
        df = df[~df.index.duplicated(keep="last")].sort_index()
        _save(name, df)
        log.info("History cache for %s: %d rows (%s → %s)", name, len(df),
                 df.index.min().date(), df.index.max().date())
    return df


def coverage_days(df: pd.DataFrame) -> int:
    if df.empty or not isinstance(df.index, pd.DatetimeIndex):
        return 0
    return int(len(pd.unique(df.index.date)))
