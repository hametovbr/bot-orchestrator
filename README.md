# bot-orchestrator

A self-hosted control panel for running many Telegram bots on top of a single
[local Telegram Bot API server](https://github.com/tdlib/telegram-bot-api).

> **Status:** 0.2. Works end to end; expect breaking changes before 1.0.

## Features

- **Web UI** for all your bots: status, routes, active tasks, updates and errors
  over the last 24 hours.
- **Add a bot with a button.** Paste a token; the orchestrator logs the bot out of
  the cloud Bot API (`deleteWebhook` + `logOut`), checks it with `getMe` and starts
  polling. Bots are database records, so `docker-compose.yml` never changes.
- **Routes.** Send updates to handlers by command, regex, content type, button
  data or update type. Drag to reorder, check which route matches a logged
  update, publish command routes to the bot menu.
- **Handlers as code.** Several Python handlers per bot, written in the browser
  (Monaco editor with suggestions for the SDK and Telegram types) or started
  from templates. Every save is a version; roll back with one click.
- **Background tasks.** `await ctx.background(func, ...)` runs long work (for
  example a yt-dlp download with `ctx.download`) outside the handler's time
  limit, with progress, cancel and retry in the UI.
- **Schedules.** Cron expressions that run a handler, with a preview of the next
  runs and "Run now".
- **Test runs.** Run the code in the editor on a real logged update before saving.
  Dry runs record Bot API calls instead of sending them.
- **Update log.** Each update with its route, handler, result, duration, output,
  Bot API calls, started tasks and the raw JSON.
- **Notifications.** Handler, task and schedule failures, a stopped bot, an
  unreachable runner or a full disk are sent to you in Telegram.
- **Bot API console.** Call any method from the browser, with history.
- **Files.** Browse, download and delete what handlers saved; the Bot API file
  cache is cleaned automatically.
- **Settings per bot.** Allowed users, encrypted secrets, stored key-value data,
  pause/resume, moving the bot back to the cloud API.

Handlers run in [bot-runner](https://github.com/hametovbr/bot-runner), each in its
own process with a time and memory limit, so a broken handler cannot affect the
panel or other bots.

## Example handler

A route "text matches regex `https?://\S+`" sends links here:

```python
import re

URL = re.compile(r"https?://\S+")


async def handle(update, ctx):
    found = URL.search(update.message.text or "")
    if found:
        await ctx.background(fetch, found.group(0), title=found.group(0))
        await ctx.reply("Queued.")


async def fetch(ctx, url):
    """Runs as a background task."""
    result = await ctx.download(url)  # yt-dlp, with a progress message in the chat
    await ctx.reply_file(result.path, caption=result.title)  # up to 2 GB
```

The full SDK reference is on the handler page and in
[docs/ARCHITECTURE.md](docs/ARCHITECTURE.md#handler-sdk).

## Quick start

Requirements: a Linux host with Docker, and `api_id` / `api_hash` from
[my.telegram.org](https://my.telegram.org).

```sh
mkdir bot-orchestrator && cd bot-orchestrator
curl -O https://raw.githubusercontent.com/hametovbr/bot-orchestrator/main/docker-compose.yml
curl -o .env https://raw.githubusercontent.com/hametovbr/bot-orchestrator/main/.env.example
# Fill in .env: ADMIN_PASSWORD, SECRET_KEY, RUNNER_SECRET, TELEGRAM_API_ID, TELEGRAM_API_HASH
docker compose up -d
```

Open `http://<host>:8000` (by default the port is bound to `127.0.0.1`; set
`ORCHESTRATOR_BIND` in `.env` to your LAN or VPN address), log in and add a bot.

To update: `docker compose pull && docker compose up -d`, or let
[Watchtower](https://containrrr.dev/watchtower/) do it.

## How it fits together

```
Browser ──► bot-orchestrator ◄──► telegram-bot-api ◄──► Telegram
                  │                       │
                  ▼                       │
              bot-runner                  │
                  │                       │
                  └──── shared volume ◄───┘
```

| Component | Role |
| --- | --- |
| **bot-orchestrator** (this repo) | Web UI, Bot API console, polling for all bots, routing, update log, storage |
| [**bot-runner**](https://github.com/hametovbr/bot-runner) | Executes user-written handlers in separate processes |
| [**telegram-bot-api**](https://github.com/tdlib/telegram-bot-api) | Local Bot API server shared by all bots: large files, local file paths |

See [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) for details.

## Development

```sh
uv sync
uv run pytest
uv run ruff check . && uv run ruff format --check .
```

Run locally against your own Bot API server and runner:

```sh
cp .env.example .env   # set BOT_API_URL, RUNNER_URL, DATABASE_PATH, FILES_ROOT for your machine
uv run uvicorn --factory bot_orchestrator.web:app --reload
```

## Security

Anyone with access to the web UI can run arbitrary code on the server. Keep the
UI on a local network or behind a VPN. See [SECURITY.md](SECURITY.md).

## License

[MIT](LICENSE)
