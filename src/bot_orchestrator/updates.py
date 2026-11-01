"""Describe Telegram updates for the update log."""

from __future__ import annotations

from typing import Any

MESSAGE_KINDS = (
    "message",
    "edited_message",
    "channel_post",
    "edited_channel_post",
    "business_message",
    "edited_business_message",
)

CONTENT_TYPES = (
    "text",
    "document",
    "photo",
    "video",
    "audio",
    "voice",
    "video_note",
    "animation",
    "sticker",
    "contact",
    "location",
    "venue",
    "poll",
    "dice",
)


def update_kind(update: dict[str, Any]) -> str:
    """The update type: the one key besides update_id."""
    for key in update:
        if key != "update_id":
            return key
    return "unknown"


def _user(update: dict[str, Any], kind: str) -> dict[str, Any] | None:
    body = update.get(kind)
    if not isinstance(body, dict):
        return None
    return body.get("from") or body.get("user") or body.get("sender_chat")


def describe(update: dict[str, Any]) -> dict[str, Any]:
    """Kind, sender and a one-line summary for the log table."""
    kind = update_kind(update)
    user = _user(update, kind)
    from_name = None
    if user:
        from_name = user.get("username") and "@" + user["username"]
        from_name = from_name or " ".join(
            p for p in (user.get("first_name"), user.get("last_name"), user.get("title")) if p
        )
    body = update.get(kind) if isinstance(update.get(kind), dict) else {}
    summary = ""
    if kind in MESSAGE_KINDS:
        content = next((c for c in CONTENT_TYPES if c in body), "other")
        if content == "text":
            summary = body["text"]
        elif content == "document":
            summary = f"[document] {body['document'].get('file_name', '')}"
        else:
            summary = f"[{content}]"
        if body.get("caption"):
            summary += f" {body['caption']}"
    elif kind == "callback_query":
        summary = f"[button] {body.get('data', '')}"
    elif kind == "schedule":
        summary = f"[schedule] {body.get('name', '')}"
    elif kind == "inline_query":
        summary = f"[inline] {body.get('query', '')}"
    return {
        "kind": kind,
        "from_id": user.get("id") if user else None,
        "from_name": from_name or None,
        "summary": summary[:200],
    }


def parse_allowed(value: str) -> set[int]:
    """'123, 456' -> {123, 456}; ignores anything that is not a number."""
    return {int(p) for p in value.replace(";", ",").replace(" ", ",").split(",") if p.isdigit()}
