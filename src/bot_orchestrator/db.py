"""SQLite storage with schema migrations."""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any

import aiosqlite

SCHEMA_VERSION = 4

# Tables shared by every schema version.
BASE_SCHEMA = """
CREATE TABLE IF NOT EXISTS bots (
    id              INTEGER PRIMARY KEY,          -- Telegram bot id
    username        TEXT NOT NULL,
    first_name      TEXT NOT NULL DEFAULT '',
    token_enc       TEXT NOT NULL,
    status          TEXT NOT NULL DEFAULT 'paused',  -- running | paused | error
    status_message  TEXT,
    update_offset   INTEGER NOT NULL DEFAULT 0,
    allowed_users   TEXT NOT NULL DEFAULT '',     -- comma-separated Telegram user ids
    created_at      REAL NOT NULL
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
    status           TEXT NOT NULL,
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

# Added in version 2: several handlers per bot, routes, background tasks, schedules.
V2_TABLES = """
CREATE TABLE handlers (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    bot_id          INTEGER NOT NULL REFERENCES bots(id) ON DELETE CASCADE,
    name            TEXT NOT NULL,
    active_version  INTEGER,
    created_at      REAL NOT NULL,
    UNIQUE (bot_id, name)
);

CREATE TABLE handler_versions (
    handler_id  INTEGER NOT NULL REFERENCES handlers(id) ON DELETE CASCADE,
    version     INTEGER NOT NULL,
    code        TEXT NOT NULL,
    comment     TEXT NOT NULL DEFAULT '',
    created_at  REAL NOT NULL,
    PRIMARY KEY (handler_id, version)
);

CREATE TABLE routes (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    bot_id       INTEGER NOT NULL REFERENCES bots(id) ON DELETE CASCADE,
    position     INTEGER NOT NULL,
    kind         TEXT NOT NULL,   -- command | regex | content | callback | update | any
    pattern      TEXT NOT NULL DEFAULT '',
    description  TEXT NOT NULL DEFAULT '',
    handler_id   INTEGER NOT NULL REFERENCES handlers(id) ON DELETE CASCADE,
    enabled      INTEGER NOT NULL DEFAULT 1
);
CREATE INDEX routes_bot ON routes (bot_id, position);

CREATE TABLE tasks (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    bot_id        INTEGER NOT NULL REFERENCES bots(id) ON DELETE CASCADE,
    handler_id    INTEGER,
    handler_name  TEXT,
    version       INTEGER NOT NULL DEFAULT 0,
    code          TEXT NOT NULL,
    entry         TEXT NOT NULL,
    args          TEXT NOT NULL DEFAULT '[]',
    kwargs        TEXT NOT NULL DEFAULT '{}',
    title         TEXT NOT NULL,
    origin_update TEXT NOT NULL,
    update_row    INTEGER,          -- updates.id of the update that started the task
    status        TEXT NOT NULL,    -- queued | running | ok | error | timeout | cancelled
                                    -- | interrupted | dispatch_error
    progress      TEXT,
    run_id        TEXT,
    created_at    REAL NOT NULL,
    started_at    REAL,
    finished_at   REAL,
    duration_ms   INTEGER,
    error         TEXT,
    logs          TEXT NOT NULL DEFAULT '[]',
    api_calls     TEXT NOT NULL DEFAULT '[]'
);
CREATE INDEX tasks_bot ON tasks (bot_id, id DESC);
CREATE INDEX tasks_status ON tasks (status, id);

CREATE TABLE schedules (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    bot_id           INTEGER NOT NULL REFERENCES bots(id) ON DELETE CASCADE,
    name             TEXT NOT NULL,
    cron             TEXT NOT NULL,
    handler_id       INTEGER NOT NULL REFERENCES handlers(id) ON DELETE CASCADE,
    chat_id          INTEGER,
    enabled          INTEGER NOT NULL DEFAULT 1,
    next_run_at      REAL,
    last_run_at      REAL,
    last_status      TEXT,
    last_update_row  INTEGER,
    created_at       REAL NOT NULL
);

