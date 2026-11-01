"""Web application: authentication, the bot list, global settings and the view modules."""

import asyncio
import hmac
from contextlib import asynccontextmanager
from typing import Annotated, Any
from urllib.parse import urlparse

from fastapi import Depends, FastAPI, Form, Request
from fastapi.responses import JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from starlette.middleware.sessions import SessionMiddleware

from . import __version__, internal_api, maintenance, views_bot, views_logic
from .config import Settings, get_settings
from .db import Database
from .notify import EVENTS, Notifier
from .security import TOKEN_FORMAT
from .service import BotError, Orchestrator
from .telegram import TelegramError
from .ui import HERE, Ui, human_size

PUBLIC_PATHS = ("/login", "/static/", "/health", "/internal/")


def create_app(
    settings: Settings | None = None, orchestrator: Orchestrator | None = None
) -> FastAPI:
    settings = settings or get_settings()
    state: dict[str, Any] = {}
    ui = Ui(settings, state)

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
    Orch = Annotated[Orchestrator, Depends(ui.orch)]

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

    # Health and login

    @app.get("/health")
    async def health() -> dict[str, str]:
        return {"status": "ok", "version": __version__}

    @app.get("/login")
    async def login_page(request: Request):
        return ui.render(request, "login.html")

    @app.post("/login")
    async def login(request: Request, password: Annotated[str, Form()] = ""):
        if hmac.compare_digest(password.encode(), settings.admin_password.encode()):
            request.session["auth"] = True
            return ui.back("/")
        await asyncio.sleep(1)
        ui.flash(request, "Wrong password.", "error")
        return ui.back("/login")

    @app.post("/logout")
    async def logout(request: Request):
        request.session.clear()
        return ui.back("/login")

    # Bot list

    @app.get("/")
    async def index(request: Request, o: Orch):
        bots = await o.db.list_bots()
        for bot in bots:
            bot["polling"] = o.is_polling(bot["id"])
        return ui.render(request, "index.html", bots=bots)

    @app.post("/bots")
    async def add_bot(
        request: Request,
        o: Orch,
        token: Annotated[str, Form()],
        log_out_cloud: Annotated[bool, Form()] = False,
    ):
        if not TOKEN_FORMAT.match(token.strip()):
            ui.flash(request, "That does not look like a bot token (123456:ABC...).", "error")
            return ui.back("/")
        try:
            bot, notes = await o.add_bot(token, log_out_cloud)
        except BotError as exc:
            ui.flash(request, str(exc), "error")
            return ui.back("/")
        for note in notes:
            ui.flash(request, note)
        ui.flash(request, f"@{bot['username']} added and polling started.", "success")
        return ui.back(f"/bots/{bot['id']}/handlers")

    # Global settings

    @app.get("/settings")
    async def app_settings(request: Request, o: Orch):
        cache = await asyncio.to_thread(maintenance.cache_usage, settings.bot_api_data_dir)
        disk = await asyncio.to_thread(maintenance.disk_percent_used, settings.files_root)
        files = await asyncio.to_thread(maintenance.folder_size, settings.files_root)
        return ui.render(
            request,
            "app_settings.html",
            notify=await o.notifier.config(),
            events=EVENTS,
            bots=await o.db.list_bots(),
            senders=await o.db.recent_senders(),
            cache=cache,
            disk=disk,
            files_size=files,
            settings=settings,
        )

    @app.post("/settings/notifications")
    async def save_notifications(request: Request, o: Orch):
        form = await request.form()
        bot_id = str(form.get("notify_bot_id") or "")
        chat_id = str(form.get("notify_chat_id") or "").strip()
        if chat_id and not chat_id.lstrip("-").isdigit():
            ui.flash(request, "The chat id must be a number, e.g. your Telegram user id.", "error")
            return ui.back("/settings")
        await o.db.set_setting("notify_bot_id", int(bot_id) if bot_id.isdigit() else None)
        await o.db.set_setting("notify_chat_id", int(chat_id) if chat_id else None)
        await o.db.set_setting(
            "notify_events", [e for e in form.getlist("notify_events") if e in EVENTS]
        )
        ui.flash(request, "Notification settings saved.", "success")
        return ui.back("/settings")

    @app.post("/settings/notifications/test")
    async def test_notification(request: Request, o: Orch):
        config = await o.notifier.config()
        if not config["notify_bot_id"] or not config["notify_chat_id"]:
            ui.flash(request, "Choose a bot and a chat first.", "error")
            return ui.back("/settings")
        try:
            await Notifier(o).deliver(
                config["notify_bot_id"],
                config["notify_chat_id"],
                "✅ Test notification from Bot Orchestrator." + o.notifier.link("/settings"),
            )
            ui.flash(request, "Test notification sent.", "success")
        except TelegramError as exc:
            ui.flash(
                request,
                f"Could not send: {exc.description}. The chat must have started the bot first.",
                "error",
            )
        return ui.back("/settings")

    @app.post("/settings/cache/clean")
    async def clean_cache(request: Request, o: Orch, hours: Annotated[float, Form()] = 0):
        removed = await o.clean_cache(max(hours, 0))
        ui.flash(
            request,
            f"Removed {removed.files} cached files ({human_size(removed.bytes)}).",
            "success",
        )
        return ui.back("/settings")

    views_bot.register(app, ui)
    views_logic.register(app, ui)
    internal_api.register(app, ui)
    return app


def app() -> FastAPI:
    """Factory for `uvicorn --factory bot_orchestrator.web:app`."""
    return create_app()
