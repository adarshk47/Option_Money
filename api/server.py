"""
FastAPI backend serving the Android app (and any other client).

Auth: simple bearer token derived from API_SECRET_KEY in .env.
Run:  python main.py api    (or: uvicorn api.server:app)

Endpoints:
  POST /auth/login                 -> bearer token
  GET  /market/overview            -> LTP + trend of all watchlist symbols
  GET  /market/candles/{name}      -> OHLCV + indicators
  GET  /market/option-chain/{name} -> chain + PCR/max-pain analysis
  GET  /signals/latest             -> recent AI recommendations
  GET  /signals/live/{name}        -> on-demand fresh analysis
  GET  /portfolio/pnl              -> paper/live PnL snapshot
  GET  /portfolio/trades           -> trade history
  POST /engine/start | /engine/stop
"""
from __future__ import annotations

import hashlib
import hmac

from fastapi import Depends, FastAPI, HTTPException, Security
from fastapi.middleware.cors import CORSMiddleware
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from pydantic import BaseModel

from ai_engine.recommendation_engine import recommendation_engine
from backend.broker.angel_one import broker
from backend.broker.paper_broker import paper_broker
from backend.config import settings
from backend.data.option_chain import option_chain_fetcher
from backend.logger import get_logger
from database.db import db
from engine.live_engine import live_engine

log = get_logger(__name__)

app = FastAPI(title="OptionMoney AI API", version="1.0.0")
app.add_middleware(
    CORSMiddleware, allow_origins=["*"], allow_methods=["*"],
    allow_headers=["*"],
)

_bearer = HTTPBearer(auto_error=False)


def _expected_token() -> str:
    return hashlib.sha256(settings.api_secret_key.encode()).hexdigest()


def require_auth(cred: HTTPAuthorizationCredentials = Security(_bearer)) -> None:
    if cred is None or not hmac.compare_digest(cred.credentials, _expected_token()):
        raise HTTPException(401, "Invalid or missing token")


class LoginRequest(BaseModel):
    secret_key: str


@app.post("/auth/login")
def login(body: LoginRequest) -> dict:
    if not hmac.compare_digest(body.secret_key, settings.api_secret_key):
        raise HTTPException(401, "Wrong secret key")
    return {"token": _expected_token(), "mode": settings.trading_mode}


@app.get("/market/overview", dependencies=[Depends(require_auth)])
def market_overview() -> dict:
    out = {}
    for name in settings.watchlist:
        try:
            ltp = broker.get_ltp(name) if broker.is_connected else None
        except Exception:
            ltp = None
        recs = live_engine.latest.get(name, {})
        five = recs.get("5min", {})
        out[name] = {
            "ltp": ltp,
            "action": five.get("action", "—"),
            "confidence": five.get("confidence", 0),
        }
    return out


@app.get("/market/candles/{name}", dependencies=[Depends(require_auth)])
def candles(name: str, interval: str = "5min", days: int = 2) -> list[dict]:
    name = name.upper()
    if name not in settings.watchlist:
        raise HTTPException(404, f"Unknown instrument {name}")
    df = broker.get_candles(name, interval, days=days)
    if df.empty:
        return []
    df = df.reset_index()
    df["timestamp"] = df["timestamp"].astype(str)
    return df.to_dict(orient="records")


@app.get("/market/option-chain/{name}", dependencies=[Depends(require_auth)])
def option_chain(name: str) -> dict:
    from ai_engine.option_chain_analysis import analyse_option_chain
    name = name.upper()
    chain, spot = option_chain_fetcher.fetch(name)
    analysis = analyse_option_chain(chain, spot)
    return {
        "spot": spot,
        "analysis": analysis,
        "chain": chain.to_dict(orient="records") if not chain.empty else [],
    }


@app.get("/signals/latest", dependencies=[Depends(require_auth)])
def latest_signals(limit: int = 30) -> list[dict]:
    return db.recent_signals(limit)


@app.get("/signals/live/{name}", dependencies=[Depends(require_auth)])
def live_signal(name: str, interval: str = "5min") -> dict:
    name = name.upper()
    if name not in settings.watchlist:
        raise HTTPException(404, f"Unknown instrument {name}")
    df = broker.get_candles(name, interval, days=5)
    chain, spot = option_chain_fetcher.fetch(name)
    rec = recommendation_engine.analyse(name, df, interval,
                                        option_chain=chain, chain_spot=spot)
    return rec.to_dict()


@app.get("/portfolio/pnl", dependencies=[Depends(require_auth)])
def pnl() -> dict:
    return paper_broker.snapshot()


@app.get("/portfolio/trades", dependencies=[Depends(require_auth)])
def trades(limit: int = 50) -> list[dict]:
    return db.recent_trades(limit)


@app.post("/engine/start", dependencies=[Depends(require_auth)])
def engine_start() -> dict:
    if not live_engine.running:
        live_engine.start()
    return {"running": live_engine.running}


@app.post("/engine/stop", dependencies=[Depends(require_auth)])
def engine_stop() -> dict:
    live_engine.stop()
    return {"running": live_engine.running}


@app.get("/health")
def health() -> dict:
    return {"status": "ok", "broker_connected": broker.is_connected,
            "engine_running": live_engine.running,
            "mode": settings.trading_mode}
