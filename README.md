# tri-buddy-agent

Phase 1 scaffold for a lightweight bot service.

## Files
- bot.py: Flask app exposing health and message endpoints.
- telegram_client.py: small helper for talking to the Telegram Bot API.
- requirements.txt: Python dependencies.

## Local run
```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
python3 bot.py
```

## Endpoints
- GET / -> service status
- GET /health -> checks the configured Telegram connection
- POST /message -> forwards a message to Telegram

## Telegram setup
1. Create a bot with [@BotFather](https://t.me/BotFather) and copy the bot token into `TELEGRAM_BOT_TOKEN`.
2. Send the bot a message, then call `https://api.telegram.org/bot<TOKEN>/getUpdates` to find your `chat.id` and set `TELEGRAM_CHAT_ID`.
3. For the production engine (`app.main:app`), point Telegram's webhook at `https://<your-host>/api/webhook/telegram`:
   ```bash
   curl -X POST "https://api.telegram.org/bot<TOKEN>/setWebhook" \
     -d "url=https://<your-host>/api/webhook/telegram" \
     -d "secret_token=<TELEGRAM_WEBHOOK_SECRET>"
   ```
   `secret_token` is optional but recommended; set the same value in `TELEGRAM_WEBHOOK_SECRET`.
