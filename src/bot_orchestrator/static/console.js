// Bot API console.

(function () {
  const form = document.getElementById("console-form");
  const method = document.getElementById("method");
  const params = document.getElementById("params");
  const output = document.getElementById("console-result");
  const send = document.getElementById("send");

  function show(response) {
    const ok = response.ok ? "ok" : "error";
    output.innerHTML = `<p><span class="badge badge-${ok}">${response.ok ? "ok" : "failed"}</span></p>` +
      `<pre class="code">${escapeHtml(JSON.stringify(response, null, 2))}</pre>`;
  }

  form.addEventListener("submit", async function (event) {
    event.preventDefault();
    let parsed;
    try {
      parsed = JSON.parse(params.value.trim() || "{}");
    } catch (err) {
      output.innerHTML = `<div class="flash flash-error">Parameters are not valid JSON: ${escapeHtml(err.message)}</div>`;
      return;
    }
    send.disabled = true;
    output.innerHTML = '<p class="muted">Sending…</p>';
    try {
      show(await postJson(`/bots/${window.BOT_ID}/console`, { method: method.value, params: parsed }));
    } catch (err) {
      output.innerHTML = `<div class="flash flash-error">${escapeHtml(err.message)}</div>`;
    } finally {
      send.disabled = false;
    }
  });

  document.querySelectorAll("[data-method]").forEach(function (button) {
    button.addEventListener("click", function () {
      method.value = button.dataset.method;
      params.value = "{}";
    });
  });

  document.querySelectorAll("[data-load-method]").forEach(function (button) {
    button.addEventListener("click", function () {
      method.value = button.dataset.loadMethod;
      params.value = JSON.stringify(JSON.parse(button.dataset.loadParams), null, 2);
      show(JSON.parse(button.dataset.response));
      window.scrollTo({ top: 0, behavior: "smooth" });
    });
  });
})();
