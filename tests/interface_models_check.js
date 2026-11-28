// Checks for Settings › Model and library-only mode in the interface, run by tests/interface_models_test.py with node:
// app/static/models.js draws each state of the recommended-model table (in use, installed, downloading with progress
// and Cancel, too large for this computer, not installed with "Download", the recommended row marked), the AI engine's
// row (running, stopped, starting, not set up), the no-model state (the start screen's note and the line under a
// question), "Set up the AI", and the "and the AI too" rule and line the Upgrade page and the free library use. node
// has no document, so a small stand-in h() records what buildTable makes.
// Run alone: node tests/interface_models_check.js app/static/models.js
"use strict";
const assert = require("assert");
const path = require("path");

let n = 0;
const ok = (name, fn) => { fn(); n++; console.log("PASS", name); };

// ---------- a stand-in h(): elements as plain objects ----------
function h(tag, props, ...kids) {
  const el = { tag, props: props || {}, kids: kids.flat(Infinity).filter((k) => k != null && k !== false) };
  for (const [k, v] of Object.entries(el.props)) {
    if (v == null || v === false) delete el.props[k];
    else if (!k.startsWith("on") && !["class", "type", "disabled", "value", "scope"].includes(k) && !/^(aria|data)-/.test(k)) {
      throw new Error(`h(): ${k} is not an attribute the page's h() allows`);
    }
  }
  return el;
}
const text = (el) => (typeof el === "string" ? el : el.kids.map(text).join(""));
const all = (el, test, out = []) => {
  if (typeof el === "string") return out;
  if (test(el)) out.push(el);
  el.kids.forEach((k) => all(k, test, out));
  return out;
};

require(path.resolve(process.argv[2] || "app/static/models.js"));
const M = globalThis.SLModels;

const ROWS = [
  { memory_gb: 8, size: "Small model", name: "Gemma 4, 4B", model: "small:4b", download_gb: 3, tested: false },
  { memory_gb: 16, size: "Medium model", name: "Gemma 4, 12B", model: "medium:12b", download_gb: 8, tested: false },
  { memory_gb: 32, size: "Large model", name: "Gemma 4, 26B", model: "large:26b", download_gb: 15, tested: true },
];
const table = (states, extra = {}) => ({
  memory_gb: 16, ollama: "running", library_only: false,
  rows: ROWS.map((r, i) => ({ ...r, recommended: i === 1, download: null, ...states[i] })), ...extra,
});

// ---------- each row state ----------
ok("not installed: a Download button, primary on the recommended row", () => {
  const v = M.rowView({ ...ROWS[1], state: "not_installed", recommended: true }, "running");
  assert.deepStrictEqual([v.action, v.label, v.primary, v.disabled], ["pull", "Download", true, false]);
  assert.strictEqual(M.rowView({ ...ROWS[0], state: "not_installed" }, "running").primary, false);
});
ok("not installed with the engine not running: the button is off and says why", () => {
  const v = M.rowView({ ...ROWS[1], state: "not_installed" }, "stopped");
  assert.strictEqual(v.disabled, true);
  assert.strictEqual(v.detail, "Needs the AI engine running.");
});
ok("downloading: progress, the percentage of the size, and Cancel", () => {
  const v = M.rowView({ ...ROWS[1], state: "downloading", download: { state: "downloading", completed: 2e9, total: 8e9, message: "Downloading…" } }, "running");
  assert.deepStrictEqual([v.action, v.label, v.progress], ["cancel", "Cancel", 0.25]);
  assert.strictEqual(v.detail, "Downloading… 25% of 8 GB");
  const starting = M.rowView({ ...ROWS[1], state: "downloading", download: { state: "downloading", completed: 0, total: 0, message: "Starting…" } }, "running");
  assert.deepStrictEqual([starting.progress, starting.detail], [0, "Starting…"]);
});
ok("installed: Use this model; in use: says so, with no button", () => {
  assert.deepStrictEqual([M.rowView({ ...ROWS[0], state: "installed" }, "running").action, M.rowView({ ...ROWS[0], state: "installed" }, "running").label],
    ["use", "Use this model"]);
  const v = M.rowView({ ...ROWS[0], state: "in_use" }, "running");
  assert.deepStrictEqual([v.action, v.status], [null, "In use"]);
});
ok("too large for this computer: says so, with no button", () => {
  const v = M.rowView({ ...ROWS[2], state: "too_large" }, "running");
  assert.deepStrictEqual([v.action, v.status], [null, "Too large for this computer"]);
});
ok("a cancelled or failed download says so on its row", () => {
  assert.strictEqual(M.rowView({ ...ROWS[1], state: "not_installed", download: { state: "cancelled", message: "Cancelled." } }, "running").detail, "Cancelled.");
  assert.strictEqual(M.rowView({ ...ROWS[1], state: "not_installed", download: { state: "error", message: "The download failed: x" } }, "running").detail,
    "The download failed: x");
});

