"""Platform-wide state: overview, notifications and storage."""

from __future__ import annotations

import asyncio
from typing import Any

from .. import __version__, maintenance
from ..errors import Invalid, Unavailable
from ..notify import EVENTS, Notifier
from ..telegram import TelegramError
from .base import Base, public_bot


class System(Base):
    async def overview(self) -> dict[str, Any]:
        """Version, timezone, bots with counters, runner packages and storage, in one call."""
        bots = [public_bot(self.o, b) for b in await self.db.list_bots()]
        packages = await self.o.packages.status()
        return {
            "version": __version__,
            "timezone": self.o.settings.tz,
            "bots": [
                {
                    k: b.get(k)
                    for k in (
                        "id",
                        "username",
                        "first_name",
                        "status",
                        "status_message",
                        "polling",
                        "updates_24h",
                        "errors_24h",
                        "tasks_active",
                        "routes_enabled",
                    )
                }
                for b in bots
            ],
            "runner": {
                "reachable": packages.get("runner") is not None,
                "error": packages.get("error"),
                "packages_in_sync": packages.get("in_sync"),
            },
            "storage": await self.storage(),
        }

    async def storage(self) -> dict[str, Any]:
        settings = self.o.settings
        cache = await asyncio.to_thread(maintenance.cache_usage, settings.bot_api_data_dir)
        return {
            "files_root": settings.files_root,
            "files_bytes": await asyncio.to_thread(maintenance.folder_size, settings.files_root),
            "disk_percent_used": await asyncio.to_thread(
                maintenance.disk_percent_used, settings.files_root
            ),
            "disk_warning_percent": settings.disk_warning_percent,
            "cache_files": cache.files,
            "cache_bytes": cache.bytes,
            "cache_retention_hours": settings.cache_retention_hours,
            "update_retention_days": settings.update_retention_days,
        }

    async def clean_cache(self, older_than_hours: float = 0) -> maintenance.CacheStats:
        """Delete files the Bot API server downloaded more than this many hours ago."""
        return await self.o.clean_cache(max(older_than_hours, 0))

    # Notifications

    async def notifications(self) -> dict[str, Any]:
        return {**await self.o.notifier.config(), "available_events": EVENTS}

    async def set_notifications(
        self,
        bot_id: int | str | None,
        chat_id: int | str | None,
        events: list[str] | None,
    ) -> dict[str, Any]:
        """Where failures are reported (a managed bot and a chat) and which events."""
        bot_text = str(bot_id if bot_id is not None else "").strip()
        chat = str(chat_id if chat_id is not None else "").strip()
        if chat and not chat.lstrip("-").isdigit():
            raise Invalid("The chat id must be a number, e.g. your Telegram user id.")
        if bot_text and not bot_text.isdigit():
            bot_text = str(await self.bot_id(bot_text))
        unknown = [e for e in events or [] if e not in EVENTS]
        if unknown:
            raise Invalid("Unknown events: " + ", ".join(unknown) + ". Use: " + ", ".join(EVENTS))
        await self.db.set_setting("notify_bot_id", int(bot_text) if bot_text.isdigit() else None)
        await self.db.set_setting("notify_chat_id", int(chat) if chat else None)
        await self.db.set_setting("notify_events", list(events or []))
        return await self.notifications()

    async def test_notification(self) -> None:
        config = await self.o.notifier.config()
        if not config["notify_bot_id"] or not config["notify_chat_id"]:
            raise Invalid("Choose a bot and a chat first.")
        try:
            await Notifier(self.o).deliver(
                config["notify_bot_id"],
                config["notify_chat_id"],
                "✅ Test notification from Bot Orchestrator." + self.o.notifier.link("/settings"),
            )
        except TelegramError as exc:
            raise Unavailable(
                f"Could not send: {exc.description}. The chat must have started the bot first."
            ) from None
