// Code editor pages (handlers and shared modules): Monaco when the CDN is reachable,
// a plain textarea otherwise, plus the test-run panel.
// The page sets window.CODE_PAGE = { testUrl, extra?: () => object, confirmText? }.

(function () {
  const textarea = document.getElementById("code");
  const editorBox = document.getElementById("editor");
  const page = window.CODE_PAGE || {};
  let editor = null;

  function currentCode() {
    return editor ? editor.getValue() : textarea.value;
  }

  // Warn before leaving with unsaved changes.
  let saved = textarea.value;
  window.addEventListener("beforeunload", function (event) {
    if (currentCode() !== saved) event.preventDefault();
  });

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
      if (window.registerSdkHints) window.registerSdkHints(monaco);
      editor.addCommand(monaco.KeyMod.CtrlCmd | monaco.KeyCode.KeyS, function () {
        document.getElementById("save-form").requestSubmit();
      });
    }, function () { /* CDN failed: keep the textarea */ });
  }

  document.getElementById("save-form").addEventListener("submit", function () {
    textarea.value = currentCode();
    saved = textarea.value;
  });

  const select = document.getElementById("test-update");
  const jsonBox = document.getElementById("test-json");
  if (!select || !page.testUrl) return;
  select.addEventListener("change", function () {
    jsonBox.classList.toggle("hidden", select.value !== "json");
  });

  const result = document.getElementById("test-result");
  const button = document.getElementById("test-run");
  button.addEventListener("click", async function () {
    const body = {
      code: currentCode(),
      dry_run: document.getElementById("test-dry").checked,
      ...(page.extra ? page.extra() : {}),
    };
    if (select.value === "json") body.update_json = jsonBox.value;
    else body.update_row = Number(select.value);
    const warning = page.confirmText || "Not a dry run: the handler will really send messages. Continue?";
    if (!body.dry_run && !confirm(warning)) return;
    button.disabled = true;
    result.innerHTML = '<p class="muted">Running…</p>';
    try {
      result.innerHTML = renderRunResult(await postJson(page.testUrl, body));
    } catch (err) {
      result.innerHTML = `<div class="flash flash-error">${escapeHtml(err.message)}</div>`;
    } finally {
      button.disabled = false;
    }
  });
})();
