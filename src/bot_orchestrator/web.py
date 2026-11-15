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

from . import __version__, internal_api, mcp_server, views_bot, views_logic, views_shared
from .config import Settings, get_settings
from .db import Database
from .errors import OpError
from .notify import EVENTS
from .ops import Operations
from .service import Orchestrator
from .ui import FAILED, HERE, Ui, human_size

# Paths with their own authentication (or none); everything else needs a session.
PUBLIC_PATHS = ("/login", "/static/", "/health", "/internal/", mcp_server.PATH)


def create_app(
    settings: Settings | None = None, orchestrator: Orchestrator | None = None
) -> FastAPI:
    settings = settings or get_settings()
    state: dict[str, Any] = {}
    ui = Ui(settings, state)
    mcp_endpoint = mcp_server.McpEndpoint(settings, ui.ops)

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        if orchestrator is not None:
            state["o"] = orchestrator
        else:
            db = Database(settings.database_path)
            await db.connect()
            state["o"] = Orchestrator(settings, db)
        state["ops"] = Operations(state["o"])
        await state["o"].start()
        try:
            async with mcp_endpoint.running():
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
    Ops = Annotated[Operations, Depends(ui.ops)]

    @app.exception_handler(OpError)
    async def op_error(request: Request, exc: OpError):
        # Errors the views let through (NotFound above all) answer like HTTPException.
        return JSONResponse({"detail": str(exc)}, status_code=exc.status)

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
    async def index(request: Request, ops: Ops):
        return ui.render(request, "index.html", bots=await ops.bots.list())

    @app.post("/bots")
    async def add_bot(
        request: Request,
        ops: Ops,
        token: Annotated[str, Form()],
        log_out_cloud: Annotated[bool, Form()] = False,
    ):
        added = await ui.act(request, ops.bots.add(token, log_out_cloud))
        if added is FAILED:
            return ui.back("/")
        bot, notes = added
        for note in notes:
            ui.flash(request, note)
        ui.flash(request, f"@{bot['username']} added and polling started.", "success")
        return ui.back(f"/bots/{bot['id']}/handlers")

    # Global settings

    @app.get("/settings")
    async def app_settings(request: Request, ops: Ops):
        storage = await ops.system.storage()
        return ui.render(
            request,
            "app_settings.html",
            notify=await ops.orchestrator.notifier.config(),
            events=EVENTS,
            bots=await ops.bots.list(),
            senders=await ops.bots.recent_senders(),
            cache={"files": storage["cache_files"], "bytes": storage["cache_bytes"]},
            disk=storage["disk_percent_used"],
            files_size=storage["files_bytes"],
            settings=settings,
            mcp_enabled=bool(settings.mcp_token),
        )

    @app.post("/settings/notifications")
    async def save_notifications(request: Request, ops: Ops):
        form = await request.form()
        saved = await ui.act(
            request,
            ops.system.set_notifications(
                str(form.get("notify_bot_id") or ""),
                str(form.get("notify_chat_id") or ""),
                [e for e in form.getlist("notify_events") if e in EVENTS],
            ),
        )
        if saved is not FAILED:
            ui.flash(request, "Notification settings saved.", "success")
        return ui.back("/settings")

    @app.post("/settings/notifications/test")
    async def test_notification(request: Request, ops: Ops):
        if await ui.act(request, ops.system.test_notification()) is not FAILED:
            ui.flash(request, "Test notification sent.", "success")
        return ui.back("/settings")

    @app.post("/settings/cache/clean")
    async def clean_cache(request: Request, ops: Ops, hours: Annotated[float, Form()] = 0):
        removed = await ops.system.clean_cache(hours)
        ui.flash(
            request,
            f"Removed {removed.files} cached files ({human_size(removed.bytes)}).",
            "success",
        )
        return ui.back("/settings")

    views_bot.register(app, ui)
    views_logic.register(app, ui)
    views_shared.register(app, ui)
    internal_api.register(app, ui)
    mcp_endpoint.register(app)
    return app


def app() -> FastAPI:
    """Factory for `uvicorn --factory bot_orchestrator.web:app`."""
    return create_app()
