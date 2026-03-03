from __future__ import annotations

import logging
import os
import random
from typing import Any

from dotenv import load_dotenv
from flask import Flask, jsonify, request

from ai_engine import generate_ai_reply_with_model
from database import Database
from flow_controller import determine_next_stage

load_dotenv()

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s - %(message)s",
)
logger = logging.getLogger("local_fun_bot")

app = Flask(__name__)

JOKES = [
    "Why do programmers prefer dark mode? Because light attracts bugs.",
    "I told my computer I needed a break, and now it keeps showing me snack ads.",
    "Why did the Python developer wear glasses? Because they could not C.",
    "My code does not have bugs. It develops random features.",
    "I asked AI for a joke about recursion. It said: I asked AI for a joke about recursion.",
]


def _read_bool_env(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "y", "on"}


def _read_int_env(name: str, default: int, minimum: int, maximum: int) -> int:
    raw = os.getenv(name, str(default)).strip()
    try:
        value = int(raw)
    except ValueError:
        logger.warning("Invalid integer for %s='%s'. Using default=%s.", name, raw, default)
        return default
    return max(minimum, min(value, maximum))


def _configure_local_model_routing() -> None:
    # When Render bridges to local_fun_bot, prefer local LLaMA by default.
    if not _read_bool_env("LOCAL_BOT_FORCE_LLAMA", True):
        return
    os.environ["PRIMARY_MODEL"] = os.getenv("LOCAL_BOT_PRIMARY_MODEL", "llama").strip()
    os.environ["FALLBACK_MODEL"] = os.getenv("LOCAL_BOT_FALLBACK_MODEL", "llama").strip()
    os.environ["USE_FALLBACK"] = os.getenv("LOCAL_BOT_USE_FALLBACK", "false").strip()


_configure_local_model_routing()
_CONTEXT_LIMIT = _read_int_env("CONTEXT_HISTORY_LIMIT", default=12, minimum=1, maximum=50)
_LOCAL_DB_PATH = os.getenv("LOCAL_BOT_SQLITE_DB_PATH", os.getenv("SQLITE_DB_PATH", "data/conversations.db"))
_LOCAL_DB = Database(_LOCAL_DB_PATH)
_LOCAL_DB.init_db()


def _unauthorized_response():
    return jsonify({"error": "Unauthorized"}), 401


def _build_fun_reply(message: str) -> str:
    normalized = message.lower().strip()
    if "who are you" in normalized:
        return "I am your local continuity bot. I can keep replies flowing when cloud models fail."
    if "joke" in normalized:
        return random.choice(JOKES)
    return f"You said '{message}'. I am keeping the conversation alive."


def _generate_stateful_reply(sender_id: str, message: str) -> tuple[str, str]:
    user = _LOCAL_DB.get_or_create_user(facebook_id=sender_id, initial_stage="greeting")
    user_id = int(user["id"])

    _LOCAL_DB.save_message(user_id=user_id, role="user", message_text=message)
    conversation_history = _LOCAL_DB.get_recent_messages(user_id=user_id, limit=_CONTEXT_LIMIT)

    current_stage = str(user.get("current_stage", "greeting"))
    next_stage = determine_next_stage(current_stage=current_stage, user_message=message)
    if next_stage != current_stage:
        _LOCAL_DB.set_user_stage(user_id=user_id, new_stage=next_stage)
    else:
        _LOCAL_DB.touch_user(user_id=user_id)

    reply, model_used = generate_ai_reply_with_model(
        stage=next_stage,
        conversation_history=conversation_history,
        user_message=message,
    )

    if model_used == "rule_based":
        reply = _build_fun_reply(message)
        model_used = "local_rule_based"

    _LOCAL_DB.save_message(user_id=user_id, role="assistant", message_text=reply, model_used=model_used)
    logger.info(
        "Processed message sender_id=%s model_used=%s db_path=%s",
        sender_id,
        model_used,
        _LOCAL_DB_PATH,
    )
    return reply, model_used


@app.before_request
def validate_api_key():
    if request.path != "/process-message":
        return None

    expected_key = os.getenv("LOCAL_API_KEY", "").strip()
    provided_key = request.headers.get("X-LOCAL-API-KEY", "").strip()

    if not expected_key:
        logger.error("LOCAL_API_KEY environment variable is not configured.")
        return _unauthorized_response()

    if provided_key != expected_key:
        logger.warning("Unauthorized request to /process-message.")
        return _unauthorized_response()

    return None


@app.route("/process-message", methods=["POST"])
def process_message():
    try:
        payload: Any = request.get_json(silent=True)
        if not isinstance(payload, dict):
            return jsonify({"error": "Invalid JSON body"}), 400

        sender_id = str(payload.get("sender_id", "")).strip()
        message = str(payload.get("message", "")).strip()

        if not sender_id or not message:
            return jsonify({"error": "sender_id and message are required"}), 400

        reply, model_used = _generate_stateful_reply(sender_id=sender_id, message=message)
        return jsonify({"reply": reply, "model_used": model_used}), 200
    except Exception:
        logger.exception("Unexpected error while processing message.")
        return jsonify({"error": "Internal server error"}), 500


@app.route("/health", methods=["GET"])
def health():
    return (
        jsonify(
            {
                "status": "ok",
                "service": "local_fun_bot",
                "db_path": _LOCAL_DB_PATH,
                "primary_model": os.getenv("PRIMARY_MODEL", ""),
                "fallback_model": os.getenv("FALLBACK_MODEL", ""),
                "use_fallback": os.getenv("USE_FALLBACK", ""),
            }
        ),
        200,
    )


@app.errorhandler(404)
def not_found(_):
    return jsonify({"error": "Not found"}), 404


@app.errorhandler(405)
def method_not_allowed(_):
    return jsonify({"error": "Method not allowed"}), 405


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5001)
