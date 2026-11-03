---
name: bot-orchestrator-handlers
description: Write, review and debug Python code for Telegram bots run by bot-orchestrator and bot-runner — handlers `handle(update, ctx)`, background tasks, schedules, shared modules and their routes. Use whenever someone asks for bot logic, a handler, a `ctx.*` call, a route or a shared module for bot-orchestrator.
---

# Writing code for bot-orchestrator bots

bot-orchestrator is a self-hosted panel that runs many Telegram bots on one
local Telegram Bot API server. Bot logic is Python code written in its web UI and
executed by bot-runner. This skill describes that code: what it may use, how it
runs and how to hand it over.

Matches **bot-orchestrator 0.5.0** and **bot-runner 0.4.0**.

## Execution model (read first)

- A **handler** is code that defines `handle(update, ctx)`, sync or async. Make it
  `async` whenever it calls `ctx` methods: all of them except `ctx.log` and the
  `ctx.files` helpers (other than `local_path`) are coroutines and must be awaited.
- Handlers do not register themselves. The bot owner creates **routes** in the UI
  (command, regex, content type, button prefix, update type, anything); the first
  enabled matching route picks the handler. Always say which route the code needs.
- **Every run is a fresh Python process.** Module-level variables do not survive
  between updates. Keep state in `ctx.kv` (JSON) or in files in the bot folder.
- Every run has a **time limit** (`HANDLER_TIMEOUT`, 60 s by default; often set to
  120 s) and a memory limit. Anything slow (downloads, conversions, long API
  calls) goes into a **background task**: `await ctx.background(func, *args)`,
  where `func(ctx, *args)` is defined at the top level of the same handler code.
  Tasks have their own limit (`TASK_TIMEOUT`, 3600 s by default).
- Up to `BOT_CONCURRENCY` (4) updates of one bot run at the same time, so
  `ctx.kv` read-modify-write is not atomic.
- The update is wrapped: `update.message.document.file_name` works, a missing
  field is `None`, the sender is `message.from_`, `update.to_dict()` is the raw
  JSON. Guard optional parts: `if update.message and update.message.text:`.
- Files live in the bot's own folder (`/files/<bot id>`); paths passed to
  `ctx.files.*` are relative to it. Files sent to Telegram must be `Path` objects
  (or go through `ctx.reply_file`), which become local `file://` uploads of up
  to 2000 MB.
- Unhandled exceptions mark the update `error` in the update log, with the
  traceback, logs and every Bot API call; the owner may get a Telegram alert.

## Workflow

1. Clarify the trigger (command, link, file, button, schedule) and what the bot
   should answer. Pick the route from [references/platform.md](references/platform.md#routes).
2. Write one self-contained handler file. Use only `ctx` for Telegram, files,
   storage, secrets and tasks — see [references/sdk.md](references/sdk.md).
3. Move slow work to `ctx.background`; report progress with `ctx.progress`.
4. Imports: the standard library, `httpx` and `yt_dlp` are always there; anything
   else must be on the global **Packages** page; reusable code can live in a
   **shared module** (`from shared import name`).
5. Handle dry runs: in a dry run Bot API calls other than `get*` return `None`
   and `ctx.background` returns `None`, so check results before using them.
6. Hand the result over in the format below.

## What to hand over

- **Handler code**: the complete file, ready to paste into the handler editor.
- **Route(s)**: condition and pattern, e.g. `command` · `start`, `regex` ·
  `https?://\S+`, `callback` · `cancel:`, plus where it goes in the order if
  other routes could match first.
- **Secrets** to add on the bot's Settings tab (name and meaning, never values).
- **Environment variables** the code reads with `os.environ`, if any: the owner
  sets them on the runner as `BOTENV_<NAME>` (global, not per bot).
- **Packages** to add on the Packages page and **shared modules** to create.
- **Schedule** (cron, chat) if the handler runs on a timer.
- **How to test**: a sample update JSON for the editor's test run (dry run is on
  by default) or the message to send to the bot.

## Rules that prevent most bugs

- `await` every `ctx` coroutine; a missing `await` silently does nothing.
- Never keep tokens, passwords or API keys in code: use `ctx.secrets["NAME"]`.
  Global, non-secret settings may come from `os.environ` (set on the runner as
  `BOTENV_<NAME>`, see [platform.md](references/platform.md#environment-variables));
  read them with `os.environ.get` and a default, since a deployment may not set them.
- Do not import or call `bot_runner` internals, the database or other services;
  the SDK is the contract. The one exception is the exception class
  `from bot_runner.media import DownloadFailed`.
- `ctx.background` takes a top-level function from the same code (not a lambda,
  not a nested or imported function); its arguments must be JSON values.
- Callback data is at most 64 bytes; message text at most 4096 characters, captions
  1024. Split long text.
- Files from `ctx.files.local_path(file_id)` sit in the Bot API cache, which is
  cleaned after `CACHE_RETENTION_HOURS` (24 h): `move`/`copy` what you keep.
- `ctx.reply` and `ctx.reply_file` need a chat: they fail for inline queries and
  for schedules without a chat. Use `ctx.api.*` with an explicit `chat_id` then.
- Telegram errors raise `TelegramError` (`.method`, `.code`, `.description`);
  `ctx.download` raises `DownloadFailed`, whose message is safe to show users.

## Minimal handler

```python
async def handle(update, ctx):
    msg = update.message
    if not msg or not msg.text:
        return
    count = await ctx.kv.get("count", 0) + 1
    await ctx.kv.set("count", count)
    await ctx.reply(f"Message #{count}: {msg.text}")
```

Route: `any`, or `content` · `text`.

## References

- [references/sdk.md](references/sdk.md) — every `ctx` member, arguments, return
  values and errors.
- [references/platform.md](references/platform.md) — routes, update types,
  schedules, shared modules, packages, limits, test runs, the update log.
- [references/examples.md](references/examples.md) — complete handlers: commands
  and menus, saving files, link downloads with a cancel button, scheduled
  digests, external APIs, shared modules.
