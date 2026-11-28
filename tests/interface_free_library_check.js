// Checks for the free library's cards on the Library page (app/static/free-library.js), run by
// tests/interface_free_library_test.py with node: the four states of the approved mockups (first visit, Customise
// sources, the build in progress, the free library in use), what each button asks app.js to do, and that no price
// shows anywhere. node has no document, so a small stand-in DOM records what render.js's h() builds.
// Run alone: node tests/interface_free_library_check.js app/static/free-library.js app/static/render.js library/free-sources.json
"use strict";
const assert = require("assert");
const fs = require("fs");
const path = require("path");

let n = 0;
const ok = (name, fn) => { fn(); n++; console.log("PASS", name); };

// ---------- a stand-in DOM ----------
class Node {}
class Text extends Node {
  constructor(t) { super(); this.data = String(t); }
  get textContent() { return this.data; }
}
class Element extends Node {
  constructor(tag) { super(); this.tagName = tag.toUpperCase(); this.attrs = new Map(); this.childNodes = []; this.listeners = {}; this.dataset = {}; this.style = {}; }
  set className(v) { this.attrs.set("class", String(v)); }
  get className() { return this.attrs.get("class") || ""; }
  setAttribute(k, v) {
    this.attrs.set(k, String(v));
    if (k.startsWith("data-")) this.dataset[k.slice(5).replace(/-([a-z])/g, (_, c) => c.toUpperCase())] = String(v);
  }
  getAttribute(k) { return this.attrs.has(k) ? this.attrs.get(k) : null; }
  hasAttribute(k) { return this.attrs.has(k); }
  addEventListener(type, fn) { (this.listeners[type] ||= []).push(fn); }
  fire(type) { for (const fn of this.listeners[type] || []) fn({ target: this, preventDefault() {} }); }
  get children() { return this.childNodes.filter((c) => c instanceof Element); }
  append(...kids) { for (const k of kids) this.childNodes.push(k instanceof Node ? k : new Text(k)); }
  get textContent() { return this.childNodes.map((c) => c.textContent).join(""); }
  querySelectorAll(sel) {  // ".class" only
    return all(this).filter((e) => e.className.split(" ").includes(sel.slice(1)));
  }
}
globalThis.Node = Node;
globalThis.document = { createElement: (t) => new Element(t), createDocumentFragment: () => new Element("#fragment") };
globalThis.window = globalThis;
const FL = require(path.resolve(process.argv[2]));
require(path.resolve(process.argv[3]));
const { h } = globalThis.SLRender;
const SOURCES = JSON.parse(fs.readFileSync(path.resolve(process.argv[4]), "utf8")).sources;

const all = (el) => el.children.flatMap((c) => [c, ...all(c)]);
const byClass = (el, cls) => all(el).filter((e) => e.className.split(" ").includes(cls));
const byAction = (el, a) => all(el).filter((e) => e.getAttribute("data-action") === a);
const icon = (name) => { const e = new Element("svg"); e.setAttribute("data-icon", name); return e; };
const PRICE = /\$\s?\d|€\s?\d|£\s?\d|\b\d+\s?(?:USD|EUR|dollars?)\b|\bprice\b|\bcosts?\b|\bbuy\b/i;
const calls = [];
const on = new Proxy({}, { get: (_, name) => (...args) => calls.push([name, ...args]) });
const ctx = { h, icon, on };
const pick = SOURCES.map((s) => ({ key: s.key, title: s.title, from: s.from, size: s.size, licence: s.licence, licence_text: s.licence_text }));
const total = pick.reduce((a, s) => a + (s.size || 0), 0);
const base = { sources: pick, count: pick.length, total_bytes: total, unknown_sizes: 3, free_bytes: 212e9, job: null,
  library_raw: false, has_library: false };
const text = (v) => [v.top, v.body].filter(Boolean).map((e) => e.textContent).join("\n");
const noPrice = (v) => assert.ok(!PRICE.test(text(v)), `a price: ${text(v).match(PRICE)}`);

