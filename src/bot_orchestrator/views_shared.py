"""Pages shared by all bots: modules and packages."""

import json
from collections import defaultdict
from typing import Annotated, Any

from fastapi import Body, Depends, FastAPI, Form, HTTPException, Request

from . import modules as shared
from .packages import PackagesError
from .service import Orchestrator
from .ui import Ui


async def module_usage(o: Orchestrator) -> dict[str, list[dict[str, str]]]:
    """For each module name, the handlers and modules that import it (active versions)."""
    users: dict[str, list[dict[str, str]]] = defaultdict(list)
    for h in await o.db.active_handler_code():
        for name in sorted(shared.shared_imports(h["code"])):
            users[name].append(
                {
                    "label": f"@{h['bot_username']} · {h['handler_name']}",
                    "url": f"/bots/{h['bot_id']}/handlers/{h['handler_id']}",
                }
            )
    ids = {m["name"]: m["id"] for m in await o.db.list_modules()}
    for name, module in (await o.db.active_modules()).items():
        for used in sorted(shared.shared_imports(module["code"])):
            if used != name:
                users[used].append({"label": f"shared.{name}", "url": f"/modules/{ids[name]}"})
    return users


async def unknown_imports(o: Orchestrator, code: str, own_name: str | None = None) -> list[str]:
    existing = set((await o.db.active_modules()).keys())
    return sorted(n for n in shared.shared_imports(code) if n not in existing and n != own_name)


