// Checks for the Markdown renderer in app/static/render.js (md()), run by tests/interface_markdown_test.py with node.
// node has no document, so a small stand-in DOM below records what md() builds; the checks read it back as tags,
// attributes and text. Also checks the swipe-back decision and the empty-document test, which use no DOM.
// Run alone: node tests/interface_markdown_check.js app/static/render.js
"use strict";
const assert = require("assert");
const path = require("path");

// ---------- a stand-in DOM: elements, text, fragments, attributes, listeners ----------
class Node {}
class Text extends Node {
  constructor(t) { super(); this.data = String(t); this.parentElement = null; }
  get textContent() { return this.data; }
}
class Element extends Node {
  constructor(tag) {
    super();
    this.tagName = tag.toUpperCase();
    this.attrs = new Map();
    this.childNodes = [];
    this.parentElement = null;
    this.listeners = {};
    const el = this;
    this.dataset = new Proxy({}, {
      set(_, k, v) { el.attrs.set(`data-${k.replace(/[A-Z]/g, (c) => `-${c.toLowerCase()}`)}`, String(v)); return true; },
      get(_, k) { return el.attrs.get(`data-${String(k).replace(/[A-Z]/g, (c) => `-${c.toLowerCase()}`)}`); },
    });
  }
  set className(v) { this.attrs.set("class", String(v)); }
  get className() { return this.attrs.get("class") || ""; }
  get id() { return this.attrs.get("id") || ""; }
  setAttribute(k, v) { this.attrs.set(k, String(v)); }
  getAttribute(k) { return this.attrs.has(k) ? this.attrs.get(k) : null; }
  addEventListener(type, fn) { (this.listeners[type] ||= []).push(fn); }
  get children() { return this.childNodes.filter((c) => c instanceof Element); }
  append(...kids) {
    for (const k of kids) {
      if (k instanceof Fragment) { for (const c of k.childNodes) { c.parentElement = this; this.childNodes.push(c); } k.childNodes = []; }
      else { const n = k instanceof Node ? k : new Text(k); n.parentElement = this; this.childNodes.push(n); }
    }
  }
  set textContent(v) { this.childNodes = [new Text(v)]; }
  get textContent() { return this.childNodes.map((c) => c.textContent).join(""); }
  querySelector(sel) {
    const m = /^\[id="(.*)"\]$/.exec(sel);
    const walk = (el) => { for (const c of el.children) { if (c.id === m[1]) return c; const f = walk(c); if (f) return f; } return null; };
    return walk(this);
  }
}
class Fragment extends Element { constructor() { super("#fragment"); } }
globalThis.Node = Node;
globalThis.document = { createElement: (t) => new Element(t), createDocumentFragment: () => new Fragment() };
require(path.resolve(process.argv[2]));
const R = globalThis.SLRender;

// ---------- reading back what md() built ----------
const all = (el) => el.children.flatMap((c) => [c, ...all(c)]);
const esc = (s) => s.replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;");
const html = (n) => (n instanceof Text ? esc(n.data)
  : n instanceof Fragment ? n.childNodes.map(html).join("")
    : `<${n.tagName.toLowerCase()}${[...n.attrs].map(([k, v]) => ` ${k}="${v}"`).join("")}>${n.childNodes.map(html).join("")}</${n.tagName.toLowerCase()}>`);
function render(src, opts) {
  const root = new Element("article");
  root.setAttribute("data-md-root", "");  // as reading mode's column is
  root.append(R.md(src, opts));
  return root;
}
const find = (root, tag) => all(root).filter((e) => e.tagName === tag.toUpperCase());

let n = 0;
const ok = (name, fn) => { fn(); n++; console.log("PASS", name); };

