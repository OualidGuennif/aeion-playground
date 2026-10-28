/*
 * Æion Mission Control — shared live client.
 * Polls the local snapshot contract and projects it into each page.
 */
(function () {
  "use strict";

  const state = {
    snapshot: null,
    timer: null,
    offline: false,
    toastTimer: null,
    graph: { digest: null, selected: "pricing" },
  };

  const $ = function (selector, root) {
    return (root || document).querySelector(selector);
  };

  function escapeHtml(value) {
    return String(value === undefined || value === null ? "" : value)
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;")
      .replace(/'/g, "&#039;");
  }

  function number(value) {
    return new Intl.NumberFormat("en-GB").format(Number(value) || 0);
  }

  function money(value, digits) {
    if (value === null || value === undefined || !isFinite(Number(value))) return "—";
    return "$" + Number(value).toFixed(digits === undefined ? 2 : digits);
  }

  function percent(value, digits) {
    if (value === null || value === undefined || !isFinite(Number(value))) return "—";
    return (Number(value) * 100).toFixed(digits === undefined ? 0 : digits) + "%";
  }

  function seconds(value) {
    if (value === null || value === undefined || !isFinite(Number(value))) return "—";
    const total = Number(value);
    if (total < 60) return total.toFixed(1) + "s";
    const minutes = Math.floor(total / 60);
    const rest = Math.round(total % 60);
    return minutes + "m " + String(rest).padStart(2, "0") + "s";
  }

  function tokens(value) {
    const total = Number(value) || 0;
    if (total >= 1000000) return (total / 1000000).toFixed(1) + "M";
    if (total >= 1000) return (total / 1000).toFixed(1) + "k";
    return String(total);
  }

  function clockTime(iso) {
    if (!iso) return "—";
    const date = new Date(iso);
    if (isNaN(date.getTime())) return "—";
    return date.toLocaleTimeString("en-GB", { hour12: false });
  }

  function toast(message) {
    const node = $("#toast");
    if (!node) return;
    node.textContent = message;
    node.classList.add("show");
    window.clearTimeout(state.toastTimer);
    state.toastTimer = window.setTimeout(function () {
      node.classList.remove("show");
    }, 3400);
  }

  const KPI_FORMAT = {
    active_agents: function (value) { return number(value); },
    total_agents: function (value) { return number(value); },
    running_missions: function (value) { return number(value); },
    completed: function (value) { return number(value); },
    autonomous: function (value) { return number(value); },
    assisted: function (value) { return number(value); },
    failed: function (value) { return number(value); },
    unknown: function (value) { return number(value); },
    success_rate: function (value) { return percent(value, 0); },
    cost_total: function (value) { return money(value, 2); },
    tokens_total: function (value) { return number(value); },
    cost_per_success: function (value) { return money(value, 2); },
    p95_seconds: function (value) { return seconds(value); },
    avg_seconds: function (value) { return seconds(value); },
    evidence_coverage: function (value) { return percent(value, 0); },
    gate_interventions: function (value) { return number(value); },
    human_approvals: function (value) { return number(value); },
  };

  const STATUS_LABEL = {
    running: "running",
    awaiting_human: "Needs your review",
    completed: "Completed",
    blocked: "blocked",
    queued: "queued",
  };

  const OUTCOME_BADGE = {
    autonomous: "badge-lime",
    assisted: "badge-cyan",
    failed: "badge-red",
    unknown: "badge-orange",
    pending: "badge-blue",
  };

  const GATE_BADGE = {
    allow: "badge-lime",
    allowed: "badge-lime",
    approved: "badge-lime",
    checkpoint: "badge-orange",
    hold: "badge-orange",
    deny: "badge-red",
    pending: "badge-blue",
  };

  const RISK_COLOR = {
    critical: "var(--danger)",
    high: "var(--warning)",
    normal: "var(--primary)",
    low: "var(--success)",
  };

  function meter(value, label, extraClass, maximum) {
    const max = maximum || 100;
    if (value == null || !Number.isFinite(Number(value))) return '<span class="meter-unknown">Unknown</span>';
    const amount = Math.max(0, Math.min(max, Number(value)));
    return '<progress class="meter ' + escapeHtml(extraClass || '') + '" max="' + max + '" value="' + amount + '" aria-label="' + escapeHtml(label) + '"></progress>';
  }

  const renderedRegions = new WeakMap();
  function updateRegion(node, markup) {
    if (!node || renderedRegions.get(node) === markup) return;
    renderedRegions.set(node, markup);
    const opened = Array.from(node.querySelectorAll('details[open][data-detail-key]')).map(function (detail) { return detail.dataset.detailKey; });
    const focused = document.activeElement;
    const focusKey = focused && focused.closest('[data-detail-key]');
    const approveId = focused && focused.getAttribute('data-approve');
    const scroll = node.scrollTop;
    node.innerHTML = markup;
    opened.forEach(function (key) { const detail = node.querySelector('[data-detail-key="' + CSS.escape(key) + '"]'); if (detail) detail.open = true; });
    if (approveId) { const button = node.querySelector('[data-approve="' + CSS.escape(approveId) + '"]'); if (button) button.focus({preventScroll:true}); }
    else if (focusKey) { const summary = node.querySelector('[data-detail-key="' + CSS.escape(focusKey.dataset.detailKey) + '"] > summary'); if (summary) summary.focus({preventScroll:true}); }
    node.scrollTop = scroll;
  }

  function themeColor(color) {
    const colors = {"#22d3ee":"--chart-1", "#a3e635":"--chart-2", "#fb923c":"--chart-3", "#c084fc":"--chart-4", "#818cf8":"--chart-5", "#facc15":"--chart-6", "#f472b6":"--chart-4", "#a78bfa":"--chart-4", "#34d399":"--chart-2", "#f87171":"--chart-6"};
    return 'var(' + (colors[color] || '--chart-1') + ')';
  }

  function agentName(identifier) {
    if (!identifier) return "—";
    const agents = (state.snapshot && state.snapshot.agents) || [];
    const agent = agents.find(function (item) {
      return item.id === identifier;
    });
    return agent ? agent.name : identifier;
  }

  function statusBadge(status) {
    return (
      '<span class="status-badge status-' +
      escapeHtml(status) +
      '"><i></i>' +
      escapeHtml(STATUS_LABEL[status] || status) +
      "</span>"
    );
  }

  function outcomeBadge(outcome) {
    const value = outcome || "pending";
    return (
      '<span class="badge ' +
      (OUTCOME_BADGE[value] || "badge-blue") +
      '">' +
      escapeHtml(value) +
      "</span>"
    );
  }

  function gateBadge(gate) {
    const value = gate || "pending";
    return (
      '<span class="badge ' +
      (GATE_BADGE[value] || "badge-blue") +
      '">' +
      escapeHtml(value) +
      "</span>"
    );
  }

  function renderKpis(snapshot) {
    document.querySelectorAll("[data-kpi]").forEach(function (node) {
      const key = node.getAttribute("data-kpi");
      const formatter = KPI_FORMAT[key] || function (value) { return String(value); };
      const text = formatter(snapshot.kpis[key]);
      if (node.textContent !== text) node.textContent = text;
    });
  }

  function renderShared(snapshot) {
    const engineState = $("#engine-state");
    if (engineState) engineState.textContent = snapshot.meta.engine;
    const engineTick = $("#engine-tick");
    if (engineTick) {
      engineTick.textContent = number(snapshot.kpis.events_total) + " recorded events";
    }
    const runtimeGrid = $("#runtime-grid");
    if (runtimeGrid) runtimeGrid.innerHTML = Object.values(snapshot.runtime || {}).map(function (component) {
      const ready = component.status === "ready";
      return '<article class="runtime-card"><div><strong>' + escapeHtml(component.name) + '</strong><span class="badge ' +
        (ready ? 'badge-lime' : 'badge-orange') + '">' + escapeHtml(component.status) + '</span></div><p>' +
        escapeHtml(component.detail) + '</p><small>' + escapeHtml(component.execution) + '</small><code>' +
        escapeHtml(component.location) + '</code></article>';
    }).join("");
    const active = snapshot.missions.filter(function (m) { return ["queued", "running", "awaiting_human"].includes(m.status); }).length;
    document.querySelectorAll("#launch-mission, #launch-mission-2").forEach(function (button) {
      button.disabled = !!button.dataset.busy || active >= snapshot.meta.max_active_missions;
      button.title = active >= snapshot.meta.max_active_missions ? "Finish the active mission or approve its checkpoint first." : "Run the selected scenario";
    });
    const otelEvents = $("#otel-events");
    if (otelEvents) otelEvents.textContent = number(snapshot.kpis.events_total);
    const croaCount = $("#croa-count");
    if (croaCount) croaCount.textContent = number(snapshot.evidence_packets.length);
  }

  /* ------------------------------------------------------------- mission control */
  function agentCard(agent) {
    const status = agent.status || 'idle';
    return '<article class="agent-card ' + escapeHtml(status) + '"><div class="agent-top"><span class="agent-avatar">' + escapeHtml(agent.initials) + '</span><div class="agent-name"><strong>' + escapeHtml(agent.name) + '</strong><span>' + escapeHtml(agent.role) + '</span></div><span class="agent-status ' + escapeHtml(status) + '">' + escapeHtml(status) + '</span></div><div class="agent-model"><span>' + escapeHtml(agent.model) + '</span><span>' + escapeHtml(agent.family) + '</span></div><div class="agent-action">' + escapeHtml(agent.mission ? agent.fixture + ' · ' + agent.action : agent.action) + '</div>' + meter(Number(agent.progress) * 100, agent.name + ' progress') + '<div class="agent-foot"><span>' + number(agent.tasks) + ' steps · ' + tokens(agent.tokens) + ' tokens</span><span>' + money(agent.cost, 3) + '</span></div></article>';
  }

  function missionCard(mission) {
    const steps = mission.steps.map(function (step) {
      return '<li class="step-' + escapeHtml(step.status) + '"><strong>' + escapeHtml(step.label) + '</strong><span>' + escapeHtml(step.detail || step.status) + '</span></li>';
    }).join('');
    const issue = mission.paperclip_url ? '<a class="issue-link" target="_blank" rel="noreferrer" href="' + escapeHtml(mission.paperclip_url) + '">' + escapeHtml(mission.paperclip_identifier) + ' ↗</a>' : '';
    const approve = mission.status === 'awaiting_human' ? '<button class="btn btn-primary mission-approve" type="button" data-approve="' + escapeHtml(mission.id) + '">Approve checkpoint</button>' : '';
    return '<article class="mission-card"><div class="mission-top"><span class="fixture">' + escapeHtml(mission.id + ' / ' + mission.fixture) + '</span><span class="priority risk-' + escapeHtml(mission.risk) + '">' + escapeHtml(mission.risk) + ' risk</span>' + statusBadge(mission.status) + '</div><h3>' + escapeHtml(mission.title) + '</h3><p>' + escapeHtml(mission.subtitle) + '</p>' + meter(Number(mission.progress) * 100, mission.id + ' progress', 'mission-meter') + '<div class="mission-foot"><span>' + escapeHtml(mission.current_step) + '</span><span>' + money(mission.cost, 3) + ' · ' + seconds(mission.elapsed_seconds) + '</span></div><details class="mission-details" data-detail-key="' + escapeHtml(mission.id) + '"><summary>Execution details</summary><p class="mission-source">' + escapeHtml(mission.profile) + '</p>' + issue + '<ol class="step-list">' + steps + '</ol>' + (mission.gate_reason ? '<p class="mission-decision">' + escapeHtml(mission.gate_reason) + '</p>' : '') + '</details>' + approve + '</article>';
  }

  function policyMarkup(policy, snapshot, compact) {
    const kpis = snapshot.kpis;
    const usedPercent = Math.min(
      100,
      kpis.budget_total ? (kpis.budget_used / kpis.budget_total) * 100 : 0
    );
    const activeProfiles = snapshot.missions
      .filter(function (mission) {
        return mission.status === "running" || mission.status === "awaiting_human";
      })
      .map(function (mission) {
        return mission.profile;
      });

    const rules = policy.rules
      .map(function (rule) {
        return "<div>" + escapeHtml(rule) + "</div>";
      })
      .join("");

    const profiles = compact
      ? ""
      : '<div class="profile-chips">' +
        policy.profiles
          .map(function (profile) {
            const active = activeProfiles.some(function (selected) {
              return selected.indexOf(profile.model) !== -1;
            });
            return (
              '<span class="' +
              (active ? "active" : "") +
              '" title="' +
              escapeHtml(profile["class"] + " · " + profile.effort) +
              '">' +
              escapeHtml(profile.model) +
              "</span>"
            );
          })
          .join("") +
        "</div>";

    return (
      '<div class="budget-block">' +
      '<span class="mini-label">Daily budget (UTC)</span>' +
      '<div class="budget-top"><strong>' +
      money(kpis.budget_used, 2) +
      "</strong><span>of " +
      money(kpis.budget_total, 0) +
      " per day</span></div>" +
      meter(usedPercent, 'Budget used') +
      '<div class="budget-meta"><span>' +
      usedPercent.toFixed(1) +
      "% used</span><span>" +
      money(kpis.budget_remaining, 2) +
      " left</span></div>" +
      "</div>" +
      '<div class="rule-list">' +
      rules +
      "</div>" +
      profiles +
      '<p class="mini-label quality-caption">Quality floor</p>' +
      '<p class="muted quality-note">' +
      escapeHtml(policy.quality_floor) +
      "</p>"
    );
  }

  function eventsMarkup(events, limit) {
    if (!events || !events.length) {
      return '<div class="table-empty">Waiting for the first event…</div>';
    }
    return events
      .slice(0, limit)
      .map(function (event) {
        const meta = [event.actor, event.fixture, event.mission].filter(Boolean).join(" · ");
        return (
          '<div class="event-row">' +
          '<span class="event-time">' +
          escapeHtml(clockTime(event.ts)) +
          "</span>" +
          '<div class="event-body"><span class="event-type ' +
          escapeHtml(event.severity) +
          '">' +
          escapeHtml(event.type) +
          "</span>" +
          '<div class="event-msg">' +
          escapeHtml(event.message) +
          "</div>" +
          '<div class="event-meta">' +
          escapeHtml(meta) +
          "</div></div></div>"
        );
      })
      .join("");
  }

  function outcomeSegments(outcomes) {
    return [
      { label: "Autonomous", value: outcomes.autonomous, color: "var(--success)" },
      { label: "Assisted", value: outcomes.assisted, color: "var(--primary)" },
      { label: "Failed", value: outcomes.failed, color: "var(--danger)" },
      { label: "Unknown", value: outcomes.unknown, color: "var(--warning)" },
    ];
  }

  function renderCharts(snapshot) {
    if (!window.Charts) return;
    const series = snapshot.charts.series || [];

    if ($("#chart-cost")) {
      window.Charts.line($("#chart-cost"), series, {
        yKey: "cost",
        color: "var(--primary)",
        label: "Recorded cost by mission",
        formatY: function (value) { return money(value, 2); },
        formatX: function (point) { return "t" + point.t; },
      });
    }

    if ($("#chart-outcomes")) {
      window.Charts.donut($("#chart-outcomes"), outcomeSegments(snapshot.charts.outcomes), {
        centerLabel: "outcomes",
        label: "Outcome distribution",
        empty: "No mature outcomes yet",
      });
    }

    if ($("#chart-models")) {
      window.Charts.bars(
        $("#chart-models"),
        snapshot.charts.model_usage.map(function (usage) {
          return { label: usage.model, value: usage.tokens, color: themeColor(usage.color) };
        }),
        {
          formatValue: function (value) { return tokens(value) + " tok"; },
          empty: "No model tokens recorded yet",
        }
      );
    }

    if ($("#chart-latency")) {
      window.Charts.latency($("#chart-latency"), snapshot.charts.latency, {
        formatY: function (value) { return value.toFixed(0) + "s"; },
        label: "Latency of completed missions",
      });
    }
  }

  function renderMissionControl(snapshot) {
    const active = snapshot.agents.filter(function (agent) {
      return agent.status === "working" || agent.status === "waiting";
    }).length;
    const agentCount = $("#agent-count");
    if (agentCount) agentCount.textContent = active + " active";

    const agentGrid = $("#agent-grid");
    if (agentGrid) agentGrid.innerHTML = snapshot.agents.map(agentCard).join("");

    const missionList = $("#mission-list");
    if (missionList) {
      const previous = missionList.scrollTop;
      updateRegion(missionList, snapshot.missions.length ? snapshot.missions.slice(0, 9).map(missionCard).join("") : '<div class="table-empty">Your next mission starts here. Choose a scenario above to begin.</div>');
      missionList.scrollTop = previous;
    }

    const policyPanel = $("#policy-panel");
    if (policyPanel) policyPanel.innerHTML = policyMarkup(snapshot.policy, snapshot, false);

    const eventStream = $("#event-stream");
    if (eventStream) {
      const previous = eventStream.scrollTop;
      updateRegion(eventStream, eventsMarkup(snapshot.events, 8));
      eventStream.scrollTop = previous;
    }

    const strip = $("#framework-strip");
    if (strip) {
      strip.innerHTML = snapshot.frameworks
        .slice(0, 5)
        .map(function (framework) {
          return (
            '<div class="framework-pill"><i aria-hidden="true"></i><div><strong>' +
            escapeHtml(framework.name) +
            "</strong><small>" +
            escapeHtml(framework.status) +
            "</small></div></div>"
          );
        })
        .join("");
    }

    renderCharts(snapshot);
  }

  /* ---------------------------------------------------------------- paperclip */
  function kanbanCard(mission) {
    const approve = mission.status === 'awaiting_human' ? '<button class="btn btn-primary" type="button" data-approve="' + escapeHtml(mission.id) + '">Approve checkpoint</button>' : '';
    const issue = mission.paperclip_url ? '<a class="issue-link" target="_blank" rel="noreferrer" href="' + escapeHtml(mission.paperclip_url) + '">' + escapeHtml(mission.paperclip_identifier) + ' ↗</a>' : '';
    return '<article class="kanban-card"><div class="kc-top"><span class="fixture">' + escapeHtml(mission.id) + '</span><span class="priority risk-' + escapeHtml(mission.risk) + '">' + escapeHtml(mission.risk) + '</span></div><h4>' + escapeHtml(mission.title) + '</h4><p>' + escapeHtml(mission.subtitle) + '</p>' + issue + '<div class="kc-meta"><span>' + escapeHtml(mission.current_step) + '</span><span>' + money(mission.cost) + '</span></div>' + meter(Number(mission.progress) * 100, mission.id + ' progress') + approve + '</article>';
  }

  function renderPaperclip(snapshot) {
    const service = snapshot.paperclip || {};
    const health = service.health || {};
    setText("pc-service-status", service.available ? "connected" : "unavailable");
    setText("pc-service-detail", service.error || (health.url + " · " + (health.version || "unknown version") + " · embedded PostgreSQL"));
    setText("pc-envelope", "of " + money(snapshot.kpis.budget_total) + " per day · resets 00:00 UTC");
    const issuesNode = document.getElementById("paperclip-issues");
    if (issuesNode) issuesNode.innerHTML = service.available && !service.error ? (service.issues || []).map(function (issue) {
      const prefix = (service.company || {}).issuePrefix || "PAR";
      const url = health.url + "/" + encodeURIComponent(prefix) + "/issues/" + encodeURIComponent(issue.identifier);
      return '<a class="issue-row" href="' + escapeHtml(url) + '" target="_blank" rel="noreferrer"><code>' + escapeHtml(issue.identifier) + '</code><span>' + escapeHtml(issue.title) + '</span><span class="badge badge-cyan">' + escapeHtml(issue.status) + '</span></a>';
    }).join("") || '<div class="table-empty">No issues returned by Paperclip.</div>' : '<div class="table-empty">' + escapeHtml(service.error || "Paperclip is unavailable; no issue data returned.") + '</div>';
    const missions = snapshot.missions || [];
    const running = missions.filter(function (mission) { return mission.status === "running"; });
    const awaiting = missions.filter(function (mission) { return mission.status === "awaiting_human"; });
    const complete = missions.filter(function (mission) { return mission.status === "completed"; });
    const blocked = missions.filter(function (mission) { return mission.status === "blocked"; });

    const open = $("#pc-open");
    if (open) open.textContent = running.length + awaiting.length;
    const approval = $("#pc-approval");
    if (approval) approval.textContent = awaiting.length;
    const budget = $("#pc-budget");
    if (budget) budget.textContent = money(snapshot.kpis.budget_used, 2);
    const routinesHealth = snapshot.routines.filter(function (routine) { return routine.status === "healthy"; }).length;
    const routines = $("#pc-routines");
    if (routines) routines.textContent = routinesHealth + "/" + snapshot.routines.length;

    const columns = [
      { key: "running", title: "In progress", items: running, limit: 4, empty: "No work in progress" },
      { key: "awaiting_human", title: "Awaiting approval", items: awaiting, limit: 4, empty: "No step-up waiting" },
      { key: "completed", title: "Complete", items: complete, limit: 4, empty: "No verified cards yet" },
      { key: "blocked", title: "Blocked", items: blocked, limit: 4, empty: "No blocked cards" },
    ];

    const kanban = $("#kanban");
    if (kanban) {
      kanban.innerHTML = columns
        .map(function (column) {
          const visible = column.items.slice(0, column.limit);
          const cards = visible.length
            ? visible.map(kanbanCard).join("")
            : '<div class="table-empty">' + escapeHtml(column.empty) + "</div>";
          const older = column.items.length - visible.length;
          const more = older > 0
            ? '<div class="kanban-more">+' + older + " older in the ledger</div>"
            : "";
          return (
            '<div class="kanban-col"><div class="kanban-head"><h3>' +
            escapeHtml(column.title) +
            "</h3><span>" +
            column.items.length +
            '</span></div><div class="kanban-cards">' +
            cards +
            more +
            "</div></div>"
          );
        })
        .join("");
    }

    const paperclipBudget = $("#paperclip-budget");
    if (paperclipBudget) {
      const used = snapshot.kpis.budget_used;
      const total = snapshot.kpis.budget_total;
      const usedPercent = Math.min(100, total ? (used / total) * 100 : 0);
      paperclipBudget.innerHTML =
        '<div class="budget-block"><span class="mini-label">Daily budget envelope · UTC</span>' +
        '<div class="budget-top"><strong>' +
        money(used, 2) +
        "</strong><span>of " +
        money(total, 0) +
        "</span></div>" +
        meter(usedPercent, 'Budget used') +
        '<div class="budget-meta"><span>' +
        usedPercent.toFixed(1) +
        "% used</span><span>" +
        money(snapshot.kpis.budget_remaining, 2) +
        " available</span></div></div>" +
        '<div class="rule-list"><div>One canonical ledger; admission is capped at ' +
        '<strong>' + number(snapshot.meta.max_active_missions) + ' active mission(s)</strong>.</div>' +
        "<div>All attempts stay in spend; autonomous successes are the cost denominator.</div>" +
        "<div>Costs and tokens are reported by the CLI. Free models can report $0; paid calls need provider balance. The budget is checked after each run, not reserved upstream.</div></div>";
    }

    const adapters = $("#adapter-list");
    if (adapters) {
      const rows = Object.values(snapshot.runtime || {}).map(function (component) {
        return { icon: component.name.slice(0, 2).toUpperCase(), name: component.name, detail: component.execution + " · " + component.detail, state: component.status };
      });
      adapters.innerHTML = rows
        .map(function (row) {
          const sim = row.state !== "ready";
          return (
            '<div class="adapter-row"><span class="adapter-icon">' +
            escapeHtml(row.icon) +
            "</span><div><strong>" +
            escapeHtml(row.name) +
            "</strong><small>" +
            escapeHtml(row.detail) +
            '</small></div><span class="adapter-state' +
            (sim ? " sim" : "") +
            '">' +
            escapeHtml(row.state) +
            "</span></div>"
          );
        })
        .join("");
    }

    const routinesList = $("#routines-list");
    if (routinesList) {
      routinesList.innerHTML = snapshot.routines
        .map(function (routine) {
          const badge = routine.status === "healthy" ? "badge-lime" : "badge-orange";
          return (
            '<div class="routine-row"><div><strong>' +
            escapeHtml(routine.name) +
            "</strong><small>" +
            escapeHtml(routine.schedule) +
            " · next " +
            escapeHtml(routine.next) +
            '</small></div><span class="badge ' +
            badge +
            '">' +
            escapeHtml(routine.status) +
            "</span><span class=\"routine-last\">" +
            escapeHtml(routine.last) +
            "</span></div>"
          );
        })
        .join("");
    }

    const workload = $("#paperclip-workload");
    if (workload) {
      const risks = Object.keys(snapshot.charts.risks || {}).map(function (risk) {
        return { label: risk, value: snapshot.charts.risks[risk], color: RISK_COLOR[risk] || "var(--primary)" };
      });
      const riskMax = Math.max.apply(
        null,
        risks.map(function (risk) { return risk.value; }).concat([1])
      );
      const riskRows = risks.map(function (risk) {
        return '<div class="workload-row"><span>' + escapeHtml(risk.label) + '</span>' + meter(risk.value, risk.label + ' missions', '', riskMax) + '<strong>' + risk.value + '</strong></div>';
      }).join('');

      const agentLoad = snapshot.agents
        .filter(function (agent) { return agent.tasks > 0; })
        .sort(function (a, b) { return b.tasks - a.tasks; })
        .slice(0, 6);
      const agentMax = Math.max.apply(
        null,
        agentLoad.map(function (agent) { return agent.tasks; }).concat([1])
      );
      const agentRows = agentLoad.map(function (agent) {
        return '<div class="workload-row"><span>' + escapeHtml(agent.name) + '</span>' + meter(agent.tasks, agent.name + ' steps', '', agentMax) + '<strong>' + agent.tasks + '</strong></div>';
      }).join('');

      workload.innerHTML =
        '<div class="workload-sub">Risk mix</div>' +
        (riskRows || '<div class="table-empty">No tasks yet</div>') +
        '<div class="workload-sub">Agent load</div>' +
        (agentRows || '<div class="table-empty">Fleet idle</div>');
    }
  }

  /* ------------------------------------------------------------ observability */
  function renderObservability(snapshot) {
    const routing = $("#routing-table tbody");
    if (routing) {
      routing.innerHTML = snapshot.routing.length
        ? snapshot.routing
            .map(function (row) {
              return (
                "<tr><td><code>" +
                escapeHtml(row.mission) +
                "</code></td><td>" +
                escapeHtml(row.fixture) +
                '</td><td><span class="risk-' + escapeHtml(row.risk) + '">' +
                escapeHtml(row.risk) +
                "</span></td><td>" +
                escapeHtml(row.profile) +
                "</td><td>" +
                gateBadge(row.gate) +
                "</td><td>" +
                outcomeBadge(row.outcome) +
                '</td><td class="num">' +
                money(row.cost, 3) +
                "</td></tr>"
              );
            })
            .join("")
        : '<tr><td colspan="7" class="table-empty">No routing decisions yet</td></tr>';
    }

    const policy = $("#observability-policy");
    if (policy) policy.innerHTML = policyMarkup(snapshot.policy, snapshot, true);

    const events = $("#observability-events");
    if (events) {
      const previous = events.scrollTop;
      events.innerHTML = eventsMarkup(snapshot.events, 60);
      events.scrollTop = previous;
    }

    renderCharts(snapshot);
  }

  /* ---------------------------------------------------------------------- ops */
  function renderOps(snapshot) {
    const opsGrid = $("#ops-grid");
    if (opsGrid) {
      opsGrid.innerHTML = snapshot.ops_controls
        .map(function (control) {
          return (
            '<div class="ops-row"><span class="ops-id">' +
            escapeHtml(control.id) +
            '</span><span class="ops-name">' +
            escapeHtml(control.name) +
            '</span><span class="ops-evidence">' +
            escapeHtml(control.evidence) +
            '</span><span class="ops-status">' +
            escapeHtml(control.status) +
            '</span><span class="ops-state ' +
            escapeHtml(control.status) +
            '"></span></div>'
          );
        })
        .join("");
    }

    setText("ops-summary", ["enforced", "partial", "disabled"].map(function (status) { return snapshot.ops_controls.filter(function (c) { return c.status === status; }).length + " " + status; }).join(" · "));
    const gateLog = $("#gate-log");
    if (gateLog) {
      const gated = snapshot.missions.filter(function (mission) {
        return mission.gate_decision || mission.status === "awaiting_human";
      });
      gateLog.innerHTML = gated.length
        ? gated
            .map(function (mission) {
              const decision = mission.gate_decision || "checkpoint";
              const approve = mission.status === "awaiting_human"
                ? '<button class="btn btn-small btn-primary" type="button" data-approve="' + escapeHtml(mission.id) + '">Approve</button>'
                : "";
              return (
                '<div class="gate-row">' +
                '<span class="badge ' +
                (GATE_BADGE[decision] || "badge-blue") +
                '">' +
                escapeHtml(decision) +
                '</span><span class="gate-reason"><strong>' +
                escapeHtml(mission.fixture) +
                " · " +
                escapeHtml(mission.id) +
                "</strong> " +
                escapeHtml(mission.gate_reason) +
                "</span>" +
                approve +
                "<span>" +
                escapeHtml(mission.human_approval ? "operator approved" : mission.status === "awaiting_human" ? "owner required" : "—") +
                "</span></div>"
              );
            })
            .join("")
        : '<div class="table-empty">No gate decisions yet. Launch a scenario to record one.</div>';
    }

    const evidence = $("#evidence-table tbody");
    if (evidence) {
      evidence.innerHTML = snapshot.evidence_packets.length
        ? snapshot.evidence_packets
            .map(function (packet) {
              return (
                '<tr><td><a href="/api/evidence/' + encodeURIComponent(packet.id) + '" target="_blank" rel="noreferrer"><code>' +
                escapeHtml(packet.id) +
                "</code></a></td><td>" +
                escapeHtml(packet.mission) +
                "</td><td>" +
                escapeHtml(packet.scope) +
                "</td><td>" +
                packet.checks +
                '</td><td><span class="badge ' +
                (packet.review === "OK" ? "badge-lime" : "badge-red") +
                '">' +
                escapeHtml(packet.review) +
                '</span></td><td><a href="/api/evidence/' + encodeURIComponent(packet.id) + '/verify" target="_blank" rel="noreferrer"><code>' +
                escapeHtml(packet.digest) +
                '</code></a></td><td><span class="badge badge-pink">' +
                escapeHtml(packet.status) +
                "</span></td></tr>"
              );
            })
            .join("")
        : '<tr><td colspan="7" class="table-empty">No sealed packets yet</td></tr>';
    }
  }

  /* --------------------------------------------------------------- frameworks */
  function renderFrameworks(snapshot) {
    const grid = $("#framework-grid");
    if (!grid) return;
    grid.innerHTML = snapshot.frameworks
      .map(function (framework) {
        const badge = framework.status === "active" ? "badge-lime" : "badge-orange";
        return (
          '<article class="framework-card"><div class="framework-card-top"><h3>' +
          escapeHtml(framework.name) +
          '</h3><span class="badge ' +
          badge +
          '">' +
          escapeHtml(framework.status) +
          "</span></div><p>" +
          escapeHtml(framework.role) +
          '</p><div class="fc-used">' +
          escapeHtml(framework.used) +
          '</div><div class="fc-path">' +
          escapeHtml(framework.path) +
          "</div></article>"
        );
      })
      .join("");
  }

  /* ------------------------------------------------------------ memory & graph */
  const MEMORY_KIND = {
    constraint: "badge-orange",
    fact: "badge-cyan",
    decision: "badge-violet",
    unknown: "badge-red",
  };

  function setText(identifier, value) {
    const node = document.getElementById(identifier);
    if (node) node.textContent = value;
  }

  function memoryMarkup(memory) {
    if (!memory || !memory.items.length) {
      return '<div class="table-empty">The vault is empty.</div>';
    }
    return memory.items
      .map(function (item) {
        const kindClass = MEMORY_KIND[item.kind] || "badge-blue";
        const stateClass = item.state === "verified" ? "verified" : item.state === "stale" ? "stale" : "candidate";
        return (
          '<div class="memory-row">' +
          '<div class="memory-row-top"><span class="memory-id">' +
          escapeHtml(item.id) +
          '</span><span class="badge ' +
          kindClass +
          '">' +
          escapeHtml(item.kind) +
          '</span><span class="memory-scope">' +
          escapeHtml(item.scope) +
          '</span><span class="memory-state ' +
          stateClass +
          '">' +
          escapeHtml(item.state) +
          "</span></div>" +
          "<p>" +
          escapeHtml(item.statement) +
          "</p>" +
          '<div class="memory-meta"><code>' +
          escapeHtml(item.source) +
          "</code><span>" +
          number(item.uses) +
          " uses</span><span>" +
          (item.last_used ? "last " + escapeHtml(item.last_used) : "not recalled") +
          "</span><span>conf " +
          Number(item.confidence).toFixed(2) +
          "</span><span>" +
          escapeHtml(item.freshness) +
          " · " +
          escapeHtml(item.authority) +
          "</span></div></div>"
        );
      })
      .join("");
  }

  function recallMarkup(memory) {
    if (!memory || !memory.recalls.length) {
      return '<div class="table-empty">No recon step has run yet.</div>';
    }
    return memory.recalls
      .map(function (recall) {
        const stateClass = recall.state === "stale" ? "stale" : "hit";
        const items = recall.items.length
          ? recall.items
              .map(function (identifier) {
                return "<span>" + escapeHtml(identifier) + "</span>";
              })
              .join("")
          : "<span>no memory hit</span>";
        return (
          '<div class="recall-row">' +
          '<div class="recall-top"><span class="fixture">' +
          escapeHtml(recall.fixture) +
          '</span><span class="priority">' +
          escapeHtml(recall.mission) +
          '</span><span class="recall-state ' +
          stateClass +
          '">' +
          escapeHtml(recall.state) +
          "</span></div>" +
          '<div class="recall-query">' +
          escapeHtml(recall.query) +
          "</div>" +
          '<div class="recall-meta"><span>' +
          (recall.nodes == null ? "graph unrecorded" : number(recall.nodes) + " nodes") +
          " · " +
          recall.items.length +
          " memories</span><span>" +
          number(recall.latency_ms) +
          " ms · " +
          escapeHtml(recall.status) +
          "</span></div>" +
          '<div class="recall-items">' +
          items +
          "</div>" +
          '<div class="recall-meta"><span>digest ' +
          escapeHtml(recall.digest || "not recorded") +
          "</span><span>" +
          (recall.digest ? "recorded in packet" : "unavailable") +
          "</span></div></div>"
        );
      })
      .join("");
  }

  function graphRadius(node) {
    if (node.id === "pricing") return 32;
    if (node.kind === "domain") return 27;
    return 24;
  }

  function graphStatusColor(node) {
    if (node.status === "denied" || node.status === "stale") return "var(--danger)";
    if (node.status === "protected") return "var(--warning)";
    if (node.status === "hot") return "var(--primary)";
    if (node.status === "verified") return "var(--success)";
    if (node.kind === "memory") return "var(--success)";
    if (node.kind === "verify") return "var(--warning)";
    if (node.kind === "evidence") return "var(--chart-4)";
    return themeColor(node.color);
  }

  function selectGraphNode(identifier, graph) {
    const data = graph || (state.snapshot && state.snapshot.graph);
    if (!data) return;
    const node = data.nodes.find(function (item) {
      return item.id === identifier;
    });
    if (!node) return;
    state.graph.selected = identifier;

    document.querySelectorAll("#context-graph .graph-node").forEach(function (element) {
      const selected = element.getAttribute("data-node") === identifier;
      element.classList.toggle("selected", selected);
      element.setAttribute("aria-pressed", String(selected));
    });
    document.querySelectorAll("#context-graph .graph-link").forEach(function (line) {
      line.classList.toggle(
        "active",
        line.getAttribute("data-source") === identifier || line.getAttribute("data-target") === identifier
      );
    });

    const detail = $("#graph-detail");
    if (!detail) return;
    const related = data.links
      .filter(function (link) {
        return link.source === identifier || link.target === identifier;
      })
      .map(function (link) {
        const other = link.source === identifier ? link.target : link.source;
        const otherNode = data.nodes.find(function (item) {
          return item.id === other;
        });
        return "<span>" + escapeHtml(link.relation) + " · " + escapeHtml(otherNode ? otherNode.label : other) + "</span>";
      })
      .join("");
    detail.innerHTML =
      '<div class="graph-detail-head"><span class="mini-label">Selected node</span><strong>' +
      escapeHtml(node.label) +
      "</strong><small>" +
      escapeHtml(node.meta) +
      "</small></div><p>" +
      escapeHtml(node.detail) +
      '</p><div class="graph-rel">' +
      (related || "<span>no edges</span>") +
      "</div>";
  }

  function renderGraph(graph) {
    if (!graph || !graph.nodes) return;
    const svg = $("#context-graph");
    if (!svg) return;
    const width = Math.max(260, Math.round(svg.clientWidth));
    const layoutKey = graph.digest + ':' + width;
    if (state.graph.digest === layoutKey) return;
    state.graph.digest = layoutKey;
    const columns = Math.max(2, Math.min(6, Math.floor(width / 145)));
    const nodesPerRow = width / columns;
    const layoutNodes = graph.nodes.map(function (node, index) {
      return Object.assign({}, node, {x: nodesPerRow * (index % columns + 0.5), y: Math.floor(index / columns) * 110 + 42});
    });
    svg.setAttribute('viewBox', '0 0 ' + width + ' ' + Math.max(110, Math.ceil(layoutNodes.length / columns) * 110));
    if (!graph.nodes.some(function (node) { return node.id === state.graph.selected; }) && graph.nodes.length) state.graph.selected = graph.nodes[0].id;
    const nodeMap = {};
    layoutNodes.forEach(function (node) {
      nodeMap[node.id] = node;
    });

    const defs =
      '<defs><marker id="graph-arrow" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="5" markerHeight="5" orient="auto-start-reverse">' +
      '<path d="M 0 0 L 10 5 L 0 10 z" fill="var(--line-strong)"></path></marker></defs>';

    const links = graph.links
      .map(function (link) {
        const source = nodeMap[link.source];
        const target = nodeMap[link.target];
        if (!source || !target) return "";
        const dx = target.x - source.x;
        const dy = target.y - source.y;
        const distance = Math.sqrt(dx * dx + dy * dy) || 1;
        const sourceRadius = graphRadius(source) + 3;
        const targetRadius = graphRadius(target) + 7;
        const x1 = source.x + (dx / distance) * sourceRadius;
        const y1 = source.y + (dy / distance) * sourceRadius;
        const x2 = target.x - (dx / distance) * targetRadius;
        const y2 = target.y - (dy / distance) * targetRadius;
        return (
          '<line class="graph-link' +
          (link.advisory ? " advisory" : "") +
          '" data-source="' +
          escapeHtml(link.source) +
          '" data-target="' +
          escapeHtml(link.target) +
          '" x1="' +
          x1.toFixed(1) +
          '" y1="' +
          y1.toFixed(1) +
          '" x2="' +
          x2.toFixed(1) +
          '" y2="' +
          y2.toFixed(1) +
          '" marker-end="url(#graph-arrow)"><title>' +
          escapeHtml(link.source) +
          " → " +
          escapeHtml(link.target) +
          " · " +
          escapeHtml(link.relation) +
          "</title></line>"
        );
      })
      .join("");

    const nodes = layoutNodes
      .map(function (node) {
        const radius = graphRadius(node);
        const statusColor = graphStatusColor(node);
        return (
          '<g class="graph-node' +
          (node.id === state.graph.selected ? " selected" : "") +
          '" data-node="' +
          escapeHtml(node.id) +
          '" tabindex="0" role="button" aria-label="' +
          escapeHtml(node.label + " · " + node.status) +
          '">' +
          '<circle class="node-halo" cx="' +
          node.x +
          '" cy="' +
          node.y +
          '" r="' +
          (radius + 8) +
          '" stroke="' +
          escapeHtml(themeColor(node.color)) +
          '"></circle>' +
          '<circle class="node-core" cx="' +
          node.x +
          '" cy="' +
          node.y +
          '" r="' +
          radius +
          '" fill="' +
          escapeHtml(themeColor(node.color)) +
          '" stroke="' +
          escapeHtml(themeColor(node.color)) +
          '"></circle>' +
          '<circle cx="' +
          (node.x + radius - 3) +
          '" cy="' +
          (node.y - radius + 3) +
          '" r="4" fill="' +
          statusColor +
          '"></circle>' +
          '<text class="node-mono" x="' +
          node.x +
          '" y="' +
          (node.y + 4) +
          '">' +
          escapeHtml(node.monogram) +
          '</text><text class="node-label" x="' +
          node.x +
          '" y="' +
          (node.y + radius + 16) +
          '">' +
          escapeHtml(node.label.length > 19 ? node.label.slice(0, 18) + '…' : node.label) +
          '</text><text class="node-status" x="' +
          node.x +
          '" y="' +
          (node.y + radius + 27) +
          '">' +
          escapeHtml(node.status) +
          "</text><title>" +
          escapeHtml(node.label + " · " + node.meta) +
          "</title></g>"
        );
      })
      .join("");

    svg.innerHTML = defs + '<g class="graph-links">' + links + '</g><g class="graph-nodes">' + nodes + "</g>";
    selectGraphNode(state.graph.selected, graph);
  }

  function renderContext(snapshot) {
    const memory = snapshot.memory;
    const graph = snapshot.graph;
    if (!memory || !graph) return;

    setText("ctx-items", number(memory.stats.total));
    setText("ctx-items-hero", number(memory.stats.total));
    setText("ctx-verified", number(memory.stats.verified));
    setText("ctx-stale", number(memory.stats.stale));
    setText("ctx-hit", percent(memory.stats.hit_rate, 0));
    setText("ctx-queries", number(memory.stats.recall_queries));
    setText("ctx-files", number(graph.files));
    setText("ctx-edges", number(graph.edges));
    setText("ctx-symbols", number(graph.symbols));
    setText("ctx-used", number(memory.stats.used_items));
    setText("ctx-edges-hero", number(graph.edges));
    setText("graph-files", number(graph.files));
    setText("graph-edges", number(graph.edges));
    setText("graph-symbols", number(graph.symbols));
    setText("graph-digest", graph.digest);
    setText("graph-status", graph.mode || "unavailable");

    const memoryList = $("#memory-list");
    if (memoryList) memoryList.innerHTML = memoryMarkup(memory);
    const recallList = $("#recall-list");
    if (recallList) recallList.innerHTML = recallMarkup(memory);
    renderGraph(graph);
  }

  /* ------------------------------------------------------------------- update */
  function renderPage(snapshot) {
    renderKpis(snapshot);
    renderShared(snapshot);
    const page = document.body.getAttribute("data-page");
    if (page === "mission_control") renderMissionControl(snapshot);
    else if (page === "paperclip") renderPaperclip(snapshot);
    else if (page === "observability") renderObservability(snapshot);
    else if (page === "ops") renderOps(snapshot);
    else if (page === "context") renderContext(snapshot);
    else if (page === "frameworks") renderFrameworks(snapshot);
    document.querySelectorAll('.data-table').forEach(function (table) {
      const headers = Array.from(table.querySelectorAll('thead th')).map(function (heading) { heading.scope = 'col'; return heading.textContent; });
      table.querySelectorAll('tbody tr').forEach(function (row) { Array.from(row.cells).forEach(function (cell, index) { cell.dataset.label = headers[index] || ''; }); });
    });
  }

  function applySnapshot(snapshot) {
    if (!snapshot) return;
    state.snapshot = snapshot;
    renderPage(snapshot);
  }

  /* ------------------------------------------------------------------ actions */
  async function postJson(url, body) {
    const response = await fetch(url, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body || {}),
    });
    let data = null;
    try {
      data = await response.json();
    } catch (error) {
      data = null;
    }
    if (!response.ok || (data && data.ok === false)) {
      throw new Error((data && (data.error || data.detail)) || "Request failed (" + response.status + ")");
    }
    return data;
  }

  async function launchMission(button) {
    if (button.dataset.busy) return;
    button.dataset.busy = "1";
    button.disabled = true;
    try {
      const data = await postJson("/api/missions", { fixture: $("#mission-scenario") ? $("#mission-scenario").value : "PD01" });
      if (data.snapshot) applySnapshot(data.snapshot);
      const mission = data.mission || {};
      toast("Mission " + (mission.id || "new") + " launched · " + (mission.fixture || "ad hoc") + " · real execution");
    } catch (error) {
      toast(error.message || "Could not launch a mission.");
    } finally {
      delete button.dataset.busy;
      button.disabled = false;
    }
  }

  async function approveMission(button) {
    const identifier = button.getAttribute("data-approve");
    if (!identifier || button.dataset.busy) return;
    button.dataset.busy = "1";
    button.disabled = true;
    try {
      const data = await postJson("/api/missions/" + encodeURIComponent(identifier) + "/approve", {});
      if (data.snapshot) applySnapshot(data.snapshot);
      toast("Step-up approved for " + identifier + " · operator authority recorded.");
    } catch (error) {
      toast(error.message || "Could not approve this mission.");
    } finally {
      delete button.dataset.busy;
      button.disabled = false;
    }
  }

  document.addEventListener("click", function (event) {
    const target = event.target;
    if (!(target instanceof Element)) return;

    const graphNode = target.closest(".graph-node");
    if (graphNode) {
      event.preventDefault();
      selectGraphNode(graphNode.getAttribute("data-node"));
      return;
    }

    const launch = target.closest("#launch-mission, #launch-mission-2");
    if (launch) {
      event.preventDefault();
      launchMission(launch);
      return;
    }

    const approve = target.closest("[data-approve]");
    if (approve) {
      event.preventDefault();
      approveMission(approve);
    }
  });

  document.addEventListener("keydown", function (event) {
    const target = event.target;
    if (!(target instanceof Element)) return;
    if (event.key !== "Enter" && event.key !== " ") return;
    const graphNode = target.closest(".graph-node");
    if (!graphNode) return;
    event.preventDefault();
    selectGraphNode(graphNode.getAttribute("data-node"));
  });

  /* ------------------------------------------------------------------ polling */
  let pollFailures = 0;

  async function poll() {
    try {
      const response = await fetch("/api/snapshot", { cache: "no-store" });
      if (!response.ok) throw new Error("snapshot " + response.status);
      const snapshot = await response.json();
      applySnapshot(snapshot);
      pollFailures = 0;
      if (state.offline) {
        state.offline = false;
        toast("Reconnected to the local engine.");
      }
    } catch (error) {
      pollFailures += 1;
      if (pollFailures === 1) {
        state.offline = true;
        toast("Local engine unreachable — retrying every second.");
      }
    } finally {
      state.timer = window.setTimeout(poll, 1000);
    }
  }

  function startClock() {
    const node = $("#clock");
    if (!node) return;
    const tick = function () {
      node.textContent = new Date().toLocaleTimeString("en-GB", { hour12: false, hour: "2-digit", minute: "2-digit" });
    };
    tick();
    window.setInterval(tick, 1000);
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

  function init() {
    startClock();
    const snapshot = initialSnapshot();
    if (snapshot) applySnapshot(snapshot);
    window.setTimeout(poll, 1200);
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", init);
  } else {
    init();
  }

  document.addEventListener('aeion:appearancechange', function () { state.graph.digest = null; if (state.snapshot) renderPage(state.snapshot); });
  window.addEventListener('resize', function () { if (state.snapshot) { renderCharts(state.snapshot); renderGraph(state.snapshot.graph); } });
  window.aeionToast = toast;
})();
