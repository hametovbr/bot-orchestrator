# bot-orchestrator

A self-hosted control panel for running many Telegram bots on top of a single
[local Telegram Bot API server](https://github.com/tdlib/telegram-bot-api).

> **Status:** 0.4. Works end to end; expect breaking changes before 1.0.

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
- **Shared modules.** Code common to all bots, imported with
  `from shared import name`, with versions, a list of who uses it and test runs
  of the unsaved code through any handler.
- **Packages.** Extra Python libraries for handlers from a `requirements.txt`-style
  list; installed by the runner with uv, switched only after a successful install.
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
- **Settings per bot.** Who may use the bot (an allow list of `@usernames` or user
  ids), encrypted secrets, stored key-value data, pause/resume, moving the bot back
  to the cloud API.
- **Environment for handlers.** Variables set on the runner as `BOTENV_<NAME>`
  appear in every handler as `os.environ["<NAME>"]`, without editing code.

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

The full SDK reference is in the
[handler skill](skills/bot-orchestrator-handlers/references/sdk.md) and in
[docs/ARCHITECTURE.md](docs/ARCHITECTURE.md#handler-sdk); the editor suggests
SDK members as you type.

## Writing handlers with an AI agent

[`skills/bot-orchestrator-handlers`](skills/bot-orchestrator-handlers) is an
[Agent Skill](https://docs.claude.com/en/docs/agents-and-tools/agent-skills/overview):
give it to an AI agent and it knows how code for these bots runs, the whole `ctx`
SDK, routes, schedules, shared modules, packages and limits, and hands back code
ready to paste together with the route, secrets and packages it needs.

| File | Contents |
| --- | --- |
| [`SKILL.md`](skills/bot-orchestrator-handlers/SKILL.md) | Execution model, workflow, rules, what the agent hands over |
| [`references/sdk.md`](skills/bot-orchestrator-handlers/references/sdk.md) | Every `ctx` member with arguments, results and errors |
| [`references/platform.md`](skills/bot-orchestrator-handlers/references/platform.md) | Routes, update types, schedules, modules, packages, limits, test runs |
| [`references/examples.md`](skills/bot-orchestrator-handlers/references/examples.md) | Complete, tested handlers |

Install it for Claude Code (all projects):

```sh
git clone --depth 1 https://github.com/hametovbr/bot-orchestrator /tmp/bot-orchestrator
mkdir -p ~/.claude/skills
cp -r /tmp/bot-orchestrator/skills/bot-orchestrator-handlers ~/.claude/skills/
```

For a single project use `.claude/skills/` instead. For apps that take skills as
a ZIP file, upload the folder packed with
`cd /tmp/bot-orchestrator/skills && zip -r bot-orchestrator-handlers.zip bot-orchestrator-handlers`.
Other agents can read `SKILL.md` and the `references/` files as plain Markdown.

The skill states the versions it matches and is updated together with the code:
tests in both repos fail when the SDK, routes or version change without it (see
[AGENTS.md](AGENTS.md#handler-skill-skillsbot-orchestrator-handlers)).

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

### Environment variables for handlers

Global settings that handlers need, such as a service URL or a default language,
can come from the compose file instead of the code. Add them to the `bot-runner`
service with the `BOTENV_` prefix:

```yaml
  bot-runner:
    environment:
      BOTENV_WEATHER_API_URL: https://api.example.com
```

and read them in a handler without the prefix: `os.environ["WEATHER_API_URL"]`.
They reach handlers, background tasks and programs started with `ctx.shell`, are
the same for every bot and are read when the runner starts (`docker compose up -d`
after a change). `PATH`, `PYTHON*`, `LD_*` and the runner's own settings cannot be
set this way. Passwords and API keys belong in the per-bot secrets (`ctx.secrets`).
Details: [bot-runner README](https://github.com/hametovbr/bot-runner#environment-variables-for-handlers).

### Versions

Releases are tagged `vX.Y.Z` in both repositories; each release publishes images
`ghcr.io/hametovbr/bot-orchestrator:X.Y.Z` and `ghcr.io/hametovbr/bot-runner:X.Y.Z`
(also `X.Y`). `latest` follows `main`. Pin both images to the same version to
avoid surprises; changes are listed in [CHANGELOG.md](CHANGELOG.md).

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
