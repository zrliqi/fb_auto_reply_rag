# Open Source Community Reply

Flask-based Facebook Messenger webhook app that can forward replies to a local bot (`local_fun_bot.py`) and includes:

- A homepage control panel (`/`) with a live reply tester
- Runtime settings page (`/settings`) to store ngrok URL
- Privacy policy page (`/privacy-policy`) for Meta app review
- Messenger webhook endpoints (`/webhook` GET and POST)

## Architecture

This repo uses two apps:

1. `wsgi.py` (main app, usually on Render, default port `5000`)
2. `local_fun_bot.py` (local reply engine, default port `5001`)

Flow:

1. Messenger event hits `wsgi.py` on `/webhook`
2. `wsgi.py` loads recent conversation context from SQLite
3. `wsgi.py` calls `ai_engine.py` with OpenAI as primary model
4. If OpenAI fails (quota/rate limit/timeout/5xx), `ai_engine.py` falls back to local LLaMA (Ollama)
5. Reply is saved with `model_used` and sent through Facebook Graph API
6. If stateful processing fails entirely, `wsgi.py` can still forward to `local_fun_bot.py` as a final safety net

The homepage tester (`POST /chat/reply`) uses the same forwarding logic.

## Key Files

- `wsgi.py`: main web app, webhook, homepage, settings, privacy policy
- `ai_engine.py`: OpenAI-first response generation and LLaMA fallback
- `database.py`: SQLite schema, message persistence, and context retrieval
- `local_fun_bot.py`: local message processor (`POST /process-message`)
- `.env.example`: base environment template
- `render.yaml` and `Procfile`: Render/Gunicorn deployment config

## Prerequisites

- Python 3.10+ (project currently tested with Python 3.14)
- `pip`
- Meta Developer app + Facebook Page (for real Messenger traffic)
- `ngrok` (only needed if Render should call your local machine)

## Environment Variables

Create `.env` from `.env.example`, then add these:

```env
PORT=5000
LOG_LEVEL=INFO

FB_VERIFY_TOKEN=your_verify_token
FB_PAGE_ACCESS_TOKEN=your_page_access_token
FB_APP_SECRET=your_app_secret_optional
FB_GRAPH_API_VERSION=v20.0

WEBHOOK_TIMEOUT_SECONDS=10
DEFAULT_REPLY=Thanks for your message. We will get back to you shortly.

# Primary/fallback model routing
USE_FALLBACK=true
PRIMARY_MODEL=openai
FALLBACK_MODEL=llama
CONTEXT_HISTORY_LIMIT=12

# OpenAI
OPENAI_API_BASE=https://api.openai.com/v1
OPENAI_API_KEY=your_openai_api_key
OPENAI_MODEL=gpt-4o-mini
OPENAI_REQUEST_TIMEOUT_SECONDS=20
OPENAI_FAILURE_THRESHOLD=3
OPENAI_COOLDOWN_SECONDS=300

# Local LLaMA (Ollama)
OLLAMA_API_BASE=http://127.0.0.1:11434
OLLAMA_MODEL=qwen2.5:3b
LLAMA_REQUEST_TIMEOUT_SECONDS=45
LLAMA_STREAMING=false

# Optional local_fun_bot safety net
LOCAL_API_KEY=use_the_same_secret_in_both_apps
LOCAL_FUN_BOT_URL=
USE_LOCAL_FUN_BOT_ON_RULE_BASED=true
APP_CONFIG_FILE=config.json

PRIVACY_POLICY_NAME=Open Source Community Reply Privacy Policy
PRIVACY_CONTACT_EMAIL=zrliqi9224@gmail.com
```

Notes:

- Webhook responses always try `PRIMARY_MODEL` first, then `FALLBACK_MODEL` when enabled.
- Same role-based context payload is used for both OpenAI and LLaMA providers.
- Assistant replies are persisted with `model_used` (`openai`, `llama`, or `local_bot`).
- When OpenAI fails 3 consecutive times (default), it is skipped for 5 minutes (default).
- `LOCAL_API_KEY` must match in both `wsgi.py` and `local_fun_bot.py`.
- `LOCAL_FUN_BOT_URL` is optional. If unset in `development`, `wsgi.py` auto-tries:
  - `http://127.0.0.1:5001`
  - `http://localhost:5001`
- `NGROK_BASE_URL` is saved from `/settings` into `config.json` at runtime.

## Model Experience

This is the expected runtime behavior for chat responses:

1. User sends a message (`/webhook` or homepage `POST /chat/reply`).
2. System loads recent conversation context from SQLite (`CONTEXT_HISTORY_LIMIT`).
3. System tries OpenAI first (`PRIMARY_MODEL=openai`).
4. If OpenAI fails (billing/quota, rate limit, timeout, 5xx), system switches to LLaMA (`FALLBACK_MODEL=llama`).
5. Reply is sent without interruption and saved in DB.

### What you will see

- `model_used=openai` when OpenAI succeeds.
- `model_used=llama` when OpenAI fails and LLaMA succeeds.
- `model_used=local_bot` only if the whole stateful pipeline crashes and final safety fallback is used.
- If both OpenAI and LLaMA are unavailable, `USE_LOCAL_FUN_BOT_ON_RULE_BASED=true` allows a final continuity attempt via `LOCAL_FUN_BOT_URL`.

### Verify in database

Run this query against `data/conversations.db`:

```sql
SELECT u.facebook_id, m.role, m.message_text, m.model_used, m.timestamp
FROM messages m
JOIN users u ON u.id = m.user_id
ORDER BY m.id DESC
LIMIT 20;
```

## Local Setup (Windows PowerShell)

1. Create and activate virtual environment:

