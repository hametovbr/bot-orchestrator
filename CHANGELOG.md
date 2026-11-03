# Changelog

Versions follow [Semantic Versioning](https://semver.org); until 1.0 a minor
version may contain breaking changes. Release `X.Y.Z` of bot-orchestrator is
meant to run with release `X.Y.Z` of
[bot-runner](https://github.com/hametovbr/bot-runner/blob/main/CHANGELOG.md).

## [0.4.0] - 2026-11-03

### Added

- Environment variables for handlers: `BOTENV_<NAME>` on the `bot-runner` service
  becomes `<NAME>` in every handler, background task and `ctx.shell` program
  (requires bot-runner 0.4.0). Documented in the README, `docs/ARCHITECTURE.md`,
  the handler skill and `docker-compose.yml`.
- Versioned releases: pushing a tag `vX.Y.Z` checks it against `pyproject.toml`,
  publishes the image as `X.Y.Z` and `X.Y` and creates a GitHub release from this
  file.

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
