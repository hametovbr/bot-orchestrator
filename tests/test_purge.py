import json

import httpx

from bot_orchestrator import purge
from bot_orchestrator.telegram import BotApi

NOW = 1_800_000_000.0
HOUR = 3600


def msg(message_id: int, hours_ago: float, text: str = "hi") -> dict:
    return {"message_id": message_id, "date": NOW - hours_ago * HOUR, "text": text}


def test_command_name():
    assert purge.command_name("/purge") == "purge"
    assert purge.command_name("/Purge@my_bot now", "my_bot") == "purge"
    assert purge.command_name("/purge@other_bot", "my_bot") is None
    assert purge.command_name("purge") is None
    assert purge.command_name(None) is None


def test_is_purge_only_in_private_chats():
    private = {"message": {"chat": {"type": "private"}, "text": "/purge"}}
    group = {"message": {"chat": {"type": "group"}, "text": "/purge"}}
    assert purge.is_purge(private, "bot")
    assert not purge.is_purge(group, "bot")
    assert not purge.is_purge({"callback_query": {}}, "bot")


def test_plan_starts_after_the_newest_old_message_and_keeps_start():
    history = [
        msg(1, 100, "/start"),
        msg(5, 60),
        msg(9, 10, "/start"),
        msg(12, 1),
    ]
    p = purge.plan(42, 15, history, now=NOW)
    assert (p.first, p.last) == (6, 15)
    assert p.keep == [9]
    assert p.ids == [6, 7, 8, 10, 11, 12, 13, 14, 15]


def test_plan_without_old_messages_is_capped():
    p = purge.plan(42, 5000, [msg(4990, 1)], now=NOW)
    assert p.first == 5000 - purge.MAX_SPAN + 1
    assert len(p.ids) == purge.MAX_SPAN
    assert purge.plan(42, 7, [], now=NOW).ids == list(range(1, 8))


async def test_execute_batches_and_falls_back_one_by_one():
    calls: list[tuple[str, dict]] = []

    def respond(request: httpx.Request) -> httpx.Response:
        method = request.url.path.rsplit("/", 1)[1]
        params = json.loads(request.content)
        calls.append((method, params))
        if method == "deleteMessages" and 1 in params["message_ids"]:
            return httpx.Response(
                400,
                json={"ok": False, "error_code": 400, "description": "message can't be deleted"},
            )
        if method == "deleteMessage" and params["message_id"] == 1:
            return httpx.Response(
                400,
                json={"ok": False, "error_code": 400, "description": "message can't be deleted"},
            )
        return httpx.Response(200, json={"ok": True, "result": True})

    async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as http:
        api = BotApi(http, "http://api", "123:abc")
        result = await purge.execute(api, purge.Plan(chat_id=42, first=1, last=150, keep=[3]))
    batches = [p["message_ids"] for m, p in calls if m == "deleteMessages"]
    assert [len(b) for b in batches] == [100, 49]
    assert 3 not in batches[0]
    singles = [p["message_id"] for m, p in calls if m == "deleteMessage"]
    assert singles == batches[0]
    assert result["status"] == "ok"
    assert "1 message(s) could not be deleted" in result["logs"][1]
    assert [c["ok"] for c in result["api_calls"]] == [False, True]
