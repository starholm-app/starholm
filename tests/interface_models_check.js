// Checks for Settings › Model and library-only mode in the interface, run by tests/interface_models_test.py with node:
// app/static/models.js draws each state of the recommended-model table (in use, installed, downloading with progress
// and Cancel, too large for this computer, not installed with "Download with Ollama", the recommended row marked),
// the Ollama row (running, stopped, starting, missing), and the no-model state (the start screen's note and the line
// under a question). node has no document, so a small stand-in h() records what buildTable makes.
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
ok("not installed: a Download with Ollama button, primary on the recommended row", () => {
  const v = M.rowView({ ...ROWS[1], state: "not_installed", recommended: true }, "running");
  assert.deepStrictEqual([v.action, v.label, v.primary, v.disabled], ["pull", "Download with Ollama", true, false]);
  assert.strictEqual(M.rowView({ ...ROWS[0], state: "not_installed" }, "running").primary, false);
});
ok("not installed with Ollama not running: the button is off and says why", () => {
  const v = M.rowView({ ...ROWS[1], state: "not_installed" }, "stopped");
  assert.strictEqual(v.disabled, true);
  assert.strictEqual(v.detail, "Needs Ollama running.");
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
  assert.deepStrictEqual(rows.map((r) => text(r.kids[3])), ["In useRemove", "Download with Ollama", "Too large for this computer"]);
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

// ---------- Ollama ----------
ok("Ollama: running, starting, stopped with Start, missing with Check again", () => {
  assert.deepStrictEqual([M.ollamaView("running").text, M.ollamaView("running").action], ["Installed and running", null]);
  assert.strictEqual(M.ollamaView("starting").text, "Starting…");
  assert.deepStrictEqual([M.ollamaView("stopped").action, M.ollamaView("stopped").label], ["start", "Start Ollama"]);
  const missing = M.ollamaView("missing");
  assert.deepStrictEqual([missing.action, missing.label], ["check", "Check again"]);
  assert.ok(missing.text.startsWith("Not found."));
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

ok("Set up the AI: before anything downloads, it says what, from where, both sizes, and that no account is needed", () => {
  const calls = [];
  const panel = M.buildSetup(missing({}), h, (a) => calls.push(a), () => {});
  const words = text(panel);
  assert.strictEqual(words.split("Set up the AI")[0],
    "One click downloads the latest stable release of Ollama, the free open-source program that runs the AI on this "
    + "computer, from Ollama's official GitHub page (231 MB). Then it downloads the model, Gemma 4, 12B (7.2 GB). You don't need an account, and "
    + "everything stays on this computer.");
  const buttons = all(panel, (e) => e.tag === "button");
  assert.deepStrictEqual(buttons.map(text), ["Set up the AI"]);
  assert.strictEqual(all(panel, (e) => e.tag === "progress").length, 0);
  assert.deepStrictEqual(calls, [], "nothing starts until the button is pressed");
  buttons[0].props.onclick();
  assert.deepStrictEqual(calls, ["setup"]);
});
ok("Set up the AI: Windows' engine size reads in GB, and with no model that fits it says to pick one", () => {
  assert.ok(M.setupLead({ ...OFFER, engine_bytes: 1857946272 })[0].endsWith("GitHub page (1.9 GB)."));
  assert.ok(M.setupLead({ ...OFFER, engine_bytes: null })[0].endsWith("Ollama's official GitHub page."),
    "before GitHub's API answers: no size, no made-up number");
  assert.strictEqual(M.pollDelay(missing({}, { setup_offer: { ...OFFER, engine_bytes: null } })), 3000,
    "asks again until the size is known");
  assert.strictEqual(M.setupLead({ engine_bytes: 230778782, model: null })[1], "Then you pick a model in the table below.");
});
ok("Set up the AI: running shows the step, the download's progress and size, and Cancel", () => {
  const calls = [];
  const dl = M.buildSetup(missing({ state: "running", step: "download", completed: 115389391, total: 230778782 }), h,
    (a) => calls.push(a), () => {});
  assert.ok(text(dl).startsWith("Downloading Ollama… 50% of 231 MB"), text(dl));
  assert.strictEqual(all(dl, (e) => e.tag === "progress")[0].props.value, "0.5");
  const cancel = all(dl, (e) => e.tag === "button");
  assert.deepStrictEqual(cancel.map(text), ["Cancel"]);
  cancel[0].props.onclick();
  assert.deepStrictEqual(calls, ["cancel"]);
  for (const [step, words] of [["check", "Checking the download…"], ["install", "Installing Ollama…"],
    ["start", "Starting Ollama…"], ["model", "Starting the model download…"]]) {
    const v = M.setupView(missing({ state: "running", step }));
    assert.deepStrictEqual([v.text, v.progress, v.action], [words, null, "cancel"]);
  }
  assert.strictEqual(M.pollDelay(missing({ state: "running", step: "install" })), 1000);
});
ok("Set up the AI: a failed step says what failed, links to ollama.com/download and offers Try again", () => {
  const calls = [];
  const opened = [];
  const panel = M.buildSetup(missing({ state: "error", failed: "check",
    message: "The downloaded file didn't match the one Ollama published, so it was deleted and nothing was installed." }),
  h, (a) => calls.push(a), (url) => opened.push(url));
  const words = text(panel);
  assert.ok(words.startsWith("The downloaded file didn't match the one Ollama published"), words);
  assert.ok(words.includes(M.MANUAL_LINE));
  const buttons = all(panel, (e) => e.tag === "button");
  assert.deepStrictEqual(buttons.map(text), ["Try again", "Open ollama.com/download"]);
  buttons.forEach((b) => b.props.onclick());
  assert.deepStrictEqual([calls, opened], [["retry"], ["https://ollama.com/download"]]);
});
ok("Set up the AI: shown only while Ollama is missing or the setup runs or failed", () => {
  assert.strictEqual(M.setupView({ ...missing({}), ollama: "running" }), null);
  assert.strictEqual(M.setupView({ ...missing({ state: "done" }), ollama: "running" }), null);
  assert.strictEqual(M.setupView(missing({ state: "cancelled" })).note, "Cancelled.");
  assert.strictEqual(M.setupView(missing({}, { setup_offer: { available: false, engine_bytes: null } })).action, null,
    "no automatic setup for this computer: only the manual route");
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
