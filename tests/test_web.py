import time

from .conftest import BOT_ID, RUNNER_SECRET, TOKEN, make_update


def wait_for(client, predicate, timeout=5.0):
    """Poll the database from the app's event loop until predicate(rows) is true."""
    deadline = time.time() + timeout
    while time.time() < deadline:
        rows = client.portal.call(client.orchestrator.db.list_updates, BOT_ID)
        if predicate(rows):
            return rows
        time.sleep(0.05)
    raise AssertionError(f"condition not met; rows: {rows}")


def add_bot(client, log_out_cloud=True):
    data = {"token": TOKEN}
    if log_out_cloud:
        data["log_out_cloud"] = "true"
    return client.post("/bots", data=data, follow_redirects=False)


def test_pages_require_login(client):
    response = client.get("/", follow_redirects=False)
    assert response.status_code == 303
    assert response.headers["location"] == "/login"
    assert client.post("/bots", data={"token": TOKEN}).status_code == 401


def test_wrong_password(client):
    response = client.post("/login", data={"password": "nope"})
    assert "Wrong password." in response.text
    assert client.get("/", follow_redirects=False).status_code == 303


def test_add_bot_logs_out_of_cloud_and_starts_polling(logged_in, fake):
    response = add_bot(logged_in)
    assert response.status_code == 303
    assert response.headers["location"] == f"/bots/{BOT_ID}/handler"
    assert [m for m, _ in fake.cloud_calls] == ["deleteWebhook", "logOut"]
    assert fake.local_calls[0][0] == "getMe"
    page = logged_in.get("/")
    assert "@test_bot" in page.text
    assert logged_in.orchestrator.is_polling(BOT_ID)
    # The token is stored encrypted and never shown in full.
    bot = logged_in.portal.call(logged_in.orchestrator.db.get_bot, BOT_ID)
    assert TOKEN not in bot["token_enc"]
    assert TOKEN not in logged_in.get(f"/bots/{BOT_ID}/settings").text


def test_add_bot_rejects_bad_token(logged_in):
    response = logged_in.post("/bots", data={"token": "not-a-token"})
    assert "does not look like a bot token" in response.text
    response = logged_in.post("/bots", data={"token": "111111:" + "C" * 35})
    assert "rejected the token" in response.text


def test_update_without_handler_is_logged(logged_in, fake):
    add_bot(logged_in)
    fake.pending.append(make_update(100))
    rows = wait_for(logged_in, lambda r: len(r) == 1)
    assert rows[0]["status"] == "no_handler"
    assert rows[0]["summary"] == "hello"
    assert rows[0]["from_name"] == "@ann"
    assert fake.runs == []


def test_saved_handler_receives_updates(logged_in, fake):
    add_bot(logged_in)
    code = "async def handle(update, ctx):\n    await ctx.reply('hi')\n"
    response = logged_in.post(f"/bots/{BOT_ID}/handler", data={"code": code, "comment": "first"})
    assert "Saved and activated version 1." in response.text
    fake.pending.append(make_update(101))
    rows = wait_for(logged_in, lambda r: r and r[0]["status"] == "ok")
    assert rows[0]["handler_version"] == 1
    run = fake.runs[0]
    assert run["code"] == code
    assert run["bot"] == {"id": BOT_ID, "username": "test_bot", "token": TOKEN}
    assert run["dry_run"] is False
    detail = logged_in.get(f"/bots/{BOT_ID}/updates/{rows[0]['id']}")
    assert "handled" in detail.text


def test_offset_advances(logged_in, fake):
    add_bot(logged_in)
    fake.pending += [make_update(200), make_update(201)]
    wait_for(logged_in, lambda r: len(r) == 2)
    time.sleep(0.2)
    rows = logged_in.portal.call(logged_in.orchestrator.db.list_updates, BOT_ID)
    assert len(rows) == 2, "updates must not be received twice"
    offsets = [b.get("offset") for m, b in fake.local_calls if m == "getUpdates"]
    assert offsets[-1] == 202


def test_syntax_error_is_not_saved(logged_in):
    add_bot(logged_in)
    response = logged_in.post(f"/bots/{BOT_ID}/handler", data={"code": "def handle(:"})
    assert "syntax error on line 1" in response.text
    versions = logged_in.portal.call(logged_in.orchestrator.db.list_versions, BOT_ID)
    assert versions == []


