// The family emergency plan: a form the household fills in once (who lives here, who to call, where to meet, where
// things are), saved on this Mac only (POST /api/family-plan, app-data/family-plan.json, family_plan.py) and
// printed as an A5 page with filled-in wallet cards (print.html?plan=1, through the same print path as a guide).
// The emergency pack (pack.js) reads the saved plan for its meeting place and contacts.
//
// Self-contained on purpose: app.js only calls SLPlan.mount() for the Family plan page, so the whole feature can be
// switched off in one place later. The headings follow what the guides say a family plan should cover (Prepare ahead,
// No internet or phone network, Leaving home in a hurry); the water line uses Prepare ahead's
// 3.8 litres per person per day.
(function (root) {
  "use strict";
  const str = (v) => (typeof v === "string" ? v : v == null ? "" : String(v));
  const MAX_TEXT = 500;  // as family_plan.py
  const MAX_PEOPLE = 20;
  const PERSON = [["name", "Name"], ["blood", "Blood group"], ["medical", "Allergies and medicines"]];
  const SECTIONS = [
    { legend: "Who to call", fields: [
      ["contact_outside", "Contact outside the area, that everyone checks in with"],
      ["contact_nearby", "Nearby contact"]] },
    { legend: "Where to meet", fields: [
      ["meet_near", "If we are separated and cannot get home"],
      ["meet_away", "If we cannot stay in the area"],
      ["shelter", "Nearest public shelter"],
      ["school_work", "School and work plans"]] },
    { legend: "Where things are", area: true, fields: [
      ["documents", "Documents"], ["go_bag", "Go-bag and kit"], ["water", "Water"], ["medicines", "Medicines"]] },
  ];
  const FIELDS = SECTIONS.flatMap((s) => s.fields.map(([k]) => k));
  const LITRES_A_DAY = 3.8;  // Prepare ahead: "1 gallon (about 3.8 liters) per person per day"

  // The plan as the page keeps it: every field a string, people a list of {name, blood, medical}.
  function cleanPlan(p) {
    p = p && typeof p === "object" ? p : {};
    const people = (Array.isArray(p.people) ? p.people : []).slice(0, MAX_PEOPLE)
      .map((x) => Object.fromEntries(PERSON.map(([k]) => [k, str(x && x[k]).slice(0, MAX_TEXT)])));
    return { people, ...Object.fromEntries(FIELDS.map((k) => [k, str(p[k]).slice(0, MAX_TEXT)])) };
  }
  // How much water the household should keep, from the number of people named: null when nobody is named.
  function water(plan) {
    const n = cleanPlan(plan).people.filter((x) => x.name.trim()).length;
    if (!n) return null;
    const litres = (days) => (n * LITRES_A_DAY * days).toLocaleString("en-GB", { maximumFractionDigits: 1 });
    return { people: n, text: `For ${n} ${n === 1 ? "person" : "people"}: ${litres(3)} litres for 3 days, ${litres(7)} litres for 7 days (3.8 litres each a day, from Prepare ahead).` };
  }

  // ---------- the form (the Family plan page) ----------
  // Kept between visits to the page, so leaving it doesn't lose what was typed and not yet saved.
  const state = { plan: null, saved: null, dirty: false, loading: null };

  // wrap: the page's element. deps: {icon(name, size), toast(text), printPlan(): the Print button's work}.
  async function mount(wrap, deps) {
    const R = root.SLRender;
    const API = root.SLApi;
    const h = R.h;
    const head = () => h("h1", { tabindex: "-1" }, "Family plan");
    if (!state.plan) {
      wrap.replaceChildren(head(), h("p", { class: "hint" }, "Opening…"));
      try {
        state.loading = state.loading || API.familyPlan.load();
        const d = await state.loading;
        state.plan = cleanPlan(d.plan);
        state.saved = typeof d.saved === "number" ? d.saved : null;
      } catch (err) {
        wrap.replaceChildren(head(), h("p", { class: "empty-note" }, err.message));
        return;
      } finally { state.loading = null; }
      if (!document.contains(wrap) || wrap.hidden) return;
    }
    if (!state.plan.people.length) state.plan.people.push({ name: "", blood: "", medical: "" }, { name: "", blood: "", medical: "" });
    const plan = state.plan;

    const savedText = () => (state.dirty ? "Not saved yet" : state.saved
      ? `Saved on this computer at ${new Date(state.saved * 1000).toLocaleString("en-GB", { day: "numeric", month: "short", hour: "2-digit", minute: "2-digit" })}`
      : "Not saved yet");
    const savedEl = h("span", { class: "plan-saved", role: "status" }, savedText());
    const waterEl = h("p", { class: "plan-calc" });
    const showWater = () => { const w = water(plan); waterEl.textContent = w ? w.text : ""; waterEl.hidden = !w; };
    const changed = () => { state.dirty = true; savedEl.textContent = savedText(); showWater(); };

    function field(tag, value, label, onInput, extra = {}) {
      const el = h(tag, { class: "input", maxlength: String(MAX_TEXT), "aria-label": label, ...extra });
      if (tag === "textarea") el.rows = 3;
      el.value = value;
      el.autocomplete = "off";
      el.addEventListener("input", () => { onInput(el.value); changed(); });
      return el;
    }
    const peopleBox = h("div", { class: "plan-people" });
    function renderPeople() {
      peopleBox.replaceChildren(
        h("div", { class: "plan-row plan-row-head", "aria-hidden": "true" }, PERSON.map(([, label]) => h("span", null, label)), h("span")),
        ...plan.people.map((person, i) => h("div", { class: "plan-row" },
          PERSON.map(([k, label]) => field("input", person[k], `${label}, person ${i + 1}`, (v) => { person[k] = v; }, { type: "text" })),
          h("button", { class: "icon-btn", type: "button", "aria-label": `Remove person ${i + 1}`, title: "Remove",
            onclick: () => { plan.people.splice(i, 1); changed(); renderPeople(); } }, deps.icon("close", "sm")))));
    }
    renderPeople();
    const addBtn = h("button", { class: "link", type: "button", onclick: () => {
      if (plan.people.length >= MAX_PEOPLE) { deps.toast(`A plan holds at most ${MAX_PEOPLE} people.`); return; }
      plan.people.push({ name: "", blood: "", medical: "" });
      changed();
      renderPeople();
      peopleBox.querySelector(".plan-row:last-child input")?.focus();
    } }, "Add a person");

    async function save() {
      const d = await API.familyPlan.save(plan);
      state.saved = typeof d.saved === "number" ? d.saved : Date.now() / 1000;
      state.dirty = false;
      savedEl.textContent = savedText();
    }
    const saveBtn = h("button", { class: "btn primary", type: "submit" }, deps.icon("check", "sm"), "Save");
    const printBtn = h("button", { class: "btn", type: "button", onclick: async () => {
      if (printBtn.disabled) return;
      printBtn.disabled = true;
      try {
        if (state.dirty || !state.saved) await save();  // the printout is made from the saved plan
        await deps.printPlan(printBtn);
      } catch (err) { deps.toast(err.message); } finally { printBtn.disabled = false; }
    } }, deps.icon("print", "sm"), "Print");
    const form = h("form", { class: "plan-form" },
      h("p", { class: "facts" }, "Fill it in together. It prints as a page for the fridge, with a wallet card for each person, and fills in the emergency pack."),
      h("p", { class: "plan-lock" }, deps.icon("lock", "sm"), "Kept on this computer only. It is never sent anywhere."),
      h("fieldset", { class: "plan-sec" }, h("legend", null, "Who lives here"), peopleBox, h("p", { class: "plan-add" }, addBtn)),
      SECTIONS.map((s) => h("fieldset", { class: "plan-sec" }, h("legend", null, s.legend),
        h("div", { class: "plan-grid" }, s.fields.map(([k, label]) => {
          const id = `plan-${k}`;
          // label and box as two rows of the section's grid (app.css), so boxes side by side line up whatever the labels
          return h("div", { class: "plan-fld" }, h("label", { for: id }, label),
            h("div", { class: "plan-ctl" }, field("textarea", plan[k], label, (v) => { plan[k] = v; }, { id }),
              k === "water" ? waterEl : null));
        })))),
      h("p", { class: "hint plan-from" }, "The headings follow the guides Prepare ahead, No internet or phone network, and Leaving home in a hurry."),
      h("div", { class: "plan-bar" }, saveBtn, printBtn, savedEl));
    form.addEventListener("submit", async (e) => {
      e.preventDefault();
      saveBtn.disabled = true;
      try { await save(); deps.toast("The family plan is saved on this computer."); } catch (err) { deps.toast(err.message); } finally { saveBtn.disabled = false; }
    });
    showWater();
    wrap.replaceChildren(head(), form);
    wrap.querySelector("h1").focus();
  }

  // ---------- the printed plan (print.html?plan=1) ----------
  // R: window.SLRender; plan: the saved plan; e: the emergency numbers (api.js shape). The pack's numbers table and
  // wallet cards come from pack.js.
  function printView(R, plan, e) {
    const h = R.h;
    const P = root.SLPack;
    const p = cleanPlan(plan);
    const val = (v) => (str(v).trim() ? str(v) : " ");
    const w = water(p);
    return [
      h("h1", null, "Family emergency plan"),
      h("h2", null, "Who lives here"),
      h("table", { class: "plan-table" },
        h("thead", null, h("tr", null, PERSON.map(([, label]) => h("th", { scope: "col" }, label)))),
        h("tbody", null, (p.people.length ? p.people : [{}, {}, {}]).map((x) => h("tr", null, PERSON.map(([k]) => h("td", null, val(x[k]))))))),
      h("h2", null, "Emergency numbers"),
      P.numbersTable(h, e),
      SECTIONS.map((s) => [h("h2", null, s.legend),
        h("dl", { class: "plan-list" }, s.fields.flatMap(([k, label]) => [h("dt", null, label), h("dd", null, val(p[k]))])),
        s.area && w ? h("p", { class: "pack-src" }, w.text) : null]),
      P.walletCards(h, p, e),
    ];
  }

  const API = { PERSON, SECTIONS, FIELDS, cleanPlan, water, mount, printView, LITRES_A_DAY };
  if (typeof module !== "undefined" && module.exports) module.exports = API;
  root.SLPlan = API;
})(typeof window !== "undefined" ? window : globalThis);
