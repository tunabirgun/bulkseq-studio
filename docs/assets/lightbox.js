/* BulkSeq Studio docs — image lightbox with zoom & pan.
   Vanilla JS, no dependencies, no external requests. Every <figure> img on the
   page becomes clickable; the overlay shows it full resolution over a dark
   backdrop, wheel/buttons/double-click zoom (centered on the cursor), and
   click-drag / touch-drag pan when zoomed in. */
(function () {
  "use strict";

  var MIN = 1;      // fit-to-viewport
  var MAX = 8;      // hard zoom ceiling
  var STEP = 1.35;  // per wheel-notch / button-press factor
  var DBL = 2.5;    // double-click zoom-in level

  var overlay = null, imgEl = null, capEl = null, hintEl = null;
  var scale = 1, tx = 0, ty = 0;      // current transform state
  var trigger = null;                  // element that opened the overlay
  var reduceMotion = false;

  // active pointers for pan + pinch
  var pointers = {};                   // id -> {x,y}
  var panning = false, panStartX = 0, panStartY = 0, panTX = 0, panTY = 0;
  var pinchDist = 0, pinchScale = 1, pinchMidX = 0, pinchMidY = 0;

  function clamp(v, lo, hi) { return v < lo ? lo : (v > hi ? hi : v); }

  function buildOverlay() {
    overlay = document.createElement("div");
    overlay.className = "lb-overlay";
    overlay.setAttribute("role", "dialog");
    overlay.setAttribute("aria-modal", "true");
    overlay.setAttribute("aria-hidden", "true");
    overlay.hidden = true;

    var stage = document.createElement("div");
    stage.className = "lb-stage";

    imgEl = document.createElement("img");
    imgEl.className = "lb-img";
    imgEl.alt = "";
    imgEl.draggable = false;
    stage.appendChild(imgEl);

    var controls = document.createElement("div");
    controls.className = "lb-controls";
    controls.appendChild(mkBtn("lb-zoom-out", "−", "Zoom out", function () { zoomAt(window.innerWidth / 2, window.innerHeight / 2, 1 / STEP); }));
    controls.appendChild(mkBtn("lb-reset", "↺", "Reset zoom", resetView));
    controls.appendChild(mkBtn("lb-zoom-in", "+", "Zoom in", function () { zoomAt(window.innerWidth / 2, window.innerHeight / 2, STEP); }));

    var closeBtn = mkBtn("lb-close", "×", "Close (Esc)", close);

    hintEl = document.createElement("div");
    hintEl.className = "lb-hint";

    capEl = document.createElement("div");
    capEl.className = "lb-caption";

    overlay.appendChild(stage);
    overlay.appendChild(controls);
    overlay.appendChild(closeBtn);
    overlay.appendChild(hintEl);
    overlay.appendChild(capEl);
    document.body.appendChild(overlay);

    // backdrop click (stage/overlay, not the image or controls) closes.
    overlay.addEventListener("click", function (e) {
      if (e.target === overlay || e.target === stage) close();
    });

    // wheel zoom — must be non-passive so preventDefault suppresses page scroll.
    stage.addEventListener("wheel", onWheel, { passive: false });

    // double-click toggles fit <-> zoomed-in at the click point.
    imgEl.addEventListener("dblclick", function (e) {
      e.preventDefault();
      if (scale > 1.01) resetView();
      else zoomAt(e.clientX, e.clientY, DBL);
    });

    // pointer-based pan + pinch (mouse, touch, pen unified).
    stage.addEventListener("pointerdown", onPointerDown);
    stage.addEventListener("pointermove", onPointerMove);
    stage.addEventListener("pointerup", onPointerUp);
    stage.addEventListener("pointercancel", onPointerUp);
  }

  function mkBtn(cls, label, aria, handler) {
    var b = document.createElement("button");
    b.type = "button";
    b.className = "lb-btn " + cls;
    b.textContent = label;
    b.setAttribute("aria-label", aria);
    b.title = aria;
    b.addEventListener("click", function (e) { e.stopPropagation(); handler(); });
    return b;
  }

  function apply() {
    imgEl.style.transform = "translate(" + tx + "px," + ty + "px) scale(" + scale + ")";
    imgEl.style.cursor = scale > 1.01 ? (panning ? "grabbing" : "grab") : "zoom-out";
    updateHint();
  }

  function updateHint() {
    hintEl.textContent = Math.round(scale * 100) + "%" +
      (scale > 1.01 ? " · drag to pan" : " · scroll or double-click to zoom");
  }

  // Zoom around a client point, keeping that point fixed under the cursor.
  function zoomAt(cx, cy, factor) {
    var rect = imgEl.getBoundingClientRect();
    var ns = clamp(scale * factor, MIN, MAX);
    if (ns === scale) return;
    var dx = cx - rect.left, dy = cy - rect.top;
    var ratio = ns / scale;
    tx += dx * (1 - ratio);
    ty += dy * (1 - ratio);
    scale = ns;
    if (scale <= 1.001) { tx = 0; ty = 0; scale = 1; } // snap back to centered fit
    apply();
  }

  function resetView() { scale = 1; tx = 0; ty = 0; apply(); }

  function onWheel(e) {
    e.preventDefault();
    var factor = e.deltaY < 0 ? STEP : 1 / STEP;
    zoomAt(e.clientX, e.clientY, factor);
  }

  function onPointerDown(e) {
    pointers[e.pointerId] = { x: e.clientX, y: e.clientY };
    var ids = Object.keys(pointers);
    if (ids.length === 1) {
      // begin pan only when zoomed in past fit
      if (scale > 1.01) {
        panning = true;
        panStartX = e.clientX; panStartY = e.clientY;
        panTX = tx; panTY = ty;
        try { e.target.setPointerCapture(e.pointerId); } catch (err) {}
        apply();
      }
    } else if (ids.length === 2) {
      // begin pinch
      panning = false;
      var a = pointers[ids[0]], b = pointers[ids[1]];
      pinchDist = Math.hypot(a.x - b.x, a.y - b.y) || 1;
      pinchScale = scale;
      pinchMidX = (a.x + b.x) / 2;
      pinchMidY = (a.y + b.y) / 2;
    }
  }

  function onPointerMove(e) {
    if (!pointers[e.pointerId]) return;
    pointers[e.pointerId] = { x: e.clientX, y: e.clientY };
    var ids = Object.keys(pointers);
    if (ids.length >= 2) {
      var a = pointers[ids[0]], b = pointers[ids[1]];
      var dist = Math.hypot(a.x - b.x, a.y - b.y) || 1;
      var target = clamp(pinchScale * (dist / pinchDist), MIN, MAX);
      // apply pinch as a zoom around the gesture midpoint
      var factor = target / scale;
      if (Math.abs(factor - 1) > 0.001) zoomAt(pinchMidX, pinchMidY, factor);
    } else if (panning) {
      tx = panTX + (e.clientX - panStartX);
      ty = panTY + (e.clientY - panStartY);
      apply();
    }
  }

  function onPointerUp(e) {
    delete pointers[e.pointerId];
    try { e.target.releasePointerCapture(e.pointerId); } catch (err) {}
    if (Object.keys(pointers).length < 2) { pinchDist = 0; }
    if (Object.keys(pointers).length === 0) { panning = false; apply(); }
  }

  function open(source) {
    if (!overlay) buildOverlay();
    trigger = source;
    resetView();

    imgEl.src = source.currentSrc || source.src;
    imgEl.alt = source.alt || "";

    // caption from the sibling <figcaption>, if any
    var fig = source.closest ? source.closest("figure") : null;
    var cap = fig ? fig.querySelector("figcaption") : null;
    if (cap && cap.textContent.trim()) {
      capEl.textContent = cap.textContent.trim();
      capEl.style.display = "";
    } else {
      capEl.textContent = "";
      capEl.style.display = "none";
    }

    var root = document.documentElement;
    root.classList.add("lb-lock");
    document.body.classList.add("lb-lock");

    overlay.hidden = false;
    overlay.setAttribute("aria-hidden", "false");
    // next frame so the CSS open transition runs
    requestAnimationFrame(function () { overlay.classList.add("lb-open"); });

    updateHint();
    // focus the close button for keyboard users
    var cb = overlay.querySelector(".lb-close");
    if (cb) cb.focus();

    document.addEventListener("keydown", onKey, true);
  }

  function close() {
    if (!overlay || overlay.hidden) return;
    overlay.classList.remove("lb-open");
    document.removeEventListener("keydown", onKey, true);
    document.documentElement.classList.remove("lb-lock");
    document.body.classList.remove("lb-lock");
    pointers = {}; panning = false; pinchDist = 0;

    var finish = function () {
      overlay.hidden = true;
      overlay.setAttribute("aria-hidden", "true");
      imgEl.removeAttribute("src");
      if (trigger && typeof trigger.focus === "function") trigger.focus();
      trigger = null;
    };
    if (reduceMotion) { finish(); }
    else { window.setTimeout(finish, 180); }
  }

  function onKey(e) {
    if (overlay.hidden) return;
    if (e.key === "Escape") { e.preventDefault(); close(); }
    else if (e.key === "+" || e.key === "=") { e.preventDefault(); zoomAt(window.innerWidth / 2, window.innerHeight / 2, STEP); }
    else if (e.key === "-" || e.key === "_") { e.preventDefault(); zoomAt(window.innerWidth / 2, window.innerHeight / 2, 1 / STEP); }
    else if (e.key === "0") { e.preventDefault(); resetView(); }
  }

  function activate(source) {
    source.setAttribute("tabindex", "0");
    source.setAttribute("role", "button");
    var label = source.alt ? ("Open " + source.alt + " full size") : "Open image full size";
    source.setAttribute("aria-label", label);
    source.style.cursor = "zoom-in";
    source.addEventListener("click", function () { open(source); });
    source.addEventListener("keydown", function (e) {
      if (e.key === "Enter" || e.key === " " || e.key === "Spacebar") {
        e.preventDefault();
        open(source);
      }
    });
  }

  function init() {
    try {
      reduceMotion = window.matchMedia &&
        window.matchMedia("(prefers-reduced-motion: reduce)").matches;
    } catch (err) { reduceMotion = false; }

    var imgs = document.querySelectorAll("figure img");
    if (!imgs.length) return; // pages with no figures (e.g. FAQ) — nothing to wire
    for (var i = 0; i < imgs.length; i++) activate(imgs[i]);
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", init);
  } else {
    init();
  }
})();
