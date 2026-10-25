const toast = (message, kind = "info") => {
  const node = document.getElementById("toast");
  if (!node) return;
  node.textContent = message;
  node.className = `toast show ${kind}`;
  window.clearTimeout(toast.timer);
  toast.timer = window.setTimeout(() => {
    node.className = "toast";
  }, 5200);
};

const json = async (response) => {
  const body = await response.json().catch(() => ({}));
  if (!response.ok) throw new Error(body.error || `Request refused (${response.status})`);
  return body;
};

document.addEventListener("click", async (event) => {
  const target = event.target.closest("[data-refund],[data-approve],[data-sync],[data-merge],[data-review]");
  if (!target) return;

  try {
    if (target.dataset.refund) {
      const id = target.dataset.refund;
      const value = Number(target.dataset.value || 0);
      const reason = window.prompt(
        value >= 20000
          ? "This refund is €200 or more. Write the reason:"
          : "Refund reason (optional):",
        "",
      );
      if (reason === null) return;
      toast(`Refunding ${id}…`);
      await json(await fetch(`/api/orders/${id}/refund`, {
        method: "POST",
        headers: { "content-type": "application/json" },
        body: JSON.stringify({ reason }),
      }));
      toast(`${id} refunded.`, "ok");
      window.setTimeout(() => window.location.reload(), 900);
      return;
    }

    if (target.dataset.approve) {
      toast(`Approving ${target.dataset.approve}…`);
      await json(await fetch(`/factory/missions/${target.dataset.approve}/approve`, {
        method: "POST",
        headers: { "content-type": "application/json" },
        body: "{}",
      }));
      toast("Checkpoint approved. The Gate will seal it.", "ok");
      window.setTimeout(() => window.location.reload(), 1400);
      return;
    }

    if (target.hasAttribute("data-sync")) {
      toast("Syncing sealed missions…");
      const body = await json(await fetch("/sync", {
        method: "POST",
        headers: { "content-type": "application/json" },
        body: JSON.stringify({ autoMerge: target.dataset.autoMerge === "true" }),
      }));
      const extra = body.merged ? `, ${body.merged} auto-merged` : "";
      toast(`Synced ${body.created} pull request(s)${extra}${body.blocked ? `, ${body.blocked} blocked` : ""}.`, body.blocked ? "warn" : "ok");
      if (body.created || body.merged) window.setTimeout(() => window.location.reload(), 1200);
      return;
    }

    if (target.dataset.merge) {
      const id = target.dataset.merge;
      if (!window.confirm(`Merge ${id}? The review and checks are already recorded.`)) return;
      toast(`Merging ${id}…`);
      await json(await fetch(`/api/pulls/${id}/merge`, {
        method: "POST",
        headers: { "content-type": "application/json" },
        body: JSON.stringify({ mergedBy: "operator" }),
      }));
      toast(`${id} merged.`, "ok");
      window.setTimeout(() => window.location.reload(), 900);
      return;
    }

    if (target.dataset.review) {
      const comment = window.prompt("What must change before this can merge?", "");
      if (comment === null) return;
      await json(await fetch(`/api/pulls/${target.dataset.review}/review`, {
        method: "POST",
        headers: { "content-type": "application/json" },
        body: JSON.stringify({ verdict: "CHANGES_REQUESTED", comment, reviewer: "operator" }),
      }));
      toast("Changes requested.", "warn");
      window.setTimeout(() => window.location.reload(), 900);
    }
  } catch (error) {
    toast(error.message || String(error), "bad");
  }
});

const form = document.getElementById("ticket-form");
if (form) {
  form.addEventListener("submit", async (event) => {
    event.preventDefault();
    const data = new FormData(form);
    const note = document.getElementById("ticket-result");
    const payload = {
      title: data.get("title"),
      outcome: data.get("outcome"),
      area: data.get("area"),
      risk: data.get("risk"),
      acceptance: data.get("acceptance"),
      constraints: data.get("constraints") || "",
      product: data.get("product") || "product",
    };
    if (note) {
      note.textContent = "Admitting the ticket and starting the mission…";
      note.className = "form-note busy";
    }
    try {
      const body = await json(await fetch("/factory/parceldesk/tickets", {
        method: "POST",
        headers: { "content-type": "application/json" },
        body: JSON.stringify(payload),
      }));
      if (note) {
        note.textContent = `${body.ticket.id} admitted as ${body.mission.id}. The Factory is running.`;
        note.className = "form-note ok";
      }
      toast(`${body.mission.id} queued.`, "ok");
      window.setTimeout(() => window.location.reload(), 1600);
    } catch (error) {
      if (note) {
        note.textContent = error.message || String(error);
        note.className = "form-note bad";
      }
      toast(error.message || String(error), "bad");
    }
  });
}

const auto = document.querySelector("[data-auto-sync]");
if (auto) {
  window.setInterval(async () => {
    if (!auto.checked) return;
    try {
      const response = await fetch("/sync", {
        method: "POST",
        headers: { "content-type": "application/json" },
        body: JSON.stringify({ autoMerge: true }),
      });
      const body = await response.json();
      if (body.merged > 0) {
        toast(`${body.merged} low-risk pull request(s) auto-merged.`, "ok");
        window.setTimeout(() => window.location.reload(), 1200);
      }
    } catch {
      // The next interval or a manual action will report the failure.
    }
  }, 15000);
}
