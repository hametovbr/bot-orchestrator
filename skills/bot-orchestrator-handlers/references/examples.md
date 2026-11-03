# Examples

Each example is a complete handler file plus the route it needs.

## Commands with an inline menu

Routes: `command` · `start` and `callback` · `menu:`, both to this handler.

```python
MENU = {
    "inline_keyboard": [
        [{"text": "📊 Stats", "callback_data": "menu:stats"}],
        [{"text": "🧹 Reset", "callback_data": "menu:reset"}],
    ]
}


async def handle(update, ctx):
    if update.callback_query:
        await on_button(update.callback_query, ctx)
        return
    name = update.message.from_.first_name or "there"
    await ctx.reply(f"Hi, {name}! Pick an action:", reply_markup=MENU)


async def on_button(query, ctx):
    action = query.data.removeprefix("menu:")
    if action == "stats":
        count = await ctx.kv.get("count", 0)
        text = f"Messages so far: {count}"
    else:
        await ctx.kv.delete("count")
        text = "Counter reset."
    await ctx.answer_callback()
    # Edit the message with the menu instead of sending a new one.
    await ctx.api.edit_message_text(
        chat_id=query.message.chat.id,
        message_id=query.message.message_id,
        text=text,
        reply_markup=MENU,
    )
```

## Save incoming files with a name built from the file info

Route: `content` · `document, photo, video, audio, voice`.

```python
import datetime as dt


async def handle(update, ctx):
    msg = update.message
    media = (
        msg.document
        or msg.video
        or msg.audio
        or msg.voice
        or (msg.photo[-1] if msg.photo else None)
    )
    if not media:
        return
    day = dt.datetime.fromtimestamp(msg.date).strftime("%Y-%m-%d")
    sender = msg.from_.username or str(msg.from_.id)
    original = media.file_name or f"{media.file_unique_id}{'.jpg' if msg.photo else ''}"
    path = await ctx.files.local_path(media.file_id)  # up to 2000 MB with the local server
    saved = ctx.files.move(path, f"inbox/{sender}/{day}_{ctx.files.safe_name(original)}")
    size_mb = saved.stat().st_size / 1024 / 1024
    await ctx.reply(f"Saved {saved.name} ({size_mb:.1f} MB)")
```

## Download a link in the background, with a cancel button

Routes: `regex` · `https?://\S+` and, above it, `callback` · `cancel:`.

```python
import re

from bot_runner.media import DownloadFailed

URL = re.compile(r"https?://\S+")


async def handle(update, ctx):
    if update.callback_query:
        task_id = int(update.callback_query.data.split(":", 1)[1])
        cancelled = await ctx.tasks.cancel(task_id)
        await ctx.answer_callback("Cancelled" if cancelled else "Already finished")
        return
    msg = update.message
    found = URL.search(msg.text or msg.caption or "")
    if not found:
        return
    url = found.group(0)
    audio = "#audio" in (msg.text or "")
    task_id = await ctx.background(fetch, url, audio, title=url[:100])
    if task_id:  # None in a dry run
        button = {"text": "Cancel", "callback_data": f"cancel:{task_id}"}
        await ctx.reply("Queued.", reply_markup={"inline_keyboard": [[button]]})


async def fetch(ctx, url, audio):
    try:
        result = await ctx.download(
            url,
            folder="downloads/audio" if audio else "downloads/video",
            audio_only=audio,
            cookies="/shared/instagram/instagram_cookies.txt" if "instagram.com" in url else None,
        )
    except DownloadFailed:
        return  # the status message already shows the error
    await ctx.reply_file(result.path, caption=result.title[:1000])
    if result.message_id:
        await ctx.api.delete_message(chat_id=ctx.chat_id, message_id=result.message_id)
```

## Scheduled digest from an external API

Schedule: cron `0 9 * * 1-5`, chat set. Secret: `WEATHER_KEY`. No route needed.

```python
async def handle(update, ctx):
    if not update.schedule:
        return
    response = await ctx.http.get(
        "https://api.example.com/forecast",
        params={"city": "Moscow", "key": ctx.secrets["WEATHER_KEY"]},
    )
    response.raise_for_status()
    data = response.json()
    last = await ctx.kv.get("last_digest")
    text = f"☀️ {data['summary']}, {data['temp']}°C"
    if text != last:
        await ctx.reply(text)
        await ctx.kv.set("last_digest", text)
```

## Long processing with progress

Route: `content` · `video`. Converts a video to MP3 with ffmpeg in a task.

```python
async def handle(update, ctx):
    video = update.message.video
    await ctx.background(convert, video.file_id, video.file_name or "video.mp4", title="To MP3")
    await ctx.reply("Converting…")


async def convert(ctx, file_id, name):
    source = await ctx.files.local_path(file_id)
    target = ctx.files.path(f"mp3/{ctx.files.safe_name(name).rsplit('.', 1)[0]}.mp3")
    target.parent.mkdir(parents=True, exist_ok=True)
    await ctx.progress("ffmpeg is running", force=True)
    result = await ctx.shell("ffmpeg", "-y", "-i", source, "-vn", "-q:a", "2", target, timeout=1800)
    if result.returncode != 0:
        ctx.log(result.stderr[-3000:])
        await ctx.reply("Conversion failed.")
        return
    await ctx.reply_file(target, kind="audio")
```

## A shared module used by several bots

Module `texts` (Modules page):

```python
"""Common texts and a language switch."""

TEXTS = {
    "en": {"hello": "Hello, {name}!"},
    "ru": {"hello": "Привет, {name}!"},
}


async def t(ctx, key, **values):
    """Text in the user's saved language (kv key "lang:<user id>")."""
    user = ctx.update.message.from_ if ctx.update.message else None
    lang = await ctx.kv.get(f"lang:{user.id}", "en") if user else "en"
    return TEXTS.get(lang, TEXTS["en"])[key].format(**values)
```

Handler in any bot:

```python
from shared import texts


async def handle(update, ctx):
    await ctx.reply(await texts.t(ctx, "hello", name=update.message.from_.first_name))
```

## Sample update JSON for a test run

```json
{"update_id": 1, "message": {"message_id": 1, "date": 1762160000,
  "chat": {"id": 123456, "type": "private"},
  "from": {"id": 123456, "is_bot": false, "first_name": "Ann"},
  "text": "/start"}}
```

For a button press, use `"callback_query": {"id": "1", "from": {...}, "data":
"menu:stats", "message": {...}}` instead of `message`.
