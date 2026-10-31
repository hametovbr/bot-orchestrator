"""SQLite storage."""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any

import aiosqlite

SCHEMA = """
CREATE TABLE IF NOT EXISTS bots (
    id              INTEGER PRIMARY KEY,          -- Telegram bot id
    username        TEXT NOT NULL,
    first_name      TEXT NOT NULL DEFAULT '',
    token_enc       TEXT NOT NULL,
    status          TEXT NOT NULL DEFAULT 'paused',  -- running | paused | error
    status_message  TEXT,
    update_offset   INTEGER NOT NULL DEFAULT 0,
    allowed_users   TEXT NOT NULL DEFAULT '',     -- comma-separated Telegram user ids
    active_version  INTEGER,
    created_at      REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS handler_versions (
    bot_id      INTEGER NOT NULL REFERENCES bots(id) ON DELETE CASCADE,
    version     INTEGER NOT NULL,
    code        TEXT NOT NULL,
    comment     TEXT NOT NULL DEFAULT '',
    created_at  REAL NOT NULL,
    PRIMARY KEY (bot_id, version)
);

CREATE TABLE IF NOT EXISTS updates (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    bot_id           INTEGER NOT NULL REFERENCES bots(id) ON DELETE CASCADE,
    update_id        INTEGER NOT NULL,
    received_at      REAL NOT NULL,
    kind             TEXT NOT NULL,
    from_id          INTEGER,
    from_name        TEXT,
    summary          TEXT NOT NULL DEFAULT '',
    raw              TEXT NOT NULL,
    status           TEXT NOT NULL,   -- pending | ok | error | timeout | ignored | no_handler
                                      -- | dispatch_error
    handler_version  INTEGER,
    duration_ms      INTEGER,
    error            TEXT,
    logs             TEXT NOT NULL DEFAULT '[]',
    api_calls        TEXT NOT NULL DEFAULT '[]'
);
CREATE INDEX IF NOT EXISTS updates_bot_time ON updates (bot_id, received_at DESC);

CREATE TABLE IF NOT EXISTS console_history (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    bot_id      INTEGER NOT NULL REFERENCES bots(id) ON DELETE CASCADE,
    method      TEXT NOT NULL,
    params      TEXT NOT NULL,
    response    TEXT NOT NULL,
    ok          INTEGER NOT NULL,
    created_at  REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS secrets (
    bot_id     INTEGER NOT NULL REFERENCES bots(id) ON DELETE CASCADE,
    name       TEXT NOT NULL,
    value_enc  TEXT NOT NULL,
    PRIMARY KEY (bot_id, name)
);

CREATE TABLE IF NOT EXISTS kv (
    bot_id      INTEGER NOT NULL REFERENCES bots(id) ON DELETE CASCADE,
    key         TEXT NOT NULL,
    value       TEXT NOT NULL,
    updated_at  REAL NOT NULL,
    PRIMARY KEY (bot_id, key)
);
"""


def _row(row: aiosqlite.Row | None) -> dict[str, Any] | None:
    return dict(row) if row is not None else None


