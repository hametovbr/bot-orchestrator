# Changelog

Versions follow [Semantic Versioning](https://semver.org); until 1.0 a minor
version may contain breaking changes. Release `X.Y.Z` of bot-orchestrator is
meant to run with release `X.Y.Z` of
[bot-runner](https://github.com/hametovbr/bot-runner/blob/main/CHANGELOG.md).

## [0.5.0] - 2026-11-04

### Added

- Built-in `/purge` command, turned on per bot on the Settings tab (off by
  default). In a private chat it deletes the messages of the last 48 hours, the
  user's and the bot's, including the command itself and keeping `/start`, without
  blocking the bot. Telegram does not let bots delete older messages, so they stay.
  Deletion goes from the newest messages down and stops at the 48-hour limit,
  usually within seconds (at most 90 s). The run appears in the update log as
  `/purge (built-in)` with its outcome. Works with bot-runner 0.4.0.
- The update log shows built-in handlers by name.

### Changed

- Database schema version 4 (adds `bots.purge_command`); migrated on start.

## [0.4.0] - 2026-11-03

### Added

- Environment variables for handlers: `BOTENV_<NAME>` on the `bot-runner` service
  becomes `<NAME>` in every handler, background task and `ctx.shell` program
  (requires bot-runner 0.4.0). Documented in the README, `docs/ARCHITECTURE.md`,
  the handler skill and `docker-compose.yml`.
- Versioned releases: pushing a tag `vX.Y.Z` checks it against `pyproject.toml`,
  publishes the image as `X.Y.Z` and `X.Y` and creates a GitHub release from this
  file.

- Allowed users accept `@usernames` as well as numeric user ids, mixed in one
  list (`@alice, 123456789`; `t.me/alice` works too). Usernames match
  case-insensitively; invalid entries are reported instead of being dropped. The
  Settings tab lists the bot's recent senders with their ids.

### Changed

- The `latest` image tag is published from `main` only.

## [0.3.0]

- Extra Python packages for handlers and shared modules.

## [0.2.0]

- Routes and several handlers per bot, background tasks, `ctx.download`,
  schedules, notifications, Bot API file cache cleanup, editor suggestions and
  handler templates.

## [0.1.0]

- MVP: bots, polling, update log, API console, one handler per bot with versions
  and test runs, file browser, allowed users.
