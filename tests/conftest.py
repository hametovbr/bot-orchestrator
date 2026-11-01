"""Fixtures: an orchestrator wired to a fake Bot API (local + cloud) and a fake runner."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

import httpx
import pytest
from fastapi.testclient import TestClient

from bot_orchestrator.config import Settings
from bot_orchestrator.db import Database
from bot_orchestrator.service import Orchestrator
from bot_orchestrator.web import create_app

TOKEN = "777000111:" + "B" * 35
BOT_ID = 777000111
PASSWORD = "correct horse"
RUNNER_SECRET = "runner-secret"


def make_update(update_id: int, text: str = "hello", user_id: int = 42) -> dict:
    return {
        "update_id": update_id,
        "message": {
            "message_id": update_id,
            "date": 1761930000,
            "chat": {"id": user_id, "type": "private"},
            "from": {"id": user_id, "is_bot": False, "first_name": "Ann", "username": "ann"},
            "text": text,
        },
    }


class Fake:
    """Answers every HTTP request the orchestrator makes."""

    def __init__(self):
        self.local_calls: list[tuple[str, dict]] = []
        self.cloud_calls: list[tuple[str, dict]] = []
        self.runs: list[dict] = []
        self.pending: list[dict] = []
        self.runner_result = {
            "status": "ok",
            "error": None,
            "logs": ["handled"],
            "api_calls": [],
            "duration_ms": 12,
        }
        # Background tasks wait for this event before answering, so tests can cancel them.
        self.task_gate: asyncio.Event | None = None
        self.cancelled: list[str] = []

    async def __call__(self, request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content or b"{}")
        if request.url.host == "runner":
            assert request.headers["x-runner-secret"] == RUNNER_SECRET
            if request.url.path.startswith("/cancel/"):
                self.cancelled.append(request.url.path.removeprefix("/cancel/"))
                if self.task_gate:
                    self.task_gate.set()
                return httpx.Response(200, json={"cancelled": True})
            self.runs.append(body)
            if body.get("kind") == "task" and self.task_gate:
                await self.task_gate.wait()
            return httpx.Response(200, json=self.runner_result)

        token, method = request.url.path.removeprefix("/bot").split("/", 1)
        calls = self.cloud_calls if request.url.host == "cloud" else self.local_calls
        calls.append((method, body))
        if token != TOKEN:
            return httpx.Response(
                401, json={"ok": False, "error_code": 401, "description": "Unauthorized"}
            )
        if method == "getMe":
            return httpx.Response(
                200,
                json={
                    "ok": True,
                    "result": {
                        "id": BOT_ID,
                        "is_bot": True,
                        "first_name": "Test",
                        "username": "test_bot",
                    },
                },
            )
        if method == "getUpdates":
            offset = body.get("offset", 0)
            ready = [u for u in self.pending if u["update_id"] >= offset]
            if not ready:
                await asyncio.sleep(0.02)
            return httpx.Response(200, json={"ok": True, "result": ready})
        return httpx.Response(200, json={"ok": True, "result": True})


@pytest.fixture
def settings(tmp_path: Path) -> Settings:
    return Settings(
        admin_password=PASSWORD,
        secret_key="x" * 40,
        runner_secret=RUNNER_SECRET,
        bot_api_url="http://local-api",
        cloud_api_url="http://cloud",
        runner_url="http://runner",
        database_path=":memory:",
        files_root=str(tmp_path / "files"),
        bot_api_data_dir=str(tmp_path / "bot-api"),
        polling_timeout=0,
        tz="Europe/Moscow",
        public_url="https://bots.test",
    )


@pytest.fixture
def fake() -> Fake:
    return Fake()


@pytest.fixture
def client(settings: Settings, fake: Fake):
    db = Database(":memory:")
    http = httpx.AsyncClient(transport=httpx.MockTransport(fake))

    orchestrator = Orchestrator(settings, db, http=http)
    original_start = orchestrator.start

    async def start():
        await db.connect()
        await original_start()

    orchestrator.start = start
    app = create_app(settings, orchestrator=orchestrator)
    with TestClient(app, base_url="http://testserver") as c:
        c.orchestrator = orchestrator
        yield c


@pytest.fixture
def logged_in(client: TestClient) -> TestClient:
    response = client.post("/login", data={"password": PASSWORD}, follow_redirects=False)
    assert response.headers["location"] == "/"
    return client
