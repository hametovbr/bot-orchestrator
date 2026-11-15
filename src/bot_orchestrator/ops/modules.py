"""Shared modules (code every handler imports as `shared.<name>`) and extra packages."""

from __future__ import annotations

from collections import defaultdict
from typing import TYPE_CHECKING, Any

from .. import modules as shared
from ..errors import Conflict, Invalid, NotFound
from ..packages import PackagesError
from .base import Base, BotRef, NameRef, as_id, check_syntax, comment_text

if TYPE_CHECKING:
    from ..db import Database


async def unknown_imports(db: Database, code: str, own_name: str | None = None) -> list[str]:
    """Shared modules the code imports that do not exist."""
    existing = set((await db.active_modules()).keys())
    return sorted(n for n in shared.shared_imports(code) if n not in existing and n != own_name)


class Modules(Base):
    async def usage(self) -> dict[str, list[dict[str, Any]]]:
        """For each module name, the handlers and modules that import it (active versions)."""
        users: dict[str, list[dict[str, Any]]] = defaultdict(list)
        for h in await self.db.active_handler_code():
            for name in sorted(shared.shared_imports(h["code"])):
                users[name].append(
                    {
                        "kind": "handler",
                        "bot_id": h["bot_id"],
                        "handler_id": h["handler_id"],
                        "label": f"@{h['bot_username']} · {h['handler_name']}",
                        "url": f"/bots/{h['bot_id']}/handlers/{h['handler_id']}",
                    }
                )
        ids = {m["name"]: m["id"] for m in await self.db.list_modules()}
        for name, module in (await self.db.active_modules()).items():
            for used in sorted(shared.shared_imports(module["code"])):
                if used != name:
                    users[used].append(
                        {
                            "kind": "module",
                            "module_id": ids[name],
                            "label": f"shared.{name}",
                            "url": f"/modules/{ids[name]}",
                        }
                    )
        return users

    async def module_row(self, ref: NameRef) -> dict[str, Any]:
        module_id = as_id(ref)
        if module_id is not None:
            module = await self.db.get_module(module_id)
        else:
            module = await self.db.get_module_by_name(str(ref).strip())
        if not module:
            raise NotFound("Module not found")
        return module

    async def list(self) -> list[dict[str, Any]]:
        """Every module with its docstring and who imports it."""
        usage = await self.usage()
        rows = await self.db.list_modules()
        active = await self.db.active_modules()
        for row in rows:
            row["users"] = usage.get(row["name"], [])
            row["doc"] = shared.module_doc(active.get(row["name"], {}).get("code", ""))
        return rows

    async def get(self, ref: NameRef, version: int | None = None) -> dict[str, Any]:
        module = await self.module_row(ref)
        shown = version or module["active_version"]
        row = await self.db.get_module_version(module["id"], shown)
        if version and not row:
            raise NotFound("Version not found")
        code = row["code"] if row else ""
        return {
            **module,
            "code": code,
            "shown_version": row["version"] if row else None,
            "versions": await self.db.list_module_versions(module["id"]),
            "users": (await self.usage()).get(module["name"], []),
            "outline": shared.outline(code),
        }

    async def hints(self) -> dict[str, dict[str, Any]]:
        """Docstrings and function signatures of every active module."""
        return {
            name: {"doc": shared.module_doc(m["code"]), "items": shared.outline(m["code"])}
            for name, m in (await self.db.active_modules()).items()
        }

    async def create(
        self, name: str, code: str | None = None, comment: str | None = None
    ) -> dict[str, Any]:
        name = name.strip()
        problem = shared.check_name(name)
        if problem:
            raise Invalid(problem)
        if await self.db.get_module_by_name(name):
            raise Conflict(f"A module called {name} already exists.")
        warnings: list[str] = []
        if code is None:
            code = shared.NEW_MODULE.format(name=name)
        else:
            check_syntax(code, f"<shared.{name}>")
            missing = await unknown_imports(self.db, code, name)
            if missing:
                warnings.append("No such modules yet: " + ", ".join(missing))
        module_id = await self.db.create_module(name, code, comment_text(comment) or "Created")
        return {"id": module_id, "name": name, "version": 1, "warnings": warnings}

    async def save(self, ref: NameRef, code: str, comment: str | None = None) -> dict[str, Any]:
        """Save and activate a new version; new runs of every importing handler use it."""
        module = await self.module_row(ref)
        check_syntax(code, f"<shared.{module['name']}>")
        version = await self.db.save_module_version(module["id"], code, comment_text(comment))
        missing = await unknown_imports(self.db, code, module["name"])
        warnings = ["No such modules yet: " + ", ".join(missing)] if missing else []
        return {
            "id": module["id"],
            "name": module["name"],
            "version": version,
            "warnings": warnings,
        }

    async def activate(self, ref: NameRef, version: int) -> None:
        module = await self.module_row(ref)
        if not await self.db.get_module_version(module["id"], version):
            raise NotFound("Version not found")
        await self.db.activate_module_version(module["id"], version)

    async def delete(self, ref: NameRef) -> dict[str, Any]:
        """Delete a module; refused while an active handler or module imports it."""
        module = await self.module_row(ref)
        users = (await self.usage()).get(module["name"], [])
        if users:
            raise Conflict(
                "Still imported by "
                + ", ".join(u["label"] for u in users)
                + ". Remove the imports first."
            )
        await self.db.delete_module(module["id"])
        return module

    async def test(
        self,
        ref: NameRef,
        bot: BotRef,
        handler: NameRef,
        code: str,
        *,
        update_row: int | None = None,
        update: dict[str, Any] | str | None = None,
        dry_run: bool = True,
    ) -> dict[str, Any]:
        """Run a handler's active version with unsaved code for this module."""
        module = await self.module_row(ref)
        bot_row = await self.bot_row(bot)
        handler_row = await self.handler_row(bot_row["id"], handler)
        active = await self.db.active_code(handler_row["id"])
        if not active:
            raise Invalid("The handler has no active version")
        sample, origin = await self.sample_update(bot_row["id"], update_row, update)
        modules = await self.db.active_modules()
        modules[module["name"]] = {"version": 0, "code": str(code)}
        return await self.o.call_runner(
            bot_row,
            code=active["code"],
            version=active["version"],
            update=sample,
            handler={
                "id": handler_row["id"],
                "name": handler_row["name"],
                "version": active["version"],
            },
            dry_run=dry_run,
            origin=origin,
            modules=modules,
        )

    # Packages

    async def packages(self) -> dict[str, Any]:
        """The saved package list and what bot-runner has installed."""
        return await self.o.packages.status()

    async def set_packages(self, requirements: str) -> dict[str, Any]:
        """Save the requirements list and ask bot-runner to install it (in the background)."""
        try:
            return await self.o.packages.save_and_install(requirements)
        except PackagesError as exc:
            raise Invalid(f"Not installed: {exc}") from None
