"""Per-bot pages: update log, API console, files and bot settings."""

from pathlib import Path
from typing import Annotated, Any

from fastapi import Body, Depends, FastAPI, Form, Request
from fastapi.responses import FileResponse

from .errors import NotFound
from .ops import Operations
from .ops.bots import UPDATE_STATUSES
from .service import PURGE_HANDLER
from .ui import FAILED, Ui

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
    Ops = Annotated[Operations, Depends(ui.ops)]

    # Update log

    @app.get("/bots/{bot_id}")
    async def bot_home(bot_id: int):
        return ui.back(f"/bots/{bot_id}/updates")

    @app.get("/bots/{bot_id}/updates")
    async def updates_page(request: Request, ops: Ops, bot_id: int, status: str | None = None):
        bot = await ui.bot(bot_id)
        rows = await ops.bots.updates(bot_id, status=status or None)
        return ui.render(
            request,
            "updates.html",
            bot=bot,
            tab="updates",
            updates=rows,
            status=status or "",
            statuses=list(UPDATE_STATUSES),
        )

    @app.get("/bots/{bot_id}/updates/{row_id}")
    async def update_page(request: Request, ops: Ops, bot_id: int, row_id: int):
        bot = await ui.bot(bot_id)
        row = await ops.bots.update(bot_id, row_id)
        return ui.render(
            request,
            "update.html",
            bot=bot,
            tab="updates",
            u=row,
            route=row["route"],
            tasks=row["tasks"],
        )

    # API console

    @app.get("/bots/{bot_id}/console")
    async def console_page(request: Request, ops: Ops, bot_id: int):
        bot = await ui.bot(bot_id)
        return ui.render(
            request,
            "console.html",
            bot=bot,
            tab="console",
            history=await ops.bots.console_history(bot_id),
            methods=COMMON_METHODS,
        )

    @app.post("/bots/{bot_id}/console")
    async def console_call(ops: Ops, bot_id: int, body: Annotated[dict[str, Any], Body()]):
        return await ops.bots.console_call(
            bot_id, str(body.get("method", "")), body.get("params") or {}
        )

    # Files

    @app.get("/bots/{bot_id}/files")
    async def files_page(request: Request, ops: Ops, bot_id: int, path: str = ""):
        bot = await ui.bot(bot_id)
        listing = await ops.files.list(bot_id, path)
        return ui.render(
            request,
            "files.html",
            bot=bot,
            tab="files",
            entries=listing["entries"],
            rel=listing["path"],
            crumbs=listing["crumbs"],
            exists=listing["exists"],
            usage=listing["usage"],
        )

    @app.get("/bots/{bot_id}/files/download")
    async def download_file(ops: Ops, bot_id: int, path: str):
        target = await ops.files.file(bot_id, path)
        return FileResponse(target, filename=target.name)

    @app.post("/bots/{bot_id}/files/delete")
    async def delete_file(request: Request, ops: Ops, bot_id: int, path: Annotated[str, Form()]):
        try:
            await ops.files.delete(bot_id, path)
        except NotFound:
            pass  # Already gone: the result the user wanted.
        ui.flash(request, f"Deleted {path}.", "success")
        parent = str(Path(path).parent)
        return ui.back(f"/bots/{bot_id}/files?path={'' if parent == '.' else parent}")

    # Bot settings

    @app.get("/bots/{bot_id}/settings")
    async def settings_page(request: Request, ops: Ops, bot_id: int):
        bot = await ui.bot(bot_id)
        return ui.render(
            request,
            "settings.html",
            bot=bot,
            tab="settings",
            secrets=await ops.bots.secret_names(bot_id),
            kv=await ops.bots.kv_list(bot_id),
            senders=await ops.bots.recent_senders(bot_id, limit=8),
            purge_handler=PURGE_HANDLER,
        )

    @app.post("/bots/{bot_id}/pause")
    async def pause(request: Request, ops: Ops, bot_id: int):
        await ops.bots.pause(bot_id)
        ui.flash(request, "Polling paused. Updates wait on the Bot API server for up to 24 hours.")
        return ui.back(f"/bots/{bot_id}/settings")

    @app.post("/bots/{bot_id}/resume")
    async def resume(request: Request, ops: Ops, bot_id: int):
        await ops.bots.resume(bot_id)
        ui.flash(request, "Polling started.", "success")
        return ui.back(f"/bots/{bot_id}/settings")

    @app.post("/bots/{bot_id}/allowed-users")
    async def allowed_users(
        request: Request, ops: Ops, bot_id: int, allowed: Annotated[str, Form()] = ""
    ):
        entries = await ui.act(request, ops.bots.set_allowed_users(bot_id, allowed))
        if entries is FAILED:
            return ui.back(f"/bots/{bot_id}/settings")
        ui.flash(
            request,
            "Allowed users saved."
            if entries
            else "Allowed users cleared: the bot handles updates from everyone.",
            "success",
        )
        return ui.back(f"/bots/{bot_id}/settings")

    @app.post("/bots/{bot_id}/purge-command")
    async def purge_command(
        request: Request, ops: Ops, bot_id: int, enabled: Annotated[str, Form()] = ""
    ):
        on = enabled == "on"
        await ops.bots.set_purge_command(bot_id, on)
        ui.flash(
            request,
            "/purge is on: it deletes the last 48 hours of a private chat."
            if on
            else "/purge is off: the command goes to your routes like any other.",
            "success",
        )
        return ui.back(f"/bots/{bot_id}/settings")

    @app.post("/bots/{bot_id}/secrets")
    async def set_secret(
        request: Request,
        ops: Ops,
        bot_id: int,
        name: Annotated[str, Form()],
        value: Annotated[str, Form()],
    ):
        name = await ui.act(request, ops.bots.set_secret(bot_id, name, value))
        if name is FAILED:
            return ui.back(f"/bots/{bot_id}/settings")
        ui.flash(request, f"Secret {name} saved.", "success")
        return ui.back(f"/bots/{bot_id}/settings")

    @app.post("/bots/{bot_id}/secrets/delete")
    async def delete_secret(request: Request, ops: Ops, bot_id: int, name: Annotated[str, Form()]):
        await ops.bots.delete_secret(bot_id, name)
        ui.flash(request, f"Secret {name} deleted.", "success")
        return ui.back(f"/bots/{bot_id}/settings")

    @app.post("/bots/{bot_id}/kv/delete")
    async def delete_kv(request: Request, ops: Ops, bot_id: int, key: Annotated[str, Form()]):
        await ops.bots.kv_delete(bot_id, key)
        ui.flash(request, f"Key {key} deleted.", "success")
        return ui.back(f"/bots/{bot_id}/settings")

    @app.post("/bots/{bot_id}/cloud-logout")
    async def cloud_logout(request: Request, ops: Ops, bot_id: int):
        for note in await ops.bots.log_out_cloud(bot_id):
            ui.flash(request, note)
        return ui.back(f"/bots/{bot_id}/settings")

    @app.post("/bots/{bot_id}/local-logout")
    async def local_logout(request: Request, ops: Ops, bot_id: int):
        if await ui.act(request, ops.bots.log_out_local(bot_id)) is not FAILED:
            ui.flash(
                request,
                "Logged out from the local server. The cloud Bot API accepts the bot again "
                "in about 10 minutes.",
                "success",
            )
        return ui.back(f"/bots/{bot_id}/settings")

    @app.post("/bots/{bot_id}/delete")
    async def delete_bot(request: Request, ops: Ops, bot_id: int):
        bot = await ops.bots.delete(bot_id)
        ui.flash(request, f"@{bot['username']} removed. Its files are kept on disk.", "success")
        return ui.back("/")
