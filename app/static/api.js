// Starholm interface: every call to the server goes through this file.
//
// It wraps the endpoints in README.md's "API for the interface" and turns the server's shapes into the ones the
// interface uses. FEATURES lists endpoints the interface waits for; it is empty now that the server has them all.
(function (root) {
  "use strict";

  const FEATURES = {};

  const TOKEN = document.querySelector('meta[name="token"]')?.getAttribute("content") || "";
  const str = (v) => (typeof v === "string" ? v : v == null ? "" : String(v));
  const num = (v) => (Number.isFinite(Number(v)) && v !== null && v !== "" ? Number(v) : null);
  // An ISO 8601 time from the server ("2026-11-06T06:01:02Z", "2026-11-19T10:00+04:00") as Unix seconds, or null.
  const isoSeconds = (v) => {
    if (typeof v !== "string" || !v) return null;
    const t = Date.parse(v);
    return Number.isFinite(t) ? t / 1000 : null;
  };

  class ApiError extends Error {
    constructor(message, status, data) { super(message); this.status = status; this.data = data; }
  }
  function errorText(status, data) {
    if (data && typeof data.error === "string" && data.error.trim()) return data.error;
    switch (status) {
      case 0: return "Can't reach Starholm. Is the app still running?";
      case 403: return "This page is out of date. Reload it to reconnect.";
      case 404: return "That isn't there any more.";
      case 409: return "That conflicts with something that changed meanwhile. Try again.";
      case 411: case 413: return "That was too large to send.";
      case 503: return "Starholm is busy or not ready yet. Try again in a moment.";
      case 507: return "There isn't enough free disk space.";
      default: return `Something went wrong (error ${status}).`;
    }
  }

  let onStale = () => {};
  async function raw(path, { method = "GET", json, body, headers = {}, signal } = {}) {
    const opts = { method, headers: { "X-Token": TOKEN, ...headers }, signal, cache: "no-store", credentials: "same-origin" };
    if (json !== undefined) { opts.body = JSON.stringify(json); opts.headers["Content-Type"] = "application/json"; }
    else if (body !== undefined) opts.body = body;
    let resp;
    try { resp = await fetch(path, opts); }
    catch (err) {
      if (err && err.name === "AbortError") throw err;
      throw new ApiError(errorText(0), 0, null);
    }
    if (resp.status === 403) onStale();
    return resp;
  }
  async function call(path, opts) {
    const resp = await raw(path, opts);
    let data = null;
    try { data = await resp.json(); } catch { /* not JSON */ }
    if (!resp.ok) throw new ApiError(errorText(resp.status, data), resp.status, data);
    return data && typeof data === "object" ? data : {};
  }
  const post = (path, json = {}, extra = {}) => call(path, { method: "POST", json, ...extra });

  // ---------- status ----------
  // The raw /api/status, plus `view`: what the redesign shows, times as Unix seconds, null wherever the server
  // doesn't know. From README "Status and models":
  //   library: {articles, sources, size_bytes, built (ISO time or null)} or null
  //   refresh: {known, running, started, finished, result, changed: [names], progress, next, scheduled, available}
  //     available: this copy has the owner's weekly job (ops/launchd/); the public app has none, and hides the section
  //   newer_library: {folder, built} or null
  function statusView(s) {
    const lib = s.library && typeof s.library === "object" ? s.library : null;
    const rf = s.refresh && typeof s.refresh === "object" ? s.refresh : null;
    const newer = s.newer_library && typeof s.newer_library === "object" ? s.newer_library : null;
    return {
      articles: lib ? num(lib.articles) : null,
      sources: lib ? num(lib.sources) : null,
      sizeBytes: lib ? num(lib.size_bytes) : null,
      built: lib ? isoSeconds(lib.built) : null,
      refresh: rf ? {
        known: !!rf.known, running: !!rf.running, started: isoSeconds(rf.started), finished: isoSeconds(rf.finished),
        result: str(rf.result), changed: Array.isArray(rf.changed) ? rf.changed.map(str).filter(Boolean) : [],
        progress: str(rf.progress), next: isoSeconds(rf.next), scheduled: !!rf.scheduled,
        available: !!rf.available,
      } : null,
      newer: newer ? { built: isoSeconds(newer.built) } : null,
    };
  }
  async function status() {
    const s = await call("/api/status");
    s.view = statusView(s);
    return s;
  }

  // ---------- emergency numbers ----------
  // The server's shape (README "Emergency numbers"): {choice: code or null, detected: {code, name, detected_by,
  // has_card} or null, countries: [{code, name}], country: {code, name} or null, current: card or null, fallback}, a
  // card being {code, name, checked, numbers: [{label, short, number, when, wallet}], ambulance, police, sources,
  // notices}. Turned here into what the interface shows: the same fields, cleaned; current is null when there is no
  // card (the page then says to use your local emergency number and shows no number).
  const CODE = /^[A-Z]{2}$/;
  function cleanCard(c) {
    if (!c || typeof c !== "object" || !CODE.test(str(c.code))) return null;
    const numbers = (Array.isArray(c.numbers) ? c.numbers : [])
      .filter((n) => n && typeof n === "object" && str(n.number).trim())
      .map((n) => ({ label: str(n.label) || "Emergency", short: str(n.short) || str(n.label) || "Emergency",
        number: str(n.number).trim(), when: str(n.when), wallet: n.wallet === true }));
    if (!numbers.length) return null;
    const sources = (Array.isArray(c.sources) ? c.sources : []).filter((x) => x && typeof x === "object")
      .map((x) => ({ title: str(x.title), publisher: str(x.publisher), url: str(x.url), checked: str(x.checked) }));
    return { code: str(c.code), name: str(c.name) || str(c.code), checked: /^\d{4}-\d{2}-\d{2}$/.test(str(c.checked)) ? str(c.checked) : "",
      numbers, ambulance: str(c.ambulance).trim() || null, police: str(c.police).trim() || null, sources,
      notices: (Array.isArray(c.notices) ? c.notices : []).map(str).filter(Boolean) };
  }
  const cleanPlace = (c) => (c && typeof c === "object" && CODE.test(str(c.code)) ? { code: str(c.code), name: str(c.name) || str(c.code) } : null);
  function cleanEmergency(d) {
    d = d && typeof d === "object" ? d : {};
    const countries = (Array.isArray(d.countries) ? d.countries : []).map(cleanPlace).filter(Boolean);
    const choice = countries.some((c) => c.code === d.choice) ? d.choice : null;
    const det = cleanPlace(d.detected);
    if (det) Object.assign(det, { hasCard: d.detected.has_card === true, detected_by: str(d.detected.detected_by) });
    const current = d.fallback ? null : cleanCard(d.current);
    return { choice, detected: det, countries, country: cleanPlace(d.country), current };
  }
  const emergency = {
    load: async () => cleanEmergency(await call("/api/emergency")),
    choose: async (choice) => cleanEmergency(await post("/api/emergency", { choice })),
  };

  // ---------- preferences ----------
  // The server's fields (README "Interface preferences"): theme, text_size, sidebar_hidden, sidebar_width, panel_width.
  // The interface uses: theme, size, sidebar ("open"|"closed"), sidebar_width, panel_width.
  const toServer = (p) => {
    const out = {};
    if ("theme" in p) out.theme = p.theme;
    if ("size" in p) out.text_size = p.size;
    if ("sidebar" in p) out.sidebar_hidden = p.sidebar === "closed";
    if ("sidebar_width" in p) out.sidebar_width = p.sidebar_width;
    if ("panel_width" in p) out.panel_width = p.panel_width;
    return out;
  };
  const fromServer = (d) => (d && typeof d === "object" ? {
    theme: d.theme, size: d.text_size, sidebar: d.sidebar_hidden === true ? "closed" : "open",
    sidebar_width: d.sidebar_width, panel_width: d.panel_width,
  } : null);
  const prefs = {
    load: async () => fromServer(await call("/api/preferences")),
    save: async (changes) => { const body = toServer(changes); if (Object.keys(body).length) await post("/api/preferences", body); },
  };

  // ---------- the library pages and something to read (README "Library and articles") ----------
  const library = {
    info: () => call("/api/library/info"),
    sources: () => call("/api/library/sources"),
    // after: {after, after_id} from the previous page's `next`, or {after: "M"} to start at a letter
    browse: (source, after = {}, limit = 50) => {
      const q = { source, limit: String(limit) };
      if (after.after) q.after = after.after;
      if (after.after && after.after_id) q.after_id = String(after.after_id);
      return call(`/api/library/browse?${new URLSearchParams(q)}`);
    },
    letters: (source) => call(`/api/library/letters?${new URLSearchParams({ source })}`),
  };
  const reads = {
    // [{doc_id, passage_id, title, source, source_name, category}], up to count; empty when none are available
    random: async (category = "anything", count = 1) => {
      const d = await call(`/api/library/random?${new URLSearchParams({ category, count: String(count) })}`);
      return (Array.isArray(d.articles) ? d.articles : [])
        .filter((a) => a && Number.isSafeInteger(Number(a.passage_id)) && Number(a.passage_id) > 0)
        .map((a) => ({ doc_id: Number(a.doc_id), passage_id: Number(a.passage_id), title: str(a.title), source_name: str(a.source_name), category: str(a.category) }));
    },
  };

  root.SLApi = {
    FEATURES, ApiError, errorText, raw, call,
    setStaleHandler(fn) { onStale = fn; },
    status,
    setModel: (model) => post("/api/model", { model }),
    warm: (model) => post("/api/warm", { model }),
    startOllama: () => post("/api/start-ollama"),
    // Settings › Model: the recommended-model table, and a row's download through the local Ollama (models.js)
    models: { load: () => call("/api/models"), pull: (model) => post("/api/models/pull", { model }),
      cancel: (model) => post("/api/models/cancel", { model }),
      remove: (model) => post("/api/models/remove", { model }) },
    // "Set up the AI": install Ollama if needed, start it, then download the model (models.js buildSetup)
    setup: { start: () => post("/api/ollama/setup"), cancel: () => post("/api/ollama/setup/cancel") },
    quit: () => post("/api/quit"),
    refreshNow: () => post("/api/refresh"),
    restart: () => post("/api/restart"),
    // chats
    chats: () => call("/api/chats"),
    chat: (id) => call(`/api/chats/${id}`),
    renameChat: (id, title) => post(`/api/chats/${id}/rename`, { title }),
    deleteChat: (id) => call(`/api/chats/${id}`, { method: "DELETE" }),
    newConversation: (old, signal) => post("/api/new", old ? { conversation: old } : {}, { signal }),
    // The answer stream: the Response itself, read by the caller. `from`: the saved index of the question being
    // edited or asked again (README "Edit and Ask again"), or null for a new question.
    // summarise: true asks for a model answer even with "Documents only" on (the Summarise button).
    ask: (conversation, message, from, signal, { summarise = false } = {}) => raw("/api/chat", { method: "POST", signal,
      json: { conversation, message, ...(Number.isInteger(from) ? { from } : {}), ...(summarise ? { summarise: true } : {}) } }),
    // library and articles. ref: a saved source's {key, url, title}, so it still opens after its source was rebuilt
    // and the id moved; or, for one saved before sources kept their key, its {name, title} (README "Library and articles").
    article: (id, ref) => {
      const q = { id: String(Number(id)) };
      if (ref && typeof ref.key === "string" && ref.key) Object.assign(q, { key: ref.key, url: str(ref.url).slice(0, 2000), title: str(ref.title).slice(0, 500) });
      else if (ref && typeof ref.name === "string" && ref.name) Object.assign(q, { name: ref.name.slice(0, 200), title: str(ref.title).slice(0, 500) });
      return call(`/api/article?${new URLSearchParams(q)}`);
    },
    search: (q, topic) => call(`/api/search?${new URLSearchParams(topic ? { q, topic } : { q })}`),
    // Title search (the Library's "Find a title"): {results: [{doc_id, title, source, source_name, official}]}
    searchTitles: (q, { sources = [], official = false, limit = 50 } = {}) => {
      const p = { q, titles: "1", limit: String(limit) };
      if (sources.length) p.source = sources.join(",");
      if (official) p.official = "1";
      return call(`/api/search?${new URLSearchParams(p)}`);
    },
    articleByDoc: (doc) => call(`/api/article?doc=${encodeURIComponent(String(Number(doc)))}`),
    // An open article's place, from any of its passage ids: {source, sourceName, category, categoryName, related:
    // [{doc, title}]} for its breadcrumb and "More in this category", or null (not in the library, or one of Your files).
    articlePlace: async (id) => {
      const d = await call(`/api/library/place?id=${encodeURIComponent(String(Number(id)))}`);
      const p = d.place && typeof d.place === "object" ? d.place : null;
      if (!p || !/^[a-z0-9-]{1,60}$/.test(str(p.source))) return null;
      const category = /^[a-z]{1,20}$/.test(str(p.category)) ? str(p.category) : null;
      return {
        source: str(p.source), sourceName: str(p.source_name) || str(p.source), category,
        categoryName: category ? str(p.category_name) || category : null,
        related: (Array.isArray(p.related) ? p.related : []).filter((r) => r && num(r.doc_id) > 0).slice(0, 12)
          .map((r) => ({ doc: num(r.doc_id), title: str(r.title) || "Untitled" })),
      };
    },
    libraryFolder: (folder) => post("/api/library/folder", { folder }),
    libraryOpen: () => post("/api/library/open"),
    pickFolder: (start) => post("/api/pick-folder", start ? { start } : {}),
    library, reads,
    // guides (repo files, no model or library needed): {guides: [{slug, title, summary, group, order}]} and one guide
    guides: () => call("/api/guides"),
    // a web address the person clicked, opened by the server in the Mac's default browser (never in this window)
    openUrl: (url) => post("/api/open-url", { url: str(url) }),
    // a guide's fold-up booklet (A5 pages, two to an A4 sheet), made by the server and opened in Preview; or all of them
    booklet: (slug) => post("/api/guides/booklet", { slug: str(slug) }),
    allBooklets: () => post("/api/guides/booklets"),
    // a guide as numbered A5 pages, opened in Preview to print; 501 when the app runs in a browser
    printGuide: (slug) => post("/api/guides/print", { slug: str(slug) }),
    guide: (slug) => call(`/api/guides/${encodeURIComponent(str(slug))}`),
    // the emergency pack, the family plan, or a library document (by any of its passage ids) as numbered A5 pages,
    // opened in Preview to print, as a guide's Print does; 501 when the app runs in a browser
    printNumbers: () => post("/api/print/numbers"),
    printPack: () => post("/api/print/pack"),
    printPlan: () => post("/api/print/plan"),
    printDocument: (id) => post("/api/print/document", { id: Number(id) }),
    // the family plan, kept on this Mac only: {plan: {people: [{name, blood, medical}], contact_outside, …}, saved}
    familyPlan: {
      load: () => call("/api/family-plan"),
      save: (plan) => post("/api/family-plan", { plan }),
    },
    // memory and instructions
    memory: () => call("/api/memory"),
    saveMemory: (text, base) => post("/api/memory", { text, base }),
    memoryAiWrites: (on) => post("/api/memory/ai-writes", { on }),
    undoMemory: (fields) => post("/api/memory/undo", fields),
    prompt: () => call("/api/prompt"),
    savePrompt: (text) => post("/api/prompt", { text }),
    resetPrompt: () => post("/api/prompt", { reset: true }),
    // your files
    myFiles: (check) => call(`/api/myfiles${check ? "?check=1" : ""}`),
    addFile: (file, query) => raw(`/api/myfiles/add?${query}`, { method: "POST", body: file, headers: { "Content-Type": "application/octet-stream" } }),
    removeFile: (name) => post("/api/myfiles/remove", { name }),
    openFiles: () => post("/api/myfiles/open"),
    filesFolder: (folder) => post("/api/myfiles/folder", { folder }),
    // how questions are answered: {documents_only, mode, think_off, modes}
    answers: { load: () => call("/api/answers"), save: (changes) => post("/api/answers", changes) },
    // interface state
    emergency, prefs,
    // the free library (free-library.js): its sources, the job, and the buttons that start or move it. The server
    // downloads; the page only asks it to, after a click.
    freeLibrary: {
      status: () => call("/api/free-library"),
      act: (action, json = {}) => post(`/api/free-library/${encodeURIComponent(str(action))}`, json),
    },
    // the upgrade (upgrade.js): the key saved in Settings, checked on this computer, and the packs' download. Only
    // download, resume and again make the server contact the key service.
    upgrade: {
      status: () => call("/api/upgrade"),
      act: (action, json = {}) => post(`/api/upgrade/${encodeURIComponent(str(action))}`, json),
    },
    // Starholm's own updates (updates.js): Check for updates (the app and the packs), the automatic switch, Later and
    // Restart now. Only check, and the automatic checks the person turns on, make the server go online.
    updates: {
      status: () => call("/api/updates"),
      act: (action, json = {}) => post(`/api/updates/${encodeURIComponent(str(action))}`, json),
      restart: () => post("/api/updates/restart"),
    },
    // offline maps (maps.js): the list, download (and resume), pause and delete (the server downloads straight from
    // Kiwix, maps/direct.py), and the viewer's calls
    maps: {
      list: () => call("/api/maps"),
      download: (id) => post("/api/maps/download", { id: str(id) }),
      pause: (id) => post("/api/maps/pause", { id: str(id) }),
      remove: (id) => post("/api/maps/remove", { id: str(id) }),
      info: (id) => call(`/api/maps/info?id=${encodeURIComponent(str(id))}`),
      search: (id, q) => call(`/api/maps/search?id=${encodeURIComponent(str(id))}&q=${encodeURIComponent(str(q))}`),
      print: (page) => post("/api/maps/print", page),
      printPage: () => call("/api/maps/print-page"),
      headers: () => ({ "X-Token": TOKEN }),  // for the map viewer's own requests to /maps/<id>/...
    },
  };
})(window);
