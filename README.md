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

## Storage

SQLite by default. Set `DATABASE_URL` to a Supabase (or any Postgres) connection string
and the same code runs on Postgres instead - Supabase *is* Postgres, so there is nothing
Supabase-specific here.

**This app never shares a schema with anything else.** Its table names - `leads`, `tasks`,
`reports`, `memories` - are exactly what a business product would also use, so every table
lives in `DB_SCHEMA` (default `tri_buddy`) and `search_path` is pinned to it on each
connection. Pointing `DATABASE_URL` at a database that also holds the Babosayee product
is therefore safe: nothing unqualified can reach another schema's tables.

Using `public` is refused outright unless `DB_ALLOW_PUBLIC_SCHEMA=1`, and the schema name
is validated against an identifier pattern before it is ever interpolated into SQL.

**Use the session pooler or a direct connection, not the transaction pooler.** In Supabase
that means the port 5432 string rather than 6543. `search_path` is passed as a connection
startup option so it survives pooling, but transaction-mode pooling can still reassign
statements between backend sessions, and anything that drops `search_path` would let
unqualified table names resolve against `public` - the exact collision the schema is there
to prevent.

On the free Supabase plan a project pauses after about 7 days of low activity, which this
bot will not hit - it queries on every message plus the daily SEO run. Free projects get
500 MB, far more than this needs.

Switching backends does not copy data. Nothing currently lives long enough on Render's
ephemeral disk for that to matter, but a Postgres switch does start empty.

A failed database init does not stop the app. A wrong `DATABASE_URL` would otherwise
take the whole service down at boot - including `/health`, leaving no way to see why - so
the error is logged, `/health` reports `database.connected: false` with the reason, and
`status` becomes `degraded`.

Tests run on SQLite. To exercise the Postgres path, point `TEST_DATABASE_URL` at a
throwaway database:

```bash
TEST_DATABASE_URL=postgresql:///some_scratch_db python -m pytest tests/test_postgres.py
```

Those tests create and drop their own `tri_buddy_test` schema and assert that nothing
lands in `public`.

## Slack setup

The bot serves Slack from the same Flask process as Telegram. HTTP Events API, not Socket
Mode: Socket Mode needs a persistent WebSocket and a always-on worker, which a free Render
instance is not.

### Endpoints

