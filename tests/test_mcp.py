"""The MCP endpoint: authentication, tool list and the main agent workflows."""

import itertools
import time

import httpx
import pytest
from fastapi.testclient import TestClient

from bot_orchestrator.db import Database
from bot_orchestrator.service import Orchestrator
from bot_orchestrator.web import create_app

from .conftest import BOT_ID, TOKEN, make_update

MCP_TOKEN = "mcp-test-token"
HEADERS = {
    "Authorization": f"Bearer {MCP_TOKEN}",
    "Accept": "application/json, text/event-stream",
}
_ids = itertools.count(1)


def make_client(settings, fake):
    db = Database(":memory:")
    orchestrator = Orchestrator(
        settings, db, http=httpx.AsyncClient(transport=httpx.MockTransport(fake))
    )
    original_start = orchestrator.start

    async def start():
        await db.connect()
        await original_start()

    orchestrator.start = start
    return TestClient(create_app(settings, orchestrator=orchestrator)), orchestrator


@pytest.fixture
def mcp(settings, fake):
    settings.mcp_token = MCP_TOKEN
    client, orchestrator = make_client(settings, fake)
    with client as c:
        c.orchestrator = orchestrator
        yield c


def rpc(client, method, params=None):
    response = client.post(
        "/mcp",
        headers=HEADERS,
        json={"jsonrpc": "2.0", "id": next(_ids), "method": method, "params": params or {}},
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert "error" not in body, body
    return body["result"]


def call(client, tool, **arguments):
    """Call a tool; returns its structured result, or raises ToolFailed with the message."""
    result = rpc(client, "tools/call", {"name": tool, "arguments": arguments})
    if result["isError"]:
        raise ToolFailed(result["content"][0]["text"])
    return result["structuredContent"]


class ToolFailed(Exception):
    pass


def tool_names(client):
    return {t["name"] for t in rpc(client, "tools/list")["tools"]}


def wait_for(predicate, timeout=5.0):
    deadline = time.time() + timeout
    while time.time() < deadline:
        value = predicate()
        if value:
            return value
        time.sleep(0.05)
    raise AssertionError("condition not met")


# Endpoint


def test_endpoint_is_off_without_a_token(client):
    assert client.post("/mcp", json={}).status_code == 404


def test_endpoint_requires_the_bearer_token(mcp):
    assert mcp.post("/mcp", json={}).status_code == 401
    bad = mcp.post("/mcp", json={}, headers={"Authorization": "Bearer wrong"})
    assert bad.status_code == 401
    assert bad.headers["www-authenticate"].startswith("Bearer")
    # The web session does not grant access, and the MCP token does not open the UI.
    assert mcp.get("/", headers=HEADERS, follow_redirects=False).status_code == 303


def test_initialize_and_tool_list(mcp):
    info = rpc(
        mcp,
        "initialize",
        {
            "protocolVersion": "2025-06-18",
            "capabilities": {},
            "clientInfo": {"name": "pytest", "version": "1"},
        },
    )
    assert info["serverInfo"]["name"] == "bot-orchestrator"
    assert "read_docs" in info["instructions"]
    tools = {t["name"]: t for t in rpc(mcp, "tools/list")["tools"]}
    for name in (
        "get_overview",
        "create_handler",
        "save_handler",
        "test_handler",
        "create_route",
        "publish_bot_commands",
        "create_schedule",
        "save_module",
        "call_bot_api",
        "read_docs",
    ):
        assert name in tools
    assert tools["list_updates"]["annotations"]["readOnlyHint"] is True
    assert tools["delete_bot"]["annotations"]["destructiveHint"] is True


def test_read_only_mode_offers_no_write_tools(settings, fake):
    settings.mcp_token = MCP_TOKEN
    settings.mcp_read_only = True
    client, _ = make_client(settings, fake)
    with client as c:
        names = tool_names(c)
    assert {"get_overview", "list_updates", "get_handler", "read_docs"} <= names
    assert not names & {"add_bot", "save_handler", "delete_bot", "call_bot_api", "set_secret"}


# Workflows


def test_agent_builds_a_command_bot(mcp, fake):
    added = call(mcp, "add_bot", token=TOKEN, log_out_cloud=False)
    assert added["bot"]["username"] == "test_bot"
    assert "token_enc" not in added["bot"] and TOKEN not in str(added)

    code = "async def handle(update, ctx):\n    await ctx.reply('pong')\n"
    handler = call(mcp, "create_handler", bot="@test_bot", name="ping", code=code)
    assert handler["version"] == 1 and handler["warnings"] == []

    route = call(
        mcp,
        "create_route",
        bot=BOT_ID,
        kind="command",
        pattern="/ping",
        handler="ping",
        description="Check the bot",
    )
    assert route["pattern"] == "ping"
    call(mcp, "create_route", bot=BOT_ID, kind="any", handler=handler["id"], position=1)
    routes = call(mcp, "list_routes", bot=BOT_ID)["routes"]
    assert [r["kind"] for r in routes] == ["any", "command"]
    call(mcp, "reorder_routes", bot=BOT_ID, route_ids=[route["id"]])
    assert call(mcp, "list_routes", bot=BOT_ID)["routes"][0]["id"] == route["id"]

    commands = call(mcp, "publish_bot_commands", bot=BOT_ID)["commands"]
    assert commands == [{"command": "ping", "description": "Check the bot"}]
    assert ("setMyCommands", {"commands": commands}) in fake.local_calls

    overview = call(mcp, "get_overview")
    assert overview["bots"][0]["routes_enabled"] == 2

    bot = call(mcp, "get_bot", bot="test_bot")
    assert [h["name"] for h in bot["handlers"]] == ["ping"]
    assert bot["bot"]["created_at"][:2] == "20"  # ISO time, not a Unix number


def test_agent_tests_then_saves_code(mcp, fake):
    call(mcp, "add_bot", token=TOKEN, log_out_cloud=False)
    call(mcp, "create_handler", bot=BOT_ID, name="main")

    result = call(
        mcp,
        "test_handler",
        bot=BOT_ID,
        handler="main",
        code="async def handle(update, ctx):\n    pass\n",
        update=make_update(1, "hi"),
    )
    assert result["status"] == "ok"
    assert fake.runs[-1]["dry_run"] is True and fake.runs[-1]["handler"]["version"] == 0

    with pytest.raises(ToolFailed, match="syntax error on line 1"):
        call(mcp, "save_handler", bot=BOT_ID, handler="main", code="def handle(:")

    saved = call(
        mcp,
        "save_handler",
        bot=BOT_ID,
        handler="main",
        code="from shared import nope\n\nasync def handle(update, ctx):\n    pass\n",
        comment="v2",
    )
    assert saved["version"] == 2
    assert "nope" in saved["warnings"][0]
    call(mcp, "activate_handler_version", bot=BOT_ID, handler="main", version=1)
    assert call(mcp, "get_handler", bot=BOT_ID, handler="main")["active_version"] == 1


def test_agent_reads_the_update_log(mcp, fake):
    call(mcp, "add_bot", token=TOKEN, log_out_cloud=False)
    call(mcp, "create_handler", bot=BOT_ID, name="main", add_template_route=True)
    fake.pending.append(make_update(5, "hello there"))

    rows = wait_for(
        lambda: [
            u
            for u in call(mcp, "list_updates", bot=BOT_ID, limit=5)["updates"]
            if u["status"] == "ok"
        ]
    )
    assert rows[0]["summary"] == "hello there"
    entry = call(mcp, "get_update", bot=BOT_ID, row_id=rows[0]["id"])
    assert entry["raw"]["message"]["text"] == "hello there"
    assert entry["route"]["kind"] == "any"
    match = call(mcp, "match_route", bot=BOT_ID, row_id=rows[0]["id"])
    assert match["route"]["handler_name"] == "main"


def test_errors_reach_the_agent_as_messages(mcp):
    with pytest.raises(ToolFailed, match="Bot not found"):
        call(mcp, "get_bot", bot="@nobody")
    call(mcp, "add_bot", token=TOKEN, log_out_cloud=False)
    with pytest.raises(ToolFailed, match="Not a Telegram user id"):
        call(mcp, "update_bot_settings", bot=BOT_ID, allowed_users=["not valid!"])
    with pytest.raises(ToolFailed, match="Choose a handler"):
        call(mcp, "create_route", bot=BOT_ID, kind="any", handler="missing")
    with pytest.raises(ToolFailed, match="Cron expression"):
        call(mcp, "create_schedule", bot=BOT_ID, name="x", cron="nope", handler="missing")
    with pytest.raises(ToolFailed, match="steal its updates"):
        call(mcp, "call_bot_api", bot=BOT_ID, method="getUpdates")


def test_settings_secrets_and_values(mcp):
    call(mcp, "add_bot", token=TOKEN, log_out_cloud=False)
    bot = call(mcp, "update_bot_settings", bot=BOT_ID, allowed_users=["@Ann", "42"])
    assert bot["bot"]["allowed_users"] == "42,@ann"
    call(mcp, "set_secret", bot=BOT_ID, name="API_KEY", value="s3cret-value")
    call(mcp, "set_stored_value", bot=BOT_ID, key="config", value={"lang": "en"})
    info = call(mcp, "get_bot", bot=BOT_ID)
    assert info["secret_names"] == ["API_KEY"] and info["stored_keys"] == ["config"]
    assert "s3cret-value" not in str(info)
    values = call(mcp, "list_stored_values", bot=BOT_ID)["values"]
    assert values[0]["value"] == {"lang": "en"}
    call(mcp, "set_bot_polling", bot=BOT_ID, running=False)
    assert call(mcp, "get_bot", bot=BOT_ID)["bot"]["status"] == "paused"


def test_schedules_and_modules(mcp, fake):
    call(mcp, "add_bot", token=TOKEN, log_out_cloud=False)
    call(mcp, "create_handler", bot=BOT_ID, name="daily")
    assert len(call(mcp, "preview_cron", expression="0 9 * * 1-5", count=3)["runs"]) == 3
    schedule = call(
        mcp, "create_schedule", bot=BOT_ID, name="Morning", cron="0 9 * * *", handler="daily"
    )
    changed = call(mcp, "update_schedule", bot=BOT_ID, schedule_id=schedule["id"], chat_id=42)
    assert changed["chat_id"] == 42 and changed["cron"] == "0 9 * * *"
    run = call(mcp, "run_schedule", bot=BOT_ID, schedule_id=schedule["id"])
    wait_for(lambda: call(mcp, "get_update", bot=BOT_ID, row_id=run["row_id"])["status"] == "ok")

    call(mcp, "create_module", name="helpers", code="def double(x):\n    return x * 2\n")
    call(
        mcp,
        "save_handler",
        bot=BOT_ID,
        handler="daily",
        code="from shared import helpers\n\nasync def handle(update, ctx):\n    pass\n",
    )
    with pytest.raises(ToolFailed, match="Still imported by"):
        call(mcp, "delete_module", module="helpers")
    module = call(mcp, "get_module", module="helpers")
    assert module["outline"][0]["name"] == "double"
    assert module["users"][0]["kind"] == "handler"
    tested = call(
        mcp,
        "test_module",
        module="helpers",
        code="def double(x):\n    return x + x\n",
        bot=BOT_ID,
        handler="daily",
        update={"update_id": 1},
    )
    assert tested["status"] == "ok"
    assert fake.runs[-1]["modules"]["helpers"]["version"] == 0


def test_docs_are_served(mcp):
    skill = call(mcp, "read_docs", topic="skill")["text"]
    assert "handle(update, ctx)" in skill
    resources = rpc(mcp, "resources/list")["resources"]
    assert {r["uri"] for r in resources} >= {"docs://handlers/sdk", "docs://handlers/skill"}
    read = rpc(mcp, "resources/read", {"uri": "docs://handlers/sdk"})
    assert "ctx.reply" in read["contents"][0]["text"]