ok("a placeholder row (not available yet): one plain line and no download button, even when recommended", () => {
  const v = M.rowView({ ...ROWS[1], state: "not_available", recommended: true }, "running");
  assert.deepStrictEqual([v.action, v.status, v.detail], [null, "", "A model for this memory size is still being tested."]);
  const t = M.buildTable(table([{ state: "not_available" }, { state: "not_available" }, { state: "too_large" }]), h, () => {});
  assert.strictEqual(all(t, (e) => e.tag === "button").length, 0);
  const rec = all(t, (e) => e.tag === "tr" && e.props.class === "mt-rec")[0];
  assert.ok(text(rec).includes("A model for this memory size is still being tested."), text(rec));
  assert.ok(!text(rec).includes("Not tested yet"), "the line already says it; no second label");
});

// ---------- the table ----------
ok("the table: one row per data row, memory, model, size, and the recommended row marked", () => {
  const t = M.buildTable(table([{ state: "in_use" }, { state: "not_installed" }, { state: "too_large" }]), h, () => {});
  const rows = all(t, (e) => e.tag === "tr").slice(1);
  assert.strictEqual(rows.length, 3);
  assert.deepStrictEqual(rows.map((r) => text(r.kids[0])), ["8 GB", "16 GB", "32 GB or more"]);
  assert.deepStrictEqual(rows.map((r) => text(r.kids[2])), ["3 GB", "8 GB", "15 GB"]);
  assert.deepStrictEqual(rows.map((r) => r.props.class || ""), ["", "mt-rec", ""]);
  assert.ok(text(rows[1]).includes("Recommended for your computer") && !text(rows[0]).includes("Recommended"));
  assert.deepStrictEqual(rows.map((r) => r.props["data-state"]), ["in_use", "not_installed", "too_large"]);
  assert.deepStrictEqual(rows.map((r) => text(r.kids[3])), ["In useRemove", "Download", "Too large for this computer"]);
});
ok("the table while downloading: a progress bar and a Cancel that calls back with the row", () => {
  const calls = [];
  const t = M.buildTable(table([{ state: "installed" }, { state: "downloading", download: { state: "downloading", completed: 4e9, total: 8e9, message: "" } },
    { state: "too_large" }]), h, (a, r) => calls.push([a, r.model]));
  const bar = all(t, (e) => e.tag === "progress");
  assert.strictEqual(bar.length, 1);
  assert.strictEqual(bar[0].props.value, "0.5");
  const buttons = all(t, (e) => e.tag === "button");
  assert.deepStrictEqual(buttons.map((b) => text(b)), ["Use this model", "Remove", "Cancel"]);
  const waiting = M.buildTable(table([{ state: "not_installed" }, { state: "downloading", download: null }, { state: "too_large" }]), h, () => {});
  assert.strictEqual(all(waiting, (e) => e.tag === "button" && e.props["data-action"] === "pull")[0].props.disabled, true,
    "one download at a time: the other rows' Download waits");
  buttons.forEach((b) => b.props.onclick());
  assert.deepStrictEqual(calls, [["use", "small:4b"], ["remove", "small:4b"], ["cancel", "medium:12b"]]);
});
ok("every row is listed with no testing label; only the best fit carries the badge", () => {
  const t = M.buildTable(table([{ state: "not_installed" }, { state: "not_installed" }, { state: "not_installed" }]), h, () => {});
  assert.ok(!text(t).includes("Not tested"), text(t));
  assert.strictEqual(all(t, (e) => e.props.class === "tag mt-badge").length, 1);
  assert.strictEqual(all(t, (e) => e.tag === "button" && e.props["data-action"] === "pull").length, 3);
});
ok("the table ends with Help choosing a model, which opens the site's model page in the browser", () => {
  const opened = [];
  const icon = (name) => h("svg", { "data-icon": name });
  const p = M.helpLink(h, icon, (url) => opened.push(url));
  const b = all(p, (e) => e.tag === "button")[0];
  assert.ok(text(b).includes("Help choosing a model"));
  assert.strictEqual(all(b, (e) => e.props["data-icon"] === "external").length, 1);
  b.props.onclick();
  assert.deepStrictEqual(opened, ["https://starholm.app/models"]);
});
ok("the lead names this computer's memory", () => {
  assert.strictEqual(M.lead({ memory_gb: 16 }), "The AI runs on this computer, so it needs memory. This computer has 16 GB. Pick the row that matches.");
  assert.ok(!M.lead(null).includes("undefined"));
});
ok("the table asks again every second while a model downloads, and not otherwise", () => {
  assert.strictEqual(M.pollDelay(table([{}, { state: "downloading" }, {}])), 1000);
  assert.strictEqual(M.pollDelay(table([{ state: "in_use" }, { state: "not_installed" }, { state: "too_large" }])), null);
});

