/*
 * ParcelDesk — client surface.
 * Live quote contract + read-only projection of the current PD01 repair.
 */
(function () {
  "use strict";

  const $ = function (selector, root) {
    return (root || document).querySelector(selector);
  };

  const state = {
    snapshot: null,
    bugExpanded: false,
    offline: false,
    toastTimer: null,
    pollFailures: 0,
  };

  function escapeHtml(value) {
    return String(value === undefined || value === null ? "" : value)
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;")
      .replace(/'/g, "&#039;");
  }

  function groupGrams(value) {
    return String(value).replace(/\B(?=(\d{3})+(?!\d))/g, " ");
  }

  function cents(value) {
    return (Number(value) / 100).toFixed(2).replace(".", ",") + " €";
  }

  function toast(message) {
    const node = $("#client-toast");
    if (!node) return;
    node.textContent = message;
    node.classList.add("show");
    window.clearTimeout(state.toastTimer);
    state.toastTimer = window.setTimeout(function () {
      node.classList.remove("show");
    }, 3400);
  }

  function currentGrams() {
    const input = $("#grams");
    if (!input) return null;
    const raw = String(input.value).trim();
    if (!raw) return null;
    const value = Number(raw);
    if (!Number.isInteger(value) || value < 1 || value > 30000) return null;
    return value;
  }

  function renderQuote(data) {
    const euros = $("#quote-euros");
    if (euros) euros.textContent = data.euros;
    const grams = $("#quote-grams");
    if (grams) grams.textContent = "for " + groupGrams(data.grams) + " g";

    const breakdown = $("#quote-breakdown");
    if (breakdown) {
      breakdown.innerHTML = data.breakdown
        .map(function (row) {
          return "<div><span>" + escapeHtml(row.label) + "</span><strong>" + cents(row.cents) + "</strong></div>";
        })
        .join("");
    }

    const rule = $("#quote-rule");
    if (rule) rule.textContent = data.rule + " · 1 g to 30 kg";

    if (state.bugExpanded) refreshBugPreview();
  }

  function renderBugPreview(data, normal) {
    const grams = $("#buggy-grams");
    if (grams) grams.textContent = groupGrams(data.grams) + " g";
    const price = $("#buggy-price");
    if (price) price.textContent = data.euros;
    const explain = $("#buggy-explain");
    if (explain) {
      explain.textContent =
        normal && normal.cents === data.cents
          ? "— the old code happened to agree at a whole kilogram. Try 1 200 g to see the 5,00 € bug."
          : "because the sandbox baseline rounds down. Repair acceptance appears in the live panel.";
    }
  }

  async function requestQuote(options) {
    const opts = options || {};
    const grams = currentGrams();
    if (grams === null) {
      toast("Enter a whole number of grams between 1 and 30 000.");
      return null;
    }

    try {
      const response = await fetch(
        "/api/quote?grams=" + encodeURIComponent(grams) + "&buggy=" + (opts.buggy ? "true" : "false"),
        { cache: "no-store" }
      );
      const data = await response.json();
      if (!data.ok) {
        toast(data.error || "This weight is outside the pricing contract.");
        return null;
      }
      return data;
    } catch (error) {
      if (!opts.silent) toast("Quote service unavailable — retrying on the next click.");
      return null;
    }
  }

  async function updateQuote() {
    const data = await requestQuote();
    if (data) renderQuote(data);
  }

  async function refreshBugPreview() {
    const results = await Promise.all([
      requestQuote({ buggy: true, silent: true }),
      requestQuote({ silent: true }),
    ]);
    const data = results[0];
    const normal = results[1];
    if (data) renderBugPreview(data, normal);
  }

  function applySnapshot(snapshot) {
    const client = snapshot.client;
    if (!client) return;
    state.snapshot = snapshot;

    const title = $("#client-mission-title");
    if (title) title.textContent = (client.mission_id ? client.fixture + " · " : "") + client.title;

    const status = $("#client-status");
    if (status) {
      const labels = {
        running: "in progress",
        awaiting_human: "awaiting owner",
        completed: client.outcome === "assisted" ? "verified · assisted" : "verified",
        blocked: "blocked",
      };
      status.textContent = labels[client.status] || client.status;
      status.setAttribute("data-status", client.status);
    }

    const progress = $("#client-progress-bar");
    if (progress) progress.style.width = Math.round((Number(client.progress) || 0) * 100) + "%";

    const identifier = $("#client-mission-id");
    if (identifier) identifier.textContent = client.mission_id || "—";

    const outcome = $("#client-outcome");
    if (outcome) outcome.textContent = client.outcome ? "outcome: " + client.outcome : "outcome pending";

    const checklist = $("#client-checklist");
    if (checklist) {
      checklist.innerHTML = client.checks
        .map(function (check) {
          const mark = check.status === "done" ? "✓" : check.status === "running" ? "›" : "";
          return (
            '<div class="repair-check ' +
            escapeHtml(check.status) +
            '" title="' +
            escapeHtml(check.detail) +
            '"><i>' +
            mark +
            "</i><span>" +
            escapeHtml(check.label) +
            "</span></div>"
          );
        })
        .join("");
    }

    const agents = $("#client-agents");
    if (agents) {
      agents.innerHTML = client.agents.length
        ? client.agents
            .map(function (agent) {
              return (
                '<span class="repair-agent"><i style="background:' +
                escapeHtml(agent.color) +
                '"></i>' +
                escapeHtml(agent.name) +
                " · " +
                escapeHtml(agent.status) +
                "</span>"
              );
            })
            .join("")
        : '<span class="repair-agent">Fleet standing by</span>';
    }

    const frameworks = $("#client-frameworks");
    if (frameworks) {
      frameworks.innerHTML = snapshot.frameworks
        .slice(0, 5)
        .map(function (framework) {
          return "<span>" + escapeHtml(framework.name.replace("Æion ", "")) + "</span>";
        })
        .join("");
    }
  }

  async function poll() {
    try {
      const response = await fetch("/api/snapshot", { cache: "no-store" });
      if (!response.ok) throw new Error("snapshot " + response.status);
      const snapshot = await response.json();
      applySnapshot(snapshot);
      state.pollFailures = 0;
      if (state.offline) {
        state.offline = false;
        toast("Live repair panel reconnected.");
      }
    } catch (error) {
      state.pollFailures += 1;
      if (state.pollFailures === 1) {
        state.offline = true;
        toast("Live repair panel offline — retrying.");
      }
    } finally {
      window.setTimeout(poll, 1200);
    }
  }

  function initialSnapshot() {
    const node = $("#initial-snapshot");
    if (!node) return null;
    try {
      return JSON.parse(node.textContent);
    } catch (error) {
      return null;
    }
  }

  function bindQuote() {
    const input = $("#grams");
    const button = $("#quote-button");

    if (button) button.addEventListener("click", updateQuote);
    if (input) {
      input.addEventListener("keydown", function (event) {
        if (event.key === "Enter") {
          event.preventDefault();
          updateQuote();
        }
      });
    }

    document.querySelectorAll(".quick-weights button").forEach(function (weight) {
      weight.addEventListener("click", function () {
        if (!input) return;
        input.value = weight.getAttribute("data-grams");
        updateQuote();
      });
    });

    const bugToggle = $("#bug-toggle");
    const bugNote = $("#bug-note");
    if (bugToggle && bugNote) {
      bugToggle.addEventListener("click", function () {
        state.bugExpanded = !state.bugExpanded;
        bugNote.hidden = !state.bugExpanded;
        bugToggle.textContent = state.bugExpanded ? "Hide the old behaviour ↑" : "See the rounding bug we fixed →";
        if (state.bugExpanded) refreshBugPreview();
      });
    }
  }

  function init() {
    bindQuote();
    const snapshot = initialSnapshot();
    if (snapshot) applySnapshot(snapshot);
    window.setTimeout(poll, 1200);
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", init);
  } else {
    init();
  }

  window.parcelDeskToast = toast;
})();
