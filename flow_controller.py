from __future__ import annotations

import re

STAGES = (
    "greeting",
    "interest_detection",
    "problem_identification",
    "capability_presentation",
    "payment_discussion",
    "closing",
)

_POSITIVE_PATTERNS = (
    r"\byes\b",
    r"\byeah\b",
    r"\byep\b",
    r"\bok\b",
    r"\bokay\b",
    r"\bsure\b",
    r"\binterested\b",
    r"\bsounds good\b",
    r"\blet's do it\b",
)

_PRICE_PATTERNS = (
    r"\bprice\b",
    r"\bcost\b",
    r"\bpayment\b",
    r"\bpay\b",
    r"\bpricing\b",
    r"\bhow much\b",
    r"\bbudget\b",
    r"\bfee\b",
)

_PROBLEM_PATTERNS = (
    r"\bproblem\b",
    r"\bissue\b",
    r"\bchallenge\b",
    r"\bstruggle\b",
    r"\bpain\b",
    r"\bstuck\b",
    r"\bneed help\b",
    r"\bcannot\b",
    r"\bcan't\b",
)

_CAPABILITY_PATTERNS = (
    r"\bfeatures?\b",
    r"\bwhat can\b",
    r"\bcan you\b",
    r"\bcapabilit(?:y|ies)\b",
    r"\bdemo\b",
    r"\bexample\b",
    r"\bhow does\b",
)

_CLOSING_PATTERNS = (
    r"\bthank you\b",
    r"\bthanks\b",
    r"\bbye\b",
    r"\bgoodbye\b",
    r"\bnot now\b",
    r"\bno thanks\b",
    r"\bstop\b",
)


def determine_next_stage(current_stage: str, user_message: str) -> str:
    message = (user_message or "").strip().lower()
    stage = current_stage if current_stage in STAGES else "greeting"
    if not message:
        return stage

    # High-priority transitions independent of current stage.
    if _matches_any(message, _CLOSING_PATTERNS):
        return "closing"
    if _matches_any(message, _PRICE_PATTERNS):
        return "payment_discussion"

    if stage == "greeting":
        if _is_positive(message):
            return "interest_detection"
        return "greeting"

    if stage == "interest_detection":
        if _matches_any(message, _PROBLEM_PATTERNS):
            return "problem_identification"
        if _matches_any(message, _CAPABILITY_PATTERNS):
            return "capability_presentation"
        if _is_positive(message):
            return "problem_identification"
        return "interest_detection"

    if stage == "problem_identification":
        if _matches_any(message, _CAPABILITY_PATTERNS) or _is_positive(message):
            return "capability_presentation"
        return "problem_identification"

    if stage == "capability_presentation":
        if _is_positive(message):
            return "payment_discussion"
        return "capability_presentation"

    if stage == "payment_discussion":
        if _is_positive(message) or _matches_any(message, _CLOSING_PATTERNS):
            return "closing"
        return "payment_discussion"

    return "closing"


def _is_positive(message: str) -> bool:
    return _matches_any(message, _POSITIVE_PATTERNS)


def _matches_any(message: str, patterns: tuple[str, ...]) -> bool:
    return any(re.search(pattern, message) for pattern in patterns)
