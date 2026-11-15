"""MCP server: lets an AI agent see and manage everything the web UI can.

It is a second interface over the same operations (`ops`) the web pages use, so an
agent follows exactly the rules a person in the UI does. Mounted at `/mcp` with the
Streamable HTTP transport (stateless, JSON responses) and protected by a bearer
token (`MCP_TOKEN`); the endpoint does not exist while the token is empty.

Tools take a bot as its numeric id or @username, and handlers and modules by id or
name. Times are ISO 8601 in the orchestrator's timezone. Bot tokens and secret
values are never returned.
"""

import contextlib
import datetime as dt
import functools
import hmac
import inspect
from collections.abc import AsyncIterator, Callable
from pathlib import Path
from typing import Annotated, Any, Literal
from zoneinfo import ZoneInfo

from fastapi import FastAPI
from mcp.server.mcpserver import MCPServer
from mcp.server.mcpserver.exceptions import ToolError
from mcp.server.transport_security import TransportSecuritySettings
from mcp_types import ToolAnnotations
from pydantic import Field
from starlette.responses import JSONResponse
from starlette.routing import Route
from starlette.types import Receive, Scope, Send

from . import __version__
from .config import Settings
from .errors import OpError
from .ops import Operations

PATH = "/mcp"

SKILL_DIR = Path(__file__).resolve().parents[2] / "skills" / "bot-orchestrator-handlers"
DOCS = {
    "skill": ("SKILL.md", "How handlers run, the workflow and key rules (start here)"),
    "sdk": ("references/sdk.md", "Every ctx member: arguments, return values, errors"),
    "platform": (
        "references/platform.md",
        "Routes, update and content types, schedules, modules, packages, limits, paths",
    ),
    "examples": ("references/examples.md", "Complete working handlers"),
}

INSTRUCTIONS = f"""\
bot-orchestrator {__version__}: a self-hosted platform running many Telegram bots on one
local Bot API server. Each bot has handlers (Python code with versions, run in an isolated
runner), routes (which handler gets which update; checked top to bottom, the first enabled
match wins), schedules (cron), background tasks, secrets, stored values (ctx.kv) and a
files folder. Shared modules (`from shared import name`) and extra pip packages are global.

Start with get_overview. Before writing handler or module code, read the handler SDK with
read_docs("skill") and read_docs("sdk"). Saving code activates it at once for live traffic,
so check new code with test_handler (dry_run=true records Bot API calls without sending
them) before save_handler. Refer to bots by id or @username, to handlers and modules by id
or name. Tools that delete things cannot be undone.
"""

# Fields that hold Unix timestamps in operation results.
_TIME_KEYS = ("modified", "last_seen")

Bot = Annotated[int | str, Field(description="Bot id or @username")]
HandlerRef = Annotated[int | str, Field(description="Handler id or name")]
ModuleRef = Annotated[int | str, Field(description="Module id or name")]
UpdateJson = Annotated[
    dict[str, Any] | None,
    Field(description="A Telegram update object to use instead of a logged one"),
]
RowId = Annotated[
    int | None,
    Field(description="Id of an entry in the bot's update log (from list_updates)"),
]
RouteKind = Literal["command", "regex", "content", "callback", "update", "any"]


def _clean(value: Any, tz: ZoneInfo) -> Any:
    """Make operation results agent-friendly: ISO times, no private fields."""
    if isinstance(value, dict):
        result = {}
        for key, item in value.items():
            if key == "token_enc":
                continue
            if (key.endswith("_at") or key in _TIME_KEYS) and isinstance(item, int | float):
                item = dt.datetime.fromtimestamp(item, tz).isoformat(timespec="seconds")
            result[key] = _clean(item, tz)
        return result
    if isinstance(value, list | tuple):
        return [_clean(v, tz) for v in value]
    return value


