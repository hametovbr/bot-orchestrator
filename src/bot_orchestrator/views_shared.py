"""Pages shared by all bots: modules and packages."""

from typing import Annotated, Any

from fastapi import Body, Depends, FastAPI, Form, Request

from .ops import Operations
from .ui import FAILED, Ui


def register(app: FastAPI, ui: Ui) -> None:
    Ops = Annotated[Operations, Depends(ui.ops)]

    # Modules

    @app.get("/modules")
    async def modules_page(request: Request, ops: Ops):
        return ui.render(request, "modules.html", modules=await ops.modules.list())

    @app.post("/modules")
    async def create_module(request: Request, ops: Ops, name: Annotated[str, Form()]):
        created = await ui.act(request, ops.modules.create(name))
        if created is FAILED:
            return ui.back("/modules")
        ui.flash(
            request,
            f"Module {created['name']} created. Import it with: from shared import "
            f"{created['name']}",
        )
        return ui.back(f"/modules/{created['id']}")

    @app.get("/modules-hints.json")
    async def module_hints(ops: Ops):
        return await ops.modules.hints()

    @app.get("/modules/{module_id}")
    async def module_page(request: Request, ops: Ops, module_id: int, version: int | None = None):
        module = await ops.modules.get(module_id, version)
        handlers = await ops.orchestrator.db.active_handler_code()
        using = {u["url"] for u in module["users"]}
        # Handlers that import this module come first in the test-run list.
        handlers.sort(
            key=lambda h: (f"/bots/{h['bot_id']}/handlers/{h['handler_id']}" not in using,)
        )
        return ui.render(
            request,
            "module.html",
            module=module,
            code=module["code"],
            shown_version=module["shown_version"],
            versions=module["versions"],
            users=module["users"],
            handlers=handlers,
        )

    @app.post("/modules/{module_id}")
    async def save_module(
        request: Request,
        ops: Ops,
        module_id: int,
        code: Annotated[str, Form()],
        comment: Annotated[str, Form()] = "",
    ):
        saved = await ui.act(request, ops.modules.save(module_id, code, comment))
        if saved is not FAILED:
            ui.flash(
                request,
                f"Saved and activated version {saved['version']}. New runs of every handler "
                f"that imports shared.{saved['name']} use it.",
                "success",
            )
            ui.flash_warnings(request, saved)
        return ui.back(f"/modules/{module_id}")

    @app.post("/modules/{module_id}/activate")
    async def activate_module(
        request: Request, ops: Ops, module_id: int, version: Annotated[int, Form()]
    ):
        await ops.modules.activate(module_id, version)
        ui.flash(request, f"Version {version} is active.", "success")
        return ui.back(f"/modules/{module_id}")

    @app.post("/modules/{module_id}/delete")
    async def delete_module(request: Request, ops: Ops, module_id: int):
        module = await ui.act(request, ops.modules.delete(module_id))
        if module is FAILED:
            return ui.back(f"/modules/{module_id}")
        ui.flash(request, f"Module {module['name']} deleted.", "success")
        return ui.back("/modules")

    @app.post("/modules/{module_id}/test")
    async def test_module(ops: Ops, module_id: int, body: Annotated[dict[str, Any], Body()]):
        """Run a handler with the module code from the editor instead of the saved one."""
        return await ops.modules.test(
            module_id,
            int(body.get("bot_id") or 0),
            int(body.get("handler_id") or 0),
            str(body.get("code", "")),
            update_row=int(body["update_row"]) if body.get("update_row") else None,
            update=body.get("update_json") or "",
            dry_run=bool(body.get("dry_run", True)),
        )

    @app.get("/bots/{bot_id}/updates.json")
    async def updates_json(ops: Ops, bot_id: int, limit: int = 30):
        rows = await ops.bots.updates(bot_id, limit=min(limit, 100))
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
    async def packages_page(request: Request, ops: Ops):
        return ui.render(request, "packages.html", p=await ops.modules.packages())

    @app.post("/packages")
    async def save_packages(request: Request, ops: Ops, requirements: Annotated[str, Form()] = ""):
        if await ui.act(request, ops.modules.set_packages(requirements)) is not FAILED:
            ui.flash(request, "Saved. bot-runner is installing the packages.", "success")
        return ui.back("/packages")
