"""Common lookups for operations."""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any

from ..errors import Invalid, NotFound
from ..security import short_token

if TYPE_CHECKING:
    from ..db import Database
    from ..service import Orchestrator

# A bot is referred to by its numeric id or its @username; handlers and modules by
# id or name. Names are what people and agents remember; ids are what URLs use.
BotRef = int | str
NameRef = int | str

# Fields of a bot row that never leave the operations layer.
PRIVATE_BOT_FIELDS = ("token_enc",)


def public_bot(o: Orchestrator, bot: dict[str, Any]) -> dict[str, Any]:
    """A bot row without the encrypted token, with a token hint and the polling state."""
    result = {k: v for k, v in bot.items() if k not in PRIVATE_BOT_FIELDS}
    result["token_hint"] = short_token(o.token_of(bot))
    result["polling"] = o.is_polling(bot["id"])
    return result


def as_id(ref: NameRef) -> int | None:
    """The number in a reference such as 12 or "12", else None."""
    if isinstance(ref, int):
        return ref
    text = str(ref).strip()
    return int(text) if text.isdigit() else None


class Base:
    """Shared by every operation group: the orchestrator and lookups that raise NotFound."""

    def __init__(self, orchestrator: Orchestrator):
        self.o = orchestrator

    @property
    def db(self) -> Database:
        return self.o.db

    async def bot_row(self, ref: BotRef) -> dict[str, Any]:
        """The full bot row (with the encrypted token) by id or @username."""
        bot_id = as_id(ref)
        if bot_id is not None:
            bot = await self.db.get_bot(bot_id)
        else:
            name = str(ref).strip().lstrip("@").lower()
            bot = next(
                (b for b in await self.db.list_bots() if (b["username"] or "").lower() == name),
                None,
            )
        if not bot:
            raise NotFound("Bot not found")
        return bot

    async def bot_id(self, ref: BotRef) -> int:
        return (await self.bot_row(ref))["id"]

    async def handler_row(self, bot_id: int, ref: NameRef) -> dict[str, Any]:
        handler_id = as_id(ref)
        if handler_id is not None:
            handler = await self.db.get_handler(bot_id, handler_id)
        else:
            handler = await self.db.get_handler_by_name(bot_id, str(ref).strip())
        if not handler:
            raise NotFound("Handler not found")
        return handler

    async def update_row(self, bot_id: int, row_id: int) -> dict[str, Any]:
        row = await self.db.get_update(bot_id, int(row_id))
        if not row:
            raise NotFound("Update not found")
        return row

    async def sample_update(
        self,
        bot_id: int,
        update_row: int | None = None,
        update: dict[str, Any] | str | None = None,
    ) -> tuple[dict[str, Any], dict[str, Any]]:
        """The update for a test run: a logged one by row id, or JSON given by the caller.

        Returns the update and the `origin` passed to the runner.
        """
        if update_row:
            row = await self.update_row(bot_id, update_row)
            return row["raw"], {"update_row": row["id"]}
        if isinstance(update, str) or update is None:
            try:
                update = json.loads(update or "")
            except ValueError:
                raise Invalid("Update JSON is not valid") from None
        if not isinstance(update, dict):
            raise Invalid("Update must be a JSON object")
        return update, {}


def check_syntax(code: str, filename: str) -> None:
    try:
        compile(code, filename, "exec")
    except SyntaxError as exc:
        raise Invalid(f"Not saved: syntax error on line {exc.lineno}: {exc.msg}") from None


def comment_text(comment: str | None) -> str:
    return (comment or "").strip()[:200]
