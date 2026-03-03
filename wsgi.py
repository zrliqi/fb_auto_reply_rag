import hashlib
import hmac
import json
import logging
import os
import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any

import requests
from dotenv import load_dotenv
from flask import Flask, jsonify, redirect, render_template_string, request, url_for

load_dotenv()

logger = logging.getLogger(__name__)


def _setup_logging() -> None:
    level = os.getenv("LOG_LEVEL", "INFO").upper()
    logging.basicConfig(
        level=getattr(logging, level, logging.INFO),
        format="%(asctime)s %(levelname)s %(name)s - %(message)s",
    )


def _verify_signature(raw_body: bytes, app_secret: str, signature_header: str) -> bool:
    if not app_secret:
        return True
    if not signature_header or "=" not in signature_header:
        return False

    algo, received = signature_header.split("=", 1)
    if algo.lower() != "sha256":
        return False

    expected = hmac.new(app_secret.encode("utf-8"), raw_body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, received)


def _build_reply(message_text: str) -> str:
    # AI response placeholder for production wiring.
    cleaned = (message_text or "").strip()
    if not cleaned:
        return os.getenv("DEFAULT_REPLY", "Thanks for your message.")
    return f"I received: {cleaned}"


class ConfigStore:
    """File-backed runtime settings for app-managed values such as NGROK URL."""

    def __init__(self, path: str) -> None:
        self.path = Path(path)
        self._lock = threading.Lock()

    def get_ngrok_base_url(self) -> str:
        # Environment variable remains a fallback if no saved config exists.
        data = self._read()
        return data.get("ngrok_base_url", "").strip() or os.getenv("NGROK_BASE_URL", "").strip()

    def set_ngrok_base_url(self, url: str) -> None:
        with self._lock:
            data = self._read_unlocked()
            data["ngrok_base_url"] = url.strip()
            self._write_unlocked(data)

    def _read(self) -> dict[str, str]:
        with self._lock:
            return self._read_unlocked()

    def _read_unlocked(self) -> dict[str, str]:
        if not self.path.exists():
            return {}
        try:
            with self.path.open("r", encoding="utf-8") as f:
                data = json.load(f)
            if isinstance(data, dict):
                return data
        except (OSError, json.JSONDecodeError):
            logger.exception("Failed to read config file at %s", self.path)
        return {}

    def _write_unlocked(self, data: dict[str, str]) -> None:
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self.path.with_suffix(".tmp")
            with tmp.open("w", encoding="utf-8") as f:
                json.dump(data, f, indent=2)
            tmp.replace(self.path)
        except OSError:
            logger.exception("Failed to write config file at %s", self.path)


def _forward_to_local_bot(sender_id: str, message_text: str, cfg: dict[str, Any]) -> str:
    """
    Forward user message to local bot endpoint and return its reply.
    Falls back to default reply when config is missing or call fails.
    """
    base_url = cfg["config_store"].get_ngrok_base_url()
    if not base_url:
        logger.warning("NGROK_BASE_URL is not configured. Using fallback reply.")
        return _build_reply(message_text)

    endpoint = f"{base_url.rstrip('/')}/process-message"
    headers = {"Content-Type": "application/json"}
    if cfg["local_api_key"]:
        headers["X-LOCAL-API-KEY"] = cfg["local_api_key"]

    try:
        resp = requests.post(
            endpoint,
            json={"sender_id": sender_id, "message": message_text},
            headers=headers,
            timeout=cfg["timeout_seconds"],
        )
        if resp.status_code != 200:
            logger.error(
                "Local bot call failed: status=%s endpoint=%s body=%s",
                resp.status_code,
                endpoint,
                resp.text,
            )
            return _build_reply(message_text)

        payload = resp.json()
        reply = str(payload.get("reply", "")).strip()
        return reply or _build_reply(message_text)
    except (requests.RequestException, ValueError):
        logger.exception("Local bot request failed: endpoint=%s", endpoint)
        return _build_reply(message_text)


def _send_message(page_access_token: str, api_version: str, recipient_id: str, text: str, timeout_s: int) -> None:
    url = f"https://graph.facebook.com/{api_version}/me/messages"
    params = {"access_token": page_access_token}
    payload = {
        "recipient": {"id": recipient_id},
        "message": {"text": text},
        "messaging_type": "RESPONSE",
    }
    resp = requests.post(url, params=params, json=payload, timeout=timeout_s)
    if resp.status_code != 200:
        logger.error("Facebook API send failed: status=%s body=%s", resp.status_code, resp.text)


def _process_event(event: dict[str, Any], cfg: dict[str, Any]) -> None:
    sender_id = event.get("sender", {}).get("id")
    message = event.get("message", {})
    text = message.get("text")

    if not sender_id or not text:
        return
    if message.get("is_echo"):
        return

    reply = _forward_to_local_bot(sender_id, text, cfg)
    _send_message(
        page_access_token=cfg["page_access_token"],
        api_version=cfg["graph_api_version"],
        recipient_id=sender_id,
        text=reply,
        timeout_s=cfg["timeout_seconds"],
    )