// ---------- 1. first visit ----------
ok("first visit: no library, nothing running, picker closed: the two cards and the comparison", () => {
  const v = FL.view(base, { picking: false, selected: null }, ctx);
  assert.strictEqual(v.state, "first");
  assert.strictEqual(v.hideLibrary, true);
  assert.strictEqual(v.facts, "There's no library on this computer yet. Pick one way to get it. The guides, the chat and your own files work in the meantime.");
  const cards = all(v.body).filter((e) => e.getAttribute("data-card"));
  assert.deepStrictEqual(cards.map((c) => c.getAttribute("data-card")), ["free", "upgrade"]);
  assert.deepStrictEqual(byClass(v.body, "fl-card-h").map((e) => e.textContent), ["Download the free library", "Get the ready-made library"]);
  const free = cards[0].textContent;
  assert.ok(free.includes("Starholm downloads each source from where it is published (Kiwix and government sites)"));
  assert.ok(free.includes("About 33 GB to download, much of it noise") && free.includes("Several hours to build"));
  assert.ok(free.includes("A lot of the 33 GB is noise"));
  const up = cards[1].textContent;
  assert.ok(up.includes("Included in the upgrade"), "the ready-made card shows its upgrade state");
  assert.ok(up.includes("About 5.8 GB to download") && up.includes("16.1 GB on this computer once unpacked") && !up.includes("hours"), up);
  assert.strictEqual(byClass(v.body, "fl-picker").length, 0, "the picker is closed");
  const raw = byClass(v.body, "fl-raw")[0].textContent;
  const clean = byClass(v.body, "fl-clean")[0].textContent;
  assert.ok(raw.includes("August 2019") && raw.includes("Page 3-4") && raw.includes("CERT Unit 3: Disaster Medical Operations-Part 1"));
  assert.ok(!clean.includes("August 2019") && !clean.includes("Page 3-4") && clean.includes("Step 6: Leave in place until EMS takes over care."));
  noPrice(v);
  assert.ok(!/example\b|fieldguide\./i.test(text(v)), "no placeholder domain");
});
ok("first visit: Download asks app.js for every source; Customise opens the picker; Get the upgrade opens the pricing page, I have a key opens Settings", () => {
  const v = FL.view(base, { picking: false, selected: null }, ctx);
  calls.length = 0;
  byAction(v.body, "download-all")[0].fire("click");
  byAction(v.body, "customise")[0].fire("click");
  byAction(v.body, "upgrade")[0].fire("click");
  byAction(v.body, "have-key")[0].fire("click");
  assert.deepStrictEqual(calls.map((c) => c[0]), ["download", "customise", "buyUpgrade", "openSettings"]);
  assert.strictEqual(calls[0][1].length, 32);
  assert.strictEqual(byAction(v.body, "customise")[0].getAttribute("aria-expanded"), "false");
});

// ---------- 2. Customise sources ----------
ok("customise: each source with its size and licence, the running total, Download with the size", () => {
  const v = FL.view(base, { picking: true, selected: null }, ctx);
  assert.strictEqual(v.state, "customise");
  const rows = byClass(v.body, "fl-pick");
  assert.strictEqual(rows.length, 32);
  const wiki = rows.find((r) => r.getAttribute("data-key") === "wikipedia").textContent;
  assert.ok(wiki.includes("Wikipedia") && wiki.includes("from Kiwix") && wiki.includes("17.1 GB") && wiki.includes("CC BY-SA"), wiki);
  const nhs = rows.find((r) => r.getAttribute("data-key") === "nhs-medicines").textContent;
  assert.ok(nhs.includes("15 MB") && nhs.includes("Open Government Licence"), nhs);
  const wikihow = rows.find((r) => r.getAttribute("data-key") === "wikihow").textContent;
  assert.ok(wikihow.includes("from Hugging Face") && wikihow.includes("619 MB") && wikihow.includes("CC BY-NC-SA"), wikihow);
  const ifixit = rows.find((r) => r.getAttribute("data-key") === "ifixit").textContent;
  assert.ok(ifixit.includes("3.6 GB") && ifixit.includes("CC BY-NC-SA"), ifixit);
  const aid = rows.find((r) => r.getAttribute("data-key") === "first-aid-guides").textContent;
  assert.ok(aid.includes("from IFRC and WHO") && aid.includes("size not known") && aid.includes("Non-commercial"), aid);
  const manuals = rows.find((r) => r.getAttribute("data-key") === "survival-manuals").textContent;
  assert.ok(manuals.includes("from government sites") && manuals.includes("size not known") && manuals.includes("Public domain"), manuals);
  assert.strictEqual(all(v.body).find((e) => e.getAttribute("id") === "fl-total").textContent,
    "32 sources · 33.0 GB selected and 3 of unknown size · each keeps its own licence");
  assert.strictEqual(byAction(v.body, "download-chosen")[0].textContent, "Download 33 GB");
  assert.ok(byClass(v.body, "fl-space")[0].textContent.includes("212 GB free on this computer"));
  assert.strictEqual(byAction(v.body, "customise")[0].getAttribute("aria-expanded"), "true");
  noPrice(v);
});
ok("customise: a smaller choice updates the total; ticking a row and Cancel go to app.js", () => {
  const v = FL.view(base, { picking: true, selected: new Set(["wikipedia", "knots"]) }, ctx);
  assert.strictEqual(all(v.body).find((e) => e.getAttribute("id") === "fl-total").textContent,
    "2 sources · 17.2 GB selected · each keeps its own licence");
  const btn = byAction(v.body, "download-chosen")[0];
  assert.strictEqual(btn.textContent, "Download 17 GB");
  calls.length = 0;
  btn.fire("click");
  all(v.body).find((e) => e.getAttribute("data-key") === "water").children[0].fire("change");
  byAction(v.body, "cancel-pick")[0].fire("click");
  byAction(v.body, "select-none")[0].fire("click");
  assert.deepStrictEqual(calls, [["download", ["wikipedia", "knots"]], ["toggle", "water"], ["cancelPick"], ["selectNone"]]);
  const none = FL.view(base, { picking: true, selected: new Set() }, ctx);
  assert.ok(byAction(none.body, "download-chosen")[0].hasAttribute("disabled"), "nothing chosen: Download is off");
  assert.strictEqual(byAction(none.body, "select-all").length, 1);
});

