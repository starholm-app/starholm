// The print page (print.html), drawn for the server's offscreen web view, which prints it to A5 pages and numbers
// them (render_page_pdf, number_pages). What it draws comes from its address:
// - ?guide=<slug>: one guide drawn with render.js's md(), as reading mode draws it;
// - ?numbers=1: the country card's numbers, an A5 page and wallet cards (pack.js numbersPrint), free;
// - ?pack=1: the emergency pack (pack.js), from the guides, the emergency numbers and the saved family plan;
// - ?plan=1: the saved family plan (family-plan.js) with its wallet cards;
// - ?passage=<id>: the library document holding that passage, its clean text only (no app chrome);
// - ?map=1: the map area being printed (maps.js printPage: the image the viewer sent, its scale and credit).
// Then <html data-print-ready="1"> once every figure has loaded ("error" when it can't be shown). Links to other
// guides and web addresses stay plain text on paper (no onGuide, no web opener). It starts nothing else: no status
// check, no model, no polling.
(function () {
  "use strict";
  const R = window.SLRender;
  const API = window.SLApi;
  const root = document.documentElement;
  const col = document.getElementById("reading-col");
  const str = (v) => (typeof v === "string" ? v : v == null ? "" : String(v));
  const params = new URLSearchParams(location.search);

  async function guidePage(slug) {
    if (!/^[a-z0-9-]{1,80}$/.test(slug)) throw new Error("bad guide");
    const [g, listed] = await Promise.all([API.guide(slug), API.guides().then((d) => (Array.isArray(d.guides) ? d.guides : []))]);
    const bySlug = new Map(listed.filter((x) => x && typeof x.slug === "string").map((x) => [x.slug, x]));
    const title = str(g.title) || slug;
    col.append(R.h("h1", null, title),
      R.h("div", { class: "part md" }, R.md(R.dropRepeatedTitle(R.dropReadNext(str(g.markdown)), title), { headingShift: 1, guide: slug, bareTables: true })));
    const related = (Array.isArray(g.related) ? g.related : []).map((s) => bySlug.get(s)).filter(Boolean);
    if (related.length) {
      col.append(R.h("section", { class: "read-next" }, R.h("h2", { class: "read-next-h" }, "Read next"),
        R.h("ul", null, related.map((x) => R.h("li", null, str(x.title), x.summary ? R.h("span", { class: "read-next-sum" }, ` ${str(x.summary)}`) : null)))));
    }
  }

  // The family plan as saved, or null when there is none (or it can't be read: the pack then prints blanks).
  async function savedPlan() {
    try {
      const d = await API.familyPlan.load();
      return d && d.saved ? d.plan : null;
    } catch { return null; }
  }

  async function packPage() {
    const P = window.SLPack;
    const slugs = [...new Set(P.PACK.pages.flatMap((p) => p.sections.map((s) => s.guide)))];
    const [e, plan, ...docs] = await Promise.all([API.emergency.load().catch(() => null), savedPlan(),
      ...slugs.map((s) => API.guide(s).catch(() => null))]);
    const guides = new Map(slugs.map((s, i) => [s, docs[i] && { title: str(docs[i].title), markdown: str(docs[i].markdown) }]).filter(([, g]) => g));
    col.classList.add("pack");
    col.append(...P.build(R, guides, e, plan));
  }

  async function numbersPage() {
    const e = await API.emergency.load().catch(() => null);
    col.classList.add("pack");
    col.append(...[window.SLPack.numbersPrint(R.h, e)].flat(Infinity).filter(Boolean));
  }

  async function planPage() {
    const [e, d] = await Promise.all([API.emergency.load().catch(() => null), API.familyPlan.load()]);
    col.classList.add("pack");
    col.append(...[window.SLPlan.printView(R, d.plan, e)].flat(Infinity).filter(Boolean));
  }

  async function documentPage(id) {
    if (!/^[0-9]{1,15}$/.test(id) || Number(id) < 1) throw new Error("bad passage");
    const doc = await API.article(Number(id));
    const parts = (Array.isArray(doc.parts) ? doc.parts : []).filter((p) => p && typeof p === "object");
    if (R.isEmptyDoc(doc)) throw new Error("empty");
    const title = str(doc.title) || "Untitled";
    const ids = new Set();
    col.append(...[R.h("h1", null, title),
      R.h("p", { class: "doc-src" }, `${str(doc.source)}${doc.official ? " · Official" : ""}`),
      ...parts.map((p, i) => R.h("div", { class: "part md" }, R.md(i === 0 ? R.dropRepeatedTitle(str(p.text), title) : str(p.text),
        { headingShift: 1, ids, bareTables: true, factBox: i === 0 }))),
      R.safeWebUrl(str(doc.url)) ? R.h("p", { class: "addr" }, `Address: ${str(doc.url)}`) : null,
      str(doc.license) ? R.h("p", { class: "doc-src" }, str(doc.license)) : null].filter(Boolean));
  }

  async function draw() {
    if (params.has("guide")) await guidePage(params.get("guide") || "");
    else if (params.get("numbers") === "1") await numbersPage();
    else if (params.get("pack") === "1") await packPage();
    else if (params.get("plan") === "1") await planPage();
    else if (params.has("passage")) await documentPage(params.get("passage") || "");
    else if (params.get("map") === "1") await window.SLMaps.printPage(R.h, col);
    else throw new Error("nothing to print");
    const imgs = [...col.querySelectorAll("img")];
    for (const img of imgs) img.setAttribute("loading", "eager");  // lazy figures may never load in a page nobody sees
    await Promise.all(imgs.map((img) => (img.complete ? null : new Promise((ok) => {
      img.addEventListener("load", ok, { once: true });
      img.addEventListener("error", ok, { once: true });
    }))));
  }
  draw().then(() => { root.dataset.printReady = "1"; }, () => { root.dataset.printReady = "error"; });
})();
