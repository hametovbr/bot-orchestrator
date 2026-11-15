"""Bot folders: browse, read and delete files handlers saved."""

from __future__ import annotations

import asyncio
import shutil
from pathlib import Path
from typing import Any

from ..errors import Invalid, NotFound
from .base import Base, BotRef

TEXT_LIMIT = 256 * 1024


class Files(Base):
    def folder(self, bot_id: int) -> Path:
        return Path(self.o.settings.files_root) / str(bot_id)

    def inside(self, bot_id: int, relative: str) -> Path:
        """An absolute path inside the bot folder; refuses anything that escapes it."""
        root = self.folder(bot_id).resolve()
        target = (root / (relative or "").lstrip("/")).resolve()
        if not target.is_relative_to(root):
            raise Invalid("Path is outside the bot folder")
        return target

    async def list(self, bot: BotRef, path: str = "") -> dict[str, Any]:
        """Entries of a folder (folders first), breadcrumbs and the size of the bot folder."""
        bot_id = await self.bot_id(bot)
        return await asyncio.to_thread(self._list, bot_id, path)

    def _list(self, bot_id: int, path: str) -> dict[str, Any]:
        folder = self.inside(bot_id, path)
        root = self.folder(bot_id).resolve()
        entries = []
        if folder.is_dir():
            for item in sorted(folder.iterdir(), key=lambda p: (not p.is_dir(), p.name.lower())):
                stat = item.stat()
                entries.append(
                    {
                        "name": item.name,
                        "path": str(item.relative_to(root)),
                        "is_dir": item.is_dir(),
                        "size": stat.st_size,
                        "modified": stat.st_mtime,
                    }
                )
        rel = "" if folder == root else str(folder.relative_to(root))
        crumbs, acc = [], []
        for part in Path(rel).parts:
            acc.append(part)
            crumbs.append({"name": part, "path": "/".join(acc)})
        usage = (
            sum(f.stat().st_size for f in root.rglob("*") if f.is_file()) if root.exists() else 0
        )
        return {
            "root": str(root),
            "path": rel,
            "exists": folder.is_dir(),
            "entries": entries,
            "crumbs": crumbs,
            "usage": usage,
        }

    async def file(self, bot: BotRef, path: str) -> Path:
        """The absolute path of an existing file in the bot folder."""
        target = self.inside(await self.bot_id(bot), path)
        if not target.is_file():
            raise NotFound("File not found")
        return target

    async def read_text(
        self, bot: BotRef, path: str, max_bytes: int = TEXT_LIMIT
    ) -> dict[str, Any]:
        """The start of a text file (UTF-8, invalid bytes replaced)."""
        target = await self.file(bot, path)
        size = target.stat().st_size
        limit = max(1, min(max_bytes, TEXT_LIMIT))
        with target.open("rb") as fh:
            data = await asyncio.to_thread(fh.read, limit)
        if b"\0" in data[:8192]:
            raise Invalid("This looks like a binary file; it can only be downloaded.")
        return {
            "path": path,
            "size": size,
            "truncated": size > limit,
            "text": data.decode("utf-8", errors="replace"),
        }

    async def delete(self, bot: BotRef, path: str) -> None:
        """Delete a file or a folder with everything in it."""
        bot_id = await self.bot_id(bot)
        target = self.inside(bot_id, path)
        if target == self.folder(bot_id).resolve():
            raise Invalid("Cannot delete the bot folder")
        if target.is_dir():
            await asyncio.to_thread(shutil.rmtree, target)
        elif target.exists():
            target.unlink()
        else:
            raise NotFound("File not found")
