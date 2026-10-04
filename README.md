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
- GET /health -> liveness plus OpenRouter status (see below)
- POST /message -> forwards a message to Telegram
- POST /api/webhook/telegram -> production Telegram webhook

### /health

Public by default and safe to expose, because it reports booleans only:

```json
{"status":"ok","openrouter":{"configured":true,"reachable":true,"key_valid":true},
 "models":{"chain_length":3,"all_free":true},"detail":"public"}
```

Set `HEALTH_TOKEN` and pass it as `?token=<...>` or an `X-Health-Token` header to also get
the key label, credit limit, remaining free requests and the resolved model chain. Those are
gated because the endpoint is reachable from the internet on Render.

The key check calls OpenRouter's `GET /key`, which validates auth **without** spending
free-model quota, so it is safe on every health check. To confirm generation end to end, add
`&probe=model` -- that spends one free request and reports which model answered, so it is
token-gated and never automatic.

`status` becomes `degraded` when the key is missing or rejected, but the HTTP code stays
**200**: the service itself is still healthy and degrades gracefully, so a non-2xx would only
make Render restart a working process. Add `?strict=1` for a monitor that wants 503 instead.

After a deploy:

```bash
curl "https://<your-host>/health?token=<HEALTH_TOKEN>&probe=model"
```

## Agent model (Hermes)

The live system prompt lives in `app/agent/prompt.py`. The agent runs **free-tier
OpenRouter models only** and replies in whichever language the user wrote in
(Bengali in -> Bengali out, English in -> English out).

Model fallback chain, tried in order (all support tool calling):

1. `nvidia/nemotron-3-ultra-550b-a55b:free` - 1M context
2. `poolside/laguna-s-2.1:free` - 262k context
3. `openrouter/free` - 200k context

`app/services/models.py` walks the chain, skipping any model that returns 402/408/429/5xx
or an empty body. If every model fails with a rate-limit status the user is told to retry
shortly; any other outage falls back to the deterministic orchestrator reply.

Configuration:

- `OPENROUTER_MODELS` - comma-separated chain. Overrides everything; the only way to use a
  paid model.
- `OPENROUTER_MODEL` - single primary prepended to the default chain. **Ignored unless it is
  a `:free` model**, so a stale paid value cannot quietly start billing the account.
- `OPENROUTER_TIMEOUT` - per-attempt timeout in seconds (default 25).

Known commands are matched *before* the model runs, so they actually write to the database:

```
Add task: <title>
Add lead: <details>
Assign <task> to <person>
Remember that my <thing> is <value>
What do you know
Draft a proposal email for <client>   ->  then: Approve <id>
```

Anything else goes to Hermes.

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
