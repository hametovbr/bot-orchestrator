# Architecture

This document describes the current design. It changes as the project develops.

## Principles

- **A bot is a database record, not a container.** The deployment always
  consists of the same three services, however many bots are configured.
- **One local Bot API server for all bots.** The bot token is part of every
  request URL, so a single `telegram-bot-api` instance serves any number of bots.
- **User code never runs inside the orchestrator.** Handlers and background
  tasks run in `bot-runner`, each in its own process, so a broken or hanging
  handler cannot take down the panel or other bots.
- **The orchestrator owns the state; the runner is stateless.** Routes, handler
  versions, tasks, schedules and logs live in the orchestrator's database. The
  runner can be restarted at any time.
- **Extra libraries and shared code are global.** One package list and one set of
  shared modules serve every bot.
- **Same file paths everywhere.** The local Bot API data directory is mounted
  at the same path in every container, so a path returned by `getFile` can be
  opened directly by handler code.

## Services

```
┌───────────┐  LAN / VPN  ┌──────────────────┐        ┌──────────────────┐  MTProto  ┌──────────┐
│  Browser  │ ──────────► │ bot-orchestrator │ ◄────► │ telegram-bot-api │ ◄───────► │ Telegram │
└───────────┘             └──────────────────┘        └──────────────────┘           └──────────┘
                                   ▲                           │
                                   ▼                           │
                          ┌──────────────────┐                 │
                          │    bot-runner    │                 │
                          └──────────────────┘                 │
                                   │                           │
                          ┌────────▼───────────────────────────▼──┐
                          │ shared volume (bot files, API cache)  │
                          └───────────────────────────────────────┘
```

### bot-orchestrator

- Web UI. Bots, and per bot: updates, routes, handlers, tasks, schedules, API
  console, files, settings. Global settings: notifications and storage.
- Bot lifecycle: validate token (`getMe`), migrate to the local server
  (`deleteWebhook` + `logOut` on `api.telegram.org`), pause, delete, migrate back.
- One `getUpdates` long-polling loop per running bot. Adding a bot starts a new
  loop without restarting any container.
- Routing: drops updates from users outside the bot's allow list, then checks the
  bot's routes in order; the first enabled match runs its handler's active
  version, up to `BOT_CONCURRENCY` at a time per bot.
- Background tasks: a queue in the database, run `TASK_CONCURRENCY` at a time
  across all bots, cancellable from the UI or from handler code.
- Schedules: a five-field cron per schedule in the `TZ` timezone, checked at the
  start of every minute. Runs missed while the orchestrator was down are
  skipped.
- Notifications: failures are sent to one chat through one of the managed bots,
  at most once per 10 minutes for the same problem.
- Shared modules: code every handler can import as `shared.<name>`, with
  versions. The active versions travel with every runner call.
- Packages: the requirements list is stored here; on start and every 10 minutes the
  orchestrator checks that the runner has it installed and asks it to install the
  list if not (for example after its volume was recreated).
- Maintenance, hourly: prunes the update log and finished tasks older than
  `UPDATE_RETENTION_DAYS`, and deletes files the Bot API server downloaded more
  than `CACHE_RETENTION_HOURS` ago. Every 10 minutes it checks disk space.
- Storage: SQLite with versioned schema migrations (`PRAGMA user_version`).

### bot-runner

- Runs every handler call and background task in a fresh Python process with a
  time limit (`HANDLER_TIMEOUT`, `TASK_TIMEOUT`) and a memory limit, so new
  versions need no restart.
- Separate concurrency limits for handlers and tasks, so long downloads never
  block replies.
- Kills a running job on request (`POST /cancel/{run_id}`).
- Provides the handler SDK (`ctx`), see below. The image includes `yt-dlp` and
  `ffmpeg`.
- No Docker socket, CPU/RAM limits on the container, file helpers scoped to the
  bot folder; a shared read-only folder (`SHARED_READ_DIR`, e.g. cookies files).
- Extra packages: installed with `uv pip install --target` into
  `PACKAGES_DIR/envs/<hash>`, constrained to the runner's own dependency versions.
  `PACKAGES_DIR/current` is switched to the new folder only after a successful
  install; workers add it to `sys.path` after the runner's libraries.
- Shared modules: an import hook makes `shared.<name>` load the code sent with the
  job, on first import. Module and handler sources are registered with `linecache`,
  so tracebacks show the failing lines.

### telegram-bot-api

Stock image `aiogram/telegram-bot-api` running with `--local`. It publishes no
ports. All three images run as uid 101, so they can share files on the volumes.

## Routes

A route is a condition and a handler. Updates go through the routes from top to
bottom; the first enabled match wins. Updates that match nothing are logged with
the status `no_route`.

| Condition | Pattern | Matches |
| --- | --- | --- |
| `command` | `start` | Text `/start`, `/start args`, `/start@this_bot` |
| `regex` | `https?://\S+` | `re.search` in the message text or caption |
| `content` | `document, photo` | Messages with any of these fields (`text`, `document`, `photo`, `video`, `audio`, `voice`, ...) |
| `callback` | `cancel:` | Button presses whose `callback_data` starts with the prefix (empty: any button) |
| `update` | `edited_message, schedule` | Update types |
| `any` | — | Everything |