// ---------- heading ids and the outline ----------
ok("every heading gets an id from its text", () => {
  const root = render("# Water storage\n\nText.\n\n## How long it keeps\n\n### Bleach: 8 drops!");
  assert.deepStrictEqual(find(root, "h1").map((e) => e.id), ["h-water-storage"]);
  assert.deepStrictEqual(find(root, "h2").map((e) => e.id), ["h-how-long-it-keeps"]);
  assert.deepStrictEqual(find(root, "h3").map((e) => e.id), ["h-bleach-8-drops"]);
});
ok("two headings with the same text get different ids, also across the parts of one document", () => {
  const ids = new Set();
  const a = render("## Steps\n\n## Steps", { ids });
  const b = render("## Steps", { ids });
  assert.deepStrictEqual([...find(a, "h2"), ...find(b, "h2")].map((e) => e.id), ["h-steps", "h-steps-1", "h-steps-2"]);
});
ok("ids are the same each time the document is drawn", () => {
  const src = "## A\n\n## B\n\n## A";
  assert.strictEqual(html(render(src)), html(render(src)));
});
ok("the outline lists the headings in order, with their level, id and text", () => {
  const root = render("# One\n\n## Two\n\ntext\n\n### Three **bold**\n\n## Four", { headingShift: 1 });
  assert.deepStrictEqual(R.outline(root), [
    { level: 2, id: "h-one", text: "One" }, { level: 3, id: "h-two", text: "Two" },
    { level: 4, id: "h-three-bold", text: "Three bold" }, { level: 3, id: "h-four", text: "Four" }]);
});
ok("the outline is empty for a document with fewer than three headings", () => {
  assert.deepStrictEqual(R.outline(render("# One\n\n## Two\n\nText.")), []);
});

// ---------- links ----------
ok("[text](#anchor) becomes a jump to the heading, which finds it", () => {
  const root = render("# Fire safety\n\nSee [the fire part](#fire-safety) and [spaced](#Fire%20Safety).");
  const btns = find(root, "button");
  assert.deepStrictEqual(btns.map((b) => b.dataset.anchor), ["h-fire-safety", "h-fire-safety"]);
  assert.strictEqual(btns[0].textContent, "the fire part");
  assert.strictEqual(btns[0].getAttribute("type"), "button");
  assert.ok(btns[0].listeners.click && btns[0].listeners.click.length === 1);
});
ok("a jump finds its heading through the enclosing elements", () => {
  const root = render("# Top\n\nGo [down](#bottom-line).\n\n## Bottom line");
  const target = find(root, "h2")[0];
  let scrolled = null;
  target.scrollIntoView = (o) => { scrolled = o; };
  assert.strictEqual(R.jumpTo(find(root, "button")[0], "h-bottom-line"), target);
  assert.strictEqual(scrolled.block, "start");
});
ok("a jump with no match in its own document does nothing, even if another document on the page has that heading", () => {
  const page = new Element("main");
  const other = render("## Water storage");
  const mine = render("Go to [water](#water-storage).");
  page.append(other, mine);
  find(other, "h2")[0].scrollIntoView = () => { throw new Error("scrolled the other document"); };
  assert.strictEqual(R.jumpTo(find(mine, "button")[0], "h-water-storage"), null);
});
ok("in a chat answer (no data-md-root) a jump stays inside its .md block", () => {
  const page = new Element("main");
  const a = new Element("div"); a.className = "md"; a.append(R.md("## Steps"));
  const b = new Element("div"); b.className = "md"; b.append(R.md("See [steps](#steps)."));
  page.append(a, b);
  assert.strictEqual(R.jumpTo(find(b, "button")[0], "h-steps"), null);
});
ok("web links stay plain text, with no link element and no href", () => {
  const root = render("Read [the CDC page](https://www.cdc.gov/x) or https://example.org/y now.");
  assert.strictEqual(find(root, "a").length, 0);
  assert.strictEqual(find(root, "button").length, 0);
  assert.ok(all(root).every((e) => !e.attrs.has("href")));
  assert.deepStrictEqual(find(root, "span").filter((s) => s.className === "ext-url").map((s) => s.textContent),
    ["https://www.cdc.gov/x", "https://example.org/y"]);
});

