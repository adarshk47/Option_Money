# Deploying the Streamlit dashboard

## Local

```bash
streamlit run streamlit_app/app.py
# opens http://localhost:8501 — login with API_SECRET_KEY
```

## Streamlit Community Cloud

1. Push this repo to GitHub (keep `.env` out — it's git-ignored).
2. Go to https://share.streamlit.io → **New app**
   - Repository: `adarshk47/Option_Money`
   - Branch: your branch
   - Main file path: `streamlit_app/app.py`
3. In **App → Settings → Secrets**, add:

```toml
API_SECRET_KEY = "your_dashboard_login_key"
ANGEL_API_KEY = "..."
ANGEL_CLIENT_ID = "..."
ANGEL_PASSWORD = "..."
ANGEL_TOTP_SECRET = "..."
TRADING_MODE = "PAPER"
```

The app reads `st.secrets` first and falls back to `.env`, so the same code
runs locally and on the cloud.

### Cloud limitations to know
- Community Cloud sleeps idle apps and gives no persistent disk — the SQLite
  DB resets on redeploys. For persistent history run the engine on a VPS and
  let the cloud dashboard be a read-only view (or use PostgreSQL).
- Outbound websockets work, but the always-on live engine belongs on a VPS,
  not inside Streamlit's request lifecycle.
- NSE may throttle requests from cloud IPs; the option-chain fetcher caches
  and degrades gracefully when that happens.

## Self-hosted (recommended for serious use)

```bash
nohup streamlit run streamlit_app/app.py --server.port 8501 \
  --server.address 0.0.0.0 &
```
Put nginx + HTTPS in front, same as the API.
