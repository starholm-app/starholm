// Settings › Model and library-only mode, as plain logic for app.js (and tests/interface_models_check.js, run with node):
// - rowView: what one row of the recommended-model table shows (in use, installed, downloading with progress and
//   Cancel, too large for this computer, not installed with "Download with Ollama"), from GET /api/models. An
//   installed row the registry has a newer version of also says "Update available" with an Update button.
// - ollamaView: the Ollama row (running, stopped with Start, starting, or missing with Check again).
// - buildTable: the table itself, made with the h() it is given (render.js h in the page, a stand-in in the check).
// - helpLink: the "Help choosing a model" link under the table, to the website's model page (opened in the browser).
// - libraryOnly / noModel / welcomeNote: library-only mode, on purpose (the Library only switch) or because no model
//   can answer (no Ollama, Ollama not running, or the chosen model not downloaded), from /api/status's library_only.
// - setupView / buildSetup: "Set up the AI" when Ollama isn't found. Before anything downloads it says what will happen,
//   with the sizes; then the step it is on with progress and Cancel; when a step fails, what failed, the manual route
//   (ollama.com/download) and Try again. From /api/models's setup and setup_offer (app/ollama_install.py).
// - removeConfirm: the confirmation before an installed row's Remove (the model, the space it frees, what answers next).
// - pollDelay: how soon to ask again while a model downloads or the setup runs.
// Nothing here touches the page or the network.
(function (root) {
  "use strict";

  const str = (v) => (typeof v === "string" ? v : v == null ? "" : String(v));
  const NO_MODEL = ["no_ollama", "stopped", "not_installed"];
  const NO_MODEL_LINE = "Written answers need a model. Set one up in Settings › Model.";
  const NOT_AVAILABLE_LINE = "A model for this memory size is still being tested.";
  const UPDATE_LINE = "Update available";
  const BADGE = "Recommended for your computer";
  const HELP_URL = "https://starholm.app/models";
  const HELP_LABEL = "Help choosing a model";
  const GUIDE_URL = "https://ollama.com/download";
  const SETUP_LABEL = "Set up the AI";
  const NO_ACCOUNT_LINE = "You don't need an account, and everything stays on this computer.";
  const MANUAL_LINE = "You can also install Ollama yourself from ollama.com/download, then press Check again.";
  const STEP_TEXT = { find: "Finding Ollama's download…", check: "Checking the download…", install: "Installing Ollama…",
    start: "Starting Ollama…", model: "Starting the model download…" };

  function gb(n) {
    const v = Number(n);
    if (!Number.isFinite(v) || v <= 0) return "";
    return v >= 10 ? `${Math.round(v)} GB` : `${Math.round(v * 10) / 10} GB`;
  }

  // A file size in bytes: "230 MB", or "1.9 GB" from a thousand million bytes up.
  function size(bytes) {
    const v = Number(bytes);
    if (!Number.isFinite(v) || v <= 0) return "";
    return v >= 1e9 ? gb(v / 1e9) : `${Math.max(1, Math.round(v / 1e6))} MB`;
  }

  // Why questions get no written answer: "setting", "no_ollama", "stopped", "not_installed", "starting", or null.
  function libraryOnly(s) {
    const why = s && s.library_only;
    return ["setting", "starting", ...NO_MODEL].includes(why) ? why : null;
  }
  // Whether no model can answer at all (so a question shows the library's documents, and Summarise can't help).
  const noModel = (s) => NO_MODEL.includes(libraryOnly(s));

  // The start screen's note: library-only mode because there is no model, or null.
  function welcomeNote(s) {
    if (!noModel(s)) return null;
    const why = libraryOnly(s);
    const first = why === "no_ollama" ? "No AI model is set up" : why === "stopped" ? "The AI model isn't running"
      : "The AI model isn't downloaded yet";
    return { text: `${first}, so Starholm is in library-only mode. Questions show the documents the library finds.`,
      link: "Set up a model" };
  }

  function ollamaView(state) {
    if (state === "running") return { dot: "", text: "Installed and running", action: null };
    if (state === "starting") return { dot: "warn", text: "Starting…", action: null };
    if (state === "stopped") return { dot: "off", text: "Installed, not running", action: "start", label: "Start Ollama" };
    return { dot: "off", text: "Not found.", action: "check", label: "Check again" };
  }

  // What the setup says before anything downloads: what it downloads, from where, how big, and that no account is
  // needed. offer: /api/models's setup_offer.
  function setupLead(offer) {
    const o = offer || {};
    const engine = size(o.engine_bytes);
    const model = o.model && Number(o.model_gb) > 0
      ? `Then it downloads the model, ${str(o.model_name) || str(o.model)} (${gb(o.model_gb)}).`
      : "Then you pick a model in the table below.";
    return [`One click downloads the latest stable release of Ollama, the free open-source program that runs the AI on this computer, from Ollama's official GitHub page${engine ? ` (${engine})` : ""}.`,
      model, NO_ACCOUNT_LINE];
  }

  // The setup panel: null when it isn't needed (Ollama found and no setup running or failed). Otherwise
  // { kind: "offer" | "running" | "error", lead (offer: its sentences), note, text, progress (0 to 1 or null),
  //   message, action ("setup", "cancel" or "retry"), label }.
  function setupView(data) {
    const d = data || {};
    const job = d.setup || {};
    if (job.state === "running") {
      const total = Number(job.total) > 0 ? Number(job.total) : 0;
      const done = total ? Math.min(Math.max(Number(job.completed) || 0, 0), total) : 0;
      let text = STEP_TEXT[job.step] || STEP_TEXT.find;
      if (job.step === "download") text = total ? `Downloading Ollama… ${Math.floor((done / total) * 100)}% of ${size(total)}` : "Downloading Ollama…";
      return { kind: "running", text, progress: job.step === "download" ? (total ? done / total : 0) : null,
        action: "cancel", label: "Cancel" };
    }
    if (job.state === "error") {
      return { kind: "error", message: str(job.message) || "The setup stopped.", note: MANUAL_LINE, action: "retry",
        label: "Try again" };
    }
    if (d.ollama !== "missing") return null;
    if (!d.setup_offer || !d.setup_offer.available) {
      return { kind: "error", message: "Automatic setup isn't available on this computer yet.", note: MANUAL_LINE,
        action: null, label: "" };
    }
    return { kind: "offer", lead: setupLead(d.setup_offer), note: job.state === "cancelled" ? "Cancelled." : "",
      action: "setup", label: SETUP_LABEL };
  }

  // The confirmation before Remove: { title, lines, label }, naming the model, the space it frees, and what answers
  // questions afterwards (row.remove_then from the server), or that no model is left (row.remove_last).
  function removeConfirm(row, rows) {
    const r = row || {};
    const name = str(r.name) || str(r.model);
    const bytes = Number(r.bytes) > 0 ? Number(r.bytes) : Number(r.download_gb) * 1e9;
    const lines = [`This deletes it from this computer${bytes > 0 ? ` and frees ${size(bytes)}` : ""}.`];
    if (r.remove_last) {
      lines.push("It's the only model on this computer. Until you download one again, questions show the library's documents with no written answer.");
    } else if (r.state === "in_use" && r.remove_then) {
      const next = (Array.isArray(rows) ? rows : []).find((x) => x && str(x.model).replace(/:latest$/, "") === str(r.remove_then));
      lines.push(`It's the model in use, so answers will come from ${next ? str(next.name) : str(r.remove_then)} instead.`);
    }
    lines.push("You can download it again later.");
    return { title: `Remove ${name}?`, lines, label: "Remove" };
  }

  // The panel itself, made with h(). on(action) runs its button; open(url) hands an address to the browser.
  function buildSetup(data, h, on, open) {
    const v = setupView(data);
    if (!v) return null;
    const button = v.action ? h("button", { class: v.action === "cancel" ? "btn" : "btn primary", type: "button",
      "data-action": `setup-${v.action}`, onclick: () => on(v.action) }, v.label) : null;
    if (v.kind === "offer") {
      return h("div", { class: "ai-setup", "data-kind": "offer" },
        h("p", { class: "ai-setup-lead" }, v.lead.join(" ")), button,
        v.note ? h("p", { class: "mt-detail" }, v.note) : null);
    }
    if (v.kind === "running") {
      return h("div", { class: "ai-setup", "data-kind": "running", "aria-live": "polite" },
        h("p", { class: "ai-setup-step" }, v.text),
        v.progress != null ? h("progress", { class: "mt-progress", value: String(v.progress), "aria-label": "Downloading Ollama" }) : null,
        button);
    }
    return h("div", { class: "ai-setup", "data-kind": "error", "aria-live": "assertive" },
      h("p", { class: "ai-setup-error" }, v.message),
      h("p", { class: "hint" }, v.note),
      button,
      h("button", { class: "btn", type: "button", "data-action": "setup-guide", "data-url": GUIDE_URL,
        onclick: () => open(GUIDE_URL) }, "Open ollama.com/download"));
  }

  // One row: { state, status (words shown instead of a button, or ""), action ("pull", "cancel", "use" or null),
  // label, primary, disabled, progress (0 to 1, or null), detail (words under the model's name, or ""),
  // update (true: "Update available" and an Update button beside the row's own button or status) }.
  // busy: another row is downloading (one download at a time), so this row's Download waits.
  function rowView(row, ollama, busy = false) {
    const r = row || {};
    const dl = r.download || null;
    const view = { state: str(r.state), status: "", action: null, label: "", primary: false, disabled: false,
      progress: null, detail: "", update: false };
    if (r.state === "in_use" || r.state === "installed") {
      if (r.state === "in_use") view.status = "In use";
      else Object.assign(view, { action: "use", label: "Use this model" });
      view.remove = true;
      view.removeDisabled = ollama !== "running" || busy;
      if (r.update === true) {
        view.update = true;
        view.detail = UPDATE_LINE;
        view.updateDisabled = ollama !== "running" || busy;
      }
    }
    else if (r.state === "downloading") {
      const total = dl && Number(dl.total) > 0 ? Number(dl.total) : 0;
      const done = total ? Math.min(Math.max(Number(dl.completed) || 0, 0), total) : 0;
      view.progress = total ? done / total : 0;
      view.detail = total ? `Downloading… ${Math.floor((done / total) * 100)}% of ${gb(total / 1e9)}` : str(dl && dl.message) || "Starting…";
      Object.assign(view, { action: "cancel", label: "Cancel" });
    } else if (r.state === "too_large") view.status = "Too large for this computer";
    else if (r.state === "not_available") view.detail = NOT_AVAILABLE_LINE;  // a placeholder row: no download
    else {
      Object.assign(view, { action: "pull", label: "Download with Ollama", primary: !!r.recommended,
        disabled: ollama !== "running" || busy });
      if (dl && (dl.state === "error" || dl.state === "cancelled")) view.detail = str(dl.message);
      else if (ollama !== "running") view.detail = "Needs Ollama running.";
    }
    return view;
  }

  // The table: Memory, Model, Download, and the row's button or status. on(action, row) runs a button.
  function buildTable(data, h, on) {
    const rows = data && Array.isArray(data.rows) ? data.rows : [];
    const ollama = data ? data.ollama : "missing";
    const busy = rows.some((r) => r.state === "downloading");
    const body = rows.map((row) => {
      const v = rowView(row, ollama, busy);
      const last = h("td", { class: "mt-act" },
        v.action ? h("button", { class: v.primary ? "btn primary" : "btn", type: "button", disabled: v.disabled,
          "data-action": v.action, "data-model": str(row.model), onclick: () => on(v.action, row) }, v.label)
          : h("span", { class: "val" }, v.status),
        v.update ? h("button", { class: "btn primary mt-update", type: "button", disabled: v.updateDisabled,
          "data-action": "update", "data-model": str(row.model), onclick: () => on("update", row) }, "Update") : null,
        v.remove ? h("button", { class: "btn mt-remove", type: "button", disabled: v.removeDisabled,
          "data-action": "remove", "data-model": str(row.model), onclick: () => on("remove", row) }, "Remove") : null);
      const memory = `${Number(row.memory_gb)} GB${row === rows[rows.length - 1] ? " or more" : ""}`;
      return h("tr", { class: row.recommended ? "mt-rec" : null, "data-state": v.state },
        h("td", null, memory),
        h("td", null, h("b", null, str(row.size)), h("span", { class: "hint" }, ` ${str(row.name)}`),
          row.recommended ? h("span", { class: "tag mt-badge" }, BADGE) : null,
          v.detail ? h("span", { class: "mt-detail" }, v.detail) : null,
          v.progress != null ? h("progress", { class: "mt-progress", value: String(v.progress),
            "aria-label": `Downloading ${str(row.name)}` }) : null),
        h("td", { class: "mt-num" }, gb(row.download_gb)),
        last);
    });
    return h("table", { class: "model-table" },
      h("thead", null, h("tr", null, h("th", { scope: "col" }, "Memory"), h("th", { scope: "col" }, "Model"),
        h("th", { scope: "col" }, "Download"), h("th", { scope: "col" }, h("span", { class: "visually-hidden" }, "Action")))),
      h("tbody", null, body));
  }

  // Under the table: open(url) hands the address to the browser (app.js's API.openUrl), a new window or tab there.
  function helpLink(h, icon, open) {
    return h("p", { class: "mt-help" }, h("button", { class: "link", type: "button", "data-action": "help-models",
      "data-url": HELP_URL, onclick: () => open(HELP_URL) }, HELP_LABEL, " ", icon("external", "sm")));
  }

  function lead(data) {
    const n = data && Number(data.memory_gb);
    return n > 0 ? `The AI runs on this computer, so it needs memory. This computer has ${n} GB. Pick the row that matches.`
      : "The AI runs on this computer, so it needs memory. Pick the row that matches this computer's memory.";
  }

  const pollDelay = (data) => (data && ((Array.isArray(data.rows) && data.rows.some((r) => r.state === "downloading"))
    || (data.setup && data.setup.state === "running")) ? 1000 : data && data.ollama === "starting" ? 3000
    : data && data.ollama === "missing" && data.setup_offer && data.setup_offer.available && !data.setup_offer.engine_bytes
      ? 3000 : null);  // the offer's size is still being read from GitHub

  root.SLModels = { rowView, ollamaView, buildTable, lead, libraryOnly, noModel, welcomeNote, pollDelay, NO_MODEL_LINE, NOT_AVAILABLE_LINE, UPDATE_LINE, BADGE, HELP_URL, HELP_LABEL, helpLink, gb, size, setupLead, setupView, buildSetup, removeConfirm, GUIDE_URL, SETUP_LABEL, NO_ACCOUNT_LINE, MANUAL_LINE, STEP_TEXT };
})(typeof window !== "undefined" ? window : globalThis);
