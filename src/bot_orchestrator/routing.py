"""Matching updates against a bot's routes."""

from __future__ import annotations

import re
from typing import Any

from .updates import CONTENT_TYPES, MESSAGE_KINDS, update_kind

ROUTE_KINDS = {
    "command": "Command",
    "regex": "Text matches regex",
    "content": "Content type",
    "callback": "Button (callback data prefix)",
    "update": "Update type",
    "any": "Anything else",
}

PATTERN_HELP = {
    "command": "Command name without the slash, e.g. start",
    "regex": r"Python regular expression searched in the text or caption, e.g. https?://\S+",
    "content": "Comma-separated: " + ", ".join(CONTENT_TYPES),
    "callback": "Prefix of callback_data, e.g. cancel: (empty matches every button)",
    "update": "Comma-separated update types, e.g. edited_message, my_chat_member, schedule",
    "any": "No pattern",
}

UPDATE_TYPES = (
    *MESSAGE_KINDS,
    "callback_query",
    "inline_query",
    "chosen_inline_result",
    "my_chat_member",
    "chat_member",
    "chat_join_request",
    "message_reaction",
    "poll",
    "poll_answer",
    "pre_checkout_query",
    "shipping_query",
    "schedule",
)

COMMAND_NAME = re.compile(r"^[A-Za-z0-9_]{1,32}$")
_COMMAND_IN_TEXT = re.compile(r"^/([A-Za-z0-9_]{1,32})(?:@([A-Za-z0-9_]+))?(?:\s|$)")


def _split(pattern: str) -> list[str]:
    return [p.strip() for p in pattern.replace(";", ",").split(",") if p.strip()]


def validate(kind: str, pattern: str) -> str:
    """Return the normalized pattern or raise ValueError with a readable message."""
    pattern = pattern.strip()
    if kind not in ROUTE_KINDS:
        raise ValueError(f"Unknown route type: {kind}")
    if kind == "command":
        name = pattern.removeprefix("/")
        if not COMMAND_NAME.match(name):
            raise ValueError("A command is 1-32 letters, digits or underscores, e.g. start")
        return name.lower()
    if kind == "regex":
        if not pattern:
            raise ValueError("Enter a regular expression")
        try:
            re.compile(pattern)
        except re.error as exc:
            raise ValueError(f"Invalid regular expression: {exc}") from None
        return pattern
    if kind == "content":
        values = [v.lower() for v in _split(pattern)]
        unknown = [v for v in values if v not in CONTENT_TYPES]
        if not values or unknown:
            raise ValueError("Content types must be from: " + ", ".join(CONTENT_TYPES))
        return ", ".join(values)
    if kind == "update":
        values = [v.lower() for v in _split(pattern)]
        unknown = [v for v in values if v not in UPDATE_TYPES]
        if not values or unknown:
            raise ValueError("Update types must be from: " + ", ".join(UPDATE_TYPES))
        return ", ".join(values)
    if kind == "callback":
        return pattern
    return ""


def _message(update: dict[str, Any]) -> dict[str, Any] | None:
    for key in MESSAGE_KINDS:
        if isinstance(update.get(key), dict):
            return update[key]
    return None


def matches(route: dict[str, Any], update: dict[str, Any], bot_username: str = "") -> bool:
    kind, pattern = route["kind"], route["pattern"]
    if kind == "any":
        return True
    if kind == "update":
        return update_kind(update) in _split(pattern)
    if kind == "callback":
        query = update.get("callback_query")
        return bool(query) and str(query.get("data") or "").startswith(pattern)
    message = _message(update)
    if message is None:
        return False
    text = message.get("text") or message.get("caption") or ""
    if kind == "command":
        found = _COMMAND_IN_TEXT.match(message.get("text") or "")
        if not found:
            return False
        name, mention = found.group(1).lower(), found.group(2)
        if mention and bot_username and mention.lower() != bot_username.lower():
            return False
        return name == pattern
    if kind == "regex":
        try:
            return re.search(pattern, text) is not None
        except re.error:
            return False
    if kind == "content":
        return any(c in message for c in _split(pattern))
    return False


def find_route(
    routes: list[dict[str, Any]], update: dict[str, Any], bot_username: str = ""
) -> dict[str, Any] | None:
    """The first enabled route, in order, that matches the update."""
    for route in routes:
        if route["enabled"] and matches(route, update, bot_username):
            return route
    return None


def pattern_text(route: dict[str, Any]) -> str:
    """The pattern alone, for tables that show the condition type separately."""
    kind, pattern = route["kind"], route["pattern"]
    if kind == "command":
        return f"/{pattern}"
    if kind == "callback":
        return f"{pattern}…" if pattern else "any button"
    if kind == "any":
        return "—"
    return pattern


def describe(route: dict[str, Any]) -> str:
    kind, pattern = route["kind"], route["pattern"]
    if kind == "command":
        return f"/{pattern}"
    if kind == "any":
        return "anything"
    if kind == "callback":
        return f"button data starts with “{pattern}”" if pattern else "any button"
    return f"{ROUTE_KINDS[kind].lower()}: {pattern}"
