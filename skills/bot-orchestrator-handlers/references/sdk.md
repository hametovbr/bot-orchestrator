# Handler SDK reference (`ctx`)

Source of truth: `bot-runner/src/bot_runner/sdk.py` and `media.py`.

## Entry points

| Code | Called as | When |
| --- | --- | --- |
| `def handle(update, ctx)` / `async def handle(update, ctx)` | `handle(ctx.update, ctx)` | A route or schedule picked this handler |
| `async def name(ctx, *args, **kwargs)` (top level) | `name(ctx, *args, **kwargs)` | A background task started with `ctx.background(name, ...)` |

`update` is the same object as `ctx.update`. Return values are ignored.

## Telegram objects

Updates and Bot API results are wrapped read-only objects:

- `obj.field` returns the field, wrapped again if it is an object or a list;
  `None` when the field is missing.
- `obj.from_` reads the JSON field `from` (a Python keyword).
- `obj["field"]` raises `KeyError` when missing; `obj.get("field", default)`;
  `"field" in obj`.
- `obj.to_dict()` returns the raw JSON; `bool(obj)` is `False` for `{}`.
- `msg.photo` is a list of sizes; the largest is `msg.photo[-1]`.

## Context members

| Member | Description |
| --- | --- |
| `ctx.update` | The update being handled (wrapped). For schedules it has `update.schedule` |
| `ctx.bot` | This bot: `ctx.bot.id`, `ctx.bot.username` |
| `ctx.chat_id` | Chat of the update (message, edited message, channel post, business message, callback query message, member updates, join requests, reactions, or the schedule's chat); `None` otherwise |
| `ctx.handler` | The running handler: `id`, `name`, `version` (0 for unsaved code in a test run) |
| `ctx.task` | Inside a background task: `ctx.task.id`, `ctx.task.title`; `None` in a handler |
| `ctx.dry_run` | `True` in a dry test run |
| `ctx.secrets` | `dict` of the bot's secrets (Settings tab). `ctx.secrets["NAME"]` raises `KeyError` if missing; use `.get` for optional ones |
| `ctx.http` | Shared `httpx.AsyncClient` (connect timeout 60 s, read timeout 300 s) for outside requests |
| `ctx.api` | Bot API client, below |
| `ctx.files` | File helpers, below |
| `ctx.kv` | Per-bot JSON storage, below |
| `ctx.tasks` | Background tasks of this bot, below |
| `ctx.log(*values, sep=" ")` | A line in the update log. `print()` works too |
| `await ctx.reply(text, **params)` | `sendMessage` to `ctx.chat_id`; `params` are any `sendMessage` fields (`parse_mode`, `reply_markup`, `reply_parameters`, `link_preview_options`...). Returns the sent `Message` (`None` in a dry run). `RuntimeError` if there is no chat |
| `await ctx.reply_file(path, caption=None, kind="auto", **params)` | Send a file to `ctx.chat_id`; see below |
| `await ctx.answer_callback(text=None, **params)` | `answerCallbackQuery` for the current update (`show_alert=True` for a pop-up). `RuntimeError` if the update is not a callback query |
| `await ctx.background(func, *args, title=None, **kwargs)` | Queue `func(ctx, *args, **kwargs)` as a background task; returns the task id (`None` in a dry run) |
| `await ctx.progress(text, force=False)` | Progress line of the current task on the Tasks tab (max 500 characters, at most once per second unless `force=True`). Outside a task it logs the text |
| `await ctx.download(url, folder="downloads", **options)` | Download media with yt-dlp; see below |
| `await ctx.shell(*args, timeout=None, cwd=None)` | Run a program without a shell; returns `ShellResult` |

### `ctx.api` — Bot API

- `await ctx.api.<method>(**params)` with the method in snake_case or camelCase:
  `ctx.api.send_message(...)` equals `ctx.api.sendMessage(...)`.
- `await ctx.api.call("methodName", **params)` for the same thing by name.
- Parameters with value `None` are dropped. Dicts, lists and wrapped objects are
  sent as JSON (`reply_markup={"inline_keyboard": [[{"text": "OK", "callback_data": "ok"}]]}`).
- A `pathlib.Path` value is sent as a local `file://` URI, so the local server
  uploads the file from disk (up to 2000 MB). A plain string is treated by
  Telegram as a `file_id` or a URL, not as a path.
- Returns the wrapped `result`. On failure raises `TelegramError` with `.method`,
  `.code` (e.g. 400, 403, 429; `None` for network errors) and `.description`.
- In a dry run, methods starting with `get` are really called; all others are
  only recorded and return `None`.
- The client is bound to this bot's token. It works with the **local** Bot API
  server, so file paths from `getFile` are absolute local paths.

### `ctx.reply_file(path, caption=None, kind="auto", **params)`

Picks the method from the extension when `kind="auto"`:

| Kind | Method | Extensions |
| --- | --- | --- |
| `video` | `sendVideo` (with `supports_streaming=True`) | `.mp4`, `.m4v`, `.mov` |
| `audio` | `sendAudio` | `.mp3`, `.m4a`, `.aac`, `.flac`, `.wav`, `.ogg`, `.opus` |
| `photo` | `sendPhoto` | `.jpg`, `.jpeg`, `.png`, `.webp` up to 10 MB |
| `animation` | `sendAnimation` | `.gif` |
| `voice` | `sendVoice` | only when passed explicitly |
| `document` | `sendDocument` | everything else |

`path` may be relative to the bot folder or absolute inside the bot folder or the
Bot API data folder. Extra `params` go to the method (`parse_mode`,
`reply_markup`, `thumbnail`, `duration`...). `FileNotFoundError` if the file is
missing. Files in the shared read-only folder (`/shared`) are not visible to the
Bot API server: copy them into the bot folder first.

### `ctx.files` — the bot folder

The bot folder is `ctx.files.root` (`/files/<bot id>`), shared with the owner's
file browser. Relative paths are resolved inside it; escaping it raises
`ValueError`. Reads may also use absolute paths in the Bot API data folder
(`/var/lib/telegram-bot-api`) and the shared read-only folder (`/shared`).

| Member | Description |
| --- | --- |
| `ctx.files.root` | `Path` of the bot folder |
| `ctx.files.path(relative=".")` | Absolute `Path` inside the bot folder (no I/O) |
| `ctx.files.readable(source)` | Absolute `Path` of a file the handler may read |
| `await ctx.files.local_path(file_id)` | `getFile` through the local server; returns the absolute `Path` of the downloaded file (in the Bot API cache) |
| `ctx.files.move(source, destination, overwrite=False)` | Move into the bot folder, creating folders; returns the final `Path`. An existing name gets ` (1)`, ` (2)`... unless `overwrite=True`. Read-only sources are copied |
| `ctx.files.copy(source, destination, overwrite=False)` | Same, keeping the source |
| `ctx.files.write_text(destination, text)` | Write a UTF-8 text file; returns the `Path` |
| `ctx.files.write_bytes(destination, data)` | Write a binary file; returns the `Path` |
| `ctx.files.read_text(source)` | Read a UTF-8 text file |
| `ctx.files.read_bytes(source)` | Read a binary file |
| `ctx.files.list(relative=".")` | Sorted list of `Path`s in a folder; `[]` if it does not exist |
| `ctx.files.exists(relative)` | Whether a path exists in the bot folder |
| `ctx.files.safe_name(name)` | A string usable as a file name (no `/`, control characters or `<>:"\|?*\`) |

These helpers are synchronous except `local_path`. Plain `open()`/`pathlib` also
work on the same paths.

### `ctx.kv` — storage

Per-bot key-value storage kept by the orchestrator, visible and editable on the
bot's Settings tab. Values are any JSON (`dict`, `list`, `str`, numbers, `bool`,
`None`).

| Member | Description |
| --- | --- |
| `await ctx.kv.get(key, default=None)` | Stored value or `default` |
| `await ctx.kv.set(key, value)` | Store a value |
| `await ctx.kv.delete(key)` | Remove a key (no error if missing) |

Use key prefixes for structure, e.g. `f"user:{user_id}:lang"`. Updates may run in
parallel, so avoid relying on atomic counters.

### `ctx.tasks` and background tasks

```python
task_id = await ctx.background(fetch, url, quality="720", title=f"Download {url}")
```

- `func` must be defined at the top level of the handler code (a module-level
  `def` or `async def`, not `handle`, not a lambda, not imported from a shared
  module — wrap the shared function in a local one).
- `args`/`kwargs` must be JSON values; `Path` and wrapped objects are converted.
- `title` (max 200 characters) is shown on the Tasks tab; defaults to the
  function name.
- The task runs later in its own process with the **same update**, so
  `ctx.reply`, `ctx.chat_id` and `ctx.update` work there too. `ctx.task` is set.
- Tasks pin the handler code and shared module versions they were queued with;
  "Run again" in the UI reuses them.
- Tasks are queued in the orchestrator and run `TASK_CONCURRENCY` (2) at a time
  across all bots.

| Member | Description |
| --- | --- |
| `await ctx.tasks.get(task_id)` | `None` or an object with `id`, `title`, `status`, `progress`, `error`, `entry` |
| `await ctx.tasks.cancel(task_id)` | Cancel a queued or running task of this bot; `False` if it already finished |

Task statuses: `queued`, `running`, `ok`, `error`, `timeout`, `cancelled`,
`interrupted`, `dispatch_error`.

### `ctx.download(url, folder="downloads", **options)` — yt-dlp

Downloads a video or audio into `folder` inside the bot folder and, by default,
keeps a status message in the chat updated with progress (and mirrors it to
`ctx.progress`). Use it inside a background task.

| Option | Default | Meaning |
| --- | --- | --- |
| `audio_only` | `False` | Extract audio as MP3 |
| `format` | best video up to 1080p + audio (MP4 preferred) | A yt-dlp format string |
| `max_size_mb` | `2000` | Skip files above this size |
| `cookies` | `None` | A Netscape cookies file, e.g. `"/shared/instagram/instagram_cookies.txt"`; a private copy is used |
| `filename` | `"%(title).150B [%(id)s].%(ext)s"` | yt-dlp output template |
| `status` | `True` | Send and edit a status message in the chat |
| `messages` | English texts | Override status texts by key: `start`, `progress` (`{percent}`, `{downloaded}`, `{total}`, `{speed}`, `{eta}`), `processing`, `done` (`{title}`, `{size}`), `failed` (`{error}`) |
| `options` | `None` | Extra yt-dlp options, merged last |

Returns a `Download`:

| Field | Meaning |
| --- | --- |
| `path` | `Path` of the file |
| `title` | Media title |
| `url` | The URL passed in |
| `size` | Size in bytes |
| `message_id` | The status message, if one was sent (edit or delete it later) |
| `info` | Trimmed yt-dlp info: `id`, `title`, `uploader`, `channel`, `duration`, `webpage_url`, `extractor_key`, `ext`, `width`, `height`, `upload_date`, `description` |

Raises `DownloadFailed` (import with `from bot_runner.media import DownloadFailed`,
or catch `Exception` and use `str(exc)`); its message is safe to show users.

### `ctx.shell(*args, timeout=None, cwd=None)`

Runs a program directly (no shell, so no pipes or globs), with `cwd` defaulting
to the bot folder. Returns `ShellResult` with `returncode`, `stdout`, `stderr`
(text). On `timeout` the program is killed and `TimeoutError` is raised.
`ffmpeg` and `ffprobe` are installed.

```python
result = await ctx.shell("ffmpeg", "-y", "-i", src, "-vn", "-c:a", "libmp3lame", dst, timeout=600)
if result.returncode != 0:
    ctx.log(result.stderr[-2000:])
```

## Errors

| Exception | Raised by |
| --- | --- |
| `TelegramError` | `ctx.api.*`, `ctx.reply`, `ctx.reply_file`, `ctx.answer_callback`, `ctx.files.local_path` |
| `DownloadFailed` | `ctx.download` |
| `RuntimeError` | `ctx.reply*` without a chat, `ctx.answer_callback` outside a callback |
| `ValueError` | Paths outside the allowed folders; `ctx.background` with a wrong function |
| `TypeError` | `ctx.background` with non-JSON arguments |
| `httpx.HTTPError` | `ctx.http`, and `ctx.kv`/`ctx.tasks` if the orchestrator is unreachable |
| `TimeoutError` | `ctx.shell` with `timeout` |

Catch `TelegramError` from `ctx.api.*` only when the failure is expected (a
deleted message, a blocked user); otherwise let it fail so it shows in the log.
