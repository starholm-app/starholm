// Starholm's own updates: the Updates rows on Settings › Status (the version, Check for updates and "Download updates
// automatically") and the notice that a new version is ready, with Restart now and Later.
//
// Pure views: each function takes the server's answer (GET /api/updates, updates_status() in app/server.py) and returns
// elements built with render.js's h(). The buttons call the handlers app.js passes in ctx.on, so this file itself never
// calls the server. Checked by tests/interface_updates_check.js.
(function (root) {
  "use strict";
  const str = (v) => (typeof v === "string" ? v : v == null ? "" : String(v));
  const AUTO_HINT = "Starholm checks when it opens and every hour, downloads new versions in the background and tells you when one is ready. When this is off, it goes online for updates only when you press Check for updates.";
  const CHECK_HINT = "Looks for a newer Starholm and, with your key, newer guides and library.";

  // 210_000_000 -> "210 MB", decimal units as elsewhere in the app
  function size(n) {
    n = Number(n) || 0;
    if (n >= 1e9) return `${Math.round(n / 1e8) / 10} GB`;
    if (n >= 1e6) return `${Math.round(n / 1e6)} MB`;
    return `${Math.max(1, Math.round(n / 1e3))} KB`;
  }

  // The line under Check for updates: what the last check found, or what is happening now. ui.checking: the button
  // was pressed and the answer hasn't come back.
  // Returns {text, error, action: null | "restart" | "upgrade"}.
  function statusLine(st, ui = {}) {
    const app = (st && st.app) || {};
    const packs = st && st.packs;
    if (ui.checking) return { text: "Checking…", error: false, action: null };
    const parts = [];
    let error = false, action = null;
    if (app.ready) {
      parts.push(`Starholm ${str(app.ready.version)} is ready to install.`);
      action = "restart";
    } else if (app.phase === "downloading") {
      const total = Number(app.bytes) || 0, done = Math.min(Number(app.done) || 0, total);
      const ver = app.latest ? ` ${str(app.latest.version)}` : "";
      parts.push(total ? `Downloading Starholm${ver} · ${size(done)} of ${size(total)}` : `Downloading Starholm${ver}`);
    } else if (app.phase === "unpacking") {
      parts.push("Checking the download…");
    } else if (app.phase === "error" && app.error) {
      parts.push(str(app.error));
      error = true;
    } else if (app.checked) {
      parts.push("Starholm is up to date.");
    }
    if (packs && packs.error) { parts.push(str(packs.error)); error = true; }
    else if (packs && packs.newer) {
      parts.push("Newer guides or a newer library are ready. Update them on the Upgrade page.");
      if (!action) action = "upgrade";
    } else if (packs && app.checked && !app.ready && app.phase !== "downloading" && app.phase !== "error") {
      parts[parts.length - 1] = "Starholm, the guides and the library are up to date.";
    }
    return { text: parts.join(" "), error, action };
  }

  // The Updates rows on Settings › Status. ctx: {h, icon(name, size), on: {check(), auto(on), restart(), upgrade()}}.
  function rows(st, ui, ctx) {
    const { h, icon, on } = ctx;
    ui = ui || {};
    const app = (st && st.app) || {};
    const line = statusLine(st, ui);
    const busy = !!ui.checking || app.phase === "checking";
    const lineEl = h("p", { class: `msg${line.error ? " error" : ""}`, id: "updates-msg", role: line.error ? "alert" : null, "data-role": "updates-msg" },
      line.text,
      line.action === "restart" ? h("button", { class: "link upd-link", type: "button", "data-action": "show-notice", onclick: () => on.restart() }, "Restart now") : null,
      line.action === "upgrade" ? h("button", { class: "link upd-link", type: "button", "data-action": "open-upgrade", onclick: () => on.upgrade() }, "Open the Upgrade page") : null);
    const toggle = h("button", { class: "toggle", type: "button", role: "switch", id: "updates-auto", "aria-checked": String(!!app.auto),
      "aria-labelledby": "updates-auto-label", "data-action": "auto", onclick: () => on.auto(!app.auto) }, h("span", { class: "switch" }));
    return [
      h("h4", { class: "sub-h upd-head" }, "Updates"),
      h("div", { class: "set-row" },
        h("div", { class: "set-label" }, h("span", null, `Version ${str(app.version)}`), h("span", { class: "hint" }, CHECK_HINT)),
        h("div", { class: "set-control" }, h("button", { class: "btn", type: "button", id: "updates-check", "data-action": "check", disabled: busy ? "" : null, onclick: () => on.check() },
          icon("retry", "sm"), root.SLRender.swapLabel(busy, "Checking…", "Check for updates")))),
      lineEl,
      h("div", { class: "set-row" },
        h("div", { class: "set-label" }, h("span", { id: "updates-auto-label" }, "Download updates automatically"), h("span", { class: "hint" }, AUTO_HINT)),
        h("div", { class: "set-control" }, toggle)),
    ];
  }

  // The notice when a new version is downloaded and checked, on the Mac and on Windows: one line and two buttons, in a
  // small card above Settings at the bottom of the side panel (app.css .upd-card, app.js placeUpdateCard). The release
  // notes stay in latest.json and on the Status page's line; the card doesn't repeat them. ready: snapshot().ready
  // {version, notes, install, why}. ctx.on: {restart(), later()}.
  function notice(ready, extra, ctx) {
    const { h, on } = ctx;
    const r = ready || {};
    return h("div", { class: "upd-notice", "data-state": "ready" },
      h("h3", { id: "update-title" }, "A new version of Starholm is ready. Restart to update."),
      h("div", { class: "dlg-actions" },
        h("button", { class: "btn", type: "button", "data-action": "later", onclick: () => on.later() }, "Later"),
        h("button", { class: "btn primary", type: "button", "data-action": "restart", onclick: () => on.restart() }, "Restart now")));
  }


  // After Restart now, when Starholm can't replace itself: out is the server's {mode: "manual", why, steps}.
  function manual(out, ctx) {
    const { h, on } = ctx;
    const o = out || {};
    return h("div", { class: "upd-notice", "data-state": "manual" },
      h("h3", { id: "update-title" }, "Install the update yourself"),
      h("p", null, str(o.why)), h("p", null, str(o.steps)),
      h("div", { class: "dlg-actions" }, h("button", { class: "btn primary", type: "button", "data-action": "close", onclick: () => on.close() }, "OK")));
  }

  // While the app closes and opens again.
  function restarting(version, ctx) {
    const { h } = ctx;
    return h("div", { class: "upd-notice", "data-state": "restarting" },
      h("h3", { id: "update-title" }, "Restarting…"),
      h("p", null, `Starholm closes and opens again as version ${str(version)}.`));
  }

  // What a screen reader hears when the card appears: it doesn't take the focus.
  function announce(ready) {
    return `Starholm ${str(ready && ready.version)} is ready to install. Restart now or Later, in the left panel above Settings.`;
  }

  // What the last restart did, shown once after it: last is snapshot().last_restart {ok, error, version}.
  function restartResult(last) {
    if (!last) return null;
    if (last.ok) return `Starholm was updated to version ${str(last.version)}.`;
    return `The update couldn't be installed, so Starholm ${str(last.version)} is still in use. ${str(last.error)}`.trim();
  }

  const API = { statusLine, rows, notice, manual, restarting, restartResult, announce, size, AUTO_HINT, CHECK_HINT };
  root.SLUpdateViews = API;
  if (typeof module !== "undefined" && module.exports) module.exports = API;
})(typeof window !== "undefined" ? window : globalThis);
