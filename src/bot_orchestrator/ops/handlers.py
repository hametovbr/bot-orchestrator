"""Handlers: code with versions, test runs in the runner."""

from __future__ import annotations

import re
from typing import Any

from .. import routing
from ..errors import Conflict, Invalid, NotFound
from ..handler_templates import TEMPLATES
from .base import Base, BotRef, NameRef, check_syntax, comment_text
from .modules import unknown_imports

HANDLER_NAME = re.compile(r"^[A-Za-z0-9_-]{1,40}$")


def check_name(name: str) -> str:
    name = name.strip()
    if not HANDLER_NAME.match(name):
        raise Invalid("Handler names use 1-40 letters, digits, - and _ (for example save_files).")
    return name


def missing_modules_warning(missing: list[str]) -> list[str]:
    if not missing:
        return []
    return [
        "No such shared modules yet: " + ", ".join(missing) + ". Create them on the Modules page."
    ]


class Handlers(Base):
    async def list(self, bot: BotRef) -> list[dict[str, Any]]:
        return await self.db.list_handlers(await self.bot_id(bot))

    async def get(self, bot: BotRef, handler: NameRef, version: int | None = None):
        """A handler with the code of one version (the active one by default), its version
        history, and the routes and schedules that run it."""
        bot_id = await self.bot_id(bot)
        row = await self.handler_row(bot_id, handler)
        shown = version or row["active_version"]
        code_row = await self.db.get_version(row["id"], shown) if shown else None
        if version and not code_row:
            raise NotFound("Version not found")
        return {
            **row,
            "code": code_row["code"] if code_row else "",
            "shown_version": code_row["version"] if code_row else None,
            "versions": await self.db.list_versions(row["id"]),
            "routes": [
                r for r in await self.db.list_routes(bot_id) if r["handler_id"] == row["id"]
            ],
            "schedules": [
                s for s in await self.db.list_schedules(bot_id) if s["handler_id"] == row["id"]
            ],
        }

    async def create(
        self,
        bot: BotRef,
        name: str,
        *,
        code: str | None = None,
        template: str = "echo",
        comment: str | None = None,
        add_route: bool = False,
    ) -> dict[str, Any]:
        """Create a handler from code or from a template; version 1 is active at once.

        With `add_route`, the template's suggested route is added too (templates only).
        Returns `id`, `name`, `version`, `route` (or None) and `warnings`.
        """
        bot_id = await self.bot_id(bot)
        name = check_name(name)
        if await self.db.get_handler_by_name(bot_id, name):
            raise Conflict(f"A handler called {name} already exists.")
        chosen = TEMPLATES.get(template, TEMPLATES["echo"])
        warnings: list[str] = []
        if code is None:
            code = chosen["code"]
            comment = comment or f"Created from “{chosen['title']}”"
        else:
            check_syntax(code, "<handler>")
            warnings = missing_modules_warning(await unknown_imports(self.db, code))
        handler_id = await self.db.create_handler(bot_id, name, code, comment_text(comment))
        route = None
        if add_route and chosen["route"]:
            kind, pattern = chosen["route"]
            route_id = await self.db.create_route(bot_id, kind, pattern, "", handler_id)
            route = {"id": route_id, "kind": kind, "pattern": pattern}
        return {"id": handler_id, "name": name, "version": 1, "route": route, "warnings": warnings}

    async def save(
        self, bot: BotRef, handler: NameRef, code: str, comment: str | None = None
    ) -> dict[str, Any]:
        """Save the code as a new version and activate it. Returns `version` and `warnings`."""
        row = await self.handler_row(await self.bot_id(bot), handler)
        check_syntax(code, "<handler>")
        version = await self.db.save_version(row["id"], code, comment_text(comment))
        warnings = missing_modules_warning(await unknown_imports(self.db, code))
        return {"id": row["id"], "name": row["name"], "version": version, "warnings": warnings}

    async def activate(self, bot: BotRef, handler: NameRef, version: int) -> None:
        """Make an older (or newer) saved version the active one."""
        row = await self.handler_row(await self.bot_id(bot), handler)
        if not await self.db.get_version(row["id"], version):
            raise NotFound("Version not found")
        await self.db.activate_version(row["id"], version)

    async def rename(self, bot: BotRef, handler: NameRef, name: str) -> bool:
        """Rename a handler; False if the name is unchanged."""
        bot_id = await self.bot_id(bot)
        row = await self.handler_row(bot_id, handler)
        name = name.strip()
        if name == row["name"]:
            return False
        name = check_name(name)
        if await self.db.get_handler_by_name(bot_id, name):
            raise Conflict(f"A handler called {name} already exists.")
        await self.db.rename_handler(row["id"], name)
        return True

    async def delete(self, bot: BotRef, handler: NameRef) -> dict[str, Any]:
        """Delete a handler together with its routes and schedules."""
        row = await self.handler_row(await self.bot_id(bot), handler)
        await self.db.delete_handler(row["id"])
        return row

    async def test(
        self,
        bot: BotRef,
        handler: NameRef,
        *,
        code: str | None = None,
        update_row: int | None = None,
        update: dict[str, Any] | str | None = None,
        dry_run: bool = True,
    ) -> dict[str, Any]:
        """Run handler code (the given code, or the active version) in bot-runner once.

        With `dry_run`, Bot API calls that change something are recorded, not sent.
        Returns the runner result: status, error, logs, api_calls, duration_ms.
        """
        bot_row = await self.bot_row(bot)
        row = await self.handler_row(bot_row["id"], handler)
        sample, origin = await self.sample_update(bot_row["id"], update_row, update)
        version = 0
        if code is None:
            active = await self.db.active_code(row["id"])
            if not active:
                raise Invalid("The handler has no active version")
            code, version = active["code"], active["version"]
        return await self.o.call_runner(
            bot_row,
            code=str(code),
            version=version,
            update=sample,
            handler={"id": row["id"], "name": row["name"], "version": version},
            dry_run=dry_run,
            origin=origin,
        )

    @staticmethod
    def templates() -> list[dict[str, Any]]:
        return [
            {
                "name": key,
                "title": t["title"],
                "route": routing.describe({"kind": t["route"][0], "pattern": t["route"][1]})
                if t["route"]
                else None,
                "code": t["code"],
            }
            for key, t in TEMPLATES.items()
        ]
