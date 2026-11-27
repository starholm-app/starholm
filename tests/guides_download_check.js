// Checks for the guide that a valid key unlocks but that isn't downloaded yet, run by tests/guides_locked_test.py
// with node: app/static/upgrade.js downloadGuide() draws its own dialog (the guide's title and summary, "Download now"
// in the Upgrade page's words, a Download now button that opens the Upgrade page) and never the lock's "Get the
// upgrade". node has no document, so a small stand-in h() records what it makes.
// Run alone: node tests/guides_download_check.js app/static/upgrade.js
"use strict";
const assert = require("assert");
const path = require("path");

let n = 0;
const ok = (name, fn) => { fn(); n++; console.log("PASS", name); };

function h(tag, props, ...kids) {
  return { tag, props: props || {}, kids: kids.flat(Infinity).filter((k) => k != null && k !== false) };
}
const text = (el) => (typeof el === "string" ? el : el.kids.map(text).join(""));
const all = (el, test, out = []) => {
  if (typeof el === "string") return out;
  if (test(el)) out.push(el);
  el.kids.forEach((k) => all(k, test, out));
  return out;
};

require(path.resolve(process.argv[2] || "app/static/upgrade.js"));
const UP = globalThis.SLUpgrade;
const calls = [];
const ctx = { h, icon: (name) => h("i", { "data-icon": name }),
  on: { notNow: () => calls.push("not-now"), getUpgrade: () => calls.push("pricing-page"), openUpgrade: () => calls.push("upgrade-page") } };
const preview = { slug: "nuclear-incident", title: "Nuclear incident", summary: "Get inside, stay inside.",
  group: "Disasters", headings: [] };
const el = UP.downloadGuide(preview, ctx);

ok("the dialog is its own state, not the lock", () => {
  assert.strictEqual(el.props["data-lock"], "download");
  assert.ok(!text(el).includes(UP.LOCKED) && !text(el).includes("Get the upgrade"), text(el));
});
ok("it names the guide from the index entry", () => {
  assert.ok(text(el).includes("Nuclear incident") && text(el).includes("Get inside, stay inside."));
});
ok("it says Download now, in the Upgrade page's words", () => {
  assert.ok(text(el).includes("Download now") && text(el).includes("Nothing downloads until you press the button."));
});
ok("its Download now button opens the Upgrade page; Not now closes", () => {
  const [btn] = all(el, (e) => e.tag === "button" && e.props["data-action"] === "download");
  assert.ok(btn && text(btn).includes("Download now"));
  btn.props.onclick();
  all(el, (e) => e.props["data-action"] === "not-now")[0].props.onclick();
  assert.deepStrictEqual(calls, ["upgrade-page", "not-now"]);
});
ok("the locked preview still offers Get the upgrade (the no-key state is unchanged)", () => {
  const locked = UP.lockedGuide(preview, ctx);
  assert.ok(text(locked).includes("Get the upgrade") && text(locked).includes(UP.LOCKED));
});
console.log(`all ${n} download dialog checks passed`);
