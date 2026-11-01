import time

from bot_orchestrator import modules as shared
from bot_orchestrator.packages import normalize, requirements_hash

from .conftest import BOT_ID, make_update
from .test_web import RUNNER, add_bot, create_handler, db_call, enqueue, tasks_of, wait_for


def create_module(client, name, code=None):
    response = client.post("/modules", data={"name": name}, follow_redirects=False)
    assert response.status_code == 303, response.text
    module_id = int(response.headers["location"].rsplit("/", 1)[1])
    if code is not None:
        client.post(f"/modules/{module_id}", data={"code": code})
    return module_id


# Pure functions


def test_shared_imports():
    code = """
from shared import texts, downloads as dl
import shared.media
from shared.util import helper
import os, shared.extra
from shared import *
"""
    assert shared.shared_imports(code) == {"texts", "downloads", "media", "util", "extra"}
    assert shared.shared_imports("def broken(:") == set()


def test_outline_and_names():
    code = '''"""Download helpers."""
LIMIT = 10
_private = 1

async def fetch(ctx, url, *, audio=False):
    """Download and send."""

class Result:
    """A result."""
'''
    assert shared.module_doc(code) == "Download helpers."
    assert shared.outline(code) == [
        {"name": "LIMIT", "kind": "variable", "signature": "LIMIT", "doc": ""},
        {
            "name": "fetch",
            "kind": "function",
            "signature": "async fetch(ctx, url, *, audio=False)",
            "doc": "Download and send.",
        },
        {"name": "Result", "kind": "class", "signature": "class Result", "doc": "A result."},
    ]
    assert shared.check_name("downloads") is None
    for bad in ("Downloads", "1x", "class", "shared", "a-b", ""):
        assert shared.check_name(bad), bad


def test_requirements_hash_matches_runner():
    # Same algorithm as bot_runner.packages; the expected value is fixed on purpose.
    assert normalize("# libs\npillow  # images\n\nopenpyxl\n") == "pillow\nopenpyxl"
    assert requirements_hash("pillow\nopenpyxl") == requirements_hash("pillow\n\nopenpyxl # x")
    assert requirements_hash("") == "e3b0c44298fc1c14"
    assert requirements_hash("pillow\nopenpyxl") == "217427db06ee0d6c"


# Modules


def test_module_lifecycle_and_usage(logged_in, fake):
    add_bot(logged_in)
    texts = create_module(logged_in, "texts", 'GREETING = "Привет"\n')
    assert "already exists" in logged_in.post("/modules", data={"name": "texts"}).text
    assert "reserved word" in logged_in.post("/modules", data={"name": "class"}).text

    handler = create_handler(logged_in, "main")
    saved = logged_in.post(
        f"/bots/{BOT_ID}/handlers/{handler}",
        data={"code": "from shared import texts, nothing\ndef handle(u, ctx): pass\n"},
    )
    assert "No such shared modules yet: nothing" in saved.text

    page = logged_in.get("/modules")
    assert "shared.texts" in page.text and "@test_bot · main" in page.text
    refused = logged_in.post(f"/modules/{texts}/delete")
    assert "Still imported by @test_bot · main" in refused.text

    # Every run carries the active versions of all modules.
    fake.pending.append(make_update(700))
    wait_for(logged_in, lambda r: r and r[0]["status"] == "ok")
    run = fake.runs[-1]
    assert run["modules"] == {"texts": {"version": 2, "code": 'GREETING = "Привет"\n'}}

    # Roll back, then delete once nothing imports it.
    logged_in.post(f"/modules/{texts}/activate", data={"version": 1})
    assert db_call(logged_in, logged_in.orchestrator.db.get_module, texts)["active_version"] == 1
    logged_in.post(f"/bots/{BOT_ID}/handlers/{handler}", data={"code": "def handle(u, c): pass"})
    deleted = logged_in.post(f"/modules/{texts}/delete")
    assert "Module texts deleted." in deleted.text


def test_module_imports_other_module(logged_in):
    base = create_module(logged_in, "base", "X = 1\n")
    create_module(logged_in, "top", "from shared import base\n")
    page = logged_in.get(f"/modules/{base}")
    assert "shared.top" in page.text
    assert "Still imported by shared.top" in logged_in.post(f"/modules/{base}/delete").text


def test_module_syntax_error_not_saved(logged_in):
    module = create_module(logged_in, "util")
    response = logged_in.post(f"/modules/{module}", data={"code": "def broken(:"})
    assert "syntax error on line 1" in response.text
    versions = db_call(logged_in, logged_in.orchestrator.db.list_module_versions, module)
    assert len(versions) == 1


def test_module_test_run_uses_draft(logged_in, fake):
    add_bot(logged_in)
    handler = create_handler(logged_in, "main", add_route=False)
    module = create_module(logged_in, "texts", "A = 1\n")
    response = logged_in.post(
        f"/modules/{module}/test",
        json={
            "code": "A = 2  # draft\n",
            "bot_id": BOT_ID,
            "handler_id": handler,
            "update_json": '{"update_id": 1}',
        },
    )
    assert response.json()["status"] == "ok"
    run = fake.runs[-1]
    assert run["modules"]["texts"] == {"version": 0, "code": "A = 2  # draft\n"}
    assert run["dry_run"] is True
    assert run["handler"]["id"] == handler and "def handle" in run["code"]
    saved = db_call(logged_in, logged_in.orchestrator.db.active_modules)
    assert saved["texts"]["code"] == "A = 1\n", "the draft is not saved"


