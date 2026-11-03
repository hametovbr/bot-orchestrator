"""Bot lifecycle, polling, routing, background tasks and schedules."""

from __future__ import annotations

import asyncio
import contextlib
import datetime as dt
import json
import logging
import time
import traceback
import uuid
from typing import Any
from zoneinfo import ZoneInfo

import httpx

from . import cron, maintenance, purge
from .config import Settings
from .db import Database
from .notify import Notifier
from .packages import Packages
from .routing import find_route
from .security import Cipher, mask, mask_value
from .telegram import BotApi, TelegramError
from .updates import describe, parse_allowed, sender

PURGE_HANDLER = "/purge (built-in)"

log = logging.getLogger("bot_orchestrator")

FAILED = ("error", "timeout", "dispatch_error", "interrupted")
TASK_ACTIVE = ("queued", "running")


class BotError(Exception):
    """A user-facing error from a bot operation."""


def _last_line(error: str | None) -> str:
    lines = (error or "").strip().splitlines()
    return lines[-1] if lines else ""


class Orchestrator:
    def __init__(self, settings: Settings, db: Database, http: httpx.AsyncClient | None = None):
        self.settings = settings
        self.db = db
        self.cipher = Cipher(settings.secret_key)
        self.http = http or httpx.AsyncClient(timeout=httpx.Timeout(30.0))
        self.notifier = Notifier(self)
        self.packages = Packages(self)
        self.tasks = TaskManager(self)
        self.timezone = ZoneInfo(settings.tz)
        self._pollers: dict[int, asyncio.Task] = {}
        self._slots: dict[int, asyncio.Semaphore] = {}
        self._background: set[asyncio.Task] = set()
        self._loops: list[asyncio.Task] = []

    # Startup and shutdown

    async def start(self) -> None:
        for bot in await self.db.list_bots():
            if bot["status"] == "running":
                self._start_poller(bot["id"])
        await self.tasks.start()
        # Reinstall the saved packages if the runner lost them; never block startup on it.
        self.spawn(self.packages.sync())
        await self._refresh_schedules()
        self._loops = [
            asyncio.create_task(self._maintenance_loop(), name="maintenance"),
            asyncio.create_task(self._scheduler_loop(), name="scheduler"),
        ]

    async def stop(self) -> None:
        tasks = [*self._pollers.values(), *self._background, *self._loops]
        for task in tasks:
            task.cancel()
        await self.tasks.stop()
        await asyncio.gather(*tasks, return_exceptions=True)
        self._pollers.clear()
        await self.http.aclose()

    def spawn(self, coro) -> asyncio.Task:
        """Run a coroutine in the background and keep a reference until it finishes."""
        task = asyncio.create_task(coro)
        self._background.add(task)
        task.add_done_callback(self._background.discard)
        return task

    # Helpers

    def token_of(self, bot: dict[str, Any]) -> str:
        return self.cipher.decrypt(bot["token_enc"])

    def local_api(self, token: str) -> BotApi:
        return BotApi(self.http, self.settings.bot_api_url, token)

    def cloud_api(self, token: str) -> BotApi:
        return BotApi(self.http, self.settings.cloud_api_url, token)

    async def require_bot(self, bot_id: int) -> dict[str, Any]:
        bot = await self.db.get_bot(bot_id)
        if not bot:
            raise BotError("Bot not found")
        return bot

    def is_polling(self, bot_id: int) -> bool:
        task = self._pollers.get(bot_id)
        return bool(task and not task.done())

    def now_local(self) -> dt.datetime:
        return dt.datetime.now(self.timezone)

    # Lifecycle

    async def log_out_from_cloud(self, token: str) -> list[str]:
        """deleteWebhook + logOut on api.telegram.org. Returns notes for the user."""
        notes = []
        cloud = self.cloud_api(token)
        try:
            await cloud.call("deleteWebhook")
            await cloud.call("logOut")
            notes.append("Logged out from the cloud Bot API.")
        except TelegramError as exc:
            if exc.code == 401:
                notes.append(
                    "The cloud Bot API rejected the token: the bot is probably "
                    "already logged out of it."
                )
            else:
                notes.append(f"Cloud logOut failed: {exc.description}")
        return notes

    async def add_bot(self, token: str, log_out_cloud: bool) -> tuple[dict[str, Any], list[str]]:
        token = token.strip()
        notes = []
        if log_out_cloud:
            notes += await self.log_out_from_cloud(token)
        try:
            me = await self.local_api(token).call("getMe")
        except TelegramError as exc:
            if exc.unreachable:
                raise BotError(f"Local Bot API server is unreachable: {exc.description}") from None
            raise BotError(
                f"The local Bot API server rejected the token: {exc.description}"
            ) from None
        if not me.get("is_bot"):
            raise BotError("This token does not belong to a bot")
        await self.db.add_bot(
            me["id"], me.get("username", ""), me.get("first_name", ""), self.cipher.encrypt(token)
        )
        await self.resume(me["id"])
        return await self.require_bot(me["id"]), notes

    async def pause(self, bot_id: int) -> None:
        await self.require_bot(bot_id)
        await self.db.set_status(bot_id, "paused")
        await self._stop_poller(bot_id)

    async def resume(self, bot_id: int) -> None:
        await self.require_bot(bot_id)
        await self.db.set_status(bot_id, "running")
        self._start_poller(bot_id)

    async def log_out_local(self, bot_id: int) -> None:
        """Release the bot from the local server so it can go back to the cloud API."""
        bot = await self.require_bot(bot_id)
        await self.pause(bot_id)
        try:
            await self.local_api(self.token_of(bot)).call("logOut")
        except TelegramError as exc:
            raise BotError(f"logOut failed: {exc.description}") from None
        await self.db.set_status(
            bot_id,
            "paused",
            "Logged out from the local server. "
            "The cloud Bot API accepts it again in about 10 minutes.",
        )

    async def delete_bot(self, bot_id: int) -> None:
        await self._stop_poller(bot_id)
        for task in await self.db.list_tasks(bot_id, status="running"):
            await self.tasks.cancel(task["id"])
        await self.db.delete_bot(bot_id)

    # Polling

    def _start_poller(self, bot_id: int) -> None:
        if self.is_polling(bot_id):
            return
        self._slot(bot_id)
        self._pollers[bot_id] = asyncio.create_task(self._poll(bot_id), name=f"poll-{bot_id}")

    def _slot(self, bot_id: int) -> asyncio.Semaphore:
        return self._slots.setdefault(bot_id, asyncio.Semaphore(self.settings.bot_concurrency))

    async def _stop_poller(self, bot_id: int) -> None:
        task = self._pollers.pop(bot_id, None)
        if task:
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await task

    async def _poll(self, bot_id: int) -> None:
        backoff = 1.0
        timeout = self.settings.polling_timeout
        while True:
            bot = await self.db.get_bot(bot_id)
            if not bot or bot["status"] != "running":
                return
            api = self.local_api(self.token_of(bot))
            try:
                updates = await api.call(
                    "getUpdates",
                    {"offset": bot["update_offset"], "timeout": timeout},
                    timeout=timeout + 15,
                )
            except TelegramError as exc:
                if exc.code == 401:
                    message = (
                        "Unauthorized: the token was revoked or the bot is logged out of the "
                        "local server."
                    )
                    await self.db.set_status(bot_id, "error", message)
                    await self.notifier.send(
                        "bot_stopped",
                        f"bot:{bot_id}",
                        f"@{bot['username']} stopped polling. {message}"
                        + self.notifier.link(f"/bots/{bot_id}/settings"),
                    )
                    return
                if exc.code == 409:
                    message = (
                        "Conflict: a webhook is set or another client calls getUpdates. "
                        "Use deleteWebhook in the console."
                    )
                elif exc.unreachable:
                    message = f"Local Bot API server is unreachable: {exc.description}"
                else:
                    message = exc.description
                await self.db.set_status_message(bot_id, message)
                if backoff >= 30:
                    await self.notifier.send(
                        "bot_stopped", f"poll:{bot_id}", f"@{bot['username']}: {message}"
                    )
                await asyncio.sleep(backoff)
                backoff = min(backoff * 2, 60)
                continue
            if bot["status_message"]:
                await self.db.set_status_message(bot_id, None)
            backoff = 1.0
            for update in updates:
                await self._receive(bot, update)
            if updates:
                await self.db.set_offset(bot_id, updates[-1]["update_id"] + 1)

    async def match_route(self, bot: dict[str, Any], update: dict[str, Any]):
        routes = await self.db.list_routes(bot["id"])
        return find_route(routes, update, bot["username"])

    async def _receive(self, bot: dict[str, Any], update: dict[str, Any]) -> None:
        info = describe(update)
        if not parse_allowed(bot["allowed_users"]).permits(sender(update)):
            await self.db.add_update(bot["id"], update, status="ignored", **info)
            return
        if bot.get("purge_command") and purge.is_purge(update, bot["username"]):
            row_id = await self.db.add_update(
                bot["id"], update, status="pending", handler_name=PURGE_HANDLER, **info
            )
            self.spawn(self._purge(bot, row_id, update))
            return
        route = await self.match_route(bot, update)
        if route is None:
            await self.db.add_update(bot["id"], update, status="no_route", **info)
            return
        row_id = await self.db.add_update(
            bot["id"],
            update,
            status="pending",
            handler_id=route["handler_id"],
            handler_name=route["handler_name"],
            route_id=route["id"],
            **info,
        )
        self.spawn(self._handle(bot, row_id, update, route["handler_id"]))

    async def _purge(self, bot: dict[str, Any], row_id: int, update: dict[str, Any]) -> None:
        """Run the built-in /purge command; see purge.py."""
        try:
            message = update["message"]
            chat_id = message["chat"]["id"]
            history = await self.db.chat_messages(bot["id"], chat_id)
            chat_plan = purge.plan(chat_id, message["message_id"], history)
            result = await purge.execute(self.local_api(self.token_of(bot)), chat_plan)
        except Exception:
            # Never leave the update pending: record the traceback instead.
            log.exception("/purge failed for bot %s", bot["id"])
            result = {"status": "error", "error": mask(traceback.format_exc())}
        await self.db.finish_update(row_id, **result)

    async def _handle(
        self, bot: dict[str, Any], row_id: int, update: dict[str, Any], handler_id: int
    ) -> dict[str, Any]:
        async with self._slot(bot["id"]):
            active = await self.db.active_code(handler_id)
            if not active:
                result = {"status": "no_route", "error": "the handler has no active version"}
                await self.db.finish_update(row_id, **result)
                return result
            result = await self.call_runner(
                bot,
                code=active["code"],
                version=active["version"],
                update=update,
                handler={"id": handler_id, "name": active["name"], "version": active["version"]},
                origin={"update_row": row_id},
            )
            await self.db.finish_update(row_id, handler_version=active["version"], **result)
        if result["status"] in FAILED:
            await self._notify_failure(
                "handler_errors",
                f"handler:{bot['id']}:{handler_id}",
                f"@{bot['username']} · {active['name']} v{active['version']}",
                result,
                f"/bots/{bot['id']}/updates/{row_id}",
            )
        return result

    async def _notify_failure(
        self, event: str, key: str, title: str, result: dict[str, Any], path: str
    ) -> None:
        if result["status"] == "dispatch_error":
            event, key = "runner_down", "runner"
        text = f"⚠️ {title}: {result['status'].replace('_', ' ')}\n{_last_line(result.get('error'))}"
        await self.notifier.send(event, key, text + self.notifier.link(path))

    # Runner

    async def secrets_of(self, bot_id: int) -> dict[str, str]:
        return {
            name: self.cipher.decrypt(value)
            for name, value in (await self.db.get_secrets_enc(bot_id)).items()
        }

    async def call_runner(
        self,
        bot: dict[str, Any],
        *,
        code: str,
        version: int,
        update: dict[str, Any],
        handler: dict[str, Any] | None = None,
        dry_run: bool = False,
        kind: str = "handler",
        entry: str = "handle",
        args: list | None = None,
        kwargs: dict | None = None,
        task: dict[str, Any] | None = None,
        origin: dict[str, Any] | None = None,
        run_id: str | None = None,
        modules: dict[str, dict[str, Any]] | None = None,
    ) -> dict[str, Any]:
        """Send one job to the runner and return its result."""
        payload = {
            "run_id": run_id or uuid.uuid4().hex,
            "kind": kind,
            "bot": {"id": bot["id"], "username": bot["username"], "token": self.token_of(bot)},
            "code": code,
            "version": version,
            "handler": handler or {},
            "entry": entry,
            "args": args or [],
            "kwargs": kwargs or {},
            "task": task,
            "origin": origin or {},
            "modules": modules if modules is not None else await self.db.active_modules(),
            "update": update,
            "secrets": await self.secrets_of(bot["id"]),
            "dry_run": dry_run,
        }
        timeout = (
            self.settings.task_request_timeout
            if kind == "task"
            else self.settings.runner_request_timeout
        )
        started = time.monotonic()
        try:
            response = await self.http.post(
                self.settings.runner_url.rstrip("/") + "/run",
                json=payload,
                headers={"X-Runner-Secret": self.settings.runner_secret},
                timeout=timeout,
            )
        except httpx.HTTPError as exc:
            return self._dispatch_error(
                f"Runner is unreachable: {type(exc).__name__}: {exc}", started
            )
        if response.status_code != 200:
            return self._dispatch_error(
                f"Runner answered HTTP {response.status_code}: {response.text[:500]}", started
            )
        result = response.json()
        return {
            "status": result.get("status", "error"),
            "error": result.get("error"),
            "logs": result.get("logs", []),
            "api_calls": mask_value(result.get("api_calls", [])),
            "duration_ms": result.get("duration_ms"),
        }

    async def cancel_run(self, run_id: str) -> bool:
        try:
            response = await self.http.post(
                self.settings.runner_url.rstrip("/") + f"/cancel/{run_id}",
                headers={"X-Runner-Secret": self.settings.runner_secret},
                timeout=10,
            )
            return response.status_code == 200 and bool(response.json().get("cancelled"))
        except httpx.HTTPError:
            return False

    @staticmethod
    def _dispatch_error(message: str, started: float) -> dict[str, Any]:
        return {
            "status": "dispatch_error",
            "error": mask(message),
            "logs": [],
            "api_calls": [],
            "duration_ms": round((time.monotonic() - started) * 1000),
        }

    # Schedules

    def next_run_at(self, expression: str) -> float:
        return cron.next_run(expression, self.now_local()).timestamp()

    async def _refresh_schedules(self) -> None:
        """Give every enabled schedule a next run in the future; missed runs are skipped."""
        now = time.time()
        for schedule in await self.db.all_schedules():
            if not schedule["enabled"]:
                continue
            if schedule["next_run_at"] is None or schedule["next_run_at"] < now:
                try:
                    next_at = self.next_run_at(schedule["cron"])
                except cron.CronError:
                    next_at = None
                await self.db.set_schedule_next(schedule["id"], next_at)

    async def run_schedule(self, schedule: dict[str, Any], manual: bool = False) -> int | None:
        """Fire a schedule now. Returns the update log row."""
        bot = await self.require_bot(schedule["bot_id"])
        payload = {
            "id": schedule["id"],
            "name": schedule["name"],
            "cron": schedule["cron"],
            "time": self.now_local().isoformat(timespec="seconds"),
            "manual": manual,
        }
        if schedule["chat_id"]:
            payload["chat"] = {"id": schedule["chat_id"]}
        update = {"update_id": 0, "schedule": payload}
        handler = await self.db.active_code(schedule["handler_id"])
        info = describe(update)
        row_id = await self.db.add_update(
            bot["id"],
            update,
            status="pending",
            handler_id=schedule["handler_id"],
            handler_name=handler["name"] if handler else None,
            **info,
        )

        async def run() -> None:
            result = await self._handle(bot, row_id, update, schedule["handler_id"])
            await self.db.record_schedule_run(schedule["id"], result["status"], row_id)
            if result["status"] in FAILED:
                await self._notify_failure(
                    "schedule_errors",
                    f"schedule:{schedule['id']}",
                    f"@{bot['username']} · schedule {schedule['name']}",
                    result,
                    f"/bots/{bot['id']}/updates/{row_id}",
                )

        self.spawn(run())
        return row_id

    async def _scheduler_loop(self) -> None:
        while True:
            # Wake up at the start of every minute.
            await asyncio.sleep(60 - time.time() % 60 + 0.2)
            try:
                for schedule in await self.db.due_schedules(time.time()):
                    await self.db.set_schedule_next(
                        schedule["id"], self.next_run_at(schedule["cron"])
                    )
                    await self.run_schedule(schedule)
            except Exception:  # noqa: BLE001 - keep the loop alive
                log.exception("scheduler failed")

    # API console

    async def console_call(
        self, bot_id: int, method: str, params: dict[str, Any]
    ) -> dict[str, Any]:
        bot = await self.require_bot(bot_id)
        method = method.strip()
        if not method.isidentifier():
            raise BotError("Method name must be a single word, e.g. getMe")
        if method == "getUpdates" and self.is_polling(bot_id):
            raise BotError(
                "The orchestrator is polling this bot, so a manual getUpdates would "
                "steal its updates. See the Updates tab, or pause the bot first."
            )
        try:
            response = await self.local_api(self.token_of(bot)).raw(method, params, timeout=60)
        except TelegramError as exc:
            response = {"ok": False, "description": exc.description}
        response = mask_value(response)
        await self.db.add_console_call(
            bot_id,
            method,
            json.dumps(params, ensure_ascii=False),
            json.dumps(response, ensure_ascii=False),
            bool(response.get("ok")),
        )
        return response

    # Maintenance

    async def clean_cache(self, older_than_hours: float | None = None) -> maintenance.CacheStats:
        hours = (
            self.settings.cache_retention_hours if older_than_hours is None else older_than_hours
        )
        return await asyncio.to_thread(
            maintenance.clean_cache, self.settings.bot_api_data_dir, hours
        )

    async def _maintenance_loop(self) -> None:
        last_cleanup = 0.0
        while True:
            try:
                if time.monotonic() - last_cleanup > 3600 or last_cleanup == 0:
                    last_cleanup = time.monotonic()
                    cutoff = time.time() - self.settings.update_retention_days * 86400
                    removed = await self.db.prune(cutoff)
                    if removed:
                        log.info("pruned %d old log entries", removed)
                    if self.settings.cache_retention_hours > 0:
                        cleaned = await self.clean_cache()
                        if cleaned.files:
                            log.info("removed %d cached Bot API files", cleaned.files)
                used = await asyncio.to_thread(
                    maintenance.disk_percent_used, self.settings.files_root
                )
                if used is not None and used >= self.settings.disk_warning_percent:
                    await self.notifier.send(
                        "disk_low",
                        "disk",
                        f"💾 The disk with bot files is {used:.0f}% full.",
                    )
                await self.packages.sync()
            except Exception:  # noqa: BLE001 - keep the loop alive
                log.exception("maintenance failed")
            await asyncio.sleep(600)


