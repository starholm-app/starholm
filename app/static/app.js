// Starholm interface: the Starholm style guide (docs/design/style-guide.md) on the layout of docs/design/BUILD-SPEC.md.
//
// Safety rules for this file:
// - Everything that comes from the server (answers, articles, file names, chat titles, errors) is untrusted. It only
//   reaches the page as text nodes or through render.js, which builds elements itself. Nothing here assigns
//   innerHTML, outerHTML or insertAdjacentHTML, and nothing uses document.write.
// - Web addresses are shown as text, never links. Source chips open inside the app.
// - No inline styles or handlers (the server's CSP forbids them): state is shown with classes and data- attributes;
//   the only sizes set from here go through CSSOM (element.style), which the CSP allows.
// - Every server call goes through api.js.
(() => {
  "use strict";

  const { h, md, splitAnswer, visibleText } = window.SLRender;
  const API = window.SLApi;
  // A web address in a document, guide or answer: the server opens it in the Mac's default browser. The page itself
  // never navigates, never sets window.location and never creates an element that would load the address.
  window.SLRender.setWebOpener((url) => { API.openUrl(url).catch((err) => toast(err.message)); });
  // Before that: a host written in another alphabet may imitate a site the reader knows, so it is shown in ASCII and
  // opens only after they say yes (render.js asks this first).
  window.SLRender.setWebCheck((host) => confirmDialog("Open this address?", [
    `Its name is written in another alphabet, which can imitate a site you know. In plain letters it is: ${host}`,
    "It opens in your web browser."], "Open"));
  const F = window.SLFlow;
  const M = window.SLModels;  // Settings › Model and library-only mode (models.js)
  const $ = (id) => document.getElementById(id);
  const ID_RE = /^[0-9a-f]{32}$/;
  const SVG_NS = $("mark").namespaceURI;
  const str = (v) => (typeof v === "string" ? v : v == null ? "" : String(v));
  const isArticleId = (id) => Number.isSafeInteger(Number(id)) && Number(id) > 0;
  // A source that is a section of one of the guides ("guide:<slug>#<heading>", which fetch-quoted hands the model
  // on safety questions): {slug, anchor (the heading's id)}, else null. Library sources are numbers.
  const guideSource = (s) => (s && typeof s.id === "string" ? window.SLRender.guideLink(s.id) : null);
  const isSourceId = (id) => isArticleId(id) || (typeof id === "string" && !!window.SLRender.guideLink(id));
  const plural = (n, one, many = `${one}s`) => `${Number(n).toLocaleString("en-GB")} ${n === 1 ? one : many}`;
  const fmt = (n) => Number(n).toLocaleString("en-GB");

  // ---------- icons (24 x 24, stroked) ----------
  const ICONS = {
    plus: [["path", { d: "M12 5v14M5 12h14" }]],
    external: [["path", { d: "M14 4.5h5.5V10M19.5 4.5 11 13M18 14v4.5a1 1 0 0 1-1 1H5.5a1 1 0 0 1-1-1V7a1 1 0 0 1 1-1H10" }]],
    mail: [["rect", { x: 3.5, y: 5.5, width: 17, height: 13, rx: 2 }], ["path", { d: "m4 7 8 6 8-6" }]],
    eye: [["path", { d: "M2.5 12S6 5.5 12 5.5 21.5 12 21.5 12 18 18.5 12 18.5 2.5 12 2.5 12z" }], ["circle", { cx: 12, cy: 12, r: 3 }]],
    sidebar: [["rect", { x: 3.5, y: 4.5, width: 17, height: 15, rx: 2.5 }], ["path", { d: "M9 4.5v15" }]],
    book: [["path", { d: "M4 4.5h6a2 2 0 0 1 2 2V20a1.5 1.5 0 0 0-1.5-1.5H4zM20 4.5h-6a2 2 0 0 0-2 2V20a1.5 1.5 0 0 1 1.5-1.5H20z" }]],
    gear: [["path", { d: "M9.72 5.17 10.5 2.52h3l.78 2.65.93.39 2.43-1.33 2.13 2.13-1.33 2.43.39.93 2.65.78v3l-2.65.78-.39.93 1.33 2.43-2.13 2.13-2.43-1.33-.93.39-.78 2.65h-3l-.78-2.65-.93-.39-2.43 1.33-2.13-2.13 1.33-2.43-.39-.93-2.65-.78v-3l2.65-.78.39-.93-1.33-2.43 2.13-2.13 2.43 1.33z" }], ["circle", { cx: 12, cy: 12, r: 3 }]],
    shuffle: [["path", { d: "M3 7h3.5c2.5 0 3.5 1.5 5 5s2.5 5 5 5H21M3 17h3.5c1.3 0 2.2-.4 2.9-1.2M14.6 8.2c.7-.8 1.6-1.2 2.9-1.2H21M18 4l3 3-3 3M18 14l3 3-3 3" }]],
    expand: [["path", { d: "M4 9V4h5M20 9V4h-5M4 15v5h5M20 15v5h-5" }]],
    edit: [["path", { d: "M4 20h4L19 9l-4-4L4 16z" }], ["path", { d: "M13.5 6.5l4 4" }]],
    chevL: [["path", { d: "M14.5 6l-6 6 6 6" }]],
    chevR: [["path", { d: "M9.5 6l6 6-6 6" }]],
    chevD: [["path", { d: "M6 9.5l6 6 6-6" }]],
    trash: [["path", { d: "M5 7h14M10 7V4.5h4V7M7 7l1 13h8l1-13" }]],
    upload: [["path", { d: "M12 16V4M7 9l5-5 5 5M5 20h14" }]],
    panel: [["rect", { x: 3.5, y: 4.5, width: 17, height: 15, rx: 2.5 }], ["path", { d: "M15 4.5v15" }]],
    close: [["path", { d: "M6 6l12 12M18 6 6 18" }]],
    search: [["circle", { cx: 11, cy: 11, r: 6.5 }], ["path", { d: "m20 20-4.2-4.2" }]],
    back: [["path", { d: "M15 6l-6 6 6 6" }]],
    print: [["path", { d: "M7 8.5V3.5h10v5M7 17H4.5v-8.5h15V17H17" }], ["rect", { x: 7, y: 14, width: 10, height: 6.5, rx: 1 }]],
    copy: [["rect", { x: 8, y: 8, width: 12, height: 12, rx: 2 }], ["path", { d: "M16 8V5.5A1.5 1.5 0 0 0 14.5 4h-9A1.5 1.5 0 0 0 4 5.5v9A1.5 1.5 0 0 0 5.5 16H8" }]],
    retry: [["path", { d: "M20 11a8 8 0 1 0-2.3 5.7M20 5v6h-6" }]],
    send: [["path", { d: "M12 19V5M6 11l6-6 6 6" }]],
    stop: [["rect", { x: 7, y: 7, width: 10, height: 10, rx: 2 }]],
    phone: [["path", { d: "M5 4h4l2 5-2.5 1.5a11 11 0 0 0 5 5L15 13l5 2v4a2 2 0 0 1-2 2A16 16 0 0 1 3 6a2 2 0 0 1 2-2" }]],
    check: [["path", { d: "m5 12.5 4.5 4.5L19 7.5" }]],
    contrast: [["circle", { cx: 12, cy: 12, r: 8.5 }], ["path", { d: "M12 3.5a8.5 8.5 0 0 0 0 17z" }]],
    note: [["path", { d: "M6 3.5h9l3 3v14H6z" }], ["path", { d: "M9 11h6M9 15h6" }]],
    sliders: [["path", { d: "M4 7h10M18 7h2M4 17h4M12 17h8" }], ["circle", { cx: 16, cy: 7, r: 2 }], ["circle", { cx: 10, cy: 17, r: 2 }]],
    folder: [["path", { d: "M3.5 6.5a2 2 0 0 1 2-2h4l2 2.5h7a2 2 0 0 1 2 2V17a2 2 0 0 1-2 2h-13a2 2 0 0 1-2-2z" }]],
    pulse: [["path", { d: "M3 12h4l2-5 4 10 2-5h6" }]],
    monitor: [["rect", { x: 3.5, y: 4.5, width: 17, height: 11, rx: 1.5 }], ["path", { d: "M9 19.5h6M12 15.5v4" }]],
    sun: [["circle", { cx: 12, cy: 12, r: 3.5 }], ["path", { d: "M12 3v2M12 19v2M3 12h2M19 12h2M5.6 5.6 7 7M17 17l1.4 1.4M5.6 18.4 7 17M17 7l1.4-1.4" }]],
    moon: [["path", { d: "M19.5 14.5A7.5 7.5 0 0 1 9.5 4.5a7.5 7.5 0 1 0 10 10z" }]],
    bandage: [["rect", { x: 2.8, y: 8.2, width: 18.4, height: 7.6, rx: 3.8, transform: "rotate(-45 12 12)" }], ["path", { d: "M10.5 10.5h.01M13.5 13.5h.01M13.5 10.5h.01M10.5 13.5h.01" }]],
    drop: [["path", { d: "M12 3.5s6 6.4 6 10.5a6 6 0 0 1-12 0c0-4.1 6-10.5 6-10.5z" }]],
    bolt: [["path", { d: "M13 3 5 13.5h6L10 21l8-10.5h-6z" }]],
    thermo: [["path", { d: "M10 4.5a2 2 0 0 1 4 0v9.3a4 4 0 1 1-4 0z" }], ["path", { d: "M12 10v6" }]],
    flame: [["path", { d: "M12 21a6 6 0 0 0 6-6c0-4-3-6-4-10-2 2-3 4-3 6-1-1-1.5-2-1.5-3C7 10 6 12.5 6 15a6 6 0 0 0 6 6z" }]],
    box: [["path", { d: "M3.5 7.5 12 3.5l8.5 4v9L12 20.5l-8.5-4z" }], ["path", { d: "M3.5 7.5 12 11.5l8.5-4M12 11.5v9" }]],
    flow: [["rect", { x: 4, y: 3.5, width: 7, height: 5, rx: 1.5 }], ["rect", { x: 13, y: 15.5, width: 7, height: 5, rx: 1.5 }], ["path", { d: "M7.5 8.5v4.5a2 2 0 0 0 2 2H13" }]],
    arrow: [["path", { d: "M5 12h14M13 6l6 6-6 6" }]],
    down: [["path", { d: "M12 5v14M6 13l6 6 6-6" }]],
    doc: [["path", { d: "M6.5 3.5h7l4 4v13h-11z" }], ["path", { d: "M13.5 3.5v4h4M9 12h6M9 15.5h6" }]],
    chat: [["path", { d: "M4 5h16v11H9l-5 4z" }]],
    dots: [["path", { d: "M6 12h.01M12 12h.01M18 12h.01" }]],
    lock: [["rect", { x: 5, y: 10.5, width: 14, height: 10, rx: 2 }], ["path", { d: "M8.5 10.5V7.5a3.5 3.5 0 0 1 7 0v3" }]],
    map: [["path", { d: "M3.5 6.5 9 4l6 2.5L20.5 4v13.5L15 20l-6-2.5-5.5 2.5z" }], ["path", { d: "M9 4v13.5M15 6.5V20" }]],
    key: [["circle", { cx: 8, cy: 15, r: 4.5 }], ["path", { d: "M11.2 11.8 20 3M16.5 6.5l2.5 2.5M14 9l2 2" }]],
    pause: [["path", { d: "M9 6v12M15 6v12" }]],
  };
  function icon(name, size) {
    const s = document.createElementNS(SVG_NS, "svg");
    s.setAttribute("class", `i${size ? ` ${size}` : ""}`);
    s.setAttribute("viewBox", "0 0 24 24");
    s.setAttribute("aria-hidden", "true");
    for (const [tag, attrs] of ICONS[name] || []) {
      const e = document.createElementNS(SVG_NS, tag);
      for (const [k, v] of Object.entries(attrs)) e.setAttribute(k, String(v));
      s.append(e);
    }
    return s;
  }
  function setIcon(el, name) { el.replaceChildren(icon(name, el.dataset.iconSize)); }
  // replaceChildren, skipping empty slots (replaceChildren itself would show null as the text "null")
  const fill = (el, ...kids) => el.replaceChildren(...kids.flat().filter((k) => k != null && k !== false));
  for (const el of document.querySelectorAll("[data-icon]")) setIcon(el, el.dataset.icon);

  // ---------- notices ----------
  let toastTimer = 0;
  function toast(text) {
    const el = $("toast");
    el.textContent = text;
    el.hidden = false;
    clearTimeout(toastTimer);
    toastTimer = setTimeout(() => { el.hidden = true; }, 5000);
  }
  function showBanner(text, label, action) {
    $("banner-text").textContent = text;
    const b = $("banner-action");
    b.hidden = !label;
    b.textContent = label || "";
    b.onclick = action || null;
    $("banner").hidden = false;
  }
  API.setStaleHandler(() => showBanner("Starholm was restarted, so this page is out of date.", "Reload", () => location.reload()));
  function setMsg(id, text, error = false) {
    const el = $(id);
    el.textContent = text || "";
    el.classList.toggle("error", !!error);
  }
  async function copyText(text) {
    try { await navigator.clipboard.writeText(text); toast("Copied."); }
    catch { toast("Couldn't copy. Select the text and press ⌘C instead."); }
  }
  function confirmDialog(title, lines, okLabel) {
    const dlg = $("confirm-dialog");
    $("confirm-title").textContent = title;
    $("confirm-text").replaceChildren(...lines.map((l) => h("p", null, l)));
    $("confirm-ok").textContent = okLabel;
    dlg.returnValue = "";
    dlg.showModal();
    // Settled by the button pressed (the form's submit), by Esc (cancel), or by the dialog closing any other way.
    return new Promise((resolve) => {
      const form = dlg.querySelector("form");
      const done = (ok) => {
        form.removeEventListener("submit", onSubmit);
        dlg.removeEventListener("cancel", onCancel);
        dlg.removeEventListener("close", onClose);
        resolve(ok);
      };
      const onSubmit = (e) => done(!!e.submitter && e.submitter.value === "ok");
      const onCancel = () => done(false);
      const onClose = () => done(dlg.returnValue === "ok");
      form.addEventListener("submit", onSubmit);
      dlg.addEventListener("cancel", onCancel);
      dlg.addEventListener("close", onClose);
    });
  }

  // ---------- state ----------
  const S = {
    view: "start",          // start | chat | library
    status: null,
    statusError: null,
    chats: [],
    current: null,          // id of the chat on screen, or null for a new one
    lastConversation: null, // the chat the server last had in memory, dropped by /api/new
    stream: null,           // AbortController of the answer being streamed
    loadRequest: 0,
    answers: [],            // Answer objects on screen
    turns: [],              // {text, userEl, answer, saved}: each question on screen, in order
    editing: null,          // the turn whose question is back in the composer for Edit
    sideNarrow: false,      // sidebar opened over the content in a narrow window
    emergency: null,
  };

  // ---------- preferences: theme, text size, sidebar, widths ----------
  // Kept by api.js (in memory until the server's preferences endpoint exists).
  const prefs = { theme: "system", size: 3, sidebar: "open", sidebar_width: 260, panel_width: 480 };
  const step20 = (v, lo, hi, dflt) => {
    const n = Number(v);
    return Number.isFinite(n) ? Math.max(lo, Math.min(hi, Math.round(n / 20) * 20)) : dflt;
  };
  function cleanPrefs(p) {
    const out = {};
    if (!p || typeof p !== "object") return out;
    if (["system", "light", "dark"].includes(p.theme)) out.theme = p.theme;
    if (Number.isInteger(Number(p.size)) && Number(p.size) >= 1 && Number(p.size) <= 7) out.size = Number(p.size);
    if (p.sidebar === "open" || p.sidebar === "closed") out.sidebar = p.sidebar;
    if (p.sidebar_width != null) out.sidebar_width = step20(p.sidebar_width, 200, 420, 260);
    if (p.panel_width != null) out.panel_width = step20(p.panel_width, 360, 1000, 460);
    return out;
  }
  function applyPrefs() {
    const root = document.documentElement;
    if (prefs.theme === "light" || prefs.theme === "dark") root.dataset.t = prefs.theme;
    else delete root.dataset.t;
    root.dataset.size = String(prefs.size);
    root.dataset.sw = String(prefs.sidebar_width);
    root.dataset.pw = String(prefs.panel_width);
    for (const b of document.querySelectorAll("[data-theme-choice]")) b.setAttribute("aria-checked", String(b.dataset.themeChoice === prefs.theme));
    $("text-size").value = String(prefs.size);
    applySidebar();
  }
  // Saved on the Mac through /api/preferences, a moment after the last change (the text size slider sends many).
  let prefsPending = {};
  let prefsTimer = 0;
  function savePrefs(changes) {
    const clean = cleanPrefs(changes);
    Object.assign(prefs, clean);
    applyPrefs();
    Object.assign(prefsPending, clean);
    clearTimeout(prefsTimer);
    prefsTimer = setTimeout(() => {
      const send = prefsPending;
      prefsPending = {};
      API.prefs.save(send).catch((err) => toast(err.message));
    }, 400);
  }
  for (const b of document.querySelectorAll("[data-theme-choice]")) b.addEventListener("click", () => savePrefs({ theme: b.dataset.themeChoice }));
  $("text-size").addEventListener("input", (e) => savePrefs({ size: Number(e.target.value) }));

  // ---------- sidebar: toggle, narrow windows, resizing ----------
  const appEl = $("app");
  const mqlWide = window.matchMedia("(min-width: 1101px)");
  const sidebarOpen = () => (mqlWide.matches ? prefs.sidebar === "open" : S.sideNarrow);
  function applySidebar() {
    const open = sidebarOpen();
    appEl.classList.toggle("side-open", open);
    appEl.classList.toggle("side-closed", !open);
    $("sidebar").inert = !open;
  }
  function toggleSidebar(force) {
    const open = typeof force === "boolean" ? force : !sidebarOpen();
    if (mqlWide.matches) savePrefs({ sidebar: open ? "open" : "closed" });
    else { S.sideNarrow = open; applySidebar(); }
  }
  const closeNarrowSidebar = () => { if (!mqlWide.matches && S.sideNarrow) toggleSidebar(false); };
  mqlWide.addEventListener("change", () => { S.sideNarrow = false; applySidebar(); });
  $("hide-sidebar").addEventListener("click", () => toggleSidebar(false));
  $("show-sidebar").addEventListener("click", () => toggleSidebar());
  $("side-scrim").addEventListener("click", () => toggleSidebar(false));

  function startResize(which, e) {
    if (e.button !== 0 || !mqlWide.matches) return;
    e.preventDefault();
    const startX = e.clientX;
    const key = which === "side" ? "sidebar_width" : "panel_width";
    const start = prefs[key];
    const [lo, hi] = which === "side" ? [200, 420] : [360, 1000];
    appEl.classList.add("resizing");
    const move = (ev) => {
      const dx = ev.clientX - startX;
      prefs[key] = step20(which === "side" ? start + dx : start - dx, lo, hi, start);
      applyPrefs();
    };
    const up = () => {
      document.removeEventListener("mousemove", move);
      document.removeEventListener("mouseup", up);
      appEl.classList.remove("resizing");
      savePrefs({ [key]: prefs[key] });
    };
    document.addEventListener("mousemove", move);
    document.addEventListener("mouseup", up);
  }
  $("resize-side").addEventListener("mousedown", (e) => startResize("side", e));
  $("resize-panel").addEventListener("mousedown", (e) => startResize("panel", e));

  // Starholm's own updates: the last GET /api/updates, what the Status page shows, and the notice already shown
  const UPD = { st: null, ui: { checking: false }, timer: null, req: 0, shown: null, loading: false, toldRestart: false };

  // ---------- status ----------
  // Checked every 30 seconds, or every 4 while something is starting; never while the window is hidden (each check
  // makes the server ask Ollama and read the library's files), and once more as soon as it is shown again.
  let warmed = "";
  async function loadStatus() {
    try { S.status = await API.status(); S.statusError = null; }
    catch (err) { S.status = null; S.statusError = err.message; }
    renderStatus();
    const s = S.status;
    const modelReady = s && (s.ollama_checked === false || (s.ollama && s.model_installed));  // not checked with Documents only
    const settled = modelReady && !(Array.isArray(s.warming) && s.warming.length) && !(s.view.refresh && s.view.refresh.running);
    return settled ? 30000 : 4000;
  }
  const statusPoll = F.createPoller({ load: loadStatus, schedule: (fn, ms) => setTimeout(fn, ms), cancel: clearTimeout, isHidden: () => document.hidden });
  document.addEventListener("visibilitychange", () => statusPoll.visibility());
  const documentsOnly = () => !!(S.status && S.status.answers && S.status.answers.documents_only === true);
  // Library-only mode: the Library only switch, or no model to answer with (a question then shows the library's documents).
  const libraryOnly = () => documentsOnly() || M.noModel(S.status);
  function shortDate(sec) { return new Date(sec * 1000).toLocaleDateString("en-GB", { day: "numeric", month: "short" }); }
  function longDate(sec) {
    return new Date(sec * 1000).toLocaleString("en-GB", { weekday: "short", day: "numeric", month: "short", hour: "2-digit", minute: "2-digit" });
  }
  function clock(sec) { return new Date(sec * 1000).toLocaleTimeString("en-GB", { hour: "2-digit", minute: "2-digit" }); }
  function bytes(n) {
    if (!Number.isFinite(n) || n <= 0) return "";
    const gb = n / 1e9;
    return gb >= 10 ? `${Math.round(gb)} GB` : gb >= 1 ? `${gb.toFixed(1)} GB` : `${Math.max(1, Math.round(n / 1e6))} MB`;
  }
  // Why a question can't be answered now, or null if it can (or the status is unknown). With "Documents only" on, a
  // question needs only the library; Summarise (needModel) needs the model too.
  function notReady(needModel = !libraryOnly()) {
    const s = S.status;
    if (!s) return null;
    if (!needModel) return s.library ? null : "There's no library on this computer yet, so there's nothing to search.";
    if (s.ollama_checked === false) return null;  // Documents only: the model wasn't checked; Summarise says if it fails
    if (!s.ollama) return s.ollama_starting ? "The model is still starting. Try again in a moment." : "The model isn't running. You can still browse the library.";
    if (!Array.isArray(s.models) || !s.models.length) return "No model is installed. You can still browse the library.";
    if (!s.model_installed) return "The chosen model isn't installed. Download one in Settings › Model.";
    return null;
  }
  function statusLine() {
    const s = S.status;
    if (!s) return { dot: "off", text: S.statusError || "Can't reach Starholm" };
    const v = s.view;
    if (v.refresh && v.refresh.running) return { dot: "warn", text: "Refreshing the library" };
    if (v.newer) return { dot: "", text: "New library ready, restart" };
    if (s.ollama_checked === false || M.noModel(s)) return s.library ? { dot: M.noModel(s) ? "warn" : "", text: `Library only${v.built ? ` · library from ${shortDate(v.built)}` : ""}` } : { dot: "warn", text: "No library found" };
    if (!s.ollama) return { dot: "off", text: s.ollama_starting ? "Starting the model…" : "Model not running" };
    if (!Array.isArray(s.models) || !s.models.length) return { dot: "off", text: "No model installed" };
    if (!s.model_installed) return { dot: "off", text: "Model not installed" };
    if (!s.library) return { dot: "warn", text: "No library found" };
    return { dot: "", text: v.built ? `Ready · library from ${shortDate(v.built)}` : "Ready" };
  }
  function renderStatus() {
    const st = statusLine();
    $("status-dot").className = `dot${st.dot ? ` ${st.dot}` : ""}`;
    $("status-text").textContent = st.text;
    $("side-status").title = st.text;
    const why = notReady();
    const note = why ? null : M.welcomeNote(S.status);  // library-only because there is no model: say so, and where to fix it
    $("start-offline").hidden = !why && !note;
    $("start-offline-text").textContent = why || (note ? note.text : "");
    $("start-offline-link").hidden = !note;
    $("under").textContent = why || (libraryOnly() ? "Questions show the documents the library finds." : "Answers can be wrong. Check the sources.");
    $("composer").classList.toggle("off", !!why);
    const s = S.status;
    if (F.shouldWarm(s, warmed)) {  // load the model ahead of the first question, once per model; not with Documents only
      warmed = s.model;
      API.warm(s.model).catch(() => { /* it loads on the first question instead */ });
    }
    if ($("settings").open) renderStatusPage();
    const ready = s && s.app_update;  // a new version is downloaded and checked: fetch the details, then the notice
    if (ready && !ready.dismissed && UPD.shown !== ready.version && !UPD.loading) loadUpdates();
  }
  $("side-status").addEventListener("click", () => openSettings(M.noModel(S.status) ? "model" : "status"));
  $("start-offline-link").addEventListener("click", () => openSettings("model"));

  // ---------- emergency numbers ----------
  // The country card (country.js draws it): the Emergency button's popover and Settings › Emergency numbers, with the
  // country picker and the free printout (an A5 page and a wallet card). No card: the local-emergency-number line.
  // Which numbers may show in red: the rules live in render.js (EMERGENCY), where the render test checks them.
  const emergencyNumbers = () => window.SLRender.EMERGENCY.bodyNumbers(S.emergency);
  async function loadEmergency() {
    try { S.emergency = await API.emergency.load(); }
    catch (err) { S.emergency = null; toast(err.message); }
    renderEmergency();
  }
  function renderEmergency() {
    renderGuided();
    const C = window.SLCountry;
    const e = S.emergency;
    $("sos-country").textContent = e && e.current ? "Emergency numbers" : "";
    $("sos-numbers").replaceChildren(...C.cardView(h, e, { compact: true }));
    const opts = C.options(e);
    $("country-select").replaceChildren(...opts.map((o) => h("option", { value: o.value, disabled: !o.value }, o.label)));
    $("country-select").value = (opts.find((o) => o.selected) || { value: "" }).value;
    $("country-hint").textContent = C.sourceLine(e);
    $("country-card").replaceChildren(...C.cardView(h, e, { inSettings: true }));
  }
  async function chooseCountry(code) {
    if (!code) return;
    try { S.emergency = await API.emergency.choose(code); renderEmergency(); }
    catch (err) { toast(err.message); renderEmergency(); }
  }
  $("country-select").addEventListener("change", (ev) => chooseCountry(ev.target.value));
  $("country-print").addEventListener("click", (ev) => printNumbers(ev.currentTarget));
  function setSos(open) {
    $("sos-pop").hidden = !open;
    $("sos-btn").setAttribute("aria-expanded", String(open));
  }
  $("sos-btn").addEventListener("click", (e) => { e.stopPropagation(); setSos($("sos-pop").hidden); });
  $("sos-pop").addEventListener("click", (e) => e.stopPropagation());
  $("sos-change").addEventListener("click", () => { setSos(false); openSettings("emergency"); });
  document.addEventListener("click", () => { if (!$("sos-pop").hidden) setSos(false); });

  // ---------- saved chats ----------
  const chatTitle = (c) => str(c && c.title).trim() || "Untitled chat";
  async function loadChats() {
    try {
      const data = await API.chats();
      S.chats = Array.isArray(data.chats) ? data.chats.filter((c) => c && ID_RE.test(str(c.id))) : [];
    } catch (err) { S.chats = []; toast(err.message); }
    renderChats();
  }
  function upsertChat(summary) {
    if (!summary || !ID_RE.test(str(summary.id))) return;
    S.chats = [summary, ...S.chats.filter((c) => c.id !== summary.id)];
    renderChats();
  }
  function renderChats() {
    const list = $("chat-list");
    list.replaceChildren();
    if (!S.chats.length) { list.append(h("div", { class: "chat-empty" }, "No saved chats yet.")); return; }
    for (const c of S.chats) {
      const current = c.id === S.current && S.view === "chat";
      const more = h("button", { class: "icon-btn chat-more", type: "button", "aria-label": `Options for ${chatTitle(c)}`, "aria-haspopup": "menu", title: "Options" }, icon("dots", "sm"));
      more.addEventListener("click", (e) => { e.stopPropagation(); openChatMenu(c.id, more); });
      list.append(h("div", { class: `chat-item${current ? " current" : ""}`, dataset: { id: c.id } },
        h("button", { class: "chat-link", type: "button", title: chatTitle(c), "aria-current": current ? "page" : null,
          onclick: () => { openChat(c.id); closeNarrowSidebar(); } }, chatTitle(c)),
        more));
    }
  }

  let menuFor = null;
  const menu = $("chat-menu");
  function openChatMenu(id, anchor) {
    closeChatMenu();
    menuFor = { id, anchor };
    anchor.closest(".chat-item")?.classList.add("menu-open");
    menu.hidden = false;
    const r = anchor.getBoundingClientRect();
    menu.style.top = `${Math.max(8, Math.min(r.bottom + 4, window.innerHeight - menu.offsetHeight - 8))}px`;
    menu.style.left = `${Math.max(8, Math.min(r.left, window.innerWidth - menu.offsetWidth - 8))}px`;
    $("menu-rename").focus();
  }
  function closeChatMenu() {
    if (!menuFor) return;
    menuFor.anchor.closest(".chat-item")?.classList.remove("menu-open");
    menu.hidden = true;
    menuFor = null;
  }
  document.addEventListener("click", (e) => { if (menuFor && !menu.contains(e.target)) closeChatMenu(); });
  menu.addEventListener("keydown", (e) => {
    const items = [...menu.querySelectorAll("button")];
    const at = items.indexOf(document.activeElement);
    if (e.key === "Escape") { e.stopPropagation(); const a = menuFor?.anchor; closeChatMenu(); a?.focus(); }
    else if (e.key === "ArrowDown") { e.preventDefault(); items[(at + 1) % items.length].focus(); }
    else if (e.key === "ArrowUp") { e.preventDefault(); items[(at - 1 + items.length) % items.length].focus(); }
  });
  $("menu-rename").addEventListener("click", () => { const id = menuFor?.id; closeChatMenu(); if (id) startRename(id); });
  $("menu-delete").addEventListener("click", () => { const id = menuFor?.id; closeChatMenu(); if (id) deleteChat(id); });

  function startRename(id) {
    const item = $("chat-list").querySelector(`.chat-item[data-id="${id}"]`);  // id passed ID_RE
    const chat = S.chats.find((c) => c.id === id);
    if (!item || !chat) return;
    const input = h("input", { class: "chat-rename", type: "text", maxlength: "120", "aria-label": "New name for the chat" });
    input.value = chatTitle(chat);
    item.replaceChildren(input);
    input.focus();
    input.select();
    let done = false;
    const finish = async (save) => {
      if (done) return;
      done = true;
      const title = input.value.trim();
      if (save && title && title !== chatTitle(chat)) {
        try {
          const out = await API.renameChat(id, title);
          if (out.chat && ID_RE.test(str(out.chat.id))) {
            const at = S.chats.findIndex((c) => c.id === id);
            if (at >= 0) S.chats[at] = out.chat;
          }
          if (id === S.current) setTitle(chatTitle(out.chat || { title }));
        } catch (err) { toast(err.message); }
      }
      renderChats();
    };
    input.addEventListener("keydown", (e) => {
      if (e.key === "Enter") { e.preventDefault(); finish(true); }
      else if (e.key === "Escape") { e.preventDefault(); e.stopPropagation(); finish(false); }
    });
    input.addEventListener("blur", () => finish(true));
  }
  async function deleteChat(id) {
    const chat = S.chats.find((c) => c.id === id);
    const ok = await confirmDialog("Delete this chat?", [`“${chatTitle(chat)}” will be deleted from this computer. This can't be undone.`], "Delete");
    if (!ok) return;
    try {
      await API.deleteChat(id);
      S.chats = S.chats.filter((c) => c.id !== id);
      if (id === S.current) newChat();
      renderChats();
    } catch (err) { toast(err.message); }
  }

  // ---------- views ----------
  const mainEl = $("main");
  const messagesEl = $("messages");
  const scrollEl = $("scroll");
  const composer = $("composer");
  const input = $("input");
  const pageView = (view) => view === "library" || view === "guides" || view === "plan" || view === "map";  // a page of its own, with no composer
  function setView(view) {
    S.view = view;
    mainEl.classList.toggle("is-start", view === "start");
    mainEl.classList.toggle("is-library", pageView(view));
    $("start").hidden = view !== "start";
    messagesEl.hidden = view !== "chat";
    $("view-chat").hidden = pageView(view);
    $("view-library").hidden = view !== "library";
    $("view-guides").hidden = view !== "guides";
    $("view-plan").hidden = view !== "plan";
    $("view-map").hidden = view !== "map";
    mainEl.classList.toggle("is-map", view === "map");
    if (view !== "map" && window.SLMaps) window.SLMaps.close();
    $("open-library").classList.toggle("on", view === "library" || view === "map");
    $("open-guides").classList.toggle("on", view === "guides");
    $("open-plan").classList.toggle("on", view === "plan");
    if (view === "start") $("start-composer").append(composer);
    else $("composer-wrap").insertBefore(composer, $("under"));
    if (view !== "chat") setEditing(null);
    if (view !== "chat" && answerPanel()) closePanel(false);
    updateSourcesButton();
    renderChats();
  }
  function setTitle(text) {
    $("chat-title").textContent = text || "";
    document.title = text ? `${text} · Starholm` : "Starholm";
  }
  // Following a streaming answer down (window.SLFlow.createFollow): the person scrolling up stops it, the bottom
  // starts it again, and "Jump to latest" shows while it is stopped with more below.
  const follow = F.createFollow();
  const jumpBtn = $("jump-latest");
  const syncJump = () => { jumpBtn.hidden = S.view !== "chat" || !follow.showJump(scrollEl); };
  scrollEl.addEventListener("scroll", () => { follow.scrolled(scrollEl); syncJump(); }, { passive: true });
  scrollEl.addEventListener("wheel", (e) => { if (e.deltaY < 0 && !e.ctrlKey) follow.intent(true, scrollEl); }, { passive: true });
  let touchY = null;
  scrollEl.addEventListener("touchstart", (e) => { touchY = e.touches.length === 1 ? e.touches[0].clientY : null; }, { passive: true });
  scrollEl.addEventListener("touchmove", (e) => {
    if (touchY !== null && e.touches.length === 1 && e.touches[0].clientY > touchY + 2) follow.intent(true, scrollEl);  // a finger down: the page up
  }, { passive: true });
  scrollEl.addEventListener("pointerdown", (e) => { if (e.target === scrollEl) follow.intent(true, scrollEl); });  // the scroll bar
  document.addEventListener("keydown", (e) => {  // the keys that scroll the chat up, outside a text field
    if (S.view !== "chat" || !$("reading").hidden || !(/^(PageUp|ArrowUp|Home)$/.test(e.key) || (e.key === " " && e.shiftKey))) return;
    if (!(e.target instanceof Element && e.target.closest("input, textarea, select, [contenteditable]"))) follow.intent(true, scrollEl);
  });
  function scrollDown(force = false) {
    if (force) follow.reset();
    const top = follow.target(scrollEl);
    if (top !== null) { scrollEl.scrollTop = top; follow.wrote(scrollEl.scrollTop); }
    syncJump();
  }
  jumpBtn.addEventListener("click", () => { scrollDown(true); input.focus(); });

  function clearConversation() {
    if (S.stream) S.stream.abort();
    S.loadRequest++;
    S.answers = [];
    S.turns = [];
    setEditing(null);
    messagesEl.replaceChildren();
    if (answerPanel()) closePanel(false);
  }
  function newChat() {
    clearConversation();
    S.current = null;
    setTitle("");
    GUIDED.path = [];
    renderGuided();
    setView("start");
    input.focus();
  }

  async function openChat(id) {
    if (!ID_RE.test(id)) return;
    if (S.stream) S.stream.abort();
    const request = ++S.loadRequest;
    let chat;
    try { chat = await API.chat(id); }
    catch (err) {
      if (request !== S.loadRequest) return;
      toast(err.status === 404 ? "That chat isn't there any more." : err.message);
      if (err.status === 404) { S.chats = S.chats.filter((c) => c.id !== id); renderChats(); }
      return;
    }
    if (request !== S.loadRequest) return;
    clearConversation();
    S.loadRequest = request;
    S.current = id;
    setTitle(chatTitle(chat));
    setView("chat");
    let turn = null;
    for (const m of Array.isArray(chat.messages) ? chat.messages : []) {
      if (!m || typeof m !== "object") continue;
      if (m.role === "user") { turn = addTurn(str(m.content), Number(m.at) || null); turn.saved = true; }
      else if (m.role === "assistant" && turn && !turn.answer) {
        const a = new Answer(turn);
        a.finish({ answer: str(m.content), sources: m.sources, follow_ups: m.follow_ups, cut_off: m.cut_off, unfinished: m.unfinished,
          marked: m.marked, claims: m.claims, grounding: m.grounding, no_match: m.no_match, no_match_call: m.no_match_call, emergency_call: m.emergency_call, documents_only: m.documents_only, no_model: m.no_model, documents: m.documents }, false);
      }
    }
    scrollDown(true);
  }

  // ---------- the composer ----------
  function autosize() {
    input.style.height = "auto";
    if (!input.value) return;  // one line: measuring the placeholder can give a wrong height before layout settles
    input.style.height = `${Math.min(input.scrollHeight, 240)}px`;
  }
  function setStreaming(on) {
    const send = $("send");
    send.dataset.icon = on ? "stop" : "send";
    setIcon(send, send.dataset.icon);
    send.setAttribute("aria-label", on ? "Stop" : "Send");
    send.title = on ? "Stop" : "Send";
    updateSend();
  }
  function updateSend() { $("send").disabled = !S.stream && !input.value.trim(); }
  function prefill(text) {
    input.value = text;
    autosize();
    updateSend();
    input.focus();
    input.setSelectionRange(input.value.length, input.value.length);
  }
  input.addEventListener("input", () => { autosize(); updateSend(); });
  input.addEventListener("keydown", (e) => {
    if (e.key === "Enter" && !e.shiftKey && !e.isComposing) { e.preventDefault(); composer.requestSubmit(); }
  });
  composer.addEventListener("submit", (e) => {
    e.preventDefault();
    if (S.stream) { S.stream.abort(); return; }
    const text = input.value.trim();
    if (!text) return;
    const blocked = notReady();
    if (blocked) { toast(blocked); return; }  // the question stays in the box for later
    input.value = "";
    autosize();
    const replace = S.editing;
    setEditing(null);
    ask(text, { replace });
  });

  // Edit: the question goes back in the composer; sending it replaces that question and everything after it.
  function setEditing(turn) {
    S.editing = turn && S.turns.includes(turn) ? turn : null;
    $("edit-bar").hidden = !S.editing;
  }
  $("edit-cancel").addEventListener("click", () => { setEditing(null); input.value = ""; autosize(); updateSend(); input.focus(); });

  // ---------- start screen ----------
  // Under the question box: the guided questions (guided-tree.js), a few quiet chips, each opening the next step in
  // place, ending on a guide, a library search or a question put in the box. A question marked call shows the current
  // country's emergency number first. Back goes up one step, Start over to the top; "Something to read" stays at the top.
  // One option per line, left-aligned with the question box. An open step shows the question chosen as an open row with
  // its options indented under it; they slide down into place going deeper and in from the left going back (app.css,
  // still under prefers-reduced-motion). shown: the depth last drawn, to tell which way the step moved.
  const GUIDED = { path: [], shown: 0 };
  const GUIDED_ICON = { question: "chevR", guide: "map", search: "search", ask: "chat" };
  function guidedOption(o) {
    return h("button", { class: `suggest-item guided-${o.kind}`, type: "button", onclick: () => chooseGuided(o) },
      icon(GUIDED_ICON[o.kind] || "chevR", "sm"), o.label);
  }
  function guidedCall() {
    const E = window.SLRender.EMERGENCY;
    const t = E.callTarget(S.emergency);
    const where = S.emergency && S.emergency.current ? S.emergency.current.name : "";
    return h("div", { class: "guided-call", role: "note" },
      h("span", { class: "guided-call-line" }, icon("phone", "sm"), E.callText(t)),
      where ? h("span", { class: "guided-where" }, where) : null);
  }
  function renderGuided(focus = false) {
    const step = F.guidedStep(window.SLGuidedTree, GUIDED.path) || F.guidedStep(window.SLGuidedTree, (GUIDED.path = []));
    if (!step) { $("suggest").replaceChildren(); return; }
    const top = step.depth === 0;
    const move = step.depth > GUIDED.shown ? " guided-deeper" : step.depth < GUIDED.shown ? " guided-up" : "";
    GUIDED.shown = step.depth;
    const back = () => { GUIDED.path = GUIDED.path.slice(0, -1); renderGuided(true); };
    const restart = () => { GUIDED.path = []; renderGuided(true); };
    const blocks = {
      call: guidedCall,
      question: () => h("p", { class: "guided-q" }, icon("chevD", "sm"), step.question),
      options: () => h("div", { class: `guided-options${move}`, role: "group", "aria-label": top ? "Guided questions" : step.question },
        step.options.map(guidedOption),
        top ? h("button", { class: "suggest-item", type: "button", onclick: () => randomRead() }, icon("shuffle", "sm"), "Something to read") : null),
      nav: () => h("div", { class: "guided-nav" },
        h("button", { class: "link", type: "button", onclick: back }, icon("back", "sm"), "Back"),
        step.depth > 1 ? h("button", { class: "link", type: "button", onclick: restart }, "Start over") : null),
    };
    fill($("suggest"), F.guidedBlocks(step).map((b) => blocks[b]()));
    $("suggest").classList.toggle("guided-open", !top);
    if (focus) $("suggest").querySelector(".guided-options button")?.focus();
  }
  function chooseGuided(o) {
    if (o.kind === "question") { GUIDED.path = [...GUIDED.path, o.index]; renderGuided(true); return; }
    if (o.kind === "guide") { openGuide(o.guide, document.activeElement, { anchor: o.anchor }); return; }
    if (o.kind === "search") { searchLibrary(o.search); return; }
    if (o.kind === "ask") prefill(o.ask);
  }
  renderGuided();

  // ---------- questions ----------
  function ago(at) {
    if (!at) return "";
    const s = Date.now() / 1000 - at;
    if (s < 60) return "Just now";
    if (s < 3600) { const m = Math.floor(s / 60); return `${m} ${m === 1 ? "minute" : "minutes"} ago`; }
    if (s < 86400) { const hr = Math.floor(s / 3600); return `${hr} ${hr === 1 ? "hour" : "hours"} ago`; }
    if (s < 172800) return "Yesterday";
    return new Date(at * 1000).toLocaleDateString("en-GB", { day: "numeric", month: "short", year: "numeric" });
  }
  setInterval(() => { for (const el of document.querySelectorAll(".when[data-at]")) el.textContent = ago(Number(el.dataset.at)); }, 60000);

  function addTurn(text, at = Date.now() / 1000) {
    const turn = { text, userEl: null, answer: null, saved: false };
    const body = h("div", { class: "bubble-text" }, text);
    const bubble = h("div", { class: "bubble" }, body);
    const el = h("div", { class: "msg-user" }, bubble,
      h("div", { class: "msg-meta" },
        at ? h("span", { class: "when", dataset: { at: String(Math.round(at)) } }, ago(at)) : null,
        h("button", { class: "icon-btn sm", type: "button", "aria-label": "Copy question", title: "Copy", onclick: () => copyText(text) }, icon("copy", "sm")),
        h("button", { class: "icon-btn sm", type: "button", "aria-label": "Edit question", title: "Edit and ask again", onclick: () => editTurn(turn) }, icon("edit", "sm")),
        h("button", { class: "icon-btn sm", type: "button", "aria-label": "Ask again", title: "Ask again", onclick: () => askAgain(turn) }, icon("retry", "sm"))));
    turn.userEl = el;
    S.turns.push(turn);
    messagesEl.append(el);
    // Long questions (more than about five lines) are cut off with a fade and "Show more".
    {  // measured right away: reading scrollHeight lays the page out first
      const line = parseFloat(getComputedStyle(body).lineHeight) || 22;
      if (body.scrollHeight <= line * 5.6) return turn;
      bubble.classList.add("clamped");
      const more = h("button", { class: "show-more", type: "button", "aria-expanded": "false" }, "Show more");
      more.addEventListener("click", () => {
        const open = bubble.classList.toggle("clamped") === false;
        more.textContent = open ? "Show less" : "Show more";
        more.setAttribute("aria-expanded", String(open));
      });
      bubble.append(more);
    }
    return turn;
  }
  function askAgain(turn) {
    const blocked = notReady();
    if (blocked) { toast(blocked); return; }
    ask(turn.text, { replace: turn });
  }
  // Summarise a "Documents only" question: the model answers it now (in place when it is saved).
  function summariseTurn(turn) {
    if (!turn) return;
    const blocked = notReady(true);
    if (blocked) { toast(blocked); return; }
    ask(turn.text, { replace: turn, summarise: true });
  }
  function askNew(text) {
    const blocked = notReady();
    if (blocked) { toast(blocked); return; }
    ask(text);
  }
  function editTurn(turn) {
    prefill(turn.text);
    setEditing(turn);
  }
  // Where a question sits in the saved chat's messages (README "Edit and Ask again"): only saved turns are in the file.
  function savedIndex(turn) {
    const at = S.turns.indexOf(turn);
    return 2 * S.turns.slice(0, at).filter((t) => t.saved).length;
  }
  // Takes a turn and every one after it off the screen.
  function dropTurnsFrom(turn) {
    const at = S.turns.indexOf(turn);
    if (at < 0) return;
    for (const t of S.turns.splice(at)) {
      t.userEl.remove();
      if (t.answer) { t.answer.el.remove(); S.answers = S.answers.filter((a) => a !== t.answer); }
    }
    if (answerPanel() && !S.answers.includes(P.ans)) closePanel(false);
    updateSourcesButton();
  }

  // ---------- answers ----------
  // Streamed Markdown is re-rendered at most once per frame and at most every 100 ms, and less often when one render
  // is slow (a very long answer): three times as long as the render took, up to a second (flow.js createRenderer).
  const renderTimers = { raf: (fn) => requestAnimationFrame(fn), setTimer: (fn, ms) => setTimeout(fn, ms), now: () => performance.now() };

  class Answer {
    // turn: the question it answers. It goes right after that question on screen (Summarise answers in place).
    constructor(turn) {
      this.turn = turn;
      this.question = turn ? turn.text : "";
      if (turn) turn.answer = this;
      this.text = "";
      this.sources = [];
      this.follow = [];
      this.paths = {};        // decision path key -> ["yes", "no", ...] chosen so far
      this.live = false;
      this.mode = "standard"; // the answer mode it was written in (the chat event); quoted modes write markers
      this.marked = null;     // a quoted answer's text with its statements marked (done.marked)
      this.claims = [];       // and the check of each one (done.claims)
      this.noMatch = null;    // the server's no_match flag: true draws the page's own no-match lines
      this.found = F.createFound();  // the documents its searches found, for the side panel
      this.docOnly = false;   // a "Documents only" question: documents, no answer
      this.panelClosed = false;  // the person closed the panel while it showed this answer's documents
      this.body = h("div", { class: "answer-body" });
      this.notes = h("div", { class: "notes" });
      this.extra = h("div", { class: "answer-extra" });
      this.el = h("div", { class: "answer" }, this.body, this.notes, this.extra);
      if (turn && turn.userEl && turn.userEl.parentNode === messagesEl) turn.userEl.after(this.el);
      else messagesEl.append(this.el);
      S.answers.push(this);
      S.answers.sort((a, b) => S.turns.indexOf(a.turn) - S.turns.indexOf(b.turn));
    }
    waiting() {
      this.live = true;
      this.body.replaceChildren(h("div", { class: "thinking", role: "status" }, h("span", { class: "spinner" }), "Searching the library…"));
    }
    // The text to draw: the checked version once a quoted answer is done; while it streams, without its markers.
    shownText() {
      if (!this.live && this.marked) return this.marked;
      return this.mode === "standard" ? this.text : window.SLRender.stripQuoteMarkers(this.text, { live: this.live });
    }
    render() {
      const parts = splitAnswer(this.shownText(), { live: this.live });
      const kids = buildAnswer(parts, this);
      if (!kids.length && this.live) return;  // nothing to show yet: keep the spinner
      // A quoted answer is drawn in the neutral "being checked" style while it streams: no statement shows as
      // normal text before its check has run.
      this.body.classList.toggle("checking", window.SLRender.isChecking(this.live, this.mode));
      this.body.replaceChildren(...kids);
    }
    token(text) {
      this.text += str(text);
      this.renderer ||= F.createRenderer(() => { this.render(); scrollDown(); }, renderTimers);
      this.renderer.request();
    }
    clear() { this.text = ""; this.paths = {}; this.waiting(); }
    note(text) { this.notes.append(h("p", null, text)); scrollDown(); }
    memory(ev) {
      const what = { add: "Added a note", change: "Changed a note", delete: "Removed a note", full: "Rewrote its notes" }[ev.action] || "Changed its notes";
      const undo = h("button", { class: "btn", type: "button" }, "Undo");
      const row = h("div", { class: "memory-note" }, h("span", null, `${what}${ev.note ? `: “${str(ev.note)}”` : ""}`), undo);
      undo.addEventListener("click", async () => {
        undo.disabled = true;
        const fields = {};
        for (const k of ["action", "note", "old", "at", "before", "after"]) if (k in ev) fields[k] = ev[k];
        try {
          await API.undoMemory(fields);
          row.replaceChildren(h("span", null, "Undone."));
        } catch (err) { undo.disabled = false; toast(err.message); }
      });
      this.notes.append(row);
      scrollDown();
    }
    // A question answered with documents only: a line, Summarise, and the documents in the side panel.
    showDocuments() {
      const n = this.found.items.length;
      const summarise = h("button", { class: "btn", type: "button", onclick: () => summariseTurn(this.turn) }, icon("chat", "sm"), "Summarise");
      if (!n) {
        this.body.replaceChildren(h("div", { class: "nomatch" }, h("p", null, "I couldn't find this in your library."),
          this.noModel ? h("p", { class: "no-model-line" }, M.NO_MODEL_LINE) : null,
          h("button", { class: "btn", type: "button", onclick: showLibrary }, icon("book", "sm"), "Browse the library")));
        return;
      }
      // With no model to answer (no Ollama, not running, or not downloaded): the line saying so, and where to set one up.
      const setUp = h("button", { class: "btn", type: "button", onclick: () => openSettings("model") }, icon("box", "sm"), "Set up a model");
      this.body.replaceChildren(h("div", { class: "docs-only" },
        h("p", null, `${plural(n, "document")} from the library ${n === 1 ? "is" : "are"} in the side panel.${this.noModel ? "" : " No AI answer yet."}`),
        this.noModel ? h("p", { class: "no-model-line" }, M.NO_MODEL_LINE) : null,
        h("div", { class: "docs-only-actions" },
          h("button", { class: "btn", type: "button", onclick: (e) => openFound(this, e.currentTarget) }, icon("panel", "sm"), "Show the documents"),
          this.noModel ? setUp : summarise)));
    }
    finish(done, live = true) {
      if (this.renderer) this.renderer.stop();  // a render still queued must not overwrite the final answer
      this.live = false;
      this.found.done();
      this.sources = Array.isArray(done.sources) ? done.sources.filter((s) => s && isSourceId(s.id)) : [];
      this.follow = Array.isArray(done.follow_ups) ? done.follow_ups.map(str).map((q) => q.trim()).filter(Boolean).slice(0, 3) : [];
      // The checked text, or, for a quoted answer stopped before the server checked it, what it wrote with every
      // statement marked as not checked (render.js settleAnswer).
      const settled = window.SLRender.settleAnswer({ mode: this.mode, text: this.text, done,
        numbers: window.SLRender.EMERGENCY.heldNumbers(S.emergency) });
      const unchecked = settled.unchecked;
      Object.assign(this, { text: settled.text, marked: settled.marked, claims: settled.claims, noMatch: settled.noMatch,
        noMatchCall: done.no_match_call, emergencyCall: done.emergency_call });
      this.body.classList.remove("checking");
      if (done.documents_only === true) {
        this.docOnly = true;
        this.noModel = typeof done.no_model === "string" && !!done.no_model;
        if (Array.isArray(done.documents)) this.found.add({ documents: done.documents });
        this.showDocuments();
        this.extra.replaceChildren();
        updateSourcesButton();
        if (P.mode === "found" && P.ans === this) renderPanel();
        scrollDown();
        return;
      }
      this.render();
      if (!visibleText(this.text)) this.body.replaceChildren(...(live ? [h("p", { class: "muted" }, "No answer came back.")] : []));
      if (done.cut_off) this.note("The answer was cut off before it finished.");
      else if (done.unfinished) this.note("The answer may be unfinished.");
      if (unchecked && this.marked) this.note("It stopped before its statements were checked, so none of them is shown as from the library.");
      if (done.grounding && done.grounding.followed === false && this.marked) {
        this.note("The AI didn't mark where its statements come from, so none of them is shown as from the library.");
      }
      this.renderExtra();
      updateSourcesButton();
      if (P.mode === "found" && P.ans === this) renderPanel();
      scrollDown();
    }
    copyText() {
      const parts = splitAnswer(this.text);
      if (window.SLRender.showsNoMatch(parts, this.noMatch)) return window.SLRender.EMERGENCY.noMatchText(parts, this.noMatchCall, S.emergency);
      const call = window.SLRender.EMERGENCY.callText(window.SLRender.EMERGENCY.emergencyCall(this.emergencyCall, S.emergency));
      const body = this.marked ? window.SLRender.claimsText(this.marked, this.claims, this.sources) : visibleText(this.text);
      return call ? `${body}\n\n${call}` : body;
    }
    renderExtra() {
      const kids = [];
      // A no-match answer is the one line and "Browse the library" (BUILD-SPEC section 7): no chips, no follow-ups.
      const nomatch = window.SLRender.showsNoMatch(splitAnswer(this.text), this.noMatch);
      if (this.sources.length && !nomatch) {
        kids.push(h("div", { class: "sources", role: "group", "aria-label": "Sources" }, this.sources.map((s, n) =>
          h("button", { class: "chip", type: "button", title: str(s.source) || null, dataset: { n: String(n) },
            onclick: (e) => openSources(this, n, e.currentTarget, { full: true }) },
            h("span", { class: "num" }, String(n + 1)), h("span", { class: "ct" }, str(s.title) || "Untitled")))));
      }
      if (this.follow.length && !nomatch) {
        kids.push(h("div", { class: "follow", role: "group", "aria-label": "Follow-up questions" }, this.follow.map((q) =>
          h("button", { class: "follow-item", type: "button", onclick: () => askNew(q) }, icon("arrow", "sm"), q))));
      }
      if (visibleText(this.text)) {
        kids.push(h("div", { class: "actions" },
          h("button", { class: "icon-btn sm", type: "button", "aria-label": "Copy answer", title: "Copy", onclick: () => copyText(this.copyText()) }, icon("copy", "sm")),
          this.turn ? h("button", { class: "icon-btn sm", type: "button", "aria-label": "Ask again", title: "Ask again", onclick: () => askAgain(this.turn) }, icon("retry", "sm")) : null));
      }
      this.extra.replaceChildren(...kids);
      syncChips();
    }
  }

  function buildAnswer(parts, ans) {
    const numbers = emergencyNumbers();
    // A checked answer: statements not backed are greyed and marked, backed ones carry their source number.
    const claims = !ans.live && ans.marked ? window.SLRender.claimView(ans.claims, ans.sources) : null;
    const opts = { numbers, claims, onSource: (n, el) => { if (ans.sources[n]) openSources(ans, n, el, { full: true }); } };
    if (window.SLRender.showsNoMatch(splitAnswer(ans.text), ans.noMatch)) {  // the page's own lines, never the model's
      parts = splitAnswer(ans.text);
      return [h("div", { class: "nomatch" }, h("p", null, "I couldn't find this in your library."),
        callLineView(window.SLRender.EMERGENCY.noMatchCall(parts, ans.noMatchCall, S.emergency)),
        h("button", { class: "btn", type: "button", onclick: showLibrary }, icon("book", "sm"), "Browse the library"))];
    }
    const kids = [];
    const more = parts.paths.length || parts.blocks.length;
    if (parts.lead) kids.push(h("div", { class: "md lead-in" }, md(parts.lead, opts)));
    if (parts.summary.length) {
      kids.push(h("ul", { class: `summary md${more ? " more" : ""}` }, parts.summary.map((b) => {
        const li = h("li");
        const frag = md(b, opts);
        // One bullet is one line: unwrap the paragraph md() puts around it.
        if (frag.childNodes.length === 1 && frag.firstChild.nodeName === "P") li.append(...frag.firstChild.childNodes);
        else li.append(frag);
        return li;
      })));
    }
    for (const p of parts.paths) kids.push(pathView(p.parsed, ans, p.key));
    // The app's own call line: an emergency question it found nothing for (never the model's words, never greyed).
    const call = window.SLRender.EMERGENCY.emergencyCall(ans.emergencyCall, S.emergency);
    for (const b of parts.blocks) {
      if (b.kind === "plain") kids.push(h("div", { class: "steps-plain" }, claims ? window.SLRender.plainClaims(b.text, claims) : window.SLRender.noMarks(b.text)));
      else kids.push(h("div", { class: "md" }, md(b.text, { ...opts, headingShift: 1 })));
    }
    if (call && !ans.live) kids.push(h("div", { class: "emergency-call" }, callLineView(call)));
    return kids;
  }

  // The no-match answer's "Call <number> now.": the model's number only when it is one of the stored numbers for the
  // country shown; otherwise the number from the stored data.
  // A no-match answer's call line, drawn from the stored numbers (render.js noMatchCall); none for null.
  function callLineView(t) {
    if (!t) return null;
    const red = (x) => h("span", { class: "num-inline" }, x);
    if (t.numbers.length) return h("p", null, "Call ", red(t.numbers.join(" or ")), " now.");
    return h("p", null, `Call ${t.text} now.`);
  }

  // The decision path: one question at a time, answered steps above it.
  function pathView(parsed, ans, key) {
    const choices = ans.paths[key] || [];
    const numbers = emergencyNumbers();
    const claims = !ans.live && ans.marked ? window.SLRender.claimView(ans.claims, ans.sources) : null;
    let at = parsed.start;
    let done = false;
    const trail = [];
    for (const c of choices) {
      const node = parsed.nodes[at];
      trail.push({ q: node.q, c, say: node[c].say });
      at = node[c].next;
      if (at === null) { done = true; break; }
    }
    const say = (text) => {
      const frag = md(text, { numbers, claims, onSource: (n, b) => { if (ans.sources[n]) openSources(ans, n, b, { full: true }); } });
      return frag.childNodes.length === 1 && frag.firstChild.nodeName === "P" ? [...frag.firstChild.childNodes] : frag;
    };
    let el;
    const redraw = (focusSel) => {
      const next = pathView(parsed, ans, key);
      el.replaceWith(next);
      next.querySelector(focusSel)?.focus();
    };
    const choose = (c) => { ans.paths[key] = [...choices, c]; redraw(".path-btns .btn, .path-restart"); };
    const node = done ? null : parsed.nodes[at];
    el = h("section", { class: "path", "aria-label": "Step by step" },
      h("div", { class: "path-h" }, icon("flow", "sm"), h("span", null, "Step by step"),
        choices.length ? h("button", { class: "link path-restart", type: "button", onclick: () => { ans.paths[key] = []; redraw(".path-btns .btn"); } }, "Start over") : null),
      trail.map((t) => h("div", { class: "path-done" },
        h("div", { class: "path-q" }, h("span", null, window.SLRender.noMarks(t.q)), h("span", { class: "path-choice" }, `· ${t.c === "yes" ? "Yes" : "No"}`)),
        t.say ? h("div", { class: "path-say" }, say(t.say)) : null)),
      node ? h("div", { class: "path-now", "aria-live": "polite" },
        h("div", { class: "path-question" }, window.SLRender.noMarks(node.q)),
        h("div", { class: "path-btns" },
          h("button", { class: "btn", type: "button", onclick: () => choose("yes") }, "Yes"),
          h("button", { class: "btn", type: "button", onclick: () => choose("no") }, "No"))) : null,
      done ? h("div", { class: "path-end" }, icon("check", "sm"), "That's the last step.") : null);
    return el;
  }

  // ---------- asking ----------
  // replace: a turn on screen to answer in place of (Edit, Ask again), with it and every turn after it.
  // summarise: a model answer even with "Documents only" on. On a saved Documents-only turn it answers that turn in
  // place and keeps the turns after it.
  async function ask(text, { replace = null, summarise = false } = {}) {
    if (S.stream) S.stream.abort();
    closeNarrowSidebar();
    if (S.view !== "chat") {
      if (pageView(S.view)) { clearConversation(); S.current = null; }
      setView("chat");
    }
    let from = null;
    let turn = null;
    if (replace && S.turns.includes(replace)) {
      if (replace.saved && S.current) from = savedIndex(replace);
      if (summarise && from !== null && replace.answer && replace.answer.docOnly) {
        turn = replace;  // answered in place
        const old = turn.answer;
        old.el.remove();
        S.answers = S.answers.filter((a) => a !== old);
        turn.answer = null;
        if (P.ans === old) closePanel(false);
      } else dropTurnsFrom(replace);
    }
    if (!turn) {
      turn = addTurn(text);
      scrollDown(true);
    }
    const answer = new Answer(turn);
    answer.waiting();
    const ctrl = new AbortController();
    S.stream = ctrl;
    setStreaming(true);
    let gotDone = false;
    try {
      let id = S.current;
      if (!id) {
        const out = await API.newConversation(S.lastConversation, ctrl.signal);
        if (!ID_RE.test(str(out.conversation))) throw new API.ApiError("The server didn't start a new chat.", 0);
        id = out.conversation;
        S.current = id;
        setTitle(text.slice(0, 120));
      }
      S.lastConversation = id;
      const resp = await API.ask(id, text, from, ctrl.signal, { summarise });
      if (!resp.ok || !resp.body) {
        let data = null;
        try { data = await resp.json(); } catch { /* not JSON */ }
        throw new API.ApiError(API.errorText(resp.status, data), resp.status, data);
      }
      const reader = resp.body.getReader();
      const decoder = new TextDecoder();
      let buf = "";
      for (;;) {
        const { value, done } = await reader.read();
        if (done) break;
        buf += decoder.decode(value, { stream: true });
        let cut;
        while ((cut = buf.indexOf("\n\n")) >= 0) {
          const block = buf.slice(0, cut);
          buf = buf.slice(cut + 2);
          const data = block.split("\n").filter((l) => l.startsWith("data:")).map((l) => l.slice(5).replace(/^ /, "")).join("\n");
          if (!data) continue;  // a heartbeat
          let ev;
          try { ev = JSON.parse(data); } catch { continue; }
          if (!ev || typeof ev !== "object") continue;
          if (ev.type === "done") gotDone = true;
          handleEvent(ev, answer);
        }
      }
      if (!gotDone) answer.finish({}, true);
    } catch (err) {
      answer.finish({}, false);
      if (err && err.name === "AbortError") answer.note("Stopped.");
      else answer.note(err && err.message ? err.message : "Something went wrong.");
      if (from !== null) answer.note("The saved chat wasn't changed. Open it again from Recent to see it as it was.");
    } finally {
      if (S.stream === ctrl) S.stream = null;
      setStreaming(false);
    }
  }

  function handleEvent(ev, answer) {
    switch (ev.type) {
      case "chat":
        if (ID_RE.test(str(ev.id))) { S.current = ev.id; S.lastConversation = ev.id; }
        if (typeof ev.mode === "string") answer.mode = ev.mode;
        if (ev.documents_only === true) answer.docOnly = true;
        break;
      case "documents":  // found by a library search, before the answer is written: into the side panel at once
        if (answer.found.add(ev)) showFound(answer);
        break;
      case "show":  // the model asks to open an article for the person
        if (isArticleId(ev.id) && mqlWide.matches) openDoc(ev.id);
        break;
      case "retry": answer.clear(); break;
      case "token": answer.token(ev.text); break;
      case "done": answer.finish(ev, true); break;
      case "saved":
        if (answer.turn) answer.turn.saved = true;
        if (ev.chat) {
          upsertChat(ev.chat);
          if (ev.chat.id === S.current) setTitle(chatTitle(ev.chat));
        }
        break;
      case "save_failed": answer.note("The answer is fine, but it couldn't be saved to this chat. Start a new chat to keep asking."); break;
      case "memory": answer.memory(ev); break;
      case "memory_unchanged": answer.note("Nothing changed in its notes."); break;
      case "memory_failed": answer.note("It couldn't update its notes."); break;
      case "error": answer.note(str(ev.message) || "Something went wrong."); break;
      default: break;  // thinking, search, search_words, read, think_limit, memory_check: not shown
    }
  }

  // ---------- side panel: sources and full documents ----------
  const panel = $("panel");
  // mode: "sources" (an answer's sources), "found" (the documents its searches found), "doc" (one document)
  const P = { mode: null, ans: null, n: 0, full: false, docId: null, byDoc: false, opener: null, req: 0, ref: null, doc: null };
  const answerPanel = () => P.mode === "sources" || P.mode === "found";
  const articles = new Map();
  // A title opened by its document id (from the Library), at its first passage.
  function getDoc(docId) {
    const key = `d${Number(docId)}`;
    if (!articles.has(key)) {
      const p = API.articleByDoc(Number(docId));
      articles.set(key, p);
      p.catch(() => articles.delete(key));
    }
    return articles.get(key);
  }
  // A saved source's reference, so it opens by it when its id has moved: {key, url, title}; for a source saved before
  // sources kept their key (13 November 2026), its {name, title} (window.SLRender.sourceRef); null for none.
  const refOf = (s) => window.SLRender.sourceRef(s);
  function getArticle(id, ref = null) {
    const key = ref ? `${Number(id)}\u0000${ref.key || ""}\u0000${ref.name || ""}\u0000${ref.url || ""}\u0000${ref.title}` : String(Number(id));
    if (!articles.has(key)) {
      const p = API.article(Number(id), ref);
      articles.set(key, p);
      p.catch(() => articles.delete(key));
    }
    return articles.get(key);
  }
  function articleError(err) {
    if (err.status !== 404) return err.message;
    if (err.data && err.data.missing === true) return window.SLRender.MISSING_DOC;
    return S.status && !S.status.library ? "There's no library on this computer yet, so this source can't be opened." : "That source isn't in the library any more.";
  }
  function showPanel(opener) {
    if (opener) P.opener = opener;
    panel.hidden = false;
    $("panel-scrim").hidden = false;
    appEl.classList.add("with-panel");
    updateSourcesButton();
  }
  function closePanel(restoreFocus = true) {
    if (P.mode === "found" && P.ans) P.ans.panelClosed = true;  // not opened again by its next documents
    P.foundShown = null;
    P.req++;
    P.mode = null;
    panel.hidden = true;
    $("panel-scrim").hidden = true;
    appEl.classList.remove("with-panel");
    if (restoreFocus && P.opener && document.contains(P.opener)) P.opener.focus();
    P.opener = null;
    syncChips();
    updateSourcesButton();
  }
  $("panel-scrim").addEventListener("click", () => closePanel());
  // full: open source n itself, the whole document at its cited passage (a click on a source chip or a statement's
  // number); otherwise the list of the answer's sources with a preview (the Sources button, Back to sources).
  function openSources(ans, n, opener, { full = false } = {}) {
    const gs = full ? guideSource(ans.sources[n]) : null;
    if (gs) { openGuide(gs.slug, opener, { anchor: gs.anchor }); return; }  // a guide opens as a guide, at its section
    Object.assign(P, { mode: "sources", ans, n, full, docId: null, byDoc: false, ref: null });
    showPanel(opener);
    renderPanel();
  }
  // The documents an answer's searches found, while it is written (and after).
  function openFound(ans, opener) {
    if (P.mode !== "found" || P.ans !== ans) P.foundShown = null;
    Object.assign(P, { mode: "found", ans, n: 0, full: false, docId: null, byDoc: false, ref: null });
    ans.panelClosed = false;
    showPanel(opener);
    renderPanel();
  }
  // New documents for an answer: shown at once in a wide window, unless the person is reading something else in the
  // panel or closed it on this answer; the list just grows when it is already open on them.
  function showFound(ans) {
    updateSourcesButton();
    if (P.mode === "found" && P.ans === ans && !panel.hidden) { renderPanel(); return; }
    if (!mqlWide.matches || ans.panelClosed || (!panel.hidden && P.mode && !(P.mode === "found" && P.ans !== ans))) return;
    openFound(ans);
  }
  function openDoc(id, opener, { byDoc = false, ref = null } = {}) {
    Object.assign(P, { mode: "doc", ans: null, n: 0, full: true, docId: Number(id), byDoc, ref });
    showPanel(opener);
    renderPanel();
  }
  function syncChips() {
    for (const a of S.answers) {
      for (const chip of a.extra.querySelectorAll(".chip")) chip.classList.toggle("on", P.mode === "sources" && P.ans === a && Number(chip.dataset.n) === P.n);
    }
  }
  function updateSourcesButton() {
    const has = S.view === "chat" && S.answers.some((a) => a.sources.length || a.found.items.length);
    $("sources-btn").hidden = !has;
    $("sources-btn").setAttribute("aria-pressed", String(!panel.hidden));
  }
  $("sources-btn").addEventListener("click", (e) => {
    if (!panel.hidden) { closePanel(); return; }
    const a = [...S.answers].reverse().find((x) => x.sources.length || x.found.items.length);
    if (a && a.sources.length) openSources(a, 0, e.currentTarget);
    else if (a) openFound(a, e.currentTarget);
  });

  const closeBtn = () => h("button", { class: "icon-btn", type: "button", "aria-label": "Close", title: "Close", onclick: () => closePanel() }, icon("close"));
  function docParts(doc) { return Array.isArray(doc.parts) ? doc.parts.filter((p) => p && typeof p === "object") : []; }
  // The parts of an article, the cited one highlighted. Returns [elements, the cited element or null].
  // guide: the guide's slug, only when showing a guide, so its own figures show.
  // The first part loses a first heading that only repeats the title shown above it; a library document's first part
  // reads its loose infobox lines as one table (factBox). A guide's [text](guide:slug) links open other guides.
  function partsView(doc, parts, highlight, guide = null) {
    let target = null;
    const ids = new Set();  // heading ids are unique across all the parts of one document
    const els = parts.map((p, i) => {
      const cited = highlight && p.id === doc.focus;
      const text = i === 0 ? window.SLRender.dropRepeatedTitle(p.text, doc.title) : p.text;
      const el = h("div", { class: `part md${cited ? " focus" : ""}` }, md(text, { headingShift: 1, ids, guide, bareTables: true,
        factBox: i === 0 && !guide, guides: guide ? G.slugs : null, onGuide: guide ? followGuide : null }));
      if (cited) target = el;
      return el;
    });
    return [els, target];
  }
  const addrLine = (doc) => (window.SLRender.safeWebUrl(str(doc.url)) ? h("p", { class: "addr" }, "Address: ", window.SLRender.inline(str(doc.url))) : null);
  const askAboutBtn = (title) => h("button", { class: "btn", type: "button", onclick: () => askAbout(title) }, icon("chat", "sm"), "Ask about this");

  async function renderPanel() {
    const req = ++P.req;
    P.doc = null;  // the whole document shown, once it is (for Cmd+P)
    const head = $("panel-head");
    const body = $("panel-body");
    const foot = $("panel-foot");
    const src = P.mode === "sources" ? P.ans.sources[P.n] : null;
    const id = src ? src.id : P.docId;
    const ref = src ? refOf(src) : P.ref;
    syncChips();
    body.classList.toggle("found-view", P.mode === "found");  // the found list scrolls in the body itself, padded
    if (P.mode === "found") { renderFound(req, head, body, foot); return; }
    if (P.mode === "sources" && !P.full) {
      head.replaceChildren(h("span", { class: "panel-title" }, "Sources"), closeBtn());
      const list = h("div", { class: "panel-list" }, P.ans.sources.map((x, n) =>
        h("button", { class: `src-item${n === P.n ? " on" : ""}`, type: "button", "aria-current": n === P.n ? "true" : null,
          onclick: () => { P.n = n; renderPanel(); } },
          h("span", { class: "num" }, String(n + 1)),
          h("span", { class: "src-text" }, h("span", null, str(x.title) || "Untitled"),
            h("span", { class: "sub" }, `${str(x.source)}${x.official ? " · Official" : ""}`)))));
      const reader = h("div", { class: "reader", "data-md-root": true }, h("p", { class: "hint" }, "Opening…"));
      body.replaceChildren(list, reader);
      foot.replaceChildren(askAboutBtn(str(src.title)));
      const gs = guideSource(src);
      if (gs) {  // a guide section: that section, and a button that opens the whole guide there
        let g;
        try { g = await API.guide(gs.slug); } catch (err) { if (req === P.req) reader.replaceChildren(h("p", { class: "muted" }, articleError(err))); return; }
        if (req !== P.req) return;
        const gdoc = { title: str(g.title), source: "Guide", parts: [{ id: 0, text: window.SLRender.guideSection(str(g.markdown), gs.anchor) }], focus: 0 };
        const [gels] = partsView(gdoc, gdoc.parts, false, gs.slug);
        reader.replaceChildren(h("h2", null, str(g.title) || "Guide"), ...gels,
          h("button", { class: "btn", type: "button", onclick: (e) => openGuide(gs.slug, e.currentTarget, { anchor: gs.anchor }) }, icon("doc", "sm"), "Open the guide"));
        return;
      }
      let doc;
      try { doc = await getArticle(id, ref); } catch (err) { if (req === P.req) reader.replaceChildren(h("p", { class: "muted" }, articleError(err))); return; }
      if (req !== P.req) return;
      const parts = docParts(doc);
      const at = Math.max(0, parts.findIndex((p) => p.id === doc.focus));
      const near = parts.slice(Math.max(0, at - 1), at + 2);
      const [els, target] = partsView(doc, near, true);
      reader.replaceChildren(h("h2", null, str(doc.title) || "Untitled"), ...els,
        h("button", { class: "btn", type: "button", onclick: () => { P.full = true; renderPanel(); } }, icon("doc", "sm"), "Open full document"));
      foot.replaceChildren(askAboutBtn(str(doc.title)));
      if (target && near[0] !== parts[at]) reader.scrollTop = Math.max(0, target.offsetTop - reader.offsetTop - 60);  // reading offsetTop lays the page out first
      return;
    }
    // The whole article in the panel, scrolled to the cited passage.
    fill(head,
      P.mode === "sources" ? h("button", { class: "icon-btn", type: "button", "aria-label": "Back to sources", title: "Back to sources",
        onclick: () => { P.full = false; renderPanel(); } }, icon("back")) : null,
      h("span", { class: "panel-title" }, src ? str(src.source) : "Opening…"),
      closeBtn());
    const reader = h("div", { class: "reader full", "data-md-root": true }, h("p", { class: "hint" }, "Opening…"));
    body.replaceChildren(reader);
    foot.replaceChildren();
    let doc;
    try { doc = await (P.byDoc ? getDoc(id) : getArticle(id, ref)); } catch (err) { if (req === P.req) reader.replaceChildren(h("p", { class: "muted" }, articleError(err))); return; }
    if (req !== P.req) return;
    const parts = docParts(doc);
    const highlight = P.mode === "sources";
    const [els, target] = partsView(doc, parts, highlight);
    head.querySelector(".panel-title").textContent = str(doc.source);
    const readBtn = () => h("button", { class: "icon-btn", type: "button", "aria-label": "Reading mode", title: "Reading mode",
      onclick: (e) => openReading(doc, { highlight, opener: e.currentTarget }) }, icon("expand"));
    head.insertBefore(readBtn(), head.lastChild);
    const [crumb, more] = articlePlace(doc, (docId, el) => openDoc(docId, el, { byDoc: true }));
    fill(reader, crumb,
      doc.official ? h("div", { class: "kicker" }, "Official") : null,
      h("h2", null, str(doc.title) || "Untitled"), ...els, more, addrLine(doc));
    P.doc = doc;
    foot.replaceChildren(
      h("button", { class: "btn", type: "button", onclick: (e) => openReading(doc, { highlight, opener: e.currentTarget }) }, icon("expand", "sm"), "Reading mode"),
      window.SLRender.isEmptyDoc(doc) ? null : h("button", { class: "btn", type: "button", id: "panel-print",
        onclick: (e) => printDocument(doc, e.currentTarget, true) }, icon("print", "sm"), "Print"),
      askAboutBtn(str(doc.title)));
    if (target && parts[0] && parts[0].id !== doc.focus) reader.scrollTop = Math.max(0, target.offsetTop - reader.offsetTop - 80);
  }

  // The documents an answer's searches found: title, source and the matching passage in full, each openable in the
  // reading view. A saved Documents-only question has no passages stored: each is loaded from the library.
  function renderFound(req, head, body, foot) {
    const ans = P.ans;
    const items = ans.found.items;
    // More documents for the list being read: the document at the top of the view stays at the top of the view.
    const same = P.foundShown === ans;
    let anchor = null;
    if (same) {
      for (const el of body.querySelectorAll(".found-item")) {
        if (el.offsetTop + el.offsetHeight > body.scrollTop) { anchor = { id: el.dataset.id, off: el.offsetTop - body.scrollTop }; break; }
      }
    }
    head.replaceChildren(h("span", { class: "panel-title" }, "Found in the library"), closeBtn());
    const intro = ans.found.refining && ans.live ? "A quick search on your question. Still refining: the AI's own results will come to the top."
      : ans.live ? "The AI is still writing its answer. You can start reading these now."
        : ans.docOnly && ans.noModel ? M.NO_MODEL_LINE
          : ans.docOnly ? "No AI answer yet. Press Summarise under the question for one." : "Everything the library search found for this question.";
    const refinedBelow = !ans.found.refining && items.some((d) => d.early) && items.some((d) => !d.early);
    const list = h("div", { class: "found-list" }, items.map((d) => {
      const passage = h("div", { class: "found-passage md", "data-md-root": true });
      if (d.passage) passage.append(md(d.passage, { headingShift: 2, bareTables: true }));
      else {
        passage.append(h("p", { class: "hint" }, "Opening…"));
        getArticle(d.id, refOf(d)).then((doc) => {
          if (req !== P.req) return;
          const part = docParts(doc).find((p) => p.id === doc.focus) || docParts(doc)[0];
          passage.replaceChildren(part ? md(str(part.text), { headingShift: 2, bareTables: true }) : h("p", { class: "muted" }, window.SLRender.MISSING_DOC));
        }).catch((err) => { if (req === P.req) passage.replaceChildren(h("p", { class: "muted" }, articleError(err))); });
      }
      const read = (e) => { ans.found.opened(d); openFoundDoc(d, e.currentTarget); };
      return h("section", { class: "found-item", dataset: { id: String(d.id) } },
        h("button", { class: "found-title", type: "button", onclick: read }, window.SLRender.capFirst(d.title)),
        h("div", { class: "sub" }, `${d.source}${d.official ? " · Official" : ""}${refinedBelow && d.early ? " · from the first quick search" : ""}`),
        passage,
        h("button", { class: "found-read", type: "button", onclick: read }, "Read the whole document"));
    }));
    body.replaceChildren(h("p", { class: `hint found-intro${ans.found.refining && ans.live ? " refining" : ""}` }, intro),
      items.length ? list : h("p", { class: "muted" }, "Nothing found yet."));
    const back = anchor && [...list.querySelectorAll(".found-item")].find((el) => el.dataset.id === anchor.id);
    body.scrollTop = back ? Math.max(0, back.offsetTop - anchor.off) : 0;  // reading offsetTop lays the page out first
    P.foundShown = ans;
    fill(foot, ans.docOnly && !ans.noModel && ans.turn ? h("button", { class: "btn", type: "button", onclick: () => summariseTurn(ans.turn) }, icon("chat", "sm"), "Summarise") : null);
  }
  async function openFoundDoc(d, opener) {
    try {
      const doc = await getArticle(d.id, refOf(d));
      openReading(doc, { highlight: true, opener });
    } catch (err) {
      if (err.status === 404) openReading({ title: d.title, source: d.source, parts: [] }, { opener });
      else toast(articleError(err));
    }
  }

  function askAbout(title) {
    closeReading(false);
    if (!mqlWide.matches) closePanel(false);
    if (pageView(S.view)) newChat();
    prefill(`About "${title}": `);
  }

  // ---------- an open library article's place: the breadcrumb above it, "More in this category" under it ----------
  // Both come back empty and hidden, and fill in when /api/library/place answers; a guide, a missing document or one
  // of Your files keeps them hidden. Each part of the breadcrumb opens that listing in the Library: the Library
  // itself, the source's A to Z page, or "Something to read" on the category. openRel(docId, el) opens a related title.
  function articlePlace(doc, openRel) {
    const crumb = h("nav", { class: "crumbs", "aria-label": "Where this article is in the library", hidden: true });
    const more = h("section", { class: "more-in", hidden: true });
    const focus = Number(doc && doc.focus);
    if (!doc || window.SLRender.isEmptyDoc(doc) || !Number.isSafeInteger(focus) || focus <= 0) return [crumb, more];
    API.articlePlace(focus).then((p) => {
      if (!p || !document.contains(crumb)) return;  // another article is open by now
      const sep = () => h("span", { class: "crumb-sep", "aria-hidden": "true" }, "›");
      const link = (text, onclick) => h("button", { class: "crumb", type: "button", onclick }, text);
      fill(crumb, link("Library", () => libraryAt()), sep(), link(p.sourceName, () => libraryAt({ source: p.source })),
        p.category ? [sep(), link(p.categoryName, () => libraryAt({ category: p.category }))] : null);
      crumb.hidden = false;
      if (!p.related.length) return;
      fill(more, h("h2", { class: "more-in-h" }, `More in ${p.category ? p.categoryName : p.sourceName}`),
        h("ul", null, p.related.map((r) => h("li", null,
          h("button", { class: "more-in-item", type: "button", onclick: (e) => openRel(r.doc, e.currentTarget) }, window.SLRender.capFirst(r.title))))));
      more.hidden = false;
    }).catch(() => {});  // the article shows without them
    return [crumb, more];
  }
  // The Library page at a source's A to Z list or at "Something to read" on a category (a breadcrumb's parts).
  async function libraryAt({ source = null, category = null } = {}) {
    if (category && CATEGORIES.some(([key]) => key === category)) {
      Object.assign(L, { cat: category, reads: null });
      if (L.built) renderRail();
    }
    await showLibrary();
    if (source) openSource(source);
    else if (category) $("lib-rail").scrollIntoView({ block: "start" });
  }

  // ---------- reading mode ----------
  // kind: "source" (a library document), "read" (Something to read), "guide" (one of the Guides)
  // stack: the guides the reader came from by a guide link, so Back returns to them ({doc, kind, guide, scroll}).
  const R = { doc: null, kind: "source", highlight: false, opener: null, category: "", swipe: null, guide: null, stack: window.SLRender.navStack() };
  // The document's own headings as a list of jumps, or null when it has fewer than three.
  function readingOutline(col) {
    const items = window.SLRender.outline(col);
    if (!items.length) return null;
    const top = Math.min(...items.map((x) => x.level));
    return h("details", { class: "outline", open: mqlWide.matches },
      h("summary", null, "Contents"),
      h("ol", null, items.map((x) => h("li", { class: `lvl-${Math.min(3, x.level - top)}` },
        h("button", { class: "link", type: "button", onclick: (e) => window.SLRender.jumpTo(e.currentTarget, x.id) }, x.text)))));
  }
  // push: opened from a link in the guide being read (Back returns to it); anchor: a heading id to scroll to.
  function openReading(doc, { kind = "source", highlight = false, opener = null, category = "", guide = null, push = false, anchor = null, scroll = null } = {}) {
    // push: true from a guide link (the guide being read goes on the Back stack), "back" from Back (the stack stays).
    if (push === true && !$("reading").hidden && R.doc) R.stack.push({ doc: R.doc, kind: R.kind, guide: R.guide, category: R.category, highlight: R.highlight, scroll: $("reading-scroll").scrollTop });
    else if (push !== true && push !== "back") R.stack.reset();
    Object.assign(R, { doc, kind, highlight, category, swipe: null, guide: kind === "guide" ? guide : null });
    if (opener) R.opener = opener;
    const col = $("reading-col");
    $("reading-another").hidden = kind !== "read";
    $("reading-print").hidden = !doc || window.SLRender.isEmptyDoc(doc);  // a guide or a library document
    $("reading-booklet").hidden = kind !== "guide" || $("reading-print").hidden;
    $("reading-ask").hidden = !doc || window.SLRender.isEmptyDoc(doc);
    if (!doc || window.SLRender.isEmptyDoc(doc)) {  // gone from the library: say so, not a blank page
      $("reading-src").textContent = doc ? str(doc.source) : "";
      fill(col, h("h1", null, str(doc && doc.title) || "Not found"), h("p", { class: "muted missing-doc" }, window.SLRender.MISSING_DOC));
    } else {
      const [els, target] = partsView(doc, docParts(doc), highlight, kind === "guide" ? guide : null);
      $("reading-src").textContent = `${str(doc.source)}${doc.official ? " · Official" : ""}`;
      const [crumb, more] = kind === "guide" ? [null, null] : articlePlace(doc, (docId) => openRelated(docId));
      const title = h("h1", null, str(doc.title) || "Untitled");
      fill(col, crumb, title, ...els, kind === "guide" ? readNext(guide) : null, more, addrLine(doc));
      const nav = readingOutline(col);
      if (nav) col.insertBefore(nav, title.nextSibling);
      R.target = (anchor && /^[\p{L}\p{N}-]+$/u.test(anchor) && col.querySelector(`[id="${anchor}"]`)) || target;
    }
    R.scroll = scroll;
    showReading();
  }
  // A title from "More in this category", opened in reading mode; Back returns to the article it was listed under.
  async function openRelated(docId) {
    try { openReading(await getDoc(docId), { kind: "source", push: true }); } catch (err) { toast(articleError(err)); }
  }
  // Back: to the guide the reader came from by a link, else out of reading mode.
  function readingBack() {
    const prev = R.stack.back();
    if (!prev) { closeReading(); return; }
    openReading(prev.doc, { kind: prev.kind, guide: prev.guide, category: prev.category, highlight: prev.highlight, scroll: prev.scroll, push: "back" });
  }
  function showReading() {
    const target = R.target;
    const scroll = R.scroll;
    R.target = null;
    R.scroll = null;
    $("reading").hidden = false;
    $("main").inert = true;
    $("sidebar").inert = true;
    panel.inert = true;
    const sc = $("reading-scroll");
    sc.scrollTop = 0;
    if (target) sc.scrollTop = Math.max(0, target.offsetTop - 120);
    else if (typeof scroll === "number") sc.scrollTop = scroll;
    $("reading-back").focus();
  }
  function closeReading(restoreFocus = true) {
    if ($("reading").hidden) return;
    $("reading").hidden = true;
    $("main").inert = false;
    panel.inert = false;
    applySidebar();
    if (restoreFocus && R.opener && document.contains(R.opener)) R.opener.focus();
    R.opener = null;
    R.stack.reset();
  }
  $("reading-back").addEventListener("click", () => readingBack());
  // How to print and fold a booklet, shown where booklets are made (the server sends the same words with each one).
  const BOOKLET_HOW = "Print it double-sided and choose “flip on short edge”. Keep the sheets in the order they come out, with page 1 facing you, fold the whole stack in half and staple along the fold: the pages then read 1, 2, 3 in order.";
  $("reading-print").addEventListener("click", () => printReading());
  $("reading-booklet").addEventListener("click", (e) => { if (R.guide) makeBooklets(e.currentTarget, R.guide); });
  $("reading-booklet").title = `A fold-up booklet: A5 pages, two to each A4 sheet. ${BOOKLET_HOW}`;
  // Print: the server renders the page as numbered A5 pages (footer "Starholm", the number in the outside corner)
  // and opens them in Preview, where the person prints (the window's own print panel can't number pages). The same
  // path prints a guide, a library document, the emergency pack and the family plan. In a browser (501) fallback()
  // runs instead: the browser's own print panel where there is a page to print.
  async function printVia(btn, run, fallback, lock = null) {
    if (btn && btn.dataset.printing === "1") return;
    const label = btn && btn.lastChild && btn.lastChild.nodeType === 3 ? btn.lastChild : null;
    const was = label ? label.data : "";
    if (btn) { btn.dataset.printing = "1"; btn.disabled = true; }
    if (label) label.data = "Preparing…";
    try {
      await run();
      toast("The numbered pages are open in Preview. Print them from there.");
    } catch (err) {
      if (err.status === 501) fallback();
      else if (err.status === 423) showLocked(lock);
      else toast(err.message);
    } finally {
      if (btn) { delete btn.dataset.printing; btn.disabled = false; }
      if (label) label.data = was;
    }
  }
  // The pack and the plan are put together by the server's print page, so in a browser there is nothing to print.
  const appOnly = () => toast("This prints only in Starholm's own window.");
  // A library document's passage id to print it by: the one it was opened at, else its first.
  function docPassage(doc) {
    const ok = (v) => Number.isSafeInteger(Number(v)) && Number(v) > 0;
    if (doc && ok(doc.focus)) return Number(doc.focus);
    const first = doc && Array.isArray(doc.parts) ? doc.parts.find((p) => p && ok(p.id)) : null;
    return first ? Number(first.id) : null;
  }
  function printGuide() {
    if (!R.guide) return;
    printVia($("reading-print"), () => API.printGuide(R.guide), () => window.print());
  }
  // A library document: from reading mode (btn: its Print button) or the side panel, where the browser fallback
  // opens it in reading mode first so the browser prints just its text.
  function printDocument(doc, btn, fromPanel = false) {
    const id = docPassage(doc);
    if (!id) { toast("This document can't be printed."); return; }
    printVia(btn, () => API.printDocument(id), () => {
      if (fromPanel) openReading(doc, { opener: btn });
      window.print();
    });
  }
  function printReading() {
    if ($("reading-print").hidden) return;
    if (R.kind === "guide") printGuide();
    else printDocument(R.doc, $("reading-print"));
  }
  const printNumbers = (btn) => printVia(btn, () => API.printNumbers(), appOnly);  // free: no lock
  const printPack = (btn) => printVia(btn, () => API.printPack(), appOnly, "pack");
  const printPlan = (btn) => printVia(btn, () => API.printPlan(), appOnly, "plan");
  // A guide's booklet (slug), or every guide's (no slug): the server makes the PDFs and opens them in Preview, or
  // their folder in Finder. Making one takes a few seconds; the button says so meanwhile.
  async function makeBooklets(btn, slug = null) {
    if (btn.disabled) return;
    const label = btn.lastChild && btn.lastChild.nodeType === 3 ? btn.lastChild : null;
    const was = label ? label.data : "";
    btn.disabled = true;
    if (label) label.data = slug ? "Making…" : "Saving booklets…";
    try {
      const r = slug ? await API.booklet(slug) : await API.allBooklets();
      toast(`${slug ? "The booklet is open in Preview." : `${r.count} booklets are in the folder now open in Finder.`} ${str(r.note)}`);
    } catch (err) { if (err.status === 423) showLocked("booklets"); else toast(err.message); } finally {
      btn.disabled = false;
      if (label) label.data = was;
    }
  }
  // A two-finger swipe the way Safari goes back (window.SLRender.SWIPE.BACK_SIGN) does what Back does, unless a
  // table or code block under the pointer can still scroll that way, the gesture is mostly a vertical scroll, or text
  // in reading mode is selected or being selected.
  $("reading").addEventListener("wheel", (e) => {
    if ($("reading").hidden || e.ctrlKey) return;  // ctrlKey: a pinch to zoom
    const scrollers = [];
    for (let n = e.target instanceof Element ? e.target : null; n && n !== e.currentTarget; n = n.parentElement) {
      if (n.scrollWidth > n.clientWidth + 1 && /^(auto|scroll)$/.test(getComputedStyle(n).overflowX)) {
        scrollers.push({ left: n.scrollLeft, width: n.scrollWidth, client: n.clientWidth });
      }
    }
    const unit = e.deltaMode === 1 ? 16 : e.deltaMode === 2 ? 400 : 1;  // lines or pages, as pixels
    const sel = window.getSelection();
    const selecting = (e.buttons & 1) === 1 || !!(sel && !sel.isCollapsed && sel.anchorNode && e.currentTarget.contains(sel.anchorNode));
    const r = window.SLRender.swipeStep(R.swipe, { dx: e.deltaX * unit, dy: e.deltaY * unit, t: e.timeStamp, scrollers, selecting });
    R.swipe = r.state;
    if (r.back) readingBack();
  }, { passive: true });
  $("reading-ask").addEventListener("click", () => askAbout(str(R.doc && R.doc.title)));
  $("reading-another").addEventListener("click", () => randomRead(R.category));

  // ---------- something to read ----------
  const CATEGORIES = [["anything", "Anything"], ["history", "History"], ["science", "Science"], ["places", "Places"], ["people", "People"], ["nature", "Nature"]];
  async function openReadItem(item, category, opener) {
    const from = opener || ($("reading").hidden ? document.activeElement : null);
    try {
      const doc = await getArticle(item.passage_id);
      openReading(doc, { kind: "read", category, opener: from });
    } catch (err) {
      if (err.status === 404 && S.status && S.status.library) openReading({ title: str(item.title), source: "", parts: [] }, { kind: "read", category, opener: from });
      else toast(articleError(err));
    }
  }
  async function randomRead(category = "anything") {
    try {
      const [item] = await API.reads.random(category, 1);
      if (!item) throw new API.ApiError("There's nothing to read in this library yet.", 404);
      await openReadItem(item, category);
    } catch (err) { toast(err.message); }
  }

  // ---------- the library page ----------
  // Sources by topic with filters, title search across the filtered sources, a source's own page (A to Z, paged),
  // and the "Something to read" rail. Titles open at their first passage in the side panel.
  const TOPIC_ORDER = [["medical", "Medical"], ["emergency", "Emergency and preparedness"], ["howto", "How-to"], ["repair", "Repair"], ["encyclopedia", "Encyclopedia and travel"], ["mine", "Your files"]];
  const KINDS = ["Wiki", "Q&A", "Web pages", "PDFs", "Guides"];
  const L = {
    built: false, sources: [], info: null, files: 0,
    topic: "all", official: false, updated: false, lic: "any", kind: "any",
    q: "", results: null, req: 0, timer: 0,
    source: null, sq: "", letter: null, titles: [], next: null, letters: [], sreq: 0, stimer: 0,
    cat: "anything", reads: null, rreq: 0,
    free: null, freeUi: { picking: false, selected: null }, freeTimer: 0, freeReq: 0,
  };
  // A licence's short name for the menu and the rows; the full text stays on the source's page.
  function licLabel(text) {
    const t = str(text);
    if (!t.trim()) return "Not stated";
    if (/^public domain/i.test(t) || /^u\.s\. government/i.test(t)) return "Public domain";
    if (/open government licence/i.test(t)) return "Open Government Licence";
    const cc = t.match(/\bCC (BY(?:-NC)?(?:-SA)?)/);
    if (cc) return `CC ${cc[1]}`;
    if (/non-commercial/i.test(t)) return "Non-commercial use";
    return t.split(/[,(]/)[0].trim().slice(0, 40);
  }
  const anyFilter = () => (L.topic !== "all" && L.topic !== "maps") || L.official || L.updated || L.lic !== "any" || L.kind !== "any";
  function filtered(ignoreTopic) {
    return L.sources.filter((s) => (ignoreTopic || L.topic === "all" || s.topic_key === L.topic)
      && (!L.official || s.official) && (!L.updated || s.updated)
      && (L.lic === "any" || licLabel(s.licence) === L.lic) && (L.kind === "any" || s.kind === L.kind));
  }
  // Your files count as a topic of their own, shown while no filter rules them out.
  const filesShown = (ignoreTopic) => L.files > 0 && !L.official && !L.updated && L.lic === "any" && L.kind === "any" && (ignoreTopic || L.topic === "all" || L.topic === "mine");

  function buildLibrary() {
    if (L.built) return;
    L.built = true;
    const search = h("input", { class: "search-input", type: "search", id: "lib-search", placeholder: "Find a title", "aria-label": "Find a title", maxlength: "300" });
    search.addEventListener("input", () => { L.q = search.value; clearTimeout(L.timer); L.timer = setTimeout(runTitleSearch, 250); renderLibMain(); });
    search.addEventListener("keydown", (e) => { if (e.key === "Enter") { e.preventDefault(); clearTimeout(L.timer); runTitleSearch(); } });
    const ssearch = h("input", { class: "search-input", type: "search", id: "src-search", placeholder: "Find a title in this source", "aria-label": "Find a title in this source", maxlength: "300" });
    ssearch.addEventListener("input", () => { L.sq = ssearch.value; clearTimeout(L.stimer); L.stimer = setTimeout(() => loadSourceTitles(true), 250); });
    $("view-library").replaceChildren(
      h("div", { id: "lib-main" },
        h("div", { id: "lib-free-top" }),
        h("h1", null, "Library"),
        h("p", { class: "facts", id: "lib-facts-line" }),
        h("div", { id: "lib-free" }),
        h("div", { id: "lib-content" },
          h("label", { class: "search" }, icon("search", "sm"), search),
          h("section", { class: "rail", id: "lib-rail", "aria-label": "Something to read" }),
          h("div", { class: "filters", id: "lib-topics", role: "group", "aria-label": "Topics" }),
          h("div", { class: "filters second", id: "lib-filters" }),
          h("div", { class: "lib-body", id: "lib-body", "aria-live": "polite" }))),
      h("div", { class: "src-detail", id: "lib-source", hidden: true },
        h("button", { class: "link backlink", type: "button", onclick: closeSource }, icon("back", "sm"), "All sources"),
        h("h1", { id: "src-name", tabindex: "-1" }),
        h("p", { class: "facts", id: "src-facts" }),
        h("label", { class: "search" }, icon("search", "sm"), ssearch),
        h("div", { class: "az", id: "src-az", role: "group", "aria-label": "Jump to a letter" }),
        h("div", { id: "src-titles", "aria-live": "polite" })));
    renderRail();
  }

  async function loadLibrary() {
    const [src, info, files, free] = await Promise.allSettled([API.library.sources(), API.library.info(), API.myFiles(false),
      API.freeLibrary.status()]);
    if (free.status === "fulfilled") L.free = free.value;
    if (src.status === "fulfilled") L.sources = Array.isArray(src.value.sources) ? src.value.sources.filter((s) => s && /^[a-z0-9-]{1,60}$/.test(str(s.key))) : [];
    else toast(src.reason.message);
    L.info = info.status === "fulfilled" ? info.value : null;
    L.files = files.status === "fulfilled" && Array.isArray(files.value.items) ? files.value.items.length : 0;
    renderLibMain();
    if (!L.reads) loadReads();
  }
  function renderLibraryFacts() {
    if (!L.built) return;
    const i = L.info;
    const bits = [];
    if (i && i.articles) bits.push(`${fmt(i.articles)} articles`);
    if (i && i.sources) bits.push(plural(Number(i.sources), "source"));
    if (i && i.size_bytes) bits.push(bytes(Number(i.size_bytes)));
    const built = i && typeof i.built === "string" ? Date.parse(i.built) / 1000 : NaN;
    if (Number.isFinite(built)) bits.push(`built ${longDate(built)}`);
    if (L.free && L.free.library_raw) bits.push("free library");
    $("lib-facts-line").textContent = i && !i.articles ? "There's no library on this computer yet. It is built from the terminal." : bits.join(" · ");
  }
  function libSwitch(label, key) {
    return h("button", { class: "toggle", type: "button", role: "switch", "aria-checked": String(L[key]),
      onclick: () => { L[key] = !L[key]; libFiltersChanged(); } }, h("span", { class: "switch" }), label);
  }
  function libSelect(label, key, values) {
    const sel = h("select", { class: "select", "aria-label": label, onchange: (e) => { L[key] = e.target.value; libFiltersChanged(); } },
      h("option", { value: "any" }, `${label}: any`), values.map((v) => h("option", { value: v }, v)));
    sel.value = L[key];
    return sel;
  }
  function libFiltersChanged() { renderLibMain(); if (L.q.trim()) runTitleSearch(); }
  function clearLibFilters() { Object.assign(L, { topic: "all", official: false, updated: false, lic: "any", kind: "any" }); libFiltersChanged(); }

  // ---------- the free library (free-library.js): the cards, the source picker, the build's progress ----------
  const FL = window.SLFreeLibrary;
  const upgradeTab = () => (tabs.some((t) => t.dataset.pane === "upgrade") ? "upgrade" : "status");
  const freeOn = {
    customise: () => { L.freeUi.picking = !L.freeUi.picking; renderFree(); },
    cancelPick: () => { L.freeUi = { picking: false, selected: null }; renderFree(); },
    toggle: (key) => {
      const sel = new Set(FL.chosen(L.free, L.freeUi));
      if (sel.has(key)) sel.delete(key); else sel.add(key);
      L.freeUi.selected = sel;
      renderFree();
    },
    selectNone: () => { L.freeUi.selected = new Set(); renderFree(); },
    selectAll: () => { L.freeUi.selected = null; renderFree(); },
    download: async (keys) => {
      const set = new Set(keys);
      const total = (L.free.sources || []).filter((s) => set.has(s.key)).reduce((a, s) => a + (Number(s.size) || 0), 0);
      const ok = await confirmDialog("Download the free library?", [
        `About ${FL.size(total)} from Kiwix and government sites, ${plural(keys.length, "source")}. Each file comes from the site that publishes it.`,
        "It can take several hours, then the library is built on this computer. You can pause it, or quit Starholm and carry on later."],
      "Download");
      if (ok) freeAct("start", { keys });
    },
    pause: () => freeAct("pause"),
    resume: () => freeAct("resume"),
    cancel: async () => {
      const ok = await confirmDialog("Cancel the free library?", ["What was downloaded so far is deleted. The library you have now stays as it is."], "Cancel the download");
      if (ok) freeAct("cancel");
    },
    retry: (key) => freeAct("retry", { key }),
    leaveOut: (key) => freeAct("leave-out", { key }),
    hideBanner: () => freeAct("hide-banner"),
    openSettings: () => openSettings(upgradeTab()),
    buyUpgrade: () => buyUpgrade(),
  };
  async function freeAct(action, body = {}) {
    try {
      L.free = await API.freeLibrary.act(action, body);
      if (action === "start" || action === "cancel") L.freeUi = { picking: false, selected: null };
    } catch (err) {
      toast(err.message);
    }
    renderLibMain();
  }
  async function pollFree() {
    const req = ++L.freeReq;
    try {
      const next = await API.freeLibrary.status();
      if (req !== L.freeReq) return;
      const was = L.free && L.free.job ? L.free.job.phase : null;
      L.free = next;
      if (was && was !== "done" && next.job && next.job.phase === "done") return loadLibrary();  // the new library is in
      renderLibMain();
    } catch { /* the next look tries again */ }
  }
  function renderFree() {
    clearTimeout(L.freeTimer);
    if (!L.built || !FL || !L.free) return;
    const v = FL.view(L.free, L.freeUi, { h, icon, on: freeOn });
    fill($("lib-free-top"), v.top);
    fill($("lib-free"), v.body);
    FL.sizeBars($("lib-free"));
    $("lib-content").hidden = v.hideLibrary;
    if (v.facts) $("lib-facts-line").textContent = v.facts;
    const job = L.free.job;
    if (v.state === "building" && job && ["downloading", "building"].includes(job.phase) && S.view === "library") {
      L.freeTimer = setTimeout(pollFree, 1500);
    }
  }

  function renderLibMain() {
    if (!L.built) return;
    renderLibraryFacts();
    renderFree();
    const searching = !!L.q.trim();
    $("lib-rail").hidden = searching;
    // topics: dimmed when no source is left under the other filters
    const pool = filtered(true);
    $("lib-topics").replaceChildren(
      h("button", { class: `fchip${L.topic === "all" ? " on" : ""}`, type: "button", "aria-pressed": String(L.topic === "all"), onclick: () => { L.topic = "all"; libFiltersChanged(); } }, "All"),
      ...TOPIC_ORDER.map(([key, label]) => {
        const has = key === "mine" ? filesShown(true) : pool.some((s) => s.topic_key === key);
        return h("button", { class: `fchip${L.topic === key ? " on" : ""}`, type: "button", "aria-pressed": String(L.topic === key), disabled: !has && L.topic !== key,
          onclick: () => { L.topic = key; libFiltersChanged(); } }, label);
      }),
      window.SLMaps ? h("button", { class: `fchip${L.topic === "maps" ? " on" : ""}`, type: "button", "aria-pressed": String(L.topic === "maps"),
        onclick: () => { L.topic = "maps"; libFiltersChanged(); } }, "Maps") : null);
    // Maps: the maps on offer (maps.js) in place of the sources, with no source filters
    $("lib-filters").hidden = L.topic === "maps";
    if (L.topic === "maps") {
      $("lib-rail").hidden = true;
      if (!searching) window.SLMaps.renderList($("lib-body"));
      return;
    }
    const lics = [...new Set(L.sources.map((s) => licLabel(s.licence)))].sort();
    const kinds = KINDS.filter((k) => L.sources.some((s) => s.kind === k));
    fill($("lib-filters"), libSwitch("Official only", "official"), libSwitch("Updated last refresh", "updated"),
      libSelect("Licence", "lic", lics), libSelect("Kind", "kind", kinds),
      anyFilter() ? h("button", { class: "link", type: "button", onclick: clearLibFilters }, "Clear") : null);
    if (!searching) renderSourceList();
  }
  function sourceMeta(s, full = false) {
    return [plural(Number(s.articles) || 0, "article"), str(s.kind), s.official ? "Official" : null,
      full && s.edition ? `edition ${str(s.edition)}` : null, full ? str(s.licence) || "Licence not stated" : licLabel(s.licence)].filter(Boolean).join(" · ");
  }
  function renderSourceList() {
    const box = $("lib-body");
    const list = filtered(false);
    const groups = TOPIC_ORDER.map(([key, label]) => {
      if (key === "mine") {
        if (!filesShown(false)) return null;
        return h("section", { class: "lib-group" }, h("h3", { class: "section-h" }, label),
          h("button", { class: "lib-row", type: "button", onclick: () => openSettings("files") },
            h("span", { class: "lib-name" }, "Your files"), h("span", { class: "lib-meta" }, `${plural(L.files, "file")} · manage them in Settings`)));
      }
      const rows = list.filter((s) => s.topic_key === key);
      if (!rows.length) return null;
      return h("section", { class: "lib-group" }, h("h3", { class: "section-h" }, label), rows.map((s) =>
        h("button", { class: "lib-row", type: "button", onclick: () => openSource(s.key) },
          h("span", { class: "lib-name" }, str(s.name), s.updated ? h("span", { class: "tag" }, "Updated") : null),
          h("span", { class: "lib-meta" }, sourceMeta(s)))));
    }).filter(Boolean);
    if (!groups.length) {
      box.replaceChildren(h("div", { class: "empty-note" }, L.sources.length ? "No sources match these filters. " : "There are no sources to show.",
        L.sources.length ? h("button", { class: "link", type: "button", onclick: clearLibFilters }, "Clear filters") : null));
      return;
    }
    box.replaceChildren(...groups);
  }

  // Title search across the filtered sources (titles=1); Your files are searched by passage.
  async function runTitleSearch() {
    const q = L.q.trim();
    const req = ++L.req;
    if (!q) { renderLibMain(); return; }
    const box = $("lib-body");
    box.replaceChildren(h("p", { class: "empty-note" }, "Searching…"));
    let cut = 0;
    try {
      let rows;
      if (L.topic === "mine") {
        const data = await API.search(q);
        rows = (Array.isArray(data.results) ? data.results : []).filter((r) => r && isArticleId(r.id) && r.source === "Your files")
          .map((r) => ({ passage: Number(r.id), title: str(r.title), sub: "Your files" }));
      } else {
        // `source` only when the filters leave some sources out; the server takes at most 40 keys.
        const pass = filtered(false);
        const keys = pass.length === L.sources.length ? [] : pass.map((s) => s.key);
        cut = keys.length > 40 ? keys.length : 0;
        if (anyFilter() && !pass.length) rows = [];
        else {
          const data = await API.searchTitles(q, { sources: keys.slice(0, 40), official: L.official, limit: 100 });
          rows = (Array.isArray(data.results) ? data.results : []).filter((r) => r && isArticleId(r.doc_id))
            .map((r) => ({ doc: Number(r.doc_id), title: str(r.title), sub: `${str(r.source_name)}${r.official ? " · Official" : ""}` }));
        }
      }
      if (req !== L.req) return;
      const note = cut ? h("p", { class: "hint cut-note" }, `Searched the first 40 of the ${cut} sources that match the filters.`) : null;
      if (!rows.length) { fill(box, note, h("p", { class: "empty-note" }, `No titles match “${q}”.`)); return; }
      fill(box, note,
        h("div", { class: "results" }, rows.map((r) => h("button", { class: "lib-row", type: "button",
        onclick: (e) => { markOn(box, e.currentTarget); if (r.doc) openDoc(r.doc, e.currentTarget, { byDoc: true }); else openDoc(r.passage, e.currentTarget); } },
        h("span", { class: "lib-name" }, r.title || "Untitled"), h("span", { class: "lib-meta" }, r.sub)))));
    } catch (err) {
      if (req === L.req) box.replaceChildren(h("p", { class: "empty-note" }, err.message));
    }
  }
  function markOn(box, el) { for (const x of box.querySelectorAll(".lib-row, .az-title")) x.classList.toggle("on", x === el); }

  // A source's page: facts, its own title search, the A to Z bar and its titles, 100 at a time.
  async function openSource(key) {
    const s = L.sources.find((x) => x.key === key);
    if (!s) return;
    Object.assign(L, { source: s, sq: "", letter: null, titles: [], next: null, letters: [] });
    $("src-search").value = "";
    $("lib-main").hidden = true;
    $("lib-source").hidden = false;
    $("src-name").textContent = str(s.name);
    $("src-facts").textContent = sourceMeta(s, true);
    scrollEl.scrollTop = 0;
    $("src-name").focus();
    renderAz();
    API.library.letters(key).then((d) => { if (L.source === s) { L.letters = Array.isArray(d.letters) ? d.letters.map(str) : []; renderAz(); } })
      .catch((err) => toast(err.message));
    loadSourceTitles(true);
  }
  function closeSource() {
    L.source = null;
    $("lib-source").hidden = true;
    $("lib-main").hidden = false;
    renderLibMain();
  }
  function renderAz() {
    // "#" holds the titles under no letter A to Z (a digit, or a script with no A to Z equivalent); it comes first.
    $("src-az").replaceChildren(..."#ABCDEFGHIJKLMNOPQRSTUVWXYZ".split("").map((c) => {
      const on = L.letter === c;
      return h("button", { type: "button", class: on ? "on" : null, "aria-pressed": String(on), disabled: !L.letters.includes(c) || !!L.sq.trim(),
        "aria-label": c === "#" ? "Titles starting with a number or another script" : null,
        onclick: () => { L.letter = on ? null : c; renderAz(); loadSourceTitles(true); } }, c);
    }));
  }
  async function loadSourceTitles(fresh) {
    const s = L.source;
    if (!s) return;
    const req = ++L.sreq;
    const q = L.sq.trim();
    if (fresh) { L.titles = []; L.next = null; renderAz(); $("src-titles").replaceChildren(h("p", { class: "empty-note" }, q ? "Searching…" : "Opening…")); }
    try {
      if (q) {
        const data = await API.searchTitles(q, { sources: [s.key], limit: 200 });
        if (req !== L.sreq) return;
        L.titles = (Array.isArray(data.results) ? data.results : []).filter((r) => r && isArticleId(r.doc_id)).map((r) => ({ doc_id: Number(r.doc_id), title: str(r.title) }));
        L.next = null;
      } else {
        const after = fresh ? (L.letter ? { after: L.letter } : {}) : L.next || {};
        const data = await API.library.browse(s.key, after, 100);
        if (req !== L.sreq) return;
        const page = (Array.isArray(data.titles) ? data.titles : []).filter((t) => t && isArticleId(t.doc_id)).map((t) => ({ doc_id: Number(t.doc_id), title: str(t.title), letter: str(t.letter) }));
        L.titles = fresh ? page : L.titles.concat(page);
        L.next = data.next && typeof data.next === "object" && typeof data.next.after === "string" ? { after: data.next.after, after_id: Number(data.next.after_id) || null } : null;
      }
    } catch (err) {
      if (req === L.sreq) $("src-titles").replaceChildren(h("p", { class: "empty-note" }, err.message));
      return;
    }
    renderSourceTitles();
  }
  function renderSourceTitles() {
    const box = $("src-titles");
    if (!L.titles.length) { box.replaceChildren(h("p", { class: "empty-note" }, L.sq.trim() ? `No titles in this source match “${L.sq.trim()}”.` : "This source has no titles.")); return; }
    const groups = new Map();
    for (const t of L.titles) {
      // The server's letter follows the Library order (wikiHow's "How to", leading quotes and accents ignored).
      const c = t.letter || (t.title.trim()[0] || "#").toUpperCase();
      const key = /^[A-Z]$/.test(c) ? c : "#";
      if (!groups.has(key)) groups.set(key, []);
      groups.get(key).push(t);
    }
    fill(box, ...[...groups].map(([letter, list]) => h("div", { class: "az-group" },
      h("div", { class: "az-letter", "aria-hidden": "true" }, letter),
      h("div", { class: "az-list" }, list.map((t) => h("button", { class: "az-title", type: "button",
        onclick: (e) => { markOn(box, e.currentTarget); openDoc(t.doc_id, e.currentTarget, { byDoc: true }); } }, t.title || "Untitled"))))),
      L.next ? h("button", { class: "btn more-btn", type: "button", onclick: (e) => { e.currentTarget.disabled = true; loadSourceTitles(false); } }, "Show more titles") : null);
    if (!L.next) box.querySelector(".more-btn")?.remove();
  }

  // The "Something to read" rail: book covers from general reading sources, by category.
  function renderRail() {
    const row = h("div", { class: "rail-row", id: "rail-row" });
    const scrollRow = (dir) => row.scrollBy({ left: dir * 540, behavior: "smooth" });
    $("lib-rail").replaceChildren(
      h("div", { class: "rail-head" },
        h("h3", { class: "rail-h" }, "Something to read"),
        h("nav", { class: "rail-tabs", "aria-label": "Categories" }, CATEGORIES.map(([key, label], i) => [
          i ? h("span", { class: "sep", "aria-hidden": "true" }, "/") : null,
          h("button", { type: "button", class: L.cat === key ? "on" : null, "aria-pressed": String(L.cat === key), onclick: () => { L.cat = key; renderRail(); loadReads(); } }, label)])),
        h("span", { class: "spacer" }),
        h("button", { class: "icon-btn", type: "button", "aria-label": "Shuffle", title: "Shuffle", onclick: loadReads }, icon("shuffle", "sm")),
        h("button", { class: "icon-btn", type: "button", "aria-label": "Scroll left", title: "Scroll left", onclick: () => scrollRow(-1) }, icon("chevL", "sm")),
        h("button", { class: "icon-btn", type: "button", "aria-label": "Scroll right", title: "Scroll right", onclick: () => scrollRow(1) }, icon("chevR", "sm"))),
      row);
    renderCovers();
  }
  function renderCovers() {
    const row = $("rail-row");
    if (!row) return;
    const label = Object.fromEntries(CATEGORIES);
    if (!L.reads) { row.replaceChildren(h("p", { class: "empty-note" }, "Finding something to read…")); return; }
    if (!L.reads.length) { row.replaceChildren(h("p", { class: "empty-note" }, "Nothing to read in this category yet.")); return; }
    // the five cover colours in turn, so no two covers side by side share one
    row.replaceChildren(...L.reads.map((r, i) => h("button", { class: `cover cv-${window.SLRender.readColour(i)}`, type: "button", title: r.title,
      onclick: (e) => openReadItem(r, L.cat, e.currentTarget) },
      window.SLRender.coverArt(window.SLRender.readCover(i)),
      h("span", { class: "cover-cat" }, label[r.category] || r.category || label[L.cat]),
      h("span", { class: "cover-title" }, r.title || "Untitled"),
      h("span", { class: "cover-src" }, r.source_name))));
  }
  async function loadReads() {
    const req = ++L.rreq;
    L.reads = null;
    renderCovers();
    try {
      const list = await API.reads.random(L.cat, 12);
      if (req === L.rreq) { L.reads = list; renderCovers(); }
    } catch (err) {
      if (req === L.rreq) { L.reads = []; renderCovers(); toast(err.message); }
    }
  }

  function showLibrary() {
    if (S.stream) S.stream.abort();
    closeNarrowSidebar();
    closeReading(false);
    if (answerPanel()) closePanel(false);
    buildLibrary();
    if (L.source) closeSource();
    setTitle("");
    setView("library");
    const loaded = loadLibrary();
    $("lib-search").focus();
    return loaded;
  }
  // The Library's "Find a title" with q in it, from a guided question's end point.
  async function searchLibrary(q) {
    const loaded = showLibrary();
    L.q = str(q);
    $("lib-search").value = L.q;
    try { await loaded; } catch { /* the search below says what went wrong */ }
    if (L.q === str(q)) runTitleSearch();
  }
  $("open-library").addEventListener("click", showLibrary);

  // ---------- offline maps (maps.js) ----------
  // The Maps chip in the Library lists the maps; Open shows one full size in its own view, Back returns to the list.
  async function backToMaps() {
    L.topic = "maps";
    await showLibrary();
  }
  if (window.SLMaps) {
    window.SLMaps.init({ h, icon, toast, confirm: confirmDialog,
      lockOn: { previewMap: (list) => showLock(UPG.lockedMap(list, lockCtx())), getUpgrade: () => buyUpgrade() },
      isShown: () => S.view === "library" && L.topic === "maps",
      openMap: (id) => {
        closeNarrowSidebar();
        setTitle("");
        setView("map");
        window.SLMaps.open(id, $("view-map"), backToMaps);
      } });
  }

  // ---------- guides ----------
  // Illustrated how-tos kept with the app (guides/ in the repo). They need no model and no library. The Guides page is
  // a shelf of covers, grouped as in guides/index.json; each cover's art is an SVG per theme (ops/covers.py).
  const G = { req: 0, list: null, slugs: new Set(), bySlug: new Map(), locked: false };
  const goodGuide = (g) => g && /^[a-z0-9-]{1,80}$/.test(str(g.slug));
  async function loadGuides() {
    const resp = await API.guides();
    const list = resp.guides;
    G.locked = resp.locked === true;  // no key: every guide but the free ones is marked locked
    G.list = Array.isArray(list) ? list.filter(goodGuide) : [];
    G.slugs = new Set(G.list.map((g) => g.slug));
    G.bySlug = new Map(G.list.map((g) => [g.slug, g]));
    return G.list;
  }
  // A guide's cover: its art, with its group and title over it (no figure, no reading time).
  function guideCover(g) {
    const cv = window.SLRender.coverClass(g.cover && g.cover.palette);
    return h("button", { class: g.locked ? `gcover ${cv} up-locked` : `gcover ${cv}`, type: "button", title: str(g.summary) || null,
      onclick: (e) => openGuide(g.slug, e.currentTarget) },
      window.SLRender.coverArt(g.slug),
      h("span", { class: "gcover-group" }, str(g.group)),
      h("span", { class: "gcover-title" }, str(g.title) || g.slug),
      g.locked ? h("span", { class: "up-lockchip" }, icon("lock", "sm"), UPG.LOCKED) : null);
  }
  async function showGuides() {
    if (S.stream) S.stream.abort();
    closeNarrowSidebar();
    closeReading(false);
    if (answerPanel()) closePanel(false);
    setTitle("");
    setView("guides");
    const wrap = $("view-guides");
    const req = ++G.req;
    const head = () => h("h1", { tabindex: "-1" }, "Guides");
    wrap.replaceChildren(head(), h("p", { class: "hint" }, "Opening…"));
    let list;
    try { list = await loadGuides(); } catch (err) {
      if (req === G.req) wrap.replaceChildren(head(), h("p", { class: "empty-note" }, err.message));
      return;
    }
    if (req !== G.req) return;
    if (!list.length) { wrap.replaceChildren(head(), h("p", { class: "empty-note" }, "There are no guides yet.")); return; }
    const groups = new Map();  // the server sends them by group, then order
    for (const g of list) {
      const key = str(g.group) || "Other";
      if (!groups.has(key)) groups.set(key, []);
      groups.get(key).push(g);
    }
    wrap.replaceChildren(head(), h("div", { class: "guides-top" },
      h("p", { class: "facts" }, "Step-by-step guides with pictures. They work without the AI."),
      h("div", { class: "guides-actions" },
        h("button", { class: G.locked ? "btn up-lockedbtn" : "btn", type: "button", title: "Your country's emergency numbers, key steps from the guides, and wallet cards to cut out",
          onclick: (e) => (G.locked ? showLocked("pack") : printPack(e.currentTarget)) }, icon(G.locked ? "lock" : "print", "sm"), "Print emergency pack"),
        h("button", { class: G.locked ? "btn up-lockedbtn" : "btn", type: "button", title: "Every guide as a fold-up booklet: A5 pages, two to each A4 sheet",
          onclick: (e) => (G.locked ? showLocked("booklets") : makeBooklets(e.currentTarget)) }, icon(G.locked ? "lock" : "doc", "sm"), "Save all booklets")),
      G.locked ? UPG.shelfNote(lockCtx()) : null,
      h("p", { class: "booklet-how" }, "The pack has your emergency numbers, key steps from the guides and wallet cards. Your ",
        h("button", { class: "link", type: "button", onclick: showPlan }, "family plan"), " fills in the meeting place and contacts."),
      h("p", { class: "booklet-how" }, h("strong", null, "How to print and fold a booklet: "), BOOKLET_HOW)),
      ...[...groups].map(([group, items]) => h("section", { class: "lib-group" }, h("h3", { class: "section-h" }, group),
        h("div", { class: "gshelf" }, items.map(guideCover)))));
    wrap.querySelector("h1").focus();
  }
  // opts: push (opened from a link in the guide being read, so Back returns there), anchor (a heading id to show).
  async function openGuide(slug, opener, { push = false, anchor = null } = {}) {
    try {
      if (!G.list) await loadGuides().catch(() => []);
      const g = await API.guide(slug);
      openReading({ title: str(g.title), source: "Guide", parts: [{ id: 0, text: window.SLRender.dropReadNext(str(g.markdown)) }] }, { kind: "guide", guide: slug, opener, push, anchor });
    } catch (err) {
      if (err.status === 423 && err.data && err.data.preview) showLock(UPG.lockedGuide(err.data.preview, lockCtx()));
      else if (err.status === 409 && err.data && err.data.download) showLock(UPG.downloadGuide(err.data.preview, lockCtx()));
      else toast(err.message);
    }
  }
  // A [text](guide:slug#heading) link, or a "Read next" entry, in the guide being read.
  function followGuide(slug, anchor) {
    if (!G.slugs.has(slug)) return;
    openGuide(slug, null, { push: true, anchor });
  }
  // "Read next": the guide's related guides from the index, as links that open them.
  function readNext(slug) {
    const entry = G.bySlug.get(slug);
    const items = (entry && Array.isArray(entry.related) ? entry.related : []).map((r) => G.bySlug.get(r)).filter(Boolean);
    if (!items.length) return null;
    return h("section", { class: "read-next", "aria-label": "Read next" }, h("h2", { class: "read-next-h" }, "Read next"),
      h("ul", null, items.map((g) => h("li", null,
        h("button", { class: "md-anchor guide-link", type: "button", dataset: { guide: g.slug }, onclick: () => followGuide(g.slug, null) }, str(g.title) || g.slug),
        g.summary ? h("span", { class: "read-next-sum" }, ` ${str(g.summary)}`) : null))));
  }
  $("open-guides").addEventListener("click", showGuides);

  // ---------- family plan ----------
  // The form lives in family-plan.js (window.SLPlan), self-contained; this only opens its page.
  async function showPlan() {
    if (S.stream) S.stream.abort();
    closeNarrowSidebar();
    closeReading(false);
    if (answerPanel()) closePanel(false);
    setTitle("");
    setView("plan");
    const st = await API.upgrade.status().catch(() => null);
    if (st && !st.unlocked) {  // no key: the form greyed out behind its card (upgrade.js)
      if (S.view === "plan") $("view-plan").replaceChildren(h("h1", { tabindex: "-1" }, "Family plan"), UPG.lockedPlan(window.SLPlan, lockCtx()));
      return;
    }
    window.SLPlan.mount($("view-plan"), { icon, toast, printPlan });
  }
  $("open-plan").addEventListener("click", showPlan);

  // ---------- settings ----------
  const settings = $("settings");
  const tabs = [...document.querySelectorAll("#settings-tabs [role=tab]")];
  function selectTab(name) {
    for (const t of tabs) {
      const on = t.dataset.pane === name;
      t.setAttribute("aria-selected", String(on));
      t.tabIndex = on ? 0 : -1;
      $(`pane-${t.dataset.pane}`).hidden = !on;
    }
    $("modal-body").scrollTop = 0;
    if (name !== "upgrade") clearTimeout(UP.timer);
    ({ emergency: loadEmergency, status: () => { renderStatusPage(); loadUpdates(); }, model: loadModelPage, answers: loadAnswers, memory: loadMemory, instructions: loadPrompt, files: () => refreshFiles(true), upgrade: loadUpgrade })[name]?.();
  }
  for (const t of tabs) t.addEventListener("click", () => selectTab(t.dataset.pane));
  $("settings-tabs").addEventListener("keydown", (e) => {
    const at = tabs.indexOf(document.activeElement);
    if (at < 0) return;
    let next = null;
    if (e.key === "ArrowDown" || e.key === "ArrowRight") next = tabs[(at + 1) % tabs.length];
    if (e.key === "ArrowUp" || e.key === "ArrowLeft") next = tabs[(at - 1 + tabs.length) % tabs.length];
    if (next) { e.preventDefault(); next.focus(); selectTab(next.dataset.pane); }
  });
  function openSettings(tab = "emergency") {
    closeNarrowSidebar();
    setSos(false);
    if (!settings.open) settings.showModal();
    selectTab(tab);
    tabs.find((t) => t.dataset.pane === tab)?.focus();
  }
  $("open-settings").addEventListener("click", () => openSettings());
  $("close-settings").addEventListener("click", () => settings.close());
  settings.addEventListener("click", (e) => { if (e.target === settings) settings.close(); });  // a click on the backdrop
  settings.addEventListener("close", () => { clearTimeout(filesTimer); clearTimeout(modelsTimer); clearTimeout(UP.timer); });

  // ---------- the upgrade (upgrade.js): the Upgrade page, and what locked features show ----------
  // The key is checked by the server on this computer; "Download now", Resume, Download again, Check for updates
  // and Update are the only buttons that make it contact the key service. Locked features open a preview in #lock-dialog.
  const UPG = window.SLUpgrade;
  const UP = { st: null, ui: { draft: "", error: "" }, timer: null, req: 0 };
  async function loadUpgrade() {
    const req = ++UP.req;
    clearTimeout(UP.timer);
    try {
      const st = await API.upgrade.status();
      if (req !== UP.req) return;
      UP.st = st;
    } catch (err) {
      if (req === UP.req) $("pane-upgrade").replaceChildren(h("h3", null, "Upgrade"), h("p", { class: "msg error" }, err.message));
      return;
    }
    renderUpgrade();
  }
  function renderUpgrade() {
    const pane = $("pane-upgrade");
    if (!UP.st) return;
    const done = UP.st.installed && UP.st.installed.library && Date.parse(str(UP.st.installed.library.installed));
    const v = UPG.view(UP.st, { ...UP.ui, guideCount: G.list ? G.list.length : 0, library: S.status && S.status.library,
      downloaded: Number.isFinite(done) ? longDate(done / 1000) : "" }, { h, icon, on: upOn });
    pane.replaceChildren(v.body);
    UPG.paint(pane);
    clearTimeout(UP.timer);
    if (v.state === "downloading" && settings.open && !pane.hidden) UP.timer = setTimeout(loadUpgrade, 1000);
  }
  async function upAct(action, body = {}) {
    try { UP.st = await API.upgrade.act(action, body); } catch (err) { toast(err.message); }
    renderUpgrade();
  }
  function unlockChanged() {  // a key saved or removed: the views that lock show it at once
    G.list = null;
    if (S.view === "guides") showGuides();
    else if (S.view === "plan") showPlan();
  }
  const openWeb = (url) => { if (url) API.openUrl(url).catch((err) => toast(err.message)); };
  const upOn = {
    save: async (text) => {
      UP.ui.draft = text;
      try {
        UP.st = await API.upgrade.act("key", { key: text });
        UP.ui = { draft: "", error: "" };
        unlockChanged();
      } catch (err) { UP.ui.error = err.message; }
      renderUpgrade();
      if (UP.ui.error) $("upgrade-key")?.focus();
    },
    remove: async () => {
      const ok = await confirmDialog("Remove the key from this computer?", [
        "The guides, booklets, maps and family plan lock again here. What was downloaded stays on this computer, and pasting the key again unlocks it."], "Remove key");
      if (!ok) return;
      await upAct("remove-key");
      unlockChanged();
    },
    download: () => upAct("download"),
    tryAgain: () => upAct("download"),
    resume: () => upAct("resume"),
    again: () => upAct("again"),
    pause: () => upAct("pause"),
    checkUpdates: async () => {  // the one Check for updates: Starholm itself and the packs, as on the Status page
      UP.ui.checking = true;
      renderUpgrade();
      await upAct("check-updates");
      UP.ui.checking = false;
      renderUpgrade();
      loadUpdates();  // a newer Starholm may be downloading now
      if (UP.st && UP.st.key && UP.st.key.verdict !== "valid") unlockChanged();  // "revoked": the paid features lock
    },
    update: () => upAct("update"),
    getUpgrade: () => buyUpgrade(),
    resend: () => openWeb(UP.st && UP.st.resend),
    writeToUs: () => API.upgrade.act("write-to-us").catch((err) => toast(err.message)),
    removeFree: async (free) => {
      const n = Number(free && free.builds) || 1;
      const ok = await confirmDialog(`Remove the free library (${UPG.size(free && free.bytes)})?`, [
        `The free library you built earlier is deleted from this computer${n > 1 ? ` (${n} builds)` : ""}. The ready-made library stays in use, and your own documents stay. This can't be undone.`], "Remove");
      if (!ok) return;
      await upAct("remove-free-library");
      if (UP.st && UP.st.note) toast(UP.st.note);
    },
  };
  const lockDialog = $("lock-dialog");
  function showLock(el) {
    $("lock-body").replaceChildren(el);
    if (!lockDialog.open) lockDialog.showModal();
    lockDialog.querySelector("[data-action=get-upgrade], [data-action=download]")?.focus();
  }
  function openUpgrade() {
    if (lockDialog.open) lockDialog.close();
    openSettings(upgradeTab());
  }
  // "Get the upgrade" opens the pricing page in the browser; a locked guide's "Download now" opens the Upgrade page.
  function buyUpgrade() {
    if (lockDialog.open) lockDialog.close();
    openWeb(UPG.BUY_URL);
  }
  const lockCtx = () => ({ h, icon, on: { notNow: () => lockDialog.close(), getUpgrade: buyUpgrade, openUpgrade } });
  function showLocked(kind) {
    if (kind === "pack") showLock(UPG.lockedPack(lockCtx()));
    else if (kind === "booklets") showLock(UPG.lockedBooklets(lockCtx()));
    else openUpgrade();
  }
  lockDialog.addEventListener("click", (e) => { if (e.target === lockDialog) lockDialog.close(); });  // a click on the backdrop

  // ---------- Starholm's own updates (updates.js): Settings › Status › Updates, the app menu, the ready notice ----------
  // GET /api/updates contacts nothing. Check for updates (here, on the Upgrade page and in the app menu) is the one
  // action that makes the server go online for updates, besides the automatic checks the person turns on.
  const UV = window.SLUpdateViews;
  const updCard = $("update-card");
  async function loadUpdates() {
    const req = ++UPD.req;
    UPD.loading = true;
    clearTimeout(UPD.timer);
    try {
      const st = await API.updates.status();
      if (req !== UPD.req) return;
      UPD.st = st;
    } catch (err) {
      if (req === UPD.req) $("updates-section").replaceChildren(h("p", { class: "msg error" }, err.message));
      return;
    } finally {
      if (req === UPD.req) UPD.loading = false;
    }
    renderUpdates();
  }
  function renderUpdates() {
    if (!UPD.st) return;
    $("updates-section").replaceChildren(...UV.rows(UPD.st, UPD.ui, { h, icon, on: updOn }));
    const app = UPD.st.app || {};
    clearTimeout(UPD.timer);
    if (["checking", "downloading", "unpacking"].includes(app.phase)) UPD.timer = setTimeout(loadUpdates, 1000);
    if (app.ready) showUpdateCard(app.ready);
    if (app.last_restart && !UPD.toldRestart) {
      UPD.toldRestart = true;
      toast(UV.restartResult(app.last_restart));
    }
  }
  async function checkUpdates() {
    UPD.ui.checking = true;
    renderUpdates();
    try { UPD.st = await API.updates.act("check"); } catch (err) { toast(err.message); }
    UPD.ui.checking = false;
    renderUpdates();
    if (UP.st && settings.open && !$("pane-upgrade").hidden) loadUpgrade();
    if (UPD.st && UPD.st.packs && UPD.st.packs.code === "revoked") unlockChanged();  // the paid features lock
  }
  const updOn = {
    check: checkUpdates,
    auto: async (on) => {
      try { UPD.st = await API.updates.act("auto", { on }); } catch (err) { toast(err.message); }
      renderUpdates();
    },
    restart: () => {  // Restart now on Settings › Status: close Settings and bring up the card, its button focused
      const r = UPD.st && UPD.st.app && UPD.st.app.ready;
      if (!r) return;
      if (settings.open) settings.close();
      showUpdateCard(r, true);
      updCard.querySelector("[data-action=restart]")?.focus();
    },
    upgrade: () => openSettings(upgradeTab()),
  };
  // The ready notice: a small card in the left panel above Settings, shown once per version and run whenever an update
  // is downloaded and checked (by the automatic schedule or by Check for updates), never over a question being confirmed
  // or a lock preview (the next status check shows it). It doesn't take the focus from what the person is doing: a
  // screen reader hears it through #update-live, and Tab reaches its buttons. Esc inside it counts as Later, which
  // hides it until the next start or the next new version. The same card shows Restarting… and the manual way.
  function hideUpdateCard() {
    const inside = updCard.contains(document.activeElement);
    updCard.hidden = true;
    $("update-body").replaceChildren();
    if (inside) $("input")?.focus();
  }
  function showUpdateCard(ready, force = false) {
    if (!ready) return;
    if (!force && (ready.dismissed || UPD.shown === ready.version)) return;
    if (!force && document.querySelector("#confirm-dialog[open], #lock-dialog[open]")) return;
    UPD.shown = ready.version;
    const later = async () => {
      hideUpdateCard();
      try { UPD.st = await API.updates.act("later"); } catch { /* shown again at the next start */ }
      renderUpdates();
    };
    const restart = async () => {
      let out;
      if (ready.install === "replace") $("update-body").replaceChildren(UV.restarting(ready.version, { h, icon }));  // the server checks the app again first
      try { out = await API.updates.restart(); } catch (err) { hideUpdateCard(); toast(err.message); return; }
      if (out.mode === "replace") return;  // the window closes; the new version opens
      if (out.mode === "manual") {
        $("update-body").replaceChildren(UV.manual(out, { h, icon, on: { close: hideUpdateCard } }));
        updCard.querySelector("[data-action=close]")?.focus();
      } else hideUpdateCard();
    };
    $("update-body").replaceChildren(UV.notice(ready, {}, { h, icon, on: { later, restart } }));
    updCard.onkeydown = (e) => {
      if (e.key !== "Escape") return;
      e.preventDefault();
      e.stopPropagation();
      if (updCard.querySelector("[data-action=later]")) later(); else hideUpdateCard();
    };
    const wasHidden = updCard.hidden;
    updCard.hidden = false;
    placeUpdateCard();
    if (wasHidden) $("update-live").textContent = UV.announce(ready);
  }
  // The card is as wide as the side panel's buttons and sits just above Settings while the panel is open, so it reads
  // as part of the panel. With the panel closed (or slid away in a narrow window) it keeps that width at the bottom
  // left. It is fixed rather than inside the panel, so a closed panel never hides it.
  function placeUpdateCard() {
    if (updCard.hidden) return;
    const foot = document.querySelector(".side-foot").getBoundingClientRect();  // its height doesn't move as the panel slides
    const open = appEl.classList.contains("side-open");
    updCard.style.setProperty("--upd-bottom", `${open ? Math.max(12, Math.round(window.innerHeight - foot.top + 6)) : 12}px`);
  }
  window.addEventListener("resize", placeUpdateCard);
  new MutationObserver(placeUpdateCard).observe(appEl, { attributes: true, attributeFilter: ["class"] });
  // The Mac app menu's Check for Updates… (app/server.py check_from_menu() calls this in the page).
  window.SLUpdates = { fromMenu: () => { openSettings("status"); $("updates-section").scrollIntoView({ block: "nearest" }); checkUpdates(); } };
  window.SLSettings = { open: () => openSettings() };  // the Mac app menu's Settings… (app/server.py settings_from_menu)
  loadUpdates();

  // Status
  function renderStatusPage() {
    const s = S.status;
    const v = s ? s.view : null;
    // banner: a newer library waiting, or the model not running
    let banner = null;
    if (v && v.newer) banner = [`A newer library${v.newer.built ? ` (${shortDate(v.newer.built)})` : ""} is ready.`, "Restart now", restartNow];
    else if (s && s.ollama_checked !== false && !s.ollama && s.ollama_installed && !s.ollama_starting) banner = ["The model isn't running.", "Start it", startEngine];
    $("status-banner").hidden = !banner;
    if (banner) {
      $("status-banner-text").textContent = banner[0];
      $("status-banner-action").textContent = banner[1];
      $("status-banner-action").onclick = banner[2];
    }
    // model menu
    const sel = $("model-select");
    const models = s && Array.isArray(s.models) ? s.models.map(str) : [];
    const sig = `${models.join("|")}#${s ? str(s.model) : ""}`;
    if (sel.dataset.sig !== sig && document.activeElement !== sel) {
      sel.dataset.sig = sig;
      sel.replaceChildren(...models.map((m) => h("option", { value: m }, m)));
      const chosen = s && models.includes(str(s.model)) ? str(s.model) : "";
      if (!models.length) sel.append(h("option", { value: "" }, "No models installed"));
      else if (!chosen) sel.prepend(h("option", { value: "", disabled: true }, "Choose a model"));
      sel.value = chosen;
      sel.disabled = !models.length;
    }
    let hint = "Only installed models are listed.";
    if (!s) hint = S.statusError || "Can't reach Starholm.";
    else if (s.ollama_checked === false) hint = "Library only is on, so the model isn't checked or loaded.";
    else if (!s.ollama) hint = s.ollama_starting ? "Starting…" : s.ollama_installed ? "Stopped." : "Ollama isn't installed. Set it up in Settings › Model.";
    else if (s.model_installed) {
      const loaded = Array.isArray(s.loaded) && s.loaded.includes(s.model);
      const warming = Array.isArray(s.warming) && s.warming.includes(s.model);
      hint = `${loaded ? "Loaded and ready." : warming ? "Loading into memory…" : "Loads on the first question."} Only installed models are listed.`;
    } else hint = "The chosen model isn't installed. Pick one of these.";
    $("model-hint").textContent = hint;
    // library facts
    const facts = [];
    if (v && v.articles) facts.push(`${fmt(v.articles)} articles`);
    if (v && v.sources) facts.push(plural(v.sources, "source"));
    if (v && v.sizeBytes) facts.push(bytes(v.sizeBytes));
    $("lib-facts").textContent = s && !s.library ? "No library found" : facts.join(" · ") || "…";
    $("row-built").hidden = !(v && v.built);
    if (v && v.built) $("lib-built").textContent = longDate(v.built);
    $("lib-folder").textContent = s ? str(s.library_dir_shown || s.library_dir) : "";  // the home folder as ~
    $("library-default-row").hidden = !(s && s.library_dir_setting);
    // refresh
    const rf = v && v.refresh;
    // The weekly refresh exists only on the owner's copy, which has the job (ops/launchd/); the public app hides it.
    $("refresh-section").hidden = !(rf && rf.available);
    const RESULTS = { ok: "finished", finished: "finished", failed: "failed", error: "failed" };
    $("row-last").hidden = !rf;
    $("row-next").hidden = !rf;
    if (rf) {
      const result = rf.result ? RESULTS[rf.result.toLowerCase()] || rf.result.toLowerCase() : "";
      if (rf.finished) $("last-val").textContent = `${longDate(rf.finished)}${result ? ` · ${result}` : ""}`;
      else $("last-val").textContent = rf.running ? "Running now" : "Unknown";
      $("last-hint").textContent = !rf.known ? "No refresh has reported yet." : rf.changed.length ? `${rf.changed.join(", ")} updated` : "No source changed.";
      $("next-val").textContent = rf.next ? longDate(rf.next) : rf.scheduled ? "Unknown" : "Not set up on this computer";
    }
    const running = !!(rf && rf.running);
    const unscheduled = !!(rf && !rf.scheduled);
    $("refresh-now").disabled = running || unscheduled;
    $("refresh-label").textContent = running ? "Refreshing" : "Refresh now";
    $("refresh-swap").dataset.alt = running ? "Refresh now" : "Refreshing";  // app.css .swap: one width for both
    $("refresh-hint").textContent = running
      ? `Running${rf.started ? ` since ${clock(rf.started)}` : ""}.${rf.progress ? ` ${rf.progress}` : ""}`
      : unscheduled ? "The weekly refresh isn't installed on this computer." : "Runs the weekly update now.";
    const ic = $("refresh-now").querySelector("[data-icon]");
    if (running) ic.replaceChildren(h("span", { class: "spinner" }));
    else if (!ic.querySelector("svg")) setIcon(ic, "retry");
  }
  async function startEngine() {
    try {
      const out = await API.startOllama();
      if (out.installed === false) toast("Ollama isn't installed yet. Press Set up the AI in Settings › Model.");
    } catch (err) { toast(err.message); }
    statusPoll.tick();
  }
  // Restart now: the app reopens on the newer library. Outside the app bundle the server answers 501; then it quits.
  async function restartNow() {
    const ok = await confirmDialog("Restart Starholm?", ["It closes and opens again on the newer library. Questions being answered stop."], "Restart now");
    if (!ok) return;
    try { await API.restart(); toast("Starholm is restarting…"); }
    catch (err) {
      if (err.status !== 501) { toast(err.message); return; }
      try { await API.quit(); toast("Starholm has quit. Open it again to use the newer library."); }
      catch (e2) { toast(e2.message); }
    }
  }
  $("model-select").addEventListener("change", async (e) => {
    const model = e.target.value;
    if (!model) return;
    setMsg("model-msg", "Saving…");
    try {
      const out = await API.setModel(model);
      setMsg("model-msg", `Answers now come from ${str(out.model || model)}.`);
      warmed = "";
      statusPoll.tick();
    } catch (err) { setMsg("model-msg", err.message, true); }
  });
  $("refresh-now").addEventListener("click", async () => {
    const ok = await confirmDialog("Refresh the library now?", [
      "This asks the weekly background job to run now. The app itself makes no connection.",
      "It can start a long download, up to about 12 GB and often 2 to 4 hours."], "Start refresh");
    if (!ok) return;
    setMsg("refresh-msg", "");
    try { await API.refreshNow(); setMsg("refresh-msg", "The refresh has started. It runs in the background."); statusPoll.tick(); }
    catch (err) { setMsg("refresh-msg", err.message, true); }
  });
  $("quit-app").addEventListener("click", async () => {
    const ok = await confirmDialog("Quit Starholm?", ["The app stops. Open it again to keep asking questions."], "Quit");
    if (!ok) return;
    try { await API.quit(); setMsg("quit-msg", "Starholm has quit. You can close this window."); }
    catch (err) { setMsg("quit-msg", err.message, true); }
  });

  // Library folder: the folder picker, or a typed path where the picker isn't available (in a browser)
  async function pickFolder(start, typeRowId, msgId) {
    try {
      const out = await API.pickFolder(start);
      return out.path ? str(out.path) : null;
    } catch (err) {
      if (err.status === 501) { $(typeRowId).hidden = false; setMsg(msgId, "Type the full path of the folder instead."); }
      else setMsg(msgId, err.message, true);
      return null;
    }
  }
  async function saveLibrary(folder) {
    setMsg("library-msg", "Saving…");
    try {
      const out = await API.libraryFolder(folder);
      $("library-folder").value = "";
      setMsg("library-msg", out.restart_needed ? "Saved. Quit and reopen Starholm to use it." : "Saved.");
      statusPoll.tick();
    } catch (err) { setMsg("library-msg", err.message, true); }
  }
  $("library-pick").addEventListener("click", async () => {
    const path = await pickFolder(S.status && S.status.library_dir, "library-type", "library-msg");
    if (path) saveLibrary(path);
  });
  $("library-save").addEventListener("click", () => {
    const folder = $("library-folder").value.trim();
    if (!folder) { setMsg("library-msg", "Type the full path of a library folder.", true); return; }
    saveLibrary(folder);
  });
  $("library-default").addEventListener("click", () => saveLibrary(null));
  $("library-show").addEventListener("click", () => API.libraryOpen().catch((err) => setMsg("library-msg", err.message, true)));

  // Answers: Documents only, and for the tests the answer mode and thinking for the model in use
  let answerSettings = null;
  function renderAnswers() {
    const a = answerSettings;
    const model = S.status ? str(S.status.model) : "";
    $("docs-only").setAttribute("aria-checked", String(!!(a && a.documents_only)));
    $("answer-mode").value = a && ["standard", "tools-quoted", "fetch-quoted"].includes(a.mode) ? a.mode : "fetch-quoted";
    const off = a && Array.isArray(a.think_off) ? a.think_off.map(str) : [];
    $("thinking").setAttribute("aria-checked", String(!off.includes(model)));
    $("thinking").disabled = !model;
    $("thinking-hint").textContent = model ? `For ${model}.` : "No model chosen.";
  }
  async function loadAnswers() {
    setMsg("answers-msg", "");
    try { answerSettings = await API.answers.load(); } catch (err) { setMsg("answers-msg", err.message, true); }
    renderAnswers();
  }
  async function saveAnswers(changes, msgId = "answers-msg") {
    try {
      answerSettings = await API.answers.save(changes);
      if (S.status) S.status.answers = answerSettings;  // Library only takes effect at once (no warm-up, no model)
      setMsg(msgId, "Saved. It applies from the next question.");
      renderStatus();
      if ("documents_only" in changes) statusPoll.tick();  // the model is checked again, or no longer
    } catch (err) { setMsg(msgId, err.message, true); }
    renderAnswers();
  }
  // The Library only switch (settings.json answers.documents_only) is on the Model page.
  $("docs-only").addEventListener("click", (e) => saveAnswers({ documents_only: e.currentTarget.getAttribute("aria-checked") !== "true" }, "models-msg"));

  // Model: the recommended-model table by memory size (models.js), Ollama's state, and the Library only switch. Asked
  // again every second while a model downloads, and only while this page is open.
  let modelsTimer = 0;
  function loadModelPage() {
    setMsg("models-msg", "");
    loadAnswers();
    loadModels();
  }
  async function loadModels() {
    clearTimeout(modelsTimer);
    let data = null;
    try { data = await API.models.load(); }
    catch (err) { setMsg("models-msg", err.message, true); }
    renderModels(data);
    const wait = M.pollDelay(data);
    if (wait && settings.open && !$("pane-model").hidden) modelsTimer = setTimeout(loadModels, wait);
  }
  let lastModels = null;  // the table last drawn, for Remove's confirmation
  function renderModels(data) {
    lastModels = data;
    $("model-lead").textContent = M.lead(data);
    const o = data ? M.ollamaView(data.ollama) : { dot: "off", text: "…", action: null };
    $("ollama-dot").className = `dot${o.dot ? ` ${o.dot}` : ""}`;
    $("ollama-text").textContent = o.text;
    $("ollama-action").hidden = !o.action;
    $("ollama-action").textContent = o.label || "";
    fill($("ai-setup"), M.buildSetup(data, h, setupAction, (url) => API.openUrl(url).catch((err) => toast(err.message))));
    fill($("model-table"), data ? M.buildTable(data, h, modelAction) : null,
      M.helpLink(h, icon, (url) => API.openUrl(url).catch((err) => toast(err.message))));
  }
  async function modelAction(action, row) {
    const model = str(row.model);
    try {
      if (action === "use") {
        const out = await API.setModel(model);
        setMsg("models-msg", `Answers now come from ${str(out.model || model)}.`);
        warmed = "";
      } else if (action === "remove") {
        const c = M.removeConfirm(row, lastModels && lastModels.rows);
        if (!(await confirmDialog(c.title, c.lines, c.label))) return;
        const out = await API.models.remove(model);
        setMsg("models-msg", str(out.message));
        warmed = "";
      } else if (action === "pull" || action === "update") { await API.models.pull(model); setMsg("models-msg", ""); }
      else if (action === "cancel") { await API.models.cancel(model); setMsg("models-msg", "Download cancelled."); }
    } catch (err) { setMsg("models-msg", err.message, true); }
    statusPoll.tick();
    loadModels();
  }
  // "Set up the AI": start it (or Try again), or cancel it. The page asks again every second while it runs.
  async function setupAction(action) {
    try {
      if (action === "cancel") await API.setup.cancel();
      else { await API.setup.start(); setMsg("models-msg", ""); }
    } catch (err) { setMsg("models-msg", err.message, true); }
    statusPoll.tick();
    loadModels();
  }
  // Start Ollama, or look for it again after installing it (the same call finds it first).
  $("ollama-action").addEventListener("click", async () => { await startEngine(); loadModels(); });
  $("answer-mode").addEventListener("change", (e) => saveAnswers({ mode: e.target.value }));
  $("thinking").addEventListener("click", (e) => {
    const model = S.status ? str(S.status.model) : "";
    if (!model || !answerSettings) return;
    const on = e.currentTarget.getAttribute("aria-checked") !== "true";
    const off = new Set((answerSettings.think_off || []).map(str));
    if (on) off.delete(model); else off.add(model);
    saveAnswers({ think_off: [...off] });
  });

  // Memory
  let memoryBase = "";
  let memoryMax = 0;
  async function loadMemory() {
    setMsg("memory-msg", "");
    try {
      const m = await API.memory();
      memoryBase = str(m.text);
      memoryMax = Number(m.max_chars) || 0;
      $("memory-text").value = memoryBase;
      if (memoryMax) $("memory-text").maxLength = memoryMax;
      $("memory-ai").setAttribute("aria-checked", String(!!m.ai_writes));
      memoryCount();
    } catch (err) { setMsg("memory-msg", err.message, true); }
  }
  function memoryCount() {
    const text = $("memory-text").value;
    const notes = text.split("\n").filter((l) => l.trim()).length;
    const bits = [plural(notes, "note")];
    if (memoryMax) bits.push(`${fmt(text.length)} of ${fmt(memoryMax)} characters`);
    $("memory-count").textContent = bits.join(" · ");
  }
  $("memory-text").addEventListener("input", memoryCount);
  $("memory-save").addEventListener("click", async () => {
    setMsg("memory-msg", "Saving…");
    try {
      const out = await API.saveMemory($("memory-text").value, memoryBase);
      memoryBase = str(out.text);
      $("memory-text").value = memoryBase;
      memoryCount();
      const kept = Array.isArray(out.kept) ? out.kept.length : 0;
      const dropped = Array.isArray(out.dropped) ? out.dropped.length : 0;
      const extra = [];
      if (kept) extra.push(`${plural(kept, "note")} the AI added while you were editing ${kept === 1 ? "was" : "were"} kept.`);
      if (dropped) extra.push(`${plural(dropped, "note")} the AI removed while you were editing ${dropped === 1 ? "stays" : "stay"} removed.`);
      setMsg("memory-msg", ["Saved.", ...extra].join(" "));
    } catch (err) { setMsg("memory-msg", err.message, true); }
  });
  $("memory-ai").addEventListener("click", async (e) => {
    const btn = e.currentTarget;
    const on = btn.getAttribute("aria-checked") !== "true";
    btn.setAttribute("aria-checked", String(on));
    try {
      const out = await API.memoryAiWrites(on);
      btn.setAttribute("aria-checked", String(!!out.ai_writes));
    } catch (err) { btn.setAttribute("aria-checked", String(!on)); setMsg("memory-msg", err.message, true); }
  });

  // Instructions
  async function loadPrompt() {
    setMsg("prompt-msg", "");
    try {
      const p = await API.prompt();
      $("prompt-text").value = str(p.custom);
      $("prompt-default").textContent = str(p.default);
    } catch (err) { setMsg("prompt-msg", err.message, true); }
  }
  $("prompt-save").addEventListener("click", async () => {
    setMsg("prompt-msg", "Saving…");
    try {
      const out = await API.savePrompt($("prompt-text").value);
      $("prompt-text").value = str(out.custom);
      setMsg("prompt-msg", "Saved. New answers follow these instructions.");
    } catch (err) { setMsg("prompt-msg", err.message, true); }
  });
  $("prompt-reset").addEventListener("click", async () => {
    try {
      const out = await API.resetPrompt();
      $("prompt-text").value = str(out.custom);
      setMsg("prompt-msg", "Back to the built-in instructions only.");
    } catch (err) { setMsg("prompt-msg", err.message, true); }
  });

  // Your files
  const FILE_TYPES = [".pdf", ".docx", ".odt", ".epub", ".html", ".htm", ".txt", ".md", ".markdown", ".csv"];
  const readable = (name) => FILE_TYPES.some((t) => name.toLowerCase().endsWith(t));
  let myFiles = null;
  let filesTimer = 0;
  let filesChecks = 0;
  let adding = 0;
  let fileNotes = [];
  const fileAsks = [];
  function size(n) {
    n = Number(n);
    if (!Number.isFinite(n) || n < 0) return "";
    if (n >= 1e9) return `${(n / 1e9).toFixed(1)} GB`;
    if (n >= 1e6) return `${(n / 1e6).toFixed(1)} MB`;
    return `${Math.max(1, Math.round(n / 1e3))} KB`;
  }
  async function refreshFiles(check = false) {
    clearTimeout(filesTimer);
    try { myFiles = await API.myFiles(check); }
    catch (err) { setMsg("files-status", err.message, true); return; }
    if (check) filesChecks = 3;  // the check runs in the background, so look again a few times
    renderFiles();
    if (settings.open && !$("pane-files").hidden && (myFiles.busy || adding || filesChecks-- > 0)) filesTimer = setTimeout(refreshFiles, 1000);
  }
  function renderFiles() {
    const f = myFiles && Array.isArray(myFiles.items) ? myFiles : { items: [] };
    const ready = f.items.filter((it) => it && it.status === "ready").length;
    $("files-status").textContent = adding ? `Adding ${adding === 1 ? "a file" : `${adding} files`}…`
      : f.busy && f.doing ? `Reading ${str(f.doing)} (${(Number(f.done) || 0) + 1} of ${Number(f.total) || 1})… Big files take a minute or two.`
        : f.items.length ? `${plural(f.items.length, "file")} · ${fmt(ready)} ready` : "";
    $("files-notes").replaceChildren(...fileNotes.map((n) => h("p", { class: n.error ? "error" : null }, n.text)));
    $("files-ask").hidden = !fileAsks.length;
    if (fileAsks.length) $("files-ask-text").textContent = `You already have a file called “${fileAsks[0].name}”. Replace it with this one?`;
    $("files-folder-text").textContent = str(f.folder_shown || f.folder);  // the home folder as ~
    $("files-list").replaceChildren(...(f.items.length ? f.items.map((it) => {
      const name = str(it.name);
      const state = it.status === "ready" ? "Ready" : str(it.error) || "Starholm couldn't read this file.";
      const remove = h("button", { class: "icon-btn sm", type: "button", "aria-label": `Remove ${name}`, title: "Remove" }, icon("trash", "sm"));
      remove.addEventListener("click", () => removeFile(name, remove));
      return h("div", { class: "file-row" }, icon("doc", "sm"),
        h("div", { class: "file-main" },
          h("span", { class: "file-name", title: name }, str(it.title) || name),
          h("span", { class: "hint" }, [it.title && it.title !== name ? name : null, size(it.size), state].filter(Boolean).join(" · "))),
        remove);
    }) : [h("p", { class: "hint" }, "No files yet.")]));
  }
  async function sendFile(file, how = "") {
    adding++;
    renderFiles();
    try {
      const resp = await API.addFile(file, `name=${encodeURIComponent(file.name)}${how}`);
      let out = {};
      try { out = (await resp.json()) || {}; } catch { /* not JSON */ }
      if (resp.status === 409) fileAsks.push({ file, name: str(out.name) || file.name });
      else if (!resp.ok) fileNotes.push({ error: true, text: `${file.name}: ${API.errorText(resp.status, out)}` });
      else if (out.duplicate || out.unchanged) fileNotes.push({ text: `${str(out.name) || file.name} is already in your files.` });
      else if (out.replaced) fileNotes.push({ text: `Replaced ${str(out.replaced)} with the new version. The old one is in the Trash.` });
      else fileNotes.push({ text: `Added ${str(out.name) || file.name}.` });
    } catch (err) {
      fileNotes.push({ error: true, text: `${file.name}: ${err.message}` });
    } finally {
      adding--;
      renderFiles();
    }
  }
  async function addFiles(list) {
    fileNotes = [];
    const taken = new Set((myFiles?.items || []).map((it) => str(it.name)));
    for (const file of [...list]) {
      if (!readable(file.name)) fileNotes.push({ error: true, text: `${file.name}: Starholm can't read this kind of file. It reads PDF, Word, OpenDocument, EPUB, web pages and text files.` });
      else if (file.size > 200 * 1024 * 1024) fileNotes.push({ error: true, text: `${file.name}: it's over 200 MB, the most Starholm reads.` });
      else if (taken.has(file.name)) fileAsks.push({ file, name: file.name });
      else await sendFile(file);
    }
    renderFiles();
    refreshFiles(true);
  }
  async function answerAsk(how) {
    const q = fileAsks.shift();
    renderFiles();
    if (q && how) await sendFile(q.file, how === "replace" ? `&replace=${encodeURIComponent(q.name)}` : "&keep=1");
    refreshFiles(true);
  }
  $("files-ask-replace").addEventListener("click", () => answerAsk("replace"));
  $("files-ask-keep").addEventListener("click", () => answerAsk("keep"));
  $("files-ask-skip").addEventListener("click", () => answerAsk(null));
  async function removeFile(name, btn) {
    if (btn.dataset.armed !== "1") {  // two clicks, so a stray click doesn't remove a file
      btn.dataset.armed = "1";
      btn.classList.remove("icon-btn", "sm");
      btn.classList.add("btn");
      btn.textContent = "Move to Trash?";
      setTimeout(() => {
        if (!document.contains(btn) || btn.disabled) return;
        btn.dataset.armed = "";
        btn.classList.remove("btn");
        btn.classList.add("icon-btn", "sm");
        btn.replaceChildren(icon("trash", "sm"));
      }, 4000);
      return;
    }
    btn.disabled = true;
    try {
      await API.removeFile(name);
      fileNotes = [{ text: `Removed ${name}. It's in the Trash if you want it back.` }];
    } catch (err) { fileNotes = [{ error: true, text: err.message }]; }
    refreshFiles();
  }
  $("files-add").addEventListener("click", () => $("file-input").click());
  $("file-input").addEventListener("change", (e) => { const files = [...e.target.files]; e.target.value = ""; if (files.length) addFiles(files); });
  const dropzone = $("dropzone");
  dropzone.addEventListener("dragover", (e) => { e.preventDefault(); dropzone.classList.add("over"); });
  dropzone.addEventListener("dragleave", () => dropzone.classList.remove("over"));
  dropzone.addEventListener("drop", (e) => {
    e.preventDefault();
    dropzone.classList.remove("over");
    if (e.dataTransfer && e.dataTransfer.files.length) addFiles(e.dataTransfer.files);
  });
  window.addEventListener("dragover", (e) => e.preventDefault());  // a file dropped elsewhere must not navigate away
  window.addEventListener("drop", (e) => e.preventDefault());
  async function saveFilesFolder(folder) {
    setMsg("files-folder-msg", "Saving…");
    try {
      await API.filesFolder(folder);
      $("files-folder").value = "";
      setMsg("files-folder-msg", "Saved.");
      refreshFiles(true);
    } catch (err) { setMsg("files-folder-msg", err.message, true); }
  }
  $("files-pick").addEventListener("click", async () => {
    const path = await pickFolder(myFiles && myFiles.folder, "files-type", "files-folder-msg");
    if (path) saveFilesFolder(path);
  });
  $("files-folder-save").addEventListener("click", () => {
    const folder = $("files-folder").value.trim();
    if (!folder) { setMsg("files-folder-msg", "Type the full path of a folder.", true); return; }
    saveFilesFolder(folder);
  });
  $("files-show").addEventListener("click", () => API.openFiles().catch((err) => setMsg("files-folder-msg", err.message, true)));

  // ---------- wiring ----------
  $("new-chat").addEventListener("click", () => { newChat(); closeNarrowSidebar(); });
  $("new-chat-top").addEventListener("click", newChat);
  // Back from the mouse's back button: whichever Back is showing, the one on top first (render.js backTarget).
  function pressBack() {
    const target = window.SLRender.backTarget({ reading: !$("reading").hidden,
      panelBack: !panel.hidden && P.mode === "sources" && P.full, librarySource: S.view === "library" && !!L.source });
    if (target === "reading") readingBack();
    else if (target === "panel") { P.full = false; renderPanel(); }
    else if (target === "library") closeSource();
    return target !== null;
  }
  document.addEventListener("mouseup", (e) => { if (window.SLRender.isBackPress(e) && pressBack()) e.preventDefault(); });
  document.addEventListener("keydown", (e) => {
    const mod = e.metaKey || e.ctrlKey;
    // Cmd+P (Ctrl+P) prints what is being read, as its Print button does: a guide or a library document in reading
    // mode, a document open in the side panel, or the family plan; elsewhere the key is left alone.
    if (window.SLRender.isPrintKey(e)) {
      if (!$("reading").hidden) {
        if (!$("reading-print").hidden) { e.preventDefault(); printReading(); }
        return;
      }
      if (!panel.hidden && P.doc && P.full && !document.querySelector("dialog[open]")) { e.preventDefault(); printDocument(P.doc, $("panel-print"), true); return; }
      const planPrint = S.view === "plan" ? $("view-plan").querySelector(".plan-bar .btn:not(.primary)") : null;
      if (planPrint && !document.querySelector("dialog[open]")) { e.preventDefault(); planPrint.click(); return; }
    }
    if (window.SLRender.isBackPress(e) && pressBack()) { e.preventDefault(); return; }
    if (mod && !e.shiftKey && (e.key === "n" || e.key === "N")) { e.preventDefault(); newChat(); return; }
    if (mod && e.shiftKey && (e.key === "o" || e.key === "O")) { e.preventDefault(); newChat(); return; }
    if (mod && e.key === ".") { e.preventDefault(); toggleSidebar(); return; }
    if (mod && e.key === ",") { e.preventDefault(); openSettings(); return; }
    if (e.key !== "Escape" || document.querySelector("dialog[open]") || menuFor) return;
    if (!$("reading").hidden) closeReading();
    else if (!$("sos-pop").hidden) { setSos(false); $("sos-btn").focus(); }
    else if (!panel.hidden) closePanel();
    else if (!mqlWide.matches && S.sideNarrow) toggleSidebar(false);
  });

  (async () => {
    try { Object.assign(prefs, cleanPrefs(await API.prefs.load())); } catch (err) { toast(err.message); }
    applyPrefs();
  })();
  applyPrefs();
  setView("start");
  autosize();
  updateSend();
  statusPoll.tick();
  loadChats();
  loadEmergency();
  input.focus();
})();
