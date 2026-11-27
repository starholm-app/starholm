// The country card on screen (DOM-free; app.js draws it with render.js's h, tests/interface_country_check.js with a
// stand-in). e is api.js's cleaned /api/emergency: the picker lists exactly the countries that have a card; the
// card shows each number with its label and its "when to call" line, and the date the card was checked, with no
// confidence score; the sources stay in a collapsed list. Without a card (no country known, or one with no card)
// it says so plainly and tells the person to use their local emergency number: it never shows another country's.
(function (root) {
  "use strict";
  const str = (v) => (typeof v === "string" ? v : v == null ? "" : String(v));
  const MONTHS = ["January", "February", "March", "April", "May", "June", "July", "August", "September", "October",
    "November", "December"];

  // "18 November 2026" from "2026-11-18"; "" for anything else.
  function dateText(iso) {
    const m = /^(\d{4})-(\d{2})-(\d{2})$/.exec(str(iso));
    return m && Number(m[2]) >= 1 && Number(m[2]) <= 12 ? `${Number(m[3])} ${MONTHS[Number(m[2]) - 1]} ${m[1]}` : "";
  }

  // The picker's options: every country with a card, in the server's (name) order, the one in use selected.
  // When none is in use (no choice, and the time zone gave no country with a card), a first "Choose your country"
  // option is selected, so the picker never shows a country it isn't using.
  function options(e) {
    const list = e && Array.isArray(e.countries) ? e.countries : [];
    const using = e && e.current ? e.current.code : null;
    const out = list.map((c) => ({ value: c.code, label: c.name, selected: c.code === using }));
    return using ? out : [{ value: "", label: "Choose your country", selected: true }, ...out];
  }

  // The line under the picker: where the country came from ("" for a guess: the card itself says so, guessLine).
  function sourceLine(e) {
    if (!e) return "";
    if (e.choice) return "You chose this country. It is saved on this computer.";
    if (e.current) return "";
    return "Choose the country you are in.";
  }

  // When the card shown is the guess from the Mac's time zone (or region setting) and nobody picked it, one plain
  // line saying so, with the way to change it; "" once a country is chosen, or when no card is shown. The popover and
  // the printouts say where to change it ("in Settings"); Settings itself, next to the picker, says "above".
  function guessLine(e, { inSettings = false } = {}) {
    if (!e || e.choice || !e.current) return "";
    const how = /^region/.test(str(e.detected && e.detected.detected_by)) ? "region setting" : "time zone";
    return `Country guessed from this computer's ${how}. ${inSettings ? "Pick another above" : "Change it in Settings"} if you are somewhere else.`;
  }

  // The no-card message, in plain words.
  function noCardText(e) {
    const c = e && e.country;
    const named = c && c.name && c.name !== c.code ? c.name : "";
    const lead = named ? `There is no country card for ${named}.` : c ? "There is no country card for this computer's country."
      : "No country is set.";
    return `${lead} In an emergency, call your local emergency number.`;
  }

  // The card: {title, checked: "Checked on …" or "", rows: [{label, when, number}], sources: [line], notices} or
  // null without a card.
  function cardData(e) {
    const c = e && e.current;
    if (!c || !Array.isArray(c.numbers) || !c.numbers.length) return null;
    const checked = dateText(c.checked);
    return { title: str(c.name), checked: checked ? `Checked on ${checked}` : "",
      rows: c.numbers.map((n) => ({ label: str(n.label), when: str(n.when), number: str(n.number) })),
      sources: (Array.isArray(c.sources) ? c.sources : []).map((s) => [str(s.title), str(s.publisher), str(s.url)].filter(Boolean).join(", ")).filter(Boolean),
      notices: (Array.isArray(c.notices) ? c.notices : []).map(str).filter(Boolean) };
  }

  // The card drawn with h: compact for the Emergency button's popover, in full (with its sources) for Settings.
  function cardView(h, e, { compact = false, inSettings = false } = {}) {
    const d = cardData(e);
    if (!d) return [h("p", { class: "cc-none", role: "note" }, e ? noCardText(e) : "The numbers couldn't be loaded. In an emergency, call your local emergency number.")];
    const guess = guessLine(e, { inSettings });
    return [
      guess ? h("p", { class: "cc-guess", role: "note" }, guess) : null,
      h("div", { class: "cc-head" }, h("b", { class: "cc-name" }, d.title), d.checked ? h("span", { class: "cc-checked" }, d.checked) : null),
      h("div", { class: compact ? "cc-rows cc-compact" : "cc-rows" }, d.rows.map((r) => h("div", { class: "cc-row" },
        h("div", { class: "cc-what" }, h("span", { class: "cc-label" }, r.label), r.when ? h("span", { class: "cc-when" }, r.when) : null),
        h("b", { class: "cc-num" }, r.number)))),
      compact || (!d.sources.length && !d.notices.length) ? null : h("details", { class: "cc-sources" },
        h("summary", null, "Sources"),
        h("ul", null, d.sources.map((s) => h("li", null, s)), d.notices.map((n) => h("li", { class: "cc-notice" }, n)))),
    ].filter(Boolean);
  }

  const API = { dateText, options, sourceLine, guessLine, noCardText, cardData, cardView };
  if (typeof module !== "undefined" && module.exports) module.exports = API;
  root.SLCountry = API;
})(typeof window !== "undefined" ? window : globalThis);
