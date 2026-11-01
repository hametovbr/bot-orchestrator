"""Telegram notifications about failures, sent through one of the managed bots."""

from __future__ import annotations

import logging
import time
from typing import TYPE_CHECKING, Any

from .telegram import TelegramError

if TYPE_CHECKING:
    from .service import Orchestrator

log = logging.getLogger("bot_orchestrator")

EVENTS = {
    "handler_errors": "Handler errors and timeouts",
    "task_errors": "Failed background tasks",
    "schedule_errors": "Failed scheduled runs",
    "bot_stopped": "A bot stopped polling (revoked token, Bot API server unreachable)",
    "runner_down": "bot-runner is unreachable",
    "disk_low": "Disk with bot files is almost full",
}
DEFAULTS = {
    "notify_bot_id": None,
    "notify_chat_id": None,
    "notify_events": list(EVENTS),
}
THROTTLE_SECONDS = 600


class Notifier:
    def __init__(self, orchestrator: Orchestrator):
        self._o = orchestrator
        # key -> (time of the last sent message, messages suppressed since)
        self._recent: dict[str, tuple[float | None, int]] = {}
        self.clock = time.monotonic

    async def config(self) -> dict[str, Any]:
        db = self._o.db
        return {key: await db.get_setting(key, default) for key, default in DEFAULTS.items()}

    def link(self, path: str) -> str:
        base = self._o.settings.public_url.rstrip("/")
        return f"\n{base}{path}" if base else ""

    async def send(self, event: str, key: str, text: str) -> None:
        """Send a notification unless the event is off or the key fired recently."""
        try:
            config = await self.config()
            if event not in config["notify_events"]:
                return
            if not config["notify_bot_id"] or not config["notify_chat_id"]:
                return
            now = self.clock()
            # No entry means "never sent". (Comparing with 0 would swallow every message
            # while the host's monotonic clock is young, e.g. just after a reboot.)
            last, suppressed = self._recent.get(key, (None, 0))
            if last is not None and now - last < THROTTLE_SECONDS:
                self._recent[key] = (last, suppressed + 1)
                return
            if suppressed:
                text += f"\n(+{suppressed} more like this in the last {THROTTLE_SECONDS // 60} min)"
            self._recent[key] = (now, 0)
            await self.deliver(config["notify_bot_id"], config["notify_chat_id"], text)
        except Exception:  # noqa: BLE001 - notifications must never break the caller
            log.exception("could not send notification")

    async def deliver(self, bot_id: int, chat_id: int, text: str) -> None:
        bot = await self._o.db.get_bot(int(bot_id))
        if not bot:
            raise TelegramError("sendMessage", None, "the notification bot was removed")
        api = self._o.local_api(self._o.token_of(bot))
        await api.call(
            "sendMessage",
            {
                "chat_id": int(chat_id),
                "text": text[:4000],
                "link_preview_options": {"is_disabled": True},
            },
        )
