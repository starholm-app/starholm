// The free library on the Library page: the two cards (the free raw build with its source picker, and the ready-made
// library in the upgrade), the CERT manual's page both ways between them, the build's progress, and the reminder once
// the free library is in use. Four states, as in the approved mockups:
//   "first"      no library yet: both cards and the comparison
//   "customise"  the same with the picker open (each source with its size and licence, a running total)
//   "building"   a download or build under way (or paused, or stopped on a failure): its steps and progress
//   "done"       the library in use is a free build: a slim reminder above the library, which can be hidden
// Anything else ("none": a ready-made library, nothing running) adds nothing to the page.
//
// The functions here only build elements from the server's /api/free-library answer; app.js fetches it, keeps the
// picker's choices and calls the server when a button is pressed (ctx.on). Nothing starts a download by itself.
// Everything is built with DOM calls (render.js's h(), passed in as ctx.h); no markup is ever made from data. No
// price appears anywhere. tests/interface_free_library_check.js renders each state with a stand-in DOM.
(function (root) {
  "use strict";
  const str = (v) => (typeof v === "string" ? v : v == null ? "" : String(v));
  const num = (v) => (Number.isFinite(Number(v)) && v !== null && v !== "" ? Number(v) : 0);
  const ACTIVE = ["downloading", "paused", "building", "failed", "build-failed"];

  function size(n) {
    n = num(n);
    if (n <= 0) return "";
    return n >= 1e9 ? `${(n / 1e9).toFixed(1)} GB` : `${Math.max(1, Math.round(n / 1e6))} MB`;
  }
  const aboutGb = (n) => `${Math.max(1, Math.round(num(n) / 1e9))} GB`;
  function timeLeft(sec) {
    sec = num(sec);
    if (sec <= 0) return "";
    if (sec < 3600) return `About ${Math.max(1, Math.round(sec / 60))} minutes left`;
    const h = Math.round(sec / 3600);
    return `About ${h} hour${h === 1 ? "" : "s"} left`;
  }

  function stateOf(model, ui) {
    const job = model && model.job;
    if (job && ACTIVE.includes(job.phase)) return "building";
    if (model && !model.has_library) return ui && ui.picking ? "customise" : "first";
    if (model && model.library_raw) return "done";
    return "none";
  }

  // The keys the picker has chosen: every source until the person changes it.
  function chosen(model, ui) {
    const all = (model.sources || []).map((s) => str(s.key));
    return ui && ui.selected instanceof Set ? all.filter((k) => ui.selected.has(k)) : all;
  }
  function chosenBytes(model, keys) {
    const set = new Set(keys);
    return (model.sources || []).filter((s) => set.has(str(s.key))).reduce((a, s) => a + num(s.size), 0);
  }

  // ---------- the two cards and the comparison ----------
  function freeCard(model, ui, ctx) {
    const { h, icon, on } = ctx;
    const total = num(model.total_bytes);
    return h("article", { class: "fl-card", "data-card": "free" },
      h("span", { class: "fl-eyebrow" }, "Free"),
      h("h2", { class: "fl-card-h" }, "Download the free library"),
      h("p", null, "Starholm downloads each source from where it is published (Kiwix and government sites) and builds " +
        "the library on this computer. It is built without the cleaning rules, so pages keep their running headers, page " +
        `numbers and broken lines. A lot of the ${aboutGb(total)} is noise: page clutter, near-duplicates and material ` +
        "you are unlikely to need."),
      h("ul", { class: "fl-facts" },
        h("li", null, `About ${aboutGb(total)} to download, much of it noise`),
        h("li", null, "Several hours to build"),
        h("li", null, "About 1.5 million documents")),
      h("div", { class: "fl-card-btns" },
        h("button", { class: "btn", type: "button", "data-action": "download-all", onclick: () => on.download(chosen(model, null)) },
          icon("down", "sm"), "Download the free library"),
        h("button", { class: "link", type: "button", "data-action": "customise", "aria-expanded": String(!!(ui && ui.picking)),
          onclick: () => on.customise() }, "Customise")));
  }

  function upgradeCard(ctx) {
    const { h, icon, on } = ctx;
    return h("article", { class: "fl-card fl-card-up", "data-card": "upgrade" },
      h("span", { class: "fl-eyebrow" }, "Upgrade"),
      h("h2", { class: "fl-card-h" }, "Get the ready-made library"),
      h("p", null, "The same sources, cleaned and checked, in one download. It keeps what is useful and drops the rest, so " +
        "it takes about half the space. Pages read like the guides, search finds less clutter, and the key also unlocks " +
        "every guide, offline maps and the family plan."),
      h("ul", { class: "fl-facts" },
        h("li", null, "About 16 GB to download"),
        h("li", null, "Ready in about 3 hours on a fast connection"),
        h("li", null, "About 1.1 million documents")),
      h("p", { class: "fl-lockline" }, icon("lock", "sm"), "Included in the upgrade"),
      h("div", { class: "fl-card-btns" },
        h("button", { class: "btn primary", type: "button", "data-action": "upgrade", onclick: () => on.buyUpgrade() },
          icon("external", "sm"), "Get the upgrade"),
        h("button", { class: "link", type: "button", "data-action": "have-key", onclick: () => on.openSettings() }, "I have a key")),
      h("p", { class: "hint" }, "Get the upgrade opens the website in your browser. I have a key opens the upgrade in Settings."));
  }

  const RAW_CERT = "How to use a tourniquet:\nStep 1: Place as high as possible on the injured limb – closest to the torso. (You can\n" +
    "place it over clothing.)\nStep 2: Pull the strap through the buckle.\nAugust 2019\n\nPage 3-4\n\n" +
    "CERT Unit 3: Disaster Medical Operations-Part 1\n\nParticipant Manual\n\nStep 3: Twist the rod tightly until bleeding " +
    "stops/slows significantly. (May be very\npainful.)\nStep 4: Secure the rod.\nStep 5: If bleeding does not stop, place " +
    "a second tourniquet.\nStep 6: Leave in place until EMS takes over care.\nImage 3.1: Tourniquet";
  const CLEAN_CERT = [
    "How to use a tourniquet: Step 1: Place as high as possible on the injured limb – closest to the torso. (You can place " +
      "it over clothing.) Step 2: Pull the strap through the buckle.",
    "Step 3: Twist the rod tightly until bleeding stops/slows significantly. (May be very painful.) Step 4: Secure the rod. " +
      "Step 5: If bleeding does not stop, place a second tourniquet. Step 6: Leave in place until EMS takes over care. " +
      "Image 3.1: Tourniquet"];

  function compare(ctx) {
    const { h } = ctx;
    return h("section", { class: "fl-compare", "aria-label": "The same page, raw and cleaned" },
      h("h3", { class: "section-h" }, "The same page of FEMA's CERT manual, both ways"),
      h("div", { class: "fl-compare-grid" },
        h("div", null, h("p", { class: "fl-compare-h" }, "Free library: as it comes from the PDF"),
          h("pre", { class: "fl-raw" }, RAW_CERT)),
        h("div", null, h("p", { class: "fl-compare-h" }, "Ready-made library: after the cleaning rules"),
          h("div", { class: "fl-clean" }, CLEAN_CERT.map((p) => h("p", null, p))))),
      h("p", { class: "hint" }, "Real text from page 3-4 of the CERT Basic Training Participant Manual (public domain). The " +
        "cleaning rules drop the running header, the footer and the page label, and join the broken lines."));
  }

  function picker(model, ui, ctx) {
    const { h, icon, on } = ctx;
    const keys = chosen(model, ui);
    const set = new Set(keys);
    const bytes = chosenBytes(model, keys);
    const unknown = (model.sources || []).filter((s) => set.has(str(s.key)) && !num(s.size)).length;
    const free = num(model.free_bytes);
    const rows = (model.sources || []).map((s) => {
      const key = str(s.key);
      return h("label", { class: "fl-pick", "data-key": key },
        h("input", { type: "checkbox", checked: set.has(key), onchange: () => on.toggle(key) }),
        h("span", { class: "fl-pick-name" }, str(s.title), h("span", { class: "hint" }, ` from ${str(s.from)}`)),
        h("span", { class: "hint fl-num" }, num(s.size) ? size(s.size) : "size not known"),
        h("span", { class: "hint fl-pick-lic", title: str(s.licence_text) }, str(s.licence)));
    });
    const summary = `${keys.length} source${keys.length === 1 ? "" : "s"} · ${size(bytes) || "0 MB"} selected` +
      (unknown ? ` and ${unknown} of unknown size` : "") + " · each keeps its own licence";
    return h("div", { class: "fl-picker", id: "fl-picker" },
      h("div", { class: "fl-picker-head" }, h("b", null, "Choose sources"), h("span", { class: "hint", id: "fl-total" }, summary)),
      h("div", { class: "fl-picker-list" }, rows),
      free ? h("p", { class: "hint fl-space" }, `${aboutGb(free)} free on this computer.`) : null,
      h("div", { class: "form-row" },
        h("button", { class: "link", type: "button", "data-action": keys.length ? "select-none" : "select-all",
          onclick: () => (keys.length ? on.selectNone() : on.selectAll()) }, keys.length ? "Select none" : "Select all"),
        h("span", { class: "btn-row" },
          h("button", { class: "btn", type: "button", "data-action": "cancel-pick", onclick: () => on.cancelPick() }, "Cancel"),
          h("button", { class: "btn primary", type: "button", "data-action": "download-chosen", disabled: !keys.length,
            onclick: () => on.download(keys) }, icon("down", "sm"), `Download ${keys.length ? aboutGb(bytes) : ""}`.trim()))));
  }

  function cards(model, ui, ctx) {
    const { h } = ctx;
    const picking = !!(ui && ui.picking);
    return h("div", { class: "fl-wrap", "data-state": picking ? "customise" : "first" },
      h("div", { class: "fl-choice" }, freeCard(model, ui, ctx), upgradeCard(ctx)),
      picking ? picker(model, ui, ctx) : null,
      compare(ctx));
  }

  // ---------- the build in progress ----------
  function building(model, ctx) {
    const { h, icon, on } = ctx;
    const job = model.job;
    const items = job.sources || {};
    const titles = Object.fromEntries((model.sources || []).map((s) => [str(s.key), s]));
    const phase = str(job.phase);
    const downloading = ["downloading", "paused", "failed"].includes(phase);
    const head = phase === "paused" ? "Paused" : phase === "building" ? "Building the library"
      : phase === "failed" ? "Some sources couldn't be downloaded" : phase === "build-failed" ? "The library didn't pass its check"
        : timeLeft(job.eta_seconds) || "Downloading";
    const buttons = [];
    if (phase === "downloading") buttons.push(h("button", { class: "btn", type: "button", "data-action": "pause", onclick: () => on.pause() }, icon("pause", "sm"), "Pause"));
    if (phase === "paused") buttons.push(h("button", { class: "btn primary", type: "button", "data-action": "resume", onclick: () => on.resume() }, icon("down", "sm"), "Resume"));
    if (phase === "failed") buttons.push(h("button", { class: "btn primary", type: "button", "data-action": "resume", onclick: () => on.resume() }, icon("retry", "sm"), "Retry all"));
    if (phase === "build-failed") buttons.push(h("button", { class: "btn primary", type: "button", "data-action": "resume", onclick: () => on.resume() }, icon("retry", "sm"), "Build again"));
    buttons.push(h("button", { class: "link", type: "button", "data-action": "cancel", onclick: () => on.cancel() }, "Cancel"));

    const total = num(job.total_bytes);
    const pct = total ? Math.min(100, Math.floor((num(job.done_bytes) / total) * 100)) : 0;
    const now = job.now && titles[job.now] ? `Now: ${str(titles[job.now].title)} from ${str(titles[job.now].from)}` : "";
    const failed = Object.entries(items).filter(([, i]) => i && i.status === "failed");
    const step = (cls, mark, ...kids) => h("li", { class: cls }, mark, h("span", null, kids));
    const doneMark = () => icon("check", "sm");
    const waitMark = () => icon("dots", "sm");
    const spin = () => h("span", { class: "spinner" });
    const dlState = downloading ? "active" : "done";
    const buildState = phase === "building" ? "active" : phase === "build-failed" ? "failed" : "";
    return h("div", { class: "fl-wrap", "data-state": "building" },
      h("article", { class: "fl-card fl-build" },
        h("div", { class: "fl-dlhead" }, h("span", null, h("b", null, head)), h("span", { class: "btn-row" }, buttons)),
        h("ol", { class: "fl-steps" },
          step("done", doneMark(), `Checked free space: ${aboutGb(job.free_bytes)} free`),
          step(dlState, dlState === "active" && phase === "downloading" ? spin() : dlState === "done" ? doneMark() : waitMark(),
            `Downloading sources · ${num(job.done_count)} of ${num(job.count)} · ${size(job.done_bytes) || "0 MB"} of ${size(total) || "0 MB"}`,
            h("div", { class: "bar fl-pbar", role: "progressbar", "aria-valuenow": String(pct), "aria-valuemin": "0",
              "aria-valuemax": "100", "aria-label": "Downloading the free library" }, h("i", { class: "fl-pct", "data-pct": String(pct) })),
            now ? h("span", { class: "hint" }, now) : null,
            phase === "paused" ? h("span", { class: "hint" }, "Paused. Resume carries on where it stopped.") : null,
            failed.length ? h("ul", { class: "fl-failed" }, failed.map(([key, i]) => h("li", { "data-key": key },
              h("span", null, h("b", null, str(titles[key] ? titles[key].title : key)), `: ${str(i.error) || "the download failed"}`),
              h("span", { class: "btn-row" },
                h("button", { class: "link", type: "button", "data-action": "retry", onclick: () => on.retry(key) }, "Retry"),
                Object.keys(items).length > 1 ? h("button", { class: "link", type: "button", "data-action": "leave-out",
                  onclick: () => on.leaveOut(key) }, "Leave it out") : null)))) : null),
          step(buildState, buildState === "active" ? spin() : waitMark(), "Building the search index"),
          step(buildState === "failed" ? "failed" : "", waitMark(), "Checking the library",
            phase === "build-failed" ? h("ul", { class: "fl-failed" }, (job.problems || []).slice(0, 5).map((p) => h("li", null, str(p)))) : null)),
        h("p", { class: "hint" }, "Keep the computer on and plugged in. You can close this page or quit Starholm; the build picks up where it stopped.")),
      h("p", { class: "fl-slim" }, icon("key", "sm"), h("span", null, "Want to skip the wait? The ready-made library is one download."),
        h("button", { class: "btn primary", type: "button", "data-action": "upgrade", onclick: () => on.buyUpgrade() }, "Get the upgrade")));
  }

  // ---------- once the free library is in use ----------
  function banner(model, ctx) {
    const { h, icon, on } = ctx;
    if (!model || !model.library_raw || (model.job && model.job.banner_hidden)) return null;
    return h("div", { class: "banner fl-slimbanner", "data-state": "done" },
      h("span", null, "You're using the free library, built without the cleaning rules. The ready-made library is cleaner and smaller."),
      h("button", { class: "btn primary", type: "button", "data-action": "upgrade", onclick: () => on.buyUpgrade() }, icon("external", "sm"), "Get the upgrade"),
      h("button", { class: "icon-btn sm", type: "button", title: "Hide", "aria-label": "Hide", "data-action": "hide-banner",
        onclick: () => on.hideBanner() }, icon("close", "sm")));
  }

  // What goes on the Library page for this state: {top (above the title), body (under the facts), facts (the line under
  // the title, or null to keep the library's own), hideLibrary (no library to show under it)}.
  function view(model, ui, ctx) {
    const state = stateOf(model, ui);
    if (state === "first" || state === "customise") {
      return { state, top: null, body: cards(model, ui, ctx), hideLibrary: true,
        facts: "There's no library on this computer yet. Pick one way to get it. The guides, the chat and your own files work in the meantime." };
    }
    if (state === "building") {
      return { state, top: null, body: building(model, ctx), hideLibrary: !model.has_library,
        facts: model.has_library ? null : "Building the free library" };
    }
    if (state === "done") return { state, top: banner(model, ctx), body: null, hideLibrary: false, facts: null };
    return { state, top: null, body: null, hideLibrary: false, facts: null };
  }

  // Bars get their width through the style object (no style attribute is ever written from data).
  function sizeBars(el) {
    for (const bar of el.querySelectorAll(".fl-pct")) bar.style.width = `${Number(bar.dataset.pct) || 0}%`;
  }

  const api = { stateOf, view, chosen, size, timeLeft, sizeBars, ACTIVE };
  root.SLFreeLibrary = api;
  if (typeof module !== "undefined" && module.exports) module.exports = api;
})(typeof window !== "undefined" ? window : globalThis);