// ---------- tables ----------
ok("a table has a header row, and :---: and ---: set each column's alignment", () => {
  const root = render("| Item | Amount | Note |\n|:---|:---:|---:|\n| Water | 4 L | per day |");
  const wrap = find(root, "div")[0];
  assert.strictEqual(wrap.className, "table-wrap");
  assert.deepStrictEqual(find(root, "th").map((c) => [c.textContent, c.className]), [["Item", ""], ["Amount", "al-center"], ["Note", "al-right"]]);
  assert.deepStrictEqual(find(root, "td").map((c) => c.className), ["", "al-center", "al-right"]);
});
ok("a table straight after a line of text still becomes a table", () => {
  const root = render("Non-Vaccine-Preventable Diseases\n| Disease | Advice |\n|---|---|\n| Dengue | Avoid bites |\nStay safe");
  assert.strictEqual(find(root, "table").length, 1);
  assert.strictEqual(find(root, "td").length, 2);
});

// ---------- bare rows, as the library stores most tables (real samples) ----------
// Only library documents and guides pass bareTables; a chat answer keeps bare rows as text.
const doc = (src, o = {}) => render(src, { bareTables: true, ...o });
const cells = (tr) => tr.children.map((c) => c.textContent);
const rowsOf = (t) => find(t, "tr").map(cells);
ok("Wikipedia (Carolina Inn): an infobox becomes a label and value table with no header row", () => {
  const root = doc("Show map of the United States\n\nLocation | 211 Pittsboro St., Chapel Hill, North Carolina\nCoordinates | 35°54′36″N 79°3′18″W\nArea | 4.5 acres (1.8 ha)\nBuilt | 1923 (1923)-1924\nArchitect | Arthur C. Nash; George Watts Carr; et al.\n\nThe Carolina Inn is a hotel listed on the National Register of Historic Places.");
  const t = find(root, "table")[0];
  assert.strictEqual(t.className, "kv");
  assert.strictEqual(find(t, "thead").length, 0);
  assert.deepStrictEqual(rowsOf(t)[0], ["Location", "211 Pittsboro St., Chapel Hill, North Carolina"]);
  assert.strictEqual(find(t, "tr").length, 5);
  assert.strictEqual(find(t, "th")[0].getAttribute("scope"), "row");
  assert.ok(find(root, "p")[1].textContent.startsWith("The Carolina Inn"));
});
ok("Wikipedia (Kanagawa Prefecture): three columns get the first row as header", () => {
  const root = doc("Historical population\n\nYear | Pop. | ±% p.a.\n1890 | 979,756 | —\n1903 | 1,051,433 | +0.54%\n1913 | 1,228,254 | +1.57%");
  assert.deepStrictEqual(find(root, "th").map((c) => c.textContent), ["Year", "Pop.", "±% p.a."]);
  assert.deepStrictEqual(find(find(root, "tbody")[0], "tr").map(cells)[2], ["1913", "1,228,254", "+1.57%"]);
});
ok("Appropedia: a row missing its last cells is padded, not split into another table", () => {
  const root = doc("Tasa Típica De flujo de agua entrando\n\nConstituyente | Concentraciones típicas de influente mg/L | Objetivo de las concentraciones de efluente mg/L\nCarga hidráulica (pulg./dia) | .4-4\nDBO | 5-100 | 5-30\nTSS | 5-100 | 5-30");
  assert.strictEqual(find(root, "table").length, 1);
  assert.deepStrictEqual(rowsOf(find(root, "table")[0])[1], ["Carga hidráulica (pulg./dia)", ".4-4", ""]);
  assert.strictEqual(find(root, "th")[0].textContent, "Constituyente");
});
ok("Stack Exchange (Sustainability): a plain-text table with a ruled line under its header", () => {
  const root = doc("--------------------------------------------------------------------------------\nAppliance | Nom. power | Wh per 24h | Wh a week | Wh a month | kWh a year\n----------------+------------+------------+-----------+------------+------------\nEfficient PIR | 0.1 W | 2.4 Wh | 16.8 Wh | 72 Wh | 0.9 kWh\nWorse PIR | 0.3 W | 7.2 Wh | 50.4 Wh | 216 Wh | 2.6 kWh\nMid light LED | 7 W | 168 Wh | 1.2 kWh | 5 kWh\n--------------------------------------------------------------------------------");
  assert.strictEqual(find(root, "table").length, 1);
  assert.strictEqual(find(root, "th").length, 6);
  assert.strictEqual(find(root, "tbody")[0].children.length, 3);
  assert.ok(!root.textContent.includes("+-----"));
});
ok("a trailing pipe on bare rows is allowed", () => {
  const root = doc("Nuts | 8mm | 6 units |\nButts | 8mm | 6 units |\nScrews | M4 x 30 | 6 units |");
  assert.deepStrictEqual(find(root, "th").map((c) => c.textContent), ["Nuts", "8mm", "6 units"]);
});
ok("a table the library broke apart: the piece after it has no header row", () => {
  const root = doc("Name | Height | Zone\nAbelia | 6 ft | 6\nSun | Moist\nAbutilon | 10 ft | 8\nAcanthus | 4 ft | 7");
  const tables = find(root, "table");
  assert.ok(tables.length >= 1);
  assert.deepStrictEqual(find(root, "th").map((c) => c.textContent), ["Name", "Height", "Zone"]);
  assert.ok(root.textContent.includes("Acanthus"));
});
ok("rows inside a quote make a table inside the quote", () => {
  const q = find(doc("> a | b\n> c | d"), "blockquote")[0];
  assert.strictEqual(find(q, "table").length, 1);
});
ok("not tables: one row alone, a heading, a list item, the Source line, a long line, a sentence", () => {
  const long = `${"word ".repeat(70)}| more | and more`;
  for (const src of ["Country | Soviet Union", "## Fire | Safety\n## Water | Storage", "- a | b\n- c | d",
    "Source: Wikipedia | https://en.wikipedia.org/wiki/X\nSource: Wikipedia | https://en.wikipedia.org/wiki/Y", `${long}\n${long}`,
    "Use a cloth | it works.\nThen boil the water for one minute before drinking it."]) {
    assert.strictEqual(find(doc(src), "table").length, 0, src.slice(0, 30));
  }
});
ok("a chat answer (no bareTables) keeps bare rows as text, as before", () => {
  const root = render("Location | Chapel Hill\nArea | 4.5 acres");
  assert.strictEqual(find(root, "table").length, 0);
  assert.strictEqual(find(root, "p").length, 1);
});
ok("a wide table right after the Source line keeps its header row", () => {
  const root = doc("# Kanagawa\nSource: Wikipedia | https://en.wikipedia.org/wiki/Kanagawa_Prefecture\nYear | Pop. | ±% p.a.\n1890 | 979,756 | —\n1903 | 1,051,433 | +0.54%");
  assert.deepStrictEqual(find(root, "th").map((c) => c.textContent), ["Year", "Pop.", "±% p.a."]);
});
ok("a wide table right after an infobox keeps its header row", () => {
  const root = doc("Country | Japan\nRegion | Kantō\n\nYear | Pop. | ±% p.a.\n1890 | 979,756 | —\n1903 | 1,051,433 | +0.54%");
  assert.deepStrictEqual(find(find(root, "table")[1], "th").map((c) => c.textContent), ["Year", "Pop.", "±% p.a."]);
});
ok("a second real table after some text keeps its header row", () => {
  const root = doc("Kvantitet | Material | Källa\n1 | Treadmill | Shop\n2 | Battery | Shop\n\nThe criteria come next.\n\nKriterier | Tvång | Vikt\nStorlek | Must fit | 3\nFunktion | Must work | 10");
  assert.deepStrictEqual(find(find(root, "table")[1], "th").map((c) => c.textContent), ["Kriterier", "Tvång", "Vikt"]);
});
ok("a real continuation piece of a broken table (after a stray wide row) has no header row", () => {
  const root = doc("Title | Author | Year | Language\nA | Ann | 2001 | English\nB | Bob | 2002 | English\nbroken-off cell text\nGrid | Mozambique | English\nC | Cat | 2003 | Portuguese\nD | Dan | 2004 | Portuguese");
  const tables = find(root, "table");
  assert.strictEqual(find(tables[0], "thead").length, 1);
  assert.strictEqual(find(tables[tables.length - 1], "thead").length, 0);
});
ok("a standard pipe table with its separator row is read as before", () => {
  const root = doc("| Item | Amount |\n|:---|---:|\n| Water | 4 L |\n| Food | 2 kg |");
  assert.strictEqual(find(root, "table")[0].className, "");
  assert.deepStrictEqual(find(root, "th").map((c) => [c.textContent, c.className]), [["Item", ""], ["Amount", "al-right"]]);
  assert.strictEqual(find(root, "td").length, 4);
});