Command routes can be published to the bot menu with `setMyCommands`; the route
description becomes the menu text.

## Handler SDK

A handler is code that defines `handle(update, ctx)`, sync or async. The same
code may define functions to run as background tasks. The complete reference for
handler authors, kept in sync with the code, is the skill
[`skills/bot-orchestrator-handlers`](../skills/bot-orchestrator-handlers/SKILL.md);
this section is a summary.

```python
import re

URL = re.compile(r"https?://\S+")


async def handle(update, ctx):
    found = URL.search(update.message.text or "")
    if found:
        await ctx.background(fetch, found.group(0), title=found.group(0))


async def fetch(ctx, url):
    result = await ctx.download(url)
    await ctx.reply_file(result.path, caption=result.title)
```

| Member | Purpose |
| --- | --- |
| `update` | Telegram fields as attributes, `None` when missing; `message.from_` is the sender; `update.to_dict()` is the raw JSON |
| `update.schedule` | In scheduled runs: `id`, `name`, `cron`, `time`, `manual`, `chat` |
| `await ctx.reply(text, **params)` | `sendMessage` to the chat of the update (or of the schedule) |
| `await ctx.reply_file(path, caption=None, kind="auto")` | `sendVideo`/`sendAudio`/`sendPhoto`/`sendAnimation`/`sendDocument` by extension |
| `await ctx.api.<method>(**params)` | Any Bot API method, snake_case or camelCase; a `Path` value is sent as a local `file://` URI |
| `await ctx.answer_callback(text)` | `answerCallbackQuery` for the current update |
| `ctx.chat_id`, `ctx.bot`, `ctx.handler` | Chat of the update; this bot; the running handler |
| `task_id = await ctx.background(func, *args, title=None, **kwargs)` | Queue `func(ctx, *args, **kwargs)` as a background task; arguments travel as JSON |
| `ctx.task`, `await ctx.progress(text)` | Inside a task: its `id` and `title`; a progress line for the UI |
| `await ctx.tasks.get(id)`, `await ctx.tasks.cancel(id)` | Status of a task of this bot, or cancel it |
| `await ctx.download(url, folder="downloads", ...)` | yt-dlp download with a progress message in the chat; returns `path`, `title`, `size`, `info`; raises `DownloadFailed` |
| `await ctx.files.local_path(file_id)` | `getFile` through the local server, returns the absolute path |
| `ctx.files.move/copy(src, dst)` | Into the bot folder; existing names get a ` (1)` suffix unless `overwrite=True` |
| `ctx.files.write_text/write_bytes/read_text/read_bytes/list/exists/path/readable/safe_name` | File helpers scoped to the bot folder |
| `await ctx.kv.get(key, default)`, `set`, `delete` | Per-bot JSON storage kept by the orchestrator |
| `ctx.secrets[name]` | Secrets from the Settings tab |
| `ctx.log(...)`, `print(...)` | Lines in the log |
| `ctx.http` | `httpx.AsyncClient` |
| `await ctx.shell(*args, timeout=, cwd=)` | Run a program (default cwd: the bot folder) |

`ctx.download` options: `audio_only` (MP3), `format` (yt-dlp format string;
default best video up to 1080p plus audio), `max_size_mb` (default 2000),
`cookies` (a Netscape cookies file, e.g. in the shared folder), `filename`
(yt-dlp output template), `status` (keep a status message in the chat),
`messages` (status texts, e.g. in another language), `options` (extra yt-dlp
options).

## Shared modules

A module is Python code with a name and versions, common to all bots. Handlers and
other modules import it with `from shared import name` or `import shared.name`.

- Saving a module activates the new version for every handler at once. The module
  page lists who imports it (found by parsing the `import` statements of the active
  handler and module code) and can run any handler with the unsaved module code.
- A module that is still imported cannot be deleted. Modules cannot be renamed,
  because that would break imports.
- Background tasks pin the module versions active when they are queued, as they pin
  the handler code; "Run again" uses the same versions.

## Packages

The Packages page holds a `requirements.txt`-style list (package specifications
only; options such as `-e` or `--index-url` are rejected). Both sides compute the
same hash of the normalized list (comments and blank lines removed, first 16 hex
digits of SHA-256) to tell whether the runner has what the orchestrator wants.

## Orchestrator–runner contract

### `POST {RUNNER_URL}/run`

Header `X-Runner-Secret: <RUNNER_SECRET>`.

```json
{
  "run_id": "hex string",
  "kind": "handler | task",
  "bot": {"id": 123, "username": "my_bot", "token": "123:ABC..."},
  "code": "async def handle(update, ctx): ...",
  "version": 3,
  "handler": {"id": 7, "name": "download", "version": 3},
  "entry": "handle",
  "args": [],
  "kwargs": {},
  "task": {"id": 12, "title": "https://..."},
  "origin": {"update_row": 345},
  "modules": {"downloads": {"version": 4, "code": "def fetch(...): ..."}},
  "update": {"update_id": 1, "message": {}},
  "secrets": {"NAME": "value"},
  "dry_run": false,
  "timeout": null
}
```