def build_server(get_ops: Callable[[], Operations], settings: Settings) -> MCPServer:
    """The MCP server with every tool; write tools are left out in read-only mode."""
    tz = ZoneInfo(settings.tz)
    server = MCPServer(
        name="bot-orchestrator",
        title="Bot Orchestrator",
        version=__version__,
        instructions=INSTRUCTIONS,
        log_level="WARNING",
    )

    def tool(*, read_only: bool = False, destructive: bool = False, idempotent: bool = False):
        """Register a tool whose OpErrors reach the agent as readable tool errors."""

        def decorator(fn: Callable[..., Any]) -> Callable[..., Any]:
            if settings.mcp_read_only and not read_only:
                return fn

            @functools.wraps(fn)
            async def wrapper(*args: Any, **kwargs: Any) -> dict[str, Any]:
                try:
                    result = await fn(*args, **kwargs)
                except OpError as exc:
                    raise ToolError(str(exc)) from None
                return _clean(result, tz)

            wrapper.__signature__ = inspect.signature(fn)  # type: ignore[attr-defined]
            server.add_tool(
                wrapper,
                name=fn.__name__,
                description=inspect.getdoc(fn),
                annotations=ToolAnnotations(
                    read_only_hint=read_only,
                    destructive_hint=destructive if not read_only else None,
                    idempotent_hint=idempotent or read_only,
                    open_world_hint=False,
                ),
            )
            return fn

        return decorator

    def ops() -> Operations:
        return get_ops()

    # Overview and bots

    @tool(read_only=True)
    async def get_overview() -> dict[str, Any]:
        """The platform at a glance: version, timezone, every bot with its status and 24-hour
        counters (updates, errors, active tasks, enabled routes), bot-runner reachability,
        package sync and storage use."""
        return await ops().system.overview()

    @tool(read_only=True)
    async def get_bot(bot: Bot) -> dict[str, Any]:
        """Everything configured for one bot: status, allowed users, /purge, handlers with
        their active versions, routes in order, schedules, secret names and stored keys."""
        o = ops()
        info = await o.bots.get(bot)
        bot_id = info["id"]
        return {
            "bot": info,
            "handlers": await o.handlers.list(bot_id),
            "routes": await o.routes.list(bot_id),
            "schedules": await o.schedules.list(bot_id),
            "secret_names": await o.bots.secret_names(bot_id),
            "stored_keys": [row["key"] for row in await o.bots.kv_list(bot_id)],
            "recent_senders": await o.bots.recent_senders(bot_id, limit=8),
        }

    @tool()
    async def add_bot(
        token: Annotated[str, Field(description="Token from @BotFather, 123456:ABC...")],
        log_out_cloud: Annotated[
            bool,
            Field(
                description="Log the bot out of api.telegram.org first (needed for a bot that "
                "was ever used with the cloud Bot API)"
            ),
        ] = True,
    ) -> dict[str, Any]:
        """Add a bot by its token: checks it with getMe on the local server, stores it
        encrypted and starts polling. Re-adding an existing bot updates its token."""
        bot, notes = await ops().bots.add(token, log_out_cloud)
        return {"bot": bot, "notes": notes}

    @tool(idempotent=True)
    async def set_bot_polling(
        bot: Bot, running: Annotated[bool, Field(description="True starts, False pauses")]
    ) -> dict[str, Any]:
        """Start or pause polling. While paused, updates wait on the Bot API server for up
        to 24 hours."""
        if running:
            await ops().bots.resume(bot)
        else:
            await ops().bots.pause(bot)
        return {"bot": await ops().bots.get(bot)}

    @tool(idempotent=True)
    async def update_bot_settings(
        bot: Bot,
        allowed_users: Annotated[
            list[str] | None,
            Field(
                description="Replace the allow list with these numeric user ids and @usernames; "
                "an empty list lets everyone use the bot; omit to keep it"
            ),
        ] = None,
        purge_command: Annotated[
            bool | None,
            Field(description="Turn the built-in /purge command on or off; omit to keep it"),
        ] = None,
    ) -> dict[str, Any]:
        """Change who may use the bot and the built-in /purge command (deletes the last
        48 hours of a private chat). Updates from other users are logged as `ignored`."""
        o = ops()
        if allowed_users is not None:
            await o.bots.set_allowed_users(bot, allowed_users)
        if purge_command is not None:
            await o.bots.set_purge_command(bot, purge_command)
        return {"bot": await o.bots.get(bot)}

    @tool(destructive=True)
    async def log_out_bot(
        bot: Bot,
        server: Annotated[
            Literal["cloud", "local"],
            Field(
                description="cloud: log out of api.telegram.org so the local server can take "
                "the bot; local: pause and log out of the local server to move it back"
            ),
        ],
    ) -> dict[str, Any]:
        """Move a bot between the cloud Bot API and the local server."""
        if server == "cloud":
            return {"notes": await ops().bots.log_out_cloud(bot)}
        await ops().bots.log_out_local(bot)
        return {
            "notes": ["Logged out from the local server; the cloud accepts it in about 10 min."]
        }

    @tool(destructive=True)
    async def delete_bot(bot: Bot) -> dict[str, Any]:
        """Remove a bot with its handlers, routes, schedules, log, secrets and stored values.
        Its files stay on disk. Cannot be undone."""
        removed = await ops().bots.delete(bot)
        return {"deleted": {"id": removed["id"], "username": removed["username"]}}

    # Update log

    @tool(read_only=True)
    async def list_updates(
        bot: Bot,
        status: Annotated[
            Literal["ok", "error", "timeout", "dispatch_error", "pending", "no_route", "ignored"]
            | None,
            Field(description="Only entries with this status"),
        ] = None,
        limit: Annotated[int, Field(ge=1, le=100)] = 20,
    ) -> dict[str, Any]:
        """The latest updates the bot received (messages, button presses, scheduled runs),
        newest first: sender, a summary of the content, the handler and version that ran,
        status, duration and the last error line."""
        return {"updates": await ops().bots.updates(bot, status=status, limit=limit)}

    @tool(read_only=True)
    async def get_update(
        bot: Bot, row_id: Annotated[int, Field(description="From list_updates")]
    ) -> dict[str, Any]:
        """One update log entry in full: the raw Telegram update, the route that matched,
        handler logs, Bot API calls made, the error traceback and tasks it started."""
        return await ops().bots.update(bot, row_id)

    # Handlers

    @tool(read_only=True)
    async def list_handlers(bot: Bot) -> dict[str, Any]:
        """The bot's handlers with their active versions."""
        return {"handlers": await ops().handlers.list(bot)}

    @tool(read_only=True)
    async def get_handler(
        bot: Bot,
        handler: HandlerRef,
        version: Annotated[int | None, Field(description="Default: the active version")] = None,
    ) -> dict[str, Any]:
        """A handler's code (active or a given version), its version history, and the routes
        and schedules that run it."""
        return await ops().handlers.get(bot, handler, version)

    @tool(read_only=True)
    async def list_handler_templates() -> dict[str, Any]:
        """Starting points for new handlers with their code and suggested route."""
        return {"templates": ops().handlers.templates()}

    @tool()
    async def create_handler(
        bot: Bot,
        name: Annotated[str, Field(description="1-40 letters, digits, - and _")],
        code: Annotated[
            str | None,
            Field(description="Python code defining handle(update, ctx); omit to use a template"),
        ] = None,
        template: Annotated[str, Field(description="Template name when code is omitted")] = "echo",
        comment: Annotated[str | None, Field(description="Note for version 1")] = None,
        add_template_route: Annotated[
            bool, Field(description="Also add the template's suggested route (templates only)")
        ] = False,
    ) -> dict[str, Any]:
        """Create a handler; version 1 is active at once. It runs only once a route or
        schedule points to it."""
        return await ops().handlers.create(
            bot,
            name,
            code=code,
            template=template,
            comment=comment,
            add_route=add_template_route and code is None,
        )

    @tool()
    async def save_handler(
        bot: Bot,
        handler: HandlerRef,
        code: Annotated[str, Field(description="The complete new code")],
        comment: Annotated[str | None, Field(description="What changed")] = None,
    ) -> dict[str, Any]:
        """Save handler code as a new version and activate it for live traffic at once.
        Refused on syntax errors; warns about missing shared modules. Test first with
        test_handler."""
        return await ops().handlers.save(bot, handler, code, comment)

    @tool(idempotent=True)
    async def activate_handler_version(
        bot: Bot, handler: HandlerRef, version: int
    ) -> dict[str, Any]:
        """Make a saved version the active one, e.g. to roll back."""
        await ops().handlers.activate(bot, handler, version)
        return {"active_version": version}

    @tool()
    async def rename_handler(bot: Bot, handler: HandlerRef, name: str) -> dict[str, Any]:
        """Rename a handler. Routes and schedules keep pointing to it."""
        changed = await ops().handlers.rename(bot, handler, name)
        return {"renamed": changed, "name": name.strip()}

    @tool(destructive=True)
    async def delete_handler(bot: Bot, handler: HandlerRef) -> dict[str, Any]:
        """Delete a handler with all its versions, routes and schedules. Cannot be undone."""
        removed = await ops().handlers.delete(bot, handler)
        return {"deleted": {"id": removed["id"], "name": removed["name"]}}

    @tool()
    async def test_handler(
        bot: Bot,
        handler: HandlerRef,
        code: Annotated[
            str | None, Field(description="Unsaved code to try; default: the active version")
        ] = None,
        row_id: RowId = None,
        update: UpdateJson = None,
        dry_run: Annotated[
            bool,
            Field(
                description="Record Bot API calls that change something instead of sending "
                "them (reads such as getChat still run)"
            ),
        ] = True,
    ) -> dict[str, Any]:
        """Run handler code once in bot-runner with a logged update (row_id) or the given
        update JSON. Returns status, error traceback, logs and the Bot API calls made.
        Background tasks it starts do run. Nothing is saved."""
        return await ops().handlers.test(
            bot, handler, code=code, update_row=row_id, update=update, dry_run=dry_run
        )

    # Routes and commands

    @tool(read_only=True)
    async def list_routes(bot: Bot) -> dict[str, Any]:
        """The bot's routes in the order they are checked (the first enabled match runs its
        handler), and what each route kind's pattern means."""
        return {"routes": await ops().routes.list(bot), "kinds": ops().routes.kinds()}

    @tool()
    async def create_route(
        bot: Bot,
        kind: Annotated[RouteKind, Field(description="See list_routes for each kind's pattern")],
        handler: HandlerRef,
        pattern: Annotated[
            str,
            Field(
                description="command: name without /; regex: Python regex on text or caption; "
                "content: e.g. 'document, photo'; callback: callback_data prefix; "
                "update: e.g. 'edited_message, schedule'; any: empty"
            ),
        ] = "",
        description: Annotated[
            str, Field(description="For command routes: the text in the bot menu")
        ] = "",
        enabled: bool = True,
        position: Annotated[
            int | None, Field(description="1 = checked first; default: last", ge=1)
        ] = None,
    ) -> dict[str, Any]:
        """Add a route sending matching updates to a handler. After changing command routes,
        call publish_bot_commands to update the menu in Telegram."""
        return await ops().routes.create(
            bot, kind, pattern, handler, description, enabled, position
        )

    @tool(idempotent=True)
    async def update_route(
        bot: Bot,
        route_id: int,
        kind: RouteKind | None = None,
        pattern: Annotated[str | None, Field(description="Required when the kind changes")] = None,
        handler: Annotated[int | str | None, Field(description="Handler id or name")] = None,
        description: str | None = None,
        enabled: bool | None = None,
        position: Annotated[int | None, Field(ge=1, description="1 = checked first")] = None,
    ) -> dict[str, Any]:
        """Change a route; omitted fields keep their values."""
        changes = {
            k: v
            for k, v in {
                "kind": kind,
                "pattern": pattern,
                "handler": handler,
                "description": description,
                "enabled": enabled,
            }.items()
            if v is not None
        }
        route = await ops().routes.update(bot, route_id, **changes)
        if position is not None:
            await ops().routes.move(bot, route_id, position)
        return route

    @tool(destructive=True)
    async def delete_route(bot: Bot, route_id: int) -> dict[str, Any]:
        """Delete a route (its handler stays)."""
        await ops().routes.delete(bot, route_id)
        return {"deleted": route_id}

    @tool(idempotent=True)
    async def reorder_routes(
        bot: Bot,
        route_ids: Annotated[
            list[int],
            Field(description="Route ids in the new order; unlisted routes follow them"),
        ],
    ) -> dict[str, Any]:
        """Set the order in which routes are checked."""
        return {"routes": await ops().routes.reorder(bot, route_ids)}

    @tool(read_only=True)
    async def match_route(
        bot: Bot, row_id: RowId = None, update: UpdateJson = None
    ) -> dict[str, Any]:
        """Which route (and handler) would get a logged update or the given update now."""
        route = await ops().routes.match(bot, update_row=row_id, update=update)
        return {"route": route}

    @tool(read_only=True)
    async def get_bot_commands(bot: Bot) -> dict[str, Any]:
        """The command menu Telegram shows now (getMyCommands) and the menu that
        publish_bot_commands would set from the enabled command routes."""
        o = ops()
        return {
            "published": await o.routes.published_commands(bot),
            "from_routes": o.routes.menu_commands(await o.routes.list(bot)),
        }

    @tool(idempotent=True)
    async def publish_bot_commands(bot: Bot) -> dict[str, Any]:
        """Set the bot's command menu (setMyCommands) from its enabled command routes; each
        route's description is the menu text."""
        return {"commands": await ops().routes.publish_commands(bot)}

    # Schedules

    @tool(read_only=True)
    async def list_schedules(bot: Bot) -> dict[str, Any]:
        """The bot's schedules with their next and last runs."""
        return {"schedules": await ops().schedules.list(bot), "timezone": settings.tz}

    @tool(read_only=True)
    async def preview_cron(
        expression: Annotated[str, Field(description="Five fields: minute hour day month weekday")],
        count: Annotated[int, Field(ge=1, le=20)] = 5,
    ) -> dict[str, Any]:
        """The next run times of a cron expression in the orchestrator's timezone."""
        return {"timezone": settings.tz, "runs": ops().schedules.preview(expression, count)}

    @tool()
    async def create_schedule(
        bot: Bot,
        name: str,
        cron: Annotated[str, Field(description="Five-field cron in the orchestrator's timezone")],
        handler: HandlerRef,
        chat_id: Annotated[
            int | None, Field(description="Chat that ctx.reply sends to in scheduled runs")
        ] = None,
        enabled: bool = True,
    ) -> dict[str, Any]:
        """Run a handler on a schedule. The handler gets update.schedule (id, name, cron,
        time, manual, chat). Runs missed while the orchestrator was down are skipped."""
        return await ops().schedules.create(bot, name, cron, handler, chat_id, enabled)

    @tool(idempotent=True)
    async def update_schedule(
        bot: Bot,
        schedule_id: int,
        name: str | None = None,
        cron: str | None = None,
        handler: Annotated[int | str | None, Field(description="Handler id or name")] = None,
        chat_id: Annotated[int | None, Field(description="0 removes the chat")] = None,
        enabled: bool | None = None,
    ) -> dict[str, Any]:
        """Change a schedule; omitted fields keep their values."""
        changes: dict[str, Any] = {
            k: v
            for k, v in {
                "name": name,
                "cron_expression": cron,
                "handler": handler,
                "enabled": enabled,
            }.items()
            if v is not None
        }
        if chat_id is not None:
            changes["chat_id"] = chat_id or None
        return await ops().schedules.update(bot, schedule_id, **changes)

    @tool()
    async def run_schedule(bot: Bot, schedule_id: int) -> dict[str, Any]:
        """Fire a schedule now. The run happens in the background; read its result with
        get_update(row_id)."""
        schedule, row_id = await ops().schedules.run_now(bot, schedule_id)
        return {"schedule": schedule["name"], "row_id": row_id}

    @tool(destructive=True)
    async def delete_schedule(bot: Bot, schedule_id: int) -> dict[str, Any]:
        """Delete a schedule."""
        removed = await ops().schedules.delete(bot, schedule_id)
        return {"deleted": {"id": removed["id"], "name": removed["name"]}}

    # Background tasks

    @tool(read_only=True)
    async def list_tasks(
        bot: Bot,
        status: Literal["queued", "running", "ok", "error", "timeout", "cancelled", "interrupted"]
        | None = None,
        limit: Annotated[int, Field(ge=1, le=100)] = 20,
    ) -> dict[str, Any]:
        """Background tasks started by ctx.background, newest first."""
        return {"tasks": await ops().tasks.list(bot, status=status, limit=limit)}

    @tool(read_only=True)
    async def get_task(bot: Bot, task_id: int) -> dict[str, Any]:
        """One background task: arguments, progress, logs, Bot API calls and error."""
        task = await ops().tasks.get(bot, task_id)
        task.pop("code", None)
        return task

    @tool(idempotent=True)
    async def cancel_task(bot: Bot, task_id: int) -> dict[str, Any]:
        """Cancel a queued or running task."""
        return {"cancelled": await ops().tasks.cancel(bot, task_id)}

    @tool()
    async def retry_task(bot: Bot, task_id: int) -> dict[str, Any]:
        """Run a task again with the same code, module versions and arguments."""
        return {"task_id": await ops().tasks.retry(bot, task_id)}

    # Shared modules and packages

    @tool(read_only=True)
    async def list_modules() -> dict[str, Any]:
        """Shared modules (imported as `from shared import name` by any bot) with their
        docstrings and who imports them."""
        return {"modules": await ops().modules.list()}

    @tool(read_only=True)
    async def get_module(
        module: ModuleRef,
        version: Annotated[int | None, Field(description="Default: the active version")] = None,
    ) -> dict[str, Any]:
        """A module's code, versions, function outline and the handlers and modules that
        import it."""
        return await ops().modules.get(module, version)

    @tool()
    async def create_module(
        name: Annotated[str, Field(description="Lowercase Python identifier, e.g. tasks_db")],
        code: Annotated[str | None, Field(description="Omit for a small example")] = None,
        comment: str | None = None,
    ) -> dict[str, Any]:
        """Create a shared module. Modules cannot be renamed later (imports would break)."""
        return await ops().modules.create(name, code, comment)

    @tool()
    async def save_module(
        module: ModuleRef, code: str, comment: str | None = None
    ) -> dict[str, Any]:
        """Save module code as a new version and activate it: new runs of every handler
        that imports it use it at once. Test first with test_module."""
        return await ops().modules.save(module, code, comment)

    @tool(idempotent=True)
    async def activate_module_version(module: ModuleRef, version: int) -> dict[str, Any]:
        """Make a saved module version the active one, e.g. to roll back."""
        await ops().modules.activate(module, version)
        return {"active_version": version}

    @tool(destructive=True)
    async def delete_module(module: ModuleRef) -> dict[str, Any]:
        """Delete a module; refused while any active handler or module imports it."""
        removed = await ops().modules.delete(module)
        return {"deleted": {"id": removed["id"], "name": removed["name"]}}

    @tool()
    async def test_module(
        module: ModuleRef,
        code: Annotated[str, Field(description="Unsaved module code to try")],
        bot: Bot,
        handler: Annotated[int | str, Field(description="A handler that imports the module")],
        row_id: RowId = None,
        update: UpdateJson = None,
        dry_run: bool = True,
    ) -> dict[str, Any]:
        """Run a handler's active version once with unsaved code for this module."""
        return await ops().modules.test(
            module, bot, handler, code, update_row=row_id, update=update, dry_run=dry_run
        )

    @tool(read_only=True)
    async def get_packages() -> dict[str, Any]:
        """The extra pip packages handlers can import, and the runner's install state."""
        return await ops().modules.packages()

    @tool(idempotent=True)
    async def set_packages(
        requirements: Annotated[
            str, Field(description="requirements.txt lines, package specifications only")
        ],
    ) -> dict[str, Any]:
        """Replace the global package list and start installing it in bot-runner; check
        progress with get_packages."""
        return await ops().modules.set_packages(requirements)

    # Secrets, stored values and files

    @tool(idempotent=True)
    async def set_secret(
        bot: Bot,
        name: Annotated[str, Field(description="Letters, digits and _; read as ctx.secrets[name]")],
        value: str,
    ) -> dict[str, Any]:
        """Store a secret for handlers, encrypted. Values can never be read back."""
        return {"saved": await ops().bots.set_secret(bot, name, value)}

    @tool(destructive=True, idempotent=True)
    async def delete_secret(bot: Bot, name: str) -> dict[str, Any]:
        """Delete a secret."""
        await ops().bots.delete_secret(bot, name)
        return {"deleted": name}

    @tool(read_only=True)
    async def list_stored_values(bot: Bot) -> dict[str, Any]:
        """Values handlers keep with ctx.kv, with their keys."""
        return {"values": await ops().bots.kv_list(bot)}

    @tool(idempotent=True)
    async def set_stored_value(
        bot: Bot, key: str, value: Annotated[Any, Field(description="Any JSON value")]
    ) -> dict[str, Any]:
        """Set a value handlers read with ctx.kv.get(key)."""
        await ops().bots.kv_set(bot, key, value)
        return {"saved": key}

    @tool(destructive=True, idempotent=True)
    async def delete_stored_value(bot: Bot, key: str) -> dict[str, Any]:
        """Delete a ctx.kv value."""
        await ops().bots.kv_delete(bot, key)
        return {"deleted": key}

    @tool(read_only=True)
    async def list_files(
        bot: Bot, path: Annotated[str, Field(description="Folder inside the bot folder")] = ""
    ) -> dict[str, Any]:
        """Files and folders in the bot's folder (where ctx.files saves)."""
        return await ops().files.list(bot, path)

    @tool(read_only=True)
    async def read_file(
        bot: Bot,
        path: str,
        max_bytes: Annotated[int, Field(ge=1, le=262144)] = 65536,
    ) -> dict[str, Any]:
        """The start of a text file in the bot's folder."""
        return await ops().files.read_text(bot, path, max_bytes)

    @tool(destructive=True)
    async def delete_file(bot: Bot, path: str) -> dict[str, Any]:
        """Delete a file, or a folder with everything in it, from the bot's folder."""
        await ops().files.delete(bot, path)
        return {"deleted": path}

    # Bot API

    @tool()
    async def call_bot_api(
        bot: Bot,
        method: Annotated[str, Field(description="Bot API method, e.g. getMe, sendMessage")],
        params: Annotated[dict[str, Any] | None, Field(description="Method parameters")] = None,
    ) -> dict[str, Any]:
        """Call any Bot API method as the bot on the local server and return Telegram's
        answer, like the API console. Real calls: sendMessage sends. getUpdates is refused
        while the bot polls."""
        return await ops().bots.console_call(bot, method, params)

    # Platform settings

    @tool(read_only=True)
    async def get_notifications() -> dict[str, Any]:
        """Where failure notifications go (a managed bot and a chat) and which events."""
        return await ops().system.notifications()

    @tool(idempotent=True)
    async def set_notifications(
        bot: Annotated[
            int | str | None, Field(description="Bot that sends them; omit to keep")
        ] = None,
        chat_id: Annotated[int | None, Field(description="Chat to send to; 0 turns off")] = None,
        events: Annotated[
            list[str] | None, Field(description="Event names from get_notifications")
        ] = None,
    ) -> dict[str, Any]:
        """Configure failure notifications; omitted fields keep their values."""
        o = ops()
        current = await o.system.notifications()
        return await o.system.set_notifications(
            bot if bot is not None else current["notify_bot_id"],
            (chat_id or None) if chat_id is not None else current["notify_chat_id"],
            events if events is not None else current["notify_events"],
        )

    @tool()
    async def send_test_notification() -> dict[str, Any]:
        """Send a test message to the notification chat."""
        await ops().system.test_notification()
        return {"sent": True}

    @tool(destructive=True)
    async def clean_bot_api_cache(
        older_than_hours: Annotated[float, Field(ge=0)] = 0,
    ) -> dict[str, Any]:
        """Delete files the local Bot API server downloaded (getFile cache)."""
        removed = await ops().system.clean_cache(older_than_hours)
        return {"files": removed.files, "bytes": removed.bytes}

    # Documentation

    @tool(read_only=True)
    async def read_docs(
        topic: Annotated[
            Literal["skill", "sdk", "platform", "examples"],
            Field(description="; ".join(f"{k}: {v[1]}" for k, v in DOCS.items())),
        ],
    ) -> dict[str, Any]:
        """The handler developer reference: how to write handlers, background tasks,
        schedules and shared modules for this platform, kept in sync with the code."""
        return {"topic": topic, "text": read_doc(topic)}

    for key, (_, description) in DOCS.items():
        server.resource(
            f"docs://handlers/{key}",
            name=f"handler-docs-{key}",
            description=description,
            mime_type="text/markdown",
        )(_doc_reader(key))

    return server


