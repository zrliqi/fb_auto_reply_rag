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
    configured_base_url = cfg["config_store"].get_ngrok_base_url().rstrip("/")
    candidate_base_urls: list[str] = []

    if configured_base_url:
        candidate_base_urls.append(configured_base_url)

    env_base_url = os.getenv("LOCAL_FUN_BOT_URL", "").strip().rstrip("/")
    if env_base_url and env_base_url not in candidate_base_urls:
        candidate_base_urls.append(env_base_url)

    # Local development convenience: talk directly to local_fun_bot if running.
    if os.getenv("FLASK_ENV", "").strip().lower() == "development":
        for local_base in ("http://127.0.0.1:5001", "http://localhost:5001"):
            if local_base not in candidate_base_urls:
                candidate_base_urls.append(local_base)

    if not candidate_base_urls:
        logger.warning("No local bot URL configured. Using fallback reply.")
        return _build_reply(message_text)

    headers = {"Content-Type": "application/json"}
    if cfg["local_api_key"]:
        headers["X-LOCAL-API-KEY"] = cfg["local_api_key"]

    for base_url in candidate_base_urls:
        endpoint = f"{base_url}/process-message"
        timeout_seconds = cfg["timeout_seconds"]
        if base_url != configured_base_url:
            timeout_seconds = min(timeout_seconds, 2)

        try:
            resp = requests.post(
                endpoint,
                json={"sender_id": sender_id, "message": message_text},
                headers=headers,
                timeout=timeout_seconds,
            )
            if resp.status_code != 200:
                logger.warning(
                    "Local bot call failed: status=%s endpoint=%s body=%s",
                    resp.status_code,
                    endpoint,
                    resp.text,
                )
                continue

            payload = resp.json()
            reply = str(payload.get("reply", "")).strip()
            if reply:
                return reply
        except (requests.RequestException, ValueError):
            logger.warning("Local bot request failed: endpoint=%s", endpoint)

    logger.warning("No reachable local bot endpoint. Using fallback reply.")
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
        html = """
        <!DOCTYPE html>
        <html lang="en">
        <head>
            <meta charset="UTF-8" />
            <meta name="viewport" content="width=device-width, initial-scale=1.0" />
            <title>Open Source Community Reply</title>
            <style>
                @import url('https://fonts.googleapis.com/css2?family=Fraunces:wght@500;700&family=Manrope:wght@400;600;700&display=swap');
                :root {
                    --bg-top: #fdf4e7;
                    --bg-bottom: #e8f4f5;
                    --ink: #10232c;
                    --muted: #365161;
                    --card: rgba(255, 255, 255, 0.82);
                    --line: rgba(16, 35, 44, 0.14);
                    --accent: #e97f2f;
                    --accent-dark: #c6651c;
                    --teal: #0f8e8a;
                }
                * { box-sizing: border-box; }
                body {
                    margin: 0;
                    min-height: 100vh;
                    font-family: "Manrope", "Segoe UI", sans-serif;
                    color: var(--ink);
                    background:
                        radial-gradient(circle at 8% 8%, rgba(233, 127, 47, 0.20) 0, rgba(233, 127, 47, 0) 34%),
                        radial-gradient(circle at 92% 14%, rgba(15, 142, 138, 0.18) 0, rgba(15, 142, 138, 0) 36%),
                        linear-gradient(145deg, var(--bg-top) 0%, var(--bg-bottom) 100%);
                    padding: 28px 18px;
                    display: grid;
                    place-items: center;
                }
                .shell {
                    width: min(980px, 100%);
                    background: var(--card);
                    border: 1px solid var(--line);
                    border-radius: 24px;
                    backdrop-filter: blur(6px);
                    box-shadow: 0 14px 40px rgba(16, 35, 44, 0.12);
                    padding: 34px 30px;
                    animation: rise 650ms ease-out;
                }
                .eyebrow {
                    display: inline-block;
                    border: 1px solid rgba(15, 142, 138, 0.3);
                    color: var(--teal);
                    font-size: 12px;
                    letter-spacing: 0.08em;
                    text-transform: uppercase;
                    font-weight: 700;
                    border-radius: 999px;
                    padding: 6px 10px;
                    background: rgba(15, 142, 138, 0.09);
                }
                h1 {
                    font-family: "Fraunces", Georgia, serif;
                    font-size: clamp(28px, 5vw, 52px);
                    line-height: 1.06;
                    margin: 14px 0 8px;
                    letter-spacing: -0.02em;
                }
                .subtitle {
                    max-width: 62ch;
                    color: var(--muted);
                    font-size: 16px;
                    margin: 0 0 22px;
                }
                .status {
                    display: inline-flex;
                    align-items: center;
                    gap: 8px;
                    font-weight: 700;
                    font-size: 14px;
                    border-radius: 999px;
                    padding: 7px 12px;
                    color: #0f6c3a;
                    background: rgba(69, 179, 107, 0.12);
                    border: 1px solid rgba(69, 179, 107, 0.34);
                }
                .status::before {
                    content: "";
                    width: 8px;
                    height: 8px;
                    border-radius: 999px;
                    background: #22a850;
                    box-shadow: 0 0 0 6px rgba(34, 168, 80, 0.18);
                }
                .menu {
                    margin-top: 24px;
                    display: grid;
                    grid-template-columns: repeat(auto-fit, minmax(190px, 1fr));
                    gap: 14px;
                }
                .menu a {
                    position: relative;
                    overflow: hidden;
                    text-decoration: none;
                    color: var(--ink);
                    border: 1px solid var(--line);
                    border-radius: 16px;
                    background: rgba(255, 255, 255, 0.86);
                    padding: 18px 16px;
                    font-weight: 700;
                    transition: transform 180ms ease, border-color 180ms ease, box-shadow 180ms ease;
                    animation: rise 620ms ease-out both;
                }
                .menu a:nth-child(1) { animation-delay: 120ms; }
                .menu a:nth-child(2) { animation-delay: 200ms; }
                .menu a:nth-child(3) { animation-delay: 280ms; }
                .menu a::after {
                    content: "";
                    position: absolute;
                    right: -28px;
                    bottom: -36px;
                    width: 90px;
                    height: 90px;
                    border-radius: 50%;
                    background: radial-gradient(circle, rgba(233, 127, 47, 0.30) 0%, rgba(233, 127, 47, 0) 72%);
                }
                .menu a:hover {
                    transform: translateY(-2px);
                    border-color: rgba(233, 127, 47, 0.42);
                    box-shadow: 0 10px 24px rgba(16, 35, 44, 0.13);
                }
                .menu a span {
                    display: block;
                    font-size: 12px;
                    margin-top: 8px;
                    font-weight: 600;
                    color: var(--muted);
                }
                .footer {
                    margin-top: 22px;
                    color: var(--muted);
                    font-size: 13px;
                }
                .chat {
                    margin-top: 22px;
                    border: 1px solid var(--line);
                    border-radius: 16px;
                    background: rgba(255, 255, 255, 0.8);
                    padding: 16px;
                }
                .chat h2 {
                    font-family: "Fraunces", Georgia, serif;
                    font-size: clamp(20px, 3.5vw, 30px);
                    margin: 0 0 6px;
                }
                .chat-tip {
                    margin: 0 0 12px;
                    color: var(--muted);
                    font-size: 14px;
                }
                .chat-log {
                    border: 1px solid var(--line);
                    border-radius: 12px;
                    background: rgba(253, 253, 253, 0.95);
                    height: 260px;
                    overflow-y: auto;
                    padding: 12px;
                    display: flex;
                    flex-direction: column;
                    gap: 10px;
                }
                .bubble {
                    max-width: 78%;
                    padding: 10px 12px;
                    border-radius: 12px;
                    font-size: 14px;
                    line-height: 1.4;
                    word-break: break-word;
                }
                .bubble.user {
                    align-self: flex-end;
                    border: 1px solid rgba(233, 127, 47, 0.35);
                    background: rgba(233, 127, 47, 0.12);
                }
                .bubble.bot {
                    align-self: flex-start;
                    border: 1px solid rgba(16, 35, 44, 0.14);
                    background: rgba(15, 142, 138, 0.08);
                }
                .chat-form {
                    margin-top: 12px;
                    display: grid;
                    grid-template-columns: 1fr auto;
                    gap: 10px;
                }
                .chat-form input {
                    border: 1px solid var(--line);
                    border-radius: 10px;
                    padding: 11px 12px;
                    font-size: 14px;
                    font-family: inherit;
                    outline: none;
                }
                .chat-form input:focus {
                    border-color: rgba(15, 142, 138, 0.6);
                    box-shadow: 0 0 0 3px rgba(15, 142, 138, 0.16);
                }
                .chat-form button {
                    border: 1px solid transparent;
                    border-radius: 10px;
                    padding: 11px 16px;
                    font-weight: 700;
                    font-size: 14px;
                    font-family: inherit;
                    color: #fff;
                    background: linear-gradient(135deg, var(--accent), var(--accent-dark));
                    cursor: pointer;
                }
                .chat-form button:disabled {
                    opacity: 0.65;
                    cursor: not-allowed;
                }
                @keyframes rise {
                    from { opacity: 0; transform: translateY(12px); }
                    to { opacity: 1; transform: translateY(0); }
                }
                @media (max-width: 640px) {
                    body { padding: 16px; }
                    .shell { padding: 22px 18px; border-radius: 18px; }
                    .chat-log { height: 220px; }
                    .bubble { max-width: 90%; }
                    .chat-form { grid-template-columns: 1fr; }
                }
            </style>
        </head>
        <body>
            <div class="shell">
                <span class="eyebrow">Messenger Automation</span>
                <h1>Open Source Community Reply</h1>
                <p class="subtitle">Manage your bot settings, publish your privacy policy, and verify service health from one clean control surface.</p>
                <p class="status">Service status: running</p>
                <div class="menu">
                    <a href="{{ url_for('settings_page') }}">Settings<span>Update NGROK base URL and forwarding target.</span></a>
                    <a href="{{ url_for('privacy_policy') }}">Privacy Policy<span>Public page for Messenger app review submission.</span></a>
                    <a href="{{ url_for('health') }}">Health<span>Quick status endpoint for uptime checks.</span></a>
                </div>
                <section class="chat" id="live-reply">
                    <h2>Live Reply Tester</h2>
                    <p class="chat-tip">Send a test message from this page and see the bot response instantly.</p>
                    <div class="chat-log" id="chat-log" aria-live="polite">
                        <div class="bubble bot">Ready. Ask me anything to verify your reply pipeline.</div>
                    </div>
                    <form class="chat-form" id="chat-form">
                        <input id="chat-input" type="text" maxlength="500" placeholder="Type a message..." required />
                        <button id="chat-send" type="submit">Send</button>
                    </form>
                </section>
                <p class="footer">Built for local + Render workflow.</p>
            </div>
            <script>
                (function () {
                    const form = document.getElementById("chat-form");
                    const input = document.getElementById("chat-input");
                    const log = document.getElementById("chat-log");
                    const sendButton = document.getElementById("chat-send");
                    const endpoint = "{{ url_for('chat_reply') }}";

                    function appendBubble(role, text) {
                        const bubble = document.createElement("div");
                        bubble.className = "bubble " + role;
                        bubble.textContent = text;
                        log.appendChild(bubble);
                        log.scrollTop = log.scrollHeight;
                    }

                    form.addEventListener("submit", async function (event) {
                        event.preventDefault();
                        const message = input.value.trim();
                        if (!message) {
                            return;
                        }

                        appendBubble("user", message);
                        input.value = "";
                        sendButton.disabled = true;
                        sendButton.textContent = "Sending...";

                        try {
                            const response = await fetch(endpoint, {
                                method: "POST",
                                headers: { "Content-Type": "application/json" },
                                body: JSON.stringify({
                                    sender_id: "homepage-user",
                                    message: message
                                })
                            });
                            const data = await response.json().catch(function () { return {}; });
                            if (!response.ok) {
                                throw new Error(data.error || "Failed to get reply.");
                            }
                            appendBubble("bot", data.reply || "No reply from bot.");
                        } catch (error) {
                            appendBubble("bot", "Error: " + (error.message || "Unknown error"));
                        } finally {
                            sendButton.disabled = false;
                            sendButton.textContent = "Send";
                            input.focus();
                        }
                    });
                })();
            </script>
        </body>
        </html>
        """
        return render_template_string(html), 200

    @flask_app.get("/health")
    def health():
        return jsonify({"status": "ok"}), 200

    @flask_app.post("/chat/reply")
    def chat_reply():
        payload = request.get_json(silent=True) or {}
        message = str(payload.get("message", "")).strip()
        sender_id = str(payload.get("sender_id", "homepage-user")).strip() or "homepage-user"

        if not message:
            return jsonify({"error": "message is required"}), 400

        try:
            reply = _forward_to_local_bot(sender_id=sender_id, message_text=message, cfg=cfg)
            return jsonify({"reply": reply}), 200
        except Exception:
            logger.exception("Homepage chat reply failed.")
            return jsonify({"error": "Internal server error"}), 500

    @flask_app.get("/privacy-policy")
    @flask_app.get("/privacy")
    def privacy_policy():
        policy_name = os.getenv("PRIVACY_POLICY_NAME", "Open Source Community Reply Privacy Policy")
        contact_email = os.getenv("PRIVACY_CONTACT_EMAIL", "zrliqi9224@gmail.com")
        html = """
        <!DOCTYPE html>
        <html lang="en">
        <head>
            <meta charset="UTF-8" />
            <meta name="viewport" content="width=device-width, initial-scale=1.0" />
            <title>{{ policy_name }}</title>
            <style>
                body { font-family: Arial, sans-serif; max-width: 820px; margin: 32px auto; padding: 0 16px; line-height: 1.5; color: #111; }
                h1, h2 { line-height: 1.25; }
                .muted { color: #555; }
                .card { border: 1px solid #ddd; border-radius: 8px; padding: 20px; }
                .nav { margin-bottom: 14px; }
                .nav a { color: #111; margin-right: 14px; text-decoration: none; }
                .nav a:hover { text-decoration: underline; }
                code { background: #f5f5f5; padding: 2px 6px; border-radius: 4px; }
            </style>
        </head>
        <body>
            <div class="nav">
                <a href="{{ url_for('root') }}">Home</a>
                <a href="{{ url_for('settings_page') }}">Settings</a>
                <a href="{{ url_for('privacy_policy') }}">Privacy Policy</a>
            </div>
            <div class="card">
                <h1>{{ policy_name }}</h1>
                <p class="muted">Last updated: March 3, 2026</p>

                <h2>1. What we collect</h2>
                <p>When you message our Facebook Page, we process your Messenger sender ID and message content so the bot can generate a reply.</p>

                <h2>2. How we use data</h2>
                <p>We use this data only to provide automated replies and to operate, monitor, and improve the bot service.</p>

                <h2>3. Data sharing</h2>
                <p>We do not sell personal data. Data may be processed by service providers required to run this bot, such as Meta and our hosting infrastructure.</p>

                <h2>4. Data retention</h2>
                <p>We keep data only as long as needed for bot operations, reliability, and legal obligations. We aim to minimize stored data.</p>

                <h2>5. Data deletion requests</h2>
                <p>To request deletion of your data, email <a href="mailto:{{ contact_email }}">{{ contact_email }}</a> with your Page conversation details so we can locate the records.</p>

                <h2>6. Contact</h2>
                <p>Privacy questions can be sent to <a href="mailto:{{ contact_email }}">{{ contact_email }}</a>.</p>
            </div>
        </body>
        </html>
        """
        return render_template_string(html, policy_name=policy_name, contact_email=contact_email), 200

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
            <title>Open Source Community Reply Settings</title>
            <style>
                body { font-family: Arial, sans-serif; max-width: 760px; margin: 40px auto; padding: 0 16px; }
                .card { border: 1px solid #ddd; border-radius: 8px; padding: 20px; }
                .nav { margin-bottom: 14px; }
                .nav a { color: #111; margin-right: 14px; text-decoration: none; }
                .nav a:hover { text-decoration: underline; }
                input { width: 100%; padding: 10px; margin: 8px 0 14px; box-sizing: border-box; }
                button { padding: 10px 16px; }
                .ok { color: #0a7a31; }
                .err { color: #b42318; }
                .hint { color: #555; font-size: 14px; }
            </style>
        </head>
        <body>
            <div class="nav">
                <a href="{{ url_for('root') }}">Home</a>
                <a href="{{ url_for('settings_page') }}">Settings</a>
                <a href="{{ url_for('privacy_policy') }}">Privacy Policy</a>
            </div>
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