def create_app() -> Flask:
    _setup_logging()

    flask_app = Flask(__name__)
    config_store = ConfigStore(os.getenv("APP_CONFIG_FILE", "config.json"))
    cfg = {
        "verify_token": os.getenv("FB_VERIFY_TOKEN", ""),
        "page_access_token": os.getenv("FB_PAGE_ACCESS_TOKEN", ""),
        "app_secret": os.getenv("FB_APP_SECRET", ""),
        "graph_api_version": os.getenv("FB_GRAPH_API_VERSION", "v20.0"),
        "timeout_seconds": int(os.getenv("WEBHOOK_TIMEOUT_SECONDS", "10")),
        "local_api_key": os.getenv("LOCAL_API_KEY", ""),
        "config_store": config_store,
    }
    pool = ThreadPoolExecutor(max_workers=8)

    flask_app.extensions["cfg"] = cfg
    flask_app.extensions["worker_pool"] = pool

    if not cfg["verify_token"]:
        logger.warning("FB_VERIFY_TOKEN is not configured.")
    if not cfg["page_access_token"]:
        logger.warning("FB_PAGE_ACCESS_TOKEN is not configured.")

    @flask_app.get("/")
    def root():
        return jsonify({"service": "facebook-messenger-bot", "status": "running"}), 200

    @flask_app.get("/health")
    def health():
        return jsonify({"status": "ok"}), 200

    @flask_app.get("/settings")
    def settings_page():
        # Small in-app admin page for runtime ngrok forwarding configuration.
        current_url = cfg["config_store"].get_ngrok_base_url()
        status = request.args.get("status", "")
        error = request.args.get("error", "")
        html = """
        <!DOCTYPE html>
        <html lang="en">
        <head>
            <meta charset="UTF-8" />
            <meta name="viewport" content="width=device-width, initial-scale=1.0" />
            <title>Bot Settings</title>
            <style>
                body { font-family: Arial, sans-serif; max-width: 760px; margin: 40px auto; padding: 0 16px; }
                .card { border: 1px solid #ddd; border-radius: 8px; padding: 20px; }
                input { width: 100%; padding: 10px; margin: 8px 0 14px; box-sizing: border-box; }
                button { padding: 10px 16px; }
                .ok { color: #0a7a31; }
                .err { color: #b42318; }
                .hint { color: #555; font-size: 14px; }
            </style>
        </head>
        <body>
            <div class="card">
                <h2>Local Bot URL Settings</h2>
                {% if status %}<p class="ok">{{ status }}</p>{% endif %}
                {% if error %}<p class="err">{{ error }}</p>{% endif %}
                <p><strong>Current NGROK_BASE_URL:</strong> {{ current_url or "Not set" }}</p>
                <form method="post" action="{{ url_for('save_settings') }}">
                    <label for="ngrok_url">NGROK_BASE_URL</label>
                    <input
                        id="ngrok_url"
                        name="ngrok_url"
                        type="url"
                        placeholder="https://abcd-1234.ngrok-free.app"
                        value="{{ current_url }}"
                        required
                    />
                    <button type="submit">Save</button>
                </form>
                <p class="hint">Forward target: {NGROK_BASE_URL}/process-message</p>
            </div>
        </body>
        </html>
        """
        return render_template_string(html, current_url=current_url, status=status, error=error), 200

    @flask_app.post("/settings")
    def save_settings():
        # Accept and validate admin-submitted ngrok URL before persisting.
        raw_url = (request.form.get("ngrok_url", "") or "").strip()
        if not raw_url.startswith(("http://", "https://")):
            return redirect(url_for("settings_page", error="URL must start with http:// or https://"))

        normalized = raw_url.rstrip("/")
        try:
            cfg["config_store"].set_ngrok_base_url(normalized)
            logger.info("NGROK_BASE_URL updated to %s", normalized)
            return redirect(url_for("settings_page", status="NGROK_BASE_URL saved"))
        except Exception:
            logger.exception("Failed to update NGROK_BASE_URL")
            return redirect(url_for("settings_page", error="Failed to save URL"))

    @flask_app.get("/webhook")
    def verify_webhook():
        mode = request.args.get("hub.mode")
        token = request.args.get("hub.verify_token")
        challenge = request.args.get("hub.challenge", "")
        if mode == "subscribe" and token == cfg["verify_token"]:
            return challenge, 200
        return "Verification failed", 403

    @flask_app.post("/webhook")
    def webhook():
        if not cfg["page_access_token"]:
            logger.error("Cannot reply: FB_PAGE_ACCESS_TOKEN missing.")
            return "OK", 200

        raw_body = request.get_data()
        signature = request.headers.get("X-Hub-Signature-256", "")
        if not _verify_signature(raw_body, cfg["app_secret"], signature):
            return "Invalid signature", 403

        payload = request.get_json(silent=True) or {}
        if payload.get("object") != "page":
            return "Ignored", 200

        for entry in payload.get("entry", []):
            for event in entry.get("messaging", []):
                pool.submit(_process_event, event, cfg)
        return "EVENT_RECEIVED", 200

    return flask_app


app = create_app()
