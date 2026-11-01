import asyncio
import time
from pathlib import Path

from .conftest import BOT_ID, RUNNER_SECRET, TOKEN, make_update

RUNNER = {"X-Runner-Secret": RUNNER_SECRET}


def db_call(client, method, *args, **kwargs):
    """Run a database coroutine on the app's event loop."""
    return client.portal.call(lambda: method(*args, **kwargs))


def wait_for(client, predicate, timeout=5.0, getter=None):
    """Poll until predicate(rows) is true; rows are update log entries by default."""
    getter = getter or (lambda: client.orchestrator.db.list_updates(BOT_ID))
    deadline = time.time() + timeout
    rows = None
    while time.time() < deadline:
        rows = client.portal.call(getter)
        if predicate(rows):
            return rows
        time.sleep(0.05)
    raise AssertionError(f"condition not met; rows: {rows}")


def add_bot(client, log_out_cloud=True):
    data = {"token": TOKEN}
    if log_out_cloud:
        data["log_out_cloud"] = "true"
    return client.post("/bots", data=data, follow_redirects=False)


def create_handler(client, name="main", template="echo", add_route=True) -> int:
    data = {"name": name, "template": template}
    if add_route:
        data["add_route"] = "true"
    response = client.post(f"/bots/{BOT_ID}/handlers", data=data, follow_redirects=False)
    assert response.status_code == 303, response.text
    return int(response.headers["location"].rsplit("/", 1)[1])


def add_route(client, kind, pattern, handler_id, description="", enabled=True):
    data = {"kind": kind, "pattern": pattern, "handler_id": handler_id, "description": description}
    if enabled:
        data["enabled"] = "true"
    return client.post(f"/bots/{BOT_ID}/routes", data=data)


# Basics


def test_pages_require_login(client):
    response = client.get("/", follow_redirects=False)
    assert response.status_code == 303
    assert response.headers["location"] == "/login"
    assert client.post("/bots", data={"token": TOKEN}).status_code == 401
    assert client.get("/settings", follow_redirects=False).status_code == 303


def test_wrong_password(client):
    response = client.post("/login", data={"password": "nope"})
    assert "Wrong password." in response.text
    assert client.get("/", follow_redirects=False).status_code == 303


def test_add_bot_logs_out_of_cloud_and_starts_polling(logged_in, fake):
    response = add_bot(logged_in)
    assert response.status_code == 303
    assert response.headers["location"] == f"/bots/{BOT_ID}/handlers"
    assert [m for m, _ in fake.cloud_calls] == ["deleteWebhook", "logOut"]
    assert fake.local_calls[0][0] == "getMe"
    assert "@test_bot" in logged_in.get("/").text
    assert logged_in.orchestrator.is_polling(BOT_ID)
    bot = db_call(logged_in, logged_in.orchestrator.db.get_bot, BOT_ID)
    assert TOKEN not in bot["token_enc"]
    assert TOKEN not in logged_in.get(f"/bots/{BOT_ID}/settings").text


def test_add_bot_rejects_bad_token(logged_in):
    response = logged_in.post("/bots", data={"token": "not-a-token"})
    assert "does not look like a bot token" in response.text
    response = logged_in.post("/bots", data={"token": "111111:" + "C" * 35})
    assert "rejected the token" in response.text


# Routing


def test_update_without_route_is_logged(logged_in, fake):
    add_bot(logged_in)
    fake.pending.append(make_update(100))
    rows = wait_for(logged_in, lambda r: len(r) == 1)
    assert rows[0]["status"] == "no_route"
    assert rows[0]["summary"] == "hello"
    assert rows[0]["from_name"] == "@ann"
    assert fake.runs == []


