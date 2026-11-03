"""The built-in /purge command: delete the recent history of a private chat with a bot.

The Bot API has no "clear history" method and no way to read a chat's history. In a
private chat, though, message ids are one sequence shared by both sides, so every
message between two known ids can be deleted by id. A bot may delete incoming and
outgoing messages there, but only those sent less than 48 hours ago.

The plan takes the range from just after the newest message known to be older than
the 48-hour window (from the update log) up to the /purge message itself, and leaves
out every /start command. Batches are deleted from the newest down, so the recent
messages go first. A refused batch is split in halves to find what can be deleted;
a batch where nothing can be deleted means the 48-hour limit was reached, and the
run stops there. Every run has a time limit.
"""

from __future__ import annotations

import asyncio
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
# The whole run, including waits for Telegram's flood control.
TIME_LIMIT = 90.0
MAX_RETRY_AFTER = 30

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

    def batches(self) -> list[list[int]]:
        """Batches of up to 100 ids, the newest batch first."""
        ids = self.ids
        return [ids[max(0, end - BATCH) : end] for end in range(len(ids), 0, -BATCH)]


def plan(
    chat_id: int, purge_id: int, history: list[dict[str, Any]], now: float | None = None
) -> Plan:
    """Which message ids to delete.

    ``history`` holds the chat's incoming messages from the update log, each with
    ``message_id``, ``date`` (Unix time) and ``text``.
    """
    now = time.time() if now is None else now
    known = [m for m in history if m.get("message_id") and m["message_id"] < purge_id]
    old = [m["message_id"] for m in known if (m.get("date") or 0) < now - DELETABLE_FOR]
    first = max(old) + 1 if old else 1
    first = max(first, purge_id - MAX_SPAN + 1)
    keep = sorted(
        m["message_id"]
        for m in known
        if m["message_id"] >= first and command_name(m.get("text")) == "start"
    )
    return Plan(chat_id=chat_id, first=first, last=purge_id, keep=keep)


class _Run:
    def __init__(self, api: BotApi, chat_id: int, deadline: float):
        self.api = api
        self.chat_id = chat_id
        self.deadline = deadline
        self.requests = 0
        self.api_calls: list[dict[str, Any]] = []
        self.errors: dict[str, int] = {}

    def _record(self, ids: list[int], ok: bool, error: str | None = None) -> None:
        if len(self.api_calls) >= 200:
            return
        call: dict[str, Any] = {
            "method": "deleteMessages",
            "params": {"chat_id": self.chat_id, "message_ids": f"{ids[0]}-{ids[-1]} ({len(ids)})"},
            "ok": ok,
        }
        if error:
            call["error"] = error
        self.api_calls.append(call)

    async def delete(self, ids: list[int]) -> bool:
        """One deleteMessages call; waits once for flood control. True if accepted."""
        for _ in range(2):
            if time.monotonic() > self.deadline:
                raise TimeoutError
            self.requests += 1
            data = await self.api.raw(
                "deleteMessages", {"chat_id": self.chat_id, "message_ids": ids}
            )
            if data.get("ok"):
                self._record(ids, True)
                return True
            retry_after = (data.get("parameters") or {}).get("retry_after")
            if data.get("error_code") == 429 and retry_after is not None:
                await asyncio.sleep(min(float(retry_after), MAX_RETRY_AFTER))
                continue
            description = str(data.get("description") or "unknown error")
            self.errors[description] = self.errors.get(description, 0) + 1
            self._record(ids, False, description)
            return False
        return False

    async def delete_some(self, ids: list[int]) -> int:
        """Delete what can be deleted; returns how many ids were accepted."""
        if await self.delete(ids):
            return len(ids)
        if len(ids) == 1:
            return 0
        # Refused as a whole: some messages are too old (or cannot be deleted). The
        # newer half is the more likely to succeed.
        middle = len(ids) // 2
        newer = await self.delete_some(ids[middle:])
        if newer == 0:
            # Nothing new is deletable, so nothing older is either.
            return 0
        return newer + await self.delete_some(ids[:middle])


async def execute(api: BotApi, chat_plan: Plan, time_limit: float = TIME_LIMIT) -> dict[str, Any]:
    """Delete the planned ids; returns status, logs and api_calls for the update log."""
    started = time.monotonic()
    run = _Run(api, chat_plan.chat_id, started + time_limit)
    logs = [
        f"Range {chat_plan.first}-{chat_plan.last} ({len(chat_plan.ids)} ids)"
        + (f", keeping /start: {', '.join(map(str, chat_plan.keep))}" if chat_plan.keep else "")
    ]
    status, error = "ok", None
    accepted = 0
    try:
        for batch in chat_plan.batches():
            done = await run.delete_some(batch)
            accepted += done
            if done < len(batch):
                logs.append(f"Stopped at {batch[0]}-{batch[-1]}: older messages cannot be deleted")
                break
    except TimeoutError:
        status, error = "timeout", f"stopped after {time_limit:g} s"
    except TelegramError as exc:
        status, error = "error", str(exc)
    logs.append(f"{run.requests} request(s), {accepted} id(s) accepted")
    for description, count in run.errors.items():
        logs.append(f"Telegram refused {count} request(s): {description}")
    return {
        "status": status,
        "error": error,
        "logs": logs,
        "api_calls": run.api_calls,
        "duration_ms": int((time.monotonic() - started) * 1000),
    }
