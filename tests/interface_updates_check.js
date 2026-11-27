// Checks for Starholm's own update views (app/static/updates.js), run by tests/interface_updates_test.py with node:
// the Updates rows on Settings › Status in each state (never checked, checking, up to date, downloading, checking
// the download, ready, an error, newer packs), the switch, the ready notice (the same on the Mac and on Windows), the manual
// way, and what each button asks app.js to do. node has no document, so a small stand-in DOM records what render.js's
// h() builds.
// Run alone: node tests/interface_updates_check.js app/static/updates.js app/static/render.js app/static/upgrade.js
"use strict";
const assert = require("assert");
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
  fire(type) { for (const fn of this.listeners[type] || []) fn({ target: this, currentTarget: this, preventDefault() {} }); }
  get children() { return this.childNodes.filter((c) => c instanceof Element); }
  append(...kids) { for (const k of kids) this.childNodes.push(k instanceof Node ? k : new Text(k)); }
  get textContent() { return this.childNodes.map((c) => c.textContent).join(""); }
  querySelectorAll(sel) { return all(this).filter((e) => e.className.split(" ").includes(sel.slice(1))); }
}
globalThis.Node = Node;
globalThis.document = { createElement: (t) => new Element(t), createDocumentFragment: () => new Element("#fragment") };
globalThis.window = globalThis;
const UV = require(path.resolve(process.argv[2]));
require(path.resolve(process.argv[3]));
const UP = require(path.resolve(process.argv[4]));
const { h } = globalThis.SLRender;

const all = (el) => el.children.flatMap((c) => [c, ...all(c)]);
const byAction = (el, a) => all(el).filter((e) => e.getAttribute("data-action") === a);
const icon = (name) => { const e = new Element("svg"); e.setAttribute("data-icon", name); return e; };
let calls = [];
const on = new Proxy({}, { get: (_, name) => (...args) => calls.push([name, ...args]) });
const ctx = { h, icon, on };
const wrap = (kids) => { const d = new Element("div"); d.append(...kids); return d; };
const text = (el) => el.textContent;
const click = (el, action) => { const b = byAction(el, action); assert.strictEqual(b.length, 1, `one ${action} button`); calls = []; b[0].fire("click"); return calls; };
const EM_DASH = /\u2014/;

const app = (over) => ({ version: "1.0.2", platform: "mac", auto: false, phase: "idle", checked: null, latest: null,
  done: 0, bytes: 0, error: null, ready: null, last_restart: null, ...over });
const st = (a, packs = null) => ({ app: app(a), packs, unlocked: !!packs, pack_job: false });