```powershell
cd C:\Users\manis\PycharmProjects\fb_auto_reply_rag
python -m venv .venv
.venv\Scripts\Activate.ps1
pip install -r requirements.txt
```

2. Create `.env` and fill values:

```powershell
Copy-Item .env.example .env
```

3. Start `local_fun_bot.py` in Terminal A:

```powershell
$env:LOCAL_API_KEY="your_shared_local_api_key"
$env:FLASK_APP="local_fun_bot.py"
& "C:\Program Files\Python314\python.exe" -m flask run --host 0.0.0.0 --port 5001
```

4. Start `wsgi.py` in Terminal B:

```powershell
$env:FLASK_ENV="development"
$env:LOCAL_API_KEY="your_shared_local_api_key"
$env:FLASK_APP="wsgi.py"
& "C:\Program Files\Python314\python.exe" -m flask run --host 0.0.0.0 --port 5000
```

5. Open local URLs:

- Home: `http://127.0.0.1:5000/`
- Settings: `http://127.0.0.1:5000/settings`
- Privacy policy: `http://127.0.0.1:5000/privacy-policy`

## Run Locally Only (No Render Server)

Use these 3 terminals when you want local development only.

1. Terminal A: run `local_fun_bot.py` on port `5001`

```powershell
cd C:\Users\manis\PycharmProjects\fb_auto_reply_rag
$key = (Get-Content .env | Where-Object { $_ -match '^LOCAL_API_KEY=' } | Select-Object -First 1).Split('=',2)[1]
$env:LOCAL_API_KEY = $key
python .\local_fun_bot.py
```

2. Terminal B: run `wsgi.py` on port `5000`

```powershell
cd C:\Users\manis\PycharmProjects\fb_auto_reply_rag
$env:FLASK_ENV = "development"
python -m flask --app wsgi.py run --host 0.0.0.0 --port 5000
```

3. Terminal C (optional): expose local bot with ngrok

```powershell
ngrok http 5001
```

If you do not need external webhook traffic, skip Terminal C.

## Test the Local Bot Directly

```powershell
Invoke-RestMethod -Method Post -Uri http://127.0.0.1:5001/process-message `
  -Headers @{ "X-LOCAL-API-KEY"="your_shared_local_api_key" } `
  -ContentType "application/json" `
  -Body '{"sender_id":"123","message":"hi"}'
```

Expected: JSON with a `reply` field.

## ngrok Setup (for Render -> local_fun_bot bridge)

Run in a separate terminal:

```powershell
ngrok http 5001
```

Copy the HTTPS URL (example: `https://abcd-1234.ngrok-free.app`), then:

1. Open your deployed app settings page: `https://<your-render-domain>/settings`
2. Save that ngrok URL as `NGROK_BASE_URL`
3. Ensure Render env var `LOCAL_API_KEY` matches local bot `LOCAL_API_KEY`

## Render Deployment

1. Push repo to GitHub.
2. Create a Render Web Service.
3. Configure:
   - Build command: `pip install -r requirements.txt`
   - Start command: `gunicorn wsgi:app --bind 0.0.0.0:$PORT --workers 2 --threads 4 --timeout 120`
4. Add env vars in Render dashboard:
   - `FB_VERIFY_TOKEN`
   - `FB_PAGE_ACCESS_TOKEN`
   - `FB_APP_SECRET` (recommended)
   - `LOCAL_API_KEY`
   - `PRIVACY_CONTACT_EMAIL` (optional override)
5. Deploy.

## Production URLs (Render)

If your domain is `https://fb-auto-reply-rag.onrender.com`:

- Home: `https://fb-auto-reply-rag.onrender.com/`
- Privacy policy: `https://fb-auto-reply-rag.onrender.com/privacy-policy`
- Settings: `https://fb-auto-reply-rag.onrender.com/settings`
- Webhook: `https://fb-auto-reply-rag.onrender.com/webhook`

## Meta Messenger Configuration

In Meta Developers -> Messenger -> Webhooks:

1. Callback URL: `https://<your-render-domain>/webhook`
2. Verify Token: exact same `FB_VERIFY_TOKEN` value
3. Subscribe fields:
   - `messages`
   - `messaging_postbacks`
4. Subscribe your Facebook Page to the app

For app review:

- Privacy policy URL: `https://<your-render-domain>/privacy-policy`

## Endpoints

- `GET /`: homepage menu + live tester UI
- `POST /chat/reply`: homepage tester reply API
- `GET /settings`: ngrok URL settings page
- `POST /settings`: save ngrok base URL
- `GET /privacy-policy`: privacy policy page
- `GET /privacy`: privacy alias
- `GET /health`: health check
- `GET /webhook`: Meta verify token callback
- `POST /webhook`: Messenger event receiver
- `POST /process-message` (in `local_fun_bot.py`): local reply endpoint

## Troubleshooting

1. `401 Unauthorized` on `/process-message`
   - `LOCAL_API_KEY` missing in local bot process, or key mismatch.
   - Restart both apps after changing env vars.

2. Homepage tester shows echo (`I received: ...`) instead of fun replies
   - `local_fun_bot.py` is not reachable.
   - Start local bot on `5001` or set `LOCAL_FUN_BOT_URL`.

3. ngrok shows `401` for `/process-message`
   - Render `LOCAL_API_KEY` does not match local bot `LOCAL_API_KEY`.

4. Meta webhook verification returns `403`
   - `FB_VERIFY_TOKEN` in Render does not match token entered in Meta console.

5. Messages received but no reply in Messenger
   - Check `FB_PAGE_ACCESS_TOKEN`.
   - Check app logs for Graph API errors.

## Security Notes

- Never commit real access tokens or API keys.
- Rotate any secret that was exposed.
- Use strong random values for `LOCAL_API_KEY`.
