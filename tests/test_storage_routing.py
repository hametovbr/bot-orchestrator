"""Schema migration, route matching and cron."""

import datetime as dt
import sqlite3
from zoneinfo import ZoneInfo

import pytest

from bot_orchestrator import cron, routing
from bot_orchestrator.db import SCHEMA_VERSION, Database

# The schema the MVP (0.1.0) created.
MVP_SCHEMA = """
CREATE TABLE bots (
    id INTEGER PRIMARY KEY, username TEXT NOT NULL, first_name TEXT NOT NULL DEFAULT '',
    token_enc TEXT NOT NULL, status TEXT NOT NULL DEFAULT 'paused', status_message TEXT,
    update_offset INTEGER NOT NULL DEFAULT 0, allowed_users TEXT NOT NULL DEFAULT '',
    active_version INTEGER, created_at REAL NOT NULL
);
CREATE TABLE handler_versions (
    bot_id INTEGER NOT NULL REFERENCES bots(id) ON DELETE CASCADE, version INTEGER NOT NULL,
    code TEXT NOT NULL, comment TEXT NOT NULL DEFAULT '', created_at REAL NOT NULL,
    PRIMARY KEY (bot_id, version)
);
CREATE TABLE updates (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    bot_id INTEGER NOT NULL REFERENCES bots(id) ON DELETE CASCADE,
    update_id INTEGER NOT NULL, received_at REAL NOT NULL, kind TEXT NOT NULL, from_id INTEGER,
    from_name TEXT, summary TEXT NOT NULL DEFAULT '', raw TEXT NOT NULL, status TEXT NOT NULL,
    handler_version INTEGER, duration_ms INTEGER, error TEXT,
    logs TEXT NOT NULL DEFAULT '[]', api_calls TEXT NOT NULL DEFAULT '[]'
);
CREATE TABLE console_history (id INTEGER PRIMARY KEY AUTOINCREMENT, bot_id INTEGER NOT NULL,
    method TEXT NOT NULL, params TEXT NOT NULL, response TEXT NOT NULL, ok INTEGER NOT NULL,
    created_at REAL NOT NULL);
CREATE TABLE secrets (bot_id INTEGER NOT NULL, name TEXT NOT NULL, value_enc TEXT NOT NULL,
    PRIMARY KEY (bot_id, name));
CREATE TABLE kv (bot_id INTEGER NOT NULL, key TEXT NOT NULL, value TEXT NOT NULL,
    updated_at REAL NOT NULL, PRIMARY KEY (bot_id, key));

INSERT INTO bots (id, username, token_enc, status, active_version, created_at)
    VALUES (1, 'active_bot', 'x', 'running', 1, 0), (2, 'disabled_bot', 'x', 'running', NULL, 0),
           (3, 'empty_bot', 'x', 'paused', NULL, 0);
INSERT INTO handler_versions VALUES (1, 1, 'code v1', 'first', 10), (1, 2, 'code v2', '', 20),
    (2, 1, 'old code', '', 30);
INSERT INTO updates (bot_id, update_id, received_at, kind, raw, status, handler_version)
    VALUES (1, 5, 0, 'message', '{}', 'ok', 1), (2, 6, 0, 'message', '{}', 'no_handler', NULL);
"""


async def test_migrates_mvp_database(tmp_path):
    path = tmp_path / "old.db"
    with sqlite3.connect(path) as conn:
        conn.executescript(MVP_SCHEMA)
    db = Database(str(path))
    await db.connect()
    try:
        assert await db._one("PRAGMA user_version") == {"user_version": SCHEMA_VERSION}
        assert await db.list_modules() == []
        columns = {r["name"] for r in await db._all("PRAGMA table_info(tasks)")}
        assert "modules" in columns

        active = await db.get_handler_by_name(1, "main")
        assert active["active_version"] == 1, "the MVP active version stays active"
        assert [v["version"] for v in await db.list_versions(active["id"])] == [2, 1]
        assert (await db.active_code(active["id"]))["code"] == "code v1"
        routes = await db.list_routes(1)
        assert [(r["kind"], r["enabled"], r["handler_name"]) for r in routes] == [
            ("any", 1, "main")
        ]

        disabled = await db.get_handler_by_name(2, "main")
        assert disabled["active_version"] == 1
        assert [r["enabled"] for r in await db.list_routes(2)] == [0]

        assert await db.list_handlers(3) == []
        logged = await db.list_updates(1)
        assert logged[0]["handler_name"] == "main"
        assert (await db.list_updates(2))[0]["status"] == "no_route"

        # Connecting again is a no-op.
        await db.close()
        db = Database(str(path))
        await db.connect()
        assert len(await db.list_routes(1)) == 1
    finally:
        await db.close()


