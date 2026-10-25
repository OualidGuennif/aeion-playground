import * as THREE from "/three.module.js";

const container = document.getElementById("warehouse-canvas");
const dataNode = document.getElementById("warehouse-data");
const detail = document.getElementById("parcel-detail");

if (container && dataNode) {
  let orders = [];
  try {
    orders = JSON.parse(dataNode.textContent || "[]");
  } catch {
    orders = [];
  }

  const palette = {
    PACKING: 0x60a5fa,
    IN_TRANSIT: 0x22d3ee,
    DELIVERED: 0xa3e635,
    REFUNDED: 0xf472b6,
  };
  const zoneNames = [
    ["PACKING", -7.2, 0x60a5fa],
    ["IN TRANSIT", -2.4, 0x22d3ee],
    ["DELIVERED", 2.4, 0xa3e635],
    ["REFUNDED", 7.2, 0xf472b6],
  ];

  const scene = new THREE.Scene();
  scene.fog = new THREE.Fog(0x08111f, 18, 42);

  const camera = new THREE.PerspectiveCamera(42, 1, 0.1, 100);
  const renderer = new THREE.WebGLRenderer({ antialias: true, alpha: true, powerPreference: "high-performance" });
  renderer.setPixelRatio(Math.min(window.devicePixelRatio || 1, 2));
  renderer.shadowMap.enabled = true;
  renderer.shadowMap.type = THREE.PCFSoftShadowMap;
  renderer.toneMapping = THREE.ACESFilmicToneMapping;
  renderer.toneMappingExposure = 1.05;
  container.appendChild(renderer.domElement);

  scene.add(new THREE.HemisphereLight(0x9bd8ff, 0x08111f, 1.15));
  const key = new THREE.DirectionalLight(0xffffff, 2.4);
  key.position.set(8, 15, 7);
  key.castShadow = true;
  key.shadow.mapSize.set(2048, 2048);
  key.shadow.camera.left = -14;
  key.shadow.camera.right = 14;
  key.shadow.camera.top = 14;
  key.shadow.camera.bottom = -14;
  key.shadow.camera.near = 1;
  key.shadow.camera.far = 40;
  key.shadow.bias = -0.0004;
  scene.add(key);
  const cyan = new THREE.PointLight(0x22d3ee, 28, 18, 2);
  cyan.position.set(-7, 4.5, 2);
  scene.add(cyan);
  const lime = new THREE.PointLight(0xa3e635, 22, 16, 2);
  lime.position.set(7, 4, -2);
  scene.add(lime);

  const floor = new THREE.Mesh(
    new THREE.PlaneGeometry(27, 20),
    new THREE.MeshStandardMaterial({ color: 0x0b1524, roughness: 0.88, metalness: 0.18 }),
  );
  floor.rotation.x = -Math.PI / 2;
  floor.receiveShadow = true;
  scene.add(floor);

  const grid = new THREE.GridHelper(27, 27, 0x1d3b54, 0x122436);
  grid.position.y = 0.015;
  grid.material.opacity = 0.65;
  grid.material.transparent = true;
  scene.add(grid);

  const strip = new THREE.Mesh(
    new THREE.BoxGeometry(23, 0.045, 0.22),
    new THREE.MeshBasicMaterial({ color: 0x22d3ee, transparent: true, opacity: 0.55 }),
  );
  strip.position.set(0, 0.03, 4.4);
  scene.add(strip);

  for (const [name, x, color] of zoneNames) {
    const pad = new THREE.Mesh(
      new THREE.PlaneGeometry(4.25, 8.6),
      new THREE.MeshBasicMaterial({ color, transparent: true, opacity: 0.045 }),
    );
    pad.rotation.x = -Math.PI / 2;
    pad.position.set(x, 0.02, 0);
    scene.add(pad);

    const canvas = document.createElement("canvas");
    canvas.width = 512;
    canvas.height = 128;
    const ctx = canvas.getContext("2d");
    ctx.clearRect(0, 0, 512, 128);
    ctx.font = "600 42px monospace";
    ctx.fillStyle = "#dbeafe";
    ctx.textAlign = "center";
    ctx.fillText(name, 256, 82);
    const texture = new THREE.CanvasTexture(canvas);
    texture.colorSpace = THREE.SRGBColorSpace;
    const sprite = new THREE.Sprite(new THREE.SpriteMaterial({ map: texture, transparent: true, opacity: 0.8, depthWrite: false }));
    sprite.scale.set(3.6, 0.9, 1);
    sprite.position.set(x, 3.8, -4.6);
    scene.add(sprite);
  }

  const metal = new THREE.MeshStandardMaterial({ color: 0x24384f, roughness: 0.42, metalness: 0.65 });
  for (const x of [-9.4, 9.4]) {
    const post = new THREE.Mesh(new THREE.BoxGeometry(0.18, 5.2, 0.18), metal);
    post.position.set(x, 2.6, -5.4);
    post.castShadow = true;
    scene.add(post);
  }
  for (const y of [1.5, 3.1, 4.7]) {
    const shelf = new THREE.Mesh(new THREE.BoxGeometry(19, 0.12, 0.9), metal);
    shelf.position.set(0, y, -5.4);
    shelf.castShadow = true;
    shelf.receiveShadow = true;
    scene.add(shelf);
  }

  const shelfBox = new THREE.BoxGeometry(0.55, 0.42, 0.55);
  const shelfColors = [0x1f4f66, 0x2d4a2e, 0x4a3a2a, 0x3b2f52];
  for (let row = 0; row < 3; row += 1) {
    for (let index = 0; index < 14; index += 1) {
      if ((index + row) % 3 === 0) continue;
      const box = new THREE.Mesh(
        shelfBox,
        new THREE.MeshStandardMaterial({ color: shelfColors[(index + row) % shelfColors.length], roughness: 0.7 }),
      );
      box.position.set(-8.6 + index * 1.32, 1.86 + row * 1.6, -5.35);
      box.castShadow = true;
      scene.add(box);
    }
  }

  const zones = [
    { status: "PACKING", x: -7.2 },
    { status: "IN_TRANSIT", x: -2.4 },
    { status: "DELIVERED", x: 2.4 },
    { status: "REFUNDED", x: 7.2 },
  ];
  const counts = {};
  const parcels = [];
  const parcelGeometry = new THREE.BoxGeometry(1, 1, 1);
  for (const order of orders) {
    const zone = zones.find((candidate) => candidate.status === order.status) ?? zones[0];
    const row = (counts[zone.status] = (counts[zone.status] ?? 0) + 1) - 1;
    const size = 0.62 + Math.min(order.grams, 30000) / 42000;
    const color = palette[order.status] ?? 0x94a3b8;
    const material = new THREE.MeshStandardMaterial({
      color,
      roughness: 0.32,
      metalness: 0.22,
      emissive: color,
      emissiveIntensity: 0.06,
    });
    const parcel = new THREE.Mesh(parcelGeometry, material);
    parcel.scale.set(size, size * 0.78, size);
    parcel.position.set(zone.x + ((row % 3) - 1) * 0.42, size * 0.39 + 0.02, -2.6 + Math.floor(row / 3) * 1.55);
    parcel.castShadow = true;
    parcel.receiveShadow = true;
    parcel.userData = { order, baseY: parcel.position.y, phase: Math.random() * Math.PI * 2, size };
    scene.add(parcel);
    parcels.push(parcel);

    const tape = new THREE.Mesh(
      new THREE.BoxGeometry(size * 1.02, 0.025, size * 0.12),
      new THREE.MeshStandardMaterial({ color: 0xf8fafc, roughness: 0.5 }),
    );
    tape.position.set(0, size * 0.4, 0);
    parcel.add(tape);
  }

  const target = new THREE.Vector3(0, 1.2, 0);
  let theta = 0.72;
  let phi = 1.02;
  let radius = 17;
  let dragging = false;
  let dragged = 0;
  let lastX = 0;
  let lastY = 0;
  let autoRotateAt = performance.now() + 3500;
  let selected = null;

  const applyCamera = () => {
    const sinPhi = Math.sin(phi);
    camera.position.set(
      target.x + radius * sinPhi * Math.sin(theta),
      target.y + radius * Math.cos(phi),
      target.z + radius * sinPhi * Math.cos(theta),
    );
    camera.lookAt(target);
  };
  applyCamera();

  const raycaster = new THREE.Raycaster();
  const pointer = new THREE.Vector2();

  const escapeHtml = (value) =>
    String(value ?? "").replace(/[&<>"']/g, (character) => ({
      "&": "&amp;",
      "<": "&lt;",
      ">": "&gt;",
      '"': "&quot;",
      "'": "&#39;",
    })[character]);

  const money = (cents) => new Intl.NumberFormat("en-IE", { style: "currency", currency: "EUR" }).format((cents ?? 0) / 100);

  const selectParcel = (parcel) => {
    if (selected === parcel) return;
    if (selected) selected.material.emissiveIntensity = 0.06;
    selected = parcel;
    if (selected) selected.material.emissiveIntensity = 0.5;
    if (!detail) return;
    if (!parcel) {
      detail.innerHTML = `<span class="detail-label">selected parcel</span><strong>Click a parcel</strong><p>Drag to orbit, scroll to zoom. The scene mirrors the live order table.</p>`;
      return;
    }
    const order = parcel.userData.order;
    detail.innerHTML = `
      <span class="detail-label">selected parcel</span>
      <div class="detail-head"><strong>${escapeHtml(order.id)}</strong><span class="status ${escapeHtml(order.status.toLowerCase())}">${escapeHtml(order.status.replaceAll("_", " "))}</span></div>
      <p>${escapeHtml(order.item)} for ${escapeHtml(order.customer)} · ${escapeHtml(order.city)}</p>
      <div class="detail-metrics"><span>${order.grams.toLocaleString("en-US")} g</span><span>${money(order.totalCents)}</span><span>${escapeHtml(order.status === "REFUNDED" ? (order.refundReason || "refunded") : "in the network")}</span></div>`;
  };

  const pointAt = (event) => {
    const rect = renderer.domElement.getBoundingClientRect();
    pointer.x = ((event.clientX - rect.left) / rect.width) * 2 - 1;
    pointer.y = -((event.clientY - rect.top) / rect.height) * 2 + 1;
  };

  renderer.domElement.addEventListener("pointerdown", (event) => {
    dragging = true;
    dragged = 0;
    lastX = event.clientX;
    lastY = event.clientY;
    renderer.domElement.setPointerCapture(event.pointerId);
    autoRotateAt = performance.now() + 5000;
  });

  renderer.domElement.addEventListener("pointermove", (event) => {
    if (dragging) {
      const dx = event.clientX - lastX;
      const dy = event.clientY - lastY;
      dragged += Math.abs(dx) + Math.abs(dy);
      theta -= dx * 0.006;
      phi = THREE.MathUtils.clamp(phi - dy * 0.005, 0.22, 1.42);
      lastX = event.clientX;
      lastY = event.clientY;
      applyCamera();
      return;
    }
    pointAt(event);
    raycaster.setFromCamera(pointer, camera);
    const hit = raycaster.intersectObjects(parcels, false)[0];
    renderer.domElement.style.cursor = hit ? "pointer" : "grab";
  });

  renderer.domElement.addEventListener("pointerup", (event) => {
    dragging = false;
    if (dragged > 8) return;
    pointAt(event);
    raycaster.setFromCamera(pointer, camera);
    const hit = raycaster.intersectObjects(parcels, false)[0];
    selectParcel(hit ? hit.object : null);
  });

  renderer.domElement.addEventListener("pointercancel", () => {
    dragging = false;
  });

  renderer.domElement.addEventListener("wheel", (event) => {
    event.preventDefault();
    radius = THREE.MathUtils.clamp(radius + event.deltaY * 0.012, 9, 26);
    applyCamera();
    autoRotateAt = performance.now() + 5000;
  }, { passive: false });

  const resize = () => {
    const width = container.clientWidth || 640;
    const height = container.clientHeight || 420;
    renderer.setSize(width, height, false);
    camera.aspect = width / height;
    camera.updateProjectionMatrix();
  };
  new ResizeObserver(resize).observe(container);
  resize();

  const clock = new THREE.Clock();
  const animate = () => {
    const elapsed = clock.getElapsedTime();
    for (const parcel of parcels) {
      parcel.position.y = parcel.userData.baseY + Math.sin(elapsed * 1.4 + parcel.userData.phase) * 0.035;
      parcel.rotation.y = Math.sin(elapsed * 0.5 + parcel.userData.phase) * 0.045;
    }
    if (!dragging && performance.now() > autoRotateAt) {
      theta += 0.0013;
      applyCamera();
    }
    renderer.render(scene, camera);
    requestAnimationFrame(animate);
  };
  animate();

  if (typeof WebGLRenderingContext === "undefined") {
    container.innerHTML = `<div class="scene-loading">WebGL is unavailable in this browser.</div>`;
  }
}
