"""Per-bot pages: update log, API console, files and bot settings."""

import re
import shutil
from pathlib import Path
from typing import Annotated, Any

from fastapi import Body, Depends, FastAPI, Form, HTTPException, Request
from fastapi.responses import FileResponse

from .service import BotError, Orchestrator
from .ui import Ui

SECRET_NAME = re.compile(r"^[A-Za-z_][A-Za-z0-9_]{0,63}$")
UPDATE_STATUSES = [
    "ok",
    "error",
    "timeout",
    "dispatch_error",
    "pending",
    "no_route",
    "ignored",
]

COMMON_METHODS = [
    "getMe",
    "getWebhookInfo",
    "deleteWebhook",
    "getUpdates",
    "sendMessage",
    "sendDocument",
    "sendPhoto",
    "getFile",
    "getChat",
    "getChatMember",
    "setMyCommands",
    "getMyCommands",
    "deleteMyCommands",
    "setMyDescription",
    "setMyShortDescription",
    "setMyName",
    "answerCallbackQuery",
    "editMessageText",
    "deleteMessage",
    "forwardMessage",
    "copyMessage",
]


def register(app: FastAPI, ui: Ui) -> None:
    Orch = Annotated[Orchestrator, Depends(ui.orch)]
    settings = ui.settings

    # Update log

    @app.get("/bots/{bot_id}")
    async def bot_home(bot_id: int):
        return ui.back(f"/bots/{bot_id}/updates")

    @app.get("/bots/{bot_id}/updates")
    async def updates_page(request: Request, o: Orch, bot_id: int, status: str | None = None):
        bot = await ui.bot(bot_id)
        rows = await o.db.list_updates(bot_id, status=status or None)
        return ui.render(
            request,
            "updates.html",
            bot=bot,
            tab="updates",
            updates=rows,
            status=status or "",
            statuses=UPDATE_STATUSES,
        )

    @app.get("/bots/{bot_id}/updates/{row_id}")
    async def update_page(request: Request, o: Orch, bot_id: int, row_id: int):
        bot = await ui.bot(bot_id)
        row = await o.db.get_update(bot_id, row_id)
        if not row:
            raise HTTPException(status_code=404, detail="Update not found")
        route = await o.db.get_route(bot_id, row["route_id"]) if row["route_id"] else None
        tasks = [t for t in await o.db.list_tasks(bot_id, limit=200) if t["update_row"] == row_id]
        return ui.render(
            request, "update.html", bot=bot, tab="updates", u=row, route=route, tasks=tasks
        )

    # API console

    @app.get("/bots/{bot_id}/console")
    async def console_page(request: Request, o: Orch, bot_id: int):
        bot = await ui.bot(bot_id)
        history = await o.db.list_console_calls(bot_id)
        return ui.render(
            request,
            "console.html",
            bot=bot,
            tab="console",
            history=history,
            methods=COMMON_METHODS,
        )

    @app.post("/bots/{bot_id}/console")
    async def console_call(o: Orch, bot_id: int, body: Annotated[dict[str, Any], Body()]):
        params = body.get("params") or {}
        if not isinstance(params, dict):
            raise HTTPException(status_code=400, detail="Parameters must be a JSON object")
        try:
            return await o.console_call(bot_id, str(body.get("method", "")), params)
        except BotError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from None

    # Files

    def bot_folder(bot_id: int) -> Path:
        return Path(settings.files_root) / str(bot_id)

    def inside(bot_id: int, relative: str) -> Path:
        root = bot_folder(bot_id).resolve()
        target = (root / relative.lstrip("/")).resolve()
        if not target.is_relative_to(root):
            raise HTTPException(status_code=400, detail="Path is outside the bot folder")
        return target

    @app.get("/bots/{bot_id}/files")
    async def files_page(request: Request, bot_id: int, path: str = ""):
        bot = await ui.bot(bot_id)
        folder = inside(bot_id, path)
        root = bot_folder(bot_id).resolve()
        entries = []
        if folder.is_dir():
            for item in sorted(folder.iterdir(), key=lambda p: (not p.is_dir(), p.name.lower())):
                stat = item.stat()
                entries.append(
                    {
                        "name": item.name,
                        "path": str(item.relative_to(root)),
                        "is_dir": item.is_dir(),
                        "size": stat.st_size,
                        "modified": stat.st_mtime,
                    }
                )
        rel = "" if folder == root else str(folder.relative_to(root))
        crumbs = []
        acc: list[str] = []
        for part in Path(rel).parts:
            acc.append(part)
            crumbs.append({"name": part, "path": "/".join(acc)})
        usage = (
            sum(f.stat().st_size for f in root.rglob("*") if f.is_file()) if root.exists() else 0
        )
        return ui.render(
            request,
            "files.html",
            bot=bot,
            tab="files",
            entries=entries,
            rel=rel,
            crumbs=crumbs,
            exists=folder.is_dir(),
            usage=usage,
        )

    @app.get("/bots/{bot_id}/files/download")
    async def download_file(bot_id: int, path: str):
        await ui.bot(bot_id)
        target = inside(bot_id, path)
        if not target.is_file():
            raise HTTPException(status_code=404, detail="File not found")
        return FileResponse(target, filename=target.name)

    @app.post("/bots/{bot_id}/files/delete")
    async def delete_file(request: Request, bot_id: int, path: Annotated[str, Form()]):
        await ui.bot(bot_id)
        target = inside(bot_id, path)
        if target == bot_folder(bot_id).resolve():
            raise HTTPException(status_code=400, detail="Cannot delete the bot folder")
        if target.is_dir():
            shutil.rmtree(target)
        elif target.exists():
            target.unlink()
        ui.flash(request, f"Deleted {path}.", "success")
        parent = str(Path(path).parent)
        return ui.back(f"/bots/{bot_id}/files?path={'' if parent == '.' else parent}")

    # Bot settings

    @app.get("/bots/{bot_id}/settings")
    async def settings_page(request: Request, o: Orch, bot_id: int):
        bot = await ui.bot(bot_id)
        return ui.render(
            request,
            "settings.html",
            bot=bot,
            tab="settings",
            secrets=await o.db.list_secret_names(bot_id),
            kv=await o.db.kv_list(bot_id),
        )

    @app.post("/bots/{bot_id}/pause")
    async def pause(request: Request, o: Orch, bot_id: int):
        await o.pause(bot_id)
        ui.flash(request, "Polling paused. Updates wait on the Bot API server for up to 24 hours.")
        return ui.back(f"/bots/{bot_id}/settings")

    @app.post("/bots/{bot_id}/resume")
    async def resume(request: Request, o: Orch, bot_id: int):
        await o.resume(bot_id)
        ui.flash(request, "Polling started.", "success")
        return ui.back(f"/bots/{bot_id}/settings")

    @app.post("/bots/{bot_id}/allowed-users")
    async def allowed_users(
        request: Request, o: Orch, bot_id: int, allowed: Annotated[str, Form()] = ""
    ):
        await ui.bot(bot_id)
        ids = sorted({p for p in re.split(r"[\s,;]+", allowed) if p})
        if any(not p.isdigit() for p in ids):
            ui.flash(request, "Allowed users must be numeric Telegram user ids.", "error")
            return ui.back(f"/bots/{bot_id}/settings")
        await o.db.set_allowed_users(bot_id, ",".join(ids))
        ui.flash(
            request,
            "Allowed users saved."
            if ids
            else "Allowed users cleared: the bot handles updates from everyone.",
            "success",
        )
        return ui.back(f"/bots/{bot_id}/settings")

    @app.post("/bots/{bot_id}/secrets")
    async def set_secret(
        request: Request,
        o: Orch,
        bot_id: int,
        name: Annotated[str, Form()],
        value: Annotated[str, Form()],
    ):
        await ui.bot(bot_id)
        name = name.strip()
        if not SECRET_NAME.match(name):
            ui.flash(
                request,
                "Secret names may use letters, digits and _ and must not start with a digit.",
                "error",
            )
            return ui.back(f"/bots/{bot_id}/settings")
        await o.db.set_secret(bot_id, name, o.cipher.encrypt(value))
        ui.flash(request, f"Secret {name} saved.", "success")
        return ui.back(f"/bots/{bot_id}/settings")

    @app.post("/bots/{bot_id}/secrets/delete")
    async def delete_secret(request: Request, o: Orch, bot_id: int, name: Annotated[str, Form()]):
        await o.db.delete_secret(bot_id, name)
        ui.flash(request, f"Secret {name} deleted.", "success")
        return ui.back(f"/bots/{bot_id}/settings")

    @app.post("/bots/{bot_id}/kv/delete")
    async def delete_kv(request: Request, o: Orch, bot_id: int, key: Annotated[str, Form()]):
        await o.db.kv_delete(bot_id, key)
        ui.flash(request, f"Key {key} deleted.", "success")
        return ui.back(f"/bots/{bot_id}/settings")

    @app.post("/bots/{bot_id}/cloud-logout")
    async def cloud_logout(request: Request, o: Orch, bot_id: int):
        bot = await ui.bot(bot_id)
        for note in await o.log_out_from_cloud(o.token_of(bot)):
            ui.flash(request, note)
        return ui.back(f"/bots/{bot_id}/settings")

    @app.post("/bots/{bot_id}/local-logout")
    async def local_logout(request: Request, o: Orch, bot_id: int):
        try:
            await o.log_out_local(bot_id)
            ui.flash(
                request,
                "Logged out from the local server. The cloud Bot API accepts the bot again "
                "in about 10 minutes.",
                "success",
            )
        except BotError as exc:
            ui.flash(request, str(exc), "error")
        return ui.back(f"/bots/{bot_id}/settings")

    @app.post("/bots/{bot_id}/delete")
    async def delete_bot(request: Request, o: Orch, bot_id: int):
        bot = await ui.bot(bot_id)
        await o.delete_bot(bot_id)
        ui.flash(request, f"@{bot['username']} removed. Its files are kept on disk.", "success")
        return ui.back("/")
