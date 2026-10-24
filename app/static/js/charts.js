/*
 * Æion Mission Control — dependency-free SVG charts.
 * Rendered against measured container pixels so labels never distort.
 */
(function () {
  "use strict";

  const NS = "http://www.w3.org/2000/svg";
  const OUTCOME_COLORS = {
    autonomous: "var(--success)",
    assisted: "var(--primary)",
    failed: "var(--danger)",
    unknown: "var(--warning)",
  };

  function el(name, attrs) {
    const node = document.createElementNS(NS, name);
    Object.keys(attrs || {}).forEach(function (key) {
      const value = attrs[key];
      if (value !== undefined && value !== null) node.setAttribute(key, String(value));
    });
    return node;
  }

  function escapeHtml(value) {
    return String(value === undefined || value === null ? "" : value)
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;")
      .replace(/'/g, "&#039;");
  }

  function message(container, text) {
    container.innerHTML = '<div class="chart-empty">' + escapeHtml(text) + "</div>";
  }

  function niceMax(value) {
    if (!isFinite(value) || value <= 0) return 1;
    const exponent = Math.floor(Math.log(value) / Math.LN10);
    const base = Math.pow(10, exponent);
    const unit = value / base;
    let step = 10;
    if (unit <= 1) step = 1;
    else if (unit <= 2) step = 2;
    else if (unit <= 2.5) step = 2.5;
    else if (unit <= 5) step = 5;
    return step * base;
  }

  function axisText(value, formatter) {
    if (typeof formatter === "function") return formatter(value);
    if (Math.abs(value) >= 1000) return Math.round(value).toLocaleString("en-GB");
    const rounded = Math.round(value * 100) / 100;
    return String(rounded);
  }

  function chartSize(container, fallbackHeight) {
    return {
      width: Math.max(260, container.clientWidth || 560),
      height: Math.max(140, container.clientHeight || fallbackHeight || 200),
    };
  }

  /* ------------------------------------------------------------------ line */
  function line(container, points, options) {
    if (!container) return;
    const opts = options || {};
    const yKey = opts.yKey || "cost";
    const data = (Array.isArray(points) ? points : []).filter(function (point) {
      return point && isFinite(Number(point[yKey]));
    });

    if (!data.length) {
      message(container, opts.empty || "No mission data yet");
      return;
    }

    const size = chartSize(container, 200);
    const W = size.width;
    const H = size.height;
    const pad = { left: 46, right: 14, top: 16, bottom: 26 };
    const color = opts.color || "var(--primary)";
    const values = data.map(function (point) {
      return Number(point[yKey]) || 0;
    });
    const yMax = niceMax(Math.max.apply(null, values) * 1.12);
    const plotW = W - pad.left - pad.right;
    const plotH = H - pad.top - pad.bottom;

    function xAt(index) {
      if (data.length === 1) return pad.left + plotW / 2;
      return pad.left + (index / (data.length - 1)) * plotW;
    }

    function yAt(value) {
      return H - pad.bottom - (value / yMax) * plotH;
    }

    const svg = el("svg", {
      viewBox: "0 0 " + W + " " + H,
      role: "img",
      "aria-label": opts.label || "Line chart",
    });

    for (let i = 0; i <= 4; i += 1) {
      const value = yMax - (i / 4) * yMax;
      const y = pad.top + (i / 4) * plotH;
      svg.appendChild(
        el("line", {
          x1: pad.left,
          y1: y.toFixed(1),
          x2: W - pad.right,
          y2: y.toFixed(1),
          class: "grid-line",
        })
      );
      const label = el("text", {
        x: pad.left - 8,
        y: (y + 3).toFixed(1),
        class: "axis-label",
        "text-anchor": "end",
      });
      label.textContent = axisText(value, opts.formatY);
      svg.appendChild(label);
    }

    let xIndexes = [0, data.length - 1];
    if (data.length > 1 && W >= 380) xIndexes = [0, Math.floor((data.length - 1) / 2), data.length - 1];
    xIndexes = xIndexes.filter(function (value, index, list) {
      return list.indexOf(value) === index;
    });
    xIndexes.forEach(function (index) {
      const label = el("text", {
        x: xAt(index).toFixed(1),
        y: H - 6,
        class: "axis-label",
        "text-anchor": index === 0 ? "start" : index === data.length - 1 ? "end" : "middle",
      });
      label.textContent = typeof opts.formatX === "function"
        ? opts.formatX(data[index])
        : "#" + (data[index].t || index + 1);
      svg.appendChild(label);
    });

    const coords = data.map(function (point, index) {
      return [xAt(index), yAt(Number(point[yKey]) || 0)];
    });
    const pathData = coords
      .map(function (coord, index) {
        return (index ? "L" : "M") + coord[0].toFixed(1) + "," + coord[1].toFixed(1);
      })
      .join(" ");

    if (coords.length > 1) {
      const gradientId = "line-gradient-" + (container.id || "chart") + "-" + Math.random().toString(36).slice(2, 7);
      const defs = el("defs");
      const gradient = el("linearGradient", {
        id: gradientId,
        x1: "0",
        y1: "0",
        x2: "0",
        y2: "1",
      });
      gradient.appendChild(el("stop", { offset: "0%", "stop-color": color, "stop-opacity": "0.32" }));
      gradient.appendChild(el("stop", { offset: "100%", "stop-color": color, "stop-opacity": "0.01" }));
      defs.appendChild(gradient);
      svg.appendChild(defs);

      const area = el("path", {
        d:
          pathData +
          " L" +
          coords[coords.length - 1][0].toFixed(1) +
          " " +
          (H - pad.bottom) +
          " L" +
          coords[0][0].toFixed(1) +
          " " +
          (H - pad.bottom) +
          " Z",
        fill: "url(#" + gradientId + ")",
      });
      svg.appendChild(area);
    }

    if (coords.length > 1) {
      svg.appendChild(
        el("path", {
          d: pathData,
          fill: "none",
          stroke: color,
          "stroke-width": 7,
          "stroke-opacity": 0.14,
          "stroke-linecap": "round",
          "stroke-linejoin": "round",
        })
      );
    }

    svg.appendChild(
      el("path", {
        d: pathData,
        class: "series-line",
        stroke: color,
      })
    );

    const last = coords[coords.length - 1];
    if (last) {
      svg.appendChild(el("circle", { cx: last[0], cy: last[1], r: 8, fill: color, opacity: 0.14 }));
      svg.appendChild(el("circle", { cx: last[0], cy: last[1], r: 3.6, fill: color, class: "dot" }));
    }

    container.innerHTML = "";
    container.appendChild(svg);
  }

  /* ----------------------------------------------------------------- donut */
  function donut(container, segments, options) {
    if (!container) return;
    const opts = options || {};
    const items = (Array.isArray(segments) ? segments : [])
      .map(function (segment) {
        return {
          label: segment.label,
          value: Math.max(0, Number(segment.value) || 0),
          color: segment.color || "var(--primary)",
        };
      })
      .filter(function (segment) {
        return segment.value > 0;
      });

    if (!items.length) {
      message(container, opts.empty || "No outcomes yet");
      return;
    }

    const total = items.reduce(function (sum, item) {
      return sum + item.value;
    }, 0);
    const radius = 64;
    const circumference = 2 * Math.PI * radius;
    const svg = el("svg", {
      viewBox: "0 0 170 170",
      role: "img",
      "aria-label": opts.label || "Donut chart",
    });

    svg.appendChild(
      el("circle", {
        cx: 85,
        cy: 85,
        r: radius,
        fill: "none",
        stroke: "var(--line)",
        "stroke-width": 18,
      })
    );

    let offset = 0;
    items.forEach(function (item) {
      const slice = (item.value / total) * circumference;
      const dash = Math.max(0.5, slice - (items.length > 1 ? 2 : 0));
      const circle = el("circle", {
        cx: 85,
        cy: 85,
        r: radius,
        fill: "none",
        stroke: item.color,
        "stroke-width": 18,
        "stroke-dasharray": dash.toFixed(2) + " " + (circumference - dash).toFixed(2),
        "stroke-dashoffset": (-offset).toFixed(2),
        transform: "rotate(-90 85 85)",
      });
      const title = el("title");
      title.textContent = item.label + ": " + item.value;
      circle.appendChild(title);
      svg.appendChild(circle);
      offset += slice;
    });

    const center = el("text", {
      x: 85,
      y: 82,
      class: "donut-center",
      "text-anchor": "middle",
    });
    center.textContent = typeof opts.centerValue === "function" ? opts.centerValue(total) : String(total);
    svg.appendChild(center);

    const sub = el("text", {
      x: 85,
      y: 98,
      class: "donut-sub",
      "text-anchor": "middle",
    });
    sub.textContent = opts.centerLabel || "outcomes";
    svg.appendChild(sub);

    const legend = document.createElement("div");
    legend.className = "donut-legend";
    legend.innerHTML = items
      .map(function (item) {
        const value = typeof opts.format === "function" ? opts.format(item.value) : String(item.value);
        return (
          '<div><svg class="legend-dot" viewBox="0 0 8 8" aria-hidden="true"><circle cx="4" cy="4" r="3" fill="' +
          escapeHtml(item.color) +
          '"/></svg><span>' +
          escapeHtml(item.label) +
          "</span><strong>" +
          escapeHtml(value) +
          "</strong></div>"
        );
      })
      .join("");

    const wrap = document.createElement("div");
    wrap.className = "donut-wrap";
    wrap.appendChild(svg);
    wrap.appendChild(legend);

    container.innerHTML = "";
    container.appendChild(wrap);
  }

  /* ------------------------------------------------------------------ bars */
  function bars(container, items, options) {
    if (!container) return;
    const opts = options || {};
    const data = (Array.isArray(items) ? items : []).filter(function (item) {
      return Number(item.value) > 0;
    });

    if (!data.length) {
      message(container, opts.empty || "No usage recorded yet");
      return;
    }

    const max = Number(opts.max) > 0
      ? Number(opts.max)
      : Math.max.apply(
          null,
          data.map(function (item) {
            return Number(item.value) || 0;
          })
        );

    const rows = data
      .map(function (item) {
        const value = Number(item.value) || 0;
        const display = typeof opts.formatValue === "function" ? opts.formatValue(value) : String(value);
        return (
          '<div class="hbar-row">' +
          '<span class="hbar-label" title="' +
          escapeHtml(item.label) +
          '">' +
          escapeHtml(item.label) +
          "</span>" +
          '<progress class="meter" max="' + max + '" value="' + value + '" aria-label="' + escapeHtml(item.label) + '"></progress>' +
          '<span class="hbar-value">' +
          escapeHtml(display) +
          "</span>" +
          "</div>"
        );
      })
      .join("");

    container.innerHTML = '<div class="hbars">' + rows + "</div>";
  }

  /* --------------------------------------------------------------- latency */
  function latency(container, points, options) {
    if (!container) return;
    const opts = options || {};
    const all = (Array.isArray(points) ? points : []).filter(function (point) {
      return point && isFinite(Number(point.seconds));
    });

    if (!all.length) {
      message(container, opts.empty || "No completed missions yet");
      return;
    }

    const size = chartSize(container, 220);
    const W = size.width;
    const H = size.height;
    const maxBars = W < 420 ? 6 : 12;
    const data = all.slice(0, maxBars).reverse();
    const pad = { left: 36, right: 10, top: 18, bottom: 34 };
    const plotW = W - pad.left - pad.right;
    const plotH = H - pad.top - pad.bottom;
    const yMax = niceMax(
      Math.max.apply(
        null,
        data.map(function (point) {
          return Number(point.seconds) || 0;
        })
      ) * 1.15
    );

    const svg = el("svg", {
      viewBox: "0 0 " + W + " " + H,
      role: "img",
      "aria-label": opts.label || "Task latency",
    });

    for (let i = 0; i <= 3; i += 1) {
      const value = yMax - (i / 3) * yMax;
      const y = pad.top + (i / 3) * plotH;
      svg.appendChild(
        el("line", {
          x1: pad.left,
          y1: y.toFixed(1),
          x2: W - pad.right,
          y2: y.toFixed(1),
          class: "grid-line",
        })
      );
      const label = el("text", {
        x: pad.left - 7,
        y: (y + 3).toFixed(1),
        class: "axis-label",
        "text-anchor": "end",
      });
      label.textContent = axisText(value, opts.formatY);
      svg.appendChild(label);
    }

    const gap = data.length > 8 ? 4 : 7;
    const rawBar = (plotW - gap * (data.length - 1)) / data.length;
    const barWidth = Math.min(40, Math.max(7, rawBar));
    const totalWidth = data.length * barWidth + gap * (data.length - 1);
    const startX = pad.left + Math.max(0, (plotW - totalWidth) / 2);
    const showValues = data.length <= 9 && barWidth >= 18;
    const showEvery = data.length > 8 && barWidth < 26 ? 2 : 1;

    data.forEach(function (point, index) {
      const value = Number(point.seconds) || 0;
      const barHeight = Math.max(2, (value / yMax) * plotH);
      const x = startX + index * (barWidth + gap);
      const y = H - pad.bottom - barHeight;
      const color = OUTCOME_COLORS[point.outcome] || "var(--primary)";
      const bar = el("rect", {
        x: x.toFixed(1),
        y: y.toFixed(1),
        width: barWidth.toFixed(1),
        height: barHeight.toFixed(1),
        rx: Math.min(3, barWidth / 3),
        fill: color,
      });
      const title = el("title");
      title.textContent =
        (point.fixture || point.id || "mission") +
        " · " +
        value.toFixed(1) +
        "s · " +
        (point.outcome || "unknown");
      bar.appendChild(title);
      svg.appendChild(bar);

      if (showValues) {
        const valueLabel = el("text", {
          x: (x + barWidth / 2).toFixed(1),
          y: Math.max(pad.top, y - 5).toFixed(1),
          class: "axis-label",
          "text-anchor": "middle",
        });
        valueLabel.textContent = value.toFixed(1) + "s";
        svg.appendChild(valueLabel);
      }

      if (index % showEvery === 0 || index === data.length - 1) {
        const label = el("text", {
          x: (x + barWidth / 2).toFixed(1),
          y: H - 14,
          class: "axis-label",
          "text-anchor": "middle",
        });
        label.textContent = point.fixture || String(index + 1);
        svg.appendChild(label);
      }
    });

    container.innerHTML = "";
    container.appendChild(svg);
  }

  window.Charts = {
    line: line,
    donut: donut,
    bars: bars,
    latency: latency,
    colors: OUTCOME_COLORS,
  };
})();
