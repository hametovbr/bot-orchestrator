// Module page: the update list follows the bot of the chosen handler.

(function () {
  const handler = document.getElementById("test-handler");
  const updates = document.getElementById("test-update");
  const jsonBox = document.getElementById("test-json");
  if (!handler || !updates) return;

  async function load() {
    const bot = handler.selectedOptions[0].dataset.bot;
    updates.innerHTML = '<option value="json">Custom JSON…</option>';
    try {
      const response = await fetch(`/bots/${bot}/updates.json?limit=30`);
      const rows = await response.json();
      for (const row of rows.reverse()) {
        const option = document.createElement("option");
        option.value = row.id;
        option.textContent = row.label;
        updates.prepend(option);
      }
      updates.selectedIndex = 0;
    } catch {
      /* keep only the custom JSON option */
    }
    jsonBox.classList.toggle("hidden", updates.value !== "json");
  }

  handler.addEventListener("change", load);
  load();
})();
