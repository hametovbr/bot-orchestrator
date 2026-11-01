// Editor suggestions for the handler SDK and common Telegram types.
// The editor does not run Python, so types are resolved from the text before the dot:
// ctx, update, msg/message, query, doc, result... and attribute chains such as
// update.message.document.

(function () {
  const F = (type, doc) => ({ kind: "field", type, doc });
  const M = (insert, doc, type) => ({ kind: "method", insert, doc, type });

  const TYPES = {
    Context: {
      update: F("Update", "The update being handled."),
      bot: F("BotInfo", "id and username of this bot."),
      chat_id: F(null, "Chat of the update, or None."),
      api: F("BotApi", "Any Bot API method: await ctx.api.send_message(chat_id=..., text=...)."),
      files: F("Files", "Files in this bot's folder."),
      kv: F("KV", "Per-bot storage of JSON values."),
      tasks: F("Tasks", "Background tasks of this bot."),
      secrets: F(null, "Secrets from the Settings tab: ctx.secrets[\"NAME\"]."),
      http: F(null, "httpx.AsyncClient for outside requests."),
      handler: F(null, "id and name of the running handler."),
      task: F("TaskInfo", "The current background task (id, title), or None in a handler."),
      dry_run: F(null, "True during a dry test run."),
      reply: M("reply(${1:text})", "await ctx.reply(text, **params) — send a message to the chat of the update.", "Message"),
      reply_file: M("reply_file(${1:path}, caption=${2:None})", "await ctx.reply_file(path, caption=None, kind=\"auto\") — send a file: video, audio, photo, animation or document.", "Message"),
      answer_callback: M("answer_callback(${1:text})", "await ctx.answer_callback(text) — answer the button press of this update."),
      background: M("background(${1:func}, ${2:args}, title=${3:\"title\"})", "task_id = await ctx.background(func, *args, title=...) — run func(ctx, *args) as a background task. func must be defined at the top level."),
      progress: M("progress(${1:text})", "await ctx.progress(text) — progress line for the current task on the Tasks tab."),
      download: M("download(${1:url})", "result = await ctx.download(url, folder=\"downloads\", audio_only=False, format=None, max_size_mb=2000, cookies=None, status=True, messages=None) — download with yt-dlp.", "Download"),
      log: M("log(${1:value})", "ctx.log(*values) — write a line to the update log."),
      shell: M("shell(${1:\"program\"}, ${2:arg})", "result = await ctx.shell(*args, timeout=None, cwd=None) — run a program in the bot folder.", "ShellResult"),
    },
    Files: {
      root: F(null, "Path of the bot folder."),
      local_path: M("local_path(${1:file_id})", "path = await ctx.files.local_path(file_id) — download a Telegram file through the local Bot API server."),
      move: M("move(${1:source}, ${2:\"inbox/name\"})", "ctx.files.move(source, destination, overwrite=False) — move a file into the bot folder; returns the final path."),
      copy: M("copy(${1:source}, ${2:\"inbox/name\"})", "ctx.files.copy(source, destination, overwrite=False)."),
      path: M("path(${1:\"relative\"})", "Path inside the bot folder."),
      safe_name: M("safe_name(${1:name})", "Make a string usable as a file name."),
      write_text: M("write_text(${1:\"name.txt\"}, ${2:text})", "Write a text file into the bot folder."),
      write_bytes: M("write_bytes(${1:\"name.bin\"}, ${2:data})", "Write a binary file into the bot folder."),
      read_text: M("read_text(${1:\"name.txt\"})", "Read a text file."),
      read_bytes: M("read_bytes(${1:\"name.bin\"})", "Read a binary file."),
      list: M("list(${1:\".\"})", "List a folder inside the bot folder."),
      exists: M("exists(${1:\"name\"})", "Whether a path exists in the bot folder."),
    },
    KV: {
      get: M("get(${1:\"key\"}, ${2:None})", "value = await ctx.kv.get(key, default=None)."),
      set: M("set(${1:\"key\"}, ${2:value})", "await ctx.kv.set(key, value) — any JSON value."),
      delete: M("delete(${1:\"key\"})", "await ctx.kv.delete(key)."),
    },
    Tasks: {
      get: M("get(${1:task_id})", "task = await ctx.tasks.get(task_id) — id, title, status, progress, error."),
      cancel: M("cancel(${1:task_id})", "await ctx.tasks.cancel(task_id) — False if it already finished."),
    },
    TaskInfo: { id: F(null, "Task id."), title: F(null, "Task title.") },
    BotInfo: { id: F(null, "Bot id."), username: F(null, "Bot username.") },
    Download: {
      path: F(null, "Path of the downloaded file."),
      title: F(null, "Title of the media."),
      url: F(null, "The URL that was downloaded."),
      size: F(null, "Size in bytes."),
      message_id: F(null, "Id of the status message in the chat."),
      info: F(null, "Trimmed yt-dlp info: id, title, uploader, duration, webpage_url..."),
    },
    ShellResult: { returncode: F(null, "Exit code."), stdout: F(null, "Output."), stderr: F(null, "Errors.") },
    Update: {
      update_id: F(null, "Update id."),
      message: F("Message", "New incoming message."),
      edited_message: F("Message", "Edited message."),
      channel_post: F("Message", "New channel post."),
      callback_query: F("CallbackQuery", "Inline button press."),
      inline_query: F(null, "Inline query."),
      my_chat_member: F(null, "The bot's member status changed in a chat."),
      schedule: F("Schedule", "Present when a schedule started this run."),
      to_dict: M("to_dict()", "The raw update JSON."),
    },
    Schedule: {
      id: F(null, "Schedule id."),
      name: F(null, "Schedule name."),
      cron: F(null, "Cron expression."),
      time: F(null, "When it fired, ISO format, in the server timezone."),
      manual: F(null, "True when started with Run now."),
      chat: F("Chat", "Chat set on the schedule, if any."),
    },
    Message: {
      message_id: F(null, "Message id."),
      from_: F("User", "Sender (the JSON field is `from`)."),
      chat: F("Chat", "Chat the message belongs to."),
      date: F(null, "Unix time."),
      text: F(null, "Text of a text message."),
      caption: F(null, "Caption of a media message."),
      entities: F(null, "Special entities in the text (links, mentions...)."),
      document: F("Document", "A general file."),
      photo: F("PhotoSize", "List of sizes; the last one is the largest: msg.photo[-1]."),
      video: F("Video", "A video."),
      audio: F("Audio", "An audio file."),
      voice: F("Voice", "A voice message."),
      animation: F("Video", "A GIF or silent video."),
      sticker: F(null, "A sticker."),
      location: F(null, "latitude and longitude."),
      contact: F(null, "A shared contact."),
      reply_to_message: F("Message", "The message this one replies to."),
      forward_origin: F(null, "Where a forwarded message came from."),
      media_group_id: F(null, "Album id for grouped media."),
    },
    User: {
      id: F(null, "User id."), is_bot: F(null, "Whether it is a bot."), first_name: F(null, "First name."),
      last_name: F(null, "Last name."), username: F(null, "Username without @."), language_code: F(null, "IETF language tag."),
    },
    Chat: { id: F(null, "Chat id."), type: F(null, "private, group, supergroup or channel."), title: F(null, "Title of a group or channel."), username: F(null, "Username.") },
    Document: { file_id: F(null, "Pass to ctx.files.local_path."), file_unique_id: F(null, "Stable id."), file_name: F(null, "Original file name."), mime_type: F(null, "MIME type."), file_size: F(null, "Size in bytes.") },
    PhotoSize: { file_id: F(null, "Pass to ctx.files.local_path."), file_unique_id: F(null, "Stable id."), width: F(null, "Width."), height: F(null, "Height."), file_size: F(null, "Size in bytes.") },
    Video: { file_id: F(null, "Pass to ctx.files.local_path."), file_unique_id: F(null, "Stable id."), file_name: F(null, "File name."), duration: F(null, "Seconds."), width: F(null, "Width."), height: F(null, "Height."), mime_type: F(null, "MIME type."), file_size: F(null, "Size in bytes.") },
    Audio: { file_id: F(null, "Pass to ctx.files.local_path."), file_unique_id: F(null, "Stable id."), file_name: F(null, "File name."), duration: F(null, "Seconds."), performer: F(null, "Performer."), title: F(null, "Title."), file_size: F(null, "Size in bytes.") },
    Voice: { file_id: F(null, "Pass to ctx.files.local_path."), duration: F(null, "Seconds."), file_size: F(null, "Size in bytes.") },
    CallbackQuery: { id: F(null, "Query id."), from_: F("User", "Who pressed the button."), message: F("Message", "Message with the button."), data: F(null, "callback_data of the button."), answer: F(null, "Use ctx.answer_callback(text).") },
  };

  const API_METHODS = {
    send_message: "chat_id=${1:ctx.chat_id}, text=${2:text}",
    send_document: "chat_id=${1:ctx.chat_id}, document=${2:path}",
    send_photo: "chat_id=${1:ctx.chat_id}, photo=${2:path}",
    send_video: "chat_id=${1:ctx.chat_id}, video=${2:path}",
    send_audio: "chat_id=${1:ctx.chat_id}, audio=${2:path}",
    send_voice: "chat_id=${1:ctx.chat_id}, voice=${2:path}",
    send_animation: "chat_id=${1:ctx.chat_id}, animation=${2:path}",
    send_media_group: "chat_id=${1:ctx.chat_id}, media=${2:[]}",
    send_location: "chat_id=${1:ctx.chat_id}, latitude=${2:0}, longitude=${3:0}",
    send_chat_action: "chat_id=${1:ctx.chat_id}, action=${2:\"typing\"}",
    edit_message_text: "chat_id=${1:ctx.chat_id}, message_id=${2:message_id}, text=${3:text}",
    edit_message_reply_markup: "chat_id=${1:ctx.chat_id}, message_id=${2:message_id}, reply_markup=${3:{}}",
    delete_message: "chat_id=${1:ctx.chat_id}, message_id=${2:message_id}",
    forward_message: "chat_id=${1:chat_id}, from_chat_id=${2:ctx.chat_id}, message_id=${3:message_id}",
    copy_message: "chat_id=${1:chat_id}, from_chat_id=${2:ctx.chat_id}, message_id=${3:message_id}",
    set_message_reaction: "chat_id=${1:ctx.chat_id}, message_id=${2:message_id}, reaction=${3:[{\"type\": \"emoji\", \"emoji\": \"👍\"}]}",
    answer_callback_query: "callback_query_id=${1:query_id}, text=${2:text}",
    get_file: "file_id=${1:file_id}",
    get_me: "",
    get_chat: "chat_id=${1:chat_id}",
    get_chat_member: "chat_id=${1:chat_id}, user_id=${2:user_id}",
    set_my_commands: "commands=${1:[]}",
    get_my_commands: "",
    pin_chat_message: "chat_id=${1:ctx.chat_id}, message_id=${2:message_id}",
  };
  const CONTEXT_TYPES = { Context: 1 };

  function rootType(name) {
    if (name === "ctx") return "Context";
    if (name === "update") return "Update";
    if (/^(msg|message|reply|sent)$/.test(name)) return "Message";
    if (/^(query|cb|callback)$/.test(name)) return "CallbackQuery";
    if (/^(doc|document)$/.test(name)) return "Document";
    if (/^(result|download|dl)$/.test(name)) return "Download";
    if (name === "schedule") return "Schedule";
    if (/^(user|sender)$/.test(name)) return "User";
    if (name === "chat") return "Chat";
    return null;
  }

  function resolve(chain) {
    const parts = chain.split(".");
    let type = rootType(parts.shift());
    for (const part of parts) {
      if (!type) return null;
      if (type === "BotApi") return null;
      const member = (TYPES[type] || {})[part];
      type = member ? member.type : null;
    }
    return type;
  }

  window.registerSdkHints = function (monaco) {
    const Kind = monaco.languages.CompletionItemKind;
    const Snippet = monaco.languages.CompletionItemInsertTextRule.InsertAsSnippet;

    monaco.languages.registerCompletionItemProvider("python", {
      triggerCharacters: ["."],
      provideCompletionItems(model, position) {
        const word = model.getWordUntilPosition(position);
        const range = {
          startLineNumber: position.lineNumber,
          endLineNumber: position.lineNumber,
          startColumn: word.startColumn,
          endColumn: word.endColumn,
        };
        const before = model.getLineContent(position.lineNumber).slice(0, word.startColumn - 1);
        const match = before.replace(/\[[^\]]*\]/g, "").match(/([A-Za-z_][\w]*(?:\.[A-Za-z_]\w*)*)\.$/);
        if (!match) {
          if (before.trim() !== "") return { suggestions: [] };
          return { suggestions: snippets(range) };
        }
        const chain = match[1];
        if (chain === "ctx.api" || chain.endsWith(".api")) {
          return {
            suggestions: Object.entries(API_METHODS).map(([name, args]) => ({
              label: name,
              kind: Kind.Method,
              insertText: `${name}(${args})`,
              insertTextRules: Snippet,
              detail: `await ctx.api.${name}(...)`,
              documentation: "Bot API method; any other method name works too (snake_case or camelCase).",
              range,
            })),
          };
        }
        const type = resolve(chain);
        if (!type || !TYPES[type]) return { suggestions: [] };
        return {
          suggestions: Object.entries(TYPES[type]).map(([name, member]) => ({
            label: name,
            kind: member.kind === "method" ? Kind.Method : Kind.Field,
            insertText: member.insert || name,
            insertTextRules: member.insert ? Snippet : undefined,
            detail: member.type ? `${type}.${name}: ${member.type}` : `${type}.${name}`,
            documentation: member.doc,
            sortText: (CONTEXT_TYPES[type] && member.kind === "method" ? "0" : "1") + name,
            range,
          })),
        };
      },
    });

    function snippets(range) {
      const items = [
        ["handle", "async def handle(update, ctx):\n    ${1:pass}", "The handler entry point."],
        ["background task", "async def ${1:work}(ctx, ${2:value}):\n    \"\"\"Runs as a background task.\"\"\"\n    ${3:pass}", "A function to start with ctx.background(...)."],
        ["download and send", "result = await ctx.download(${1:url})\nawait ctx.reply_file(result.path, caption=result.title)", "Download with yt-dlp and send the file."],
        ["save incoming file", "path = await ctx.files.local_path(${1:update.message.document}.file_id)\nsaved = ctx.files.move(path, f\"inbox/{ctx.files.safe_name(${1:update.message.document}.file_name)}\")", "Save a file from a message to the bot folder."],
        ["inline button", "reply_markup={\"inline_keyboard\": [[{\"text\": \"${1:Label}\", \"callback_data\": \"${2:data}\"}]]}", "Keyboard argument for ctx.reply."],
      ];
      return items.map(([label, text, doc]) => ({
        label,
        kind: monaco.languages.CompletionItemKind.Snippet,
        insertText: text,
        insertTextRules: Snippet,
        documentation: doc,
        range,
      }));
    }
  };

  // Exposed for tests and debugging.
  window.__sdkHints = { TYPES, resolve };
})();
