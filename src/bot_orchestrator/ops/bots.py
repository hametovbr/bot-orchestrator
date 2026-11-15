"""Bots: lifecycle, access settings, secrets, stored values, the update log and the API console."""

from __future__ import annotations

import json
import re
from typing import Any

from ..errors import Invalid, NotFound
from ..security import TOKEN_FORMAT
from ..updates import normalize_allowed
from .base import Base, BotRef, public_bot

SECRET_NAME = re.compile(r"^[A-Za-z_][A-Za-z0-9_]{0,63}$")
KV_KEY = re.compile(r"^[\w.:-]{1,200}$")

UPDATE_STATUSES = ("ok", "error", "timeout", "dispatch_error", "pending", "no_route", "ignored")


class Bots(Base):
    # Reading

    async def list(self) -> list[dict[str, Any]]:
        """Every bot with its status, polling state and 24-hour counters."""
        return [public_bot(self.o, bot) for bot in await self.db.list_bots()]

    async def get(self, ref: BotRef) -> dict[str, Any]:
        return public_bot(self.o, await self.bot_row(ref))

    # Lifecycle

    async def add(self, token: str, log_out_cloud: bool = True) -> tuple[dict[str, Any], list[str]]:
        """Validate the token with getMe, store the bot and start polling. Returns notes."""
        token = token.strip()
        if not TOKEN_FORMAT.match(token):
            raise Invalid("That does not look like a bot token (123456:ABC...).")
        bot, notes = await self.o.add_bot(token, log_out_cloud)
        return public_bot(self.o, bot), notes

    async def pause(self, ref: BotRef) -> None:
        await self.o.pause(await self.bot_id(ref))

    async def resume(self, ref: BotRef) -> None:
        await self.o.resume(await self.bot_id(ref))

    async def log_out_cloud(self, ref: BotRef) -> list[str]:
        """deleteWebhook + logOut on api.telegram.org; returns notes about the outcome."""
        return await self.o.log_out_from_cloud(self.o.token_of(await self.bot_row(ref)))

    async def log_out_local(self, ref: BotRef) -> None:
        """Pause the bot and release it from the local server, so it can go back to the cloud."""
        await self.o.log_out_local(await self.bot_id(ref))

    async def delete(self, ref: BotRef) -> dict[str, Any]:
        """Stop polling, cancel running tasks and remove the bot. Its files stay on disk."""
        bot = await self.get(ref)
        await self.o.delete_bot(bot["id"])
        return bot

    # Access

    async def set_allowed_users(self, ref: BotRef, allowed: str | list[str]) -> list[str]:
        """Replace the allow list (ids and @usernames). An empty list allows everyone."""
        bot_id = await self.bot_id(ref)
        text = allowed if isinstance(allowed, str) else ",".join(str(a) for a in allowed)
        entries, invalid = normalize_allowed(text)
        if invalid:
            raise Invalid(
                "Not a Telegram user id or username: "
                + ", ".join(invalid)
                + ". Use numeric ids or @usernames."
            )
        await self.db.set_allowed_users(bot_id, ",".join(entries))
        return entries

    async def set_purge_command(self, ref: BotRef, enabled: bool) -> None:
        await self.db.set_purge_command(await self.bot_id(ref), enabled)

    async def recent_senders(self, ref: BotRef | None = None, limit: int = 10):
        bot_id = await self.bot_id(ref) if ref is not None else None
        return await self.db.recent_senders(limit=limit, bot_id=bot_id)

    # Secrets: write-only; values are never returned.

    async def secret_names(self, ref: BotRef) -> list[str]:
        return await self.db.list_secret_names(await self.bot_id(ref))

    async def set_secret(self, ref: BotRef, name: str, value: str) -> str:
        bot_id = await self.bot_id(ref)
        name = name.strip()
        if not SECRET_NAME.match(name):
            raise Invalid(
                "Secret names may use letters, digits and _ and must not start with a digit."
            )
        await self.db.set_secret(bot_id, name, self.o.cipher.encrypt(value))
        return name

    async def delete_secret(self, ref: BotRef, name: str) -> None:
        await self.db.delete_secret(await self.bot_id(ref), name)

    # Values stored by handlers with ctx.kv

    @staticmethod
    def check_key(key: str) -> str:
        if not KV_KEY.match(key):
            raise Invalid("Keys use 1-200 letters, digits, _, ., : and -.")
        return key

    async def kv_list(self, ref: BotRef) -> list[dict[str, Any]]:
        rows = await self.db.kv_list(await self.bot_id(ref))
        return [{**row, "value": json.loads(row["value"])} for row in rows]

    async def kv_get(self, ref: BotRef, key: str) -> Any:
        found, value = await self.db.kv_get(await self.bot_id(ref), self.check_key(key))
        if not found:
            raise NotFound(f"No stored value for key {key}")
        return value

    async def kv_set(self, ref: BotRef, key: str, value: Any) -> None:
        await self.db.kv_set(await self.bot_id(ref), self.check_key(key), value)

    async def kv_delete(self, ref: BotRef, key: str) -> None:
        await self.db.kv_delete(await self.bot_id(ref), key)

    # Update log

    async def updates(
        self, ref: BotRef, status: str | None = None, limit: int = 100
    ) -> list[dict[str, Any]]:
        if status and status not in UPDATE_STATUSES:
            raise Invalid("Status must be one of: " + ", ".join(UPDATE_STATUSES))
        return await self.db.list_updates(
            await self.bot_id(ref), status=status or None, limit=limit
        )

    async def update(self, ref: BotRef, row_id: int) -> dict[str, Any]:
        """One logged update with its raw JSON, logs, API calls, route and the tasks it started."""
        bot_id = await self.bot_id(ref)
        row = await self.update_row(bot_id, row_id)
        route = await self.db.get_route(bot_id, row["route_id"]) if row["route_id"] else None
        tasks = [
            t for t in await self.db.list_tasks(bot_id, limit=200) if t["update_row"] == row_id
        ]
        return {**row, "route": route, "tasks": tasks}

    # API console

    async def console_history(self, ref: BotRef, limit: int = 50) -> list[dict[str, Any]]:
        return await self.db.list_console_calls(await self.bot_id(ref), limit=limit)

    async def console_call(
        self, ref: BotRef, method: str, params: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        """Call any Bot API method on the local server; tokens are masked in the answer."""
        if params is None:
            params = {}
        if not isinstance(params, dict):
            raise Invalid("Parameters must be a JSON object")
        return await self.o.console_call(await self.bot_id(ref), str(method or ""), params)