// ---------- the AI engine's row ----------
ok("the engine: running, starting, not running with Start the AI, not set up with Check again", () => {
  assert.deepStrictEqual([M.ollamaView("running").text, M.ollamaView("running").action], ["Running", null]);
  assert.strictEqual(M.ollamaView("starting").text, "Starting…");
  assert.deepStrictEqual([M.ollamaView("stopped").text, M.ollamaView("stopped").action, M.ollamaView("stopped").label],
    ["Not running", "start", "Start the AI"]);
  const missing = M.ollamaView("missing");
  assert.deepStrictEqual([missing.text, missing.action, missing.label], ["Not set up yet.", "check", "Check again"]);
});

// ---------- the no-model state ----------
ok("no model: library-only mode, a start-screen note naming it, with Set up a model", () => {
  for (const why of ["no_ollama", "stopped", "not_installed"]) {
    const s = { library_only: why };
    assert.strictEqual(M.libraryOnly(s), why);
    assert.strictEqual(M.noModel(s), true);
    const note = M.welcomeNote(s);
    assert.ok(note.text.endsWith("so Starholm is in library-only mode. Questions show the documents the library finds."), note.text);
    assert.strictEqual(note.link, "Set up a model");
  }
  assert.ok(M.welcomeNote({ library_only: "no_ollama" }).text.startsWith("No AI model is set up"));
});
ok("Library only on purpose, or a model ready: no no-model note", () => {
  assert.strictEqual(M.libraryOnly({ library_only: "setting" }), "setting");
  assert.strictEqual(M.noModel({ library_only: "setting" }), false);
  assert.strictEqual(M.welcomeNote({ library_only: "setting" }), null);
  assert.strictEqual(M.welcomeNote({ library_only: null }), null);
  assert.strictEqual(M.welcomeNote({ library_only: "starting" }), null);
  assert.strictEqual(M.noModel(null), false);
});
ok("the line under a question with no model matches the server's", () => {
  assert.strictEqual(M.NO_MODEL_LINE, "Written answers need a model. Set one up in Settings › Model.");
});
ok("no price anywhere in the model page's words", () => {
  const words = JSON.stringify([M.lead({ memory_gb: 8 }), ...["running", "stopped", "starting", "missing"].map(M.ollamaView),
    M.welcomeNote({ library_only: "no_ollama" }), M.NO_MODEL_LINE]);
  assert.ok(!/[$€£]|\bprice\b|\bpaid\b/i.test(words), words);
});

