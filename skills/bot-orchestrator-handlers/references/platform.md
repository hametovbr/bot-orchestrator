# Platform: routes, schedules, modules, packages, limits

Source of truth: `bot-orchestrator/src/bot_orchestrator/routing.py`, `updates.py`,
`service.py`, `modules.py`, and `bot-runner/src/bot_runner/config.py`.

## How an update reaches code

1. One long-polling loop per bot gets the update from the local Bot API server.
2. If the bot has **allowed users** (Settings tab) and the sender is not one of
   them, the update is logged as `ignored` and nothing runs.
3. The bot's **routes** are checked from top to bottom; the first enabled match
   runs the **active version** of its handler. No match: status `no_route`.
4. The handler runs in bot-runner; the update log stores the status, duration,
   logs, Bot API calls, started tasks and the traceback.

A bot has several named handlers; each save is a new version and becomes active.
Older versions can be activated again.

## Routes

| Condition | Pattern | Matches |
| --- | --- | --- |
| `command` | Name without the slash: `start` | Message text `/start`, `/start args`, `/start@this_bot` (case-insensitive; a mention of another bot does not match) |
| `regex` | Python regex: `https?://\S+` | `re.search` in the message text, or the caption |
| `content` | Comma-separated content types: `document, photo` | Messages that have any of these fields |
| `callback` | Prefix of `callback_data`: `cancel:` | Button presses whose data starts with the prefix; empty matches every button |
| `update` | Comma-separated update types: `edited_message, my_chat_member` | Updates of these types |
| `any` | — | Everything |

Command, regex and content routes look at these message kinds: `message`,
`edited_message`, `channel_post`, `edited_channel_post`, `business_message`,
`edited_business_message`.

Content types: `text`, `document`, `photo`, `video`, `audio`, `voice`,
`video_note`, `animation`, `sticker`, `contact`, `location`, `venue`, `poll`,
`dice`.

Update types: the message kinds above plus `callback_query`, `inline_query`,
`chosen_inline_result`, `my_chat_member`, `chat_member`, `chat_join_request`,
`message_reaction`, `poll`, `poll_answer`, `pre_checkout_query`,
`shipping_query`, `schedule`.

Order matters: put specific routes (commands, buttons) above broad ones
(`regex`, `content`, `any`). A command route can be published to the bot menu
(`setMyCommands`); its route description becomes the menu text.

One handler may serve several routes and branch on the update itself:

```python
async def handle(update, ctx):
    if update.callback_query:
        ...
    elif update.message and update.message.text and update.message.text.startswith("/help"):
        ...
```

Note: updates such as `chat_member` and `message_reaction` arrive only if the bot
asks for them; the orchestrator polls with Telegram's default `allowed_updates`.

## Schedules

A schedule runs a handler on a five-field cron (`minute hour day month weekday`)
in the server timezone (`TZ`), checked at the start of every minute; missed runs
are skipped. The handler receives a synthetic update:

```json
{"update_id": 0, "schedule": {"id": 3, "name": "Morning digest", "cron": "0 9 * * 1-5",
 "time": "2026-11-03T09:00:00+03:00", "manual": false, "chat": {"id": 123456}}}
```

| Field | Meaning |
| --- | --- |
| `id` | Schedule id |
| `name` | Schedule name |
| `cron` | The cron expression |
| `time` | When it fired, ISO 8601 with offset |
| `manual` | `True` when started with "Run now" |
| `chat` | Present only if a chat is set on the schedule; then `ctx.reply` writes there |

A handler used both by routes and by a schedule should check `update.schedule`.

## Shared modules

Code common to all bots, edited on the global **Modules** page.

- Import with `from shared import texts` or `import shared.texts`.
- Names: 1–40 lowercase letters, digits and `_`, starting with a letter or `_`;
  not a Python keyword and not `shared`. Modules cannot be renamed.
- Every save is a version and becomes active for **every** handler at once; a
  test run on the module page runs any handler with the unsaved module code.
- Modules may import packages and other shared modules. They do not get `ctx`:
  pass it as an argument (`await texts.send_menu(ctx)`).
- A module still imported by a handler or another module cannot be deleted.
- Background tasks pin the module versions active when they were queued.
- Tracebacks show module lines as `<shared.texts v3>`.

## Packages

The global **Packages** page holds a `requirements.txt`-style list (package specs
only; `-e`, `--index-url` and other options are rejected). bot-runner installs it
with uv and switches to the new set only after a successful install; a failed list
keeps the previous packages.

- Always available without the list: the Python 3.12 standard library, `httpx`,
  `yt_dlp`. The runner's own dependencies (`fastapi`, `pydantic`, `uvicorn`) are
  importable but are not part of the contract.
- Packages cannot replace the runner's own dependency versions (for example a
  different `httpx`).
- `ModuleNotFoundError` in the update log gets a hint to add the package.
- System binaries available to `ctx.shell`: `ffmpeg`, `ffprobe`. No compilers are
  promised; prefer packages with wheels.

## Limits and environment

| Setting | Default | Meaning |
| --- | --- | --- |
| `HANDLER_TIMEOUT` | 60 s | A handler run is killed after this; status `timeout` |
| `TASK_TIMEOUT` | 3600 s | Same for a background task |
| `HANDLER_MEMORY_MB` | 1024 | Address-space limit per run; large libraries may need more |
| `BOT_CONCURRENCY` | 4 | Updates of one bot handled at the same time |
| `MAX_CONCURRENT_RUNS` | 8 | Handler processes in the runner at once |
| `TASK_CONCURRENCY` | 2 | Background tasks running at once, all bots together |
| `CACHE_RETENTION_HOURS` | 24 | Files downloaded by the Bot API server are deleted after this |
| `UPDATE_RETENTION_DAYS` | 14 | Update log and finished tasks are kept this long |

Deployments override these; ask the owner when a limit matters.

Paths, identical in every container:

| Path | Contents |
| --- | --- |
| `/files/<bot id>` | The bot folder (`ctx.files.root`), read-write |
| `/var/lib/telegram-bot-api` | Files downloaded by the local Bot API server (`getFile`), cleaned regularly |
| `/shared` | Read-only files for handlers, e.g. cookies (`/shared/instagram/...`); not visible to the Bot API server |

Per run: at most 500 log lines and 200 recorded Bot API calls are kept. Bot
tokens are masked everywhere in logs and errors.

The local Bot API server allows uploads and downloads up to 2000 MB and returns
absolute local paths from `getFile`.

## Test runs and the update log

- The handler editor has **Test run**: it runs the code in the editor (saved or
  not, `ctx.handler.version == 0`) on a logged update or on hand-written update
  JSON. **Dry run** is on by default: `get*` Bot API calls are real, everything
  else (and `ctx.background`) is only recorded. Files, `ctx.kv` and `ctx.http`
  are **not** simulated in a dry run.
- Every update shows its route, handler and version, status, duration, logs,
  Bot API calls, started tasks, the traceback and the raw JSON. Any logged update
  can be picked as the input of a test run.
- Update statuses: `pending`, `ok`, `error`, `timeout`, `dispatch_error`,
  `no_route`, `ignored`.

## What the SDK does not do

- No inline mode helpers, payments helpers, webhooks or conversation state
  machines: use `ctx.api.*` and `ctx.kv`.
- `ctx.api` is bound to the current bot. To act as another bot of the platform,
  store its token as a secret and call the local server with `ctx.http`
  (`http://telegram-bot-api:8081/bot<token>/<method>`); prefer one bot per
  feature instead.
- No direct database, Docker or network access to the orchestrator.
