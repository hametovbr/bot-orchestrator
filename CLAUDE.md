# Notes for AI assistants

## Project

bot-orchestrator is the web UI and control service of a self-hosted platform for
many Telegram bots on one local Telegram Bot API server. Handlers written in the
UI run in the companion repo [bot-runner](https://github.com/hametovbr/bot-runner).
The design lives in [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md); keep it updated
when the design changes.

## Language rules

- Everything in the repository is in English: code, comments, docs, commit
  messages, issue and PR text.
- The user interface is in English.
- The owner may write to you in Russian; reply in the conversation in their
  language, but keep repository content in English.

## Conventions

- Python 3.12+, dependencies managed with `uv` (`pyproject.toml` + `uv.lock`).
- Format and lint with `ruff`; tests with `pytest`.
- Commit messages: imperative mood, short subject line (≤ 72 characters),
  optional body explaining why.
- Configuration comes from environment variables; document every variable in
  `.env.example`. Never commit real tokens, `api_id`/`api_hash` or keys.
- The container image is built by GitHub Actions and pushed to
  `ghcr.io/hametovbr/bot-orchestrator`.

## Invariants

- A bot is a database record; adding a bot must never require editing
  `docker-compose.yml` or restarting containers.
- User handler code never runs inside the orchestrator process.
- The local Bot API data directory is mounted at the same path in every
  container.
- Bot tokens are masked in logs, the API console and the update log.
