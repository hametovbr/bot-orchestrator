import json

import httpx

from bot_orchestrator import purge
from bot_orchestrator.telegram import BotApi

NOW = 1_800_000_000.0
HOUR = 3600
REFUSED = {"ok": False, "error_code": 400, "description": "message can't be deleted"}


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


class FakeApi:
    """deleteMessages refuses any batch holding an id below `oldest`."""

    def __init__(self, oldest: int, flood_once: bool = False):
        self.oldest = oldest
        self.flood_once = flood_once
        self.calls: list[list[int]] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        ids = json.loads(request.content)["message_ids"]
        self.calls.append(ids)
        if self.flood_once:
            self.flood_once = False
            return httpx.Response(
                429,
                json={
                    "ok": False,
                    "error_code": 429,
                    "description": "Too Many Requests",
                    "parameters": {"retry_after": 0},
                },
            )
        if min(ids) < self.oldest:
            return httpx.Response(400, json=REFUSED)
        return httpx.Response(200, json={"ok": True, "result": True})


async def run(fake: FakeApi, chat_plan: purge.Plan, **options) -> dict:
    async with httpx.AsyncClient(transport=httpx.MockTransport(fake)) as http:
        return await purge.execute(BotApi(http, "http://api", "123:abc"), chat_plan, **options)


def test_batches_newest_first():
    batches = purge.Plan(chat_id=1, first=1, last=250, keep=[3]).batches()
    assert [len(b) for b in batches] == [100, 100, 49]
    assert batches[0][-1] == 250 and batches[-1][0] == 1 and 3 not in batches[-1]


async def test_execute_deletes_everything_when_all_is_recent():
    fake = FakeApi(oldest=0)
    result = await run(fake, purge.Plan(chat_id=1, first=1, last=250))
    assert result["status"] == "ok"
    assert len(fake.calls) == 3
    assert sorted(i for c in fake.calls for i in c) == list(range(1, 251))


async def test_execute_finds_the_boundary_and_stops():
    fake = FakeApi(oldest=170)
    result = await run(fake, purge.Plan(chat_id=1, first=1, last=300))
    assert result["status"] == "ok"
    accepted = sorted(i for c in fake.calls if min(c) >= 170 for i in c)
    assert accepted == list(range(170, 301))
    # The batch with 101-200 is split; the batch with 1-100 is never tried.
    assert all(max(c) > 100 for c in fake.calls)
    assert len(fake.calls) < 40
    assert any("Stopped at" in line for line in result["logs"])


async def test_execute_waits_for_flood_control():
    fake = FakeApi(oldest=0, flood_once=True)
    result = await run(fake, purge.Plan(chat_id=1, first=1, last=10))
    assert result["status"] == "ok"
    assert len(fake.calls) == 2


async def test_execute_has_a_time_limit():
    fake = FakeApi(oldest=0)
    result = await run(fake, purge.Plan(chat_id=1, first=1, last=500), time_limit=-1)
    assert result["status"] == "timeout"
    assert fake.calls == []
