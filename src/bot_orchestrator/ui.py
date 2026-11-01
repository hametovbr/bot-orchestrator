"""Shared helpers for the web views."""

from __future__ import annotations

import datetime as dt
import json
import re
from pathlib import Path
from typing import TYPE_CHECKING, Any
from zoneinfo import ZoneInfo

from fastapi import HTTPException, Request
from fastapi.responses import RedirectResponse, Response
from fastapi.templating import Jinja2Templates

from . import __version__
from .config import Settings
from .routing import describe as describe_route
from .routing import pattern_text
from .security import short_token

if TYPE_CHECKING:
    from .service import Orchestrator

HERE = Path(__file__).parent


def human_size(value: float | None) -> str:
    size = float(value or 0)
    for unit in ("B", "KB", "MB", "GB"):
        if size < 1024 or unit == "GB":
            return f"{size:.0f} {unit}" if unit == "B" else f"{size:.1f} {unit}"
        size /= 1024
    return f"{size:.1f} TB"


def human_duration(ms: int | None) -> str:
    if ms is None:
        return ""
    if ms < 1000:
        return f"{ms} ms"
    seconds = ms / 1000
    if seconds < 60:
        return f"{seconds:.1f} s"
    minutes, seconds = divmod(int(seconds), 60)
    if minutes < 60:
        return f"{minutes} min {seconds} s"
    hours, minutes = divmod(minutes, 60)
    return f"{hours} h {minutes} min"


_NO_MODULE = re.compile(r"No module named '([^']+)'")


def missing_module(error: str | None) -> str:
    """The module name from a ModuleNotFoundError in a traceback, or ''."""
    found = _NO_MODULE.search(error or "")
    return found.group(1) if found else ""


class Ui:
    """Templates, flash messages and lookups used by every view module."""

    def __init__(self, settings: Settings, state: dict[str, Any]):
        self.settings = settings
        self._state = state
        self.timezone = ZoneInfo(settings.tz)
        self.templates = Jinja2Templates(directory=HERE / "templates")
        env = self.templates.env
        env.filters["time"] = self._time
        env.filters["size"] = human_size
        env.filters["duration"] = human_duration
        env.filters["pretty"] = lambda v: json.dumps(v, ensure_ascii=False, indent=2)
        env.filters["route"] = describe_route
        env.filters["route_pattern"] = pattern_text
        env.filters["missing_module"] = missing_module
        env.globals["version"] = __version__

    def _time(self, value: float | None, seconds: bool = True) -> str:
        if not value:
            return ""
        moment = dt.datetime.fromtimestamp(value, self.timezone)
        return moment.strftime("%Y-%m-%d %H:%M:%S" if seconds else "%Y-%m-%d %H:%M")

    def orch(self) -> Orchestrator:
        return self._state["o"]

    @staticmethod
    def flash(request: Request, message: str, kind: str = "info") -> None:
        # Reassign instead of appending in place: the session only notices top-level changes.
        request.session["flash"] = [
            *request.session.get("flash", []),
            {"kind": kind, "text": message},
        ]

    def render(self, request: Request, name: str, **context: Any) -> Response:
        messages = request.session.pop("flash", [])
        return self.templates.TemplateResponse(request, name, {"messages": messages, **context})

    @staticmethod
    def back(url: str) -> RedirectResponse:
        return RedirectResponse(url, status_code=303)

    async def bot(self, bot_id: int) -> dict[str, Any]:
        o = self.orch()
        bot = await o.db.get_bot(bot_id)
        if not bot:
            raise HTTPException(status_code=404, detail="Bot not found")
        bot["token_hint"] = short_token(o.token_of(bot))
        bot["polling"] = o.is_polling(bot_id)
        return bot

    async def handler(self, bot_id: int, handler_id: int) -> dict[str, Any]:
        handler = await self.orch().db.get_handler(bot_id, handler_id)
        if not handler:
            raise HTTPException(status_code=404, detail="Handler not found")
        return handler
