// Shared helpers for the orchestrator pages.

function escapeHtml(text) {
  return String(text ?? "").replace(/[&<>"']/g, (c) => ({
    "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;",
  })[c]);
}

async function postJson(url, body) {
  const response = await fetch(url, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  let data;
  try {
    data = await response.json();
  } catch {
    data = { detail: `HTTP ${response.status}` };
  }
  if (!response.ok) {
    throw new Error(typeof data.detail === "string" ? data.detail : JSON.stringify(data.detail));
  }
  return data;
}

function renderRunResult(result) {
  const badge = `<span class="badge badge-${escapeHtml(result.status)}">${escapeHtml(result.status.replace("_", " "))}</span>`;
  let html = `<p>${badge} <span class="muted">${result.duration_ms ?? "?"} ms</span></p>`;
  if (result.error) html += `<pre class="code error">${escapeHtml(result.error)}</pre>`;
  if (result.logs && result.logs.length) {
    html += `<h3>Log</h3><pre class="code">${escapeHtml(result.logs.join("\n"))}</pre>`;
  }
  if (result.api_calls && result.api_calls.length) {
    html += "<h3>Bot API calls</h3><table class='table compact'><tbody>";
    for (const c of result.api_calls) {
      let state = c.dry_run ? "dry run" : c.ok ? "ok" : `failed: ${c.error}`;
      if (c.task_id) state += ` · task #${c.task_id}`;
      html += `<tr><td><code>${escapeHtml(c.method)}</code></td>` +
        `<td><pre class="inline">${escapeHtml(JSON.stringify(c.params, null, 2))}</pre></td>` +
        `<td class="small">${escapeHtml(state)}</td></tr>`;
    }
    html += "</tbody></table>";
  }
  return html;
}

// <form data-confirm="Question?"> asks before submitting.
document.addEventListener("submit", function (event) {
  const message = event.target.dataset && event.target.dataset.confirm;
  if (message && !confirm(message)) event.preventDefault();
}, true);

// Show an error message right after an element.
function alertBox(anchor, message) {
  const box = document.createElement("div");
  box.className = "flash flash-error";
  box.textContent = message;
  anchor.insertAdjacentElement("afterend", box);
}

// Rows with data-href open their page when clicked anywhere.
document.addEventListener("click", function (event) {
  const row = event.target.closest("tr[data-href]");
  if (row && !event.target.closest("a, button, input, select, form")) {
    location.href = row.dataset.href;
  }
});

// <span id="auto-refresh" data-seconds="3"> reloads the page while it is visible.
(function () {
  const marker = document.getElementById("auto-refresh");
  if (!marker) return;
  const seconds = Number(marker.dataset.seconds || 3);
  setInterval(function () {
    if (document.visibilityState === "visible" && !document.querySelector("input:focus, select:focus")) {
      location.reload();
    }
  }, seconds * 1000);
})();