ok("an installed model with a newer version: Update available and an Update button that calls back with the row", () => {
  const calls = [];
  const t = M.buildTable(table([{ state: "in_use", update: true }, { state: "installed", update: true }, { state: "too_large" }]), h,
    (a, r) => calls.push([a, r.model]));
  const rows = all(t, (e) => e.tag === "tr").slice(1);
  assert.ok(text(rows[0]).includes("Update available") && text(rows[0].kids[3]).includes("In use"), text(rows[0]));
  assert.ok(text(rows[1].kids[3]).includes("Use this model"), "the row's own button stays");
  const ups = all(t, (e) => e.tag === "button" && e.props["data-action"] === "update");
  assert.strictEqual(ups.length, 2);
  ups[0].props.onclick();
  assert.deepStrictEqual(calls, [["update", "small:4b"]]);
});
ok("no Update for the same version, or while another model downloads", () => {
  const same = M.buildTable(table([{ state: "in_use", update: false }, { state: "installed" }, { state: "too_large" }]), h, () => {});
  assert.strictEqual(all(same, (e) => e.props && e.props["data-action"] === "update").length, 0);
  assert.ok(!text(same).includes("Update available"));
  assert.strictEqual(M.rowView({ ...ROWS[0], state: "installed", update: true }, "running", true).updateDisabled, true);
});

// ---------- Set up the AI (app/ollama_install.py) ----------
const OFFER = { available: true, engine_bytes: 230778782, model: "gemma4:12b-it-qat", model_name: "Gemma 4, 12B", model_gb: 7.2 };
const missing = (setup, extra = {}) => ({ ...table([{}, {}, {}]), ollama: "missing", setup: { state: "idle", ...setup },
  setup_offer: OFFER, ...extra });