| URL | Use |
|---|---|
| `POST /slack/events` | DMs and `@mentions` (also answers Slack's URL verification) |
| `POST /slack/commands` | slash commands |

### Slash commands

| Command | Does |
|---|---|
| `/daily <text>` | save today's report |
| `/report <text>` | alias for `/daily` |
| `/status` | your last 5 reports |
| `/status @someone` | that person's last 5 reports |
| `/team` | most recent report from each teammate |
| `/ask <question>` | ask Hermes, with team reports and stored facts in context |
| `/lead Name - Business - Phone - Notes` | save a lead and post it to the lead channel |
| `/leads` | the 10 most recent leads |

A plain DM or an `@mention` goes straight to Hermes.

### Lead capture

Leads are stored in the `leads` table and announced in `SLACK_LEAD_CHANNEL`
(default `lead_hub`). Score is out of 100 and computed in code, not by the model:
business +30, asked about pricing or trial +25, shared a phone number +25, clear buying
intent +20. At 55 or above a lead is saved automatically.

A chat completion cannot save anything by itself, so capture runs as a separate step
after the reply has been sent: a keyword and phone-number pre-filter decides whether the
message is worth a second model call, that call extracts the fields as JSON, and the score
is computed from them. The phone number is always taken from the message by regex rather
than from the model, so a hallucinated number cannot be stored. Duplicate phone numbers
and names are skipped, and a Slack posting failure never loses the lead.

**The bot must be a member of the lead channel.** With only `chat:write` it cannot post to
a channel it has not joined; Slack replies `not_in_channel` and the failure is otherwise
silent. Either invite it (`/invite @Babosayee Second brain` in the channel) or add the
`chat:write.public` scope. Setting `SLACK_LEAD_CHANNEL` to the channel ID (`C0...`) avoids
name-resolution problems.

### Daily SEO report

`POST /tasks/seo-report` audits `SEO_SITE_URL` and posts the result to
`SLACK_SEO_CHANNEL` (default `babosayee_seo`). It takes the same `HEALTH_TOKEN`, as a
`token=` query parameter or an `X-Health-Token` header, and `?dry=1` returns the report
without posting.

Everything in the report is measured from the live page: status, redirects, response
time, page weight, title and meta description length, H1 count, canonical, indexability,
viewport, `html lang`, Open Graph, image alt coverage, robots.txt and sitemap.xml. Each
run is stored, so the next one can report what was fixed and what newly broke.

**It contains no rankings, traffic, impressions or keyword data**, and says so in every
post. Those need Search Console, which this bot cannot read. A report that quietly
estimated them would be worse than no report.

Render's free plan has no cron, so scheduling is external:
`.github/workflows/daily-seo.yml` calls the endpoint at 03:00 UTC (09:00 Dhaka) and needs
two repository secrets, `SERVICE_URL` and `HEALTH_TOKEN`. The call that wakes a sleeping
instance is the same one that runs the check.

### PR review

`.github/workflows/pr-review.yml` runs on every pull request (opened, synchronised,
reopened), fetches the diff with the workflow's own `GITHUB_TOKEN`, and POSTs it to
`POST /tasks/pr-review`. The service reviews it and posts to `SLACK_DEV_CHANNEL`
(default `byabosayee_devs`). Same `HEALTH_TOKEN` as the other task endpoints, and
`?dry=1` returns the review without posting.

The diff is sent *to* the service rather than fetched *by* it, so no GitHub token is
stored in Render. Reviews of the same PR are deduplicated by a concurrency group, so
pushing three times queues one review, not three. Diffs over 60k characters are truncated
and the report says so.

The review sees the diff and nothing else - not the rest of the codebase, the tests, or
CI - and the prompt forbids claiming otherwise: no invented files or line numbers, no
statements about tests passing, and uncertainty phrased as "if X is not handled in the
caller, this would ..." rather than asserted as a bug. Every post carries that caveat. A
failed review posts "nothing was checked" rather than staying silent, because silence
reads as approval. The workflow never fails the PR.

It is a first-pass reviewer on a free model, not a gate.

### Daily traffic report (GA4)

`POST /tasks/ga-report` reads the GA4 Data API and posts to `SLACK_GA_CHANNEL`
(defaults to the SEO channel). Same `HEALTH_TOKEN`, and `?dry=1` previews without posting.
The morning workflow calls it right after the SEO check, so both land together.

It reports yesterday's users, sessions, pageviews, average session, bounce rate and
conversions, the top channels and pages, and live users from the realtime API. Every
figure comes from the API; nothing is estimated, and a section that fails to load is
named as missing rather than shown as zero.

Setup:

1. In Google Cloud, create a service account and download its JSON key.
2. Enable the **Google Analytics Data API** for that project.
3. In GA4 Admin → Property Access Management, add the service account's email as a
   **Viewer**.
4. In Render set `GA4_PROPERTY_ID` (the numeric id, not `G-XXXX`) and
   `GA4_SERVICE_ACCOUNT_JSON` (the whole key file on one line).

This uses `google-auth` with the REST API rather than `google-analytics-data`, which pulls
in grpcio and protobuf - a much heavier build for no benefit here.

### Bot token scopes

`commands`, `chat:write`, `app_mentions:read`, `im:history`, `im:read`, `im:write`,
`users:read`.

### App config

1. **Slash Commands** - add each command above, Request URL `https://<host>/slack/commands`.
2. **Event Subscriptions** - Request URL `https://<host>/slack/events`. Slack sends a
   `url_verification` challenge, which the endpoint echoes back. Subscribe to bot events
   `message.im` and `app_mention`.
3. **Interactivity** - not needed; there are no buttons or modals yet.
4. Reinstall the app after changing scopes.

### Environment

`SLACK_SIGNING_SECRET` and `SLACK_BOT_TOKEN`, both set in the Render dashboard
(`sync: false` in the blueprint). Requests are rejected with 403 unless they carry a valid
Slack v0 signature within a 5 minute window, and an unset signing secret fails closed.

### The 3-second rule

Slack requires an acknowledgement within 3 seconds, and a Hermes reply takes 8-30. So every
handler acks immediately and finishes the work on a background thread, delivering the answer
through `response_url` (slash commands) or `chat.postMessage` (events). Measured ack latency
is well under a second against a deliberately slow model.

Two consequences worth knowing. Slack retries anything it believes failed, so requests
carrying `X-Slack-Retry-Num` are dropped to avoid answering twice. And the background thread
lives in the web process: if Render recycles or sleeps the instance mid-flight, that one
answer is lost. On a free instance a cold start can also swallow the first request after idle.

## Seeing images and web pages

Send the bot a photo on Telegram and it reads it; the caption becomes the question. Images
go to a separate free vision chain (`OPENROUTER_VISION_MODELS`, default
`qwen/qwen3.8-27b:free`, then gemma, then `openrouter/free`), with the same fallback
behaviour as the text chain. Images over 5MB are rejected.

Put a link in a message and the page is fetched and included as context, up to three links
per message and 12k characters per page. The bot cannot search, browse, or follow links
found inside a fetched page - it only ever sees pages someone linked explicitly.

Fetching is guarded against SSRF: loopback, private, link-local and reserved addresses are
refused before any request is made, so the bot cannot be used to read cloud metadata or
anything else inside the network. Non-HTTP schemes and non-text responses are refused too.

Fetched text and image contents are framed in the prompt as source material, never as
instructions, and the prompt tells the model to report rather than obey any directions
found inside them.

Slack image uploads are *not* supported - that needs the `files:read` scope, which the app
does not have.

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
