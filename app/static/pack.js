// The printable emergency pack (the Guides page's "Print emergency pack", printed through print.html?pack=1 like a
// guide): a few A5 pages put together from fixed sections of the guides, so every instruction is the guide's own
// wording and follows the guide when it changes; the current country's emergency numbers; and wallet cards to cut
// out. When a family plan is saved (family-plan.js), its meeting place and contacts fill the blanks.
//
// The numbers all come from the country card (countries/<iso2>.json, served as /api/emergency's current): the A5
// page lists every number with its label and "when to call" line and the date the card was checked; the wallet card
// takes only the numbers marked wallet, with their short labels. Without a card nothing is printed but a line saying
// to use your local emergency number. numbersPrint() is the free printout of just these two (print.html?numbers=1).
//
// PACK below is the list of what goes on which page. Each section is a guide's slug and the id of one of its
// headings ("h-" and the heading's words in lower case joined by "-"); top: true leaves out the section's
// sub-headings. Figures are left out to keep the pack short. tests/interface_guided_check.js checks every section
// exists and the wallet card's note is in its guide word for word.
(function (root) {
  "use strict";
  const PACK = {
    pages: [
      { title: "Call, then act", numbers: true, sections: [
        { guide: "war-and-air-raid-alerts", anchor: "h-when-the-warning-sounds" },
        { guide: "fire-at-home-or-in-your-building", anchor: "h-if-you-find-a-fire" },
        { guide: "bleeding-burns-and-heat-stroke", anchor: "h-severe-bleeding", top: true },
      ] },
      { title: "Water and power", sections: [
        { guide: "no-running-water", anchor: "h-make-doubtful-water-safe" },
        { guide: "no-electricity", anchor: "h-do-this-first" },
        { guide: "staying-warm-without-power", anchor: "h-what-never-to-use-for-heat-indoors" },
      ] },
      { title: "If you have to leave", plan: true, sections: [
        { guide: "leaving-home-in-a-hurry", anchor: "h-the-go-bag" },
        { guide: "leaving-home-in-a-hurry", anchor: "h-before-you-leave" },
      ] },
    ],
    // The line under the numbers on the back of each wallet card, quoted from its guide.
    walletNote: { guide: "no-internet-or-phone-network", text: "Do not keep calling a busy network: text instead." },
  };

  const str = (v) => (typeof v === "string" ? v : v == null ? "" : String(v));

  // A section's Markdown as the pack prints it: the guide's section (guideSection), its figures taken out, and with
  // top its sub-headings and what follows them. "" when the guide has no such heading.
  function sectionText(guideSection, markdown, anchor, top = false) {
    const md = str(markdown);
    const text = guideSection(md, anchor);
    if (!text || text === md.trim() || text === md) return "";
    let lines = text.split("\n").filter((line) => !/^\s*!\[[^\]]*\]\([^)]*\)\s*$/.test(line));
    if (top) {
      const level = (/^(#{1,6})\s/.exec(lines[0]) || ["", "######"])[1].length;
      const cut = lines.findIndex((line, i) => i > 0 && /^(#{1,6})\s/.test(line) && /^(#{1,6})/.exec(line)[1].length > level);
      if (cut > 0) lines = lines.slice(0, cut);
    }
    return lines.join("\n").replace(/\n{3,}/g, "\n\n").trim();
  }

  // What the printouts take from the country card: {name, checked, page: [{label, when, number}] (every number, for
  // the A5 page), wallet: [{short, number}] (the wallet numbers only)}, or null without a card.
  // guess: country.js's line when the country is the time-zone guess nobody picked ("" once one is chosen).
  function cardPrint(e) {
    const c = e && e.current;
    if (!c || !Array.isArray(c.numbers) || !c.numbers.length) return null;
    const C = root.SLCountry;
    return { name: str(c.name), checked: str(c.checked), guess: C ? C.guessLine(e) : "",
      page: c.numbers.map((n) => ({ label: str(n.label), when: str(n.when), number: str(n.number) })),
      wallet: c.numbers.filter((n) => n.wallet === true).map((n) => ({ short: str(n.short) || str(n.label), number: str(n.number) })) };
  }
  const NO_CARD = "There is no country card for this country. In an emergency, call your local emergency number.";
  // "Checked on 18 November 2026" from "2026-11-18".
  function checkedText(iso) {
    const m = /^(\d{4})-(\d{2})-(\d{2})$/.exec(str(iso));
    if (!m) return "";
    const months = ["January", "February", "March", "April", "May", "June", "July", "August", "September", "October", "November", "December"];
    return `Checked on ${Number(m[3])} ${months[Number(m[2]) - 1]} ${m[1]}`;
  }

  // The A5 page's numbers: the country's name, every number with its label and "when to call" line, the date checked.
  function numbersTable(h, e) {
    const c = cardPrint(e);
    if (!c) return [h("p", null, NO_CARD), h("p", null, "Write your local emergency numbers here: ________________")];
    return h("div", { class: "pack-numbers" },
      h("p", { class: "pack-country" }, c.name),
      c.guess ? h("p", { class: "pack-guess" }, c.guess) : null,
      h("table", null, h("tbody", null, c.page.map((n) => h("tr", null,
        h("td", null, n.label, n.when ? h("span", { class: "pack-when" }, n.when) : null), h("td", { class: "pack-num" }, n.number))))),
      c.checked ? h("p", { class: "pack-src" }, checkedText(c.checked)) : null);
  }

  // The numbers side of a wallet card: the country's wallet numbers with their short labels.
  function walletBack(h, e) {
    const c = cardPrint(e);
    return h("div", { class: "wcard wcard-back" },
      h("div", { class: "wh" }, h("b", null, c ? c.name : "Emergency numbers"), h("span", null, "Starholm")),
      c ? h("div", { class: "wnums" }, c.wallet.flatMap((n) => [h("span", null, n.short), h("b", null, n.number)]))
        : h("p", null, "Your local emergency number: ________"),
      c && c.guess ? h("p", { class: "wnote" }, c.guess) : null,
      h("p", { class: "wnote" }, PACK.walletNote.text));
  }

  // A line to write on, or the saved value.
  const line = (h, label, value) => h("div", { class: "wl" }, h("span", { class: "wl-label" }, label),
    value ? h("span", { class: "wl-value" }, value) : h("span", { class: "wl-blank" }));

  // Wallet cards to cut out and fold: for each person in the plan (one blank card when there is none), the front with
  // their details and the household's contacts and meeting place, the back with the card's wallet numbers.
  function walletCards(h, plan, e) {
    const p = plan && typeof plan === "object" ? plan : {};
    const people = Array.isArray(p.people) && p.people.length ? p.people : [{}];
    const back = () => walletBack(h, e);
    return h("section", { class: "pack-page wallet-page" },
      h("h2", null, "Wallet cards"),
      h("p", { class: "pack-how" }, "Cut out each card along the dashed line and fold it in half: it is the size of a bank card."),
      people.map((person) => h("div", { class: "wallet" },
        h("div", { class: "wcard wcard-front" },
          h("div", { class: "wh" }, h("b", null, "In an emergency"), h("span", null, "Starholm")),
          line(h, "Name", str(person.name)),
          line(h, "Blood group", str(person.blood)),
          line(h, "Allergies, medicines", str(person.medical)),
          line(h, "Contact", str(p.contact_outside)),
          line(h, "Contact", str(p.contact_nearby)),
          line(h, "Meeting point", str(p.meet_near))),
        back())));
  }

  // The whole pack. R: window.SLRender; guides: slug -> {title, markdown}; e: the emergency numbers (api.js shape);
  // plan: the saved family plan or null.
  function build(R, guides, e, plan) {
    const h = R.h;
    const pages = PACK.pages.map((page, i) => {
      const kids = [h("h2", { class: "pack-title" }, page.title)];
      if (page.numbers) kids.push(...[].concat(numbersTable(h, e)));
      for (const s of page.sections) {
        const g = guides.get(s.guide);
        const text = g ? sectionText(R.guideSection, g.markdown, s.anchor, s.top) : "";
        if (!text) continue;
        kids.push(h("div", { class: "pack-section" },
          h("div", { class: "part md" }, R.md(text, { headingShift: 1, guide: s.guide, bareTables: true })),
          h("p", { class: "pack-src" }, `From the guide “${str(g.title)}”`)));
      }
      if (page.plan) {
        const p = plan && typeof plan === "object" ? plan : {};
        kids.push(h("div", { class: "planbox" },
          line(h, "Meeting place", str(p.meet_near)),
          line(h, "If we cannot stay in the area", str(p.meet_away)),
          line(h, "Contact outside the area", str(p.contact_outside))),
        p.meet_near || p.contact_outside ? h("p", { class: "pack-src" }, "From your family plan") : null);
      }
      return h("section", { class: `pack-page${i ? "" : " first"}` }, kids);
    });
    return [h("h1", null, "Emergency pack"), ...pages, walletCards(h, plan, e)];
  }

  // The free printout (print.html?numbers=1): the A5 page "Call, then act" with the country card's numbers, then a
  // page of wallet cards (the numbers side only, two to cut out). No lock: emergency numbers are free.
  function numbersPrint(h, e) {
    return [h("h1", null, "Emergency numbers"),
      h("section", { class: "pack-page first" }, h("h2", { class: "pack-title" }, "Call, then act"), [].concat(numbersTable(h, e))),
      h("section", { class: "pack-page wallet-page" },
        h("h2", null, "Wallet card"),
        h("p", { class: "pack-how" }, "Cut out a card along the dashed line: it is the size of a bank card."),
        [0, 1].map(() => h("div", { class: "wallet wallet-single" }, walletBack(h, e))))];
  }

  const API = { PACK, sectionText, cardPrint, checkedText, build, walletCards, walletBack, numbersTable, numbersPrint, NO_CARD };
  if (typeof module !== "undefined" && module.exports) module.exports = API;
  root.SLPack = API;
})(typeof window !== "undefined" ? window : globalThis);
