# Facebook Messenger Auto-Reply Bot (Production-Ready Flask + RAG Placeholder)

Production-oriented Messenger webhook bot with:
- Flask app factory
- `/webhook` GET verification
- `/webhook` POST event handling
- signature validation support (`X-Hub-Signature-256`)
- modular services (Facebook client, AI responder, RAG retriever)
- Gunicorn + Render deployment files
- logging and error handling

## Project structure

```text
bot_app/
  __init__.py
  config.py
  logging_config.py
  routes/
    webhook.py
  services/
    ai_service.py
    facebook_client.py
    rag_service.py
data/
  knowledge_base.txt
.env.example
Procfile
render.yaml
requirements.txt
run.py
wsgi.py
```

## Environment variables

Copy `.env.example` to `.env` and set:

```env
PORT=5000
LOG_LEVEL=INFO
FB_VERIFY_TOKEN=change_me_verify_token
FB_PAGE_ACCESS_TOKEN=change_me_page_access_token
FB_APP_SECRET=change_me_app_secret_optional
FB_GRAPH_API_VERSION=v20.0
WEBHOOK_TIMEOUT_SECONDS=10
DEFAULT_REPLY=Thanks for your message. We will get back to you shortly.
KB_FILE=data/knowledge_base.txt
RAG_TOP_K=3
```

## Local run

```bash
python -m venv .venv
# Windows
.venv\Scripts\activate
# macOS/Linux
# source .venv/bin/activate

pip install -r requirements.txt
python run.py
```

## Webhook endpoints

- `GET /webhook`: Meta verification (`hub.verify_token` check)
- `POST /webhook`: receives page messaging events and replies
- `GET /health`: health probe

## AI response placeholder

Current placeholder is in:
- `bot_app/services/ai_service.py` -> `_call_ai_model(...)`

Replace this method with your real model/provider call (OpenAI, Claude, local model, etc).

## RAG support

Current RAG is a lightweight retriever against `data/knowledge_base.txt`.
For production, replace `RAGService.retrieve(...)` with a real vector store retriever.

## Gunicorn compatibility

WSGI entrypoint:
- `wsgi.py` exposes `app`

Run manually:

```bash
gunicorn wsgi:app --bind 0.0.0.0:5000 --workers 2 --threads 4 --timeout 120
```

## Render deployment

1. Push repo to GitHub.
2. Create a new **Web Service** on Render.
3. Configure:
   - Build command: `pip install -r requirements.txt`
   - Start command: `gunicorn wsgi:app --bind 0.0.0.0:$PORT --workers 2 --threads 4 --timeout 120`
4. Add environment variables from `.env.example` in Render dashboard.
5. Deploy.
   Alternative: use `render.yaml` blueprint deployment.

After deploy:
1. In Meta Developers -> Messenger -> Webhooks:
   - Callback URL: `https://<your-render-domain>/webhook`
   - Verify token: same value as `FB_VERIFY_TOKEN`
2. Subscribe page webhook fields (`messages`, `messaging_postbacks`).
3. Subscribe your Facebook Page to the app.

## Scaling notes

- App is stateless and can run multiple instances.
- Keep long-running LLM calls out of webhook path; use queue workers for heavy AI workloads.
- Add Redis/job queue if you need retries, delayed jobs, or high throughput.
- For strict idempotency and audit trails, persist event/message IDs in a shared database.
