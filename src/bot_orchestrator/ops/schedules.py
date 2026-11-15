"""Schedules: run a handler on a cron expression, and background tasks."""

from __future__ import annotations

from typing import Any

from .. import cron
from ..errors import Invalid, NotFound
from .base import Base, BotRef, NameRef

_UNSET: Any = object()

TASK_STATUSES = ("queued", "running", "ok", "error", "timeout", "cancelled", "interrupted")


class Schedules(Base):
    async def list(self, bot: BotRef) -> list[dict[str, Any]]:
        return await self.db.list_schedules(await self.bot_id(bot))

    async def get(self, bot: BotRef, schedule_id: int) -> dict[str, Any]:
        schedule = await self.db.get_schedule(await self.bot_id(bot), int(schedule_id))
        if not schedule:
            raise NotFound("Schedule not found")
        return schedule

    def preview(self, expression: str, count: int = 3) -> list[str]:
        """The next run times of a cron expression in the orchestrator's timezone."""
        try:
            parsed = cron.parse(expression)
        except cron.CronError as exc:
            raise Invalid(str(exc)) from None
        runs, moment = [], self.o.now_local()
        for _ in range(max(1, min(count, 20))):
            moment = parsed.next_after(moment)
            runs.append(moment.strftime("%a %Y-%m-%d %H:%M"))
        return runs

    async def _validate(
        self,
        bot_id: int,
        name: str,
        expression: str,
        handler: NameRef | None,
        chat_id: int | str | None,
        enabled: bool,
    ) -> dict[str, Any]:
        name = (name or "").strip()[:100]
        expression = " ".join(str(expression or "").split())
        chat = str(chat_id if chat_id is not None else "").strip()
        if not name:
            raise Invalid("Give the schedule a name.")
        try:
            next_at = self.o.next_run_at(expression)
        except cron.CronError as exc:
            raise Invalid(f"Cron expression: {exc}") from None
        if chat and not chat.lstrip("-").isdigit():
            raise Invalid("The chat id must be a number.")
        if handler in (None, ""):
            raise Invalid("Choose a handler.")
        try:
            handler_row = await self.handler_row(bot_id, handler)
        except NotFound:
            raise Invalid("Choose a handler.") from None
        return {
            "name": name,
            "cron": expression,
            "handler_id": handler_row["id"],
            "chat_id": int(chat) if chat else None,
            "enabled": bool(enabled),
            "next_run_at": next_at if enabled else None,
        }

    async def create(
        self,
        bot: BotRef,
        name: str,
        cron_expression: str,
        handler: NameRef | None,
        chat_id: int | str | None = None,
        enabled: bool = True,
    ) -> dict[str, Any]:
        bot_id = await self.bot_id(bot)
        data = await self._validate(bot_id, name, cron_expression, handler, chat_id, enabled)
        schedule_id = await self.db.create_schedule(bot_id, **data)
        return {**data, "id": schedule_id}

    async def update(
        self,
        bot: BotRef,
        schedule_id: int,
        *,
        name: str = _UNSET,
        cron_expression: str = _UNSET,
        handler: NameRef | None = _UNSET,
        chat_id: int | str | None = _UNSET,
        enabled: bool = _UNSET,
    ) -> dict[str, Any]:
        """Change some fields of a schedule; the others keep their values."""
        bot_id = await self.bot_id(bot)
        current = await self.get(bot_id, schedule_id)

        def pick(value: Any, key: str) -> Any:
            return current[key] if value is _UNSET else value

        data = await self._validate(
            bot_id,
            pick(name, "name"),
            pick(cron_expression, "cron"),
            pick(handler, "handler_id"),
            pick(chat_id, "chat_id"),
            pick(enabled, "enabled"),
        )
        await self.db.update_schedule(int(schedule_id), **data)
        return {**data, "id": int(schedule_id)}

    async def set_enabled(self, bot: BotRef, schedule_id: int, enabled: bool | None = None) -> bool:
        """Turn a schedule on or off (toggle when `enabled` is None). Returns the new state."""
        schedule = await self.get(bot, schedule_id)
        state = (not schedule["enabled"]) if enabled is None else bool(enabled)
        await self.db.update_schedule(
            schedule["id"],
            **{
                **schedule,
                "enabled": state,
                "next_run_at": self.o.next_run_at(schedule["cron"]) if state else None,
            },
        )
        return state

    async def run_now(self, bot: BotRef, schedule_id: int) -> tuple[dict[str, Any], int | None]:
        """Fire a schedule now (in the background). Returns it and the update log row id."""
        schedule = await self.get(bot, schedule_id)
        return schedule, await self.o.run_schedule(schedule, manual=True)

    async def delete(self, bot: BotRef, schedule_id: int) -> dict[str, Any]:
        schedule = await self.get(bot, schedule_id)
        await self.db.delete_schedule(schedule["id"])
        return schedule


class Tasks(Base):
    async def list(
        self, bot: BotRef, status: str | None = None, limit: int = 100
    ) -> list[dict[str, Any]]:
        if status and status not in TASK_STATUSES:
            raise Invalid("Status must be one of: " + ", ".join(TASK_STATUSES))
        return await self.db.list_tasks(await self.bot_id(bot), status=status or None, limit=limit)

    async def get(self, bot: BotRef, task_id: int) -> dict[str, Any]:
        task = await self.db.get_task(int(task_id), await self.bot_id(bot))
        if not task:
            raise NotFound("Task not found")
        return task

    async def cancel(self, bot: BotRef, task_id: int) -> bool:
        """Cancel a queued or running task. False if it had already finished."""
        task = await self.get(bot, task_id)
        return await self.o.tasks.cancel(task["id"])

    async def retry(self, bot: BotRef, task_id: int) -> int:
        """Queue the task again with the same code, module versions and arguments."""
        return await self.o.tasks.retry(await self.bot_id(bot), int(task_id))
