import logging
import os
import random
from typing import Any

from flask import Flask, jsonify, request


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

EMOJIS = ["😄", "🤖", "🎉", "😂", "🔥", "✨", "😎", "🙌"]


def _unauthorized_response():
    return jsonify({"error": "Unauthorized"}), 401


def _with_emoji(text: str) -> str:
    return f"{text} {random.choice(EMOJIS)}"


def _build_reply(message: str) -> str:
    normalized = message.lower().strip()

    if "who are you" in normalized:
        return _with_emoji("I am your chaotic local reply engine with premium dad-joke firmware.")

    if "joke" in normalized:
        return _with_emoji(random.choice(JOKES))

    if "hi" in normalized:
        return _with_emoji("Hey there! Great to hear from you.")

    return _with_emoji(f"You said '{message}' and I fully support this energy.")


@app.before_request
def validate_api_key():
    if request.path != "/process-message":
        return None

    expected_key = os.getenv("LOCAL_API_KEY")
    provided_key = request.headers.get("X-LOCAL-API-KEY", "")

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

        reply = _build_reply(message)
        logger.info("Processed message from sender_id=%s", sender_id)
        return jsonify({"reply": reply}), 200
    except Exception:
        logger.exception("Unexpected error while processing message.")
        return jsonify({"error": "Internal server error"}), 500


@app.errorhandler(404)
def not_found(_):
    return jsonify({"error": "Not found"}), 404


@app.errorhandler(405)
def method_not_allowed(_):
    return jsonify({"error": "Method not allowed"}), 405


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5001)