def test_routes_send_updates_to_handlers(logged_in, fake):
    add_bot(logged_in)
    main = create_handler(logged_in, "main")  # adds a catch-all route
    start = create_handler(logged_in, "start", add_route=False)
    response = add_route(logged_in, "command", "/start", start, "Start the bot")
    assert "Route added: /start." in response.text
    routes = db_call(logged_in, logged_in.orchestrator.db.list_routes, BOT_ID)
    # Put /start above the catch-all.
    order = logged_in.post(
        f"/bots/{BOT_ID}/routes-order", json={"ids": [routes[1]["id"], routes[0]["id"]]}
    )
    assert order.json() == {"ok": True}

    fake.pending += [make_update(101, "/start"), make_update(102, "hello")]
    rows = wait_for(logged_in, lambda r: len(r) == 2 and all(x["status"] == "ok" for x in r))
    by_update = {r["update_id"]: r for r in rows}
    assert by_update[101]["handler_name"] == "start"
    assert by_update[102]["handler_name"] == "main"
    assert {run["handler"]["id"] for run in fake.runs} == {main, start}
    run = next(r for r in fake.runs if r["update"]["update_id"] == 101)
    assert run["bot"] == {"id": BOT_ID, "username": "test_bot", "token": TOKEN}
    assert run["kind"] == "handler" and run["dry_run"] is False
    assert run["handler"] == {"id": start, "name": "start", "version": 1}
    assert run["origin"] == {"update_row": by_update[101]["id"]}

    detail = logged_in.get(f"/bots/{BOT_ID}/updates/{by_update[101]['id']}")
    assert "/start" in detail.text and "handled" in detail.text

    match = logged_in.post(
        f"/bots/{BOT_ID}/routes-match", json={"update_row": by_update[102]["id"]}
    )
    assert match.json()["text"] == "Matches “anything” → main"


def test_disabled_route_is_skipped(logged_in, fake):
    add_bot(logged_in)
    create_handler(logged_in, "main")
    route = db_call(logged_in, logged_in.orchestrator.db.list_routes, BOT_ID)[0]
    logged_in.post(f"/bots/{BOT_ID}/routes/{route['id']}/toggle")
    fake.pending.append(make_update(103))
    rows = wait_for(logged_in, lambda r: len(r) == 1)
    assert rows[0]["status"] == "no_route"


def test_route_validation_and_edit(logged_in):
    add_bot(logged_in)
    handler = create_handler(logged_in, "main", add_route=False)
    assert "Invalid regular expression" in add_route(logged_in, "regex", "(", handler).text
    assert "Content types must be from" in add_route(logged_in, "content", "pdf", handler).text
    add_route(logged_in, "regex", r"https?://\S+", handler)
    route = db_call(logged_in, logged_in.orchestrator.db.list_routes, BOT_ID)[0]
    page = logged_in.get(f"/bots/{BOT_ID}/routes/{route['id']}")
    assert "https?://" in page.text
    logged_in.post(
        f"/bots/{BOT_ID}/routes/{route['id']}",
        data={"kind": "content", "pattern": "document", "handler_id": handler},
    )
    route = db_call(logged_in, logged_in.orchestrator.db.get_route, BOT_ID, route["id"])
    assert (route["kind"], route["pattern"], route["enabled"]) == ("content", "document", 0)
    logged_in.post(f"/bots/{BOT_ID}/routes/{route['id']}/delete")
    assert db_call(logged_in, logged_in.orchestrator.db.list_routes, BOT_ID) == []


def test_publish_commands(logged_in, fake):
    add_bot(logged_in)
    handler = create_handler(logged_in, "main", add_route=False)
    add_route(logged_in, "command", "start", handler, "Start the bot")
    add_route(logged_in, "command", "help", handler)
    add_route(logged_in, "command", "off", handler, enabled=False)
    response = logged_in.post(f"/bots/{BOT_ID}/routes-commands")
    assert "The bot menu now has 2 commands." in response.text
    body = [b for m, b in fake.local_calls if m == "setMyCommands"][-1]
    assert body["commands"] == [
        {"command": "start", "description": "Start the bot"},
        {"command": "help", "description": "main"},
    ]