class TaskManager:
    """Queue of background tasks. State lives in the database; the runner executes them."""

    def __init__(self, orchestrator: Orchestrator):
        self._o = orchestrator
        self._wake = asyncio.Event()
        self._running: dict[int, str] = {}  # task id -> runner run id
        self._cancel_requested: set[int] = set()
        self._loop: asyncio.Task | None = None
        self._jobs: set[asyncio.Task] = set()

    async def start(self) -> None:
        interrupted = await self._o.db.interrupt_running_tasks()
        if interrupted:
            log.warning("%d background tasks were interrupted by a restart", interrupted)
        self._loop = asyncio.create_task(self._dispatch_loop(), name="tasks")

    async def stop(self) -> None:
        jobs = [*self._jobs, *([self._loop] if self._loop else [])]
        for job in jobs:
            job.cancel()
        await asyncio.gather(*jobs, return_exceptions=True)

    def is_running(self, task_id: int) -> bool:
        return task_id in self._running

    async def enqueue(
        self, bot_id: int, data: dict[str, Any], modules: dict[str, int] | None = None
    ) -> int:
        """Store a task started by ctx.background() and wake the dispatcher."""
        await self._o.require_bot(bot_id)
        handler = data.get("handler") or {}
        handler_id = handler.get("id")
        if handler_id is not None and not await self._o.db.get_handler(bot_id, handler_id):
            handler_id = None
        origin = data.get("origin") or {}
        task_id = await self._o.db.create_task(
            bot_id,
            handler_id=handler_id,
            handler_name=handler.get("name"),
            version=int(handler.get("version") or 0),
            code=data["code"],
            entry=data["entry"],
            args=data.get("args", []),
            kwargs=data.get("kwargs", {}),
            title=str(data.get("title") or data["entry"])[:200],
            origin_update=data.get("update") or {},
            update_row=origin.get("update_row"),
            # Module versions are pinned when the task is queued, like the handler code.
            modules=modules
            if modules is not None
            else {name: m["version"] for name, m in (await self._o.db.active_modules()).items()},
        )
        self._wake.set()
        return task_id

    async def retry(self, bot_id: int, task_id: int) -> int:
        task = await self._o.db.get_task(task_id, bot_id)
        if not task:
            raise BotError("Task not found")
        return await self.enqueue(
            bot_id,
            {
                "handler": {
                    "id": task["handler_id"],
                    "name": task["handler_name"],
                    "version": task["version"],
                },
                "code": task["code"],
                "entry": task["entry"],
                "args": task["args"],
                "kwargs": task["kwargs"],
                "title": task["title"],
                "update": task["origin_update"],
                "origin": {"update_row": task["update_row"]},
            },
            modules=task["modules"],
        )

    async def cancel(self, task_id: int) -> bool:
        """Cancel a queued or running task. False if it already finished."""
        if await self._o.db.cancel_queued_task(task_id):
            return True
        run_id = self._running.get(task_id)
        if run_id is None:
            return False
        self._cancel_requested.add(task_id)
        await self._o.cancel_run(run_id)
        return True

    async def _dispatch_loop(self) -> None:
        while True:
            try:
                free = self._o.settings.task_concurrency - len(self._running)
                if free > 0:
                    for row in await self._o.db.queued_tasks(free):
                        if row["id"] in self._running:
                            continue
                        run_id = uuid.uuid4().hex
                        if not await self._o.db.start_task(row["id"], run_id):
                            continue
                        self._running[row["id"]] = run_id
                        job = asyncio.create_task(self._run(row["id"], run_id))
                        self._jobs.add(job)
                        job.add_done_callback(self._jobs.discard)
            except Exception:  # noqa: BLE001 - keep the loop alive
                log.exception("task dispatch failed")
            self._wake.clear()
            with contextlib.suppress(TimeoutError):
                await asyncio.wait_for(self._wake.wait(), 5)

    async def _run(self, task_id: int, run_id: str) -> None:
        db = self._o.db
        try:
            task = await db.get_task(task_id)
            bot = await db.get_bot(task["bot_id"]) if task else None
            if not task or not bot:
                return
            if task_id in self._cancel_requested:
                result = {"status": "cancelled", "error": "cancelled"}
            else:
                result = await self._o.call_runner(
                    bot,
                    kind="task",
                    code=task["code"],
                    version=task["version"],
                    update=task["origin_update"],
                    handler={
                        "id": task["handler_id"],
                        "name": task["handler_name"],
                        "version": task["version"],
                    },
                    entry=task["entry"],
                    args=task["args"],
                    kwargs=task["kwargs"],
                    task={"id": task_id, "title": task["title"]},
                    origin={"update_row": task["update_row"], "task_id": task_id},
                    run_id=run_id,
                    modules=await db.modules_at(task["modules"])
                    if task["modules"]
                    else await db.active_modules(),
                )
                if task_id in self._cancel_requested:
                    result = {**result, "status": "cancelled", "error": "cancelled"}
            await db.finish_task(task_id, **result)
            if result["status"] in FAILED:
                await self._o._notify_failure(
                    "task_errors",
                    f"task:{bot['id']}:{task['entry']}",
                    f"@{bot['username']} · task “{task['title']}”",
                    result,
                    f"/bots/{bot['id']}/tasks/{task_id}",
                )
        except Exception:  # noqa: BLE001 - record the failure instead of losing the task
            log.exception("background task %s failed", task_id)
            with contextlib.suppress(Exception):
                await db.finish_task(task_id, status="error", error="orchestrator error")
        finally:
            self._running.pop(task_id, None)
            self._cancel_requested.discard(task_id)
            self._wake.set()