def register(app: FastAPI, ui: Ui) -> None:
    Orch = Annotated[Orchestrator, Depends(ui.orch)]

    async def module_or_404(module_id: int) -> dict[str, Any]:
        module = await ui.orch().db.get_module(module_id)
        if not module:
            raise HTTPException(status_code=404, detail="Module not found")
        return module

    # Modules

    @app.get("/modules")
    async def modules_page(request: Request, o: Orch):
        usage = await module_usage(o)
        rows = await o.db.list_modules()
        active = await o.db.active_modules()
        for row in rows:
            row["users"] = usage.get(row["name"], [])
            row["doc"] = shared.module_doc(active.get(row["name"], {}).get("code", ""))
        return ui.render(request, "modules.html", modules=rows)

    @app.post("/modules")
    async def create_module(request: Request, o: Orch, name: Annotated[str, Form()]):
        name = name.strip()
        problem = shared.check_name(name)
        if problem:
            ui.flash(request, problem, "error")
            return ui.back("/modules")
        if await o.db.get_module_by_name(name):
            ui.flash(request, f"A module called {name} already exists.", "error")
            return ui.back("/modules")
        module_id = await o.db.create_module(name, shared.NEW_MODULE.format(name=name), "Created")
        ui.flash(request, f"Module {name} created. Import it with: from shared import {name}")
        return ui.back(f"/modules/{module_id}")

    @app.get("/modules-hints.json")
    async def module_hints(o: Orch):
        return {
            name: {"doc": shared.module_doc(m["code"]), "items": shared.outline(m["code"])}
            for name, m in (await o.db.active_modules()).items()
        }

    @app.get("/modules/{module_id}")
    async def module_page(request: Request, o: Orch, module_id: int, version: int | None = None):
        module = await module_or_404(module_id)
        shown = version or module["active_version"]
        row = await o.db.get_module_version(module_id, shown)
        handlers = await o.db.active_handler_code()
        users = (await module_usage(o)).get(module["name"], [])
        using = {u["url"] for u in users}
        # Handlers that import this module come first in the test-run list.
        handlers.sort(
            key=lambda h: (f"/bots/{h['bot_id']}/handlers/{h['handler_id']}" not in using,)
        )
        return ui.render(
            request,
            "module.html",
            module=module,
            code=row["code"] if row else "",
            shown_version=row["version"] if row else None,
            versions=await o.db.list_module_versions(module_id),
            users=users,
            handlers=handlers,
        )

    @app.post("/modules/{module_id}")
    async def save_module(
        request: Request,
        o: Orch,
        module_id: int,
        code: Annotated[str, Form()],
        comment: Annotated[str, Form()] = "",
    ):
        module = await module_or_404(module_id)
        url = f"/modules/{module_id}"
        try:
            compile(code, f"<shared.{module['name']}>", "exec")
        except SyntaxError as exc:
            ui.flash(request, f"Not saved: syntax error on line {exc.lineno}: {exc.msg}", "error")
            return ui.back(url)
        version = await o.db.save_module_version(module_id, code, comment.strip()[:200])
        ui.flash(
            request,
            f"Saved and activated version {version}. New runs of every handler that imports "
            f"shared.{module['name']} use it.",
            "success",
        )
        missing = await unknown_imports(o, code, module["name"])
        if missing:
            ui.flash(request, "No such modules yet: " + ", ".join(missing), "warning")
        return ui.back(url)

    @app.post("/modules/{module_id}/activate")
    async def activate_module(
        request: Request, o: Orch, module_id: int, version: Annotated[int, Form()]
    ):
        await module_or_404(module_id)
        if not await o.db.get_module_version(module_id, version):
            raise HTTPException(status_code=404, detail="Version not found")
        await o.db.activate_module_version(module_id, version)
        ui.flash(request, f"Version {version} is active.", "success")
        return ui.back(f"/modules/{module_id}")

    @app.post("/modules/{module_id}/delete")
    async def delete_module(request: Request, o: Orch, module_id: int):
        module = await module_or_404(module_id)
        users = (await module_usage(o)).get(module["name"], [])
        if users:
            ui.flash(
                request,
                "Still imported by " + ", ".join(u["label"] for u in users) + ". Remove the "
                "imports first.",
                "error",
            )
            return ui.back(f"/modules/{module_id}")
        await o.db.delete_module(module_id)
        ui.flash(request, f"Module {module['name']} deleted.", "success")
        return ui.back("/modules")

    @app.post("/modules/{module_id}/test")
    async def test_module(o: Orch, module_id: int, body: Annotated[dict[str, Any], Body()]):
        """Run a handler with the module code from the editor instead of the saved one."""
        module = await module_or_404(module_id)
        bot_id = int(body.get("bot_id") or 0)
        handler_id = int(body.get("handler_id") or 0)
        bot = await ui.bot(bot_id)
        handler = await ui.handler(bot_id, handler_id)
        active = await o.db.active_code(handler_id)
        if not active:
            raise HTTPException(status_code=400, detail="The handler has no active version")
        origin: dict[str, Any] = {}
        if body.get("update_row"):
            row = await o.db.get_update(bot_id, int(body["update_row"]))
            if not row:
                raise HTTPException(status_code=404, detail="Update not found")
            update, origin = row["raw"], {"update_row": row["id"]}
        else:
            try:
                update = json.loads(body.get("update_json") or "")
            except ValueError:
                raise HTTPException(status_code=400, detail="Update JSON is not valid") from None
            if not isinstance(update, dict):
                raise HTTPException(status_code=400, detail="Update must be a JSON object")
        modules = await o.db.active_modules()
        modules[module["name"]] = {"version": 0, "code": str(body.get("code", ""))}
        return await o.call_runner(
            bot,
            code=active["code"],
            version=active["version"],
            update=update,
            handler={"id": handler_id, "name": handler["name"], "version": active["version"]},
            dry_run=bool(body.get("dry_run", True)),
            origin=origin,
            modules=modules,
        )

    @app.get("/bots/{bot_id}/updates.json")
    async def updates_json(o: Orch, bot_id: int, limit: int = 30):
        await ui.bot(bot_id)
        rows = await o.db.list_updates(bot_id, limit=min(limit, 100))
        return [
            {
                "id": r["id"],
                "label": " · ".join(
                    [ui._time(r["received_at"]), r["kind"], (r["summary"] or "")[:60]]
                ),
            }
            for r in rows
        ]

    # Packages

    @app.get("/packages")
    async def packages_page(request: Request, o: Orch):
        status = await o.packages.status()
        return ui.render(request, "packages.html", p=status)

    @app.post("/packages")
    async def save_packages(request: Request, o: Orch, requirements: Annotated[str, Form()] = ""):
        try:
            await o.packages.save_and_install(requirements)
        except PackagesError as exc:
            ui.flash(request, f"Not installed: {exc}", "error")
            return ui.back("/packages")
        ui.flash(request, "Saved. bot-runner is installing the packages.", "success")
        return ui.back("/packages")
