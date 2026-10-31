"""Bot lifecycle, polling and dispatch of updates to the runner."""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import time
import uuid
from typing import Any

import httpx

from .config import Settings
from .db import Database
from .security import Cipher, mask, mask_value
from .telegram import BotApi, TelegramError
from .updates import describe, parse_allowed

log = logging.getLogger("bot_orchestrator")

FAILED = ("error", "timeout", "dispatch_error")


class BotError(Exception):
    """A user-facing error from a bot operation."""


class Orchestrator:
    def __init__(self, settings: Settings, db: Database, http: httpx.AsyncClient | None = None):
        self.settings = settings
        self.db = db
        self.cipher = Cipher(settings.secret_key)
        self.http = http or httpx.AsyncClient(timeout=httpx.Timeout(30.0))
        self._pollers: dict[int, asyncio.Task] = {}
        self._slots: dict[int, asyncio.Semaphore] = {}
        self._handler_tasks: set[asyncio.Task] = set()
        self._maintenance: asyncio.Task | None = None

    # Startup and shutdown

    async def start(self) -> None:
        for bot in await self.db.list_bots():
            if bot["status"] == "running":
                self._start_poller(bot["id"])
        self._maintenance = asyncio.create_task(self._maintenance_loop())

    async def stop(self) -> None:
        tasks = [*self._pollers.values(), *self._handler_tasks]
        if self._maintenance:
            tasks.append(self._maintenance)
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        self._pollers.clear()
        await self.http.aclose()

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
        await self.db.delete_bot(bot_id)

    # Polling

    def _start_poller(self, bot_id: int) -> None:
        if self.is_polling(bot_id):
            return
        self._slots.setdefault(bot_id, asyncio.Semaphore(self.settings.bot_concurrency))
        self._pollers[bot_id] = asyncio.create_task(self._poll(bot_id), name=f"poll-{bot_id}")

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
                    await self.db.set_status(
                        bot_id,
                        "error",
                        "Unauthorized: the token was revoked or the bot is logged out of the "
                        "local server.",
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

    async def _receive(self, bot: dict[str, Any], update: dict[str, Any]) -> None:
        info = describe(update)
        allowed = parse_allowed(bot["allowed_users"])
        status = "pending"
        if allowed and info["from_id"] not in allowed:
            status = "ignored"
        elif bot["active_version"] is None:
            status = "no_handler"
        row_id = await self.db.add_update(bot["id"], update, status=status, **info)
        if status != "pending":
            return
        task = asyncio.create_task(self._handle(bot, row_id, update))
        self._handler_tasks.add(task)
        task.add_done_callback(self._handler_tasks.discard)

    async def _handle(self, bot: dict[str, Any], row_id: int, update: dict[str, Any]) -> None:
        async with self._slots[bot["id"]]:
            version = await self.db.get_version(bot["id"], bot["active_version"])
            if not version:
                await self.db.finish_update(row_id, status="no_handler")
                return
            result = await self.run_handler(bot, version["code"], version["version"], update)
            await self.db.finish_update(row_id, handler_version=version["version"], **result)

    # Handlers

    async def run_handler(
        self,
        bot: dict[str, Any],
        code: str,
        version: int,
        update: dict[str, Any],
        dry_run: bool = False,
    ) -> dict[str, Any]:
        """Send one update to the runner and return its result."""
        secrets = {
            name: self.cipher.decrypt(value)
            for name, value in (await self.db.get_secrets_enc(bot["id"])).items()
        }
        payload = {
            "run_id": uuid.uuid4().hex,
            "bot": {"id": bot["id"], "username": bot["username"], "token": self.token_of(bot)},
            "code": code,
            "version": version,
            "update": update,
            "secrets": secrets,
            "dry_run": dry_run,
        }
        started = time.monotonic()
        try:
            response = await self.http.post(
                self.settings.runner_url.rstrip("/") + "/run",
                json=payload,
                headers={"X-Runner-Secret": self.settings.runner_secret},
                timeout=self.settings.runner_request_timeout,
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

    @staticmethod
    def _dispatch_error(message: str, started: float) -> dict[str, Any]:
        return {
            "status": "dispatch_error",
            "error": mask(message),
            "logs": [],
            "api_calls": [],
            "duration_ms": round((time.monotonic() - started) * 1000),
        }

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

    async def _maintenance_loop(self) -> None:
        while True:
            try:
                cutoff = time.time() - self.settings.update_retention_days * 86400
                removed = await self.db.prune_updates(cutoff)
                if removed:
                    log.info("pruned %d old updates", removed)
            except Exception:  # noqa: BLE001 - keep the loop alive
                log.exception("maintenance failed")
            await asyncio.sleep(3600)