// ---------- task lists ----------
ok("task lists show display-only checkboxes, ticked for [x]", () => {
  const root = render("- [ ] Fill water jugs\n- [x] Check torch batteries\n- plain item");
  const boxes = find(root, "input");
  assert.deepStrictEqual(boxes.map((b) => [b.getAttribute("type"), b.attrs.has("disabled"), b.attrs.has("checked")]),
    [["checkbox", true, false], ["checkbox", true, true]]);
  const lis = find(root, "li");
  assert.deepStrictEqual(lis.map((l) => l.className), ["task", "task", ""]);
  assert.strictEqual(lis[1].textContent, "Check torch batteries");
});
ok("nested lists keep their nesting", () => {
  const root = render("- Water\n  - Boil it\n  - Filter it\n- Food");
  assert.strictEqual(find(root, "ul").length, 2);
  assert.strictEqual(find(root, "li").length, 4);
});

// ---------- callouts ----------
for (const [word, kind] of [["note", "note"], ["tip", "tip"], ["warning", "warning"], ["danger", "danger"], ["CAUTION", "warning"], ["whatever", "note"]]) {
  ok(`> [!${word}] becomes a ${kind} callout`, () => {
    const root = render(`> [!${word}]\n> Keep it **dry**.`);
    const box = find(root, "div").find((d) => d.className.startsWith("callout "));
    assert.strictEqual(box.className, `callout callout-${kind}`);
    assert.strictEqual(find(box, "div")[0].textContent, word[0].toUpperCase() + word.slice(1).toLowerCase());
    assert.strictEqual(find(box, "strong")[0].textContent, "dry");
    assert.strictEqual(find(root, "blockquote").length, 0);
  });
}
for (const word of ["constructor", "toString", "__proto__", "hasOwnProperty", "valueOf"]) {
  ok(`> [!${word}] is a note callout or a plain quote, with nothing odd in any class`, () => {
    const root = render(`> [!${word}]\n> Text.`);
    const box = find(root, "div").find((d) => d.className.startsWith("callout "));
    if (box) assert.strictEqual(box.className, "callout callout-note");
    else assert.strictEqual(find(root, "blockquote").length, 1);  // [!__proto__] is not callout syntax at all
    assert.ok(all(root).every((e) => /^[a-z0-9 -]*$/.test(e.className)));
  });
}
ok("a callout's own title is used, and a plain quote stays a quote", () => {
  const root = render("> [!danger] Carbon monoxide\n> Never run a generator indoors.\n\n> Just a quote.");
  assert.strictEqual(find(root, "div").find((d) => d.className === "callout-title").textContent, "Carbon monoxide");
  assert.strictEqual(find(root, "blockquote").length, 1);
});

