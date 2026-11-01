"""Per-bot logic pages: handlers, routes, background tasks and schedules."""

import json
import re
from typing import Annotated, Any

from fastapi import Body, Depends, FastAPI, Form, HTTPException, Request

from . import cron, routing
from .handler_templates import TEMPLATES
from .service import BotError, Orchestrator
from .telegram import TelegramError
from .ui import Ui
from .views_shared import unknown_imports

HANDLER_NAME = re.compile(r"^[A-Za-z0-9_-]{1,40}$")
TASK_STATUSES = ["queued", "running", "ok", "error", "timeout", "cancelled", "interrupted"]


def register(app: FastAPI, ui: Ui) -> None:
    Orch = Annotated[Orchestrator, Depends(ui.orch)]

    def check_name(request: Request, name: str) -> bool:
        if HANDLER_NAME.match(name):
            return True
        ui.flash(
            request,
            "Handler names use 1-40 letters, digits, - and _ (for example save_files).",
            "error",
        )
        return False

    # Handlers

    @app.get("/bots/{bot_id}/handler")
    async def old_handler_url(bot_id: int):
        return ui.back(f"/bots/{bot_id}/handlers")

    @app.get("/bots/{bot_id}/handlers")
    async def handlers_page(request: Request, o: Orch, bot_id: int):
        bot = await ui.bot(bot_id)
        return ui.render(
            request,
            "handlers.html",
            bot=bot,
            tab="handlers",
            handlers=await o.db.list_handlers(bot_id),
            templates=TEMPLATES,
        )

    @app.post("/bots/{bot_id}/handlers")
    async def create_handler(
        request: Request,
        o: Orch,
        bot_id: int,
        name: Annotated[str, Form()],
        template: Annotated[str, Form()] = "echo",
        add_route: Annotated[bool, Form()] = False,
    ):
        await ui.bot(bot_id)
        name = name.strip()
        if not check_name(request, name):
            return ui.back(f"/bots/{bot_id}/handlers")
        if await o.db.get_handler_by_name(bot_id, name):
            ui.flash(request, f"A handler called {name} already exists.", "error")
            return ui.back(f"/bots/{bot_id}/handlers")
        chosen = TEMPLATES.get(template, TEMPLATES["echo"])
        handler_id = await o.db.create_handler(
            bot_id, name, chosen["code"], f"Created from “{chosen['title']}”"
        )
        message = f"Handler {name} created."
        if add_route and chosen["route"]:
            kind, pattern = chosen["route"]
            await o.db.create_route(bot_id, kind, pattern, "", handler_id)
            message += f" Route added: {routing.describe({'kind': kind, 'pattern': pattern})}."
        ui.flash(request, message, "success")
        return ui.back(f"/bots/{bot_id}/handlers/{handler_id}")

    @app.get("/bots/{bot_id}/handlers/{handler_id}")
    async def handler_page(
        request: Request,
        o: Orch,
        bot_id: int,
        handler_id: int,
        version: int | None = None,
        update: int | None = None,
    ):
        bot = await ui.bot(bot_id)
        handler = await ui.handler(bot_id, handler_id)
        shown = version or handler["active_version"]
        row = await o.db.get_version(handler_id, shown) if shown else None
        routes = [r for r in await o.db.list_routes(bot_id) if r["handler_id"] == handler_id]
        schedules = [s for s in await o.db.list_schedules(bot_id) if s["handler_id"] == handler_id]
        return ui.render(
            request,
            "handler.html",
            bot=bot,
            tab="handlers",
            handler=handler,
            code=row["code"] if row else "",
            shown_version=row["version"] if row else None,
            versions=await o.db.list_versions(handler_id),
            recent=await o.db.list_updates(bot_id, limit=30),
            selected_update=update,
            routes=routes,
            schedules=schedules,
        )

    @app.post("/bots/{bot_id}/handlers/{handler_id}")
    async def save_handler(
        request: Request,
        o: Orch,
        bot_id: int,
        handler_id: int,
        code: Annotated[str, Form()],
        comment: Annotated[str, Form()] = "",
    ):
        await ui.handler(bot_id, handler_id)
        url = f"/bots/{bot_id}/handlers/{handler_id}"
        try:
            compile(code, "<handler>", "exec")
        except SyntaxError as exc:
            ui.flash(request, f"Not saved: syntax error on line {exc.lineno}: {exc.msg}", "error")
            return ui.back(url)
        version = await o.db.save_version(handler_id, code, comment.strip()[:200])
        ui.flash(request, f"Saved and activated version {version}.", "success")
        missing = await unknown_imports(o, code)
        if missing:
            ui.flash(
                request,
                "No such shared modules yet: " + ", ".join(missing) + ". Create them on the "
                "Modules page.",
                "warning",
            )
        return ui.back(url)

    @app.post("/bots/{bot_id}/handlers/{handler_id}/activate")
    async def activate_version(
        request: Request,
        o: Orch,
        bot_id: int,
        handler_id: int,
        version: Annotated[int, Form()],
    ):
        await ui.handler(bot_id, handler_id)
        if not await o.db.get_version(handler_id, version):
            raise HTTPException(status_code=404, detail="Version not found")
        await o.db.activate_version(handler_id, version)
        ui.flash(request, f"Version {version} is active.", "success")
        return ui.back(f"/bots/{bot_id}/handlers/{handler_id}")

    @app.post("/bots/{bot_id}/handlers/{handler_id}/rename")
    async def rename_handler(
        request: Request,
        o: Orch,
        bot_id: int,
        handler_id: int,
        name: Annotated[str, Form()],
    ):
        handler = await ui.handler(bot_id, handler_id)
        name = name.strip()
        url = f"/bots/{bot_id}/handlers/{handler_id}"
        if name == handler["name"] or not check_name(request, name):
            return ui.back(url)
        if await o.db.get_handler_by_name(bot_id, name):
            ui.flash(request, f"A handler called {name} already exists.", "error")
            return ui.back(url)
        await o.db.rename_handler(handler_id, name)
        ui.flash(request, f"Renamed to {name}.", "success")
        return ui.back(url)

    @app.post("/bots/{bot_id}/handlers/{handler_id}/delete")
    async def delete_handler(request: Request, o: Orch, bot_id: int, handler_id: int):
        handler = await ui.handler(bot_id, handler_id)
        await o.db.delete_handler(handler_id)
        ui.flash(
            request, f"Handler {handler['name']} deleted with its routes and schedules.", "success"
        )
        return ui.back(f"/bots/{bot_id}/handlers")

    @app.post("/bots/{bot_id}/handlers/{handler_id}/test")
    async def test_handler(
        o: Orch, bot_id: int, handler_id: int, body: Annotated[dict[str, Any], Body()]
    ):
        bot = await ui.bot(bot_id)
        handler = await ui.handler(bot_id, handler_id)
        origin: dict[str, Any] = {}
        if body.get("update_row"):
            row = await o.db.get_update(bot_id, int(body["update_row"]))
            if not row:
                raise HTTPException(status_code=404, detail="Update not found")
            update = row["raw"]
            origin = {"update_row": row["id"]}
        else:
            try:
                update = json.loads(body.get("update_json") or "")
            except ValueError:
                raise HTTPException(status_code=400, detail="Update JSON is not valid") from None
            if not isinstance(update, dict):
                raise HTTPException(status_code=400, detail="Update must be a JSON object")
        return await o.call_runner(
            bot,
            code=str(body.get("code", "")),
            version=0,
            update=update,
            handler={"id": handler_id, "name": handler["name"], "version": 0},
            dry_run=bool(body.get("dry_run", True)),
            origin=origin,
        )

    # Routes

    async def route_form_data(bot_id: int) -> dict[str, Any]:
        o = ui.orch()
        return {
            "kinds": routing.ROUTE_KINDS,
            "help": routing.PATTERN_HELP,
            "handlers": await o.db.list_handlers(bot_id),
        }

    async def read_route_form(request: Request, bot_id: int) -> dict[str, Any] | None:
        form = await request.form()
        kind = str(form.get("kind", ""))
        try:
            pattern = routing.validate(kind, str(form.get("pattern", "")))
        except ValueError as exc:
            ui.flash(request, str(exc), "error")
            return None
        handler_id = str(form.get("handler_id", ""))
        if not handler_id.isdigit() or not await ui.orch().db.get_handler(bot_id, int(handler_id)):
            ui.flash(request, "Choose a handler.", "error")
            return None
        return {
            "kind": kind,
            "pattern": pattern,
            "description": str(form.get("description", "")).strip()[:256],
            "handler_id": int(handler_id),
            "enabled": bool(form.get("enabled")),
        }

    @app.get("/bots/{bot_id}/routes")
    async def routes_page(request: Request, o: Orch, bot_id: int):
        bot = await ui.bot(bot_id)
        return ui.render(
            request,
            "routes.html",
            bot=bot,
            tab="routes",
            routes=await o.db.list_routes(bot_id),
            recent=await o.db.list_updates(bot_id, limit=30),
            **await route_form_data(bot_id),
        )

    @app.post("/bots/{bot_id}/routes")
    async def create_route(request: Request, o: Orch, bot_id: int):
        await ui.bot(bot_id)
        data = await read_route_form(request, bot_id)
        if data:
            await o.db.create_route(bot_id, **data)
            ui.flash(request, f"Route added: {routing.describe(data)}.", "success")
        return ui.back(f"/bots/{bot_id}/routes")

    @app.get("/bots/{bot_id}/routes/{route_id}")
    async def route_page(request: Request, o: Orch, bot_id: int, route_id: int):
        bot = await ui.bot(bot_id)
        route = await o.db.get_route(bot_id, route_id)
        if not route:
            raise HTTPException(status_code=404, detail="Route not found")
        return ui.render(
            request,
            "route_edit.html",
            bot=bot,
            tab="routes",
            route=route,
            **await route_form_data(bot_id),
        )

    @app.post("/bots/{bot_id}/routes/{route_id}")
    async def update_route(request: Request, o: Orch, bot_id: int, route_id: int):
        if not await o.db.get_route(bot_id, route_id):
            raise HTTPException(status_code=404, detail="Route not found")
        data = await read_route_form(request, bot_id)
        if not data:
            return ui.back(f"/bots/{bot_id}/routes/{route_id}")
        await o.db.update_route(route_id, **data)
        ui.flash(request, "Route saved.", "success")
        return ui.back(f"/bots/{bot_id}/routes")

    @app.post("/bots/{bot_id}/routes/{route_id}/toggle")
    async def toggle_route(request: Request, o: Orch, bot_id: int, route_id: int):
        route = await o.db.get_route(bot_id, route_id)
        if not route:
            raise HTTPException(status_code=404, detail="Route not found")
        await o.db.set_route_enabled(route_id, not route["enabled"])
        return ui.back(f"/bots/{bot_id}/routes")

    @app.post("/bots/{bot_id}/routes/{route_id}/delete")
    async def delete_route(request: Request, o: Orch, bot_id: int, route_id: int):
        if await o.db.get_route(bot_id, route_id):
            await o.db.delete_route(route_id)
            ui.flash(request, "Route deleted.", "success")
        return ui.back(f"/bots/{bot_id}/routes")

    @app.post("/bots/{bot_id}/routes-order")
    async def reorder_routes(o: Orch, bot_id: int, body: Annotated[dict[str, Any], Body()]):
        await ui.bot(bot_id)
        ids = body.get("ids")
        if not isinstance(ids, list) or not all(isinstance(i, int) for i in ids):
            raise HTTPException(status_code=400, detail="ids must be a list of route ids")
        await o.db.reorder_routes(bot_id, ids)
        return {"ok": True}

    @app.post("/bots/{bot_id}/routes-match")
    async def match_route(o: Orch, bot_id: int, body: Annotated[dict[str, Any], Body()]):
        bot = await ui.bot(bot_id)
        row = await o.db.get_update(bot_id, int(body.get("update_row") or 0))
        if not row:
            raise HTTPException(status_code=404, detail="Update not found")
        route = await o.match_route(bot, row["raw"])
        if not route:
            return {"route_id": None, "text": "No enabled route matches this update."}
        return {
            "route_id": route["id"],
            "text": f"Matches “{routing.describe(route)}” → {route['handler_name']}",
        }

    @app.post("/bots/{bot_id}/routes-commands")
    async def publish_commands(request: Request, o: Orch, bot_id: int):
        bot = await ui.bot(bot_id)
        commands = []
        for route in await o.db.list_routes(bot_id):
            if route["kind"] == "command" and route["enabled"]:
                if route["pattern"] in {c["command"] for c in commands}:
                    continue
                description = route["description"] or route["handler_name"]
                commands.append({"command": route["pattern"], "description": description[:256]})
        try:
            await o.local_api(o.token_of(bot)).call("setMyCommands", {"commands": commands})
        except TelegramError as exc:
            ui.flash(request, f"setMyCommands failed: {exc.description}", "error")
            return ui.back(f"/bots/{bot_id}/routes")
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
    async def tasks_page(request: Request, o: Orch, bot_id: int, status: str | None = None):
        bot = await ui.bot(bot_id)
        rows = await o.db.list_tasks(bot_id, status=status or None)
        active = sum(1 for t in rows if t["status"] in ("queued", "running"))
        return ui.render(
            request,
            "tasks.html",
            bot=bot,
            tab="tasks",
            tasks=rows,
            status=status or "",
            statuses=TASK_STATUSES,
            active=active,
        )

    @app.get("/bots/{bot_id}/tasks/{task_id}")
    async def task_page(request: Request, o: Orch, bot_id: int, task_id: int):
        bot = await ui.bot(bot_id)
        task = await o.db.get_task(task_id, bot_id)
        if not task:
            raise HTTPException(status_code=404, detail="Task not found")
        return ui.render(request, "task.html", bot=bot, tab="tasks", t=task)

    @app.post("/bots/{bot_id}/tasks/{task_id}/cancel")
    async def cancel_task(request: Request, o: Orch, bot_id: int, task_id: int):
        if not await o.db.get_task(task_id, bot_id):
            raise HTTPException(status_code=404, detail="Task not found")
        if await o.tasks.cancel(task_id):
            ui.flash(request, "Task cancelled.", "success")
        else:
            ui.flash(request, "The task has already finished.")
        return ui.back(f"/bots/{bot_id}/tasks/{task_id}")

    @app.post("/bots/{bot_id}/tasks/{task_id}/retry")
    async def retry_task(request: Request, o: Orch, bot_id: int, task_id: int):
        try:
            new_id = await o.tasks.retry(bot_id, task_id)
        except BotError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from None
        ui.flash(request, f"Started again as task {new_id}.", "success")
        return ui.back(f"/bots/{bot_id}/tasks/{new_id}")

    # Schedules

    async def read_schedule_form(request: Request, bot_id: int) -> dict[str, Any] | None:
        form = await request.form()
        name = str(form.get("name", "")).strip()[:100]
        expression = " ".join(str(form.get("cron", "")).split())
        chat_id = str(form.get("chat_id", "")).strip()
        handler_id = str(form.get("handler_id", ""))
        if not name:
            ui.flash(request, "Give the schedule a name.", "error")
            return None
        try:
            next_at = ui.orch().next_run_at(expression)
        except cron.CronError as exc:
            ui.flash(request, f"Cron expression: {exc}", "error")
            return None
        if chat_id and not chat_id.lstrip("-").isdigit():
            ui.flash(request, "The chat id must be a number.", "error")
            return None
        if not handler_id.isdigit() or not await ui.orch().db.get_handler(bot_id, int(handler_id)):
            ui.flash(request, "Choose a handler.", "error")
            return None
        enabled = bool(form.get("enabled"))
        return {
            "name": name,
            "cron": expression,
            "handler_id": int(handler_id),
            "chat_id": int(chat_id) if chat_id else None,
            "enabled": enabled,
            "next_run_at": next_at if enabled else None,
        }

    async def schedule_form_data(bot_id: int) -> dict[str, Any]:
        o = ui.orch()
        return {
            "handlers": await o.db.list_handlers(bot_id),
            "senders": await o.db.recent_senders(),
            "timezone": ui.settings.tz,
        }

    @app.get("/bots/{bot_id}/schedules")
    async def schedules_page(request: Request, o: Orch, bot_id: int):
        bot = await ui.bot(bot_id)
        return ui.render(
            request,
            "schedules.html",
            bot=bot,
            tab="schedules",
            schedules=await o.db.list_schedules(bot_id),
            **await schedule_form_data(bot_id),
        )

    @app.get("/bots/{bot_id}/schedules-preview")
    async def preview_schedule(o: Orch, bot_id: int, expression: str = ""):
        try:
            parsed = cron.parse(expression)
        except cron.CronError as exc:
            return {"ok": False, "error": str(exc)}
        runs, moment = [], o.now_local()
        for _ in range(3):
            moment = parsed.next_after(moment)
            runs.append(moment.strftime("%a %Y-%m-%d %H:%M"))
        return {"ok": True, "runs": runs}

    @app.post("/bots/{bot_id}/schedules")
    async def create_schedule(request: Request, o: Orch, bot_id: int):
        await ui.bot(bot_id)
        data = await read_schedule_form(request, bot_id)
        if data:
            await o.db.create_schedule(bot_id, **data)
            ui.flash(request, f"Schedule {data['name']} added.", "success")
        return ui.back(f"/bots/{bot_id}/schedules")

    async def schedule_or_404(bot_id: int, schedule_id: int) -> dict[str, Any]:
        schedule = await ui.orch().db.get_schedule(bot_id, schedule_id)
        if not schedule:
            raise HTTPException(status_code=404, detail="Schedule not found")
        return schedule

    @app.get("/bots/{bot_id}/schedules/{schedule_id}")
    async def schedule_page(request: Request, bot_id: int, schedule_id: int):
        bot = await ui.bot(bot_id)
        return ui.render(
            request,
            "schedule_edit.html",
            bot=bot,
            tab="schedules",
            schedule=await schedule_or_404(bot_id, schedule_id),
            **await schedule_form_data(bot_id),
        )

    @app.post("/bots/{bot_id}/schedules/{schedule_id}")
    async def update_schedule(request: Request, o: Orch, bot_id: int, schedule_id: int):
        await schedule_or_404(bot_id, schedule_id)
        data = await read_schedule_form(request, bot_id)
        if not data:
            return ui.back(f"/bots/{bot_id}/schedules/{schedule_id}")
        await o.db.update_schedule(schedule_id, **data)
        ui.flash(request, "Schedule saved.", "success")
        return ui.back(f"/bots/{bot_id}/schedules")

    @app.post("/bots/{bot_id}/schedules/{schedule_id}/toggle")
    async def toggle_schedule(request: Request, o: Orch, bot_id: int, schedule_id: int):
        schedule = await schedule_or_404(bot_id, schedule_id)
        enabled = not schedule["enabled"]
        await o.db.update_schedule(
            schedule_id,
            **{
                **schedule,
                "enabled": enabled,
                "next_run_at": o.next_run_at(schedule["cron"]) if enabled else None,
            },
        )
        return ui.back(f"/bots/{bot_id}/schedules")

    @app.post("/bots/{bot_id}/schedules/{schedule_id}/run")
    async def run_schedule(request: Request, o: Orch, bot_id: int, schedule_id: int):
        schedule = await schedule_or_404(bot_id, schedule_id)
        row_id = await o.run_schedule(schedule, manual=True)
        ui.flash(request, f"Started {schedule['name']}; see the result in the update log.")
        return ui.back(f"/bots/{bot_id}/updates/{row_id}")

    @app.post("/bots/{bot_id}/schedules/{schedule_id}/delete")
    async def delete_schedule(request: Request, o: Orch, bot_id: int, schedule_id: int):
        schedule = await schedule_or_404(bot_id, schedule_id)
        await o.db.delete_schedule(schedule_id)
        ui.flash(request, f"Schedule {schedule['name']} deleted.", "success")
        return ui.back(f"/bots/{bot_id}/schedules")