def test_allowed_users_filter(logged_in, fake):
    add_bot(logged_in)
    logged_in.post(f"/bots/{BOT_ID}/handler", data={"code": "def handle(u, c): pass"})
    logged_in.post(f"/bots/{BOT_ID}/allowed-users", data={"allowed": "1, 2"})
    fake.pending.append(make_update(300, user_id=42))
    rows = wait_for(logged_in, lambda r: len(r) == 1)
    assert rows[0]["status"] == "ignored"
    assert fake.runs == []


def test_runner_failure_is_reported(logged_in, fake):
    add_bot(logged_in)
    logged_in.post(f"/bots/{BOT_ID}/handler", data={"code": "def handle(u, c): pass"})
    fake.runner_result = {
        "status": "error",
        "error": f"Traceback ... {TOKEN}",
        "logs": [],
        "api_calls": [],
        "duration_ms": 3,
    }
    fake.pending.append(make_update(400))
    wait_for(logged_in, lambda r: r and r[0]["status"] == "error")
    page = logged_in.get(f"/bots/{BOT_ID}/updates")
    assert "Traceback" in page.text


def test_test_run_is_dry_by_default(logged_in, fake):
    add_bot(logged_in)
    fake.pending.append(make_update(500))
    rows = wait_for(logged_in, lambda r: len(r) == 1)
    response = logged_in.post(
        f"/bots/{BOT_ID}/handler/test",
        json={"code": "def handle(u, c): pass", "update_row": rows[0]["id"]},
    )
    assert response.json()["status"] == "ok"
    assert fake.runs[-1]["dry_run"] is True
    assert fake.runs[-1]["update"]["update_id"] == 500
    bad = logged_in.post(f"/bots/{BOT_ID}/handler/test", json={"code": "", "update_json": "[1]"})
    assert bad.status_code == 400


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
    logged_in.post(f"/bots/{BOT_ID}/secrets", data={"name": "API_KEY", "value": "s3cret"})
    page = logged_in.get(f"/bots/{BOT_ID}/settings")
    assert "API_KEY" in page.text and "s3cret" not in page.text
    logged_in.post(
        f"/bots/{BOT_ID}/handler/test", json={"code": "", "update_json": '{"update_id": 1}'}
    )
    assert fake.runs[-1]["secrets"] == {"API_KEY": "s3cret"}


def test_internal_kv(logged_in):
    add_bot(logged_in)
    url = f"/internal/kv/{BOT_ID}/counter"
    assert logged_in.get(url).status_code == 401
    headers = {"X-Runner-Secret": RUNNER_SECRET}
    assert logged_in.get(url, headers=headers).status_code == 404
    assert logged_in.put(url, headers=headers, json={"value": {"n": 1}}).status_code == 200
    assert logged_in.get(url, headers=headers).json() == {"value": {"n": 1}}
    assert "counter" in logged_in.get(f"/bots/{BOT_ID}/settings").text
    logged_in.delete(url, headers=headers)
    assert logged_in.get(url, headers=headers).status_code == 404
    assert logged_in.get(f"/internal/kv/{BOT_ID}/bad%20key", headers=headers).status_code == 400


def test_files(logged_in, settings):
    from pathlib import Path

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
    fake.pending.append(make_update(600))
    rows = wait_for(logged_in, lambda r: len(r) == 1)
    for path in [
        "/",
        f"/bots/{BOT_ID}/updates",
        f"/bots/{BOT_ID}/updates/{rows[0]['id']}",
        f"/bots/{BOT_ID}/handler",
        f"/bots/{BOT_ID}/console",
        f"/bots/{BOT_ID}/files",
        f"/bots/{BOT_ID}/settings",
        f"/bots/{BOT_ID}/updates?status=ok",
    ]:
        assert logged_in.get(path).status_code == 200, path


def test_delete_bot(logged_in):
    add_bot(logged_in)
    response = logged_in.post(f"/bots/{BOT_ID}/delete")
    assert "removed" in response.text
    assert not logged_in.orchestrator.is_polling(BOT_ID)
    assert logged_in.get(f"/bots/{BOT_ID}/updates").status_code == 404