def test_allowed_users_filter(logged_in, fake):
    add_bot(logged_in)
    create_handler(logged_in, "main")
    logged_in.post(f"/bots/{BOT_ID}/allowed-users", data={"allowed": "1, 2"})
    fake.pending.append(make_update(300, user_id=42))
    rows = wait_for(logged_in, lambda r: len(r) == 1)
    assert rows[0]["status"] == "ignored"
    assert fake.runs == []


def test_offset_advances(logged_in, fake):
    add_bot(logged_in)
    fake.pending += [make_update(200), make_update(201)]
    wait_for(logged_in, lambda r: len(r) == 2)
    time.sleep(0.2)
    rows = db_call(logged_in, logged_in.orchestrator.db.list_updates, BOT_ID)
    assert len(rows) == 2, "updates must not be received twice"
    offsets = [b.get("offset") for m, b in fake.local_calls if m == "getUpdates"]
    assert offsets[-1] == 202


# Handlers


def test_handler_versions_and_templates(logged_in):
    add_bot(logged_in)
    handler = create_handler(logged_in, "dl", template="download_link")
    page = logged_in.get(f"/bots/{BOT_ID}/handlers/{handler}")
    assert "ctx.background(fetch" in page.text
    routes = db_call(logged_in, logged_in.orchestrator.db.list_routes, BOT_ID)
    assert (routes[0]["kind"], routes[0]["pattern"]) == ("regex", r"https?://\S+")

    bad = logged_in.post(f"/bots/{BOT_ID}/handlers/{handler}", data={"code": "def handle(:"})
    assert "syntax error on line 1" in bad.text
    saved = logged_in.post(
        f"/bots/{BOT_ID}/handlers/{handler}", data={"code": "def handle(u, c): pass"}
    )
    assert "Saved and activated version 2." in saved.text
    logged_in.post(f"/bots/{BOT_ID}/handlers/{handler}/activate", data={"version": 1})
    active = db_call(logged_in, logged_in.orchestrator.db.active_code, handler)
    assert active["version"] == 1 and "fetch" in active["code"]

    dup = logged_in.post(f"/bots/{BOT_ID}/handlers", data={"name": "dl"})
    assert "already exists" in dup.text
    logged_in.post(f"/bots/{BOT_ID}/handlers/{handler}/rename", data={"name": "download"})
    assert "download" in logged_in.get(f"/bots/{BOT_ID}/handlers").text
    logged_in.post(f"/bots/{BOT_ID}/handlers/{handler}/delete")
    assert db_call(logged_in, logged_in.orchestrator.db.list_routes, BOT_ID) == []


def test_test_run_is_dry_by_default(logged_in, fake):
    add_bot(logged_in)
    handler = create_handler(logged_in, "main", add_route=False)
    fake.pending.append(make_update(500))
    rows = wait_for(logged_in, lambda r: len(r) == 1)
    response = logged_in.post(
        f"/bots/{BOT_ID}/handlers/{handler}/test",
        json={"code": "def handle(u, c): pass", "update_row": rows[0]["id"]},
    )
    assert response.json()["status"] == "ok"
    assert fake.runs[-1]["dry_run"] is True
    assert fake.runs[-1]["update"]["update_id"] == 500
    assert fake.runs[-1]["handler"] == {"id": handler, "name": "main", "version": 0}
    bad = logged_in.post(
        f"/bots/{BOT_ID}/handlers/{handler}/test", json={"code": "", "update_json": "[1]"}
    )
    assert bad.status_code == 400


def test_old_handler_url_redirects(logged_in):
    add_bot(logged_in)
    response = logged_in.get(f"/bots/{BOT_ID}/handler", follow_redirects=False)
    assert response.headers["location"] == f"/bots/{BOT_ID}/handlers"


# Background tasks