CREATE TABLE app_settings (
    key    TEXT PRIMARY KEY,
    value  TEXT NOT NULL
);
"""

V2_UPDATE_COLUMNS = """
ALTER TABLE updates ADD COLUMN handler_id INTEGER;
ALTER TABLE updates ADD COLUMN handler_name TEXT;
ALTER TABLE updates ADD COLUMN route_id INTEGER;
"""

# From the MVP schema (version 1): one handler per bot. Its versions become a handler
# called "main" behind a catch-all route that is enabled if the MVP handler was active.
MIGRATE_1_TO_2 = (
    "ALTER TABLE handler_versions RENAME TO handler_versions_v1;\n"
    + V2_TABLES
    + V2_UPDATE_COLUMNS
    + """
INSERT INTO handlers (bot_id, name, active_version, created_at)
    SELECT v.bot_id, 'main',
           COALESCE(b.active_version, MAX(v.version)), MIN(v.created_at)
      FROM handler_versions_v1 v JOIN bots b ON b.id = v.bot_id
     GROUP BY v.bot_id;
INSERT INTO handler_versions (handler_id, version, code, comment, created_at)
    SELECT h.id, v.version, v.code, v.comment, v.created_at
      FROM handler_versions_v1 v JOIN handlers h ON h.bot_id = v.bot_id AND h.name = 'main';
INSERT INTO routes (bot_id, position, kind, pattern, description, handler_id, enabled)
    SELECT h.bot_id, 1, 'any', '', '', h.id,
           CASE WHEN b.active_version IS NULL THEN 0 ELSE 1 END
      FROM handlers h JOIN bots b ON b.id = h.bot_id;
UPDATE updates SET handler_name = 'main' WHERE handler_version IS NOT NULL;
UPDATE updates SET status = 'no_route' WHERE status = 'no_handler';
DROP TABLE handler_versions_v1;
"""
)


# Added in version 3: shared modules, importable by every handler as shared.<name>.
MIGRATE_2_TO_3 = """
CREATE TABLE modules (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    name            TEXT NOT NULL UNIQUE,
    active_version  INTEGER,
    created_at      REAL NOT NULL
);

CREATE TABLE module_versions (
    module_id   INTEGER NOT NULL REFERENCES modules(id) ON DELETE CASCADE,
    version     INTEGER NOT NULL,
    code        TEXT NOT NULL,
    comment     TEXT NOT NULL DEFAULT '',
    created_at  REAL NOT NULL,
    PRIMARY KEY (module_id, version)
);

