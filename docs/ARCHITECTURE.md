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
- Routing: picks a handler for each update by command, regex, content type,
  callback data or a catch-all route, in priority order.
- Update log: raw update JSON, matched route, duration, error, handler logs.
- Bot API console with request history.
- Storage: SQLite to start with.

### bot-runner

- Receives `(bot, update, handler version)` from the orchestrator and runs the
  handler with a timeout.
- Loads handler code by version without restarting.
- Provides the handler SDK (`ctx`), see below.
- Background queue for long tasks such as downloads.
- No Docker socket, CPU/RAM limits, write access only to the bot files folder.

### telegram-bot-api

Stock image (for example `aiogram/telegram-bot-api`) running with `--local`.
Never exposed outside the internal Docker network.

## Handler SDK (draft)

```python
async def handle(update, ctx):
    doc = update.message.document
    path = await ctx.files.local_path(doc.file_id)
    saved = ctx.files.move(path, f"inbox/{update.message.date:%Y-%m-%d}_{doc.file_name}")
    await ctx.reply(f"Saved: {saved}")
```

| Member | Purpose |
| --- | --- |
| `ctx.reply()`, `ctx.api.<method>()` | Bot API calls on behalf of the current bot |
| `ctx.files` | Shared volume access, scoped to the bot's folder |
| `ctx.kv` | Per-bot key-value storage |
| `ctx.secrets` | Secrets configured in the UI |
| `ctx.log` | Execution log shown in the update log |
| `ctx.http`, `ctx.shell()` | HTTP requests and external tools such as `yt-dlp` |
| `ctx.bots["name"]` | Bot API calls on behalf of another managed bot |
| `ctx.userbot` | Conversations with third-party bots via a user account |

## Data model (draft)

| Table | Contents |
| --- | --- |
| `bots` | Encrypted token, username, mode, update offset, allowed users, status |
| `handlers`, `handler_versions` | Handler code, version number, active version |
| `routes` | Bot, filter type, pattern, priority, handler |
| `updates` | Raw update, matched route, duration, error, logs (kept N days) |
| `kv`, `secrets`, `schedules`, `userbots` | State, keys, cron jobs, Telethon sessions |

## Roadmap

1. **MVP:** add bot with migration, polling, update log, API console, one
   handler per bot with an editor, `ctx.reply/api/files/kv/secrets`, file browser.
2. **Stage 2:** routes and multiple handlers, replaying logged updates, handler
   versions, background queue with `yt-dlp`, user-bots, schedules, error
   notifications.
3. **Stage 3:** shared modules, Git sync for handlers, AI assistant in the
   editor, metrics, webhook mode.
