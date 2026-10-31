// Handler editor: Monaco when the CDN is reachable, a plain textarea otherwise.

(function () {
  const textarea = document.getElementById("code");
  const editorBox = document.getElementById("editor");
  let editor = null;

  function currentCode() {
    return editor ? editor.getValue() : textarea.value;
  }

  if (window.require && window.require.config) {
    window.require.config({
      paths: { vs: "https://cdnjs.cloudflare.com/ajax/libs/monaco-editor/0.52.2/min/vs" },
    });
    window.require(["vs/editor/editor.main"], function () {
      const dark = window.matchMedia("(prefers-color-scheme: dark)").matches;
      editor = monaco.editor.create(editorBox, {
        value: textarea.value,
        language: "python",
        theme: dark ? "vs-dark" : "vs",
        automaticLayout: true,
        minimap: { enabled: false },
        fontSize: 14,
        tabSize: 4,
        insertSpaces: true,
        scrollBeyondLastLine: false,
      });
      textarea.classList.add("hidden");
      editorBox.classList.add("ready");
      editor.addCommand(monaco.KeyMod.CtrlCmd | monaco.KeyCode.KeyS, function () {
        document.getElementById("save-form").requestSubmit();
      });
    }, function () { /* CDN failed: keep the textarea */ });
  }

  document.getElementById("save-form").addEventListener("submit", function () {
    textarea.value = currentCode();
  });

  const select = document.getElementById("test-update");
  const jsonBox = document.getElementById("test-json");
  select.addEventListener("change", function () {
    jsonBox.classList.toggle("hidden", select.value !== "json");
  });

  const result = document.getElementById("test-result");
  const button = document.getElementById("test-run");
  button.addEventListener("click", async function () {
    const body = { code: currentCode(), dry_run: document.getElementById("test-dry").checked };
    if (select.value === "json") body.update_json = jsonBox.value;
    else body.update_row = Number(select.value);
    if (!body.dry_run && !confirm("Not a dry run: the handler will really send messages. Continue?")) {
      return;
    }
    button.disabled = true;
    result.innerHTML = '<p class="muted">Running…</p>';
    try {
      result.innerHTML = renderRunResult(await postJson(`/bots/${window.BOT_ID}/handler/test`, body));
    } catch (err) {
      result.innerHTML = `<div class="flash flash-error">${escapeHtml(err.message)}</div>`;
    } finally {
      button.disabled = false;
    }
  });
})();
