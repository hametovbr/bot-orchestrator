# bot-orchestrator

A self-hosted control panel for running many Telegram bots on top of a single
[local Telegram Bot API server](https://github.com/tdlib/telegram-bot-api).

> **Status:** early design stage. Nothing here is usable yet.

## What it does

- **Web UI** to manage all your bots in one place.
- **Add a bot with a button** — paste a token, and the orchestrator moves the bot
  to the local Bot API server (`logOut` from the cloud API) and starts receiving
  updates. Bots are database records, not containers, so `docker-compose.yml`
  never changes when you add one.
- **Bot API console** — call `getMe`, `getUpdates`, `sendMessage` and any other
  method from the browser, with a history of requests.
- **Handlers as code** — write Python handlers in the browser, route updates to
  them by command, regex, content type or callback, and test them against real
  updates from the log before going live.
- **Update log** — every incoming update with the handler that ran, its duration
  and errors.

Planned: scheduled jobs, key-value storage and secrets per bot, a file browser
for the shared volume, user-bot accounts for talking to third-party bots, and
error notifications in Telegram.

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
| [**bot-runner**](https://github.com/hametovbr/bot-runner) | Executes user-written handlers in an isolated container |
| [**telegram-bot-api**](https://github.com/tdlib/telegram-bot-api) | Local Bot API server shared by all bots: large files, local file paths |

See [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) for details.

## Security

Anyone with access to the web UI can run arbitrary code on the server. Keep the
UI on a local network or behind a VPN. See [SECURITY.md](SECURITY.md).

## License

[MIT](LICENSE)
