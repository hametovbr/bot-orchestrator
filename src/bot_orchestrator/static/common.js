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
      const state = c.dry_run ? "dry run" : c.ok ? "ok" : `failed: ${c.error}`;
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
