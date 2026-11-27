// Offline maps: the Maps tab in the Library (the maps on offer, download and delete), the map viewer (place search,
// zoom, "Print this area", "Save this area as PDF") and the printed page (print.html?map=1).
//
// The viewer is MapLibre GL JS (its CSP build, bundled in app/static: maplibre-gl-csp.js and its worker), drawing the
// vector tiles, glyphs and sprites the server reads out of the downloaded map file (/maps/<id>/...). Every request
// it makes goes to this app's own server with the token in a header; nothing else is reachable (the CSP allows only
// 'self'). Labels are shown in Latin script only, so no right-to-left text plugin is needed.
//
// Everything here is built with DOM calls (render.js's h()); no markup is ever made from data.
(function (root) {
  "use strict";
  const API = root.SLApi;
  const str = (v) => (typeof v === "string" ? v : v == null ? "" : String(v));
  const ID_RE = /^[a-z0-9-]{1,40}$/;
  const PAGE_RATIO = 0.85;  // width / height of the map on a printed A5 page (print.html's map box)
  const CREDIT = "Map data © OpenStreetMap contributors · © OpenMapTiles · OpenFreeMap";

  function dateLabel(d) {
    const m = /^(\d{4})-(\d{2})/.exec(str(d));
    if (!m) return "";
    return new Date(Date.UTC(Number(m[1]), Number(m[2]) - 1, 1)).toLocaleDateString("en-GB", { month: "long", year: "numeric", timeZone: "UTC" });
  }
  function mb(n) {
    if (!Number.isFinite(n) || n <= 0) return "";
    return n >= 1e9 ? `${(n / 1e9).toFixed(1)} GB` : `${Math.max(1, Math.round(n / 1e6))} MB`;
  }

  // ---------- the Maps tab ----------
  // ctx (from app.js): h, icon, toast, confirm(title, lines, okLabel), openMap(id), isShown() (the Maps tab is on screen)
  const T = { ctx: null, box: null, data: null, timer: 0, req: 0 };

  function init(ctx) { T.ctx = ctx; }

  async function renderList(box) {
    T.box = box;
    const req = ++T.req;
    clearTimeout(T.timer);
    if (!T.data) box.replaceChildren(T.ctx.h("p", { class: "empty-note" }, "Loading the maps…"));
    try {
      const data = await API.maps.list();
      if (req !== T.req) return;
      T.data = data;
    } catch (err) {
      if (req !== T.req) return;
      box.replaceChildren(T.ctx.h("p", { class: "empty-note" }, str(err.message)));
      return;
    }
    drawList();
    const busy = T.data.maps.some((m) => m.state === "waiting" || m.state === "downloading" || m.state === "updating");
    if (busy) T.timer = setTimeout(() => { if (T.ctx.isShown() && T.box === box) renderList(box); }, 3000);
  }

  function drawList() {
    const { h } = T.ctx;
    const list = (Array.isArray(T.data.maps) ? T.data.maps : []).filter((m) => m && ID_RE.test(str(m.id)));
    const saved = list.filter((m) => m.state === "saved" || m.state === "updating");
    const used = saved.reduce((n, m) => n + (Number(m.used) || Number(m.size) || 0), 0);
    if (T.data.locked && root.SLUpgrade) {  // no key: each card locked, with Preview and Get the upgrade (upgrade.js)
      const on = { previewMap: () => T.ctx.lockOn.previewMap(list), getUpgrade: () => T.ctx.lockOn.getUpgrade() };
      T.box.replaceChildren(
        h("p", { class: "maps-facts" }, `${list.length} ${list.length === 1 ? "map" : "maps"} on offer · included in the upgrade`),
        h("div", { class: "map-grid" }, list.map((m) => root.SLUpgrade.lockedMapCard(m, metaLine(m), { h, icon: T.ctx.icon, on }))),
        h("p", { class: "maps-note" }, "Street maps from OpenStreetMap, © OpenStreetMap contributors, packaged by Kiwix. A downloaded map works with no internet. They come with the upgrade key."));
      return;
    }
    const facts = [`${list.length} ${list.length === 1 ? "map" : "maps"} on offer`, `${saved.length} downloaded`];
    if (used) facts.push(`${mb(used)} of maps on this computer`);
    T.box.replaceChildren(...[
      h("p", { class: "maps-facts" }, facts.join(" · ")),
      h("div", { class: "map-grid" }, list.map(card)),
      h("p", { class: "maps-note" }, "Street maps from OpenStreetMap, made by Kiwix. A downloaded map works with no internet. Each download is checked against Kiwix's checksum before the map is used.")]);
  }

  function metaLine(m) {
    const size = mb(Number(m.size));
    return [size && m.size_about ? `about ${size}` : size, m.date ? `${dateLabel(m.date)} build` : ""].filter(Boolean).join(" · ");
  }

  function card(m) {
    const { h, icon } = T.ctx;
    const meta = metaLine(m);
    let state, buttons;
    if (m.state === "saved" || m.state === "updating") {
      state = h("div", { class: "map-state st-saved" }, h("i", { class: "dot" }), m.state === "updating" ? "Downloaded · a newer build is downloading" : "Downloaded");
      buttons = [h("button", { class: "btn primary", type: "button", onclick: () => T.ctx.openMap(m.id) }, icon("map", "sm"), "Open"),
        h("button", { class: "btn quiet", type: "button", onclick: () => removeMap(m) }, icon("trash", "sm"), "Delete")];
    } else if (m.state === "downloading") {
      const done = Number(m.done) || 0, total = Number(m.total) || Number(m.size) || 0;
      const pct = total ? Math.min(100, Math.round((done / total) * 100)) : 0;
      state = [h("div", { class: "map-state st-dl" }, h("i", { class: "dot" }), total ? `Downloading · ${mb(done) || "0 MB"} of ${mb(total)}` : "Downloading"),
        h("div", { class: "bar", role: "progressbar", "aria-valuenow": String(pct), "aria-valuemin": "0", "aria-valuemax": "100", "aria-label": `Downloading ${str(m.name)}` },
          h("i", { dataset: { pct: String(pct) } }))];
      buttons = m.state === "downloading" ? [h("button", { class: "btn", type: "button", "data-action": "pause", onclick: () => pause(m) }, icon("pause", "sm"), "Pause")] : [];
    } else if (m.state === "paused") {
      const done = Number(m.done) || 0, total = Number(m.total) || Number(m.size) || 0;
      const pct = total ? Math.min(100, Math.round((done / total) * 100)) : 0;
      state = [h("div", { class: "map-state" }, h("i", { class: "dot" }), total ? `Paused · ${mb(done) || "0 MB"} of ${mb(total)}` : "Paused"),
        h("div", { class: "bar", role: "progressbar", "aria-valuenow": String(pct), "aria-valuemin": "0", "aria-valuemax": "100", "aria-label": `Paused: ${str(m.name)}` },
          h("i", { dataset: { pct: String(pct) } }))];
      buttons = [h("button", { class: "btn primary", type: "button", "data-action": "resume", onclick: () => resume(m) }, icon("down", "sm"), "Resume"),
        h("button", { class: "btn quiet", type: "button", onclick: () => removeMap(m, true) }, icon("close", "sm"), "Cancel")];
    } else if (m.state === "waiting") {
      state = h("div", { class: "map-state st-dl" }, h("i", { class: "dot" }), m.busy ? "Waiting for the library refresh to finish" : "Not downloaded yet");
      buttons = [h("button", { class: "btn primary", type: "button", "data-action": "resume", onclick: () => resume(m) }, icon("down", "sm"), "Try again"),
        h("button", { class: "btn quiet", type: "button", onclick: () => removeMap(m, true) }, icon("close", "sm"), "Cancel")];
    } else {
      state = h("div", { class: "map-state" }, h("i", { class: "dot" }), "Available to download");
      buttons = [h("button", { class: "btn primary", type: "button", "data-action": "download", onclick: () => download(m) }, icon("down", "sm"), "Download for offline use")];
    }
    const el = h("article", { class: "map-card" },
      h("div", { class: "map-card-head" }, h("span", { class: "map-card-region" }, str(m.region)), h("span", { class: "map-card-name" }, str(m.name))),
      h("div", { class: "map-card-body" },
        h("div", { class: "map-meta" }, meta),
        state,
        m.problem ? h("div", { class: "map-problem" }, str(m.problem)) : null,
        h("div", { class: "map-btns" }, buttons)));
    // the progress bar's width, set through the style object (no style attribute is ever written from data)
    for (const bar of el.querySelectorAll(".bar i")) bar.style.width = `${Number(bar.dataset.pct) || 0}%`;
    return el;
  }

  async function download(m) {
    const size = mb(Number(m.size));
    const ok = await T.ctx.confirm(`Download the ${str(m.name)} map?`, [
      `This starts a download of ${size ? `about ${size}` : "the map"} from Kiwix (its newest build). It runs in the background, and you can keep using Starholm. You can pause it and carry on later.`,
      "The download is checked before the map is used. After that the map works with no internet."], "Download");
    if (!ok) return;
    try {
      const r = await API.maps.download(m.id);
      T.ctx.toast(str(r.note) || `The ${str(m.name)} map is downloading.`);
    } catch (err) { T.ctx.toast(str(err.message)); }
    if (T.box) renderList(T.box);
  }

  async function pause(m) {
    try { await API.maps.pause(m.id); } catch (err) { T.ctx.toast(str(err.message)); }
    if (T.box) renderList(T.box);
  }

  async function resume(m) {  // carries on from the last byte downloaded
    try { await API.maps.download(m.id); } catch (err) { T.ctx.toast(str(err.message)); }
    if (T.box) renderList(T.box);
  }

  async function removeMap(m, waiting = false) {
    const ok = waiting ? true : await T.ctx.confirm(`Delete the ${str(m.name)} map?`, [
      `It is deleted from this computer and frees ${mb(Number(m.used) || Number(m.size)) || "its space"}. You can download it again later.`], "Delete");
    if (!ok) return;
    try {
      await API.maps.remove(m.id);
      T.ctx.toast(waiting ? "Cancelled." : `The ${str(m.name)} map was deleted.`);
    } catch (err) { T.ctx.toast(str(err.message)); }
    if (T.box) renderList(T.box);
  }

  // ---------- the map viewer ----------
  const V = { printing: false, map: null, id: null, info: null, place: null, marker: null, sreq: 0, stimer: 0, theme: null, observer: null, media: null };

  function darkWanted() {
    const t = document.documentElement.dataset.t;
    if (t === "dark") return true;
    if (t === "light") return false;
    return !!(root.matchMedia && root.matchMedia("(prefers-color-scheme: dark)").matches);
  }

  // Labels in Latin script only: a style expression naming name:nonlatin becomes English, then Latin, then the name.
  const LATIN = ["coalesce", ["get", "name_en"], ["get", "name:latin"], ["get", "name"]];
  // MapLibre wants the sprite and glyph addresses absolute; the server gives them as /maps/<id>/...
  function latinLabels(style) {
    for (const key of ["sprite", "glyphs"]) if (typeof style[key] === "string") style[key] = absolute(style[key]).replace(/%7B/g, "{").replace(/%7D/g, "}");
    for (const layer of Array.isArray(style.layers) ? style.layers : []) {
      const tf = layer && layer.layout && layer.layout["text-field"];
      if (tf && JSON.stringify(tf).includes("name:nonlatin")) layer.layout["text-field"] = LATIN;
    }
    return style;
  }

  function useStyle(theme) {
    V.map.setStyle(`/maps/${V.id}/assets/kiwix-${theme}`, { validate: false, transformStyle: (_prev, next) => latinLabels(next) });
  }
  function setTheme() {
    const theme = darkWanted() ? "dark" : "light";
    if (!V.map || theme === V.theme || V.printing) return;
    V.theme = theme;
    useStyle(theme);
  }

  function absolute(url) {
    try { return new URL(url, location.origin).href; } catch { return url; }
  }

  // view: the element the viewer fills; back(): leave it.
  async function open(id, view, back) {
    const { h, icon } = T.ctx;
    close();
    if (!ID_RE.test(str(id))) return;
    V.id = id;
    const title = h("span", { class: "map-title" }, "Map");
    const canvas = h("div", { class: "map-canvas", id: "map-canvas" });
    const frame = h("div", { class: "map-frame", "aria-hidden": "true" }, h("span", null, "Printed area"));
    const input = h("input", { class: "search-input", type: "search", placeholder: "Find a place", "aria-label": "Find a place", maxlength: "100" });
    input.autocomplete = "off";
    const results = h("ul", { class: "map-results", hidden: true });
    const area = h("div", { class: "map-area" });
    const printBtn = h("button", { class: "btn primary", type: "button", onclick: () => printArea("print", printBtn) }, icon("print", "sm"), "Print this area");
    const pdfBtn = h("button", { class: "btn", type: "button", onclick: () => printArea("pdf", pdfBtn) }, icon("doc", "sm"), "Save this area as PDF");
    view.replaceChildren(h("div", { class: "map-view" },
      h("div", { class: "map-top" },
        h("button", { class: "btn quiet", type: "button", onclick: back }, icon("back", "sm"), "All maps"), title),
      h("div", { class: "map-stage" }, canvas, frame,
        h("div", { class: "map-search" }, h("label", { class: "search" }, icon("search", "sm"), input), results),
        h("p", { class: "map-credit" }, CREDIT)),
      h("div", { class: "map-actions" }, area,
        h("div", { class: "map-act" }, printBtn, h("span", { class: "hint" }, "Opens the area in the dashed box as one A5 page, ready to print.")),
        h("div", { class: "map-act" }, pdfBtn, h("span", { class: "hint" }, "Saves the same page as a PDF in your Downloads folder.")))));
    V.els = { title, canvas, frame, input, results, area };
    input.addEventListener("input", () => { clearTimeout(V.stimer); V.stimer = setTimeout(runSearch, 200); });
    input.addEventListener("keydown", (e) => {
      if (e.key === "Enter") { e.preventDefault(); clearTimeout(V.stimer); runSearch(true); }
      if (e.key === "Escape") { results.hidden = true; }
    });

    let info;
    try { info = await API.maps.info(id); } catch (err) {
      canvas.replaceChildren(h("p", { class: "empty-note map-error" }, str(err.message)));
      return;
    }
    if (V.id !== id) return;
    V.info = info;
    title.textContent = [str(info.name), info.date ? `map data from ${dateLabel(info.date)}` : ""].filter(Boolean).join(" · ");
    area.textContent = str(info.name);
    const ml = root.maplibregl;
    if (!ml) { canvas.replaceChildren(h("p", { class: "empty-note map-error" }, "The map viewer didn't load.")); return; }
    ml.setWorkerUrl("/static/maplibre-gl-csp-worker.js");
    const headers = API.maps.headers();
    let map;
    try {
      map = new ml.Map({
        container: canvas, style: { version: 8, sources: {}, layers: [] }, attributionControl: false,
        maxZoom: Math.min(Number(info.max_zoom) || 14, 14) + 4, dragRotate: false, pitchWithRotate: false, touchPitch: false,
        canvasContextAttributes: { preserveDrawingBuffer: true },
        maxBounds: info.bounds ? padBounds(info.bounds) : undefined,
        bounds: info.bounds || undefined,
        transformRequest: (url) => {
          const abs = absolute(url);
          return abs.startsWith(`${location.origin}/maps/`) ? { url: abs, headers } : { url: abs };
        },
      });
    } catch (err) {
      canvas.replaceChildren(h("p", { class: "empty-note map-error" }, `The map can't be shown here: ${str(err.message)}`));
      return;
    }
    map.touchZoomRotate.disableRotation();
    map.keyboard.disableRotation();
    map.addControl(new ml.NavigationControl({ showCompass: false }), "bottom-right");
    map.addControl(new ml.ScaleControl({ unit: "metric" }), "bottom-left");
    V.map = map;
    V.theme = null;
    setTheme();
    V.observer = new MutationObserver(setTheme);
    V.observer.observe(document.documentElement, { attributes: true, attributeFilter: ["data-t"] });
    V.media = root.matchMedia ? root.matchMedia("(prefers-color-scheme: dark)") : null;
    if (V.media) V.media.addEventListener("change", setTheme);
    map.on("resize", placeFrame);
    map.on("moveend", describeArea);
    map.on("error", (e) => { if (e && e.error && root.console) root.console.warn("map:", e.error.message || e.error); });
    placeFrame();
    input.focus();
  }

  function padBounds(b) {
    const [[w, s], [e, n]] = b;
    const dx = (e - w) * 0.25, dy = (n - s) * 0.25;
    return [[Math.max(-180, w - dx), Math.max(-85, s - dy)], [Math.min(180, e + dx), Math.min(85, n + dy)]];
  }

  function close() {
    clearTimeout(V.stimer);
    if (V.observer) V.observer.disconnect();
    if (V.media) V.media.removeEventListener("change", setTheme);
    if (V.map) V.map.remove();
    Object.assign(V, { map: null, id: null, info: null, place: null, marker: null, theme: null, observer: null, media: null });
  }

  // The dashed box: the part of the map that goes on the page, as tall as fits, PAGE_RATIO wide.
  function frameRect() {
    const c = V.els.canvas;
    const W = c.clientWidth, H = c.clientHeight;
    const top = 64, bottom = 24;  // clear of the search box and the credit line
    let fh = Math.max(80, H - top - bottom), fw = fh * PAGE_RATIO;
    if (fw > W - 40) { fw = Math.max(60, W - 40); fh = fw / PAGE_RATIO; }
    return { x: (W - fw) / 2, y: top + Math.max(0, (H - top - bottom - fh) / 2), w: fw, h: fh };
  }
  function placeFrame() {
    if (!V.els) return;
    const r = frameRect();
    Object.assign(V.els.frame.style, { left: `${r.x}px`, top: `${r.y}px`, width: `${r.w}px`, height: `${r.h}px` });
  }
  function describeArea() {
    if (!V.map || !V.info) return;
    const near = V.place && V.map.getBounds().contains([V.place.lon, V.place.lat]);
    V.els.area.textContent = near ? `${V.place.label}, ${str(V.info.name)}` : str(V.info.name);
  }

  async function runSearch(jump = false) {
    const { h, icon } = T.ctx;
    const q = V.els.input.value.trim();
    const req = ++V.sreq;
    if (!q) { V.els.results.hidden = true; return; }
    let found = [];
    try { found = (await API.maps.search(V.id, q)).results || []; } catch (err) { if (req === V.sreq) T.ctx.toast(str(err.message)); return; }
    if (req !== V.sreq) return;
    const rows = found.filter((r) => r && Number.isFinite(Number(r.lat)) && Number.isFinite(Number(r.lon)));
    V.els.results.replaceChildren(...(rows.length ? rows.map((r) => h("li", null,
      h("button", { class: "map-result", type: "button", onclick: () => goTo(r) }, icon("map", "sm"), h("span", null, str(r.label))))) :
      [h("li", { class: "map-none" }, "No place by that name in this map. It lists towns, districts and landmarks, not streets.")]));
    V.els.results.hidden = false;
    if (jump && rows.length) goTo(rows[0]);
  }

  function goTo(r) {
    if (!V.map) return;
    const ml = root.maplibregl;
    V.place = { label: str(r.label), lat: Number(r.lat), lon: Number(r.lon) };
    V.els.results.hidden = true;
    V.els.input.value = V.place.label;
    if (V.marker) V.marker.remove();
    const pin = T.ctx.h("div", { class: "map-pin", title: V.place.label });
    V.marker = new ml.Marker({ element: pin }).setLngLat([V.place.lon, V.place.lat]).addTo(V.map);
    V.map.flyTo({ center: [V.place.lon, V.place.lat], zoom: Math.max(Number(r.zoom) || 12, 11), duration: 800 });
  }

  // A round distance for the printed scale bar: at most a third of the box's width.
  function scaleFor(widthMetres) {
    const most = widthMetres / 3;
    const pow = Math.pow(10, Math.floor(Math.log10(most)));
    const step = [5, 2, 1].map((k) => k * pow).find((d) => d <= most) || pow;
    return { label: step >= 1000 ? `${step / 1000} km` : `${step} m`, share: step / widthMetres };
  }

  function idle(map) {
    return new Promise((ok) => {
      if (map.loaded() && map.areTilesLoaded()) ok();
      else map.once("idle", ok);
    });
  }

  async function printArea(mode, btn) {
    if (!V.map || btn.disabled) return;
    btn.disabled = true;
    // Paper always gets the light map, whatever the screen shows.
    const dark = V.theme === "dark";
    V.printing = true;
    try {
      if (dark) { useStyle("light"); await new Promise((ok) => V.map.once("idle", ok)); }
      await idle(V.map);
      const canvas = V.map.getCanvas();
      const k = canvas.width / canvas.clientWidth;
      const r = frameRect();
      const out = document.createElement("canvas");
      out.width = Math.round(r.w * k);
      out.height = Math.round(r.h * k);
      out.getContext("2d").drawImage(canvas, r.x * k, r.y * k, r.w * k, r.h * k, 0, 0, out.width, out.height);
      const image = out.toDataURL("image/jpeg", 0.92);
      const lat = V.map.getCenter().lat;
      const metresPerPx = (40075016.686 * Math.cos((lat * Math.PI) / 180)) / (512 * Math.pow(2, V.map.getZoom()));
      const near = V.place && V.map.getBounds().contains([V.place.lon, V.place.lat]);
      const name = str(V.info.name);
      const title = near ? `${V.place.label}, ${name}` : name;
      const printed = new Date().toLocaleDateString("en-GB", { day: "numeric", month: "long", year: "numeric" });
      const subtitle = [name, V.info.date ? `map data from ${dateLabel(V.info.date)}` : "", `printed ${printed}`].filter(Boolean).join(" · ");
      T.ctx.toast(mode === "pdf" ? "Making the PDF…" : "Making the page…");
      const res = await API.maps.print({ mode, title, subtitle, scale: scaleFor(r.w * metresPerPx), image });
      T.ctx.toast(mode === "pdf" ? `Saved in Downloads as “${str(res.file)}”.` : "The page is open in Preview. Print it from there.");
    } catch (err) {
      T.ctx.toast(str(err.message));
    } finally {
      V.printing = false;
      if (dark && V.map) { V.theme = null; setTheme(); }
      btn.disabled = false;
    }
  }

  // ---------- the printed page (print.html?map=1) ----------
  // h: render.js's h(). The page: title, subtitle, the map with a north arrow, the scale bar and the credit.
  async function printPage(h, col) {
    const page = await API.maps.printPage();
    if (!/^data:image\/(?:jpeg|png);base64,/.test(str(page.image))) throw new Error("no map");
    const img = h("img", { class: "map-print-img", alt: str(page.title) });
    const loaded = new Promise((ok, fail) => { img.addEventListener("load", ok, { once: true }); img.addEventListener("error", fail, { once: true }); });
    img.src = page.image;
    const svgNS = "http://www.w3.org/2000/svg";
    const north = document.createElementNS(svgNS, "svg");
    north.setAttribute("viewBox", "0 0 20 28");
    north.setAttribute("class", "map-print-north");
    north.setAttribute("aria-hidden", "true");
    for (const [d, fill] of [["M10 1 17 22 10 18 3 22z", "#1a1a1a"], ["M10 1 10 18 3 22z", "#ffffff"]]) {
      const p = document.createElementNS(svgNS, "path");
      p.setAttribute("d", d); p.setAttribute("fill", fill); p.setAttribute("stroke", "#1a1a1a"); p.setAttribute("stroke-width", "1");
      north.append(p);
    }
    const scale = page.scale && typeof page.scale === "object" ? page.scale : null;
    const bar = h("i", { class: "map-print-bar" }, h("b"), h("b"), h("b"), h("b"));
    if (scale) bar.style.width = `${Math.max(1, Math.min(100, Number(scale.share) * 100))}%`;
    col.classList.add("map-print");
    col.append(h("h1", null, str(page.title)),
      h("p", { class: "doc-src" }, str(page.subtitle)),
      h("div", { class: "map-print-box" }, img, north),
      h("div", { class: "map-print-scale" }, scale ? bar : null,
        h("span", null, [scale ? `0 – ${str(scale.label)}` : "", "North is up"].filter(Boolean).join(" · "))),
      h("p", { class: "map-print-credit" }, CREDIT));
    await loaded;
  }

  root.SLMaps = { init, renderList, open, close, printPage };
})(window);