-- {module name: version} active when the task was queued.
ALTER TABLE tasks ADD COLUMN modules TEXT NOT NULL DEFAULT '{}';
"""

# Version 4: the built-in /purge command, off by default.
MIGRATE_3_TO_4 = """
ALTER TABLE bots ADD COLUMN purge_command INTEGER NOT NULL DEFAULT 0;
"""


def _row(row: aiosqlite.Row | None) -> dict[str, Any] | None:
    return dict(row) if row is not None else None


def _decode(row: dict[str, Any] | None, *fields: str) -> dict[str, Any] | None:
    if row:
        for name in fields:
            if row.get(name) is not None:
                row[name] = json.loads(row[name])
    return row


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False)


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
        await self._migrate()
        await self._db.execute("PRAGMA foreign_keys=ON")

    async def _migrate(self) -> None:
        async with self.db.execute("PRAGMA user_version") as cursor:
            version = (await cursor.fetchone())[0]
        async with self.db.execute(
            "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'bots'"
        ) as cursor:
            has_tables = await cursor.fetchone() is not None
        if version >= SCHEMA_VERSION:
            return
        if not has_tables:
            script = BASE_SCHEMA + V2_TABLES + V2_UPDATE_COLUMNS + MIGRATE_2_TO_3 + MIGRATE_3_TO_4
        elif version in (0, 1):
            script = MIGRATE_1_TO_2 + MIGRATE_2_TO_3 + MIGRATE_3_TO_4
        elif version == 2:
            script = MIGRATE_2_TO_3 + MIGRATE_3_TO_4
        elif version == 3:
            script = MIGRATE_3_TO_4
        else:
            raise RuntimeError(f"unknown database schema version {version}")
        # One transaction: a failed migration leaves the database untouched.
        await self.db.executescript(
            f"BEGIN;\n{script}\nPRAGMA user_version = {SCHEMA_VERSION};\nCOMMIT;"
        )

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

    async def _change(self, sql: str, *args: Any) -> int:
        """Run an UPDATE/DELETE and return the number of changed rows."""
        cursor = await self.db.execute(sql, args)
        await self.db.commit()
        return cursor.rowcount

    # Bots

    async def list_bots(self) -> list[dict[str, Any]]:
        since = time.time() - 86400
        return await self._all(
            """
            SELECT b.*,
                   (SELECT COUNT(*) FROM updates u
                     WHERE u.bot_id = b.id AND u.received_at > ?) AS updates_24h,
                   (SELECT COUNT(*) FROM updates u
                     WHERE u.bot_id = b.id AND u.received_at > ?
                       AND u.status IN ('error', 'timeout', 'dispatch_error')) AS errors_24h,
                   (SELECT COUNT(*) FROM tasks t
                     WHERE t.bot_id = b.id AND t.status IN ('queued', 'running')) AS tasks_active,
                   (SELECT COUNT(*) FROM routes r
                     WHERE r.bot_id = b.id AND r.enabled = 1) AS routes_enabled
              FROM bots b ORDER BY b.username
            """,
            since,
            since,
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

    async def set_purge_command(self, bot_id: int, enabled: bool) -> None:
        await self._exec("UPDATE bots SET purge_command = ? WHERE id = ?", int(enabled), bot_id)

    async def chat_messages(self, bot_id: int, chat_id: int) -> list[dict[str, Any]]:
        """Incoming messages of a chat still in the update log: message_id, date, text."""
        return await self._all(
            """
            SELECT json_extract(raw, '$.message.message_id') AS message_id,
                   json_extract(raw, '$.message.date') AS date,
                   json_extract(raw, '$.message.text') AS text
              FROM updates
             WHERE bot_id = ? AND kind = 'message'
               AND json_extract(raw, '$.message.chat.id') = ?
            """,
            bot_id,
            chat_id,
        )

    async def delete_bot(self, bot_id: int) -> None:
        await self._exec("DELETE FROM bots WHERE id = ?", bot_id)

    # Handlers

    async def list_handlers(self, bot_id: int) -> list[dict[str, Any]]:
        return await self._all(
            """
            SELECT h.*,
                   (SELECT COUNT(*) FROM routes r WHERE r.handler_id = h.id) AS routes,
                   (SELECT COUNT(*) FROM schedules s WHERE s.handler_id = h.id) AS schedules,
                   (SELECT MAX(version) FROM handler_versions v
                     WHERE v.handler_id = h.id) AS latest_version,
                   (SELECT MAX(created_at) FROM handler_versions v
                     WHERE v.handler_id = h.id) AS updated_at
              FROM handlers h WHERE h.bot_id = ? ORDER BY h.name
            """,
            bot_id,
        )

    async def get_handler(self, bot_id: int, handler_id: int) -> dict[str, Any] | None:
        return await self._one(
            "SELECT * FROM handlers WHERE bot_id = ? AND id = ?", bot_id, handler_id
        )

    async def get_handler_by_name(self, bot_id: int, name: str) -> dict[str, Any] | None:
        return await self._one("SELECT * FROM handlers WHERE bot_id = ? AND name = ?", bot_id, name)

    async def create_handler(self, bot_id: int, name: str, code: str, comment: str = "") -> int:
        cursor = await self.db.execute(
            "INSERT INTO handlers (bot_id, name, active_version, created_at) VALUES (?, ?, 1, ?)",
            (bot_id, name, time.time()),
        )
        handler_id = cursor.lastrowid
        await self.db.execute(
            "INSERT INTO handler_versions (handler_id, version, code, comment, created_at)"
            " VALUES (?, 1, ?, ?, ?)",
            (handler_id, code, comment, time.time()),
        )
        await self.db.commit()
        return handler_id

    async def rename_handler(self, handler_id: int, name: str) -> None:
        await self._exec("UPDATE handlers SET name = ? WHERE id = ?", name, handler_id)

    async def delete_handler(self, handler_id: int) -> None:
        await self._exec("DELETE FROM handlers WHERE id = ?", handler_id)

    async def list_versions(self, handler_id: int) -> list[dict[str, Any]]:
        return await self._all(
            "SELECT version, comment, created_at, length(code) AS size FROM handler_versions"
            " WHERE handler_id = ? ORDER BY version DESC",
            handler_id,
        )

    async def get_version(self, handler_id: int, version: int) -> dict[str, Any] | None:
        return await self._one(
            "SELECT * FROM handler_versions WHERE handler_id = ? AND version = ?",
            handler_id,
            version,
        )

    async def active_code(self, handler_id: int) -> dict[str, Any] | None:
        """The active version of a handler: version, code, handler name."""
        return await self._one(
            """
            SELECT v.version, v.code, h.name, h.id AS handler_id
              FROM handlers h JOIN handler_versions v
                ON v.handler_id = h.id AND v.version = h.active_version
             WHERE h.id = ?
            """,
            handler_id,
        )

    async def save_version(self, handler_id: int, code: str, comment: str = "") -> int:
        row = await self._one(
            "SELECT COALESCE(MAX(version), 0) + 1 AS next FROM handler_versions"
            " WHERE handler_id = ?",
            handler_id,
        )
        version = row["next"] if row else 1
        await self.db.execute(
            "INSERT INTO handler_versions (handler_id, version, code, comment, created_at)"
            " VALUES (?, ?, ?, ?, ?)",
            (handler_id, version, code, comment, time.time()),
        )
        await self.db.execute(
            "UPDATE handlers SET active_version = ? WHERE id = ?", (version, handler_id)
        )
        await self.db.commit()
        return version

    async def activate_version(self, handler_id: int, version: int) -> None:
        await self._exec("UPDATE handlers SET active_version = ? WHERE id = ?", version, handler_id)

    # Routes

    async def list_routes(self, bot_id: int) -> list[dict[str, Any]]:
        return await self._all(
            """
            SELECT r.*, h.name AS handler_name
              FROM routes r JOIN handlers h ON h.id = r.handler_id
             WHERE r.bot_id = ? ORDER BY r.position, r.id
            """,
            bot_id,
        )

    async def get_route(self, bot_id: int, route_id: int) -> dict[str, Any] | None:
        return await self._one("SELECT * FROM routes WHERE bot_id = ? AND id = ?", bot_id, route_id)

    async def create_route(
        self,
        bot_id: int,
        kind: str,
        pattern: str,
        description: str,
        handler_id: int,
        enabled: bool = True,
    ) -> int:
        row = await self._one(
            "SELECT COALESCE(MAX(position), 0) + 1 AS next FROM routes WHERE bot_id = ?", bot_id
        )
        return await self._exec(
            "INSERT INTO routes (bot_id, position, kind, pattern, description, handler_id,"
            " enabled) VALUES (?, ?, ?, ?, ?, ?, ?)",
            bot_id,
            row["next"] if row else 1,
            kind,
            pattern,
            description,
            handler_id,
            int(enabled),
        )

    async def update_route(
        self,
        route_id: int,
        kind: str,
        pattern: str,
        description: str,
        handler_id: int,
        enabled: bool,
    ) -> None:
        await self._exec(
            "UPDATE routes SET kind = ?, pattern = ?, description = ?, handler_id = ?,"
            " enabled = ? WHERE id = ?",
            kind,
            pattern,
            description,
            handler_id,
            int(enabled),
            route_id,
        )

    async def set_route_enabled(self, route_id: int, enabled: bool) -> None:
        await self._exec("UPDATE routes SET enabled = ? WHERE id = ?", int(enabled), route_id)

    async def delete_route(self, route_id: int) -> None:
        await self._exec("DELETE FROM routes WHERE id = ?", route_id)

    async def reorder_routes(self, bot_id: int, route_ids: list[int]) -> None:
        """Set positions in the given order; routes not listed keep their relative order last."""
        current = [r["id"] for r in await self.list_routes(bot_id)]
        ordered = [i for i in route_ids if i in current]
        ordered += [i for i in current if i not in ordered]
        for position, route_id in enumerate(ordered, start=1):
            await self.db.execute(
                "UPDATE routes SET position = ? WHERE id = ? AND bot_id = ?",
                (position, route_id, bot_id),
            )
        await self.db.commit()

    # Update log

    async def add_update(self, bot_id: int, update: dict[str, Any], **fields: Any) -> int:
        return await self._exec(
            """
            INSERT INTO updates (bot_id, update_id, received_at, kind, from_id, from_name,
                                 summary, raw, status, handler_id, handler_name, route_id)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            bot_id,
            update.get("update_id", 0),
            time.time(),
            fields["kind"],
            fields.get("from_id"),
            fields.get("from_name"),
            fields.get("summary", ""),
            _json(update),
            fields.get("status", "pending"),
            fields.get("handler_id"),
            fields.get("handler_name"),
            fields.get("route_id"),
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
            _json(fields.get("logs", [])),
            _json(fields.get("api_calls", [])),
            row_id,
        )

    async def list_updates(
        self, bot_id: int, status: str | None = None, limit: int = 100
    ) -> list[dict[str, Any]]:
        sql = (
            "SELECT id, update_id, received_at, kind, from_id, from_name, summary, status,"
            " handler_version, handler_name, route_id, duration_ms, error"
            " FROM updates WHERE bot_id = ?"
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
        return _decode(row, "raw", "logs", "api_calls")

    async def recent_senders(
        self, limit: int = 10, bot_id: int | None = None
    ) -> list[dict[str, Any]]:
        """People who wrote to any bot (or to one bot) recently."""
        return await self._all(
            """
            SELECT from_id, MAX(from_name) AS from_name, MAX(received_at) AS last_seen
              FROM updates WHERE from_id IS NOT NULL AND kind != 'schedule'
               AND (? IS NULL OR bot_id = ?)
             GROUP BY from_id ORDER BY last_seen DESC LIMIT ?
            """,
            bot_id,
            bot_id,
            limit,
        )

    async def prune(self, older_than: float) -> int:
        """Remove old update log entries and finished tasks."""
        removed = await self._change("DELETE FROM updates WHERE received_at < ?", older_than)
        removed += await self._change(
            "DELETE FROM tasks WHERE created_at < ? AND status NOT IN ('queued', 'running')",
            older_than,
        )
        return removed

    # Background tasks

    async def create_task(self, bot_id: int, **fields: Any) -> int:
        return await self._exec(
            """
            INSERT INTO tasks (bot_id, handler_id, handler_name, version, code, entry, args,
                               kwargs, title, origin_update, update_row, modules, status,
                               created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'queued', ?)
            """,
            bot_id,
            fields.get("handler_id"),
            fields.get("handler_name"),
            fields.get("version", 0),
            fields["code"],
            fields["entry"],
            _json(fields.get("args", [])),
            _json(fields.get("kwargs", {})),
            fields["title"],
            _json(fields["origin_update"]),
            fields.get("update_row"),
            _json(fields.get("modules", {})),
            time.time(),
        )

    async def get_task(self, task_id: int, bot_id: int | None = None) -> dict[str, Any] | None:
        row = await self._one("SELECT * FROM tasks WHERE id = ?", task_id)
        if row and bot_id is not None and row["bot_id"] != bot_id:
            return None
        return _decode(row, "args", "kwargs", "origin_update", "logs", "api_calls", "modules")

    async def list_tasks(
        self, bot_id: int, status: str | None = None, limit: int = 100
    ) -> list[dict[str, Any]]:
        sql = (
            "SELECT id, handler_name, version, entry, title, status, progress, created_at,"
            " started_at, finished_at, duration_ms, error, update_row FROM tasks WHERE bot_id = ?"
        )
        args: list[Any] = [bot_id]
        if status:
            sql += " AND status = ?"
            args.append(status)
        sql += " ORDER BY id DESC LIMIT ?"
        args.append(limit)
        return await self._all(sql, *args)

    async def queued_tasks(self, limit: int) -> list[dict[str, Any]]:
        return await self._all(
            "SELECT id FROM tasks WHERE status = 'queued' ORDER BY id LIMIT ?", limit
        )

    async def start_task(self, task_id: int, run_id: str) -> bool:
        """Mark a queued task as running. False if it is no longer queued (e.g. cancelled)."""
        changed = await self._change(
            "UPDATE tasks SET status = 'running', run_id = ?, started_at = ?"
            " WHERE id = ? AND status = 'queued'",
            run_id,
            time.time(),
            task_id,
        )
        return changed == 1

    async def finish_task(self, task_id: int, **fields: Any) -> None:
        await self._exec(
            """
            UPDATE tasks SET status = ?, finished_at = ?, duration_ms = ?, error = ?,
                             logs = ?, api_calls = ?
             WHERE id = ?
            """,
            fields["status"],
            time.time(),
            fields.get("duration_ms"),
            fields.get("error"),
            _json(fields.get("logs", [])),
            _json(fields.get("api_calls", [])),
            task_id,
        )

    async def set_task_progress(self, task_id: int, text: str) -> None:
        await self._exec(
            "UPDATE tasks SET progress = ? WHERE id = ? AND status = 'running'", text, task_id
        )

    async def cancel_queued_task(self, task_id: int) -> bool:
        changed = await self._change(
            "UPDATE tasks SET status = 'cancelled', finished_at = ?, error = 'cancelled'"
            " WHERE id = ? AND status = 'queued'",
            time.time(),
            task_id,
        )
        return changed == 1

    async def interrupt_running_tasks(self) -> int:
        """After a restart, tasks that were running have lost their runner call."""
        return await self._change(
            "UPDATE tasks SET status = 'interrupted', finished_at = ?,"
            " error = 'the orchestrator restarted while the task was running'"
            " WHERE status = 'running'",
            time.time(),
        )

    # Schedules

    async def list_schedules(self, bot_id: int) -> list[dict[str, Any]]:
        return await self._all(
            """
            SELECT s.*, h.name AS handler_name
              FROM schedules s JOIN handlers h ON h.id = s.handler_id
             WHERE s.bot_id = ? ORDER BY s.name
            """,
            bot_id,
        )

    async def get_schedule(self, bot_id: int, schedule_id: int) -> dict[str, Any] | None:
        return await self._one(
            "SELECT * FROM schedules WHERE bot_id = ? AND id = ?", bot_id, schedule_id
        )

    async def due_schedules(self, now: float) -> list[dict[str, Any]]:
        return await self._all(
            """
            SELECT s.* FROM schedules s JOIN bots b ON b.id = s.bot_id
             WHERE s.enabled = 1 AND s.next_run_at IS NOT NULL AND s.next_run_at <= ?
               AND b.status = 'running'
             ORDER BY s.next_run_at
            """,
            now,
        )

    async def all_schedules(self) -> list[dict[str, Any]]:
        return await self._all("SELECT * FROM schedules")

    async def create_schedule(self, bot_id: int, **fields: Any) -> int:
        return await self._exec(
            """
            INSERT INTO schedules (bot_id, name, cron, handler_id, chat_id, enabled,
                                   next_run_at, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            bot_id,
            fields["name"],
            fields["cron"],
            fields["handler_id"],
            fields.get("chat_id"),
            int(fields.get("enabled", True)),
            fields.get("next_run_at"),
            time.time(),
        )

    async def update_schedule(self, schedule_id: int, **fields: Any) -> None:
        await self._exec(
            """
            UPDATE schedules SET name = ?, cron = ?, handler_id = ?, chat_id = ?, enabled = ?,
                                 next_run_at = ?
             WHERE id = ?
            """,
            fields["name"],
            fields["cron"],
            fields["handler_id"],
            fields.get("chat_id"),
            int(fields.get("enabled", True)),
            fields.get("next_run_at"),
            schedule_id,
        )

    async def set_schedule_next(self, schedule_id: int, next_run_at: float | None) -> None:
        await self._exec(
            "UPDATE schedules SET next_run_at = ? WHERE id = ?", next_run_at, schedule_id
        )

    async def record_schedule_run(
        self, schedule_id: int, status: str, update_row: int | None
    ) -> None:
        await self._exec(
            "UPDATE schedules SET last_run_at = ?, last_status = ?, last_update_row = ?"
            " WHERE id = ?",
            time.time(),
            status,
            update_row,
            schedule_id,
        )

    async def delete_schedule(self, schedule_id: int) -> None:
        await self._exec("DELETE FROM schedules WHERE id = ?", schedule_id)

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

    async def kv_get(self, bot_id: int, key: str) -> tuple[bool, Any]:
        row = await self._one("SELECT value FROM kv WHERE bot_id = ? AND key = ?", bot_id, key)
        return (True, json.loads(row["value"])) if row else (False, None)

    async def kv_set(self, bot_id: int, key: str, value: Any) -> None:
        await self._exec(
            "INSERT INTO kv (bot_id, key, value, updated_at) VALUES (?, ?, ?, ?)"
            " ON CONFLICT(bot_id, key) DO UPDATE SET value = excluded.value,"
            " updated_at = excluded.updated_at",
            bot_id,
            key,
            _json(value),
            time.time(),
        )

    async def kv_delete(self, bot_id: int, key: str) -> None:
        await self._exec("DELETE FROM kv WHERE bot_id = ? AND key = ?", bot_id, key)

    async def kv_list(self, bot_id: int) -> list[dict[str, Any]]:
        return await self._all(
            "SELECT key, value, updated_at FROM kv WHERE bot_id = ? ORDER BY key", bot_id
        )

    # Shared modules

    async def list_modules(self) -> list[dict[str, Any]]:
        return await self._all(
            """
            SELECT m.*,
                   (SELECT MAX(version) FROM module_versions v
                     WHERE v.module_id = m.id) AS latest_version,
                   (SELECT MAX(created_at) FROM module_versions v
                     WHERE v.module_id = m.id) AS updated_at
              FROM modules m ORDER BY m.name
            """
        )

    async def get_module(self, module_id: int) -> dict[str, Any] | None:
        return await self._one("SELECT * FROM modules WHERE id = ?", module_id)

    async def get_module_by_name(self, name: str) -> dict[str, Any] | None:
        return await self._one("SELECT * FROM modules WHERE name = ?", name)

    async def create_module(self, name: str, code: str, comment: str = "") -> int:
        cursor = await self.db.execute(
            "INSERT INTO modules (name, active_version, created_at) VALUES (?, 1, ?)",
            (name, time.time()),
        )
        module_id = cursor.lastrowid
        await self.db.execute(
            "INSERT INTO module_versions (module_id, version, code, comment, created_at)"
            " VALUES (?, 1, ?, ?, ?)",
            (module_id, code, comment, time.time()),
        )
        await self.db.commit()
        return module_id

    async def save_module_version(self, module_id: int, code: str, comment: str = "") -> int:
        row = await self._one(
            "SELECT COALESCE(MAX(version), 0) + 1 AS next FROM module_versions WHERE module_id = ?",
            module_id,
        )
        version = row["next"] if row else 1
        await self.db.execute(
            "INSERT INTO module_versions (module_id, version, code, comment, created_at)"
            " VALUES (?, ?, ?, ?, ?)",
            (module_id, version, code, comment, time.time()),
        )
        await self.db.execute(
            "UPDATE modules SET active_version = ? WHERE id = ?", (version, module_id)
        )
        await self.db.commit()
        return version

    async def activate_module_version(self, module_id: int, version: int) -> None:
        await self._exec("UPDATE modules SET active_version = ? WHERE id = ?", version, module_id)

    async def list_module_versions(self, module_id: int) -> list[dict[str, Any]]:
        return await self._all(
            "SELECT version, comment, created_at, length(code) AS size FROM module_versions"
            " WHERE module_id = ? ORDER BY version DESC",
            module_id,
        )

    async def get_module_version(self, module_id: int, version: int) -> dict[str, Any] | None:
        return await self._one(
            "SELECT * FROM module_versions WHERE module_id = ? AND version = ?",
            module_id,
            version,
        )

    async def delete_module(self, module_id: int) -> None:
        await self._exec("DELETE FROM modules WHERE id = ?", module_id)

    async def active_modules(self) -> dict[str, dict[str, Any]]:
        """{name: {"version": n, "code": "..."}} for the active version of every module."""
        rows = await self._all(
            """
            SELECT m.name, v.version, v.code
              FROM modules m JOIN module_versions v
                ON v.module_id = m.id AND v.version = m.active_version
            """
        )
        return {r["name"]: {"version": r["version"], "code": r["code"]} for r in rows}

    async def modules_at(self, versions: dict[str, int]) -> dict[str, dict[str, Any]]:
        """Code of the given module versions; modules deleted since are left out."""
        found = {}
        for name, version in versions.items():
            row = await self._one(
                """
                SELECT v.version, v.code FROM modules m JOIN module_versions v
                    ON v.module_id = m.id
                 WHERE m.name = ? AND v.version = ?
                """,
                name,
                int(version),
            )
            if row:
                found[name] = {"version": row["version"], "code": row["code"]}
        return found

    async def active_handler_code(self) -> list[dict[str, Any]]:
        """Active code of every handler of every bot, for finding module imports."""
        return await self._all(
            """
            SELECT h.id AS handler_id, h.name AS handler_name, b.id AS bot_id,
                   b.username AS bot_username, v.code
              FROM handlers h
              JOIN bots b ON b.id = h.bot_id
              JOIN handler_versions v ON v.handler_id = h.id AND v.version = h.active_version
             ORDER BY b.username, h.name
            """
        )

    # Application settings

    async def get_setting(self, key: str, default: Any = None) -> Any:
        row = await self._one("SELECT value FROM app_settings WHERE key = ?", key)
        return json.loads(row["value"]) if row else default

    async def set_setting(self, key: str, value: Any) -> None:
        await self._exec(
            "INSERT INTO app_settings (key, value) VALUES (?, ?)"
            " ON CONFLICT(key) DO UPDATE SET value = excluded.value",
            key,
            _json(value),
        )