ok("Set up the AI: before anything downloads, it says what, both sizes, that no account is needed, and one credit", () => {
  const calls = [];
  const panel = M.buildSetup(missing({}), h, (a) => calls.push(a), () => {});
  const words = text(panel);
  assert.strictEqual(words.split("Set up the AI")[0],
    "One click downloads the AI engine (231 MB) and the model recommended for this computer, Gemma 4, 12B (7.2 GB). "
    + "No account is needed, and everything stays on this computer. The engine is Ollama, a free open-source program.");
  const buttons = all(panel, (e) => e.tag === "button");
  assert.deepStrictEqual(buttons.map(text), ["Set up the AI"]);
  assert.strictEqual(all(panel, (e) => e.tag === "progress").length, 0);
  assert.deepStrictEqual(calls, [], "nothing starts until the button is pressed");
  buttons[0].props.onclick();
  assert.deepStrictEqual(calls, ["setup"]);
});
ok("Set up the AI: Windows' engine size reads in GB, and with no model that fits it says to pick one", () => {
  assert.ok(M.setupLead({ ...OFFER, engine_bytes: 1857946272 })[0].startsWith("One click downloads the AI engine (1.9 GB) and"));
  assert.ok(M.setupLead({ ...OFFER, engine_bytes: null })[0].startsWith("One click downloads the AI engine and the model"),
    "before GitHub's API answers: no size, no made-up number");
  assert.strictEqual(M.pollDelay(missing({}, { setup_offer: { ...OFFER, engine_bytes: null } })), 3000,
    "asks again until the size is known");
  assert.strictEqual(M.setupLead({ engine_bytes: 230778782, model: null })[0],
    "One click downloads the AI engine (231 MB). Then you pick a model in the table below.");
});
ok("Set up the AI: running shows the step, the download's progress and size, and Cancel", () => {
  const calls = [];
  const dl = M.buildSetup(missing({ state: "running", step: "download", completed: 115389391, total: 230778782 }), h,
    (a) => calls.push(a), () => {});
  assert.ok(text(dl).startsWith("Downloading the AI engine… 50% of 231 MB"), text(dl));
  assert.strictEqual(all(dl, (e) => e.tag === "progress")[0].props.value, "0.5");
  const cancel = all(dl, (e) => e.tag === "button");
  assert.deepStrictEqual(cancel.map(text), ["Cancel"]);
  cancel[0].props.onclick();
  assert.deepStrictEqual(calls, ["cancel"]);
  for (const [step, words] of [["find", "Finding the AI engine…"], ["check", "Checking the download…"],
    ["install", "Installing the AI engine…"], ["start", "Starting the AI…"], ["model", "Starting the model download…"]]) {
    const v = M.setupView(missing({ state: "running", step }));
    assert.deepStrictEqual([v.text, v.progress, v.action], [words, null, "cancel"]);
  }
  const unpack = M.setupView(missing({ state: "running", step: "install", completed: 3e8, total: 1.2e9 }));
  assert.deepStrictEqual([unpack.text, unpack.progress], ["Installing the AI engine… 25%", 0.25], "Windows: the unpacking's progress");
  assert.strictEqual(M.pollDelay(missing({ state: "running", step: "install" })), 1000);
});
ok("Set up the AI: a failed step says what failed, offers Try again, then one line linking to ollama.com/download", () => {
  const calls = [];
  const opened = [];
  const panel = M.buildSetup(missing({ state: "error", failed: "check",
    message: "The downloaded file didn't match the published one, so it was deleted and nothing was installed." }),
  h, (a) => calls.push(a), (url) => opened.push(url));
  const words = text(panel);
  assert.ok(words.startsWith("The downloaded file didn't match the published one"), words);
  assert.strictEqual(M.MANUAL_LINE, "You can also install Ollama yourself from ollama.com/download, then press Try again.");
  assert.ok(words.endsWith(M.MANUAL_LINE), words);
  assert.ok(!words.includes("Check again"), "the error panel's only button is Try again");
  const buttons = all(panel, (e) => e.tag === "button");
  assert.deepStrictEqual(buttons.map(text), ["Try again", "ollama.com/download"]);
  assert.deepStrictEqual(buttons.map((b) => b.props.class), ["btn primary", "link"]);
  buttons.forEach((b) => b.props.onclick());
  assert.deepStrictEqual([calls, opened], [["retry"], ["https://ollama.com/download"]]);
});
ok("Set up the AI: shown only while Ollama is missing or the setup runs or failed", () => {
  assert.strictEqual(M.setupView({ ...missing({}), ollama: "running" }), null);
  assert.strictEqual(M.setupView({ ...missing({ state: "done" }), ollama: "running" }), null);
  assert.strictEqual(M.setupView(missing({ state: "cancelled" })).note, "Cancelled.");
  const none = M.setupView(missing({}, { setup_offer: { available: false, engine_bytes: null } }));
  assert.strictEqual(none.action, null, "no automatic setup for this computer: only the manual route");
  assert.ok(!none.note.includes("Try again") && !none.note.includes("Check again"), none.note);
});

