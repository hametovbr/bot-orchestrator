"""Routes: which handler runs for which update, and the bot's command menu."""

from __future__ import annotations

from typing import Any

from .. import routing
from ..errors import Invalid, NotFound, Unavailable
from ..telegram import TelegramError
from .base import Base, BotRef, NameRef

_UNSET: Any = object()


class Routes(Base):
    async def list(self, bot: BotRef) -> list[dict[str, Any]]:
        """Routes in the order they are checked; the first enabled match wins."""
        return await self.db.list_routes(await self.bot_id(bot))

    async def get(self, bot: BotRef, route_id: int) -> dict[str, Any]:
        route = await self.db.get_route(await self.bot_id(bot), int(route_id))
        if not route:
            raise NotFound("Route not found")
        return route

    async def _validate(
        self,
        bot_id: int,
        kind: str,
        pattern: str,
        handler: NameRef | None,
        description: str,
        enabled: bool,
    ) -> dict[str, Any]:
        try:
            pattern = routing.validate(kind, pattern or "")
        except ValueError as exc:
            raise Invalid(str(exc)) from None
        if handler in (None, ""):
            raise Invalid("Choose a handler.")
        try:
            handler_row = await self.handler_row(bot_id, handler)
        except NotFound:
            raise Invalid("Choose a handler.") from None
        return {
            "kind": kind,
            "pattern": pattern,
            "description": (description or "").strip()[:256],
            "handler_id": handler_row["id"],
            "enabled": bool(enabled),
        }

    async def create(
        self,
        bot: BotRef,
        kind: str,
        pattern: str,
        handler: NameRef | None,
        description: str = "",
        enabled: bool = True,
        position: int | None = None,
    ) -> dict[str, Any]:
        """Add a route at the end, or at `position` (1 is checked first)."""
        bot_id = await self.bot_id(bot)
        data = await self._validate(bot_id, kind, pattern, handler, description, enabled)
        route_id = await self.db.create_route(bot_id, **data)
        if position is not None:
            await self.move(bot_id, route_id, position)
        return {**data, "id": route_id, "text": routing.describe(data)}

    async def update(
        self,
        bot: BotRef,
        route_id: int,
        *,
        kind: str = _UNSET,
        pattern: str = _UNSET,
        handler: NameRef | None = _UNSET,
        description: str = _UNSET,
        enabled: bool = _UNSET,
    ) -> dict[str, Any]:
        """Change some fields of a route; the others keep their values."""
        bot_id = await self.bot_id(bot)
        current = await self.get(bot_id, route_id)

        def pick(value: Any, key: str) -> Any:
            return current[key] if value is _UNSET else value

        if kind is not _UNSET and kind != current["kind"] and pattern is _UNSET:
            pattern = ""
        data = await self._validate(
            bot_id,
            pick(kind, "kind"),
            pick(pattern, "pattern"),
            current["handler_id"] if handler is _UNSET else handler,
            pick(description, "description"),
            pick(enabled, "enabled"),
        )
        await self.db.update_route(int(route_id), **data)
        return {**data, "id": int(route_id), "text": routing.describe(data)}

    async def set_enabled(self, bot: BotRef, route_id: int, enabled: bool | None = None) -> bool:
        """Turn a route on or off (toggle when `enabled` is None). Returns the new state."""
        route = await self.get(bot, route_id)
        state = (not route["enabled"]) if enabled is None else bool(enabled)
        await self.db.set_route_enabled(route["id"], state)
        return state

    async def delete(self, bot: BotRef, route_id: int) -> None:
        route = await self.get(bot, route_id)
        await self.db.delete_route(route["id"])

    async def reorder(self, bot: BotRef, route_ids: list[int]) -> list[dict[str, Any]]:
        """Check routes in this order; routes not listed keep their relative order after them."""
        if not isinstance(route_ids, list) or not all(
            isinstance(i, int) and not isinstance(i, bool) for i in route_ids
        ):
            raise Invalid("ids must be a list of route ids")
        bot_id = await self.bot_id(bot)
        await self.db.reorder_routes(bot_id, route_ids)
        return await self.db.list_routes(bot_id)

    async def move(self, bot: BotRef, route_id: int, position: int) -> list[dict[str, Any]]:
        """Move one route to a 1-based position."""
        bot_id = await self.bot_id(bot)
        await self.get(bot_id, route_id)
        ids = [r["id"] for r in await self.db.list_routes(bot_id) if r["id"] != route_id]
        index = min(max(int(position), 1), len(ids) + 1) - 1
        ids.insert(index, int(route_id))
        return await self.reorder(bot_id, ids)

    async def match(
        self,
        bot: BotRef,
        update_row: int | None = None,
        update: dict[str, Any] | str | None = None,
    ) -> dict[str, Any] | None:
        """The route that would handle a logged update or the given update JSON."""
        bot_row = await self.bot_row(bot)
        sample, _ = await self.sample_update(bot_row["id"], update_row, update)
        return await self.o.match_route(bot_row, sample)

    def menu_commands(self, routes: list[dict[str, Any]]) -> list[dict[str, str]]:
        commands: list[dict[str, str]] = []
        for route in routes:
            if route["kind"] == "command" and route["enabled"]:
                if route["pattern"] in {c["command"] for c in commands}:
                    continue
                description = route["description"] or route["handler_name"]
                commands.append({"command": route["pattern"], "description": description[:256]})
        return commands

    async def publish_commands(self, bot: BotRef) -> list[dict[str, str]]:
        """Set the bot menu (setMyCommands) from the enabled command routes.

        A route's description becomes the menu text (the handler name if empty).
        """
        bot_row = await self.bot_row(bot)
        commands = self.menu_commands(await self.db.list_routes(bot_row["id"]))
        try:
            await self.o.local_api(self.o.token_of(bot_row)).call(
                "setMyCommands", {"commands": commands}
            )
        except TelegramError as exc:
            raise Unavailable(f"setMyCommands failed: {exc.description}") from None
        return commands

    async def published_commands(self, bot: BotRef) -> list[dict[str, str]]:
        """The bot menu as Telegram has it now (getMyCommands)."""
        bot_row = await self.bot_row(bot)
        try:
            return await self.o.local_api(self.o.token_of(bot_row)).call("getMyCommands")
        except TelegramError as exc:
            raise Unavailable(f"getMyCommands failed: {exc.description}") from None

    @staticmethod
    def kinds() -> list[dict[str, str]]:
        return [
            {"kind": kind, "title": title, "pattern": routing.PATTERN_HELP[kind]}
            for kind, title in routing.ROUTE_KINDS.items()
        ]