def _doc_reader(topic: str) -> Callable[[], str]:
    def read() -> str:
        return read_doc(topic)

    read.__name__ = f"read_{topic}_docs"
    return read


def read_doc(topic: str) -> str:
    path = SKILL_DIR / DOCS[topic][0]
    if not path.is_file():
        raise OpError(f"Documentation not found at {path}")
    return path.read_text(encoding="utf-8")


class McpEndpoint:
    """The ASGI endpoint at /mcp: bearer-token check, then the MCP session manager."""

    def __init__(self, settings: Settings, get_ops: Callable[[], Operations]):
        self.settings = settings
        self.enabled = bool(settings.mcp_token)
        self.server = build_server(get_ops, settings)
        self._token = settings.mcp_token.encode()

    def register(self, app: FastAPI) -> None:
        if not self.enabled:
            return
        # Builds the session manager. Stateless: every request stands alone, so restarts
        # and several agents need no session bookkeeping.
        self.server.streamable_http_app(
            streamable_http_path=PATH,
            stateless_http=True,
            json_response=True,
            # The bearer token protects the endpoint; the panel is reached by LAN host names
            # and IPs that cannot be listed in advance.
            transport_security=TransportSecuritySettings(enable_dns_rebinding_protection=False),
        )
        app.router.routes.append(Route(PATH, self))

    @contextlib.asynccontextmanager
    async def running(self) -> AsyncIterator[None]:
        if not self.enabled:
            yield
            return
        async with self.server.session_manager.run():
            yield

    def _authorized(self, scope: Scope) -> bool:
        for name, value in scope.get("headers", []):
            if name == b"authorization":
                scheme, _, token = value.partition(b" ")
                return scheme.lower() == b"bearer" and hmac.compare_digest(token, self._token)
        return False

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if not self._authorized(scope):
            response = JSONResponse(
                {"error": "invalid_token", "error_description": "Bearer token required"},
                status_code=401,
                headers={"WWW-Authenticate": 'Bearer realm="bot-orchestrator"'},
            )
            await response(scope, receive, send)
            return
        await self.server.session_manager.handle_request(scope, receive, send)
