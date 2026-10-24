/*
 * ParcelDesk — warehouse and support desk.
 *
 * The 3D floor is procedural: no textures, models or CDN requests. Three.js is
 * vendored under /static/vendor/three with its MIT licence. Orders, refunds and
 * tickets come from the local cockpit API; the scene only visualises their counts.
 */
import * as THREE from "/static/vendor/three/three.module.min.js";

const $ = (selector, root = document) => root.querySelector(selector);
const $$ = (selector, root = document) => Array.from(root.querySelectorAll(selector));
const reducedMotion = window.matchMedia("(prefers-reduced-motion: reduce)").matches;
const money = new Intl.NumberFormat("fr-FR", { style: "currency", currency: "EUR" });
const number = new Intl.NumberFormat("fr-FR");

const state = {
  orders: [],
  summary: null,
  tickets: [],
  tab: "orders",
  busy: false,
  toastTimer: null,
  pollTimer: null,
  filter: "",
};

function escapeHtml(value) {
  return String(value ?? "")
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;")
    .replace(/'/g, "&#039;");
}

function toast(message) {
  const node = $("#wh-toast");
  if (!node) return;
  node.textContent = message;
  node.classList.add("show");
  clearTimeout(state.toastTimer);
  state.toastTimer = setTimeout(() => node.classList.remove("show"), 3600);
}

async function api(path, options = {}) {
  const response = await fetch(path, {
    cache: "no-store",
    headers: { "Content-Type": "application/json", ...(options.headers || {}) },
    ...options,
  });
  let data = null;
  try {
    data = await response.json();
  } catch (error) {
    throw new Error("The local API returned an unreadable response.");
  }
  if (!response.ok || data.ok === false) {
    throw new Error(data.error || `Request failed (${response.status}).`);
  }
  return data;
}

function statusLabel(value) {
  return String(value || "unknown").replace(/_/g, " ");
}

function orderRow(order) {
  const refunded = Boolean(order.refund_id);
  const canRefund = order.status === "delivered" && !refunded;
  return `
    <article class="wh-order" data-order="${escapeHtml(order.id)}">
      <div class="wh-order-top">
        <span class="wh-order-id">${escapeHtml(order.id)}</span>
        <span class="wh-order-item">${escapeHtml(order.item)}</span>
      </div>
      <div class="wh-order-right">
        <span class="wh-amount">${escapeHtml(money.format(order.total_cents / 100))}</span>
        <span class="wh-status ${escapeHtml(order.status)}">${escapeHtml(statusLabel(order.status))}</span>
      </div>
      <div class="wh-order-meta">${escapeHtml(order.customer)} · ${escapeHtml(order.city)} · ${number.format(order.grams)} g</div>
      <div class="wh-order-meta">${escapeHtml(order.delivered_at ? "Delivered " + order.delivered_at.slice(0, 10) : "Created " + order.created_at.slice(0, 10))}</div>
      ${refunded ? `<div class="wh-refund-reason"><strong>${escapeHtml(money.format(order.refund_amount / 100))}</strong> refunded · ${escapeHtml(order.refund_reason)}</div>` : ""}
      ${canRefund ? `<button class="wh-refund-link" type="button" data-refund="${escapeHtml(order.id)}">Refund this order →</button>` : ""}
    </article>`;
}

function renderKpis() {
  const summary = state.summary;
  if (!summary) return;
  $("#kpi-orders").textContent = number.format(summary.orders);
  $("#kpi-transit").textContent = number.format(summary.counts.in_transit);
  $("#kpi-refunds").textContent = number.format(summary.counts.refunded);
  $("#kpi-tickets").textContent = number.format(summary.open_tickets);
  $("#tab-orders-count").textContent = summary.orders ? `(${summary.orders})` : "";
}

function renderOrders() {
  const list = $("#orders-list");
  if (!list) return;
  const needle = state.filter.trim().toLowerCase();
  const rows = state.orders.filter((order) => {
    if (!needle) return true;
    return [order.id, order.customer, order.city, order.item, order.status].join(" ").toLowerCase().includes(needle);
  });
  $("#order-count").textContent = `${rows.length} / ${state.orders.length}`;
  list.innerHTML = rows.length
    ? rows.map(orderRow).join("")
    : `<div class="wh-empty">No order matches “${escapeHtml(state.filter)}”.</div>`;
}

function renderSupport() {
  const list = $("#refund-list");
  if (!list) return;
  const refunded = state.orders.filter((order) => order.refund_id);
  const eligible = state.orders.filter((order) => order.status === "delivered" && !order.refund_id);
  const rows = [];
  for (const order of refunded) {
    rows.push(`
      <article class="wh-order">
        <div class="wh-order-top"><span class="wh-order-id">${escapeHtml(order.id)}</span><span class="wh-order-item">${escapeHtml(order.item)}</span></div>
        <div class="wh-order-right"><span class="wh-amount">${escapeHtml(money.format(order.refund_amount / 100))}</span><span class="wh-status refunded">refunded</span></div>
        <div class="wh-refund-reason">${escapeHtml(order.refund_reason)}</div>
      </article>`);
  }
  for (const order of eligible) {
    rows.push(`
      <article class="wh-order">
        <div class="wh-order-top"><span class="wh-order-id">${escapeHtml(order.id)}</span><span class="wh-order-item">${escapeHtml(order.item)}</span></div>
        <div class="wh-order-right"><span class="wh-amount">${escapeHtml(money.format(order.total_cents / 100))}</span><span class="wh-status delivered">delivered</span></div>
        <div class="wh-order-meta">${escapeHtml(order.customer)} · ${escapeHtml(order.city)}</div>
        <button class="wh-refund-link" type="button" data-refund="${escapeHtml(order.id)}">Refund this order →</button>
      </article>`);
  }
  list.innerHTML = rows.length ? rows.join("") : `<div class="wh-empty">No refundable order right now.</div>`;
  const select = $("#refund-order");
  if (select) {
    select.innerHTML = eligible.length
      ? eligible.map((order) => `<option value="${escapeHtml(order.id)}">${escapeHtml(order.id)} · ${escapeHtml(order.customer)} · ${escapeHtml(money.format(order.total_cents / 100))}</option>`).join("")
      : `<option value="">No eligible order</option>`;
  }
}

function renderTickets() {
  const list = $("#tickets-list");
  if (!list) return;
  $("#tickets-count").textContent = state.tickets.length ? `${state.tickets.length}` : "";
  if (!state.tickets.length) {
    list.innerHTML = `<div class="wh-empty">No ticket yet. The first one starts a real mission.</div>`;
    return;
  }
  list.innerHTML = state.tickets.map((ticket) => {
    const mission = ticket.mission;
    const status = mission ? mission.status : ticket.status;
    const detail = mission
      ? `${escapeHtml(mission.id)} · ${escapeHtml(mission.current_step || "queued")}${mission.gate_reason_code ? " · " + escapeHtml(mission.gate_reason_code) : ""}`
      : "mission not linked";
    const reason = mission && mission.gate_reason ? `<div class="wh-ticket-reason">${escapeHtml(mission.gate_reason)}</div>` : "";
    const error = ticket.error ? `<div class="wh-ticket-reason">${escapeHtml(ticket.error)}</div>` : "";
    return `
      <article class="wh-ticket">
        <div class="wh-ticket-top">
          <span class="wh-ticket-title">${escapeHtml(ticket.title)}</span>
          <span class="wh-status ${escapeHtml(status)}">${escapeHtml(statusLabel(status))}</span>
        </div>
        <div class="wh-ticket-meta">${escapeHtml(ticket.id)} · ${escapeHtml(ticket.area)} · risk ${escapeHtml(ticket.risk)} · ${detail}</div>
        ${reason}${error}
      </article>`;
  }).join("");
}

function renderAll() {
  renderKpis();
  renderOrders();
  renderSupport();
  renderTickets();
  if (window.warehouseScene) {
    const transit = state.summary ? state.summary.counts.in_transit : 4;
    window.warehouseScene.setActive(transit);
  }
}

async function loadData() {
  try {
    const [summary, orders, tickets] = await Promise.all([
      api("/api/parceldesk/summary"),
      api("/api/parceldesk/orders"),
      api("/api/parceldesk/tickets"),
    ]);
    state.summary = summary;
    state.orders = orders.orders;
    state.tickets = tickets.tickets;
    renderAll();
  } catch (error) {
    $("#orders-list").innerHTML = `<div class="wh-empty">${escapeHtml(error.message)}</div>`;
    $("#refund-list").innerHTML = `<div class="wh-empty">${escapeHtml(error.message)}</div>`;
    $("#tickets-list").innerHTML = `<div class="wh-empty">${escapeHtml(error.message)}</div>`;
  }
}

function activateTab(name) {
  state.tab = name;
  $$(".wh-tabs button").forEach((button) => {
    const active = button.dataset.tab === name;
    button.setAttribute("aria-selected", active ? "true" : "false");
    button.tabIndex = active ? 0 : -1;
  });
  for (const view of ["orders", "support", "factory"]) {
    const panel = $(`#view-${view}`);
    if (panel) panel.hidden = view !== name;
  }
  if (name === "orders") $("#order-search")?.focus({ preventScroll: true });
  if (name === "support") $("#refund-reason")?.focus({ preventScroll: true });
}

function chooseRefund(orderId) {
  activateTab("support");
  const select = $("#refund-order");
  if (select) select.value = orderId;
  $("#refund-reason")?.focus();
}

async function submitRefund(event) {
  event.preventDefault();
  const orderId = $("#refund-order")?.value;
  const reason = $("#refund-reason")?.value || "";
  const note = $("#refund-note");
  if (!orderId) {
    if (note) { note.textContent = "No delivered order is eligible for a refund."; note.className = "wh-note error"; }
    return;
  }
  const button = $("#refund-submit");
  if (button) button.disabled = true;
  try {
    const result = await api(`/api/parceldesk/orders/${encodeURIComponent(orderId)}/refund`, {
      method: "POST",
      body: JSON.stringify({ reason }),
    });
    state.orders = result.orders;
    state.summary = result.summary;
    renderAll();
    if ($("#refund-reason")) $("#refund-reason").value = "";
    if (note) {
      note.textContent = `${result.refund.id}: ${money.format(result.refund.amount_cents / 100)} refunded to ${orderId}.`;
      note.className = "wh-note ok";
    }
    toast(`Refund recorded for ${orderId}.`);
  } catch (error) {
    if (note) { note.textContent = error.message; note.className = "wh-note error"; }
  } finally {
    if (button) button.disabled = false;
  }
}

async function submitTicket(event) {
  event.preventDefault();
  const note = $("#ticket-note");
  const button = $("#ticket-submit");
  const payload = {
    title: $("#ticket-title").value.trim(),
    outcome: $("#ticket-outcome").value.trim(),
    area: $("#ticket-area").value,
    risk: $("#ticket-risk").value,
    acceptance: $("#ticket-acceptance").value.trim(),
    constraints: $("#ticket-constraints").value.trim(),
  };
  if (payload.outcome.length < 10 || payload.acceptance.length < 10) {
    if (note) { note.textContent = "Outcome and acceptance checks need at least 10 characters."; note.className = "wh-note error"; }
    return;
  }
  if (button) button.disabled = true;
  try {
    const result = await api("/api/parceldesk/tickets", { method: "POST", body: JSON.stringify(payload) });
    if (note) {
      note.textContent = `Ticket ${result.ticket.id} became mission ${result.mission.id}. The Gate decides; a blocked mission is a normal outcome.`;
      note.className = "wh-note ok";
    }
    $("#ticket-form").reset();
    state.tickets = [result.ticket, ...state.tickets];
    renderTickets();
    toast(`Mission ${result.mission.id} admitted from ${result.ticket.id}.`);
    pollSoon();
  } catch (error) {
    if (note) { note.textContent = error.message; note.className = "wh-note error"; }
  } finally {
    if (button) button.disabled = false;
  }
}

function pollSoon() {
  clearTimeout(state.pollTimer);
  state.pollTimer = setTimeout(async () => {
    await loadData();
    const live = state.tickets.some((ticket) => ticket.mission && ["queued", "running"].includes(ticket.mission.status));
    if (live) pollSoon();
  }, 4000);
}

/* ------------------------------------------------------------------ 3D floor */
function createWarehouse(canvas) {
  let renderer;
  try {
    renderer = new THREE.WebGLRenderer({ canvas, antialias: true, alpha: true, powerPreference: "high-performance", preserveDrawingBuffer: true });
  } catch (error) {
    return null;
  }
  renderer.setPixelRatio(Math.min(window.devicePixelRatio || 1, 2));
  renderer.outputColorSpace = THREE.SRGBColorSpace;
  renderer.toneMapping = THREE.ACESFilmicToneMapping;
  renderer.toneMappingExposure = 1.62;
  renderer.shadowMap.enabled = true;
  renderer.shadowMap.type = THREE.PCFSoftShadowMap;

  const scene = new THREE.Scene();
  scene.fog = new THREE.FogExp2(0x0b1a14, 0.022);
  const camera = new THREE.PerspectiveCamera(40, 1, 0.1, 120);
  const target = new THREE.Vector3(1.4, 1.15, -1.4);
  let yaw = 0.30, pitch = 0.94, distance = 32;
  let dragging = false, lastX = 0, lastY = 0, lastInteraction = performance.now();
  let active = 8;
  let disposed = false;

  const material = (color, opts = {}) => new THREE.MeshStandardMaterial({ color, roughness: 0.78, metalness: 0.08, ...opts });
  const box = (w, h, d, color, opts = {}) => {
    const mesh = new THREE.Mesh(new THREE.BoxGeometry(w, h, d), material(color, opts));
    mesh.castShadow = true;
    mesh.receiveShadow = true;
    return mesh;
  };
  const palette = [0xd8f26a, 0xf5b85c, 0x4fd1b0, 0xff8a65, 0xe9e2cf, 0x9db8ff];

  // Lights.
  scene.add(new THREE.HemisphereLight(0xd6f4e5, 0x1a2f25, 3.6));
  const sun = new THREE.DirectionalLight(0xfff4db, 4.4);
  sun.position.set(-8, 16, 10);
  sun.castShadow = true;
  sun.shadow.mapSize.set(1024, 1024);
  sun.shadow.camera.left = -18;
  sun.shadow.camera.right = 18;
  sun.shadow.camera.top = 16;
  sun.shadow.camera.bottom = -16;
  sun.shadow.camera.near = 1;
  sun.shadow.camera.far = 42;
  scene.add(sun);
  for (const [x, z, color, power] of [[-9, 4, 0x4fd1b0, 26], [5, -4, 0xd8f26a, 22], [10, 5, 0xf25b35, 18]]) {
    const light = new THREE.PointLight(color, power, 18, 2);
    light.position.set(x, 5, z);
    scene.add(light);
  }

  // Floor and grid.
  const floor = box(30, 0.24, 20, 0x1d3529);
  floor.position.y = -0.12;
  scene.add(floor);
  const grid = new THREE.GridHelper(30, 30, 0x5a806b, 0x274437);
  grid.position.y = 0.01;
  grid.material.transparent = true;
  grid.material.opacity = 0.7;
  scene.add(grid);

  // Back wall and a procedural sign.
  const wall = box(30, 4.6, 0.3, 0x14251d);
  wall.position.set(0, 2.3, -10);
  scene.add(wall);
  const signCanvas = document.createElement("canvas");
  signCanvas.width = 1024;
  signCanvas.height = 256;
  const context = signCanvas.getContext("2d");
  context.fillStyle = "#0b1611";
  context.fillRect(0, 0, 1024, 256);
  context.fillStyle = "#d8f26a";
  context.font = "700 86px Inter, Arial, sans-serif";
  context.fillText("PARCELDESK", 56, 118);
  context.fillStyle = "#8fa49a";
  context.font = "500 24px monospace";
  context.fillText("FULFILMENT · LYON · 45.76° N", 58, 164);
  for (let index = 0; index < 42; index += 1) {
    context.fillStyle = index % 3 ? "#d8f26a" : "#4fd1b0";
    context.fillRect(700 + index * 7, 58, 3, 112);
  }
  const signTexture = new THREE.CanvasTexture(signCanvas);
  signTexture.colorSpace = THREE.SRGBColorSpace;
  const sign = new THREE.Mesh(new THREE.PlaneGeometry(8.4, 2.1), new THREE.MeshBasicMaterial({ map: signTexture, transparent: true }));
  sign.position.set(0, 3.0, -9.82);
  scene.add(sign);

  // Racks with parcels.
  const racks = new THREE.Group();
  for (const [index, x] of [-8.6, -5.2, -1.8].entries()) {
    const rack = new THREE.Group();
    const metal = material(0x466456, { metalness: 0.35, roughness: 0.5 });
    for (const dx of [-1.12, 1.12]) {
      for (const dz of [-0.38, 0.38]) {
        const post = new THREE.Mesh(new THREE.BoxGeometry(0.1, 3.15, 0.1), metal);
        post.position.set(dx, 1.58, dz);
        post.castShadow = true;
        rack.add(post);
      }
    }
    for (const [level, y] of [0.52, 1.3, 2.08, 2.86].entries()) {
      const shelf = box(2.4, 0.08, 0.94, 0x304b3d);
      shelf.position.y = y;
      rack.add(shelf);
      for (const [slot, bx] of [-0.78, 0, 0.78].entries()) {
        const parcel = box(0.42, 0.3, 0.38, palette[(index + level + slot) % palette.length]);
        parcel.position.set(bx, y + 0.2, 0);
        rack.add(parcel);
        const label = box(0.2, 0.015, 0.22, 0xf4f0e6);
        label.position.set(bx, y + 0.365, 0);
        label.castShadow = false;
        rack.add(label);
      }
    }
    rack.position.set(x, 0, -7.2);
    racks.add(rack);
  }
  scene.add(racks);

  // Conveyor with moving parcels and stripes.
  const conveyor = new THREE.Group();
  const belt = box(16, 0.14, 1.4, 0x20372c);
  belt.position.y = 0.86;
  conveyor.add(belt);
  for (const dz of [-0.78, 0.78]) {
    const rail = box(16, 0.07, 0.07, 0x3b5648);
    rail.position.set(0, 0.96, dz);
    conveyor.add(rail);
  }
  for (let x = -7.6; x <= 7.6; x += 0.8) {
    const roller = new THREE.Mesh(new THREE.CylinderGeometry(0.085, 0.085, 1.3, 10), material(0x466152, { metalness: 0.4 }));
    roller.rotation.x = Math.PI / 2;
    roller.position.set(x, 0.93, 0);
    conveyor.add(roller);
  }
  for (const x of [-7.4, -2.5, 2.5, 7.4]) {
    for (const dz of [-0.6, 0.6]) {
      const leg = box(0.09, 0.86, 0.09, 0x22352c);
      leg.position.set(x, 0.43, dz);
      conveyor.add(leg);
    }
  }
  const stripes = [];
  for (let index = 0; index < 20; index += 1) {
    const stripe = box(0.46, 0.02, 1.18, 0x2a4438);
    stripe.position.set(-7.6 + index * 0.8, 0.945, 0);
    stripe.castShadow = false;
    stripe.userData.base = -7.6 + index * 0.8;
    conveyor.add(stripe);
    stripes.push(stripe);
  }
  const parcels = [];
  for (let index = 0; index < 10; index += 1) {
    const parcel = box(0.5, 0.4, 0.5, palette[index % palette.length]);
    parcel.position.set(-7 + index * 1.4, 1.17, 0);
    parcel.userData.offset = index * 1.55;
    parcel.userData.speed = 0.8 + (index % 3) * 0.12;
    conveyor.add(parcel);
    parcels.push(parcel);
    const label = box(0.24, 0.015, 0.3, 0xf4f0e6);
    label.position.set(0, 0.21, 0);
    label.castShadow = false;
    parcel.add(label);
  }
  conveyor.position.set(0, 0, 1.9);
  scene.add(conveyor);

  // Scan arch.
  const arch = new THREE.Group();
  for (const dz of [-1.02, 1.02]) {
    const post = box(0.13, 2.8, 0.13, 0x355244, { metalness: 0.35 });
    post.position.set(3.4, 1.4, dz);
    arch.add(post);
  }
  const beam = box(0.13, 0.18, 2.18, 0x355244);
  beam.position.set(3.4, 2.8, 0);
  arch.add(beam);
  const scanLight = new THREE.Mesh(new THREE.BoxGeometry(0.05, 2.4, 1.9),
    new THREE.MeshBasicMaterial({ color: 0x4fd1b0, transparent: true, opacity: 0.22, side: THREE.DoubleSide }));
  scanLight.position.set(3.4, 1.22, 0);
  arch.add(scanLight);
  conveyor.add(arch);
  const beacon = new THREE.Mesh(new THREE.SphereGeometry(0.09, 10, 8), new THREE.MeshBasicMaterial({ color: 0x4fd1b0 }));
  beacon.position.set(3.4, 2.95, 0);
  conveyor.add(beacon);

  // Packing tables and boxes.
  const tableGroup = new THREE.Group();
  for (const x of [5.7, 8.7]) {
    const top = box(2.2, 0.1, 1.25, 0x2c4438);
    top.position.set(x, 0.95, -3.4);
    tableGroup.add(top);
    for (const dx of [-0.9, 0.9]) {
      for (const dz of [-0.45, 0.45]) {
        const leg = box(0.08, 0.95, 0.08, 0x1d3028);
        leg.position.set(x + dx, 0.48, -3.4 + dz);
        tableGroup.add(leg);
      }
    }
    for (const [index, dx] of [-0.55, 0, 0.55].entries()) {
      const parcel = box(0.42, 0.3, 0.42, palette[(index + (x > 6 ? 2 : 0)) % palette.length]);
      parcel.position.set(x + dx, 1.15, -3.4);
      tableGroup.add(parcel);
    }
  }
  scene.add(tableGroup);

  // Truck at the dock.
  const truck = new THREE.Group();
  const cargo = box(3.4, 2.5, 2.3, 0xc6d7c8);
  cargo.position.set(10.8, 1.55, 3.2);
  truck.add(cargo);
  const cab = box(1.5, 1.8, 2.2, 0x1f3b31);
  cab.position.set(8.35, 1.05, 3.2);
  truck.add(cab);
  const windscreen = box(0.04, 0.6, 1.7, 0x8fd6c4);
  windscreen.position.set(7.6, 1.5, 3.2);
  truck.add(windscreen);
  for (const x of [8.1, 9.6, 11.7]) {
    for (const dz of [2.15, 4.25]) {
      const wheel = new THREE.Mesh(new THREE.CylinderGeometry(0.34, 0.34, 0.22, 14), material(0x0a0f0d, { roughness: 0.9 }));
      wheel.rotation.x = Math.PI / 2;
      wheel.position.set(x, 0.34, dz);
      truck.add(wheel);
    }
  }
  const stripe = box(3.3, 0.18, 0.02, 0xd8f26a);
  stripe.position.set(10.8, 1.8, 2.03);
  truck.add(stripe);
  truck.position.x = -1.8;
  truck.scale.setScalar(0.86);
  scene.add(truck);

  // Dock markings: alternating hazard blocks in front of the truck.
  for (let index = 0; index < 9; index += 1) {
    const stripe = box(0.86, 0.018, 0.48, index % 2 ? 0xd8f26a : 0x1a2b24);
    stripe.position.set(5.1 + index * 0.9, 0.016, 5.6);
    stripe.rotation.y = -0.12;
    stripe.castShadow = false;
    scene.add(stripe);
  }
  const guide = box(8.4, 0.012, 0.06, 0x4fd1b0);
  guide.position.set(8.9, 0.016, 4.7);
  guide.castShadow = false;
  scene.add(guide);

  // Workers.
  const workers = [];
  const walkers = [];
  function makeWorker(vest, x, z, rotation = 0) {
    const group = new THREE.Group();
    const legs = [];
    for (const dx of [-0.11, 0.11]) {
      const leg = new THREE.Mesh(new THREE.CylinderGeometry(0.065, 0.075, 0.58, 8), material(0x1a2b24));
      leg.position.set(dx, 0.3, 0);
      leg.castShadow = true;
      group.add(leg);
      legs.push(leg);
    }
    const body = new THREE.Mesh(new THREE.CylinderGeometry(0.22, 0.27, 0.66, 10), material(vest));
    body.position.y = 0.94;
    body.castShadow = true;
    group.add(body);
    const head = new THREE.Mesh(new THREE.SphereGeometry(0.17, 16, 12), material(0xe3b58f));
    head.position.y = 1.43;
    head.castShadow = true;
    group.add(head);
    const cap = new THREE.Mesh(new THREE.CylinderGeometry(0.18, 0.18, 0.07, 12), material(0x1a2b24));
    cap.position.y = 1.56;
    group.add(cap);
    const arms = [];
    for (const dx of [-0.27, 0.27]) {
      const arm = new THREE.Mesh(new THREE.CylinderGeometry(0.05, 0.055, 0.52, 8), material(vest));
      arm.position.set(dx, 1.0, 0);
      arm.rotation.z = dx > 0 ? -0.2 : 0.2;
      arm.castShadow = true;
      group.add(arm);
      arms.push(arm);
    }
    const shadow = new THREE.Mesh(new THREE.CircleGeometry(0.32, 16), new THREE.MeshBasicMaterial({ color: 0x000000, transparent: true, opacity: 0.28 }));
    shadow.rotation.x = -Math.PI / 2;
    shadow.position.y = 0.012;
    group.add(shadow);
    group.position.set(x, 0, z);
    group.rotation.y = rotation;
    group.userData = { legs, arms, baseX: x, baseZ: z, phase: Math.random() * Math.PI * 2 };
    scene.add(group);
    workers.push(group);
    return group;
  }
  makeWorker(0xd8f26a, 5.4, -1.7, 0.2);
  makeWorker(0xf5b85c, 8.4, -1.7, -0.2);
  makeWorker(0x4fd1b0, -1.2, 0.35, Math.PI);
  const walker = makeWorker(0xff8a65, -6.4, -4.7, Math.PI / 2);
  walkers.push(walker);

  // A forklift silhouette adds depth to the aisle.
  const forklift = new THREE.Group();
  const fbody = box(1.5, 0.7, 0.9, 0xc8a33c);
  fbody.position.set(-10.9, 0.55, -1.8);
  forklift.add(fbody);
  const mast = box(0.12, 2.1, 0.12, 0x2b3c33);
  mast.position.set(-10.2, 1.35, -1.8);
  forklift.add(mast);
  const fork = box(1.0, 0.08, 0.5, 0x2b3c33);
  fork.position.set(-10.5, 0.35, -1.8);
  forklift.add(fork);
  for (const dx of [-0.45, 0.45]) {
    for (const dz of [-0.35, 0.35]) {
      const wheel = new THREE.Mesh(new THREE.CylinderGeometry(0.22, 0.22, 0.16, 12), material(0x101813));
      wheel.rotation.x = Math.PI / 2;
      wheel.position.set(-10.9 + dx, 0.22, -1.8 + dz);
      forklift.add(wheel);
    }
  }
  scene.add(forklift);

  function placeCamera() {
    const horizontal = Math.sin(pitch) * distance;
    camera.position.set(target.x + Math.sin(yaw) * horizontal, target.y + Math.cos(pitch) * distance, target.z + Math.cos(yaw) * horizontal);
    camera.lookAt(target);
  }

  function resize() {
    const width = canvas.clientWidth || canvas.parentElement.clientWidth;
    const height = canvas.clientHeight || canvas.parentElement.clientHeight;
    if (!width || !height) return;
    renderer.setSize(width, height, false);
    camera.aspect = width / height;
    camera.updateProjectionMatrix();
  }
  const observer = new ResizeObserver(resize);
  observer.observe(canvas);
  resize();
  placeCamera();

  canvas.addEventListener("pointerdown", (event) => {
    dragging = true;
    lastX = event.clientX;
    lastY = event.clientY;
    lastInteraction = performance.now();
    canvas.setPointerCapture(event.pointerId);
  });
  canvas.addEventListener("pointermove", (event) => {
    if (!dragging) return;
    yaw -= (event.clientX - lastX) * 0.006;
    pitch = Math.min(1.34, Math.max(0.55, pitch - (event.clientY - lastY) * 0.005));
    lastX = event.clientX;
    lastY = event.clientY;
    lastInteraction = performance.now();
    placeCamera();
  });
  const release = () => { dragging = false; lastInteraction = performance.now(); };
  canvas.addEventListener("pointerup", release);
  canvas.addEventListener("pointercancel", release);
  canvas.addEventListener("wheel", (event) => {
    event.preventDefault();
    distance = Math.min(40, Math.max(18, distance + event.deltaY * 0.02));
    lastInteraction = performance.now();
    placeCamera();
  }, { passive: false });

  const clock = new THREE.Clock();
  let elapsed = 0;
  function frame() {
    if (disposed) return;
    const delta = Math.min(clock.getDelta(), 0.1);
    elapsed += delta;
    if (!reducedMotion && !dragging && performance.now() - lastInteraction > 6000) {
      yaw += delta * 0.045;
      placeCamera();
    }
    for (const [index, stripe] of stripes.entries()) {
      stripe.position.x = ((stripe.userData.base + elapsed * 1.7) % 16 + 16) % 16 - 8;
    }
    for (const [index, parcel] of parcels.entries()) {
      if (!parcel.visible) continue;
      parcel.position.x = ((parcel.userData.offset + elapsed * parcel.userData.speed) % 16 + 16) % 16 - 8;
      parcel.position.y = 1.17 + Math.sin(elapsed * 3 + index) * 0.012;
    }
    for (const [index, worker] of workers.entries()) {
      if (worker === walker) {
        const travel = (elapsed * 0.32) % 10;
        worker.position.x = -6.4 + travel;
        worker.position.z = -4.7 + Math.sin(travel * 0.9) * 0.25;
        worker.rotation.y = Math.PI / 2;
      }
      worker.position.y = Math.sin(elapsed * 2.2 + index) * 0.018;
      worker.userData.arms[0].rotation.x = Math.sin(elapsed * 2 + index) * 0.35;
      worker.userData.arms[1].rotation.x = -Math.sin(elapsed * 2 + index) * 0.35;
      worker.userData.legs[0].rotation.x = Math.sin(elapsed * 5 + index) * 0.28;
      worker.userData.legs[1].rotation.x = -Math.sin(elapsed * 5 + index) * 0.28;
    }
    scanLight.material.opacity = 0.14 + Math.abs(Math.sin(elapsed * 2.4)) * 0.18;
    beacon.material.color.setHex(Math.sin(elapsed * 4) > 0 ? 0x4fd1b0 : 0xd8f26a);
    renderer.render(scene, camera);
  }
  renderer.setAnimationLoop(frame);

  return {
    renderOnce() {
      placeCamera();
      renderer.render(scene, camera);
    },
    setActive(count) {
      active = Math.max(0, Math.min(parcels.length, Number(count) || 0));
      parcels.forEach((parcel, index) => { parcel.visible = index < active; });
    },
    dispose() {
      disposed = true;
      renderer.setAnimationLoop(null);
      observer.disconnect();
      renderer.dispose();
    },
  };
}

/* ------------------------------------------------------------------ wiring */
function init() {
  const canvas = $("#wh-canvas");
  if (canvas) {
    const scene = createWarehouse(canvas);
    if (scene) {
      window.warehouseScene = scene;
    } else {
      canvas.hidden = true;
      $("#wh-fallback").hidden = false;
    }
  }

  $$(".wh-tabs button").forEach((button) => {
    button.addEventListener("click", () => activateTab(button.dataset.tab));
    button.addEventListener("keydown", (event) => {
      if (!["ArrowLeft", "ArrowRight"].includes(event.key)) return;
      const buttons = $$(".wh-tabs button");
      const index = buttons.indexOf(button);
      const next = buttons[(index + (event.key === "ArrowRight" ? 1 : buttons.length - 1)) % buttons.length];
      next.focus();
      activateTab(next.dataset.tab);
    });
  });
  $("#order-search")?.addEventListener("input", (event) => {
    state.filter = event.target.value;
    renderOrders();
  });
  document.addEventListener("click", (event) => {
    const button = event.target.closest("[data-refund]");
    if (button) chooseRefund(button.dataset.refund);
  });
  $("#refund-form")?.addEventListener("submit", submitRefund);
  $("#ticket-form")?.addEventListener("submit", submitTicket);

  function tickClock() {
    const node = $("#wh-clock");
    if (node) node.textContent = new Date().toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
  }
  tickClock();
  setInterval(tickClock, 30000);

  loadData();
  setInterval(() => {
    if (document.visibilityState === "visible") loadData();
  }, 30000);
}

if (document.readyState === "loading") {
  document.addEventListener("DOMContentLoaded", init);
} else {
  init();
}
