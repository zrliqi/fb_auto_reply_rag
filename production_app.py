import hashlib
import hmac
import logging
import os
from concurrent.futures import ThreadPoolExecutor
from typing import Any

import requests
from dotenv import load_dotenv
from flask import Flask, jsonify, request

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

    reply = _build_reply(text)
    _send_message(
        page_access_token=cfg["page_access_token"],
        api_version=cfg["graph_api_version"],
        recipient_id=sender_id,
        text=reply,
        timeout_s=cfg["timeout_seconds"],
    )


def create_app() -> Flask:
    _setup_logging()

    app = Flask(__name__)
    cfg = {
        "verify_token": os.getenv("FB_VERIFY_TOKEN", ""),
        "page_access_token": os.getenv("FB_PAGE_ACCESS_TOKEN", ""),
        "app_secret": os.getenv("FB_APP_SECRET", ""),
        "graph_api_version": os.getenv("FB_GRAPH_API_VERSION", "v20.0"),
        "timeout_seconds": int(os.getenv("WEBHOOK_TIMEOUT_SECONDS", "10")),
        "port": int(os.getenv("PORT", "5000")),
    }
    pool = ThreadPoolExecutor(max_workers=8)

    app.extensions["cfg"] = cfg
    app.extensions["worker_pool"] = pool

    if not cfg["verify_token"]:
        logger.warning("FB_VERIFY_TOKEN is not configured.")
    if not cfg["page_access_token"]:
        logger.warning("FB_PAGE_ACCESS_TOKEN is not configured.")

    @app.get("/")
    def root():
        return jsonify({"service": "facebook-messenger-bot", "status": "running"}), 200

    @app.get("/health")
    def health():
        return jsonify({"status": "ok"}), 200

    @app.get("/webhook")
    def verify_webhook():
        mode = request.args.get("hub.mode")
        token = request.args.get("hub.verify_token")
        challenge = request.args.get("hub.challenge", "")
        if mode == "subscribe" and token == cfg["verify_token"]:
            return challenge, 200
        return "Verification failed", 403

    @app.post("/webhook")
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

    return app