ok("never checked: the version, Check for updates, the switch off, and no line", () => {
  const el = wrap(UV.rows(st({}), {}, ctx));
  assert.ok(text(el).includes("Version 1.0.2"));
  assert.ok(text(el).includes("Check for updates") && text(el).includes("Download updates automatically"));
  assert.strictEqual(byAction(el, "auto")[0].getAttribute("aria-checked"), "false");
  assert.strictEqual(UV.statusLine(st({})).text, "");
  assert.deepStrictEqual(click(el, "check"), [["check"]]);
});
ok("the switch says what it does and asks app.js to turn it on, then off", () => {
  const off = wrap(UV.rows(st({}), {}, ctx));
  assert.ok(text(off).includes("it goes online for updates only when you press Check for updates"));
  assert.deepStrictEqual(click(off, "auto"), [["auto", true]]);
  const onEl = wrap(UV.rows(st({ auto: true }), {}, ctx));
  assert.strictEqual(byAction(onEl, "auto")[0].getAttribute("aria-checked"), "true");
  assert.deepStrictEqual(click(onEl, "auto"), [["auto", false]]);
});
ok("checking: the button is busy", () => {
  const el = wrap(UV.rows(st({}), { checking: true }, ctx));
  assert.ok(byAction(el, "check")[0].hasAttribute("disabled"));
  assert.strictEqual(UV.statusLine(st({}), { checking: true }).text, "Checking…");
});
ok("Check for updates keeps one size: each state's label sits over a hidden copy of the other (app.css .swap)", () => {
  for (const [checking, shown, hidden] of [[false, "Check for updates", "Checking…"], [true, "Checking…", "Check for updates"]]) {
    const button = byAction(wrap(UV.rows(st({}), { checking }, ctx)), "check")[0];
    const swap = button.querySelectorAll(".swap");
    assert.strictEqual(swap.length, 1, "one .swap label");
    assert.strictEqual(swap[0].textContent, shown);
    assert.strictEqual(swap[0].getAttribute("data-alt"), hidden);
    assert.strictEqual(button.textContent, shown, "the hidden label isn't in the text");
  }
});
ok("up to date, with and without the packs", () => {
  assert.strictEqual(UV.statusLine(st({ checked: 1 })).text, "Starholm is up to date.");
  assert.strictEqual(UV.statusLine(st({ checked: 1 }, { newer: false, packs: [] })).text, "Starholm, the guides and the library are up to date.");
});
ok("downloading shows the version and how much is in", () => {
  const line = UV.statusLine(st({ phase: "downloading", latest: { version: "1.0.3" }, done: 45e6, bytes: 210e6 }));
  assert.strictEqual(line.text, "Downloading Starholm 1.0.3 · 45 MB of 210 MB");
  assert.strictEqual(UV.statusLine(st({ phase: "unpacking" })).text, "Checking the download…");
});
ok("ready: the line offers Restart now, which opens the notice", () => {
  const s = st({ phase: "ready", ready: { version: "1.0.3", notes: "Faster downloads.", install: "replace" } });
  assert.strictEqual(UV.statusLine(s).text, "Starholm 1.0.3 is ready to install.");
  const el = wrap(UV.rows(s, {}, ctx));
  assert.deepStrictEqual(click(el, "show-notice"), [["restart"]]);
});
ok("an error is shown as an alert", () => {
  const s = st({ phase: "error", error: "Starholm couldn't reach the update server. Check the internet connection and try again." });
  const line = UV.statusLine(s);
  assert.ok(line.error && line.text.startsWith("Starholm couldn't reach"));
  assert.strictEqual(all(wrap(UV.rows(s, {}, ctx))).find((e) => e.getAttribute("data-role") === "updates-msg").getAttribute("role"), "alert");
});
ok("newer packs send the person to the Upgrade page", () => {
  const s = st({ checked: 1 }, { newer: true, packs: [{ pack: "library", newer: true }] });
  assert.ok(UV.statusLine(s).text.includes("Update them on the Upgrade page."));
  assert.deepStrictEqual(click(wrap(UV.rows(s, {}, ctx)), "open-upgrade"), [["upgrade"]]);
});
ok("the notice (Mac): one line and two buttons, with no notes, subtitle or restart explanation", () => {
  const el = UV.notice({ version: "1.0.5", notes: "Library downloads are faster.", install: "replace" }, { packsRunning: true }, ctx);
  assert.strictEqual(text(el), "A new version of Starholm is ready. Restart to update.LaterRestart now");
  assert.ok(!all(el).some((e) => ["notes", "packs-note"].includes(e.getAttribute("data-role")) || e.getAttribute("id") === "update-sub"));
  assert.deepStrictEqual(click(el, "restart"), [["restart"]]);
  assert.deepStrictEqual(click(el, "later"), [["later"]]);
  const order = byAction(el, "later").concat(byAction(el, "restart"));
  assert.ok(order[1].className.includes("primary") && !order[0].className.includes("primary"), "Restart now is the main button");
});
ok("the notice on Windows is the same: ready, with Restart now (the installer runs by itself)", () => {
  const s = st({ platform: "win", checked: 1, phase: "ready", ready: { version: "1.0.3", notes: "x", install: "replace" } });
  assert.strictEqual(UV.statusLine(s).text, "Starholm 1.0.3 is ready to install.");
  const el = UV.notice(s.app.ready, {}, ctx);
  assert.ok(text(el).includes("A new version of Starholm is ready.") && text(el).includes("Restart now") && !text(el).includes("browser"));
  assert.deepStrictEqual(click(el, "restart"), [["restart"]]);
});
ok("the card's title is what aria-labelledby names, and the announcement says where it is", () => {
  const el = UV.notice({ version: "1.0.4", notes: "", install: "replace" }, {}, ctx);
  assert.ok(all(el).some((e) => e.getAttribute("id") === "update-title" && text(e) === "A new version of Starholm is ready. Restart to update."));
  assert.strictEqual(UV.announce({ version: "1.0.4" }), "Starholm 1.0.4 is ready to install. Restart now or Later, in the left panel above Settings.");
});
ok("the automatic switch's hint says it checks every hour and tells you when a version is ready", () => {
  assert.ok(UV.AUTO_HINT.includes("every hour") && UV.AUTO_HINT.includes("tells you when one is ready"));
});
ok("the manual way says why and what to do, with OK", () => {
  const el = UV.manual({ mode: "manual", why: "Starholm can't write to the folder it's in, so it can't replace itself.", steps: "Quit Starholm, then drag the new Starholm into Applications and choose Replace." }, ctx);
  assert.ok(text(el).includes("Install the update yourself") && text(el).includes("drag the new Starholm"));
  assert.deepStrictEqual(click(el, "close"), [["close"]]);
});
ok("after a restart: one line for success, one for a failure", () => {
  assert.strictEqual(UV.restartResult({ ok: true, version: "1.0.3" }), "Starholm was updated to version 1.0.3.");
  assert.ok(UV.restartResult({ ok: false, version: "1.0.2", error: "x" }).startsWith("The update couldn't be installed, so Starholm 1.0.2 is still in use."));
  assert.strictEqual(UV.restartResult(null), null);
});
ok("no em dash in any text the views show", () => {
  for (const el of [wrap(UV.rows(st({ checked: 1 }), {}, ctx)), UV.notice({ version: "1.0.3", notes: "", install: "replace" }, { packsRunning: true }, ctx)]) {
    assert.ok(!EM_DASH.test(text(el)), text(el));
  }
  assert.ok(!EM_DASH.test(UV.AUTO_HINT + UV.CHECK_HINT));
});
ok("the Upgrade page's time left: past three days it says so instead of a number of hours", () => {
  assert.strictEqual(UP.duration(3 * 24 * 3600), "more than 3 days");
  assert.strictEqual(UP.duration(1006 * 3600), "more than 3 days");
  assert.strictEqual(UP.duration(10_992), "3 hours");
});
console.log(`${n} update view checks passed`);