// ---------- footnotes ----------
ok("footnotes: numbered references that jump to a list at the end, and back", () => {
  const root = render("Boil it[^boil] and store it[^1].\n\n[^1]: In clean jugs.\n[^boil]: For one minute,\n  three above 2,000 m.");
  const refs = find(root, "sup");
  assert.deepStrictEqual(refs.map((s) => s.textContent), ["1", "2"]);
  const list = find(root, "section")[0];
  assert.strictEqual(list.className, "footnotes");
  const items = find(list, "li");
  assert.deepStrictEqual(items.map((li) => li.id), ["fn-boil", "fn-1"]);
  assert.ok(items[0].textContent.startsWith("For one minute,"));
  assert.ok(items[0].textContent.includes("three above 2,000 m."));
  assert.strictEqual(find(refs[0], "button")[0].dataset.anchor, "fn-boil");
  assert.strictEqual(find(items[0], "button")[0].dataset.anchor, find(refs[0], "button")[0].id);
  assert.ok(!root.textContent.includes("[^1]:"));
});
ok("a footnote reference with no definition stays text, and a definition inside code stays code", () => {
  const root = render("Text[^nope].\n\n```\n[^1]: not a note\n```");
  assert.strictEqual(find(root, "sup").length, 0);
  assert.ok(root.textContent.includes("Text[^nope]."));
  assert.strictEqual(find(root, "code")[0].textContent, "[^1]: not a note");
});