def test_tasks_pin_module_versions(logged_in, fake):
    add_bot(logged_in)
    module = create_module(logged_in, "texts", "V = 2\n")
    fake.task_gate = None
    o = logged_in.orchestrator
    # Queue while the runner is busy elsewhere: pause dispatch by filling the slots.
    task_id = enqueue(logged_in).json()["id"]
    task = db_call(logged_in, o.db.get_task, task_id)
    assert task["modules"] == {"texts": 2}
    wait_for(logged_in, lambda r: r and r[0]["status"] == "ok", getter=tasks_of(logged_in))
    logged_in.post(f"/modules/{module}", data={"code": "V = 3\n"})
    retry = logged_in.post(f"/bots/{BOT_ID}/tasks/{task_id}/retry", follow_redirects=False)
    assert retry.status_code == 303
    deadline = time.time() + 5
    while time.time() < deadline and len([r for r in fake.runs if r["kind"] == "task"]) < 2:
        time.sleep(0.05)
    task_runs = [r for r in fake.runs if r["kind"] == "task"]
    assert task_runs[-1]["modules"] == {"texts": {"version": 2, "code": "V = 2\n"}}


def test_hints_and_updates_json(logged_in, fake):
    add_bot(logged_in)
    create_module(logged_in, "dl", '"""Downloads."""\n\ndef fetch(url):\n    """Get it."""\n')
    hints = logged_in.get("/modules-hints.json").json()
    assert hints["dl"]["doc"] == "Downloads."
    assert hints["dl"]["items"][0]["signature"] == "fetch(url)"
    fake.pending.append(make_update(800, "hello there"))
    wait_for(logged_in, lambda r: len(r) == 1)
    rows = logged_in.get(f"/bots/{BOT_ID}/updates.json").json()
    assert rows[0]["label"].endswith("message · hello there")


def test_missing_module_hint_on_update_page(logged_in, fake):
    add_bot(logged_in)
    create_handler(logged_in, "main")
    fake.runner_result = {
        "status": "error",
        "error": "Traceback...\nModuleNotFoundError: No module named 'PIL'",
        "logs": [],
        "api_calls": [],
        "duration_ms": 1,
    }
    fake.pending.append(make_update(900))
    rows = wait_for(logged_in, lambda r: r and r[0]["status"] == "error")
    page = logged_in.get(f"/bots/{BOT_ID}/updates/{rows[0]['id']}")
    assert "Package <code>PIL</code> is not installed" in page.text


# Packages


def test_packages_save_and_status(logged_in, fake):
    page = logged_in.get("/packages")
    assert "No extra packages installed." in page.text
    bad = logged_in.post("/packages", data={"requirements": "-e ."})
    assert "Options are not supported" in bad.text
    assert fake.package_installs == []
    ok = logged_in.post("/packages", data={"requirements": "pillow\n# comment\nopenpyxl"})
    assert "bot-runner is installing the packages." in ok.text
    assert fake.package_installs == ["pillow\n# comment\nopenpyxl"]
    page = logged_in.get("/packages")
    assert "badge-ok" in page.text and "<code>pillow</code>" in page.text
    saved = db_call(logged_in, logged_in.orchestrator.db.get_setting, "packages_requirements")
    assert saved == "pillow\n# comment\nopenpyxl"


def test_sync_reinstalls_when_runner_lost_packages(logged_in, fake):
    o = logged_in.orchestrator
    db_call(logged_in, o.db.set_setting, "packages_requirements", "pillow")
    assert db_call(logged_in, o.packages.sync) is True, "runner has nothing; install"
    assert fake.package_installs == ["pillow"]
    assert db_call(logged_in, o.packages.sync) is False, "already in sync"
    # A failed install for the same list is not retried in a loop.
    fake.packages.update(
        hash=requirements_hash(""), error="boom", target_hash=requirements_hash("pillow")
    )
    assert db_call(logged_in, o.packages.sync) is False


def test_runner_unreachable_on_packages_page(logged_in, fake, monkeypatch):
    async def down(*args, **kwargs):
        from bot_orchestrator.packages import PackagesError

        raise PackagesError("bot-runner is unreachable: ConnectError")

    monkeypatch.setattr(logged_in.orchestrator.packages, "_call", down)
    page = logged_in.get("/packages")
    assert "runner unreachable" in page.text


def test_shared_pages_render(logged_in):
    add_bot(logged_in)
    module = create_module(logged_in, "texts")
    create_handler(logged_in, "main")
    for path in ["/modules", f"/modules/{module}", f"/modules/{module}?version=1", "/packages"]:
        assert logged_in.get(path).status_code == 200, path
    assert logged_in.get("/modules/999").status_code == 404
    assert logged_in.post("/internal/tasks", json={}, headers=RUNNER).status_code == 400
