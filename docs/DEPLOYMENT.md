# Deployment Guide

## 1. Local setup (all platforms)

```bash
git clone <repo-url> && cd Option_Money
python -m venv .venv
source .venv/bin/activate        # Windows: .venv\Scripts\activate
pip install -r requirements.txt
cp .env.example .env
```

Edit `.env`:
- Angel One: `ANGEL_API_KEY`, `ANGEL_CLIENT_ID`, `ANGEL_PASSWORD` (your PIN),
  `ANGEL_TOTP_SECRET` (from https://smartapi.angelbroking.com/enable-totp)
- Keep `TRADING_MODE=PAPER` until you trust the system
- Set a strong random `API_SECRET_KEY` (this is the login key for web + mobile)
- Optional: `TELEGRAM_BOT_TOKEN` (from @BotFather) + `TELEGRAM_CHAT_ID`

Sanity check:
```bash
python -c "from backend.broker.angel_one import broker; print(broker.login())"
```

## 2. Run the live engine 24×5 (server / VPS)

Any small Linux VPS (or a spare PC) works. With systemd:

```ini
# /etc/systemd/system/optionmoney.service
[Unit]
Description=OptionMoney AI engine
After=network-online.target

[Service]
WorkingDirectory=/opt/Option_Money
ExecStart=/opt/Option_Money/.venv/bin/python main.py engine
Restart=always
RestartSec=10
EnvironmentFile=/opt/Option_Money/.env

[Install]
WantedBy=multi-user.target
```

```bash
sudo systemctl enable --now optionmoney
journalctl -u optionmoney -f       # watch logs
```

Run the API for the Android app the same way with `ExecStart=... main.py api`,
then open port 8000 (or put nginx + HTTPS in front — recommended).

## 3. Telegram alerts

1. Message **@BotFather** → `/newbot` → copy the token
2. Message your new bot once, then visit
   `https://api.telegram.org/bot<TOKEN>/getUpdates` → copy `chat.id`
3. Put both in `.env` — every signal/trade/exit now lands in Telegram.

## 4. Database

SQLite is zero-config (file at `database/trading.db`). For PostgreSQL,
set `DATABASE_URL=postgresql://...` and adapt `database/db.py`'s connection
(the SQL is portable).

## 5. Security checklist

- `.env` is git-ignored — **never commit credentials**
- The FastAPI server uses bearer-token auth derived from `API_SECRET_KEY`
- Use HTTPS (nginx + certbot) if exposing the API to the internet
- Angel One TOTP secret grants trading access — treat it like a password
- Prefer a dedicated trading sub-account with limited funds for LIVE mode