def enqueue(client, **extra):
    body = {
        "bot_id": BOT_ID,
        "handler": {"id": None, "name": "main"},
        "code": "async def fetch(ctx, url): pass",
        "entry": "fetch",
        "args": ["https://x.test/v"],
        "title": "Video",
        "update": make_update(1),
        "origin": {"update_row": None},
        **extra,
    }
    return client.post("/internal/tasks", json=body, headers=RUNNER)


def tasks_of(client):
    return lambda: client.orchestrator.db.list_tasks(BOT_ID)


def test_background_task_runs_and_is_shown(logged_in, fake):
    add_bot(logged_in)
    assert logged_in.post("/internal/tasks", json={}).status_code == 401
    response = enqueue(logged_in)
    task_id = response.json()["id"]
    tasks = wait_for(logged_in, lambda r: r and r[0]["status"] == "ok", getter=tasks_of(logged_in))
    assert tasks[0]["title"] == "Video"
    run = next(r for r in fake.runs if r["kind"] == "task")
    assert run["entry"] == "fetch" and run["args"] == ["https://x.test/v"]
    assert run["task"] == {"id": task_id, "title": "Video"}
    assert run["update"]["update_id"] == 1

    status = logged_in.get(f"/internal/tasks/{task_id}?bot_id={BOT_ID}", headers=RUNNER)
    assert status.json()["status"] == "ok"
    assert logged_in.get(f"/internal/tasks/{task_id}?bot_id=1", headers=RUNNER).status_code == 404

    page = logged_in.get(f"/bots/{BOT_ID}/tasks/{task_id}")
    assert "fetch(ctx, &#34;https://x.test/v&#34;)" in page.text or "https://x.test/v" in page.text
    assert "Run again" in page.text
    again = logged_in.post(f"/bots/{BOT_ID}/tasks/{task_id}/retry", follow_redirects=False)
    assert again.headers["location"] == f"/bots/{BOT_ID}/tasks/{task_id + 1}"


def test_cancel_running_task_and_progress(logged_in, fake):
    add_bot(logged_in)
    fake.task_gate = asyncio.Event()
    task_id = enqueue(logged_in).json()["id"]
    wait_for(logged_in, lambda r: r and r[0]["status"] == "running", getter=tasks_of(logged_in))
    progress = logged_in.post(
        f"/internal/tasks/{task_id}/progress",
        json={"bot_id": BOT_ID, "text": "42%"},
        headers=RUNNER,
    )
    assert progress.status_code == 200
    assert "42%" in logged_in.get(f"/bots/{BOT_ID}/tasks").text
    response = logged_in.post(
        f"/internal/tasks/{task_id}/cancel", json={"bot_id": BOT_ID}, headers=RUNNER
    )
    assert response.json() == {"cancelled": True}
    tasks = wait_for(logged_in, lambda r: r[0]["status"] == "cancelled", getter=tasks_of(logged_in))
    assert fake.cancelled, "the runner was asked to kill the process"
    assert tasks[0]["status"] == "cancelled"


def test_cancel_queued_task(logged_in, fake, settings):
    add_bot(logged_in)
    fake.task_gate = asyncio.Event()
    ids = [enqueue(logged_in).json()["id"] for _ in range(settings.task_concurrency + 1)]
    wait_for(
        logged_in,
        lambda r: sum(t["status"] == "running" for t in r) == settings.task_concurrency,
        getter=tasks_of(logged_in),
    )
    queued = ids[-1]
    response = logged_in.post(f"/bots/{BOT_ID}/tasks/{queued}/cancel")
    assert "Task cancelled." in response.text
    task = db_call(logged_in, logged_in.orchestrator.db.get_task, queued)
    assert task["status"] == "cancelled"
    fake.task_gate.set()