async def test_fresh_database_and_cascades():
    db = Database(":memory:")
    await db.connect()
    await db.add_bot(1, "b", "B", "x")
    handler = await db.create_handler(1, "main", "code")
    route = await db.create_route(1, "any", "", "", handler)
    second = await db.create_route(1, "command", "start", "", handler)
    await db.reorder_routes(1, [second, route])
    assert [r["id"] for r in await db.list_routes(1)] == [second, route]
    task = await db.create_task(1, code="c", entry="f", title="t", origin_update={"update_id": 1})
    assert await db.start_task(task, "run") is True
    assert await db.start_task(task, "run") is False
    assert await db.interrupt_running_tasks() == 1
    await db.delete_handler(handler)
    assert await db.list_routes(1) == []
    await db.delete_bot(1)
    assert await db.get_task(task) is None
    await db.close()


def message(text=None, **extra):
    body = {"message_id": 1, "chat": {"id": 1}, "from": {"id": 1}, **extra}
    if text is not None:
        body["text"] = text
    return {"update_id": 1, "message": body}


def route(kind, pattern="", enabled=1, rid=1):
    return {"id": rid, "kind": kind, "pattern": pattern, "enabled": enabled}


@pytest.mark.parametrize(
    ("kind", "pattern", "update", "expected"),
    [
        ("command", "start", message("/start"), True),
        ("command", "start", message("/start payload"), True),
        ("command", "start", message("/start@my_bot"), True),
        ("command", "start", message("/start@other_bot"), False),
        ("command", "start", message("/started"), False),
        ("command", "start", message("hi /start"), False),
        ("regex", r"https?://\S+", message("see https://x.test/v"), True),
        ("regex", r"https?://\S+", message(caption="https://x.test", photo=[{}]), True),
        ("regex", r"https?://\S+", message("no link"), False),
        ("content", "document, video", message(document={}), True),
        ("content", "document", message("text"), False),
        ("content", "text", message("text"), True),
        ("callback", "cancel:", {"update_id": 1, "callback_query": {"data": "cancel:5"}}, True),
        ("callback", "cancel:", {"update_id": 1, "callback_query": {"data": "ok"}}, False),
        ("callback", "", {"update_id": 1, "callback_query": {"data": "ok"}}, True),
        ("update", "my_chat_member", {"update_id": 1, "my_chat_member": {}}, True),
        ("update", "schedule", {"update_id": 0, "schedule": {"id": 1}}, True),
        ("update", "edited_message", message("x"), False),
        ("any", "", {"update_id": 1, "inline_query": {}}, True),
    ],
)
def test_route_matching(kind, pattern, update, expected):
    assert routing.matches(route(kind, pattern), update, "my_bot") is expected


def test_find_route_uses_order_and_skips_disabled():
    routes = [
        route("command", "start", enabled=0, rid=1),
        route("regex", "start", rid=2),
        route("any", rid=3),
    ]
    assert routing.find_route(routes, message("/start"))["id"] == 2
    assert routing.find_route(routes, message("hello"))["id"] == 3
    assert routing.find_route(routes[:2], message("hello")) is None


@pytest.mark.parametrize(
    ("kind", "pattern", "result"),
    [
        ("command", "/Start", "start"),
        ("content", "Document;video", "document, video"),
        ("callback", " cancel: ", "cancel:"),
        ("any", "ignored", ""),
    ],
)
def test_route_validation(kind, pattern, result):
    assert routing.validate(kind, pattern) == result


@pytest.mark.parametrize(
    ("kind", "pattern"),
    [("command", "two words"), ("regex", "("), ("content", "pdf"), ("update", ""), ("x", "")],
)
def test_route_validation_errors(kind, pattern):
    with pytest.raises(ValueError):
        routing.validate(kind, pattern)


MSK = ZoneInfo("Europe/Moscow")


@pytest.mark.parametrize(
    ("expression", "after", "expected"),
    [
        ("*/15 * * * *", "2026-11-01 10:07", "2026-11-01 10:15"),
        ("0 9 * * mon-fri", "2026-10-31 12:00", "2026-11-02 09:00"),  # Saturday -> Monday
        ("30 8 1 * *", "2026-11-01 09:00", "2026-12-01 08:30"),
        ("@daily", "2026-12-31 23:59", "2027-01-01 00:00"),
        ("0 0 13 * fri", "2026-11-01 00:00", "2026-11-06 00:00"),  # dom OR dow
        ("0 12 * * 7", "2026-11-01 12:00", "2026-11-08 12:00"),  # 7 is Sunday
        ("0 0 29 2 *", "2026-03-01 00:00", "2028-02-29 00:00"),
        ("5 8-18/5 * jan *", "2026-11-01 00:00", "2027-01-01 08:05"),
    ],
)
def test_cron_next_run(expression, after, expected):
    start = dt.datetime.fromisoformat(after).replace(tzinfo=MSK)
    assert cron.next_run(expression, start) == dt.datetime.fromisoformat(expected).replace(
        tzinfo=MSK
    )


@pytest.mark.parametrize(
    "expression", ["* * * *", "60 * * * *", "0 0 30 2 *", "*/0 * * * *", "0 0 * * funday"]
)
def test_cron_errors(expression):
    with pytest.raises(cron.CronError):
        cron.parse(expression)
