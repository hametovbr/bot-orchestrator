"""Starting points offered when a handler is created."""

TEMPLATES = {
    "echo": {
        "title": "Echo text",
        "route": ("any", ""),
        "code": '''\
async def handle(update, ctx):
    """Replies with the text it receives."""
    if update.message and update.message.text:
        await ctx.reply(f"You said: {update.message.text}")
''',
    },
    "save_files": {
        "title": "Save files to the bot folder",
        "route": ("content", "document, photo, video, audio"),
        "code": '''\
import datetime as dt


async def handle(update, ctx):
    """Saves documents, photos, videos and audio to inbox/ with the date in the name."""
    msg = update.message
    if not msg:
        return
    media = msg.document or msg.video or msg.audio or (msg.photo[-1] if msg.photo else None)
    if not media:
        await ctx.reply("Send me a file.")
        return
    day = dt.date.fromtimestamp(msg.date).isoformat()
    name = media.file_name or f"{media.file_unique_id}.jpg"
    path = await ctx.files.local_path(media.file_id)
    saved = ctx.files.move(path, f"inbox/{day}_{ctx.files.safe_name(name)}")
    await ctx.reply(f"Saved {saved.name}")
''',
    },
    "download_link": {
        "title": "Download a link in the background (yt-dlp)",
        "route": ("regex", r"https?://\S+"),
        "code": '''\
import re

URL = re.compile(r"https?://\\S+")


async def handle(update, ctx):
    """Starts a background download for the first link in the message."""
    msg = update.message
    found = URL.search((msg and (msg.text or msg.caption)) or "")
    if not found:
        await ctx.reply("Send me a link.")
        return
    url = found.group(0)
    task_id = await ctx.background(fetch, url, title=url[:100])
    if task_id:
        button = {"text": "Cancel", "callback_data": f"cancel:{task_id}"}
        await ctx.reply("Queued.", reply_markup={"inline_keyboard": [[button]]})


async def fetch(ctx, url):
    """Runs as a background task: downloads with yt-dlp and sends the file back."""
    result = await ctx.download(url)
    await ctx.reply_file(result.path, caption=result.title)
''',
    },
    "cancel_button": {
        "title": "Cancel button for background tasks",
        "route": ("callback", "cancel:"),
        "code": '''\
async def handle(update, ctx):
    """Handles buttons with callback data "cancel:<task id>"."""
    task_id = int(update.callback_query.data.split(":", 1)[1])
    cancelled = await ctx.tasks.cancel(task_id)
    await ctx.answer_callback("Cancelled" if cancelled else "Already finished")
''',
    },
    "scheduled": {
        "title": "Scheduled message",
        "route": None,
        "code": '''\
async def handle(update, ctx):
    """Runs on a schedule. Set a chat on the schedule so ctx.reply knows where to write."""
    schedule = update.schedule
    await ctx.reply(f"⏰ {schedule.name} ({schedule.time})")
''',
    },
}