class Database:
    def __init__(self, path: str):
        self._path = path
        self._db: aiosqlite.Connection | None = None

    async def connect(self) -> None:
        if self._path != ":memory:":
            Path(self._path).parent.mkdir(parents=True, exist_ok=True)
        self._db = await aiosqlite.connect(self._path)
        self._db.row_factory = aiosqlite.Row
        await self._db.execute("PRAGMA journal_mode=WAL")
        await self._db.execute("PRAGMA foreign_keys=ON")
        await self._db.executescript(SCHEMA)
        await self._db.commit()

    async def close(self) -> None:
        if self._db:
            await self._db.close()

    @property
    def db(self) -> aiosqlite.Connection:
        assert self._db, "database is not connected"
        return self._db

    async def _all(self, sql: str, *args: Any) -> list[dict[str, Any]]:
        async with self.db.execute(sql, args) as cursor:
            return [dict(r) for r in await cursor.fetchall()]

    async def _one(self, sql: str, *args: Any) -> dict[str, Any] | None:
        async with self.db.execute(sql, args) as cursor:
            return _row(await cursor.fetchone())

    async def _exec(self, sql: str, *args: Any) -> int:
        cursor = await self.db.execute(sql, args)
        await self.db.commit()
        return cursor.lastrowid or 0

    # Bots

    async def list_bots(self) -> list[dict[str, Any]]:
        return await self._all(
            """
            SELECT b.*,
                   (SELECT COUNT(*) FROM updates u
                     WHERE u.bot_id = b.id AND u.received_at > ?) AS updates_24h,
                   (SELECT COUNT(*) FROM updates u
                     WHERE u.bot_id = b.id AND u.received_at > ?
                       AND u.status IN ('error', 'timeout', 'dispatch_error')) AS errors_24h
              FROM bots b ORDER BY b.username
            """,
            time.time() - 86400,
            time.time() - 86400,
        )

    async def get_bot(self, bot_id: int) -> dict[str, Any] | None:
        return await self._one("SELECT * FROM bots WHERE id = ?", bot_id)

    async def add_bot(self, bot_id: int, username: str, first_name: str, token_enc: str) -> None:
        await self._exec(
            """
            INSERT INTO bots (id, username, first_name, token_enc, status, created_at)
            VALUES (?, ?, ?, ?, 'paused', ?)
            ON CONFLICT(id) DO UPDATE SET username = excluded.username,
                first_name = excluded.first_name, token_enc = excluded.token_enc
            """,
            bot_id,
            username,
            first_name,
            token_enc,
            time.time(),
        )

    async def set_status(self, bot_id: int, status: str, message: str | None = None) -> None:
        await self._exec(
            "UPDATE bots SET status = ?, status_message = ? WHERE id = ?", status, message, bot_id
        )

    async def set_status_message(self, bot_id: int, message: str | None) -> None:
        await self._exec("UPDATE bots SET status_message = ? WHERE id = ?", message, bot_id)

    async def set_offset(self, bot_id: int, offset: int) -> None:
        await self._exec("UPDATE bots SET update_offset = ? WHERE id = ?", offset, bot_id)

    async def set_allowed_users(self, bot_id: int, allowed: str) -> None:
        await self._exec("UPDATE bots SET allowed_users = ? WHERE id = ?", allowed, bot_id)

    async def delete_bot(self, bot_id: int) -> None:
        await self._exec("DELETE FROM bots WHERE id = ?", bot_id)

    # Handler versions

    async def list_versions(self, bot_id: int) -> list[dict[str, Any]]:
        return await self._all(
            "SELECT version, comment, created_at, length(code) AS size FROM handler_versions"
            " WHERE bot_id = ? ORDER BY version DESC",
            bot_id,
        )

    async def get_version(self, bot_id: int, version: int) -> dict[str, Any] | None:
        return await self._one(
            "SELECT * FROM handler_versions WHERE bot_id = ? AND version = ?", bot_id, version
        )

    async def save_version(self, bot_id: int, code: str, comment: str = "") -> int:
        row = await self._one(
            "SELECT COALESCE(MAX(version), 0) + 1 AS next FROM handler_versions WHERE bot_id = ?",
            bot_id,
        )
        version = row["next"] if row else 1
        await self.db.execute(
            "INSERT INTO handler_versions (bot_id, version, code, comment, created_at)"
            " VALUES (?, ?, ?, ?, ?)",
            (bot_id, version, code, comment, time.time()),
        )
        await self.db.execute("UPDATE bots SET active_version = ? WHERE id = ?", (version, bot_id))
        await self.db.commit()
        return version

    async def activate_version(self, bot_id: int, version: int | None) -> None:
        await self._exec("UPDATE bots SET active_version = ? WHERE id = ?", version, bot_id)

    # Update log

    async def add_update(self, bot_id: int, update: dict[str, Any], **fields: Any) -> int:
        return await self._exec(
            """
            INSERT INTO updates (bot_id, update_id, received_at, kind, from_id, from_name,
                                 summary, raw, status)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            bot_id,
            update.get("update_id", 0),
            time.time(),
            fields["kind"],
            fields.get("from_id"),
            fields.get("from_name"),
            fields.get("summary", ""),
            json.dumps(update, ensure_ascii=False),
            fields.get("status", "pending"),
        )

    async def finish_update(self, row_id: int, **fields: Any) -> None:
        await self._exec(
            """
            UPDATE updates SET status = ?, handler_version = ?, duration_ms = ?, error = ?,
                               logs = ?, api_calls = ?
             WHERE id = ?
            """,
            fields["status"],
            fields.get("handler_version"),
            fields.get("duration_ms"),
            fields.get("error"),
            json.dumps(fields.get("logs", []), ensure_ascii=False),
            json.dumps(fields.get("api_calls", []), ensure_ascii=False),
            row_id,
        )

    async def list_updates(
        self, bot_id: int, status: str | None = None, limit: int = 100
    ) -> list[dict[str, Any]]:
        sql = (
            "SELECT id, update_id, received_at, kind, from_id, from_name, summary, status,"
            " handler_version, duration_ms, error FROM updates WHERE bot_id = ?"
        )
        args: list[Any] = [bot_id]
        if status:
            sql += " AND status = ?"
            args.append(status)
        sql += " ORDER BY id DESC LIMIT ?"
        args.append(limit)
        return await self._all(sql, *args)

    async def get_update(self, bot_id: int, row_id: int) -> dict[str, Any] | None:
        row = await self._one("SELECT * FROM updates WHERE bot_id = ? AND id = ?", bot_id, row_id)
        if row:
            row["raw"] = json.loads(row["raw"])
            row["logs"] = json.loads(row["logs"])
            row["api_calls"] = json.loads(row["api_calls"])
        return row

    async def prune_updates(self, older_than: float) -> int:
        cursor = await self.db.execute("DELETE FROM updates WHERE received_at < ?", (older_than,))
        await self.db.commit()
        return cursor.rowcount

    # Console history

    async def add_console_call(
        self, bot_id: int, method: str, params: str, response: str, ok: bool
    ) -> None:
        await self._exec(
            "INSERT INTO console_history (bot_id, method, params, response, ok, created_at)"
            " VALUES (?, ?, ?, ?, ?, ?)",
            bot_id,
            method,
            params,
            response,
            int(ok),
            time.time(),
        )
        # Keep the last 200 calls per bot.
        await self._exec(
            "DELETE FROM console_history WHERE bot_id = ? AND id NOT IN"
            " (SELECT id FROM console_history WHERE bot_id = ? ORDER BY id DESC LIMIT 200)",
            bot_id,
            bot_id,
        )

    async def list_console_calls(self, bot_id: int, limit: int = 50) -> list[dict[str, Any]]:
        return await self._all(
            "SELECT * FROM console_history WHERE bot_id = ? ORDER BY id DESC LIMIT ?",
            bot_id,
            limit,
        )

    # Secrets

    async def list_secret_names(self, bot_id: int) -> list[str]:
        rows = await self._all("SELECT name FROM secrets WHERE bot_id = ? ORDER BY name", bot_id)
        return [r["name"] for r in rows]

    async def get_secrets_enc(self, bot_id: int) -> dict[str, str]:
        rows = await self._all("SELECT name, value_enc FROM secrets WHERE bot_id = ?", bot_id)
        return {r["name"]: r["value_enc"] for r in rows}

    async def set_secret(self, bot_id: int, name: str, value_enc: str) -> None:
        await self._exec(
            "INSERT INTO secrets (bot_id, name, value_enc) VALUES (?, ?, ?)"
            " ON CONFLICT(bot_id, name) DO UPDATE SET value_enc = excluded.value_enc",
            bot_id,
            name,
            value_enc,
        )

    async def delete_secret(self, bot_id: int, name: str) -> None:
        await self._exec("DELETE FROM secrets WHERE bot_id = ? AND name = ?", bot_id, name)

    # Key-value storage

    async def kv_get(self, bot_id: int, key: str) -> Any:
        row = await self._one("SELECT value FROM kv WHERE bot_id = ? AND key = ?", bot_id, key)
        return (True, json.loads(row["value"])) if row else (False, None)

    async def kv_set(self, bot_id: int, key: str, value: Any) -> None:
        await self._exec(
            "INSERT INTO kv (bot_id, key, value, updated_at) VALUES (?, ?, ?, ?)"
            " ON CONFLICT(bot_id, key) DO UPDATE SET value = excluded.value,"
            " updated_at = excluded.updated_at",
            bot_id,
            key,
            json.dumps(value, ensure_ascii=False),
            time.time(),
        )

    async def kv_delete(self, bot_id: int, key: str) -> None:
        await self._exec("DELETE FROM kv WHERE bot_id = ? AND key = ?", bot_id, key)

    async def kv_list(self, bot_id: int) -> list[dict[str, Any]]:
        return await self._all(
            "SELECT key, value, updated_at FROM kv WHERE bot_id = ? ORDER BY key", bot_id
        )
