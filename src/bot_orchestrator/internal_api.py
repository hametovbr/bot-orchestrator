"""Internal API used by bot-runner: key-value storage and background tasks."""

import hmac
import re
from typing import Annotated, Any

from fastapi import Body, Depends, FastAPI, Header, HTTPException

from .service import BotError, Orchestrator
from .ui import Ui

KV_KEY = re.compile(r"^[\w.:-]{1,200}$")


def register(app: FastAPI, ui: Ui) -> None:
    Orch = Annotated[Orchestrator, Depends(ui.orch)]
    secret = ui.settings.runner_secret

    def runner_only(x_runner_secret: Annotated[str | None, Header()] = None) -> None:
        if not x_runner_secret or not hmac.compare_digest(x_runner_secret, secret):
            raise HTTPException(status_code=401, detail="invalid runner secret")

    Runner = [Depends(runner_only)]

    def check_key(key: str) -> None:
        if not KV_KEY.match(key):
            raise HTTPException(status_code=400, detail="invalid key")

    # Key-value storage

    @app.get("/internal/kv/{bot_id}/{key}", dependencies=Runner)
    async def kv_get(o: Orch, bot_id: int, key: str):
        check_key(key)
        found, value = await o.db.kv_get(bot_id, key)
        if not found:
            raise HTTPException(status_code=404, detail="key not found")
        return {"value": value}

    @app.put("/internal/kv/{bot_id}/{key}", dependencies=Runner)
    async def kv_put(o: Orch, bot_id: int, key: str, body: Annotated[dict[str, Any], Body()]):
        check_key(key)
        if not await o.db.get_bot(bot_id):
            raise HTTPException(status_code=404, detail="bot not found")
        await o.db.kv_set(bot_id, key, body.get("value"))
        return {"ok": True}

    @app.delete("/internal/kv/{bot_id}/{key}", dependencies=Runner)
    async def kv_delete(o: Orch, bot_id: int, key: str):
        check_key(key)
        await o.db.kv_delete(bot_id, key)
        return {"ok": True}

    # Background tasks

    async def task_of(o: Orchestrator, task_id: int, bot_id: Any) -> dict[str, Any]:
        task = await o.db.get_task(task_id, int(bot_id or 0))
        if not task:
            raise HTTPException(status_code=404, detail="task not found")
        return task

    @app.post("/internal/tasks", dependencies=Runner)
    async def create_task(o: Orch, body: Annotated[dict[str, Any], Body()]):
        entry = str(body.get("entry", ""))
        if not entry.isidentifier() or not isinstance(body.get("code"), str):
            raise HTTPException(status_code=400, detail="entry and code are required")
        if not isinstance(body.get("args", []), list) or not isinstance(
            body.get("kwargs", {}), dict
        ):
            raise HTTPException(status_code=400, detail="args must be a list, kwargs an object")
        try:
            task_id = await o.tasks.enqueue(int(body.get("bot_id") or 0), body)
        except BotError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from None
        return {"id": task_id}

    @app.get("/internal/tasks/{task_id}", dependencies=Runner)
    async def get_task(o: Orch, task_id: int, bot_id: int):
        task = await task_of(o, task_id, bot_id)
        return {key: task[key] for key in ("id", "title", "status", "progress", "error", "entry")}

    @app.post("/internal/tasks/{task_id}/cancel", dependencies=Runner)
    async def cancel_task(o: Orch, task_id: int, body: Annotated[dict[str, Any], Body()]):
        await task_of(o, task_id, body.get("bot_id"))
        return {"cancelled": await o.tasks.cancel(task_id)}

    @app.post("/internal/tasks/{task_id}/progress", dependencies=Runner)
    async def task_progress(o: Orch, task_id: int, body: Annotated[dict[str, Any], Body()]):
        await task_of(o, task_id, body.get("bot_id"))
        await o.db.set_task_progress(task_id, str(body.get("text", ""))[:500])
        return {"ok": True}