`kind: "handler"` calls `handle(update, ctx)`; `kind: "task"` calls
`entry(ctx, *args, **kwargs)` with `ctx.task` set. `origin` is passed back
unchanged when the run starts a task. `handler.version` is 0 for unsaved code
from a test run; a module `version` of 0 is a draft from the module page.

Response:

```json
{
  "status": "ok | error | timeout | cancelled",
  "error": "traceback or null",
  "logs": ["line"],
  "api_calls": [{"method": "sendMessage", "params": {}, "ok": true, "dry_run": false}],
  "duration_ms": 42
}
```

Tokens are masked in `error`, `logs` and `api_calls`. Logs are capped at 500
lines and API calls at 200 per run. `ctx.background` appears in `api_calls` as
the pseudo-method `background` with the new `task_id`.

### `POST {RUNNER_URL}/cancel/{run_id}`

Kills the process of a running job; its `/run` call then returns `cancelled`.
Answers `{"cancelled": false}` if no such job is running.

### `GET {RUNNER_URL}/packages`

```json
{
  "hash": "217427db06ee0d6c",
  "requirements": "pillow\nopenpyxl",
  "installed": [{"name": "pillow", "version": "11.0.0"}],
  "installing": false,
  "target_hash": "217427db06ee0d6c",
  "error": null,
  "log": ["..."],
  "finished_at": 1762020000.0
}
```

### `POST {RUNNER_URL}/packages` with `{"requirements": "..."}`

Starts an install in the background and answers 202 with the status above; 400 for
options in the list, 409 while another install runs.

### Internal API of the orchestrator

Used by the SDK; requires the same `X-Runner-Secret` header.

| Request | Effect |
| --- | --- |
| `GET /internal/kv/{bot_id}/{key}` | `{"value": ...}` or 404 |
| `PUT /internal/kv/{bot_id}/{key}` with `{"value": ...}` | Store a JSON value |
| `DELETE /internal/kv/{bot_id}/{key}` | Remove a key |
| `POST /internal/tasks` with `bot_id`, `handler`, `code`, `entry`, `args`, `kwargs`, `title`, `update`, `origin` | Queue a task: `{"id": 12}` |
| `GET /internal/tasks/{id}?bot_id=` | `id`, `title`, `status`, `progress`, `error`, `entry` |
| `POST /internal/tasks/{id}/cancel` with `bot_id` | `{"cancelled": true/false}` |
| `POST /internal/tasks/{id}/progress` with `bot_id`, `text` | Progress line of a running task |

## Data model

Schema version 3 (`PRAGMA user_version`). Older databases are migrated on start,
step by step: from 0.1.0 the MVP handler becomes a handler called `main` behind a
catch-all route, enabled if the MVP handler was active; from 0.2.0 the module
tables are added.

| Table | Contents |
| --- | --- |
| `bots` | Encrypted token, username, status, update offset, allowed users |
| `handlers` | Name and active version per bot |
| `handler_versions` | Code and comment of every saved version |
| `routes` | Position, condition, pattern, description, handler, enabled |
| `updates` | Raw update, sender, summary, route, handler and version, status, duration, error, logs, API calls |
| `tasks` | Code snapshot, pinned module versions, entry, arguments, origin update, status, progress, run id, timings, error, logs, API calls |
| `modules`, `module_versions` | Shared modules: name, active version; code and comment of every version |
| `schedules` | Name, cron, handler, chat, enabled, next and last run |
| `console_history` | Last 200 API console calls per bot |
| `secrets` | Encrypted secret values |
| `kv` | JSON values stored by handlers |
| `app_settings` | Notification settings, the package list |

Update statuses: `pending`, `ok`, `error`, `timeout`, `dispatch_error` (the runner
could not be reached), `no_route`, `ignored` (sender not in allowed users).

Task statuses: `queued`, `running`, `ok`, `error`, `timeout`, `cancelled`,
`interrupted` (the orchestrator restarted while it ran), `dispatch_error`.

## Roadmap

1. **MVP (0.1, done):** add bot with cloud logOut, polling, update log, API
   console, one handler per bot with an editor, versions and test runs,
   `ctx.reply/api/files/kv/secrets/shell`, file browser, allowed users.
2. **Stage 2 (0.2, done):** routes and multiple handlers per bot, background
   tasks with cancel and retry, `ctx.download` (yt-dlp) and `ctx.reply_file`,
   Bot API file cache cleanup, schedules, notifications in Telegram, editor
   suggestions and handler templates.
3. **Stage 3 (0.3, in progress):** extra Python packages for handlers and shared
   modules (done); next: export to Git and consistent SQLite backups, sign-in
   through the reverse proxy, simple charts.
4. **Later, maybe:** user-bots (a Telegram user account driven by code) for
   conversations with third-party bots; an AI assistant in the editor.
   Webhook mode is not planned: long polling against the local server is enough.
