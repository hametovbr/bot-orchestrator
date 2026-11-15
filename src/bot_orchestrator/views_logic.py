"""Per-bot logic pages: handlers, routes, background tasks and schedules.

The pages only translate between HTML forms and operations (`ops`); the rules live there.
"""

from typing import Annotated, Any

from fastapi import Body, Depends, FastAPI, Form, Request

from . import routing
from .errors import Invalid, NotFound
from .handler_templates import TEMPLATES
from .ops import Operations
from .ops.schedules import TASK_STATUSES
from .ui import FAILED, Ui


def register(app: FastAPI, ui: Ui) -> None:
    Ops = Annotated[Operations, Depends(ui.ops)]

    # Handlers

    @app.get("/bots/{bot_id}/handler")
    async def old_handler_url(bot_id: int):
        return ui.back(f"/bots/{bot_id}/handlers")

    @app.get("/bots/{bot_id}/handlers")
    async def handlers_page(request: Request, ops: Ops, bot_id: int):
        bot = await ui.bot(bot_id)
        return ui.render(
            request,
            "handlers.html",
            bot=bot,
            tab="handlers",
            handlers=await ops.handlers.list(bot_id),
            templates=TEMPLATES,
        )

    @app.post("/bots/{bot_id}/handlers")
    async def create_handler(
        request: Request,
        ops: Ops,
        bot_id: int,
        name: Annotated[str, Form()],
        template: Annotated[str, Form()] = "echo",
        add_route: Annotated[bool, Form()] = False,
    ):
        created = await ui.act(
            request, ops.handlers.create(bot_id, name, template=template, add_route=add_route)
        )
        if created is FAILED:
            return ui.back(f"/bots/{bot_id}/handlers")
        message = f"Handler {created['name']} created."
        if created["route"]:
            message += f" Route added: {routing.describe(created['route'])}."
        ui.flash(request, message, "success")
        return ui.back(f"/bots/{bot_id}/handlers/{created['id']}")

    @app.get("/bots/{bot_id}/handlers/{handler_id}")
    async def handler_page(
        request: Request,
        ops: Ops,
        bot_id: int,
        handler_id: int,
        version: int | None = None,
        update: int | None = None,
    ):
        bot = await ui.bot(bot_id)
        handler = await ops.handlers.get(bot_id, handler_id, version)
        return ui.render(
            request,
            "handler.html",
            bot=bot,
            tab="handlers",
            handler=handler,
            code=handler["code"],
            shown_version=handler["shown_version"],
            versions=handler["versions"],
            recent=await ops.bots.updates(bot_id, limit=30),
            selected_update=update,
            routes=handler["routes"],
            schedules=handler["schedules"],
        )

    @app.post("/bots/{bot_id}/handlers/{handler_id}")
    async def save_handler(
        request: Request,
        ops: Ops,
        bot_id: int,
        handler_id: int,
        code: Annotated[str, Form()],
        comment: Annotated[str, Form()] = "",
    ):
        saved = await ui.act(request, ops.handlers.save(bot_id, handler_id, code, comment))
        if saved is not FAILED:
            ui.flash(request, f"Saved and activated version {saved['version']}.", "success")
            ui.flash_warnings(request, saved)
        return ui.back(f"/bots/{bot_id}/handlers/{handler_id}")

    @app.post("/bots/{bot_id}/handlers/{handler_id}/activate")
    async def activate_version(
        request: Request,
        ops: Ops,
        bot_id: int,
        handler_id: int,
        version: Annotated[int, Form()],
    ):
        await ops.handlers.activate(bot_id, handler_id, version)
        ui.flash(request, f"Version {version} is active.", "success")
        return ui.back(f"/bots/{bot_id}/handlers/{handler_id}")

    @app.post("/bots/{bot_id}/handlers/{handler_id}/rename")
    async def rename_handler(
        request: Request,
        ops: Ops,
        bot_id: int,
        handler_id: int,
        name: Annotated[str, Form()],
    ):
        if await ui.act(request, ops.handlers.rename(bot_id, handler_id, name)) is True:
            ui.flash(request, f"Renamed to {name.strip()}.", "success")
        return ui.back(f"/bots/{bot_id}/handlers/{handler_id}")

    @app.post("/bots/{bot_id}/handlers/{handler_id}/delete")
    async def delete_handler(request: Request, ops: Ops, bot_id: int, handler_id: int):
        handler = await ops.handlers.delete(bot_id, handler_id)
        ui.flash(
            request, f"Handler {handler['name']} deleted with its routes and schedules.", "success"
        )
        return ui.back(f"/bots/{bot_id}/handlers")

    @app.post("/bots/{bot_id}/handlers/{handler_id}/test")
    async def test_handler(
        ops: Ops, bot_id: int, handler_id: int, body: Annotated[dict[str, Any], Body()]
    ):
        return await ops.handlers.test(
            bot_id,
            handler_id,
            code=str(body.get("code", "")),
            update_row=int(body["update_row"]) if body.get("update_row") else None,
            update=body.get("update_json") or "",
            dry_run=bool(body.get("dry_run", True)),
        )

    # Routes

    async def route_form_data(ops: Operations, bot_id: int) -> dict[str, Any]:
        return {
            "kinds": routing.ROUTE_KINDS,
            "help": routing.PATTERN_HELP,
            "handlers": await ops.handlers.list(bot_id),
        }

    async def route_form(request: Request) -> dict[str, Any]:
        form = await request.form()
        handler_id = str(form.get("handler_id", ""))
        return {
            "kind": str(form.get("kind", "")),
            "pattern": str(form.get("pattern", "")),
            # Only numeric ids come from the form; anything else means "not chosen".
            "handler": int(handler_id) if handler_id.isdigit() else None,
            "description": str(form.get("description", "")),
            "enabled": bool(form.get("enabled")),
        }

    @app.get("/bots/{bot_id}/routes")
    async def routes_page(request: Request, ops: Ops, bot_id: int):
        bot = await ui.bot(bot_id)
        return ui.render(
            request,
            "routes.html",
            bot=bot,
            tab="routes",
            routes=await ops.routes.list(bot_id),
            recent=await ops.bots.updates(bot_id, limit=30),
            **await route_form_data(ops, bot_id),
        )

    @app.post("/bots/{bot_id}/routes")
    async def create_route(request: Request, ops: Ops, bot_id: int):
        await ui.bot(bot_id)
        route = await ui.act(request, ops.routes.create(bot_id, **await route_form(request)))
        if route is not FAILED:
            ui.flash(request, f"Route added: {route['text']}.", "success")
        return ui.back(f"/bots/{bot_id}/routes")

    @app.get("/bots/{bot_id}/routes/{route_id}")
    async def route_page(request: Request, ops: Ops, bot_id: int, route_id: int):
        bot = await ui.bot(bot_id)
        return ui.render(
            request,
            "route_edit.html",
            bot=bot,
            tab="routes",
            route=await ops.routes.get(bot_id, route_id),
            **await route_form_data(ops, bot_id),
        )

    @app.post("/bots/{bot_id}/routes/{route_id}")
    async def update_route(request: Request, ops: Ops, bot_id: int, route_id: int):
        await ops.routes.get(bot_id, route_id)
        result = await ui.act(
            request, ops.routes.update(bot_id, route_id, **await route_form(request))
        )
        if result is FAILED:
            return ui.back(f"/bots/{bot_id}/routes/{route_id}")
        ui.flash(request, "Route saved.", "success")
        return ui.back(f"/bots/{bot_id}/routes")

    @app.post("/bots/{bot_id}/routes/{route_id}/toggle")
    async def toggle_route(ops: Ops, bot_id: int, route_id: int):
        await ops.routes.set_enabled(bot_id, route_id)
        return ui.back(f"/bots/{bot_id}/routes")

    @app.post("/bots/{bot_id}/routes/{route_id}/delete")
    async def delete_route(request: Request, ops: Ops, bot_id: int, route_id: int):
        try:
            await ops.routes.delete(bot_id, route_id)
            ui.flash(request, "Route deleted.", "success")
        except NotFound:
            pass  # Already gone.
        return ui.back(f"/bots/{bot_id}/routes")

    @app.post("/bots/{bot_id}/routes-order")
    async def reorder_routes(ops: Ops, bot_id: int, body: Annotated[dict[str, Any], Body()]):
        await ops.routes.reorder(bot_id, body.get("ids"))
        return {"ok": True}

    @app.post("/bots/{bot_id}/routes-match")
    async def match_route(ops: Ops, bot_id: int, body: Annotated[dict[str, Any], Body()]):
        row_id = int(body.get("update_row") or 0)
        if not row_id:
            raise NotFound("Update not found")
        route = await ops.routes.match(bot_id, update_row=row_id)
        if not route:
            return {"route_id": None, "text": "No enabled route matches this update."}
        return {
            "route_id": route["id"],
            "text": f"Matches “{routing.describe(route)}” → {route['handler_name']}",
        }

    @app.post("/bots/{bot_id}/routes-commands")
    async def publish_commands(request: Request, ops: Ops, bot_id: int):
        commands = await ui.act(request, ops.routes.publish_commands(bot_id))
        if commands is not FAILED:
            ui.flash(
                request,
                f"The bot menu now has {len(commands)} commands."
                if commands
                else "The bot menu was cleared: there are no enabled command routes.",
                "success",
            )
        return ui.back(f"/bots/{bot_id}/routes")

    # Background tasks

    @app.get("/bots/{bot_id}/tasks")
    async def tasks_page(request: Request, ops: Ops, bot_id: int, status: str | None = None):
        bot = await ui.bot(bot_id)
        rows = await ops.tasks.list(bot_id, status=status or None)
        active = sum(1 for t in rows if t["status"] in ("queued", "running"))
        return ui.render(
            request,
            "tasks.html",
            bot=bot,
            tab="tasks",
            tasks=rows,
            status=status or "",
            statuses=list(TASK_STATUSES),
            active=active,
        )

    @app.get("/bots/{bot_id}/tasks/{task_id}")
    async def task_page(request: Request, ops: Ops, bot_id: int, task_id: int):
        bot = await ui.bot(bot_id)
        task = await ops.tasks.get(bot_id, task_id)
        return ui.render(request, "task.html", bot=bot, tab="tasks", t=task)

    @app.post("/bots/{bot_id}/tasks/{task_id}/cancel")
    async def cancel_task(request: Request, ops: Ops, bot_id: int, task_id: int):
        if await ops.tasks.cancel(bot_id, task_id):
            ui.flash(request, "Task cancelled.", "success")
        else:
            ui.flash(request, "The task has already finished.")
        return ui.back(f"/bots/{bot_id}/tasks/{task_id}")

    @app.post("/bots/{bot_id}/tasks/{task_id}/retry")
    async def retry_task(request: Request, ops: Ops, bot_id: int, task_id: int):
        new_id = await ops.tasks.retry(bot_id, task_id)
        ui.flash(request, f"Started again as task {new_id}.", "success")
        return ui.back(f"/bots/{bot_id}/tasks/{new_id}")

    # Schedules

    async def schedule_form(request: Request) -> dict[str, Any]:
        form = await request.form()
        handler_id = str(form.get("handler_id", ""))
        return {
            "name": str(form.get("name", "")),
            "cron_expression": str(form.get("cron", "")),
            "handler": int(handler_id) if handler_id.isdigit() else None,
            "chat_id": str(form.get("chat_id", "")),
            "enabled": bool(form.get("enabled")),
        }

    async def schedule_form_data(ops: Operations, bot_id: int) -> dict[str, Any]:
        return {
            "handlers": await ops.handlers.list(bot_id),
            "senders": await ops.bots.recent_senders(),
            "timezone": ui.settings.tz,
        }

    @app.get("/bots/{bot_id}/schedules")
    async def schedules_page(request: Request, ops: Ops, bot_id: int):
        bot = await ui.bot(bot_id)
        return ui.render(
            request,
            "schedules.html",
            bot=bot,
            tab="schedules",
            schedules=await ops.schedules.list(bot_id),
            **await schedule_form_data(ops, bot_id),
        )

    @app.get("/bots/{bot_id}/schedules-preview")
    async def preview_schedule(ops: Ops, bot_id: int, expression: str = ""):
        try:
            return {"ok": True, "runs": ops.schedules.preview(expression)}
        except Invalid as exc:
            return {"ok": False, "error": str(exc)}

    @app.post("/bots/{bot_id}/schedules")
    async def create_schedule(request: Request, ops: Ops, bot_id: int):
        await ui.bot(bot_id)
        created = await ui.act(
            request, ops.schedules.create(bot_id, **await schedule_form(request))
        )
        if created is not FAILED:
            ui.flash(request, f"Schedule {created['name']} added.", "success")
        return ui.back(f"/bots/{bot_id}/schedules")

    @app.get("/bots/{bot_id}/schedules/{schedule_id}")
    async def schedule_page(request: Request, ops: Ops, bot_id: int, schedule_id: int):
        bot = await ui.bot(bot_id)
        return ui.render(
            request,
            "schedule_edit.html",
            bot=bot,
            tab="schedules",
            schedule=await ops.schedules.get(bot_id, schedule_id),
            **await schedule_form_data(ops, bot_id),
        )

    @app.post("/bots/{bot_id}/schedules/{schedule_id}")
    async def update_schedule(request: Request, ops: Ops, bot_id: int, schedule_id: int):
        await ops.schedules.get(bot_id, schedule_id)
        result = await ui.act(
            request, ops.schedules.update(bot_id, schedule_id, **await schedule_form(request))
        )
        if result is FAILED:
            return ui.back(f"/bots/{bot_id}/schedules/{schedule_id}")
        ui.flash(request, "Schedule saved.", "success")
        return ui.back(f"/bots/{bot_id}/schedules")

    @app.post("/bots/{bot_id}/schedules/{schedule_id}/toggle")
    async def toggle_schedule(ops: Ops, bot_id: int, schedule_id: int):
        await ops.schedules.set_enabled(bot_id, schedule_id)
        return ui.back(f"/bots/{bot_id}/schedules")

    @app.post("/bots/{bot_id}/schedules/{schedule_id}/run")
    async def run_schedule(request: Request, ops: Ops, bot_id: int, schedule_id: int):
        schedule, row_id = await ops.schedules.run_now(bot_id, schedule_id)
        ui.flash(request, f"Started {schedule['name']}; see the result in the update log.")
        return ui.back(f"/bots/{bot_id}/updates/{row_id}")

    @app.post("/bots/{bot_id}/schedules/{schedule_id}/delete")
    async def delete_schedule(request: Request, ops: Ops, bot_id: int, schedule_id: int):
        schedule = await ops.schedules.delete(bot_id, schedule_id)
        ui.flash(request, f"Schedule {schedule['name']} deleted.", "success")
        return ui.back(f"/bots/{bot_id}/schedules")
