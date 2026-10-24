/* Local appearance, accessible navigation and a restrained decorative star field. */
(function () {
  "use strict";
  const root = document.documentElement;
  const preference = window.matchMedia("(prefers-color-scheme: light)");
  const motion = window.matchMedia("(prefers-reduced-motion: reduce)");
  const mobile = window.matchMedia("(max-width: 720px)");
  let explicit = null;
  try { explicit = window.localStorage.getItem("aeion-appearance"); } catch (_) { /* Storage can be unavailable. */ }
  if (explicit !== "light" && explicit !== "dark") explicit = null;
  root.dataset.theme = explicit || (preference.matches ? "light" : "dark");
  root.classList.add("has-js");

  function ready() {
    const buttons = document.querySelectorAll("[data-theme-toggle]");
    let redraw = function () {};
    function updateTheme(theme) {
      root.dataset.theme = theme;
      buttons.forEach(function (button) {
        button.setAttribute("aria-label", "Switch to " + (theme === "dark" ? "light" : "dark") + " appearance");
        button.setAttribute("aria-pressed", String(theme === "light"));
      });
      redraw();
      document.dispatchEvent(new CustomEvent("aeion:appearancechange"));
    }
    buttons.forEach(function (button) {
      button.addEventListener("click", function () {
        explicit = root.dataset.theme === "dark" ? "light" : "dark";
        try { window.localStorage.setItem("aeion-appearance", explicit); } catch (_) { /* Keep the in-memory choice. */ }
        updateTheme(explicit);
      });
    });
    preference.addEventListener("change", function () { if (!explicit) updateTheme(preference.matches ? "light" : "dark"); });
    updateTheme(root.dataset.theme);

    const navigation = document.getElementById("workspace-navigation");
    let returnFocus = null;
    function setMenu(open) {
      const expanded = !!open && mobile.matches;
      if (expanded) returnFocus = document.activeElement;
      document.body.classList.toggle("menu-open", expanded);
      document.querySelectorAll("[data-menu-toggle]").forEach(function (button) { button.setAttribute("aria-expanded", String(expanded)); });
      if (navigation) navigation.inert = mobile.matches && !expanded;
      document.querySelectorAll(".shell, .mobile-nav").forEach(function (region) { region.inert = expanded; });
      if (expanded && navigation) {
        const first = navigation.querySelector("a, button");
        if (first) first.focus();
      } else if (returnFocus) {
        returnFocus.focus();
        returnFocus = null;
      }
    }
    document.addEventListener("click", function (event) {
      if (!(event.target instanceof Element)) return;
      if (event.target.closest("[data-menu-toggle]")) setMenu(!document.body.classList.contains("menu-open"));
      else if (event.target.closest("[data-menu-close], .nav-item")) setMenu(false);
    });
    document.addEventListener("keydown", function (event) {
      if (!document.body.classList.contains("menu-open")) return;
      if (event.key === "Escape") { event.preventDefault(); setMenu(false); }
      if (event.key === "Tab" && navigation) {
        const targets = Array.from(navigation.querySelectorAll("a, button:not([disabled])"));
        const first = targets[0], last = targets[targets.length - 1];
        if (!navigation.contains(document.activeElement)) { event.preventDefault(); first.focus(); }
        else if (event.shiftKey && document.activeElement === first) { event.preventDefault(); last.focus(); }
        else if (!event.shiftKey && document.activeElement === last) { event.preventDefault(); first.focus(); }
      }
    });
    mobile.addEventListener("change", function () { setMenu(false); });
    setMenu(false);

    const canvas = document.getElementById("starfield");
    if (!canvas) return;
    const context = canvas.getContext("2d");
    if (!context) return;
    let width = 0, height = 0, stars = [], frame = 0, lastPaint = 0, offset = 0;
    let seed = 71;
    function random() { seed = (seed * 16807) % 2147483647; return (seed - 1) / 2147483646; }
    function paint(time) {
      const styles = getComputedStyle(root);
      context.clearRect(0, 0, width, height);
      stars.forEach(function (star) {
        const shimmer = motion.matches ? 0.7 : 0.66 + Math.sin(time * 0.00022 + star.phase) * 0.18;
        context.globalAlpha = star.opacity * shimmer;
        context.fillStyle = star.bright ? styles.getPropertyValue("--star").trim() : styles.getPropertyValue("--star-dim").trim();
        const y = (star.y + offset * star.depth) % height;
        context.beginPath(); context.arc(star.x, y, star.radius, 0, Math.PI * 2); context.fill();
        if (star.bright) {
          context.globalAlpha *= 0.22;
          context.fillRect(star.x - 3, y - 0.4, 6, 0.8);
          context.fillRect(star.x - 0.4, y - 3, 0.8, 6);
        }
      });
      context.globalAlpha = 1;
    }
    function resize() {
      width = window.innerWidth; height = window.innerHeight;
      const ratio = Math.min(window.devicePixelRatio || 1, 1.5);
      canvas.width = Math.round(width * ratio); canvas.height = Math.round(height * ratio);
      context.setTransform(ratio, 0, 0, ratio, 0, 0);
      seed = 71;
      const count = Math.min(220, Math.max(80, Math.round(width * height / 7000)));
      stars = Array.from({ length: count }, function () {
        return { x: random() * width, y: random() * height, radius: 0.35 + random() * 0.6, depth: 0.15 + random() * 0.5, opacity: 0.25 + random() * 0.65, phase: random() * 6.3, bright: random() > 0.96 };
      });
      paint(performance.now());
    }
    function tick(time) {
      if (document.hidden || motion.matches) { frame = 0; return; }
      if (time - lastPaint >= 80) { offset += 0.025; paint(time); lastPaint = time; }
      frame = window.requestAnimationFrame(tick);
    }
    function resume() {
      if (frame) window.cancelAnimationFrame(frame);
      frame = 0;
      paint(performance.now());
      if (!document.hidden && !motion.matches) frame = window.requestAnimationFrame(tick);
    }
    redraw = function () { paint(performance.now()); };
    window.addEventListener("resize", resize);
    document.addEventListener("visibilitychange", resume);
    motion.addEventListener("change", resume);
    resize(); resume();
  }
  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", ready);
  else ready();
})();
