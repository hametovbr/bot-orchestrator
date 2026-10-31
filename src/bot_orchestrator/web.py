"""Web UI and the internal API used by the runner."""

import asyncio
import datetime as dt
import hmac
import json
import re
import shutil
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Annotated, Any
from urllib.parse import urlparse

from fastapi import Body, Depends, FastAPI, Form, Header, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from starlette.middleware.sessions import SessionMiddleware

from . import __version__
from .config import Settings, get_settings
from .db import Database
from .security import TOKEN_FORMAT, short_token
from .service import FAILED, BotError, Orchestrator

HERE = Path(__file__).parent
PUBLIC_PATHS = ("/login", "/static/", "/health", "/internal/")
SECRET_NAME = re.compile(r"^[A-Za-z_][A-Za-z0-9_]{0,63}$")
KV_KEY = re.compile(r"^[\w.:-]{1,200}$")

DEFAULT_HANDLER = '''\
async def handle(update, ctx):
    """Called for every update the bot receives."""
    if update.message and update.message.text:
        await ctx.reply(f"You said: {update.message.text}")
'''

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


def _time(value: float | None) -> str:
    if not value:
        return ""
    return dt.datetime.fromtimestamp(value).strftime("%Y-%m-%d %H:%M:%S")


def _size(value: int) -> str:
    size = float(value)
    for unit in ("B", "KB", "MB", "GB"):
        if size < 1024 or unit == "GB":
            return f"{size:.0f} {unit}" if unit == "B" else f"{size:.1f} {unit}"
        size /= 1024
    return f"{size:.1f} GB"