// ---------- "and the AI too": the Upgrade page's Download now and the free library's download ----------
const NONE = { ...table([{}, {}, {}]), ollama: "missing", setup: { state: "idle" }, setup_offer: OFFER };
const noModelStatus = { ollama: false, ollama_checked: true, models: [], model_installed: false, library_only: "no_ollama" };
ok("the AI rule: no engine, no model, a setup this computer can run: the download sets up the AI too", () => {
  assert.strictEqual(M.aiDecision(noModelStatus, NONE), "start");
  assert.strictEqual(M.aiNeeded(noModelStatus, NONE), true);
  assert.strictEqual(M.aiNeeded(noModelStatus, { ...NONE, ollama: "stopped" }), true, "an engine that isn't running: started, then the model");
  assert.strictEqual(M.aiNeeded(noModelStatus, { ...NONE, setup: { state: "error", message: "x" } }), true, "a failed setup: tried again");
  assert.strictEqual(M.aiNeeded(noModelStatus, { ...NONE, setup: { state: "cancelled" } }), true);
});
ok("the AI rule: Library only on: never", () => {
  assert.strictEqual(M.aiNeeded({ ...noModelStatus, ollama_checked: false, library_only: "setting" }, NONE), false);
  assert.strictEqual(M.aiNeeded(noModelStatus, { ...NONE, library_only: true }), false);
  assert.strictEqual(M.aiLine({ ollama_checked: false, library_only: "setting" }, NONE, "done"), null, "and no line about it");
});
ok("the AI rule: a model installed: never", () => {
  assert.strictEqual(M.aiNeeded({ ...noModelStatus, ollama: true, models: ["medium:12b"], model_installed: true, library_only: null }, null), false);
  assert.strictEqual(M.aiNeeded({ ...noModelStatus, ollama: true, models: ["other:7b"], library_only: "not_installed" }, NONE), false,
    "any chat model counts, even one not in the table");
  assert.strictEqual(M.aiNeeded(noModelStatus, { ...NONE, ollama: "running", rows: NONE.rows.map((r, i) => (i ? r : { ...r, state: "installed" })) }), false);
  assert.strictEqual(M.aiNeeded(noModelStatus, { ...NONE, ollama: "running", rows: NONE.rows.map((r, i) => (i === 1 ? { ...r, state: "downloading" } : r)) }), false,
    "a model already downloading");
});
ok("the AI rule: the setup already running: no second start", () => {
  assert.strictEqual(M.aiDecision(noModelStatus, { ...NONE, setup: { state: "running", step: "download" } }), "none");
});
ok("the AI rule: no automatic setup here, or no model that fits this computer: never", () => {
  assert.strictEqual(M.aiNeeded(noModelStatus, { ...NONE, setup_offer: { ...OFFER, available: false } }), false);
  assert.strictEqual(M.aiNeeded(noModelStatus, { ...NONE, setup_offer: { ...OFFER, model: null } }), false);
  assert.strictEqual(M.aiNeeded(noModelStatus, { ...NONE, setup_offer: null }), false);
  assert.strictEqual(M.aiNeeded(noModelStatus, null), false, "/api/models not asked: nothing starts");
});
ok("the AI rule: the engine still starting: wait until it answers", () => {
  assert.strictEqual(M.aiDecision(noModelStatus, { ...NONE, ollama: "starting" }), "wait");
  assert.strictEqual(M.aiNeeded(noModelStatus, { ...NONE, ollama: "starting" }), false);
});
ok("what the AI's setup downloads: the engine and the model, or only the model when the engine is here", () => {
  assert.strictEqual(M.aiPlan(NONE), "the AI engine (231 MB) and the model Gemma 4, 12B (7.2 GB)");
  assert.strictEqual(M.aiPlan({ ...NONE, setup_offer: { ...OFFER, engine_bytes: null } }), "the AI engine and the model Gemma 4, 12B (7.2 GB)");
  assert.strictEqual(M.aiPlan({ ...NONE, ollama: "stopped" }), "the model Gemma 4, 12B (7.2 GB)");
  assert.strictEqual(M.aiPlan({ ...NONE, setup_offer: { ...OFFER, model: null } }), "");
});
ok("the AI's line: each setup step, then the model's download, then The AI is ready.", () => {
  const run = (setup) => M.aiLine(noModelStatus, { ...NONE, setup: { state: "running", ...setup } }, "downloading", true);
  assert.deepStrictEqual([run({ step: "find" }).kind, run({ step: "find" }).text], ["running", "Finding the AI engine…"]);
  const dl = run({ step: "download", completed: 92e6, total: 230778782 });
  assert.deepStrictEqual([dl.text, dl.action], ["Downloading the AI engine… 39% of 231 MB", null]);
  assert.ok(Math.abs(dl.progress - 92e6 / 230778782) < 1e-9);
  assert.strictEqual(run({ step: "check" }).text, "Checking the download…");
  assert.strictEqual(run({ step: "install", completed: 5e8, total: 1e9 }).text, "Installing the AI engine… 50%");
  assert.strictEqual(run({ step: "start" }).text, "Starting the AI…");
  assert.strictEqual(run({ step: "model" }).text, "Starting the model download…");
  const rows = (state, download) => NONE.rows.map((r, i) => (i === 1 ? { ...r, state, download } : r));
  const model = M.aiLine(noModelStatus, { ...NONE, ollama: "running", setup: { state: "done" },
    rows: rows("downloading", { state: "downloading", completed: 2e9, total: 8e9 }) }, "downloading", true);
  assert.deepStrictEqual([model.kind, model.text, model.progress], ["downloading", "Downloading the model… 25% of 8 GB", 0.25]);
  const ready = M.aiLine(noModelStatus, { ...NONE, ollama: "running", setup: { state: "done" }, rows: rows("in_use", null) }, "downloading", true);
  assert.deepStrictEqual([ready.kind, ready.text, ready.action], ["ready", "The AI is ready.", null]);
});
ok("the AI's line: a failed or cancelled setup says so with Try again", () => {
  const err = M.aiLine(noModelStatus, { ...NONE, setup: { state: "error", message: "The AI engine's download stopped before it finished." } }, "downloading");
  assert.deepStrictEqual([err.kind, err.text, err.action, err.label], ["error", "The AI engine's download stopped before it finished.", "setup", "Try again"]);
  assert.strictEqual(M.aiLine(noModelStatus, { ...NONE, setup: { state: "cancelled" } }, "done").text, "The AI setup was cancelled.");
  const pull = M.aiLine(noModelStatus, { ...NONE, ollama: "running", setup: { state: "done" },
    rows: NONE.rows.map((r, i) => (i === 1 ? { ...r, state: "not_installed", download: { state: "error", message: "The download stopped before it finished. Try again." } } : r)) }, "done");
  assert.deepStrictEqual([pull.kind, pull.action], ["error", "setup"], "the model download the setup started failed");
});
ok("the AI's line: not set up and not running: Set up the AI, except before Download now (its banner says it)", () => {
  const offer = M.aiLine(noModelStatus, NONE, "done");
  assert.deepStrictEqual([offer.kind, offer.action, offer.label], ["offer", "setup", "Set up the AI"]);
  assert.strictEqual(offer.text, "The AI isn't set up yet. Setting it up downloads the AI engine (231 MB) and the model Gemma 4, 12B (7.2 GB).");
  assert.strictEqual(M.aiLine(noModelStatus, NONE, "saved"), null);
  assert.strictEqual(M.aiLine(noModelStatus, { ...NONE, setup_offer: { ...OFFER, available: false } }, "done"), null);
});
ok("the AI's line: a person whose AI is ready sees only The AI is ready., and only once everything is downloaded", () => {
  const ready = { ...noModelStatus, ollama: true, models: ["medium:12b"], model_installed: true, library_only: null };
  assert.strictEqual(M.aiLine(ready, null, "saved"), null);
  assert.strictEqual(M.aiLine(ready, null, "downloading"), null);
  assert.strictEqual(M.aiLine(ready, null, "done").text, "The AI is ready.");
  assert.strictEqual(M.aiLine(noModelStatus, { ...NONE, ollama: "starting" }, "downloading"), null, "starting, not asked for: nothing");
  assert.strictEqual(M.aiLine(noModelStatus, { ...NONE, ollama: "starting" }, "downloading", true).text, "Starting the AI…");
});
ok("no word the person reads in models.js names Ollama, except the credit sentence and the manual route", () => {
  const src = require("fs").readFileSync(path.resolve(process.argv[2] || "app/static/models.js"), "utf8")
    .split("\n").filter((l) => !l.trim().startsWith("//")).join("\n");
  const literals = src.match(/"(?:[^"\\\n]|\\.)*"|`(?:[^`\\]|\\.)*`/g) || [];
  const named = literals.filter((l) => l.includes("Ollama")).map((l) => l.slice(1, -1));
  assert.deepStrictEqual(named, ["The engine is Ollama, a free open-source program.", "You can also install Ollama yourself from ",
    "You can install Ollama yourself from "]);
  const shown = JSON.stringify([M.lead({ memory_gb: 8 }), ...["running", "stopped", "starting", "missing"].map(M.ollamaView),
    M.welcomeNote({ library_only: "no_ollama" }), M.NO_MODEL_LINE, M.setupLead(OFFER).slice(0, 2), Object.values(M.STEP_TEXT),
    M.rowView({ ...ROWS[1], state: "not_installed" }, "stopped"), M.aiLine(noModelStatus, NONE, "done"), M.aiPlan(NONE)]);
  assert.ok(!/ollama/i.test(shown), shown);
});