def test_failed_task_sends_notification(logged_in, fake):
    add_bot(logged_in)
    logged_in.post(
        "/settings/notifications",
        data={"notify_bot_id": BOT_ID, "notify_chat_id": "42", "notify_events": ["task_errors"]},
    )
    fake.runner_result = {
        "status": "error",
        "error": "Traceback...\nDownloadFailed: HTTP Error 404",
        "logs": [],
        "api_calls": [],
        "duration_ms": 5,
    }
    enqueue(logged_in)
    wait_for(logged_in, lambda r: r and r[0]["status"] == "error", getter=tasks_of(logged_in))
    deadline = time.time() + 5
    while time.time() < deadline:
        sent = [b for m, b in fake.local_calls if m == "sendMessage"]
        if sent:
            break
        time.sleep(0.05)
    assert sent[0]["chat_id"] == 42
    assert "task “Video”: error" in sent[0]["text"]
    assert "DownloadFailed: HTTP Error 404" in sent[0]["text"]
    assert "https://bots.test/bots/" in sent[0]["text"]


# Schedules


def test_schedule_create_run_and_preview(logged_in, fake):
    add_bot(logged_in)
    handler = create_handler(logged_in, "report", template="scheduled", add_route=False)
    bad = logged_in.post(
        f"/bots/{BOT_ID}/schedules",
        data={"name": "x", "cron": "61 * * * *", "handler_id": handler, "enabled": "true"},
    )
    assert "Cron expression" in bad.text
    logged_in.post(
        f"/bots/{BOT_ID}/schedules",
        data={
            "name": "Morning",
            "cron": "0 9 * * mon-fri",
            "handler_id": handler,
            "chat_id": "42",
            "enabled": "true",
        },
    )
    schedule = db_call(logged_in, logged_in.orchestrator.db.list_schedules, BOT_ID)[0]
    assert schedule["next_run_at"] > time.time()
    page = logged_in.get(f"/bots/{BOT_ID}/schedules")
    assert "Morning" in page.text and "0 9 * * mon-fri" in page.text

    preview = logged_in.get(f"/bots/{BOT_ID}/schedules-preview?expression=@daily").json()
    assert preview["ok"] and len(preview["runs"]) == 3 and preview["runs"][0].endswith("00:00")

    logged_in.post(f"/bots/{BOT_ID}/schedules/{schedule['id']}/run")
    rows = wait_for(logged_in, lambda r: r and r[0]["status"] == "ok")
    assert rows[0]["kind"] == "schedule" and rows[0]["summary"] == "[schedule] Morning"
    run = fake.runs[-1]
    assert run["update"]["schedule"]["chat"] == {"id": 42}
    assert run["update"]["schedule"]["manual"] is True
    schedule = db_call(logged_in, logged_in.orchestrator.db.get_schedule, BOT_ID, schedule["id"])
    assert schedule["last_status"] == "ok"

    logged_in.post(f"/bots/{BOT_ID}/schedules/{schedule['id']}/toggle")
    schedule = db_call(logged_in, logged_in.orchestrator.db.get_schedule, BOT_ID, schedule["id"])
    assert schedule["enabled"] == 0 and schedule["next_run_at"] is None


def test_due_schedule_fires(logged_in, fake):
    add_bot(logged_in)
    handler = create_handler(logged_in, "report", template="scheduled", add_route=False)
    o = logged_in.orchestrator
    schedule_id = db_call(
        logged_in,
        o.db.create_schedule,
        BOT_ID,
        name="Due",
        cron="* * * * *",
        handler_id=handler,
        next_run_at=time.time() - 1,
    )
    due = db_call(logged_in, o.db.due_schedules, time.time())
    assert [s["id"] for s in due] == [schedule_id]
    db_call(logged_in, o.run_schedule, due[0])
    rows = wait_for(logged_in, lambda r: r and r[0]["status"] == "ok")
    assert rows[0]["handler_name"] == "report"


# Notifications and settings


