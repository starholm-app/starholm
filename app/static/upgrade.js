// The upgrade: the Upgrade page in Settings (its seven states in the approved mockups: no key, a key that isn't
// valid, key saved, downloading, paused, everything downloaded, and a fourth computer) and what a locked guide, the
// emergency pack, the maps and the family plan show without a key. No price appears anywhere.
//
// Pure views: each function takes the server's answer (GET /api/upgrade, upgrade_status() in app/server.py) and
// returns elements built with render.js's h(). The buttons call the handlers app.js passes in ctx.on, so this file
// itself never calls the server. Checked by tests/interface_upgrade_check.js.
(function (root) {
  "use strict";
  const str = (v) => (typeof v === "string" ? v : v == null ? "" : String(v));
  const LOCKED = "Included in the upgrade";
  const CHECKED = "It is checked on this computer, with no account. The key stays here and nothing about you is sent anywhere.";
  // 3 -> "three computers", 25 -> "25 computers"
  const computers = (n) => { n = Number(n) > 0 ? Number(n) : 3; return `${({ 1: "one", 2: "two", 3: "three" })[n] || n} computer${n === 1 ? "" : "s"}`; };
  // What a key covers: an individual key three computers in a household, an organisation key 25. Before a key is
  // saved, the individual key's line (the mockups').
  function keyFactsText(key) {
    if (key && key.licence === "organisation") return `This key covers ${computers(key.computers || 25)} in your organisation and never expires. ${CHECKED}`;
    return `${key && key.computers ? "This key" : "One key"} covers ${computers(key && key.computers)} in your household and never expires. ${CHECKED}`;
  }
  const UPSELL = "The upgrade unlocks every guide, the booklets, the emergency pack, offline maps, the family plan and the ready-made library. One key covers three household computers and never expires.";
  // Every "Get the upgrade" button opens the pricing page in the person's own browser.
  const BUY_URL = "https://starholm.app/upgrade";
  const SWAP_NOTE = "Your current library stays in use until the new one is ready. Then Starholm swaps them over, which takes a few seconds.";
  const PACK_TITLE = { guides: "Guides, booklets and the emergency pack", library: "The ready-made library" };
  const CHECKING = "Checking files already on this computer";

  function unlocks(guideCount) {
    return [
      ["The ready-made library", "About 16 GB, about 1.1 million documents, cleaned and checked."],
      ["Every guide", `${guideCount ? `All ${guideCount} guides` : "All the guides"}, booklets to print and fold, and the emergency pack.`],
      ["Offline maps", "Street maps by region that work with no internet."],
      ["The family plan", "A form for who to call and where to meet, printed for the fridge and as wallet cards."],
    ];
  }

  // 15_038_000_000 -> "15 GB"; 38_000_000 -> "38 MB"; decimal units, as the Library's sizes.
  function size(n) {
    n = Number(n) || 0;
    if (n >= 1e9) { const g = n / 1e9; return `${g >= 100 ? Math.round(g) : Math.round(g * 10) / 10} GB`; }
    if (n >= 1e6) return `${Math.round(n / 1e6)} MB`;
    if (n >= 1e3) return `${Math.round(n / 1e3)} KB`;
    return `${Math.round(n)} bytes`;
  }
  // seconds -> "3 hours", "2 hours 50 minutes", "12 minutes", "less than a minute", "more than 3 days"
  const LONGEST = 3 * 24 * 3600;  // past this, a figure in hours means nothing to anyone
  function duration(s) {
    s = Math.max(0, Number(s) || 0);
    if (s >= LONGEST) return "more than 3 days";
    if (s < 60) return "less than a minute";
    const mins = Math.round(s / 60);
    if (mins < 60) return `${mins} ${mins === 1 ? "minute" : "minutes"}`;
    let hours = Math.floor(mins / 60);
    let rest = Math.round((mins % 60) / 10) * 10;
    if (rest === 60) { hours += 1; rest = 0; }
    if (hours >= 3 && rest <= 10) return `${hours} hours`;
    return `${hours} ${hours === 1 ? "hour" : "hours"}${rest ? ` ${rest} minutes` : ""}`;
  }

  // Which of the seven states the page is in. ui.error: Save's refusal, shown while no key is saved.
  function state(st, ui = {}) {
    const key = st && st.key;
    if (!key || key.verdict !== "valid") return ui.error ? "invalid" : "empty";
    const job = st.job;
    const phase = job && job.phase;
    if (phase === "refused") return "fourth";
    if (phase === "asking" || phase === "checking" || phase === "downloading" || phase === "installing") return "downloading";
    if (phase === "paused" || phase === "error") return "paused";
    const have = st.installed || {};
    if ((st.order || ["guides", "library"]).every((p) => have[p])) return "done";
    return "saved";
  }

  // "Remove the free library (x GB)": offered once the paid library is installed and free builds are left
  // (st.free_library from the server: {bytes, builds}), or null.
  function removeFree(st, ctx) {
    const { h, icon, on } = ctx;
    const free = st && st.free_library;
    if (!free || !(Number(free.builds) > 0)) return null;
    return h("div", { class: "banner up-dl up-free" }, h("span", null, h("b", null, "The free library is no longer needed"),
      h("span", { class: "hint" }, "The ready-made library is in use. Removing the free library you built earlier frees the space. Your own documents stay.")),
    h("button", { class: "btn", type: "button", "data-action": "remove-free", onclick: () => on.removeFree(free) }, icon("trash", "sm"), `Remove the free library (${size(free.bytes)})`));
  }

  // "Check for updates", then "Update" when a newer pack is out (st.updates from the server: check_updates()'s
  // answer {packs: [{pack, name, installed, latest, newer}], newer, checked}, or {error}). Nothing is asked of the
  // download server until the person presses the button. It is the same check as Settings › Status's: it also looks
  // for a newer Starholm, which the Status page and the update notice show.
  function updates(st, ui, ctx) {
    const { h, icon, on } = ctx;
    const u = st && st.updates;
    const out = [];
    if (u && u.newer) {
      const lines = (u.packs || []).filter((p) => p.newer).map((p) => `${PACK_TITLE[p.pack] || str(p.name)}: ${str(p.latest)} (you have ${str(p.installed)})`);
      out.push(h("div", { class: "banner up-dl up-update" }, h("span", null, h("b", null, "An update is ready"),
        h("span", { class: "hint" }, `${lines.join(". ")}. Only the files that changed are downloaded, and what you have stays in use until the swap.`)),
      h("button", { class: "btn primary", type: "button", "data-action": "update", onclick: () => on.update() }, icon("down", "sm"), "Update")));
      return out;
    }
    out.push(h("div", { class: "banner up-dl" }, h("span", null, h("b", null, "Check for updates"),
      h("span", { class: "hint" }, "Looks for a newer Starholm and newer guides and library. It connects only when you press the button.")),
    h("button", { class: "btn", type: "button", "data-action": "check-updates", disabled: ui.checking ? "" : null, onclick: () => on.checkUpdates() },
      icon("retry", "sm"), root.SLRender.swapLabel(!!ui.checking, "Checking…", "Check for updates"))));
    if (u && u.error) out.push(h("p", { class: "msg error", role: "alert", "data-role": "updates-msg" }, str(u.error)));
    else if (u) out.push(h("p", { class: "msg", "data-role": "updates-msg" }, "Everything is up to date."));
    return out;
  }

  // ctx: {h, icon(name, size), on: {save(text), remove(), download(), pause(), resume(), again(), getUpgrade(),
  // resend(), writeToUs(), tryAgain(), removeFree(free), checkUpdates(), update()}}.
  // ui: {draft, error, checking, guideCount, library: {articles, size_bytes}, downloaded}.
  function view(st, ui, ctx) {
    const { h, icon, on } = ctx;
    ui = ui || {};
    const s = state(st, ui);
    const unlockList = (done) => h("ul", { class: `up-unlocks${done ? " done" : ""}` },
      unlocks(ui.guideCount).map(([b, t]) => h("li", null, icon(done ? "check" : "lock", "sm"),
        h("span", null, h("b", null, b), h("span", { class: "hint" }, t)))));
    const keyFacts = () => h("p", { class: "hint up-keyfacts" }, keyFactsText(st && st.key && st.key.verdict === "valid" ? st.key : null));
    const keyRow = () => h("div", { class: "set-row up-keyrow" },
      h("div", { class: "set-label" }, h("span", null, "Your key"), h("span", { class: "hint fpath", "data-role": "masked" }, str(st.key && st.key.masked))),
      h("div", { class: "set-control" }, h("span", { class: "btn-row" },
        h("span", { class: "val" }, icon("check", "sm"), "Saved"),
        h("button", { class: "btn", type: "button", "data-action": "remove", onclick: () => on.remove() }, "Remove key"))));
    const parts = [h("h3", null, "Upgrade")];

    if (s === "empty" || s === "invalid") {
      parts.push(h("p", { class: "lead" }, "The upgrade is optional. Everything you use now stays free. A key saves you the long setup, unlocks the content below, and supports the project."),
        unlockList(false));
      const input = h("input", { class: `input up-keyinput${s === "invalid" ? " up-bad" : ""}`, type: "text", id: "upgrade-key",
        placeholder: "SH1-…", "aria-invalid": s === "invalid" ? "true" : null, "aria-describedby": "upgrade-key-msg" });
      for (const [k, v] of [["spellcheck", "false"], ["autocapitalize", "off"], ["autocomplete", "off"]]) input.setAttribute(k, v);
      input.value = str(ui.draft);
      parts.push(h("div", { class: "set-row up-keyentry" },
        h("div", { class: "set-label" }, h("label", { for: "upgrade-key" }, "Your key"),
          h("span", { class: "hint" }, "It's in the email you got after buying, and on the thank-you page."))),
      h("div", { class: "form-row" }, input,
        h("button", { class: "btn primary", type: "button", "data-action": "save", onclick: () => on.save(input.value) }, "Save")),
      h("p", { class: `msg${s === "invalid" ? " error" : ""}`, id: "upgrade-key-msg", role: s === "invalid" ? "alert" : null },
        s === "invalid" ? str(ui.error) : st && st.key && st.key.verdict === "revoked" ? "This key has been withdrawn. If you think this is a mistake, write to us." : ""),
      keyFacts(),
      h("div", { class: "banner up-site" }, h("span", null, "Don't have a key? The website has the details. It opens in your browser."),
        h("button", { class: "btn primary", type: "button", "data-action": "get-upgrade", onclick: () => on.getUpgrade() }, icon("external", "sm"), "Get the upgrade")),
      h("div", { class: "form-row end" }, h("button", { class: "link", type: "button", "data-action": "resend", onclick: () => on.resend() }, "Lost your key? Get it sent again")));
      return { state: s, body: h("div", { class: "up-page", "data-state": s }, parts) };
    }

    if (s === "saved") {
      const est = st.estimate || {};
      parts.push(h("div", { class: "banner up-ok" }, icon("check", "sm"),
        h("span", null, h("b", null, "Key saved. Thank you for supporting Starholm."), " These are now unlocked:")),
      unlockList(true),
      h("div", { class: "banner up-dl" }, h("span", null, h("b", null, "Download now"),
        h("span", { class: "hint" }, `${est.disk ? `About ${size(est.bytes)} to download, ${size(est.disk)} on this computer once unpacked` : `About ${size(est.bytes)} in all`}: the guides, then the library. About ${duration(est.seconds)} on a fast connection. Nothing downloads until you press the button.`)),
      h("button", { class: "btn primary", type: "button", "data-action": "download", onclick: () => on.download() }, icon("down", "sm"), "Download now")),
      removeFree(st, ctx), keyRow(), keyFacts());
      return { state: s, body: h("div", { class: "up-page", "data-state": s }, parts) };
    }

    if (s === "downloading" || s === "paused") {
      const job = st.job || {};
      const packs = Array.isArray(job.packs) && job.packs.length ? job.packs
        : (st.order || ["guides", "library"]).map((p, i) => ({ pack: p, bytes: 0, done: 0, state: i ? "waiting" : "downloading", waiting_on: i ? "guides" : null }));
      // What comes over the network (download_*, less than the unpacked bytes for compressed packs), else the bytes.
      const netTotal = job.download_bytes != null ? Number(job.download_bytes) || 0 : Number(job.bytes) || 0;
      const netDone = job.download_bytes != null ? Number(job.download_done) || 0 : Number(job.done) || 0;
      const left = Math.max(0, netTotal - netDone);
      let head;
      if (s === "downloading") {
        // The time left comes from the server once it has seen enough of the download (packs.Speed); until then
        // the page says it is working it out, never a guess.
        const when = job.phase === "checking" ? CHECKING
          : job.seconds_left != null ? `Downloading · about ${duration(job.seconds_left)} left`
          : job.phase === "asking" ? "Starting the download"
          : job.phase === "downloading" ? "Downloading · working out the time left" : "Downloading";
        head = [h("b", null, when), h("button", { class: "btn", type: "button", "data-action": "pause", onclick: () => on.pause() }, icon("pause", "sm"), "Pause")];
      } else {
        head = [h("b", null, job.phase === "error" ? `Stopped · ${size(left)} left` : `Paused · ${size(left)} left`),
          h("button", { class: "btn primary", type: "button", "data-action": "resume", onclick: () => on.resume() }, icon("down", "sm"), "Resume")];
      }
      parts.push(h("div", { class: "up-dlhead" }, head));
      if (job.phase === "error") parts.push(h("p", { class: "msg error", role: "alert" }, `The download stopped: ${str(job.error)}. Resume carries on from where it stopped.`));
      // One bar for everything being downloaded, by bytes, whatever the packs (the server fetches them together).
      const checking = packs.find((p) => p.state === "checking");
      let line, bar, title, pct = 0;
      if (checking) {
        const all = Number(checking.to_check) || 0, seen = Math.min(Number(checking.checked) || 0, all);
        const cpct = all ? Math.round((seen / all) * 100) : 0;
        title = CHECKING;
        line = h("span", { class: "map-state st-dl" }, h("i", { class: "dot" }), all ? `${CHECKING} · ${size(seen)} of ${size(all)}` : CHECKING);
        bar = h("div", { class: "bar", role: "progressbar", "aria-valuenow": String(cpct), "aria-valuemin": "0", "aria-valuemax": "100", "aria-label": CHECKING },
          h("i", { class: "up-pct", "data-pct": String(cpct) }));
      } else {
        const total = netTotal || packs.reduce((n, p) => n + (Number(p.bytes) || 0), 0);
        const done = Math.min(netDone, total);
        pct = total ? Math.min(100, Math.floor((done / total) * 100)) : 0;
        const installing = s === "downloading" && (job.phase === "installing" || packs.some((p) => p.state === "installing") && done >= total);
        const word = s === "paused" ? "Paused" : installing ? "Putting it in place" : "Downloading";
        const names = packs.map((p) => p.pack);
        title = names.length === 1 ? PACK_TITLE[names[0]] || str(names[0]) : "The guides and the ready-made library";
        line = h("span", { class: "map-state st-dl" }, h("i", { class: "dot" }), total ? `${word} · ${size(done)} of ${size(total)}` : word);
        bar = h("div", { class: "bar", role: "progressbar", "aria-valuenow": String(pct), "aria-valuemin": "0", "aria-valuemax": "100", "aria-label": `${word}: ${title}` },
          h("i", { class: "up-pct", "data-pct": String(pct) }));
      }
      parts.push(h("div", { class: "up-dl", "data-pack": "all" },
        h("div", { class: "up-dl-top" }, h("b", null, title), checking ? null : h("span", { class: "hint" }, `${pct}%`)), line, bar));
      if (packs.some((p) => p.pack === "guides" && p.state === "done") && packs.some((p) => p.state !== "done")) {
        parts.push(h("p", { class: "hint", "data-role": "guides-ready" }, "The guides are in and work now. The library is still coming."));
      }
      parts.push(h("p", { class: "hint up-swap" }, SWAP_NOTE));
      if (s === "downloading") parts.push(h("p", { class: "hint" }, "You can keep using Starholm. If the computer sleeps or you quit, the download picks up where it stopped."));
      else {
        const ready = packs.filter((p) => p.state === "done" && p.pack !== "library").map((p) => p.pack);
        const works = ready.includes("guides") ? " The guides already downloaded work now." : "";
        parts.push(h("p", { class: "hint" }, `Nothing downloads while it's paused.${works}`));
      }
      parts.push(keyRow());
      return { state: s, body: h("div", { class: "up-page", "data-state": s }, parts) };
    }

    if (s === "done") {
      const lib = ui.library || {};
      const facts = [lib.articles ? `${Number(lib.articles).toLocaleString("en-GB")} articles` : null,
        lib.size_bytes ? size(lib.size_bytes) : null, ui.downloaded ? `downloaded ${ui.downloaded}` : null].filter(Boolean).join(" · ");
      parts.push(h("div", { class: "banner up-ok" }, icon("check", "sm"), h("span", null, "Everything is downloaded. The ready-made library is in use.")),
        unlockList(true),
        h("div", { class: "set-row" }, h("div", { class: "set-label" }, h("span", null, "Library"), h("span", { class: "hint" }, facts))),
        ...updates(st, ui, ctx),
        h("div", { class: "banner up-dl" }, h("span", null, h("b", null, "Download again"),
          h("span", { class: "hint" }, "Gets the newest library and guides. Your current ones stay in use until the swap. It never runs on its own.")),
        h("button", { class: "btn", type: "button", "data-action": "again", onclick: () => on.again() }, icon("retry", "sm"), "Download again")),
        removeFree(st, ctx), keyRow(), keyFacts());
      return { state: s, body: h("div", { class: "up-page", "data-state": s }, parts) };
    }

    // one computer too many: the download server has counted every computer the key allows (3, or 25 for an
    // organisation). Only the server can free one, so the page sends the person to the contact address.
    const job = st.job || {};
    const limit = Number(job.limit) || (st.key && st.key.computers) || 3;
    parts.push(h("div", { class: "banner up-warn", role: "alert" },
      h("span", null, h("b", null, `This key is already in use on ${computers(limit)}, the most it allows.`),
        h("span", null, `If you've replaced a computer or think this is a mistake, write to us at ${str(st.help_email)} and we'll sort it out.`))),
    h("div", { class: "form-row" }, h("span", { class: "btn-row" },
      h("button", { class: "btn primary", type: "button", "data-action": "write", onclick: () => on.writeToUs() }, icon("mail", "sm"), "Write to us"),
      h("button", { class: "btn", type: "button", "data-action": "try-again", onclick: () => on.tryAgain() }, "Try again"))),
    h("p", { class: "hint" }, `Write to us opens an email to ${str(st.help_email)} in your mail app, with your key's first letters filled in so we can find it. Nothing is sent until you press send.`),
    keyRow(), h("p", { class: "hint" }, "The guides and library you already have keep working."));
    return { state: s, body: h("div", { class: "up-page", "data-state": s }, parts) };
  }

  // ---------- locks: what the paid features show without a key ----------
  const chip = (h, icon) => h("span", { class: "up-lockchip" }, icon("lock", "sm"), LOCKED);
  const actions = (h, icon, on) => h("div", { class: "dlg-actions" },
    h("button", { class: "btn", type: "button", "data-action": "not-now", onclick: () => on.notNow() }, "Not now"),
    h("button", { class: "btn primary", type: "button", "data-action": "get-upgrade", onclick: () => on.getUpgrade() }, icon("lock", "sm"), "Get the upgrade"));

  // A locked guide's preview: preview is the server's {title, summary, group, headings} (423 from /api/guides/<slug>).
  function lockedGuide(preview, ctx) {
    const { h, icon, on } = ctx;
    const p = preview || {};
    const heads = Array.isArray(p.headings) ? p.headings.map(str).filter(Boolean) : [];
    return h("div", { class: "up-preview", "data-lock": "guide" },
      h("div", { class: "up-prev" },
        h("div", { class: `gcover up-thumb ${root.SLRender ? root.SLRender.coverClass(p.palette) : "cv-plain"}`, "aria-hidden": "true" }, root.SLRender && p.slug ? root.SLRender.coverArt(str(p.slug)) : null,
          h("span", { class: "gcover-group" }, str(p.group)), h("span", { class: "gcover-title" }, str(p.title))),
        h("div", null, chip(h, icon), h("h3", { id: "lock-title" }, str(p.title)), h("p", { class: "muted" }, str(p.summary)),
          heads.length ? [h("p", { class: "sub-h up-inthis" }, "In this guide"), h("ul", { class: "up-heads" }, heads.map((t) => h("li", null, t)))] : null)),
      h("p", { class: "hint" }, UPSELL), actions(h, icon, on));
  }

  // A guide the key unlocks whose text isn't downloaded yet (409 {"download": true} from /api/guides/<slug>): the
  // Upgrade page's "Download now" gets it, so the dialog sends the person there.
  function downloadGuide(preview, ctx) {
    const { h, icon, on } = ctx;
    const p = preview || {};
    return h("div", { class: "up-preview", "data-lock": "download" },
      h("div", { class: "up-prev" },
        h("div", { class: `gcover up-thumb ${root.SLRender ? root.SLRender.coverClass(p.palette) : "cv-plain"}`, "aria-hidden": "true" }, root.SLRender && p.slug ? root.SLRender.coverArt(str(p.slug)) : null,
          h("span", { class: "gcover-group" }, str(p.group)), h("span", { class: "gcover-title" }, str(p.title))),
        h("div", null, h("h3", { id: "lock-title" }, str(p.title)), h("p", { class: "muted" }, str(p.summary)))),
      h("div", { class: "banner up-dl" }, h("span", null, h("b", null, "Download now"),
        h("span", { class: "hint" }, "Your key unlocks this guide. It comes with the guides, which download first. Nothing downloads until you press the button."))),
      h("div", { class: "dlg-actions" },
        h("button", { class: "btn", type: "button", "data-action": "not-now", onclick: () => on.notNow() }, "Not now"),
        h("button", { class: "btn primary", type: "button", "data-action": "download", onclick: () => on.openUpgrade() }, icon("down", "sm"), "Download now")));
  }

  function lockedPack(ctx) {
    const { h, icon, on } = ctx;
    return h("div", { class: "up-preview", "data-lock": "pack" },
      h("div", { class: "up-prev" }, h("div", { class: "up-thumb-plain", "aria-hidden": "true" }, icon("print")),
        h("div", null, chip(h, icon), h("h3", { id: "lock-title" }, "Print emergency pack"),
          h("p", { class: "muted" }, "A few pages to keep by the door: your country's emergency numbers, the key steps from every guide, and wallet cards to cut out. Your family plan fills in the meeting place and contacts."),
          h("p", { class: "sub-h up-inthis" }, "What's in it"),
          h("ul", { class: "up-heads" }, ["Emergency numbers for your country", "Key steps: CPR, bleeding, fire, flood, shelter",
            "Wallet cards for each person", "Your family plan, if you filled it in"].map((t) => h("li", null, t))))),
      h("p", { class: "hint" }, UPSELL), actions(h, icon, on));
  }

  // The booklets ("Save all booklets", a guide's Booklet button).
  function lockedBooklets(ctx) {
    const { h, icon, on } = ctx;
    return h("div", { class: "up-preview", "data-lock": "booklets" },
      h("div", { class: "up-prev" }, h("div", { class: "up-thumb-plain", "aria-hidden": "true" }, icon("doc")),
        h("div", null, chip(h, icon), h("h3", { id: "lock-title" }, "Booklets to print and fold"),
          h("p", { class: "muted" }, "Every guide as a fold-up booklet: A5 pages, two to each A4 sheet, to keep with your kit."))),
      h("p", { class: "hint" }, UPSELL), actions(h, icon, on));
  }

  // maps: [{name, size}] from /api/maps.
  function lockedMap(maps, ctx) {
    const { h, icon, on } = ctx;
    const list = (Array.isArray(maps) ? maps : []).map((m) => `${str(m.name)}${m.size ? `, about ${size(m.size)}` : ""}`);
    return h("div", { class: "up-preview", "data-lock": "maps" },
      chip(h, icon), h("h3", { id: "lock-title" }, "Offline maps"),
      h("p", { class: "muted" }, "Street maps of a whole region that work with no internet: search for a place, see hospitals, pharmacies and shelters, and print a map page for your go-bag."),
      list.length ? h("ul", { class: "up-heads" }, list.map((t) => h("li", null, t))) : null,
      h("p", { class: "hint" }, "Maps carry the credit © OpenStreetMap contributors."),
      h("p", { class: "hint" }, UPSELL), actions(h, icon, on));
  }

  // One map card on the Maps tab without a key: m is an entry of /api/maps.
  // meta: the card's "about 826 MB · June 2026 build" line, as maps.js writes it.
  function lockedMapCard(m, meta, ctx) {
    const { h, icon, on } = ctx;
    return h("article", { class: "map-card up-mapcard", "data-map": str(m.id) },
      h("div", { class: "map-card-head" }, h("span", { class: "map-card-region" }, str(m.region)), h("span", { class: "map-card-name" }, str(m.name))),
      h("div", { class: "map-card-body" }, meta ? h("div", { class: "map-meta" }, str(meta)) : null,
        h("div", { class: "map-state" }, icon("lock", "sm"), LOCKED),
        h("div", { class: "map-btns" },
          h("button", { class: "btn", type: "button", "data-action": "preview", onclick: () => on.previewMap() }, icon("eye", "sm"), "Preview"),
          h("button", { class: "btn primary", type: "button", "data-action": "get-upgrade", onclick: () => on.getUpgrade() }, icon("lock", "sm"), "Get the upgrade"))));
  }

  // The family plan page without a key: the real form, greyed and inert, behind a card. form: SLPlan's
  // {PERSON, SECTIONS}, so the preview is the form itself.
  function lockedPlan(form, ctx) {
    const { h, icon, on } = ctx;
    const person = (form && form.PERSON) || [];
    const sections = (form && form.SECTIONS) || [];
    const ghost = h("div", { class: "up-locked-form", "aria-hidden": "true" },
      h("p", { class: "facts" }, "Fill it in together. It prints as a page for the fridge, with a wallet card for each person, and fills in the emergency pack."),
      h("fieldset", { class: "plan-sec", disabled: true }, h("legend", null, "Who lives here"),
        h("div", { class: "plan-row plan-row-head" }, person.map(([, label]) => h("span", null, label)), h("span", null)),
        [0, 1].map(() => h("div", { class: "plan-row" }, person.map(() => h("input", { class: "input", type: "text", disabled: true, tabindex: "-1" })), h("span", null)))),
      sections.map((sec) => h("fieldset", { class: "plan-sec", disabled: true }, h("legend", null, str(sec.legend)),
        h("div", { class: "plan-grid" }, sec.fields.map(([, label]) => h("div", { class: "plan-fld" }, h("label", null, label),
          h("div", { class: "plan-ctl" }, h("textarea", { class: "input", disabled: true, tabindex: "-1" }))))))));
    ghost.setAttribute("inert", "");  // a preview: nothing in it can be focused or clicked
    return h("div", { class: "up-lockwrap", "data-lock": "plan" }, ghost,
      h("div", { class: "up-lockcard", role: "group", "aria-labelledby": "plan-lock-title" }, icon("lock"),
        h("h3", { id: "plan-lock-title" }, "The family plan is included in the upgrade"),
        h("p", null, "Fill it in together: who lives here, who to call, where to meet and where things are. It prints as a page for the fridge, with a wallet card for each person, and fills in the emergency pack. It stays on this computer."),
        h("div", { class: "btn-row" }, h("button", { class: "btn primary", type: "button", "data-action": "get-upgrade", onclick: () => on.getUpgrade() }, icon("lock", "sm"), "Get the upgrade")),
        h("p", { class: "hint" }, "The form behind this card is a preview.")));
  }

  // The line over a locked guide shelf.
  function shelfNote(ctx) {
    const { h, icon, on } = ctx;
    return h("div", { class: "banner up-shelf" },
      h("span", null, "Three guides are free: CPR and choking; bleeding, burns and heat stroke; and leaving home in a hurry. Emergency numbers are always free. The upgrade adds the other guides, the booklets and the emergency pack."),
      h("button", { class: "btn primary", type: "button", "data-action": "get-upgrade", onclick: () => on.getUpgrade() }, icon("lock", "sm"), "Get the upgrade"));
  }

  // After a view is in the page: the progress bars' widths (no inline styles under the page's CSP).
  function paint(el) {
    for (const bar of el.querySelectorAll(".up-pct")) bar.style.width = `${Number(bar.dataset.pct) || 0}%`;
  }

  const API = { LOCKED, BUY_URL, state, view, lockedGuide, downloadGuide, lockedPack, lockedBooklets, lockedMap, lockedMapCard, lockedPlan, shelfNote,
    paint, size, duration, unlocks, computers, keyFactsText, CHECKING };
  root.SLUpgrade = API;
  if (typeof module !== "undefined" && module.exports) module.exports = API;
})(typeof window !== "undefined" ? window : globalThis);
