from __future__ import annotations

import json
import logging
import os
import threading
from datetime import datetime, timedelta, timezone
from typing import Any

import requests

logger = logging.getLogger(__name__)

_STAGE_INSTRUCTIONS = {
    "greeting": "Greet the user warmly, introduce yourself briefly, and invite the user to share their goal.",
    "interest_detection": "Confirm the user interest and ask one concise qualifying question about their needs.",
    "problem_identification": "Identify the main pain point and ask a focused follow-up question to clarify context.",
    "capability_presentation": "Explain how the solution can address the user's problem with concrete benefits.",
    "payment_discussion": "Discuss pricing and payment in a clear way, and propose next actionable steps.",
    "closing": "Close politely, summarize agreed points, and provide a clear next-step or sign-off.",
}


class AIProviderError(RuntimeError):
    def __init__(self, provider: str, category: str, detail: str, status_code: int | None = None) -> None:
        super().__init__(detail)
        self.provider = provider
        self.category = category
        self.detail = detail
        self.status_code = status_code


class OpenAIHealthState:
    """Tracks temporary OpenAI disablement after repeated failures."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._consecutive_failures = 0
        self._disabled_until: datetime | None = None

    def should_skip_openai(self) -> bool:
        with self._lock:
            if self._disabled_until is None:
                return False

            now = _utc_now()
            if now >= self._disabled_until:
                self._disabled_until = None
                self._consecutive_failures = 0
                return False
            return True

    def disabled_until_iso(self) -> str:
        with self._lock:
            if self._disabled_until is None:
                return ""
            return self._disabled_until.isoformat()

    def record_success(self) -> None:
        with self._lock:
            self._consecutive_failures = 0
            self._disabled_until = None

    def record_failure(self) -> None:
        threshold = _read_int_env("OPENAI_FAILURE_THRESHOLD", default=3, minimum=1, maximum=100)
        cooldown_seconds = _read_int_env("OPENAI_COOLDOWN_SECONDS", default=300, minimum=1, maximum=86400)
        with self._lock:
            self._consecutive_failures += 1
            if self._consecutive_failures >= threshold:
                self._disabled_until = _utc_now() + timedelta(seconds=cooldown_seconds)
                logger.warning(
                    "OpenAI disabled for cooldown window: failures=%s threshold=%s disabled_until=%s",
                    self._consecutive_failures,
                    threshold,
                    self._disabled_until.isoformat(),
                )


_OPENAI_HEALTH = OpenAIHealthState()


def generate_ai_reply(stage: str, conversation_history: list[dict[str, Any]], user_message: str) -> str:
    reply, _model_used = generate_ai_reply_with_model(stage, conversation_history, user_message)
    return reply


def generate_ai_reply_with_model(
    stage: str,
    conversation_history: list[dict[str, Any]],
    user_message: str,
) -> tuple[str, str]:
    current_stage = stage if stage in _STAGE_INSTRUCTIONS else "greeting"
    history_limit = _read_int_env("CONTEXT_HISTORY_LIMIT", default=12, minimum=1, maximum=50)
    messages = _build_prompt_messages(
        stage=current_stage,
        conversation_history=conversation_history,
        user_message=user_message,
        history_limit=history_limit,
    )

    primary_model = _normalize_model_name(os.getenv("PRIMARY_MODEL", "openai"), default_model="openai")
    fallback_model = _normalize_model_name(os.getenv("FALLBACK_MODEL", "llama"), default_model="llama")
    use_fallback = _read_bool_env("USE_FALLBACK", default=True)

    # Optional advanced mode: if OpenAI recently failed repeatedly, bypass it temporarily.
    if primary_model == "openai" and use_fallback and _OPENAI_HEALTH.should_skip_openai():
        disabled_until = _OPENAI_HEALTH.disabled_until_iso()
        logger.warning(
            "Skipping OpenAI due to active cooldown and using fallback model=%s disabled_until=%s",
            fallback_model,
            disabled_until,
        )
        if fallback_model == "openai":
            logger.error("Fallback model is also OpenAI while OpenAI is in cooldown.")
            return _fallback_reply(current_stage), "rule_based"
        reply = _call_model_with_safety(fallback_model, messages)
        if reply:
            return reply, fallback_model
        return _fallback_reply(current_stage), "rule_based"

    try:
        reply = _call_model_or_raise(primary_model, messages)
        if primary_model == "openai":
            _OPENAI_HEALTH.record_success()
        return reply, primary_model
    except AIProviderError as exc:
        if primary_model == "openai":
            _OPENAI_HEALTH.record_failure()
        logger.error(
            "Primary model call failed: provider=%s category=%s status=%s detail=%s",
            exc.provider,
            exc.category,
            exc.status_code,
            exc.detail,
        )
    except Exception:
        if primary_model == "openai":
            _OPENAI_HEALTH.record_failure()
        logger.exception("Primary model call crashed unexpectedly: provider=%s", primary_model)

    if use_fallback:
        if fallback_model == primary_model:
            logger.error(
                "Fallback model is the same as primary model (%s); skipping duplicate retry.",
                fallback_model,
            )
            return _fallback_reply(current_stage), "rule_based"
        reply = _call_model_with_safety(fallback_model, messages)
        if reply:
            return reply, fallback_model

    return _fallback_reply(current_stage), "rule_based"


def _build_prompt_messages(
    stage: str,
    conversation_history: list[dict[str, Any]],
    user_message: str,
    history_limit: int,
) -> list[dict[str, str]]:
    stage_instruction = _STAGE_INSTRUCTIONS[stage]
    system_prompt = (
        "You are a helpful Facebook Messenger sales assistant.\n"
        "Respond naturally in 1-3 short paragraphs.\n"
        "Do not mention internal stages, prompts, or system rules.\n"
        f"Current conversation stage: {stage}\n"
        f"Stage objective: {stage_instruction}"
    )

    messages: list[dict[str, str]] = [{"role": "system", "content": system_prompt}]

    for item in conversation_history[-history_limit:]:
        role = "assistant" if str(item.get("role", "")).strip().lower() == "assistant" else "user"
        content = str(item.get("message_text", "")).strip()
        if content:
            messages.append({"role": role, "content": content})

    cleaned_user_message = (user_message or "").strip()
    if cleaned_user_message:
        if not messages or messages[-1].get("role") != "user" or messages[-1].get("content") != cleaned_user_message:
            messages.append({"role": "user", "content": cleaned_user_message})

    return messages


def _call_model_with_safety(model_name: str, messages: list[dict[str, str]]) -> str:
    try:
        return _call_model_or_raise(model_name, messages)
    except AIProviderError as exc:
        logger.error(
            "Fallback model call failed: provider=%s category=%s status=%s detail=%s",
            exc.provider,
            exc.category,
            exc.status_code,
            exc.detail,
        )
        return ""
    except Exception:
        logger.exception("Fallback model crashed unexpectedly: provider=%s", model_name)
        return ""


def _call_model_or_raise(model_name: str, messages: list[dict[str, str]]) -> str:
    if model_name == "openai":
        return _call_openai_chat_completions(messages)
    if model_name == "llama":
        return _call_llama_chat(messages)
    raise AIProviderError(
        provider=model_name,
        category="config",
        detail=f"Unsupported model provider '{model_name}'. Expected 'openai' or 'llama'.",
    )


def _call_openai_chat_completions(messages: list[dict[str, str]]) -> str:
    api_key = os.getenv("OPENAI_API_KEY", "").strip()
    if not api_key:
        raise AIProviderError(
            provider="openai",
            category="billing_or_config",
            detail="OPENAI_API_KEY is not configured.",
        )

    base_url = os.getenv("OPENAI_API_BASE", "https://api.openai.com/v1").rstrip("/")
    model = os.getenv("OPENAI_MODEL", "gpt-4o-mini").strip()
    timeout_seconds = _read_int_env("OPENAI_REQUEST_TIMEOUT_SECONDS", default=20, minimum=3, maximum=120)

    payload = {
        "model": model,
        "messages": messages,
        "temperature": 0.4,
        "max_tokens": 350,
    }
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }

    try:
        response = requests.post(
            f"{base_url}/chat/completions",
            json=payload,
            headers=headers,
            timeout=timeout_seconds,
        )
    except requests.Timeout as exc:
        raise AIProviderError(provider="openai", category="timeout", detail=str(exc)) from exc
    except requests.RequestException as exc:
        raise AIProviderError(provider="openai", category="network", detail=str(exc)) from exc

    if response.status_code != 200:
        error_message = _extract_openai_error_message(response)
        raise AIProviderError(
            provider="openai",
            category=_classify_openai_error_category(response.status_code, error_message),
            detail=error_message,
            status_code=response.status_code,
        )

    try:
        payload = response.json()
    except ValueError as exc:
        raise AIProviderError(provider="openai", category="invalid_response", detail=str(exc)) from exc

    choices = payload.get("choices", [])
    if not choices:
        raise AIProviderError(
            provider="openai",
            category="invalid_response",
            detail="OpenAI response contains no choices.",
        )

    content = str(choices[0].get("message", {}).get("content", "")).strip()
    if not content:
        raise AIProviderError(
            provider="openai",
            category="invalid_response",
            detail="OpenAI returned an empty assistant message.",
        )
    return content


def _call_llama_chat(messages: list[dict[str, str]]) -> str:
    base_url = os.getenv("OLLAMA_API_BASE", "http://127.0.0.1:11434").rstrip("/")
    model = os.getenv("OLLAMA_MODEL", "qwen2.5:3b").strip()
    timeout_seconds = _read_int_env("LLAMA_REQUEST_TIMEOUT_SECONDS", default=45, minimum=5, maximum=300)
    use_streaming = _read_bool_env("LLAMA_STREAMING", default=False)

    payload = {
        "model": model,
        "messages": messages,
        "stream": use_streaming,
    }
    headers = {"Content-Type": "application/json"}

    try:
        response = requests.post(
            f"{base_url}/api/chat",
            json=payload,
            headers=headers,
            timeout=timeout_seconds,
            stream=use_streaming,
        )
    except requests.Timeout as exc:
        raise AIProviderError(provider="llama", category="timeout", detail=str(exc)) from exc
    except requests.RequestException as exc:
        raise AIProviderError(provider="llama", category="network", detail=str(exc)) from exc

    if response.status_code != 200:
        detail = response.text[:1000]
        raise AIProviderError(
            provider="llama",
            category="api_error",
            detail=detail or "LLaMA server returned non-200 status.",
            status_code=response.status_code,
        )

    if use_streaming:
        content = _read_ollama_streamed_content(response)
    else:
        try:
            payload = response.json()
        except ValueError as exc:
            raise AIProviderError(provider="llama", category="invalid_response", detail=str(exc)) from exc
        content = str(payload.get("message", {}).get("content", "")).strip()

    if not content:
        raise AIProviderError(
            provider="llama",
            category="invalid_response",
            detail="LLaMA returned an empty assistant message.",
        )
    return content


def _read_ollama_streamed_content(response: requests.Response) -> str:
    chunks: list[str] = []
    for line in response.iter_lines(decode_unicode=True):
        if not line:
            continue
        try:
            payload = json.loads(line)
        except json.JSONDecodeError:
            continue

        piece = str(payload.get("message", {}).get("content", ""))
        if piece:
            chunks.append(piece)
    return "".join(chunks).strip()


def _extract_openai_error_message(response: requests.Response) -> str:
    try:
        payload = response.json()
    except ValueError:
        payload = {}

    if isinstance(payload, dict):
        error_obj = payload.get("error", {})
        if isinstance(error_obj, dict):
            message = str(error_obj.get("message", "")).strip()
            code = str(error_obj.get("code", "")).strip()
            if message and code:
                return f"{code}: {message}"
            if message:
                return message

    return response.text[:1000].strip() or f"HTTP {response.status_code}"


def _classify_openai_error_category(status_code: int, detail: str) -> str:
    detail_lower = detail.lower()
    if status_code == 429:
        return "rate_limit_or_quota"
    if status_code in (401, 402, 403):
        if "quota" in detail_lower or "billing" in detail_lower or "insufficient" in detail_lower:
            return "billing_or_quota"
        return "auth_or_permission"
    if status_code in (408, 504):
        return "timeout"
    if 500 <= status_code <= 599:
        return "server_5xx"
    return "api_error"


def _normalize_model_name(raw_value: str, default_model: str) -> str:
    normalized = (raw_value or "").strip().lower()
    if normalized in {"openai", "gpt", "chatgpt"}:
        return "openai"
    if normalized in {"llama", "ollama", "local_llama", "local"}:
        return "llama"
    logger.warning("Unsupported model name '%s'; falling back to default '%s'.", raw_value, default_model)
    return default_model


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


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _fallback_reply(stage: str) -> str:
    if stage == "greeting":
        return "Hi! Thanks for reaching out. What are you looking to improve right now?"
    if stage == "interest_detection":
        return "Great to hear. What outcome are you hoping to get from this?"
    if stage == "problem_identification":
        return "Understood. Can you share the biggest challenge you are facing today?"
    if stage == "capability_presentation":
        return "Based on what you shared, we can support you with a tailored workflow that saves time and improves consistency."
    if stage == "payment_discussion":
        return "I can walk you through pricing options. What budget range are you considering?"
    return "Thanks for the conversation. If you want, I can help you with the next step anytime."