// ---------- images ----------
ok("in a guide, ![alt](name.svg) shows that guide's figure", () => {
  const root = render("![A bowline knot](bowline-steps.svg)", { guide: "knots-basics" });
  const img = find(root, "img")[0];
  assert.strictEqual(img.getAttribute("src"), "/guides/knots-basics/bowline-steps.svg");
  assert.strictEqual(img.getAttribute("alt"), "A bowline knot");
});
ok("outside a guide an image stays text", () => {
  const root = render("![A bowline knot](bowline-steps.svg)");
  assert.strictEqual(find(root, "img").length, 0);
  assert.strictEqual(root.textContent, "[Image: A bowline knot] (bowline-steps.svg)");
});
ok("outside a guide an image's web address still shows, as plain text", () => {
  const root = render("![Map](https://example.org/map.png)");
  assert.strictEqual(find(root, "img").length, 0);
  assert.strictEqual(find(root, "a").length, 0);
  assert.deepStrictEqual(find(root, "span").filter((x) => x.className === "ext-url").map((x) => x.textContent), ["https://example.org/map.png"]);
});
for (const target of ["../secret.svg", "/etc/x.svg", "sub/fig.svg", "fig.png", "FIG.svg", "https://evil.example/x.svg",
  "javascript:alert(1)", "fig.svg?x=1", "data:image/svg+xml,<svg>", "%2e%2e%2ffig.svg"]) {
  ok(`in a guide, the image ${target} stays text`, () => {
    const root = render(`![x](${target})`, { guide: "knots-basics" });
    assert.strictEqual(find(root, "img").length, 0);
  });
}
ok("a guide slug that isn't plain never makes an image", () => {
  for (const guide of ["../x", "a/b", "A", "", null]) assert.strictEqual(find(render("![x](fig.svg)", { guide }), "img").length, 0);
});

// ---------- hostile input never becomes markup or a request ----------
const ALLOWED = new Set(["P", "BR", "STRONG", "EM", "DEL", "CODE", "PRE", "H1", "H2", "H3", "H4", "H5", "H6", "UL", "OL", "LI", "BLOCKQUOTE",
  "HR", "TABLE", "THEAD", "TBODY", "TR", "TH", "TD", "DIV", "SPAN", "BUTTON", "SUP", "SECTION", "INPUT", "IMG"]);