// ---------- Remove ----------
ok("Remove: a button on each installed row (in use too), off while another model downloads", () => {
  const calls = [];
  const t = M.buildTable(table([{ state: "in_use" }, { state: "installed" }, { state: "too_large" }]), h, (a, r) => calls.push([a, r.model]));
  const removes = all(t, (e) => e.tag === "button" && e.props["data-action"] === "remove");
  assert.deepStrictEqual(removes.map((b) => [text(b), b.props["data-model"]]), [["Remove", "small:4b"], ["Remove", "medium:12b"]]);
  removes[1].props.onclick();
  assert.deepStrictEqual(calls, [["remove", "medium:12b"]]);
  assert.strictEqual(M.rowView({ ...ROWS[0], state: "installed" }, "running", true).removeDisabled, true);
  assert.strictEqual(M.rowView({ ...ROWS[0], state: "installed" }, "stopped").removeDisabled, true);
  assert.strictEqual(M.rowView({ ...ROWS[0], state: "not_installed" }, "running").remove, undefined);
});
ok("Remove: the confirmation names the model and the space it frees", () => {
  const c = M.removeConfirm({ ...ROWS[0], state: "installed", bytes: 6.1e9, remove_then: null, remove_last: false });
  assert.deepStrictEqual(c, { title: "Remove Gemma 4, 4B?", label: "Remove",
    lines: ["This deletes it from this computer and frees 6.1 GB.", "You can download it again later."] });
});
ok("Remove the model in use: the confirmation says which model answers next", () => {
  const rows = ROWS.map((r) => ({ ...r }));
  const c = M.removeConfirm({ ...ROWS[1], state: "in_use", bytes: 7.2e9, remove_then: "large:26b", remove_last: false }, rows);
  assert.strictEqual(c.lines[1], "It's the model in use, so answers will come from Gemma 4, 26B instead.");
});
ok("Remove the last model: the confirmation says questions get no written answer until a new download", () => {
  const c = M.removeConfirm({ ...ROWS[1], state: "in_use", bytes: 7.2e9, remove_then: null, remove_last: true });
  assert.strictEqual(c.lines[1], "It's the only model on this computer. Until you download one again, questions show the library's documents with no written answer.");
});

console.log(`all ${n} model checks passed`);
