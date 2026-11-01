// Schedules: preview the next runs of a cron expression while typing.

(function () {
  document.querySelectorAll(".schedule-form").forEach(function (form) {
    const input = form.querySelector(".cron-input");
    const preview = form.querySelector(".cron-preview");
    const original = preview.innerHTML;
    let timer = null;

    async function update() {
      const expression = input.value.trim();
      if (!expression) {
        preview.innerHTML = original;
        return;
      }
      try {
        const url = `/bots/${window.BOT_ID}/schedules-preview?expression=${encodeURIComponent(expression)}`;
        const response = await fetch(url);
        const data = await response.json();
        preview.textContent = data.ok ? `Next: ${data.runs.join(" · ")}` : data.error;
        preview.classList.toggle("text-error", !data.ok);
      } catch {
        preview.innerHTML = original;
      }
    }

    input.addEventListener("input", function () {
      clearTimeout(timer);
      timer = setTimeout(update, 300);
    });
    update();
  });
})();
