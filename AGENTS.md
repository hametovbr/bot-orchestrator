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

## Releases

1. Bump the version in `pyproject.toml` and `src/<package>/__init__.py`, run
   `uv lock`, and add a `## [X.Y.Z] - YYYY-MM-DD` section to `CHANGELOG.md`.
2. Release both repositories with the same version when a change spans them, and
   bump "Matches bot-orchestrator X and bot-runner Y" in the handler skill.
3. Merge to `main`, then push a tag `vX.Y.Z` on that commit, or create a release
   with a new tag `vX.Y.Z` on `main` in the GitHub UI.
4. CI fails if the tag differs from `pyproject.toml` or `CHANGELOG.md` has no
   section for it. Otherwise it publishes `ghcr.io/hametovbr/<repo>:X.Y.Z` and
   `:X.Y` and creates the GitHub release with that changelog section as notes.
   `:latest` and `:<commit sha>` are published from `main`.

## Invariants

- A bot is a database record; adding a bot must never require editing
  `docker-compose.yml` or restarting containers.
- User handler code never runs inside the orchestrator process.
- The local Bot API data directory is mounted at the same path in every
  container.
- Bot tokens are masked in logs, the API console and the update log.

## Handler skill (`skills/bot-orchestrator-handlers`)

### What it is

An [Agent Skill](https://docs.claude.com/en/docs/agents-and-tools/agent-skills/overview)
that teaches any AI agent to write code for bots on this platform: handlers,
background tasks, schedules, shared modules, routes, the full `ctx` SDK, limits
and examples. It is the reference handed to agents that write bot logic, so it
must describe the platform exactly as the code behaves.

| File | Contents |
| --- | --- |
| `SKILL.md` | Execution model, workflow, what to hand over, key rules, the supported versions |
| `references/sdk.md` | Every `ctx` member: arguments, return values, errors |
| `references/platform.md` | Routes, update and content types, schedules, modules, packages, limits, paths, test runs |
| `references/examples.md` | Complete handlers; each must run unchanged on the current runner |

### Updating it is mandatory

**Any change to behavior described in the skill must update the skill in the same
change (commit or PR).** A change is not done while the skill is stale. This
covers, in this repo and in bot-runner:

- the handler SDK (`bot-runner/src/bot_runner/sdk.py`, `media.py`, `worker.py`,
  `shared_modules.py`): new, removed or renamed `ctx` members, arguments,
  defaults, return values, exceptions, dry-run behavior;
- routing and updates (`routing.py`, `updates.py`): route conditions, content and
  update types, allowed users;
- schedules (`service.py`, `cron.py`): the `update.schedule` payload, cron rules;
- shared modules and packages (`modules.py`, `packages.py`, and the runner's
  `packages.py`): naming, imports, what is preinstalled;
- limits and paths (`config.py` of both services, `docker-compose.yml`): timeouts,
  memory, concurrency, retention, mounted folders;
- test runs and the update log as seen by handler authors;
- the project version.

### How to update it

1. Change the matching reference file; keep `SKILL.md` short and move details to
   `references/`.
2. Update or add an example in `references/examples.md` when usage changes, and
   run every example against the runner (the runner's `tests/conftest.py` has a
   fake Bot API for this).
3. Bump "Matches bot-orchestrator X and bot-runner Y" in `SKILL.md` on every
   release.
4. Keep `docs/ARCHITECTURE.md` (Handler SDK section) and the editor hints in
   `src/bot_orchestrator/static/sdk_hints.js` consistent with the skill.
5. Run `uv run pytest tests/test_skill.py`.

### Automatic checks

- `tests/test_skill.py` here fails when the skill misses a `ctx` member from
  `sdk_hints.js`, a route condition, content type or update type, a `Download` or
  schedule field, when its version differs from `pyproject.toml`, when a Python
  example does not compile or when a link is broken.
- `tests/test_sdk_surface.py` in bot-runner holds a snapshot of the public SDK and
  fails on any signature change; updating that snapshot means updating this skill.

The checks catch omissions, not wrong descriptions: read the changed code and the
skill side by side.
