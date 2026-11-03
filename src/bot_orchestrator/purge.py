"""The built-in /purge command: delete the recent history of a private chat with a bot.

The Bot API has no "clear history" method and no way to read a chat's history. In a
private chat, though, message ids are one sequence shared by both sides, so every
message between two known ids can be deleted by id. A bot may delete incoming and
outgoing messages there, but only those sent less than 48 hours ago.

The plan therefore takes the range from just after the newest message known to be
older than the 48-hour window (from the update log) up to the /purge message itself,
and leaves out every /start command. Older messages stay: Telegram refuses to delete
them.
"""

from __future__ import annotations

import re
import time
from dataclasses import dataclass, field
from typing import Any

from .telegram import BotApi, TelegramError

COMMAND = "purge"
# Telegram allows deleting messages younger than 48 hours; keep a margin for clock skew.
DELETABLE_FOR = 47 * 3600
# Never try more ids than this in one run (100 per request).
MAX_SPAN = 3000
BATCH = 100

_COMMAND_RE = re.compile(r"^/([A-Za-z0-9_]{1,32})(?:@([A-Za-z0-9_]+))?(?:\s|$)")


def command_name(text: str | None, bot_username: str = "") -> str | None:
    """'/purge@my_bot' -> 'purge'; None if not a command or addressed to another bot."""
    found = _COMMAND_RE.match(text or "")
    if not found:
        return None
    mention = found.group(2)
    if mention and bot_username and mention.lower() != bot_username.lower():
        return None
    return found.group(1).lower()


def is_purge(update: dict[str, Any], bot_username: str) -> bool:
    """A /purge command in a private chat."""
    message = update.get("message")
    if not isinstance(message, dict) or (message.get("chat") or {}).get("type") != "private":
        return False
    return command_name(message.get("text"), bot_username) == COMMAND


@dataclass
class Plan:
    chat_id: int
    first: int
    last: int
    keep: list[int] = field(default_factory=list)

    @property
    def ids(self) -> list[int]:
        keep = set(self.keep)
        return [i for i in range(self.first, self.last + 1) if i not in keep]


def plan(
    chat_id: int, purge_id: int, history: list[dict[str, Any]], now: float | None = None
) -> Plan:
    """Which message ids to delete.

    ``history`` holds the chat's incoming messages from the update log, each with
    ``message_id``, ``date`` (Unix time) and ``text``.
    """
    now = time.time() if now is None else now
    known = [m for m in history if m["message_id"] < purge_id]
    old = [m["message_id"] for m in known if m["date"] < now - DELETABLE_FOR]
    first = max(old) + 1 if old else 1
    first = max(first, purge_id - MAX_SPAN + 1)
    keep = sorted(
        m["message_id"]
        for m in known
        if m["message_id"] >= first and command_name(m.get("text")) == "start"
    )
    return Plan(chat_id=chat_id, first=first, last=purge_id, keep=keep)


async def execute(api: BotApi, chat_plan: Plan) -> dict[str, Any]:
    """Delete the planned ids; returns logs and api_calls for the update log."""
    started = time.monotonic()
    ids = chat_plan.ids
    logs = [
        f"Deleting messages {chat_plan.first}-{chat_plan.last} ({len(ids)} ids)"
        + (f", keeping /start: {', '.join(map(str, chat_plan.keep))}" if chat_plan.keep else "")
    ]
    api_calls: list[dict[str, Any]] = []
    failed_batches = 0
    single_failures = 0
    for start in range(0, len(ids), BATCH):
        batch = ids[start : start + BATCH]
        params = {"chat_id": chat_plan.chat_id, "message_ids": batch}
        summary = {"chat_id": chat_plan.chat_id, "message_ids": f"{batch[0]}-{batch[-1]}"}
        try:
            await api.call("deleteMessages", params)
            api_calls.append({"method": "deleteMessages", "params": summary, "ok": True})
            continue
        except TelegramError as exc:
            if exc.unreachable:
                raise
            failed_batches += 1
            api_calls.append(
                {
                    "method": "deleteMessages",
                    "params": summary,
                    "ok": False,
                    "error": exc.description,
                }
            )
        # The batch was refused as a whole (for example it held a message that is too
        # old): delete its messages one by one and skip the ones Telegram refuses.
        for message_id in batch:
            try:
                await api.call(
                    "deleteMessage", {"chat_id": chat_plan.chat_id, "message_id": message_id}
                )
            except TelegramError as exc:
                if exc.unreachable:
                    raise
                single_failures += 1
    if failed_batches:
        logs.append(
            f"{failed_batches} batch(es) refused; retried one by one, "
            f"{single_failures} message(s) could not be deleted (too old or already gone)"
        )
    logs.append("Done")
    return {
        "status": "ok",
        "logs": logs,
        "api_calls": api_calls[:200],
        "duration_ms": int((time.monotonic() - started) * 1000),
    }