def create_app(
    settings: Settings | None = None, orchestrator: Orchestrator | None = None
) -> FastAPI:
    settings = settings or get_settings()
    state: dict[str, Orchestrator] = {}

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        if orchestrator is not None:
            state["o"] = orchestrator
        else:
            db = Database(settings.database_path)
            await db.connect()
            state["o"] = Orchestrator(settings, db)
        await state["o"].start()
        try:
            yield
        finally:
            await state["o"].stop()
            await state["o"].db.close()

    app = FastAPI(
        title="bot-orchestrator",
        version=__version__,
        lifespan=lifespan,
        docs_url=None,
        redoc_url=None,
        openapi_url=None,
    )
    templates = Jinja2Templates(directory=HERE / "templates")
    templates.env.filters["time"] = _time
    templates.env.filters["size"] = _size
    templates.env.filters["pretty"] = lambda v: json.dumps(v, ensure_ascii=False, indent=2)
    templates.env.globals["version"] = __version__
    templates.env.globals["FAILED"] = FAILED

    def orch() -> Orchestrator:
        return state["o"]

    Orch = Annotated[Orchestrator, Depends(orch)]

    @app.middleware("http")
    async def guard(request: Request, call_next):
        path = request.url.path
        if not path.startswith(PUBLIC_PATHS):
            if not request.session.get("auth"):
                if request.method == "GET":
                    return RedirectResponse("/login", status_code=303)
                return JSONResponse({"detail": "not logged in"}, status_code=401)
            # Same-origin check for state-changing requests.
            if request.method not in ("GET", "HEAD"):
                origin = request.headers.get("origin")
                if origin and urlparse(origin).netloc != request.url.netloc:
                    return JSONResponse({"detail": "cross-origin request"}, status_code=403)
        return await call_next(request)

    # Added after the guard so it wraps it and request.session is available there.
    app.add_middleware(
        SessionMiddleware,
        secret_key=settings.secret_key,
        same_site="strict",
        https_only=settings.secure_cookies,
        max_age=30 * 86400,
        session_cookie="orchestrator_session",
    )
    app.mount("/static", StaticFiles(directory=HERE / "static"), name="static")

    def flash(request: Request, message: str, kind: str = "info") -> None:
        # Reassign instead of appending in place: the session only notices top-level changes.
        request.session["flash"] = [
            *request.session.get("flash", []),
            {"kind": kind, "text": message},
        ]

    def render(request: Request, name: str, **context: Any) -> Response:
        messages = request.session.pop("flash", [])
        return templates.TemplateResponse(request, name, {"messages": messages, **context})

    def back(url: str) -> RedirectResponse:
        return RedirectResponse(url, status_code=303)

    async def bot_or_404(o: Orchestrator, bot_id: int) -> dict[str, Any]:
        bot = await o.db.get_bot(bot_id)
        if not bot:
            raise HTTPException(status_code=404, detail="Bot not found")
        bot["token_hint"] = short_token(o.token_of(bot))
        bot["polling"] = o.is_polling(bot_id)
        return bot

    # Health and login

    @app.get("/health")
    async def health() -> dict[str, str]:
        return {"status": "ok", "version": __version__}

    @app.get("/login")
    async def login_page(request: Request):
        return render(request, "login.html")

    @app.post("/login")
    async def login(request: Request, password: Annotated[str, Form()] = ""):
        if hmac.compare_digest(password.encode(), settings.admin_password.encode()):
            request.session["auth"] = True
            return back("/")
        await asyncio.sleep(1)
        flash(request, "Wrong password.", "error")
        return back("/login")

    @app.post("/logout")
    async def logout(request: Request):
        request.session.clear()
        return back("/login")

    # Bot list

    @app.get("/")
    async def index(request: Request, o: Orch):
        bots = await o.db.list_bots()
        for bot in bots:
            bot["polling"] = o.is_polling(bot["id"])
        return render(request, "index.html", bots=bots)

    @app.post("/bots")
    async def add_bot(
        request: Request,
        o: Orch,
        token: Annotated[str, Form()],
        log_out_cloud: Annotated[bool, Form()] = False,
    ):
        if not TOKEN_FORMAT.match(token.strip()):
            flash(request, "That does not look like a bot token (123456:ABC...).", "error")
            return back("/")
        try:
            bot, notes = await o.add_bot(token, log_out_cloud)
        except BotError as exc:
            flash(request, str(exc), "error")
            return back("/")
        for note in notes:
            flash(request, note)
        flash(request, f"@{bot['username']} added and polling started.", "success")
        return back(f"/bots/{bot['id']}/handler")

    # Updates

    @app.get("/bots/{bot_id}")
    async def bot_home(bot_id: int):
        return back(f"/bots/{bot_id}/updates")

    @app.get("/bots/{bot_id}/updates")
    async def updates_page(request: Request, o: Orch, bot_id: int, status: str | None = None):
        bot = await bot_or_404(o, bot_id)
        rows = await o.db.list_updates(bot_id, status=status or None)
        return render(
            request, "updates.html", bot=bot, tab="updates", updates=rows, status=status or ""
        )

    @app.get("/bots/{bot_id}/updates/{row_id}")
    async def update_page(request: Request, o: Orch, bot_id: int, row_id: int):
        bot = await bot_or_404(o, bot_id)
        row = await o.db.get_update(bot_id, row_id)
        if not row:
            raise HTTPException(status_code=404, detail="Update not found")
        return render(request, "update.html", bot=bot, tab="updates", u=row)

    # Handler editor

    @app.get("/bots/{bot_id}/handler")
    async def handler_page(
        request: Request,
        o: Orch,
        bot_id: int,
        version: int | None = None,
        update: int | None = None,
    ):
        bot = await bot_or_404(o, bot_id)
        shown = version or bot["active_version"]
        row = await o.db.get_version(bot_id, shown) if shown else None
        code = row["code"] if row else DEFAULT_HANDLER
        recent = [u for u in await o.db.list_updates(bot_id, limit=30)]
        return render(
            request,
            "handler.html",
            bot=bot,
            tab="handler",
            code=code,
            shown_version=row["version"] if row else None,
            versions=await o.db.list_versions(bot_id),
            recent=recent,
            selected_update=update,
        )

    @app.post("/bots/{bot_id}/handler")
    async def save_handler(
        request: Request,
        o: Orch,
        bot_id: int,
        code: Annotated[str, Form()],
        comment: Annotated[str, Form()] = "",
    ):
        await bot_or_404(o, bot_id)
        try:
            compile(code, "<handler>", "exec")
        except SyntaxError as exc:
            flash(request, f"Not saved: syntax error on line {exc.lineno}: {exc.msg}", "error")
            return back(f"/bots/{bot_id}/handler")
        version = await o.db.save_version(bot_id, code, comment.strip()[:200])
        flash(request, f"Saved and activated version {version}.", "success")
        return back(f"/bots/{bot_id}/handler")

    @app.post("/bots/{bot_id}/handler/activate")
    async def activate_handler(
        request: Request, o: Orch, bot_id: int, version: Annotated[int, Form()] = 0
    ):
        await bot_or_404(o, bot_id)
        if version and not await o.db.get_version(bot_id, version):
            raise HTTPException(status_code=404, detail="Version not found")
        await o.db.activate_version(bot_id, version or None)
        flash(
            request,
            f"Version {version} is active."
            if version
            else "Handler disabled: updates are logged but not handled.",
            "success",
        )
        return back(f"/bots/{bot_id}/handler")

    @app.post("/bots/{bot_id}/handler/test")
    async def test_handler(o: Orch, bot_id: int, body: Annotated[dict[str, Any], Body()]):
        bot = await bot_or_404(o, bot_id)
        if body.get("update_row"):
            row = await o.db.get_update(bot_id, int(body["update_row"]))
            if not row:
                raise HTTPException(status_code=404, detail="Update not found")
            update = row["raw"]
        else:
            try:
                update = json.loads(body.get("update_json") or "")
            except ValueError:
                raise HTTPException(status_code=400, detail="Update JSON is not valid") from None
            if not isinstance(update, dict):
                raise HTTPException(status_code=400, detail="Update must be a JSON object")
        return await o.run_handler(
            bot, str(body.get("code", "")), 0, update, dry_run=bool(body.get("dry_run", True))
        )

    # API console

    @app.get("/bots/{bot_id}/console")
    async def console_page(request: Request, o: Orch, bot_id: int):
        bot = await bot_or_404(o, bot_id)
        history = await o.db.list_console_calls(bot_id)
        return render(
            request, "console.html", bot=bot, tab="console", history=history, methods=COMMON_METHODS
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
    async def files_page(request: Request, o: Orch, bot_id: int, path: str = ""):
        bot = await bot_or_404(o, bot_id)
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
        acc = []
        for part in Path(rel).parts:
            acc.append(part)
            crumbs.append({"name": part, "path": "/".join(acc)})
        usage = (
            sum(f.stat().st_size for f in root.rglob("*") if f.is_file()) if root.exists() else 0
        )
        return render(
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
    async def download_file(o: Orch, bot_id: int, path: str):
        await bot_or_404(o, bot_id)
        target = inside(bot_id, path)
        if not target.is_file():
            raise HTTPException(status_code=404, detail="File not found")
        return FileResponse(target, filename=target.name)

    @app.post("/bots/{bot_id}/files/delete")
    async def delete_file(request: Request, o: Orch, bot_id: int, path: Annotated[str, Form()]):
        await bot_or_404(o, bot_id)
        target = inside(bot_id, path)
        if target == bot_folder(bot_id).resolve():
            raise HTTPException(status_code=400, detail="Cannot delete the bot folder")
        if target.is_dir():
            shutil.rmtree(target)
        elif target.exists():
            target.unlink()
        flash(request, f"Deleted {path}.", "success")
        parent = str(Path(path).parent)
        return back(f"/bots/{bot_id}/files?path={'' if parent == '.' else parent}")

    # Settings

    @app.get("/bots/{bot_id}/settings")
    async def settings_page(request: Request, o: Orch, bot_id: int):
        bot = await bot_or_404(o, bot_id)
        return render(
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
        flash(request, "Polling paused. Updates wait on the Bot API server for up to 24 hours.")
        return back(f"/bots/{bot_id}/settings")

    @app.post("/bots/{bot_id}/resume")
    async def resume(request: Request, o: Orch, bot_id: int):
        await o.resume(bot_id)
        flash(request, "Polling started.", "success")
        return back(f"/bots/{bot_id}/settings")

    @app.post("/bots/{bot_id}/allowed-users")
    async def allowed_users(
        request: Request, o: Orch, bot_id: int, allowed: Annotated[str, Form()] = ""
    ):
        await bot_or_404(o, bot_id)
        ids = sorted({p for p in re.split(r"[\s,;]+", allowed) if p})
        if any(not p.isdigit() for p in ids):
            flash(request, "Allowed users must be numeric Telegram user ids.", "error")
            return back(f"/bots/{bot_id}/settings")
        await o.db.set_allowed_users(bot_id, ",".join(ids))
        flash(
            request,
            "Allowed users saved."
            if ids
            else "Allowed users cleared: the bot handles updates from everyone.",
            "success",
        )
        return back(f"/bots/{bot_id}/settings")

    @app.post("/bots/{bot_id}/secrets")
    async def set_secret(
        request: Request,
        o: Orch,
        bot_id: int,
        name: Annotated[str, Form()],
        value: Annotated[str, Form()],
    ):
        await bot_or_404(o, bot_id)
        name = name.strip()
        if not SECRET_NAME.match(name):
            flash(
                request,
                "Secret names may use letters, digits and _ and must not start with a digit.",
                "error",
            )
            return back(f"/bots/{bot_id}/settings")
        await o.db.set_secret(bot_id, name, o.cipher.encrypt(value))
        flash(request, f"Secret {name} saved.", "success")
        return back(f"/bots/{bot_id}/settings")

    @app.post("/bots/{bot_id}/secrets/delete")
    async def delete_secret(request: Request, o: Orch, bot_id: int, name: Annotated[str, Form()]):
        await o.db.delete_secret(bot_id, name)
        flash(request, f"Secret {name} deleted.", "success")
        return back(f"/bots/{bot_id}/settings")

    @app.post("/bots/{bot_id}/kv/delete")
    async def delete_kv(request: Request, o: Orch, bot_id: int, key: Annotated[str, Form()]):
        await o.db.kv_delete(bot_id, key)
        flash(request, f"Key {key} deleted.", "success")
        return back(f"/bots/{bot_id}/settings")

    @app.post("/bots/{bot_id}/cloud-logout")
    async def cloud_logout(request: Request, o: Orch, bot_id: int):
        bot = await bot_or_404(o, bot_id)
        for note in await o.log_out_from_cloud(o.token_of(bot)):
            flash(request, note)
        return back(f"/bots/{bot_id}/settings")

    @app.post("/bots/{bot_id}/local-logout")
    async def local_logout(request: Request, o: Orch, bot_id: int):
        try:
            await o.log_out_local(bot_id)
            flash(
                request,
                "Logged out from the local server. The cloud Bot API accepts the "
                "bot again in about 10 minutes.",
                "success",
            )
        except BotError as exc:
            flash(request, str(exc), "error")
        return back(f"/bots/{bot_id}/settings")

    @app.post("/bots/{bot_id}/delete")
    async def delete_bot(request: Request, o: Orch, bot_id: int):
        bot = await bot_or_404(o, bot_id)
        await o.delete_bot(bot_id)
        flash(request, f"@{bot['username']} removed. Its files are kept on disk.", "success")
        return back("/")

    # Internal API for the runner

    def runner_only(x_runner_secret: Annotated[str | None, Header()] = None) -> None:
        if not x_runner_secret or not hmac.compare_digest(x_runner_secret, settings.runner_secret):
            raise HTTPException(status_code=401, detail="invalid runner secret")

    def check_key(key: str) -> None:
        if not KV_KEY.match(key):
            raise HTTPException(status_code=400, detail="invalid key")

    @app.get("/internal/kv/{bot_id}/{key}", dependencies=[Depends(runner_only)])
    async def kv_get(o: Orch, bot_id: int, key: str):
        check_key(key)
        found, value = await o.db.kv_get(bot_id, key)
        if not found:
            raise HTTPException(status_code=404, detail="key not found")
        return {"value": value}

    @app.put("/internal/kv/{bot_id}/{key}", dependencies=[Depends(runner_only)])
    async def kv_put(o: Orch, bot_id: int, key: str, body: Annotated[dict[str, Any], Body()]):
        check_key(key)
        if not await o.db.get_bot(bot_id):
            raise HTTPException(status_code=404, detail="bot not found")
        await o.db.kv_set(bot_id, key, body.get("value"))
        return {"ok": True}

    @app.delete("/internal/kv/{bot_id}/{key}", dependencies=[Depends(runner_only)])
    async def kv_delete(o: Orch, bot_id: int, key: str):
        check_key(key)
        await o.db.kv_delete(bot_id, key)
        return {"ok": True}

    return app


def app() -> FastAPI:
    """Factory for `uvicorn --factory bot_orchestrator.web:app`."""
    return create_app()