def test_handler_error_notification_is_throttled(logged_in, fake):
    add_bot(logged_in)
    create_handler(logged_in, "main")
    logged_in.post(
        "/settings/notifications",
        data={"notify_bot_id": BOT_ID, "notify_chat_id": "42", "notify_events": ["handler_errors"]},
    )
    fake.runner_result = {
        "status": "error",
        "error": f"Traceback ... {TOKEN}\nValueError: boom",
        "logs": [],
        "api_calls": [],
        "duration_ms": 3,
    }
    fake.pending += [make_update(400), make_update(401)]
    wait_for(logged_in, lambda r: len(r) == 2 and all(x["status"] == "error" for x in r))
    time.sleep(0.2)
    sent = [b for m, b in fake.local_calls if m == "sendMessage"]
    assert len(sent) == 1, "the second error within 10 minutes is grouped"
    assert "main v1: error" in sent[0]["text"] and "ValueError: boom" in sent[0]["text"]


def test_settings_page_and_test_notification(logged_in, fake, settings):
    add_bot(logged_in)
    cache = Path(settings.bot_api_data_dir) / TOKEN / "documents"
    cache.mkdir(parents=True)
    (cache / "old.pdf").write_bytes(b"x" * 2048)
    (cache.parent / "td.binlog").write_bytes(b"state")
    page = logged_in.get("/settings")
    assert "2.0 KB in 1 files" in page.text
    assert "Choose a bot and a chat first." in logged_in.post("/settings/notifications/test").text
    bad = logged_in.post("/settings/notifications", data={"notify_chat_id": "me"})
    assert "must be a number" in bad.text
    logged_in.post(
        "/settings/notifications", data={"notify_bot_id": BOT_ID, "notify_chat_id": "-100123"}
    )
    sent = logged_in.post("/settings/notifications/test")
    assert "Test notification sent." in sent.text
    sent_messages = [b for m, b in fake.local_calls if m == "sendMessage"]
    assert sent_messages[-1]["chat_id"] == -100123
    cleaned = logged_in.post("/settings/cache/clean", data={"hours": 0})
    assert "Removed 1 cached files (2.0 KB)." in cleaned.text
    assert (cache.parent / "td.binlog").exists(), "the server's own state is kept"


# Other pages


def test_console(logged_in, fake):
    add_bot(logged_in)
    response = logged_in.post(f"/bots/{BOT_ID}/console", json={"method": "getMe", "params": {}})
    assert response.json()["result"]["username"] == "test_bot"
    blocked = logged_in.post(f"/bots/{BOT_ID}/console", json={"method": "getUpdates", "params": {}})
    assert blocked.status_code == 400
    assert "steal its updates" in blocked.json()["detail"]
    assert "getMe" in logged_in.get(f"/bots/{BOT_ID}/console").text


def test_pause_and_resume(logged_in):
    add_bot(logged_in)
    logged_in.post(f"/bots/{BOT_ID}/pause")
    assert not logged_in.orchestrator.is_polling(BOT_ID)
    logged_in.post(f"/bots/{BOT_ID}/resume")
    assert logged_in.orchestrator.is_polling(BOT_ID)


def test_secrets_are_encrypted_and_sent_to_runner(logged_in, fake):
    add_bot(logged_in)
    handler = create_handler(logged_in, "main", add_route=False)
    logged_in.post(f"/bots/{BOT_ID}/secrets", data={"name": "API_KEY", "value": "s3cret"})
    page = logged_in.get(f"/bots/{BOT_ID}/settings")
    assert "API_KEY" in page.text and "s3cret" not in page.text
    logged_in.post(
        f"/bots/{BOT_ID}/handlers/{handler}/test",
        json={"code": "", "update_json": '{"update_id": 1}'},
    )
    assert fake.runs[-1]["secrets"] == {"API_KEY": "s3cret"}