// ---------- 3. the build in progress ----------
const sourcesState = Object.fromEntries(pick.map((s, i) => [s.key, { status: i < 9 ? "done" : i === 9 ? "downloading" : "waiting", done: 0, size: s.size }]));
const job = { phase: "downloading", running: true, free_bytes: 212e9, count: 29, done_count: 9, total_bytes: 28.8e9,
  done_bytes: 11.6e9, now: "wikipedia", eta_seconds: 4 * 3600, sources: sourcesState, problems: [] };
ok("building: the steps, the progress, Pause and Cancel, and the pointer to the ready-made library", () => {
  const v = FL.view({ ...base, job }, null, ctx);
  assert.strictEqual(v.state, "building");
  assert.strictEqual(v.facts, "Building the free library");
  assert.strictEqual(v.hideLibrary, true);
  const t = v.body.textContent;
  assert.ok(t.includes("About 4 hours left"));
  const steps = byClass(v.body, "fl-steps")[0].children;
  assert.deepStrictEqual(steps.map((s) => s.className), ["done", "active", "", ""]);
  assert.ok(steps[0].textContent.includes("Checked free space: 212 GB free"));
  assert.ok(steps[1].textContent.startsWith("Downloading sources · 9 of 29 · 11.6 GB of 28.8 GB"), steps[1].textContent);
  assert.ok(steps[1].textContent.includes("Now: Wikipedia from Kiwix"));
  assert.strictEqual(byClass(v.body, "fl-pbar")[0].getAttribute("aria-valuenow"), "40");
  assert.deepStrictEqual([steps[2].textContent, steps[3].textContent], ["Building the search index", "Checking the library"]);
  assert.ok(t.includes("You can close this page or quit Starholm; the build picks up where it stopped."));
  assert.ok(byClass(v.body, "fl-slim")[0].textContent.includes("Want to skip the wait? The ready-made library is one download."));
  calls.length = 0;
  byAction(v.body, "pause")[0].fire("click");
  byAction(v.body, "cancel")[0].fire("click");
  assert.deepStrictEqual(calls.map((c) => c[0]), ["pause", "cancel"]);
  FL.sizeBars(v.body);
  assert.strictEqual(byClass(v.body, "fl-pct")[0].style.width, "40%");
  noPrice(v);
});
ok("building: paused shows Resume; a failed source shows why, with Retry and Leave it out", () => {
  const paused = FL.view({ ...base, job: { ...job, phase: "paused", running: false, now: null } }, null, ctx);
  assert.ok(paused.body.textContent.includes("Paused"));
  assert.strictEqual(byAction(paused.body, "resume").length, 1);
  assert.strictEqual(byAction(paused.body, "pause").length, 0);
  const failedSources = { ...sourcesState, water: { status: "failed", error: "the SHA-256 does not match the one published" } };
  const failed = FL.view({ ...base, job: { ...job, phase: "failed", running: false, now: null, sources: failedSources } }, null, ctx);
  const item = byClass(failed.body, "fl-failed")[0].children[0];
  assert.ok(item.textContent.includes("Water Treatment Library: the SHA-256 does not match the one published"), item.textContent);
  calls.length = 0;
  byAction(item, "retry")[0].fire("click");
  byAction(item, "leave-out")[0].fire("click");
  assert.deepStrictEqual(calls, [["retry", "water"], ["leaveOut", "water"]]);
  const broken = FL.view({ ...base, job: { ...job, phase: "build-failed", problems: ["no results for the check search 'x'"] } }, null, ctx);
  assert.ok(broken.body.textContent.includes("The library didn't pass its check") && broken.body.textContent.includes("no results for the check search"));
  assert.strictEqual(byAction(broken.body, "resume")[0].textContent, "Build again");
  const withLib = FL.view({ ...base, has_library: true, job }, null, ctx);
  assert.strictEqual(withLib.hideLibrary, false, "a library already here stays usable under the progress");
  assert.strictEqual(withLib.facts, null);
});

