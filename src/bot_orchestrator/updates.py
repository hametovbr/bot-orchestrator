"""Describe Telegram updates for the update log."""

from __future__ import annotations

import re
from dataclasses import dataclass
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


# Telegram usernames: letters, digits and "_", starting with a letter (3-32 accepted).
USERNAME_RE = re.compile(r"[A-Za-z][A-Za-z0-9_]{2,31}")


@dataclass(frozen=True)
class Allowed:
    """The allow list of a bot: numeric user ids and lowercase usernames."""

    ids: frozenset[int] = frozenset()
    usernames: frozenset[str] = frozenset()

    def __bool__(self) -> bool:
        return bool(self.ids or self.usernames)

    def permits(self, user: dict[str, Any] | None) -> bool:
        """True if the list is empty or the sender matches an id or a username."""
        if not self:
            return True
        if not user:
            return False
        if user.get("id") in self.ids:
            return True
        username = user.get("username")
        return bool(username) and username.lower() in self.usernames


def normalize_allowed(text: str) -> tuple[list[str], list[str]]:
    """Split user input into stored entries ("123", "@name") and invalid parts.

    Entries are separated by commas, semicolons or whitespace. A number is a user
    id; "@name", "name" and "t.me/name" are usernames, stored lowercase with "@".
    """
    entries: set[str] = set()
    invalid: list[str] = []
    for part in re.split(r"[\s,;]+", text):
        if not part:
            continue
        name = re.sub(r"^(https?://)?(t\.me|telegram\.me)/", "", part, flags=re.I).lstrip("@")
        if part.isdigit():
            entries.add(part)
        elif USERNAME_RE.fullmatch(name):
            entries.add("@" + name.lower())
        else:
            invalid.append(part)
    ids = sorted((e for e in entries if e.isdigit()), key=int)
    names = sorted(e for e in entries if not e.isdigit())
    return ids + names, invalid


def parse_allowed(value: str) -> Allowed:
    """Read the stored list ("123,@name"); ignores anything malformed."""
    entries, _ = normalize_allowed(value)
    return Allowed(
        ids=frozenset(int(e) for e in entries if e.isdigit()),
        usernames=frozenset(e[1:] for e in entries if e.startswith("@")),
    )


def sender(update: dict[str, Any]) -> dict[str, Any] | None:
    """The user (or chat) that sent the update, if any."""
    return _user(update, update_kind(update))
