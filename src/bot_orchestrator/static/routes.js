// Routes: drag-and-drop ordering, pattern help, and "which route matches".

(function () {
  // Pattern help and placeholder follow the chosen condition.
  document.querySelectorAll(".route-form").forEach(function (form) {
    const kind = form.querySelector(".route-kind");
    const pattern = form.querySelector(".route-pattern");
    const help = form.querySelector(".route-help");
    function update() {
      help.textContent = (window.PATTERN_HELP || {})[kind.value] || "";
      pattern.disabled = kind.value === "any";
      pattern.required = ["command", "regex", "content", "update"].includes(kind.value);
    }
    kind.addEventListener("change", update);
    update();
  });

  const table = document.getElementById("routes");
  if (table) {
    const body = table.querySelector("tbody");
    let dragged = null;

    function renumber() {
      body.querySelectorAll("tr").forEach(function (row, i) {
        row.querySelector(".pos").textContent = i + 1;
      });
    }

    async function save() {
      renumber();
      const ids = Array.from(body.querySelectorAll("tr")).map((r) => Number(r.dataset.id));
      try {
        await postJson(`/bots/${window.BOT_ID}/routes-order`, { ids: ids });
      } catch (err) {
        alertBox(table, `Could not save the order: ${err.message}`);
      }
    }

    body.addEventListener("dragstart", function (event) {
      dragged = event.target.closest("tr");
      dragged.classList.add("dragging");
      event.dataTransfer.effectAllowed = "move";
    });
    body.addEventListener("dragend", function () {
      if (dragged) dragged.classList.remove("dragging");
      dragged = null;
      save();
    });
    body.addEventListener("dragover", function (event) {
      event.preventDefault();
      const row = event.target.closest("tr");
      if (!dragged || !row || row === dragged) return;
      const box = row.getBoundingClientRect();
      const after = event.clientY > box.top + box.height / 2;
      body.insertBefore(dragged, after ? row.nextSibling : row);
    });

    // Keyboard and touch friendly alternative to dragging.
    body.querySelectorAll(".move").forEach(function (button) {
      button.addEventListener("click", function () {
        const row = button.closest("tr");
        if (button.dataset.dir === "-1" && row.previousElementSibling) {
          body.insertBefore(row, row.previousElementSibling);
        } else if (button.dataset.dir === "1" && row.nextElementSibling) {
          body.insertBefore(row.nextElementSibling, row);
        } else {
          return;
        }
        save();
        button.focus();
      });
    });
  }

  const matchButton = document.getElementById("match-run");
  if (matchButton) {
    matchButton.addEventListener("click", async function () {
      const result = document.getElementById("match-result");
      document.querySelectorAll("#routes tr.matched").forEach((r) => r.classList.remove("matched"));
      try {
        const data = await postJson(`/bots/${window.BOT_ID}/routes-match`, {
          update_row: Number(document.getElementById("match-update").value),
        });
        result.textContent = data.text;
        if (data.route_id) {
          const row = document.querySelector(`#routes tr[data-id="${data.route_id}"]`);
          if (row) row.classList.add("matched");
        }
      } catch (err) {
        result.textContent = err.message;
      }
    });
  }
})();