// ---------- 4. the free library in use ----------
ok("free library done: a slim reminder above the library, which can be hidden", () => {
  const model = { ...base, has_library: true, library_raw: true, job: { phase: "done", sources: {}, banner_hidden: false } };
  const v = FL.view(model, null, ctx);
  assert.strictEqual(v.state, "done");
  assert.strictEqual(v.hideLibrary, false);
  assert.strictEqual(v.body, null);
  assert.ok(v.top.textContent.startsWith("You're using the free library, built without the cleaning rules. The ready-made library is cleaner and smaller."));
  calls.length = 0;
  byAction(v.top, "upgrade")[0].fire("click");
  byAction(v.top, "hide-banner")[0].fire("click");
  assert.deepStrictEqual(calls.map((c) => c[0]), ["buyUpgrade", "hideBanner"]);
  noPrice(v);
  const hidden = FL.view({ ...model, job: { ...model.job, banner_hidden: true } }, null, ctx);
  assert.strictEqual(hidden.top, null);
  const noJob = FL.view({ ...model, job: null }, null, ctx);
  assert.ok(noJob.top, "a free build with no job record still shows the reminder");
});
ok("a ready-made library with nothing running adds nothing to the page", () => {
  const v = FL.view({ ...base, has_library: true }, null, ctx);
  assert.deepStrictEqual([v.state, v.top, v.body, v.hideLibrary, v.facts], ["none", null, null, false, null]);
});
ok("the module itself holds no price and makes no network call", () => {
  const src = fs.readFileSync(path.resolve(process.argv[2]), "utf8");
  assert.ok(!PRICE.test(src.replace(/^\s*\/\/.*$/gm, "")), "a price in free-library.js");
  assert.ok(!/fetch\(|XMLHttpRequest|innerHTML|outerHTML|insertAdjacentHTML/.test(src));
});

// ---------- the AI too (ctx.ai from models.js aiPlan and aiLine) ----------
ok("the AI too: with the AI to set up, the free card says its download sets it up as well; ready, nothing changes", () => {
  const plain = FL.view(base, { picking: false, selected: null }, ctx);
  const same = FL.view(base, { picking: false, selected: null }, { ...ctx, ai: { plan: "", line: null } });
  assert.strictEqual(text(same), text(plain), "the AI ready, or Library only: the card as before");
  const v = FL.view(base, { picking: false, selected: null }, { ...ctx, ai: { plan: "the AI engine (231 MB) and the model Gemma 4, 12B (7.2 GB)", line: null } });
  const free = all(v.body).find((e) => e.getAttribute("data-card") === "free");
  assert.ok(free.textContent.includes("It also sets up the AI for this computer: the AI engine (231 MB) and the model Gemma 4, 12B (7.2 GB)."), free.textContent);
  assert.ok(!/ollama/i.test(text(v)));
});
ok("the AI too: while the library builds, the AI's line links to Settings › Model; ready says so with no link", () => {
  const v = FL.view({ ...base, job }, null, { ...ctx, ai: { plan: "", line: { kind: "running", text: "Downloading the AI engine… 40% of 405 MB" } } });
  const line = all(v.body).find((e) => e.getAttribute("data-role") === "ai");
  assert.ok(line.textContent.startsWith("Downloading the AI engine… 40% of 405 MB"), line.textContent);
  calls.length = 0;
  byAction(v.body, "open-model")[0].fire("click");
  assert.deepStrictEqual(calls, [["openModel"]]);
  const ready = FL.view({ ...base, job }, null, { ...ctx, ai: { plan: "", line: { kind: "ready", text: "The AI is ready." } } });
  assert.ok(ready.body.textContent.includes("The AI is ready.") && byAction(ready.body, "open-model").length === 0);
  const none = FL.view({ ...base, job }, null, { ...ctx, ai: { plan: "", line: null } });
  assert.strictEqual(all(none.body).filter((e) => e.getAttribute("data-role") === "ai").length, 0);
});

console.log(`${n} free library interface checks passed`);