const HOSTILE = [
  "<script>alert(1)</script>",
  "<img src=x onerror=alert(1)>",
  "![x](x.svg\" onerror=\"alert(1))",
  "![x\" onerror=\"alert(1)](fig.svg)",
  "[click](javascript:alert(1))",
  "[click](#\"><script>alert(1)</script>)",
  "# <b onmouseover=alert(1)>Head</b>",
  "> [!note] <iframe src=//evil.example>",
  "| <svg onload=alert(1)> | b |\n|---|---|\n| <a href=//x>y</a> | c |",
  "Text[^1]\n\n[^1]: <img src=x onerror=alert(1)> [x](javascript:alert(1))",
  "![a](../../etc/passwd) ![b](..%2f..%2fx.svg) ![c](//evil.example/x.svg)",
  "- [x] <style>body{display:none}</style>",
];
for (const src of HOSTILE) {
  ok(`hostile input makes no markup or request: ${JSON.stringify(src).slice(0, 50)}`, () => {
    for (const guide of [undefined, "knots-basics"]) {
      const root = render(src, { guide });
      for (const el of all(root)) {
        assert.ok(ALLOWED.has(el.tagName), `unexpected <${el.tagName}>`);
        for (const [k, v] of el.attrs) {
          assert.ok(!/^on/i.test(k) && k !== "href" && k !== "style" && k !== "srcdoc", `attribute ${k}`);
          if (k === "src") assert.match(v, /^\/guides\/[a-z0-9-]+\/[a-z0-9-]+\.svg(?:#print)?$/);  // #print: its print copy
          if (k === "id" || k === "data-anchor") assert.match(v, /^[\p{L}\p{N}-]+$/u);
        }
      }
      assert.ok(find(root, "img").every((i) => ["/guides/knots-basics/fig.svg", "/guides/knots-basics/fig.svg#print"].includes(i.getAttribute("src"))));
    }
  });
}

// ---------- swipe back ----------
const feed = (events) => {
  let state = null;
  let backs = 0;
  for (const ev of events) { const r = R.swipeStep(state, ev); state = r.state; if (r.back) backs++; }
  return backs;
};
const gesture = (dx, dy, count, scrollers = [], t0 = 0) => Array.from({ length: count }, (_, i) => ({ dx, dy, t: t0 + i * 16, scrollers }));
const BACK = R.SWIPE.BACK_SIGN;
ok("a clear horizontal two-finger swipe the back way goes back, once", () => {
  assert.strictEqual(feed(gesture(BACK * 15, 1, 30)), 1);
});
ok("the same swipe the other way does nothing", () => {
  assert.strictEqual(feed(gesture(-BACK * 15, 1, 30)), 0);
});
ok("a mostly vertical scroll never goes back, even with some sideways drift", () => {
  assert.strictEqual(feed(gesture(BACK * 6, 20, 40)), 0);
});
ok("a scroll that starts vertical and drifts sideways stays a scroll", () => {
  assert.strictEqual(feed([...gesture(0, 20, 5), ...gesture(BACK * 30, 0, 20, [], 80)]), 0);
});
ok("over a table that can still scroll that way, the swipe scrolls the table instead", () => {
  const table = { left: 200, width: 1200, client: 600 };
  assert.strictEqual(feed(gesture(BACK * 15, 0, 30, [table])), 0);
});
ok("over a table already at its edge, the swipe goes back", () => {
  const atEdge = BACK < 0 ? { left: 0, width: 1200, client: 600 } : { left: 600, width: 1200, client: 600 };
  assert.strictEqual(feed(gesture(BACK * 15, 0, 30, [atEdge])), 1);
});
ok("while text is being selected the swipe never goes back", () => {
  assert.strictEqual(feed(gesture(BACK * 15, 0, 30).map((e) => ({ ...e, selecting: true }))), 0);
  const mixed = gesture(BACK * 15, 0, 30).map((e, i) => ({ ...e, selecting: i < 3 }));
  assert.strictEqual(feed(mixed), 0);
});
ok("two swipes with a pause between them go back twice", () => {
  assert.strictEqual(feed([...gesture(BACK * 15, 0, 20), ...gesture(BACK * 15, 0, 20, [], 2000)]), 2);
});

// ---------- a document that no longer exists ----------
ok("an empty or missing document is spotted, a real one is not", () => {
  assert.strictEqual(R.isEmptyDoc(null), true);
  assert.strictEqual(R.isEmptyDoc({ parts: [] }), true);
  assert.strictEqual(R.isEmptyDoc({ parts: [{ id: 1, text: "  \n" }] }), true);
  assert.strictEqual(R.isEmptyDoc({ parts: [{ id: 1, text: "Boil water." }] }), false);
  assert.match(R.MISSING_DOC, /isn't in the library any more/);
});

// ---------- h(): only attribute names on its list, so no handler or link can be smuggled in ----------
ok("h() sets the attribute names the interface uses, aria-* and data-*", () => {
  const el = R.h("button", { type: "button", title: "t", "aria-label": "x", "data-md-root": true, tabindex: 0, disabled: true });
  assert.strictEqual(el.getAttribute("aria-label"), "x");
  assert.strictEqual(el.getAttribute("data-md-root"), "");
  assert.strictEqual(el.getAttribute("disabled"), "");
});
ok("h() refuses any other attribute name, so a name from a document can never become one", () => {
  for (const k of ["href", "src", "srcdoc", "style", "formaction", "action", "xlink:href", "poster", "background",
    "data-", "aria-", "Href", "data-x y"]) {
    assert.throws(() => R.h("a", { [k]: "javascript:alert(1)" }), /is not allowed/, k);
  }
  const el = R.h("button", { onclick: "alert(1)" });  // a handler given as text is dropped, never set
  assert.ok(![...el.attrs.keys()].some((k) => /^on/i.test(k)) && !el.listeners.click);
});
ok("every attribute name in the interface's own h() calls is on the list", () => {
  const fs = require("fs");
  const dir = path.dirname(path.resolve(process.argv[2]));
  for (const file of ["app.js", "render.js", "print.js", "flow.js"]) {
    const src = fs.readFileSync(path.join(dir, file), "utf8");
    for (const m of src.matchAll(/\bh\(\s*(?:"[^"]*"|`[^`]*`|\w+)\s*,\s*\{([^{}]*)\}/g)) {
      for (const k of m[1].matchAll(/(?:^|,)\s*(?:"([^"]+)"|([A-Za-z_$][\w$-]*))\s*(?=:|,|$)/g)) {
        const name = k[1] || k[2];
        if (["class", "text", "dataset"].includes(name) || name.startsWith("on")) continue;
        assert.ok(R.attrOk(name), `${file}: ${name}`);
      }
    }
  }
});

// ---------- look-alike hosts (U2) ----------
ok("a host in another alphabet (punycode) is spotted and shown in ASCII", () => {
  assert.strictEqual(R.lookalikeHost("https://xn--pypal-4ve.com/login"), "xn--pypal-4ve.com");
  assert.strictEqual(R.lookalikeHost("https://shop.XN--80ak6aa92e.com/"), "shop.xn--80ak6aa92e.com");
});
ok("an ordinary host, or no address at all, is not", () => {
  assert.strictEqual(R.lookalikeHost("https://www.redcross.org/get-help"), null);
  assert.strictEqual(R.lookalikeHost("https://example.com/xn--path"), null);
  assert.strictEqual(R.lookalikeHost("not a url"), null);
});

// A click on a look-alike address asks first (setWebCheck) and opens only on yes; any other address opens at once.
const opened = [];
const asked = [];
let answer = false;
R.setWebOpener((url) => opened.push(url));
R.setWebCheck((host) => { asked.push(host); return Promise.resolve(answer); });
const clickLink = (text) => {
  const box = document.createElement("div");
  box.append(R.md(text));
  const btn = find(box, "button")[0];
  for (const fn of btn.listeners.click || []) fn({ preventDefault() {} });
};
ok("an ordinary address opens at once, without asking", () => {
  clickLink("See https://www.redcross.org/get-help now.");
  assert.deepStrictEqual(opened, ["https://www.redcross.org/get-help"]);
  assert.deepStrictEqual(asked, []);
});
(async () => {
  opened.length = 0;
  clickLink("See https://xn--pypal-4ve.com/login now.");
  await new Promise((done) => setTimeout(done, 0));
  assert.deepStrictEqual(asked, ["xn--pypal-4ve.com"], "a look-alike host is asked about, in ASCII");
  assert.deepStrictEqual(opened, [], "a look-alike address doesn't open when the answer is no");
  answer = true;
  clickLink("See https://xn--pypal-4ve.com/login now.");
  await new Promise((done) => setTimeout(done, 0));
  assert.deepStrictEqual(opened, ["https://xn--pypal-4ve.com/login"], "it opens on yes");
  R.setWebCheck(null);
  R.setWebOpener(null);
  n++;
  console.log("PASS a look-alike address is asked about and opens only on yes");
  console.log(`${n} Markdown checks passed`);
})();