def test_internal_kv(logged_in):
    add_bot(logged_in)
    url = f"/internal/kv/{BOT_ID}/counter"
    assert logged_in.get(url).status_code == 401
    assert logged_in.get(url, headers=RUNNER).status_code == 404
    assert logged_in.put(url, headers=RUNNER, json={"value": {"n": 1}}).status_code == 200
    assert logged_in.get(url, headers=RUNNER).json() == {"value": {"n": 1}}
    assert "counter" in logged_in.get(f"/bots/{BOT_ID}/settings").text
    logged_in.delete(url, headers=RUNNER)
    assert logged_in.get(url, headers=RUNNER).status_code == 404
    assert logged_in.get(f"/internal/kv/{BOT_ID}/bad%20key", headers=RUNNER).status_code == 400


def test_files(logged_in, settings):
    add_bot(logged_in)
    folder = Path(settings.files_root) / str(BOT_ID) / "inbox"
    folder.mkdir(parents=True)
    (folder / "a.txt").write_text("data")
    page = logged_in.get(f"/bots/{BOT_ID}/files?path=inbox")
    assert "a.txt" in page.text
    download = logged_in.get(f"/bots/{BOT_ID}/files/download?path=inbox/a.txt")
    assert download.content == b"data"
    assert logged_in.get(f"/bots/{BOT_ID}/files?path=../..").status_code == 400
    assert logged_in.get(f"/bots/{BOT_ID}/files/download?path=../../etc/passwd").status_code == 400
    logged_in.post(f"/bots/{BOT_ID}/files/delete", data={"path": "inbox/a.txt"})
    assert not (folder / "a.txt").exists()


def test_cross_origin_post_is_blocked(logged_in):
    response = logged_in.post(
        "/bots", data={"token": TOKEN}, headers={"Origin": "http://evil.example"}
    )
    assert response.status_code == 403


def test_all_pages_render(logged_in, fake):
    add_bot(logged_in)
    handler = create_handler(logged_in, "main")
    logged_in.post(
        f"/bots/{BOT_ID}/schedules",
        data={"name": "Daily", "cron": "@daily", "handler_id": handler, "enabled": "true"},
    )
    fake.pending.append(make_update(600))
    rows = wait_for(logged_in, lambda r: len(r) == 1 and r[0]["status"] == "ok")
    task_id = enqueue(logged_in).json()["id"]
    route = db_call(logged_in, logged_in.orchestrator.db.list_routes, BOT_ID)[0]
    schedule = db_call(logged_in, logged_in.orchestrator.db.list_schedules, BOT_ID)[0]
    for path in [
        "/",
        "/settings",
        f"/bots/{BOT_ID}/updates",
        f"/bots/{BOT_ID}/updates?status=ok",
        f"/bots/{BOT_ID}/updates/{rows[0]['id']}",
        f"/bots/{BOT_ID}/routes",
        f"/bots/{BOT_ID}/routes/{route['id']}",
        f"/bots/{BOT_ID}/handlers",
        f"/bots/{BOT_ID}/handlers/{handler}",
        f"/bots/{BOT_ID}/handlers/{handler}?version=1&update={rows[0]['id']}",
        f"/bots/{BOT_ID}/tasks",
        f"/bots/{BOT_ID}/tasks?status=ok",
        f"/bots/{BOT_ID}/tasks/{task_id}",
        f"/bots/{BOT_ID}/schedules",
        f"/bots/{BOT_ID}/schedules/{schedule['id']}",
        f"/bots/{BOT_ID}/console",
        f"/bots/{BOT_ID}/files",
        f"/bots/{BOT_ID}/settings",
    ]:
        assert logged_in.get(path).status_code == 200, path


def test_delete_bot(logged_in):
    add_bot(logged_in)
    create_handler(logged_in, "main")
    response = logged_in.post(f"/bots/{BOT_ID}/delete")
    assert "removed" in response.text
    assert not logged_in.orchestrator.is_polling(BOT_ID)
    assert logged_in.get(f"/bots/{BOT_ID}/updates").status_code == 404
