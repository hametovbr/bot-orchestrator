# Architecture

This document describes the target design. It will change as the project
develops.

## Principles

- **A bot is a database record, not a container.** The deployment always
  consists of the same three services, however many bots are configured.
- **One local Bot API server for all bots.** The bot token is part of every
  request URL, so a single `telegram-bot-api` instance serves any number of bots.
- **User code never runs inside the orchestrator.** Handlers run in
  `bot-runner`, so a broken or hanging handler cannot take down the panel or
  other bots.
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

- Web UI and REST API for the UI.
- Bot lifecycle: validate token (`getMe`), migrate to the local server
  (`deleteWebhook` + `logOut` on `api.telegram.org`), pause, delete, migrate back.
- One `getUpdates` long-polling loop per active bot. Adding a bot starts a new
  loop without restarting any container.
- Dispatch: drops updates from users outside the bot's allow list and sends the
  rest to the active handler version, up to `BOT_CONCURRENCY` at a time per bot.
  (Routing to several handlers is planned.)
- Update log: raw update JSON, matched route, duration, error, handler logs.
- Bot API console with request history.
- Storage: SQLite to start with.

### bot-runner

- Receives the update, the bot and the handler code from the orchestrator and
  runs it in a fresh process with a time and memory limit, so new versions need
  no restart.
- Provides the handler SDK (`ctx`), see below.
- No Docker socket, CPU/RAM limits on the container, file helpers scoped to the
  bot folder.

### telegram-bot-api

Stock image `aiogram/telegram-bot-api` running with `--local`. It publishes no
ports. All three images run as uid 101, so they can share files on the volumes.

## Handler SDK

A handler is a module that defines `handle(update, ctx)`, sync or async. Each run
happens in a fresh process inside bot-runner.

```python
import datetime as dt


async def handle(update, ctx):
    msg = update.message
    if msg and msg.document:
        day = dt.date.fromtimestamp(msg.date).isoformat()
        path = await ctx.files.local_path(msg.document.file_id)
        saved = ctx.files.move(path, f"inbox/{day}_{ctx.files.safe_name(msg.document.file_name)}")
        await ctx.reply(f"Saved: {saved.name}")
```

| Member | Purpose |
| --- | --- |
| `update` | Telegram fields as attributes, `None` when missing; `message.from_` is the sender; `update.to_dict()` is the raw JSON |
| `await ctx.reply(text, **params)` | `sendMessage` to the chat of the update |
| `await ctx.api.<method>(**params)` | Any Bot API method, snake_case or camelCase; a `Path` value is sent as a local `file://` URI |
| `await ctx.answer_callback(text)` | `answerCallbackQuery` for the current update |
| `ctx.chat_id`, `ctx.bot` | Chat of the update; `id` and `username` of the bot |
| `await ctx.files.local_path(file_id)` | `getFile` through the local server, returns the absolute path |
| `ctx.files.move/copy(src, dst)` | Into the bot folder; existing names get a ` (1)` suffix unless `overwrite=True` |
| `ctx.files.write_text/write_bytes/read_text/read_bytes/list/exists/path/safe_name` | File helpers scoped to the bot folder |
| `await ctx.kv.get(key, default)`, `set`, `delete` | Per-bot JSON storage kept by the orchestrator |
| `ctx.secrets[name]` | Secrets from the Settings tab |
| `ctx.log(...)`, `print(...)` | Lines in the update log |
| `ctx.http` | `httpx.AsyncClient` |
| `await ctx.shell(*args, timeout=, cwd=)` | Run a program (default cwd: the bot folder) |

Planned: `ctx.bots["name"]` (another managed bot) and `ctx.userbot`
(conversations with third-party bots through a user account).

## Orchestrator–runner contract

### `POST {RUNNER_URL}/run`

Header `X-Runner-Secret: <RUNNER_SECRET>`.

```json
{
  "run_id": "hex string",
  "bot": {"id": 123, "username": "my_bot", "token": "123:ABC..."},
  "code": "async def handle(update, ctx): ...",
  "version": 3,
  "update": {"update_id": 1, "message": {}},
  "secrets": {"NAME": "value"},
  "dry_run": false,
  "timeout": null
}
```

Response:

```json
{
  "status": "ok | error | timeout",
  "error": "traceback or null",
  "logs": ["line"],
  "api_calls": [{"method": "sendMessage", "params": {}, "ok": true, "dry_run": false}],
  "duration_ms": 42
}
```

Tokens are masked in `error`, `logs` and `api_calls`. Logs are capped at 500
lines and API calls at 200 per run.

### Internal API of the orchestrator

Used by `ctx.kv`; requires the same `X-Runner-Secret` header.

| Request | Effect |
| --- | --- |
| `GET /internal/kv/{bot_id}/{key}` | `{"value": ...}` or 404 |
| `PUT /internal/kv/{bot_id}/{key}` with `{"value": ...}` | Store a JSON value |
| `DELETE /internal/kv/{bot_id}/{key}` | Remove a key |

## Data model

| Table | Contents |
| --- | --- |
| `bots` | Encrypted token, username, status, update offset, allowed users, active handler version |
| `handler_versions` | Code and comment of every saved version |
| `updates` | Raw update, sender, summary, status, handler version, duration, error, logs, API calls (kept `UPDATE_RETENTION_DAYS`) |
| `console_history` | Last 200 API console calls per bot |
| `secrets` | Encrypted secret values |
| `kv` | JSON values stored by handlers |

Update statuses: `pending`, `ok`, `error`, `timeout`, `dispatch_error` (the runner
could not be reached), `no_handler`, `ignored` (sender not in allowed users).

## Roadmap

1. **MVP (done):** add bot with cloud logOut, polling, update log, API console,
   one handler per bot with an editor, versions and test runs,
   `ctx.reply/api/files/kv/secrets/shell`, file browser, allowed users.
2. **Stage 2:** routes and multiple handlers per bot, background queue for long
   tasks, user-bots, schedules, error notifications in Telegram, editor
   autocompletion for the SDK.
3. **Stage 3:** shared modules, Git sync for handlers, AI assistant in the
   editor, metrics, webhook mode.
