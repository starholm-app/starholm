// Starholm interface: turning text into page content.
//
// Safety rules for this file:
// - Everything handed in here (answers, articles, file names) is untrusted. It reaches the page only as text nodes and
//   as elements built with createElement. Nothing here assigns innerHTML, outerHTML or insertAdjacentHTML.
// - Markdown never makes an <a>, never sets an href and never loads an address. A web address (http or https only,
//   checked by safeWebUrl) is drawn as a button whose text is the address itself; a click hands the address to the
//   opener app.js sets (setWebOpener), which asks the server to open it in the Mac's default browser. With no opener,
//   or any other address, it stays plain text (urlText). [text](#anchor) and footnotes scroll within the document;
//   [text](guide:slug#anchor) asks app.js to open another guide (opts.onGuide). None of them load anything here.
// - Images stay text, except a guide's own figure (![alt](name.svg), a bare file name) inside a guide.
// - parseSteps and splitAnswer use no DOM, so they can be checked outside a browser.
(function (root) {
  "use strict";

  const str = (v) => (typeof v === "string" ? v : v == null ? "" : String(v));

  // ---------- the `steps` decision path ----------
  // Format (BUILD-SPEC section 7):
  //   1? Is the water cloudy or brown?
  //   yes: Let it settle and filter it. -> 2
  //   no: No need to filter it first. -> 2
  // Each question is `N? text`; each branch is `yes:` or `no:`, what to do, then `-> N` or `-> end`.
  // Returns {ok: true, start, nodes: {N: {q, yes: {say, next}, no: {say, next}}}} or {ok: false, why}.
  // `next` is null for `end`. A missing branch, a jump to a step that doesn't exist or a loop makes it fail.
  // Every pattern here and below runs on each render while an answer streams, so each must take linear time on any
  // line: no two neighbouring runs that can match the same characters. Lines longer than MAX_LINE skip the patterns
  // altogether and are drawn as plain text.
  const MAX_LINE = 5000;
  const LEAD_MAX = 200;  // characters in a lead-in line before the opening bullets
  const STEP_Q = /^(\d{1,3})\s*\?\s*(\S.*)$/;
  const STEP_B_HEAD = /^(yes|no)\s*:/i;
  const STEP_TARGET = /^(end|\d{1,3})$/i;
  const MAX_STEPS = 40;
  // A branch line: `yes:` or `no:`, what to do, then `-> N` or `-> end`, read without a backtracking pattern.
  function parseBranch(line) {
    const m = STEP_B_HEAD.exec(line);
    if (!m) return null;
    const rest = line.slice(m[0].length);
    const a = rest.lastIndexOf("->");
    const b = rest.lastIndexOf("→");
    const at = Math.max(a, b);
    if (at < 0) return null;
    let target = rest.slice(at + (at === a ? 2 : 1)).trim();
    if (target.endsWith(".")) target = target.slice(0, -1).trim();
    if (!STEP_TARGET.test(target)) return null;
    return { key: m[1].toLowerCase(), say: rest.slice(0, at).trim(), target: target.toLowerCase() };
  }

  function parseSteps(src) {
    const fail = (why) => ({ ok: false, why });
    const lines = str(src).replace(/\r\n?/g, "\n").split("\n").map((l) => l.trim()).filter(Boolean);
    const nodes = {};
    const order = [];
    let cur = null;
    for (const line of lines) {
      let m;
      if (line.length > MAX_LINE) return fail("a line is too long");
      if ((m = STEP_Q.exec(line))) {
        const id = String(Number(m[1]));
        if (nodes[id]) return fail(`step ${id} appears twice`);
        if (order.length >= MAX_STEPS) return fail("too many steps");
        cur = { q: m[2].trim(), yes: null, no: null };
        nodes[id] = cur;
        order.push(id);
        continue;
      }
      if ((m = parseBranch(line))) {
        if (!cur) return fail("a branch before the first question");
        if (cur[m.key]) return fail(`two "${m.key}" branches in one step`);
        cur[m.key] = { say: m.say, next: m.target === "end" ? null : String(Number(m.target)) };
        continue;
      }
      return fail(`a line that isn't a question or a branch: ${line.slice(0, 60)}`);
    }
    if (!order.length) return fail("no questions");
    for (const id of order) {
      const n = nodes[id];
      if (!n.yes || !n.no) return fail(`step ${id} is missing a branch`);
      for (const b of [n.yes, n.no]) if (b.next !== null && !nodes[b.next]) return fail(`step ${id} jumps to step ${b.next}, which doesn't exist`);
    }
    // A loop: any path from the first step that comes back to a step already on it.
    const state = {};  // id -> 1 while on the current path, 2 when done
    const visit = (id) => {
      if (state[id] === 1) return true;
      if (state[id] === 2) return false;
      state[id] = 1;
      for (const b of [nodes[id].yes, nodes[id].no]) if (b.next !== null && visit(b.next)) return true;
      state[id] = 2;
      return false;
    };
    if (visit(order[0])) return fail("the steps loop back on themselves");
    return { ok: true, start: order[0], nodes };
  }

  // ---------- an answer split into its parts ----------
  // Returns {nomatch, call, lead, summary: [bullet text], paths: [{key, parsed}], blocks: [{kind: "md"|"plain", text}]}.
  // `lead` is a short line written before the opening bullets, or null.
  // `call` is the number in a no-match answer's "Call <number> now." line, or null.
  // - The trailing "Sources:" line (kept in the model's output for the evals) is hidden.
  // - Bullets at the very start are the summary.
  // - A `steps` block that parses becomes a decision path; one that doesn't is shown as plain text where it was.
  //   While streaming (live), a block that hasn't closed yet is left out.
  const SOURCES_RE = /^\s*[*_]{0,2}sources?[*_]{0,2}\s*:/i;
  const BULLET_RE = /^ {0,3}[-*+•][ \t]+(\S.*)$/;
  const FENCE_OPEN = /^ {0,3}(`{3,}|~{3,})[ \t]*steps[ \t]*$/i;
  const FOLLOW_RE = /^[ \t>]*(?:[*_#]+[ \t]*)?(?:follow[- ]?ups?|suggested (?:follow[- ]up )?questions)[*_ ]*:/i;  // as core.py FOLLOW_UPS
  // The no-match answer: the one line, or the one line and "Call <number> now." for an emergency.
  // The call line may be long ("Call 998 for an ambulance or 999 for the police now."): it is read up to 300 characters.
  const NOMATCH_RE = /^i couldn[’']t find this in your library\.?(?:\s+call\s+([^\n]{1,300}?)\s+now\.?)?$/i;

  // The last line matching re, with only blank lines or list items after it: cut from there to the end.
  const short = (l) => l.length <= MAX_LINE;
  const listTail = (l) => !l.trim() || (short(l) && (BULLET_RE.test(l) || /^ {0,3}\d{1,3}[.)][ \t]/.test(l)));
  function cutTail(lines, re) {
    for (let i = lines.length - 1; i >= 0; i--) {
      if (!short(lines[i]) || !re.test(lines[i])) continue;
      return lines.slice(i + 1).every(listTail) ? lines.slice(0, i) : lines;
    }
    return lines;
  }
  // Hides the "Sources:" line and, before it, the "Follow-ups:" block (the server sends follow-ups in `done`;
  // the streamed text still carries the block).
  const stripSources = (lines) => cutTail(cutTail(lines, SOURCES_RE), FOLLOW_RE);

  // A model that leaks its draft writes it, then a line "</think>", then the real answer: keep only what follows the
  // last such line. (The server cuts there too; this is the second guard.)
  function afterThink(lines) {
    for (let i = lines.length - 1; i >= 0; i--) if (lines[i].trim() === "</think>") return lines.slice(i + 1);
    return lines;
  }

  function splitAnswer(text, { live = false } = {}) {
    let lines = afterThink(str(text).replace(/\r\n?/g, "\n").split("\n"));
    lines = stripSources(lines);
    const out = { nomatch: false, call: null, lead: null, summary: [], paths: [], blocks: [] };
    const whole = lines.join("\n").trim();
    const nm = NOMATCH_RE.exec(whole);
    if (nm) { out.nomatch = true; out.call = nm[1] ? nm[1].replace(/\*+/g, "").trim() : null; return out; }

    let i = 0;
    while (i < lines.length && !lines[i].trim()) i++;
    // A lead-in: one short line of plain text before the opening bullets ("Here is what to do."). It is kept as a
    // line of its own above the summary, so the bullets still read as the summary.
    if (i < lines.length && short(lines[i]) && lines[i].trim().length <= LEAD_MAX && !BULLET_RE.test(lines[i]) && !/^\s*(?:#|>|\d{1,3}[.)]\s|`{3}|~{3}|\|)/.test(lines[i])) {
      let j = i + 1;
      while (j < lines.length && !lines[j].trim()) j++;
      if (j < lines.length && short(lines[j]) && BULLET_RE.test(lines[j])) { out.lead = lines[i].trim(); i = j; }
    }
    // The summary: consecutive bullets at the start (a wrapped line indented under a bullet stays with it).
    while (i < lines.length) {
      const m = short(lines[i]) ? BULLET_RE.exec(lines[i]) : null;
      if (m) { out.summary.push(m[1].trim()); i++; continue; }
      if (out.summary.length && lines[i].trim() && /^[ \t]{2,}\S/.test(lines[i])) { out.summary[out.summary.length - 1] += ` ${lines[i].trim()}`; i++; continue; }
      if (out.summary.length && !lines[i].trim()) {
        let j = i;
        while (j < lines.length && !lines[j].trim()) j++;
        if (j < lines.length && short(lines[j]) && BULLET_RE.test(lines[j])) { i = j; continue; }
      }
      break;
    }

    let buf = [];
    const flush = () => { if (buf.join("").trim()) out.blocks.push({ kind: "md", text: buf.join("\n") }); buf = []; };
    while (i < lines.length) {
      const m = short(lines[i]) ? FENCE_OPEN.exec(lines[i]) : null;
      if (!m) { buf.push(lines[i]); i++; continue; }
      const fence = m[1];
      const body = [];
      let j = i + 1;
      while (j < lines.length && !lines[j].trim().startsWith(fence)) body.push(lines[j++]);
      const closed = j < lines.length;
      flush();
      if (!closed && live) { i = lines.length; break; }
      const parsed = closed ? parseSteps(body.join("\n")) : { ok: false, why: "the block never closes" };
      if (parsed.ok) out.paths.push({ key: out.paths.length, parsed });
      else out.blocks.push({ kind: "plain", text: body.join("\n").trim() });
      i = closed ? j + 1 : lines.length;
    }
    flush();
    return out;
  }

  // ---------- Markdown block patterns (DOM-free, so the timing checks can run them) ----------
  const LIST_RE = /^( {0,3})([-*+•]|\d{1,9}[.)])[ \t]+(.*)$/;
  const FENCE_RE = /^ {0,3}(`{3,}|~{3,})[ \t]*([\w+#-]*)/;
  const QUOTE_RE = /^ {0,3}> ?/;
  const HEADING_START = /^ {0,3}(#{1,6})[ \t]+(.*)$/;
  const SEP_CELL = /^:?-+:?$/;
  // A heading: [level, text] or null. The closing #s are taken off with a loop, not a pattern.
  function heading(line) {
    const m = HEADING_START.exec(line);
    if (!m) return null;
    let text = m[2].trimEnd();
    let j = text.length;
    while (j > 0 && text[j - 1] === "#") j--;
    if (j === 0) text = "";
    else if (j < text.length && (text[j - 1] === " " || text[j - 1] === "\t")) text = text.slice(0, j).trimEnd();
    return [m[1].length, text];
  }
  // A thematic break (---, ***, ___, spaces allowed between): checked character by character.
  function isHr(line) {
    let indent = 0;
    while (indent < line.length && line[indent] === " ") indent++;
    if (indent > 3) return false;
    const rest = line.slice(indent).replace(/[ \t]/g, "");
    if (rest.length < 3 || !"-*_".includes(rest[0])) return false;
    for (const c of rest) if (c !== rest[0]) return false;
    return true;
  }
  // A table's separator row (| --- | :--: |): split on pipes, each cell checked alone.
  function isTableSep(line) {
    let t = line.trim();
    if (!t.includes("-")) return false;
    if (t.startsWith("|")) t = t.slice(1);
    if (t.endsWith("|")) t = t.slice(0, -1);
    const cells = t.split("|").map((c) => c.trim());
    return cells.length > 0 && cells.every((c) => SEP_CELL.test(c));
  }
  const MD = { LIST_RE, FENCE_RE, QUOTE_RE, HEADING_START, heading, isHr, isTableSep };

  // ---------- which emergency numbers may show in red (DOM-free; e is api.js's cleaned /api/emergency) ----------
  // The ASCII digit runs of a number ("112 or 15" gives 112 and 15). Other digits (Arabic-Indic, fullwidth) don't count.
  const dialable = (v) => (str(v).match(/[0-9][0-9 ]*[0-9]|[0-9]/g) || []).map((x) => x.trim());
  const LOCAL = "your local emergency number";
  // Who to call, as emergency.py call_line() decides it: the country card's ambulance number (or its first emergency
  // number); "your local emergency number" without a card. Never another country's number.
  // {numbers: [to show in red], text (when there is no number)}
  function callTarget(e) {
    const none = { numbers: [], text: LOCAL };
    const amb = e && e.current ? dialable(e.current.ambulance) : [];
    return amb.length ? { numbers: amb, text: null } : none;
  }
  // Numbers body text may show in red: the card's ambulance and police numbers; none without a card.
  function bodyNumbers(e) {
    if (!e || !e.current) return [];
    return [...new Set([...dialable(e.current.ambulance), ...dialable(e.current.police)])];
  }
  // Every number on the card a model's number may match; none without a card.
  function storedNumbers(e) {
    if (!e || !e.current) return new Set();
    return new Set(e.current.numbers.flatMap((n) => dialable(n.number)));
  }
  // The no-match answer's call line: {numbers, text}. The model's number counts only when each of its digit runs is
  // on the card, and then only those runs are drawn (never the model's own string).
  function callLine(modelNumber, e) {
    const parts = dialable(modelNumber);
    const stored = storedNumbers(e);
    if (parts.length && parts.every((x) => stored.has(x))) return { numbers: parts, text: null };
    return callTarget(e);
  }
  // Every number on the current country's card, spaces taken out: the numbers a call-only sentence may name and
  // still be left unchecked.
  function heldNumbers(e) {
    if (!e || !e.current) return [];
    const out = new Set();
    const c = e.current;
    for (const v of [...c.numbers.map((n) => n && n.number), c.ambulance, c.police]) for (const run of dialable(v)) out.add(run.replace(/ /g, ""));
    return [...out];
  }
  // Who a no-match answer's call line names: {numbers, text}, or null for no call line. The call target saved with
  // the answer (no_match_call, emergency_call) is redrawn only when every number in it is on the current card: a
  // chat saved under another country, or before the country cards (an old two-country "both" line), gets the
  // current card's target instead, and "your local emergency number" without a card. With no saved target, as for
  // a chat saved before it existed, the model's call line is read through callLine (card numbers only). A call line
  // is never lost: any sign of one shows it.
  const NUMBER = /^\d[\d ]{0,9}$/;
  function noMatchCall(parts, flagCall, e) {
    if (flagCall && typeof flagCall === "object") {
      const numbers = (Array.isArray(flagCall.numbers) ? flagCall.numbers : []).map(str).filter((n) => NUMBER.test(n));
      const stored = storedNumbers(e);
      if (numbers.length && !flagCall.both && numbers.every((n) => dialable(n).every((x) => stored.has(x)))) return { numbers, text: null };
      return callTarget(e);  // a number not on the current card, or none usable: the card's target, else the words
    }
    return parts && parts.call ? callLine(parts.call, e) : null;
  }
  // A call target as the page writes it: "Call 998 now." ("" for none).
  const callText = (t) => (!t ? "" : t.numbers.length ? `Call ${t.numbers.join(" or ")} now.` : `Call ${t.text} now.`);
  // The no-match answer as the page shows it, in words (for Copy): the page's own lines, never the model's.
  function noMatchText(parts, flagCall, e) {
    const t = noMatchCall(parts, flagCall, e);
    return ["I couldn't find this in your library.", t ? callText(t) : null].filter(Boolean).join("\n");
  }
  // The call line the app adds under an answer to an emergency question when it found nothing to give the model
  // (done.emergency_call): the saved target when its numbers are on the current card, else the card's, or null.
  const emergencyCall = (flag, e) => (flag && typeof flag === "object" ? noMatchCall(null, flag, e) : null);
  const EMERGENCY = { dialable, callTarget, bodyNumbers, storedNumbers, callLine, heldNumbers, noMatchCall, noMatchText,
    callText, emergencyCall };

  // The answer as the person sees it, as Markdown: without the trailing "Sources:" line. For Copy.
  const visibleText = (text) => stripSources(afterThink(str(text).replace(/\r\n?/g, "\n").split("\n"))).join("\n").trim();

  // ---------- heading anchors, guide figures, empty documents ----------
  // A heading's anchor: lower-case letters and digits joined by single dashes, as Obsidian and GitHub make them.
  // Only letters, digits and dashes survive, so an id is always safe inside a [id="…"] selector.
  function slugify(text) {
    const s = str(text).slice(0, 400).normalize("NFKC").toLowerCase().replace(/[^\p{L}\p{N}]+/gu, "-")
      .replace(/^-/, "").slice(0, 80).replace(/-$/, "");
    return s || "section";
  }
  // base, or base-1, base-2… when a heading with the same text came earlier in the document.
  function uniqueId(base, used) {
    let id = base;
    for (let n = 1; used.has(id); n++) id = `${base}-${n}`;
    used.add(id);
    return id;
  }
  const HEADING_PREFIX = "h-";  // keeps heading ids apart from the page's own element ids
  // The id a [text](#anchor) link jumps to.
  function anchorId(anchor) {
    let a = str(anchor);
    try { a = decodeURIComponent(a); } catch { /* keep it as written */ }
    return HEADING_PREFIX + slugify(a);
  }
  // A guide's figure: only a bare file name (lower-case letters, digits, dashes, then .svg) inside a guide whose
  // slug is just as plain. Returns the address it is served at, or null so the image stays text.
  const GUIDE_SLUG = /^[a-z0-9-]{1,80}$/;
  const FIGURE_NAME = /^[a-z0-9-]{1,80}\.svg$/;
  function guideFigure(slug, target) {
    if (typeof slug !== "string" || !GUIDE_SLUG.test(slug) || typeof target !== "string" || !FIGURE_NAME.test(target)) return null;
    return `/guides/${slug}/${target}`;
  }
  // ---------- web addresses, guide links, the title shown once, guide covers ----------
  // A web address the page may hand to the opener: http or https, a host, no user name or password, no spaces or
  // control characters, at most 2,000 characters. Returns the address exactly as written, or null.
  const MAX_URL = 2000;
  // A host not to open: a name without a dot or whose last label has no letter; this Mac or the local network:
  // localhost and names ending in .localhost or .local; IPv4 0/8, 127/8,
  // 10/8, 172.16/12, 192.168/16, 169.254/16; IPv6 ::, ::1, fe80::/10 (link-local), fc00::/7 (unique local) and
  // IPv4-mapped addresses. hostname is URL's, already normalised ("127.1" and "2130706433" read as 127.0.0.1).
  // As app/server.py private_host(): a document must never get the app's own page or Ollama opened in a browser.
  function privateHost(hostname) {
    const host = str(hostname).toLowerCase().replace(/\.+$/, "");
    if (!host || host === "localhost" || host.endsWith(".localhost") || host.endsWith(".local")) return true;
    if (host.startsWith("[")) {
      const v6 = host.slice(1, -1);
      const first = v6.split(":")[0];
      return v6 === "::" || v6 === "::1" || v6.startsWith("::ffff:") || (first.length === 4 && /^fe[89ab]|^f[cd]/.test(first));
    }
    const m = /^(\d{1,3})\.(\d{1,3})\.(\d{1,3})\.(\d{1,3})$/.exec(host);
    if (!m) {  // a name: it needs a dot, and its last label a letter and not a number (as app/server.py private_host)
      const labels = host.split(".");
      const last = labels[labels.length - 1];
      return labels.length < 2 || !/[a-z]/.test(last) || /^(?:[0-9]+|0x[0-9a-f]*)$/.test(last);
    }
    const [a, b] = [Number(m[1]), Number(m[2])];
    return a === 0 || a === 127 || a === 10 || (a === 172 && b >= 16 && b <= 31) || (a === 192 && b === 168) || (a === 169 && b === 254);
  }
  function safeWebUrl(url) {
    if (typeof url !== "string" || !url || url.length > MAX_URL) return null;
    if (/[\u0000-\u0020\u007f-\u00a0\u2028\u2029\\]/.test(url)) return null;
    const m = /^https?:\/\/([^/?#]*)/i.exec(url);
    if (!m || !m[1] || m[1].includes("@")) return null;
    if (!/^(?:[A-Za-z0-9.-]+|\[[0-9A-Fa-f:.]+\])(?::\d{1,5})?$/.test(m[1])) return null;
    // A host written as a number must be four plain decimal parts: "1.1", "0x7f.1", "0177.0.0.1" and "0x" are
    // refused before URL turns them into tidy addresses (as app/server.py private_host).
    const raw = m[1].startsWith("[") ? "" : m[1].replace(/:\d{1,5}$/, "").replace(/\.$/, "").toLowerCase();
    if (raw && raw.split(".").every((x) => /^(?:[0-9]+|0x[0-9a-f]*)$/.test(x))
      && !/^(?:0|[1-9][0-9]{0,2})(?:\.(?:0|[1-9][0-9]{0,2})){3}$/.test(raw)) return null;
    let u;
    try { u = new URL(url); } catch { return null; }
    if ((u.protocol !== "http:" && u.protocol !== "https:") || u.username || u.password || !u.hostname) return null;
    if (privateHost(u.hostname)) return null;  // this Mac or the local network: stays plain text
    return url;
  }
  // The part of a guide's Markdown under the heading whose id (anchorId form, "h-…") is given: that heading and what
  // follows it up to the next heading of the same or a higher level. Headings are numbered as the page numbers them
  // (a repeated heading gets -1, -2…). The whole text when no heading has that id.
  function guideSection(markdown, headingId) {
    const lines = str(markdown).replace(/\r\n?/g, "\n").split("\n");
    const used = new Set();
    let start = -1;
    let level = 0;
    for (let i = 0; i < lines.length; i++) {
      const m = /^(#{1,6})\s+(.+?)\s*#*\s*$/.exec(lines[i]);
      if (!m) continue;
      if (start >= 0 && m[1].length <= level) return lines.slice(start, i).join("\n").trim();
      const id = uniqueId(HEADING_PREFIX + slugify(m[2]), used);
      if (start < 0 && id === str(headingId)) { start = i; level = m[1].length; }
    }
    return start >= 0 ? lines.slice(start).join("\n").trim() : str(markdown);
  }
  // [text](guide:<slug>) or [text](guide:<slug>#<heading>): {slug, anchor (the heading's id, or null)}, or null.
  const GUIDE_LINK = /^guide:([a-z0-9-]{1,80})(?:#([^\s#]{1,200}))?$/;
  function guideLink(target) {
    const m = GUIDE_LINK.exec(str(target));
    return m ? { slug: m[1], anchor: m[2] ? anchorId(m[2]) : null } : null;
  }
  // The text with its first heading taken out when that heading only repeats the title the reading view already
  // shows (same words, ignoring case, spacing, emphasis marks and a closing full stop or colon).
  const sameTitle = (a) => str(a).normalize("NFKC").replace(/[*_`]/g, "").replace(/\s+/g, " ").trim().replace(/[.:]$/, "").toLowerCase();
  function dropRepeatedTitle(text, title) {
    const t = str(text);
    const want = sameTitle(title);
    if (!want) return t;
    const lines = t.replace(/\r\n?/g, "\n").split("\n");
    let i = 0;
    while (i < lines.length && !lines[i].trim()) i++;
    const hd = i < lines.length && short(lines[i]) ? heading(lines[i]) : null;
    if (!hd || sameTitle(hd[1]) !== want) return t;
    let j = i + 1;
    while (j < lines.length && !lines[j].trim()) j++;
    return lines.slice(j).join("\n");
  }
  // How a saved source opens when the library was rebuilt and its id moved (README "Library and articles"): by its
  // source key, address and title; or, saved before sources kept their key (13 November 2026), by its source name
  // and title, which the server trusts only when exactly one document has both. null when it has neither.
  function sourceRef(s) {
    if (!s || typeof s !== "object") return null;
    if (typeof s.key === "string" && s.key) return { key: s.key, url: str(s.url), title: str(s.title) };
    const name = str(s.source).trim();
    const title = str(s.title);
    return name && title.trim() ? { name, title } : null;
  }
  // A title with its first letter a capital (the side panel's found documents): "minor burn" -> "Minor burn", and
  // past any opening quote or bracket: "\u201caquifex\u201d" -> "\u201cAquifex\u201d". A name like "iPhone" or "eBay" (a
  // capital second) stays as it is, and so does the rest of the title.
  const capFirst = (t) => str(t).replace(/^([^\p{L}\p{N}]*)(\p{Ll})(?!\p{Lu})/u, (_, lead, c) => lead + c.toUpperCase());
  // Cmd+P (Ctrl+P) prints, as the Print button does: the key alone with Command or Control, no Shift or Option.
  const isPrintKey = (e) => !!e && (e.metaKey || e.ctrlKey) && !e.shiftKey && !e.altKey && (e.key === "p" || e.key === "P");
  // The mouse's back button (button 3), or a back key some mice and keyboards send, does what Back does.
  const isBackPress = (e) => !!e && ((e.type === "mouseup" && e.button === 3) || (e.type === "keydown" && e.key === "BrowserBack"));
  // Which Back that press means, the one on top first: reading mode's Back, the side panel's "Back to sources", or
  // the Library's "All sources" link; null when no Back is showing. state: {reading, panelBack, librarySource}.
  function backTarget(state) {
    if (!state) return null;
    if (state.reading) return "reading";
    if (state.panelBack) return "panel";
    if (state.librarySource) return "library";
    return null;
  }
  // A guide's text without its own closing "## Read next" section: the reader and the booklet show the index's list
  // instead (it links each guide and gives its summary), so the guide's copy would show the same list twice. Only a
  // "Read next" heading outside code fences with no heading of level 1 or 2 after it is dropped, with what follows it.
  function dropReadNext(text) {
    const lines = str(text).replace(/\r\n?/g, "\n").split("\n");
    let fence = null, at = -1;
    for (let i = 0; i < lines.length; i++) {
      const fm = /^ {0,3}(`{3,}|~{3,})/.exec(lines[i]);
      if (fm) { if (!fence) fence = fm[1][0]; else if (fm[1][0] === fence) fence = null; continue; }
      if (fence) continue;
      if (/^ {0,3}##[ \t]+read next[ \t#]*$/i.test(lines[i])) at = i;
      else if (at >= 0 && /^ {0,3}#{1,2}[ \t]/.test(lines[i])) at = -1;
    }
    return at < 0 ? str(text) : lines.slice(0, at).join("\n").replace(/\s+$/, "");
  }
  // A cover's art: the SVG file ops/covers.py writes for it (app/static/covers/<name>.svg), the same in both themes.
  // The name is a guide's slug, or read-<colour> for the Library's reading row, which takes the five cover colours in
  // turn. Built here because h() refuses src; a name that isn't a plain slug gets no art, and an image that fails to load
  // is dropped, so the card keeps its plain ground. coverClass gives the card its cover colour (app.css .cv-<colour>:
  // the ground and the ink of the text); guides/index.json names eight palettes, each one of the style guide's five.
  const PALETTES = ["ember", "dune", "gulf", "night", "palm", "clay", "steel", "dawn"];
  const COLOURS = { ember: "ember", dune: "sand", gulf: "navy", night: "navy", palm: "stone", clay: "chocolate", steel: "stone", dawn: "sand" };
  const READS = ["navy", "sand", "ember", "stone", "chocolate"];
  function coverArt(name) {
    if (!/^[a-z0-9-]{1,100}$/.test(str(name))) return [];
    const img = document.createElement("img");
    img.className = "cover-art";
    img.setAttribute("alt", "");
    img.setAttribute("aria-hidden", "true");
    img.setAttribute("loading", "lazy");
    img.setAttribute("src", `/static/covers/${name}.svg`);
    img.addEventListener("error", () => img.remove());
    return [img];
  }
  const readColour = (i) => READS[((i % READS.length) + READS.length) % READS.length];
  const readCover = (i) => `read-${readColour(i)}`;
  const coverClass = (palette) => `cv-${COLOURS[str(palette)] || "plain"}`;

  // Reading mode's Back: each guide opened from a link in another is pushed; Back returns to the last one pushed, and
  // with nothing left, leaves reading mode (back() gives null). Opening a document any other way starts afresh.
  function navStack(limit = 50) {
    let items = [];
    return {
      push(entry) { items.push(entry); if (items.length > limit) items = items.slice(-limit); },
      back() { return items.length ? items.pop() : null; },
      reset() { items = []; },
      get size() { return items.length; },
    };
  }

  // ---------- the fact box at the top of a library document ----------
  // Wikipedia's infobox reaches the library as loose lines. factBox(lines) reads the lines before the first prose
  // paragraph and returns {rows, end} (end: the first line after the box) or null to leave the lines as they are.
  // A row is {label, value: [lines]} or {label, list: [items]}, or {text} for a line that pairs with nothing (shown
  // across both columns). The pairs it is sure of:
  //   "Kingdom:" then "Fungi"                       a label ending in a colon, then its value line
  //   "Founded | August 2011"                       label | value on one line
  //   "Type of site" then "| E-commerce"            a lone "| value" line continues the label above
  //   "Founders" then "- Walker Williams" …         a label line, then a short bullet list (1 to 8 items)
  // and two it accepts only after at least one sure pair:
  //   "Headquarters" (ending its paragraph: capitalised, no digits, up to three words) then "San Francisco, California,
  //   U.S." (alone in the next paragraph, with a digit, comma or space), when a sure pair or the end follows
  //   "Providence, Rhode Island, U.S." right under a value, in the same paragraph: one more line of that value, if it
  //   has a comma, digit or bracket or no sure pair follows (a short line before a pair is an infobox section heading)
  // A box needs at least two sure pairs and no more lone lines than max(3, pairs). Only separators are dropped (the
  // "|" between label and value, a label's closing colon and a bullet's marker); every other character stays.
  const BOX_MAX_LINES = 80;
  const BOX_LABEL_MAX = 60;
  const BOX_VALUE_MAX = 200;
  const BOX_BULLET_MAX = 8;
  const BULLET_ONLY = /^ {0,3}[-*+•][ \t]+(\S.*)$/;
  function boxProse(t) {
    if (t.length > 160) return true;
    const words = t.split(/\s+/).length;
    return words >= 14 || (words >= 7 && /[.!?]["')\]]?$/.test(t));
  }
  function factBox(lines) {
    let s = 0;
    while (s < lines.length && !lines[s].trim()) s++;
    // The region: lines before the first prose line or other block.
    const items = [];  // {t, kind, i, paraFirst, paraLast}
    let i = s;
    let blankBefore = true;
    while (i < lines.length && i - s < BOX_MAX_LINES) {
      const l = lines[i];
      if (!l.trim()) { blankBefore = true; i++; continue; }
      if (!short(l) || l.length > 300) break;
      const t = l.trim();
      if (heading(l) || FENCE_RE.test(l) || QUOTE_RE.test(l) || isHr(l) || isTableSep(l) || /^\d{1,9}[.)][ \t]/.test(t) || t.startsWith("Source: ")) break;
      let kind;
      const b = BULLET_ONLY.exec(l);
      if (b) kind = "bullet";
      else if (boxProse(t)) break;
      else if (t.startsWith("|")) kind = t.slice(1).trim() && !t.slice(1).includes("|") ? "cont" : "plain";
      else if (t.includes(" | ")) {
        const cells = t.replace(/\|$/, "").split("|").map((c) => c.trim());
        kind = cells.length === 2 && cells[0] && cells[1] && cells[0].length <= BOX_LABEL_MAX ? "row" : "plain";
      } else kind = t.endsWith(":") && t.length <= BOX_LABEL_MAX && t.length > 1 ? "colon" : "plain";
      if (items.length && !blankBefore) items[items.length - 1].paraLast = false;
      items.push({ t, kind, i, paraFirst: blankBefore, paraLast: true, bullet: b ? b[1].trim() : null });
      blankBefore = false;
      i++;
    }
    if (items.length < 3) return null;
    const rows = [];
    const starts = [];  // the item each row starts at
    let sure = 0;
    let lone = 0;
    let last = null;  // the last pair, which a following line may continue
    let k = 0;
    const isLabelish = (it) => !!it && (it.kind === "plain" || it.kind === "colon") && it.t.length <= BOX_LABEL_MAX;
    const bulletsFrom = (j) => { let n = 0; while (j + n < items.length && items[j + n].kind === "bullet" && items[j + n].bullet.length <= 100) n++; return n; };
    const labelOf = (it) => (it.kind === "colon" ? it.t.slice(0, -1).trim() : it.t);
    // Whether item j is itself a label: it ends in a colon, or a "| value" line or a bullet follows it.
    const isLabel = (j) => { const it = items[j]; const nx = items[j + 1]; return !!it && (it.kind === "colon" || (!!nx && (nx.kind === "cont" || nx.kind === "bullet"))); };
    const add = (row, at) => { rows.push(row); starts.push(at); };
    // Whether item j starts a sure pair: a row, a label ending in a colon, or a label followed by "| value" or bullets.
    const isSureStart = (j) => { const it = items[j]; const nx = items[j + 1];
      return !!it && (it.kind === "row" || it.kind === "colon" || (isLabelish(it) && !!nx && (nx.kind === "cont" || nx.kind === "bullet"))); };
    // A label without a colon: capitalised words, no digits or punctuation, at most three words.
    const SOFT_LABEL = /^[A-Z][A-Za-z'’ -]{0,29}$/;
    while (k < items.length) {
      const it = items[k];
      const nx = items[k + 1];
      const nb = bulletsFrom(k + 1);
      if (it.kind === "row") {
        const at = it.t.indexOf(" | ");
        let v = it.t.slice(at + 3).trim();
        if (v.endsWith("|")) v = v.slice(0, -1).trim();
        last = { label: it.t.slice(0, at).trim(), value: [v] };
        add(last, k); sure++; k++;
      } else if (isLabelish(it) && nx && nx.kind === "cont") {
        last = { label: labelOf(it), value: [nx.t.slice(1).trim()] };
        add(last, k); sure++; k += 2;
      } else if (isLabelish(it) && nb >= 1 && nb <= BOX_BULLET_MAX && !(items[k + 1 + nb] && items[k + 1 + nb].kind === "bullet")) {
        add({ label: labelOf(it), list: items.slice(k + 1, k + 1 + nb).map((x) => x.bullet) }, k);
        last = null; sure++; k += 1 + nb;
      } else if (it.kind === "colon" && nx && nx.kind === "plain" && nx.t.length <= BOX_VALUE_MAX && !isLabel(k + 1)) {
        last = { label: labelOf(it), value: [nx.t] };
        add(last, k); sure++; k += 2;
      } else if (it.kind === "cont" && last) {
        last.value.push(it.t.slice(1).trim()); k++;
      } else if (sure && it.kind === "plain" && SOFT_LABEL.test(it.t) && it.t.split(" ").length <= 3 && it.paraLast
        && nx && nx.kind === "plain" && nx.paraFirst && nx.paraLast && nx.t.length <= BOX_VALUE_MAX && !isLabel(k + 1)
        && (/[\d,]/.test(nx.t) || nx.t.includes(" ")) && (k + 2 >= items.length || isSureStart(k + 2))) {
        last = { label: it.t, value: [nx.t] };
        add(last, k); k += 2;
      } else if (last && it.kind === "plain" && !it.paraFirst && it.t.length <= BOX_VALUE_MAX
        && (/[\d,()]/.test(it.t) || !isSureStart(k + 1))) {  // a short line just before a pair is a section heading
        last.value.push(it.t); k++;
      } else if (it.kind === "bullet") {
        break;  // a list that belongs to no label: the box ends before it
      } else {
        add({ text: it.t }, k); lone++; last = null; k++;
      }
    }
    let end = k < items.length ? items[k].i : items[items.length - 1].i + 1;
    // Lone lines at the end belong to the text after the box, not to it.
    while (rows.length && rows[rows.length - 1].text !== undefined) {
      rows.pop(); lone--;
      end = items[starts.pop()].i;
    }
    if (sure < 2 || lone > Math.max(3, sure)) return null;
    return { rows, end };
  }

  // A document with no text left to show: removed from the library, or its text file is gone.
  const isEmptyDoc = (doc) => !doc || typeof doc !== "object" || !Array.isArray(doc.parts)
    || !doc.parts.some((p) => p && typeof p.text === "string" && p.text.trim());
  const MISSING_DOC = "This document isn't in the library any more. It may have been removed when the library was last updated.";

  // ---------- two-finger swipe back in reading mode ----------
  // Fed one wheel event at a time: {dx, dy, t (ms), scrollers: [{left, width, client}] for each element under the
  // pointer that scrolls sideways, selecting: true while text is being selected}. Returns {state, back}; back is true once per gesture.
  // BACK_SIGN is the sign of the wheel's deltaX that means "back". With macOS's default natural scrolling, two
  // fingers moving left to right (Safari's "back" swipe) scroll the page toward its left edge: deltaX is negative.
  const SWIPE = { BACK_SIGN: -1, MIN: 120, RATIO: 2, DECIDE: 12, GAP_MS: 300 };
  const canScrollToward = (s, dx) => (dx < 0 ? s.left > 0.5 : s.left + s.client < s.width - 0.5);
  function swipeStep(state, ev) {
    const t = Number(ev && ev.t) || 0;
    if (!state || t - state.t > SWIPE.GAP_MS) state = { t, dx: 0, dy: 0, axis: null, done: false };
    else state = { ...state, t };
    const dx = Number(ev && ev.dx) || 0;
    const dy = Number(ev && ev.dy) || 0;
    if (state.done || state.axis === "other") return { state, back: false };
    if (ev.selecting) return { state: { ...state, axis: "other" }, back: false };  // selecting text: never go back
    // A table or code block under the pointer that can still scroll that way takes the gesture.
    if (dx && (Array.isArray(ev.scrollers) ? ev.scrollers : []).some((s) => s && canScrollToward(s, dx))) {
      return { state: { ...state, axis: "other" }, back: false };
    }
    state.dx += dx;
    state.dy += dy;
    if (!state.axis && Math.abs(state.dx) + Math.abs(state.dy) >= SWIPE.DECIDE) {
      state.axis = Math.abs(state.dx) >= SWIPE.RATIO * Math.abs(state.dy) ? "x" : "other";
    }
    if (state.axis === "x" && Math.abs(state.dy) * SWIPE.RATIO > Math.abs(state.dx)) state.axis = "other";  // turned vertical
    if (state.axis === "x" && Math.sign(state.dx) === SWIPE.BACK_SIGN && Math.abs(state.dx) >= SWIPE.MIN) {
      state.done = true;
      return { state, back: true };
    }
    return { state, back: false };
  }

  // ---------- quote markers and checked statements (the quoted answer modes, core.py "grounded answers") ----------
  // While a quoted answer streams, the model writes [[P2: words from the passage]] after each statement. The page never
  // shows them: stripQuoteMarkers takes out every closed marker, the opening of one left open at the end of its line
  // (the text after it stays, as the server keeps it), and, while live, a marker still being written ("[[P", a lone
  // "[" at the very end, or an open marker on the line being written). It scans with indexOf, so a hostile answer
  // costs linear time.
  // fetch-quoted cites sentence numbers instead: [[S12]] or [[S12, S14]].
  const MARKER_HEAD = /\[\[[ \t]*[A-Za-z]{0,2}[ \t]?\d{1,12}(?:[ \t]*[,;][ \t]*[A-Za-z]{0,2}[ \t]?\d{1,12})*[ \t]*(?:[:：][ \t]*|(?=\]\]))/y;
  const MARKER_START = /^\[\[[ \t]*[A-Za-z]{0,2}[ \t]?\d{0,12}(?:[ \t]*[,;][ \t]*[A-Za-z]{0,2}[ \t]?\d{0,12})*[ \t]*$/;
  // Sentence numbers as Gemma also writes them ("[S12]", "[[S5], [S67]]", "[S3] [S4]"): out whole, as core.CITE_GROUP
  // reads them; while live, one still being written at the very end is held back.
  const CITE = String.raw`\[{1,2}[ \t]*S\d{1,4}(?:[ \t]*\]{0,2}[ \t]*[,;]+[ \t]*\[{0,2}[ \t]*S\d{1,4}|[ \t]*\]{1,2}[ \t]*\[{1,2}[ \t]*S\d{1,4})*[ \t]*\]{1,2}`;
  const CITE_GROUP = new RegExp(CITE, "g");
  const CITE_NOTE = /\[\[[ \t]*S\d{1,4}[ \t]*[(\-–—][^\[\]\n:]{0,80}\]{1,2}/g;  // "[[S19 (implicit from ...)]"
  const CITE_GONE = /[ \t]*\uE020(?=[.,;:!?) \t]|\n|$)|\uE020/g;  // where a whole run was: with the space before it at an end
  const CITE_OPEN = /[ \t]*\[{1,2}[ \t]*S\d{0,4}(?:[ \t]*\]{0,2}[ \t]*[,;]*[ \t]*\[{0,2}[ \t]*S?\d{0,4}){0,20}$/;
  function stripQuoteMarkers(text, { live = false } = {}) {
    let t = str(text).replace(/\uE020/g, "").replace(CITE_NOTE, "\uE020").replace(CITE_GROUP, "\uE020").replace(CITE_GONE, "");
    if (live) t = t.replace(CITE_OPEN, "");
    let out = "";
    let pos = 0;
    let nextEol = -2;
    let nextClose = -2;
    for (;;) {
      const i = t.indexOf("[[", pos);
      if (i < 0) break;
      MARKER_HEAD.lastIndex = i;
      if (!MARKER_HEAD.test(t)) {
        if (live && t.length - i < 20 && MARKER_START.test(t.slice(i))) return out + t.slice(pos, i);  // still being written
        out += t.slice(pos, i + 2);
        pos = i + 2;
        continue;
      }
      const headEnd = MARKER_HEAD.lastIndex;
      // The next line end and the next "]]", found once and reused by later markers, so the scan stays linear.
      if (nextEol !== -1 && nextEol < i) nextEol = t.indexOf("\n", i);
      if (nextClose !== -1 && nextClose < headEnd) nextClose = t.indexOf("]]", headEnd);
      const eol = nextEol;
      const close = nextClose;
      if (close >= 0 && (eol < 0 || close < eol)) {  // a whole marker: out, with the space before it at a line's end or before punctuation
        out += /^(?:[.,;:!?)\n]|$)/.test(t.slice(close + 2, close + 3)) ? t.slice(pos, i).replace(/[ \t]+$/, "") : t.slice(pos, i);
        pos = close + 2;
        continue;
      }
      // Not closed on its line: while the line is still being written it may yet close, so the rest is held back;
      // once the line is done (or the answer is), only the opening "[[P2:" goes and the text after it stays.
      if (eol < 0 && live) return out + t.slice(pos, i).replace(/[ \t]+$/, "");
      out += t.slice(pos, i);
      pos = headEnd;
    }
    out += t.slice(pos);
    return live && out.endsWith("[") ? out.slice(0, -1) : out;
  }
  // The server marks each checked statement in an answer's `marked` text: U+E000, the claim's index, U+E001, the
  // statement, U+E002. noMarks takes the marks out (for plain text and Copy without claims).
  const CLAIM_MARKS = /\uE000\d{0,4}\uE001|[\uE000-\uE002]/g;
  const noMarks = (text) => str(text).replace(CLAIM_MARKS, "");
  // Where a claim's source sits among the answer's sources (one per article): its index, or -1.
  function sourceIndex(source, sources) {
    if (!source || typeof source !== "object" || !Array.isArray(sources)) return -1;
    const byId = sources.findIndex((s) => s && (Number(s.id) === Number(source.id) || (typeof s.id === "string" && s.id === source.id)));
    if (byId >= 0) return byId;
    return sources.findIndex((s) => s && str(s.title) === str(source.title) && str(s.source) === str(source.source));
  }
  // What md() needs to draw a claim: [{backed, n (1-based source number or 0), label}], from the done event's claims.
  function claimView(claims, sources) {
    return (Array.isArray(claims) ? claims : []).map((c) => {
      const backed = !!(c && c.backed === true);
      const at = backed ? sourceIndex(c.source, sources) : -1;
      const src = c && c.source && typeof c.source === "object" ? c.source : null;
      return { backed, n: at + 1, label: src ? `${str(src.title) || "Untitled"}${src.source ? ` (${str(src.source)})` : ""}` : "" };
    });
  }
  // The answer for Copy, in words: each statement not backed ends with "[not from the library]", each backed one with
  // its source number, and the numbered sources follow. Lines the check left alone are copied as they are.
  function claimsText(marked, claims, sources) {
    const view = claimView(claims, sources);
    const used = new Set();
    const body = visibleText(marked).replace(/\uE000(\d{1,4})\uE001([^\uE000-\uE002]*)\uE002/g, (_, n, text) => {
      const c = view[Number(n)];
      if (!c || !c.backed) return `${text} [not from the library]`;
      if (!c.n) return text;
      used.add(c.n);
      return `${text} [${c.n}]`;
    });
    const list = (Array.isArray(sources) ? sources : []).map((s, i) => (used.has(i + 1) ? `[${i + 1}] ${str(s && s.title) || "Untitled"}${s && s.source ? ` (${str(s.source)})` : ""}` : null)).filter(Boolean);
    return noMarks(body) + (list.length ? `\n\nSources:\n${list.join("\n")}` : "");
  }

  // A quoted answer that stopped before the server checked it (Stop, an error): every line it wrote is one statement,
  // marked not checked, so nothing shows as from the library. Left alone as the server leaves them: blank lines,
  // headings, code, steps questions, the no-match and call lines, a sentence that is only an instruction to call for
  // help, and everything from a Follow-ups or Sources line on. Returns {marked, claims} like a checked answer's.
  // As core.py CALL_STATEMENT: a sentence that is nothing but an instruction to call emergency help. A number counts
  // only when it is on the current country's card (EMERGENCY.heldNumbers).
  const CALL_ONLY = /^[*_ \t]*(?:call|dial|phone|ring)[ \t]+(?:(\d(?:[\d ]{0,6}\d)?(?:[ \t]+or[ \t]+\d(?:[\d ]{0,6}\d)?|[ \t]*\/[ \t]*\d(?:[\d ]{0,6}\d)?)*)|(?:(?:the|your)[ \t]+)?(?:local[ \t]+)?emergency[ \t]+(?:number|services)|an[ \t]+ambulance)(?:[ \t]+(?:now|right away|immediately|at once))?[ \t]*[.!]?[*_ \t]*$/i;
  function callOnly(sentence, numbers) {
    const m = CALL_ONLY.exec(sentence);
    if (!m) return false;
    if (!m[1]) return true;
    const held = new Set((Array.isArray(numbers) ? numbers : []).map((n) => str(n).replace(/ /g, "")));
    return m[1].split(/[ \t]*(?:\bor\b|\/)[ \t]*/).filter((x) => x.trim()).every((x) => held.has(x.replace(/ /g, "")));
  }
  const LINE_PREFIX = /^[ \t]*(?:>[ \t]?)*[ \t]*(?:(?:[-*+•]|\d{1,9}[.)])[ \t]+(?:\[[ xX]\][ \t]+)?)?/;
  function uncheckedMarked(text, numbers = []) {
    const claims = [];
    let fence = null;
    let stop = false;
    const mark = (s) => {
      if (!/\w/.test(s) || (s.length <= MAX_LINE && callOnly(s, numbers))) return s;
      claims.push({ n: claims.length, text: s.trim().slice(0, 400), backed: false, source: null, quote: "", why: "not checked" });
      return `\uE000${claims.length - 1}\uE001${s.trim()}\uE002`;
    };
    const lines = noMarks(stripQuoteMarkers(text)).replace(/\r\n?/g, "\n").split("\n").map((line) => {
      if (stop) return line;
      if (line.length > MAX_LINE) return mark(line);  // too long for the patterns below: the whole line is one statement
      const f = /^ {0,3}(`{3,}|~{3,})[ \t]*([\w+#-]*)/.exec(line);
      if (fence) {
        if (line.trim().startsWith(fence.mark)) { fence = null; return line; }
        const b = fence.steps && /^\s*(?:yes|no)\s*:\s*/i.exec(line);
        if (!b) return line;
        const arrow = Math.max(line.lastIndexOf("->"), line.lastIndexOf("→"));
        const end = arrow > b[0].length ? arrow : line.length;
        return line.slice(0, b[0].length) + mark(line.slice(b[0].length, end)) + (end < line.length ? ` ${line.slice(end)}` : "");
      }
      if (f) { fence = { mark: f[1], steps: f[2].toLowerCase() === "steps" }; return line; }
      if (FOLLOW_RE.test(line) || SOURCES_RE.test(line)) { stop = true; return line; }
      if (!line.trim() || /^ {0,3}#{1,6}(?:[ \t]|$)/.test(line) || isHr(line) || isTableSep(line)) return line;
      const prefix = LINE_PREFIX.exec(line)[0];
      return prefix + mark(line.slice(prefix.length));
    });
    return { marked: lines.join("\n"), claims };
  }

  // How an answer settles when it ends: {text, marked, claims, unchecked, noMatch}. A done event's checked text when
  // it has one. A quoted answer that ended with no done event from the server (Stop, an error, a lost connection)
  // keeps what it wrote, every statement marked not checked (unchecked: true). noMatch is the server's no_match flag
  // (null when it sent none: an older chat or the standard mode).
  function settleAnswer({ mode = "standard", text = "", done = {}, numbers = [] } = {}) {
    const d = done && typeof done === "object" ? done : {};
    let marked = typeof d.marked === "string" && Array.isArray(d.claims) && d.claims.length ? d.marked : null;
    let claims = marked ? d.claims : [];
    let out = typeof d.answer === "string" ? d.answer : str(text);
    const unchecked = mode !== "standard" && !d.grounding && typeof d.answer !== "string" && d.documents_only !== true;
    if (unchecked) {
      const u = uncheckedMarked(out, numbers);
      out = noMarks(u.marked);
      if (u.claims.length) { marked = u.marked; claims = u.claims; }
    }
    if (d.emergency_call && typeof d.emergency_call === "object") {  // the app's own call line goes under it: once
      out = dropCallLines(out, numbers);
      if (marked) marked = dropCallLines(marked, numbers);
    }
    return { text: out, marked, claims, unchecked, noMatch: typeof d.no_match === "boolean" ? d.no_match : null };
  }
  // An answer without its lines that are nothing but an instruction to call emergency help (callOnly): for an answer
  // under which the app draws its own call line, so the call line shows once.
  function dropCallLines(text, numbers = []) {
    return str(text).split("\n").filter((line) => {
      if (line.length > MAX_LINE) return true;
      const content = noMarks(line.slice(LINE_PREFIX.exec(line)[0].length)).trim();
      return !(content && callOnly(content, numbers));
    }).join("\n");
  }
  // Whether a streaming answer is drawn in the neutral "being checked" style: a quoted answer, while it streams.
  const isChecking = (live, mode) => live === true && typeof mode === "string" && mode !== "standard";
  // Whether to draw the no-match view (the page's own lines): the server's flag when it sent one, else the text's form.
  const showsNoMatch = (parts, flag) => (flag === true || flag === false ? flag : !!(parts && parts.nomatch));

  // Exported for a check outside the browser (node), which has no document.
  // The block patterns of the Markdown renderer below, exported for the timing checks.
  const PATTERNS = { STEP_Q, STEP_B_HEAD, FOLLOW_RE, SOURCES_RE, BULLET_RE, FENCE_OPEN };
  // The attribute names h() may set: the ones this interface uses, plus aria-* and data-*. Any other name (href, src,
  // style, srcdoc, formaction, a handler given as text...) throws, so a name that came from a document or an answer
  // can never become an attribute.
  const ATTRS = new Set(["alt", "checked", "colspan", "dir", "disabled", "for", "hidden", "id", "lang", "maxlength", "name",
    "open", "placeholder", "role", "rowspan", "scope", "start", "tabindex", "title", "type", "value"]);
  const attrOk = (k) => ATTRS.has(k) || /^aria-[a-z]+$/.test(k) || /^data-[a-z0-9]+(?:-[a-z0-9]+)*$/.test(k);
  // A web address whose host is written in another alphabet (punycode, "xn--"): its host in ASCII, to show before
  // opening it, since it may imitate a name the reader knows. null for any other address.
  function lookalikeHost(url) {
    let host;
    try { host = new URL(url).hostname; } catch { return null; }
    return host.split(".").some((label) => /^xn--/i.test(label)) ? host : null;
  }
  const PURE = { attrOk, lookalikeHost, slugify, uniqueId, anchorId, guideFigure, isEmptyDoc, MISSING_DOC, swipeStep, SWIPE,
    safeWebUrl, privateHost, MAX_URL, guideLink, guideSection, dropRepeatedTitle, dropReadNext, coverArt, readCover, readColour, coverClass, PALETTES, READS, factBox, navStack, sourceRef, capFirst, isPrintKey, isBackPress, backTarget,
    stripQuoteMarkers, noMarks, claimView, claimsText, sourceIndex, uncheckedMarked, settleAnswer, isChecking, showsNoMatch,
    callOnly, dropCallLines };
  if (typeof module !== "undefined" && module.exports) module.exports = { parseSteps, splitAnswer, visibleText, MAX_LINE, PATTERNS, mdPatterns: () => MD, EMERGENCY, ...PURE };
  if (typeof document === "undefined") return;

  // ---------- building elements ----------
  function h(tag, props, ...kids) {
    const el = document.createElement(tag);
    if (props) {
      for (const [k, v] of Object.entries(props)) {
        if (v == null || v === false) continue;
        if (k === "class") el.className = v;
        else if (k === "text") el.textContent = v;
        else if (k === "dataset") Object.assign(el.dataset, v);
        else if (k.startsWith("on")) { if (typeof v === "function") el.addEventListener(k.slice(2), v); }
        else if (attrOk(k)) el.setAttribute(k, v === true ? "" : String(v));
        else throw new Error(`h(): ${k} is not allowed`);
      }
    }
    for (const kid of kids.flat(Infinity)) {
      if (kid == null || kid === false) continue;
      el.append(kid instanceof Node ? kid : String(kid));
    }
    return el;
  }

  // ---------- Markdown: a small renderer that builds DOM nodes, never HTML strings ----------
  // A web address shows its full text, so the reader sees exactly where a click goes. It is a button, never an <a>:
  // a click hands the address to the opener (app.js: the server opens it in the default browser, outside the app).
  const urlText = (url) => h("span", { class: "ext-url" }, url);
  let WEB_OPEN = null;
  function setWebOpener(fn) { WEB_OPEN = typeof fn === "function" ? fn : null; }
  // Asked before opening an address whose host is a look-alike (lookalikeHost): fn(host) resolves true to open it.
  // Without one, such an address opens like any other.
  let WEB_CHECK = null;
  function setWebCheck(fn) { WEB_CHECK = typeof fn === "function" ? fn : null; }
  async function openWeb(url) {
    const host = lookalikeHost(url);
    if (host && WEB_CHECK && !(await WEB_CHECK(host))) return;
    if (WEB_OPEN) WEB_OPEN(url);
  }
  function webLink(url) {
    const safe = safeWebUrl(url);
    if (!safe || !WEB_OPEN) return urlText(url);
    // Inside a statement no passage backs, the address keeps the statement's grey (still clickable).
    const grey = !!(OPTS.claim && !OPTS.claim.backed);
    return h("button", { class: `ext-url ext-link${grey ? " unbacked" : ""}`, type: "button", title: "Open in your web browser",
      onclick: (e) => { e.preventDefault(); openWeb(safe); } }, safe);
  }

  const MAX_DEPTH = 8;

  // Options for the render in progress: {numbers: [emergency numbers to show in red], headingShift: n,
  // ids: a Set of the ids already given in this document (pass the same Set for every part of one document),
  // guide: the guide's slug, which lets ![alt](name.svg) show that guide's figure,
  // guides: the slugs of the guides there are, and onGuide(slug, headingId or null): [text](guide:slug) opens one,
  // factBox: true for a library document's first part, whose loose infobox lines become one table (factBox above),
  // claims: claimView() of a quoted answer, whose `marked` text this is: a statement not backed is drawn greyed and
  // followed by "not from the library", a backed one by its source number (onSource(index, button) opens it).
  // Without claims, any statement marks in the text are dropped.}
  let OPTS = {};
  function md(src, opts = {}) {
    const saved = OPTS;
    OPTS = { ...opts, ids: opts.ids instanceof Set ? opts.ids : new Set(), notes: null, claim: null };
    if (!Array.isArray(OPTS.claims)) OPTS.claims = null;
    try {
      const frag = document.createDocumentFragment();
      const text = OPTS.claims ? str(src) : noMarks(src);
      const [body, defs] = takeFootnotes(text.replace(/\r\n?/g, "\n").split("\n"));
      if (defs.size) OPTS.notes = { defs, count: 0 };
      const box = OPTS.factBox === true ? factBox(body) : null;
      if (box) frag.append(factBoxView(box.rows));
      blocks(box ? body.slice(box.end) : body, frag, 0);
      if (OPTS.notes) frag.append(footnoteList());
      return frag;
    } finally { OPTS = saved; }
  }

  // ---------- anchors: headings, [text](#anchor) and footnotes ----------
  const reduceMotion = () => typeof matchMedia === "function" && matchMedia("(prefers-reduced-motion: reduce)").matches;
  // The document a jump belongs to: the nearest element marked data-md-root (reading mode's column, the side
  // panel's reader), else the nearest rendered Markdown block (.md, a chat answer). Never the whole page.
  function docRoot(from) {
    let md = null;
    for (let n = from.parentElement; n; n = n.parentElement) {
      if (n.getAttribute && n.getAttribute("data-md-root") !== null) return n;
      if (!md && String(n.className || "").split(" ").includes("md")) md = n;
    }
    return md;
  }
  // Scroll to the element with this id inside the open document; with no match there, do nothing.
  function jumpTo(from, id) {
    const root = docRoot(from);
    const t = root ? root.querySelector(`[id="${id}"]`) : null;
    if (t) t.scrollIntoView({ block: "start", behavior: reduceMotion() ? "auto" : "smooth" });
    return t;
  }
  const jump = (e) => { e.preventDefault(); jumpTo(e.currentTarget, e.currentTarget.dataset.anchor); };
  const anchorBtn = (id, kids, extra = {}) => h("button", { class: "md-anchor", type: "button", dataset: { anchor: id }, onclick: jump, ...extra }, kids);

  // Footnote definitions ([^label]: text, with indented lines after it) are taken out of the text before the
  // blocks are read, outside fenced code. Returns [the other lines, Map label → {text, id, ref, n}].
  const FOOTDEF = /^ {0,3}\[\^([^\]\s]{1,40})\]:[ \t]?(.*)$/;
  const FOOTREF = /\[\^([^\]\s]{1,40})\]/y;
  function takeFootnotes(lines) {
    const out = [];
    const defs = new Map();
    let fence = null;
    for (let i = 0; i < lines.length; i++) {
      const l = lines[i];
      const f = short(l) ? FENCE_RE.exec(l) : null;
      if (fence) { if (l.trim().startsWith(fence)) fence = null; out.push(l); continue; }
      if (f) { fence = f[1]; out.push(l); continue; }
      const m = short(l) ? FOOTDEF.exec(l) : null;
      if (!m) { out.push(l); continue; }
      const text = [m[2]];
      while (i + 1 < lines.length && lines[i + 1].trim() && leading(lines[i + 1]) >= 2) text.push(lines[++i].trim());
      if (!defs.has(m[1])) defs.set(m[1], { text: text.join("\n"), id: null, ref: null, n: 0 });
    }
    return [out, defs];
  }
  // A note's number, given at its first reference; its ids are given with the number.
  function noteNumber(note, label) {
    if (!note.n) {
      note.n = ++OPTS.notes.count;
      note.id = uniqueId(`fn-${slugify(label)}`, OPTS.ids);
      note.ref = uniqueId(`fnref-${slugify(label)}`, OPTS.ids);
      return [note.n, note.ref];
    }
    return [note.n, null];
  }
  function footnoteList() {
    const notes = OPTS.notes;
    for (const [label, note] of notes.defs) if (!note.n) { noteNumber(note, label); note.unused = true; }
    const items = [...notes.defs.values()].sort((a, b) => a.n - b.n);
    return h("section", { class: "footnotes", "aria-label": "Footnotes" },
      h("ol", null, items.map((note) => h("li", { id: note.id, value: String(note.n) },
        inline(note.text),
        note.unused ? null : [" ", anchorBtn(note.ref, "↩", { class: "md-anchor fn-back", "aria-label": `Back to reference ${note.n}` })]))));
  }

  // ---------- callouts: > [!note] Title ----------
  const CALLOUT_RE = /^\[!([A-Za-z]{1,20})\][+-]?(?:[ \t]+(.*))?$/;
  const CALLOUT_KIND = { note: "note", info: "note", tip: "tip", hint: "tip", important: "tip", success: "tip",
    warning: "warning", caution: "warning", attention: "warning", danger: "danger", error: "danger" };

  // ---------- an image: a guide's own figure, or else its description as text ----------
  // A figure is drawn twice from the same file: as it is on screen (following light or dark), and as name.svg#print,
  // shown only when printing (app.css). A figure inside <img> keeps the window's dark appearance when printed, and
  // neither a print rule inside the SVG nor color-scheme on the page changes that (tested with WebKit's print, as the
  // Print button and the booklets use it: tests/figure_print_check.py). Addressed as #print, the SVG's own
  // ":target" rules draw its light, ink-friendly version instead; a figure without them prints as it shows.
  // The screen copy loads lazily; the print copy, hidden until printing, loads at once so it is ready to print.
  function figureImg(url, alt, cls, lazy) {
    const img = document.createElement("img");  // built here because h() refuses src
    img.className = cls;
    img.setAttribute("alt", alt);
    img.setAttribute("loading", lazy ? "lazy" : "eager");
    img.setAttribute("src", url);
    return img;
  }
  function image(alt, target) {
    const url = guideFigure(OPTS.guide, target);
    if (!url) return h("span", { class: "img-alt" }, `[Image: ${alt || target}]`, " (", urlText(target), ")");  // the address stays text
    const both = document.createDocumentFragment();
    both.append(figureImg(url, alt, "md-img md-img-screen", true), figureImg(`${url}#print`, "", "md-img md-img-print", false));
    return both;
  }

  // The fact box: one compact table of label and value; a line that pairs with nothing spans both columns.
  function factBoxView(rows) {
    return h("div", { class: "table-wrap" }, h("table", { class: "kv factbox" }, h("tbody", null, rows.map((r) => (r.text !== undefined
      ? h("tr", null, h("td", { colspan: "2", class: "fb-line" }, inline(r.text)))
      : h("tr", null, h("th", { scope: "row" }, inline(r.label)),
        h("td", null, r.list ? h("ul", null, r.list.map((x) => h("li", null, inline(x)))) : inline(r.value.join("\n")))))))));
  }

  // The headings of a rendered document, for its outline: [{level, id, text}], or [] when there are fewer than 3.
  function outline(root) {
    const found = [];
    const walk = (el) => {
      for (const c of el.children || []) {
        if (/^H[1-6]$/i.test(c.tagName) && c.id && String(c.className).split(" ").includes("md-h")) {
          found.push({ level: Number(c.tagName[1]), id: c.id, text: c.textContent.trim() });
        } else walk(c);
      }
    };
    walk(root);
    return found.length >= 3 ? found : [];
  }
  const leading = (line) => line.match(/^[ \t]*/)[0].replace(/\t/g, "    ").length;
  const isTableStart = (lines, i) => lines[i].includes("|") && i + 1 < lines.length && short(lines[i + 1]) && isTableSep(lines[i + 1]);
  function startsBlock(lines, i) {
    const l = lines[i];
    if (!short(l)) return false;
    return FENCE_RE.test(l) || !!heading(l) || isHr(l) || QUOTE_RE.test(l) || LIST_RE.test(l) || isTableStart(lines, i)
      || (OPTS.bareTables === true && !!plainTable(lines, i, 2));
  }
  // The library stores many tables as bare rows, one per line: `Country | Soviet Union` or `Nuts | 8mm | 6 units`, with
  // no separator row. A row has " | " in it (a trailing pipe is allowed) and is not a heading, list item, quote, the
  // "Source:" line, a line starting with a pipe (in this library, the broken-off end of a cell) or a line over
  // PLAIN_ROW_MAX characters. Standard pipe tables are read before this.
  const PLAIN_ROW_MAX = 300;
  function plainCells(line) {
    if (line.length > PLAIN_ROW_MAX || !line.includes(" | ")) return null;
    let t = line.trim();
    if (!t || t.startsWith("|") || t.startsWith("Source: ") || heading(line) || LIST_RE.test(line) || QUOTE_RE.test(line) || FENCE_RE.test(line)) return null;
    if (t.endsWith("|")) t = t.slice(0, -1);
    const cells = t.split("|").map((c) => c.trim());
    return cells.length >= 2 ? cells : null;
  }
  // A ruled line between rows in plain-text tables (----+----, ====): skipped inside a run.
  const isRule = (line) => line.length <= PLAIN_ROW_MAX && /^[-=+|: \t]+$/.test(line) && /[-=]{3}/.test(line);
  // The rows of a bare table starting at line i: {rows, end}, or null when line i doesn't start one. Its first row sets
  // the width; later rows may be shorter (the library drops empty cells at the end) and are padded, but a wider row,
  // a line that isn't a row, or two blank lines end it. One blank line or one ruled line between rows is skipped.
  function plainTable(lines, i, max = Infinity) {
    const first = plainCells(lines[i]);
    if (!first) return null;
    const width = first.length;
    const rows = [first];
    let j = i + 1;
    let end = j;
    while (j < lines.length && rows.length < max) {
      let k = j;
      if (!lines[k].trim() || isRule(lines[k])) k++;
      const cells = k < lines.length ? plainCells(lines[k]) : null;
      if (!cells || cells.length > width || (width === 2 && cells.length !== 2)) break;
      while (cells.length < width) cells.push("");
      rows.push(cells);
      j = end = k + 1;
    }
    return rows.length >= 2 ? { rows, end } : null;
  }
  function splitRow(line) {
    let s = line.trim();
    if (s.startsWith("|")) s = s.slice(1);
    if (s.endsWith("|") && !s.endsWith("\\|")) s = s.slice(0, -1);
    const cells = [];
    let cur = "";
    for (let i = 0; i < s.length; i++) {
      if (s[i] === "\\" && s[i + 1] === "|") { cur += "|"; i++; }
      else if (s[i] === "|") { cells.push(cur.trim()); cur = ""; }
      else cur += s[i];
    }
    cells.push(cur.trim());
    return cells;
  }

  const TASK_RE = /^\[([ xX])\](?:[ \t]+|$)/;
  function blocks(lines, out, depth) {
    let i = 0;
    let bareEnd = -Infinity;  // where the last bare table of three or more columns ended
    const CONTINUE_GAP = 40;
    while (i < lines.length) {
      const line = lines[i];
      if (!line.trim()) { i++; continue; }
      let m;
      if (depth >= MAX_DEPTH) {  // too deeply nested: show the rest as plain text
        out.append(h("p", null, lines.slice(i).join("\n")));
        return;
      }
      if (!short(line)) {  // a very long line: plain text, before any pattern runs on it (its statement marks kept)
        out.append(h("p", null, OPTS.claims ? plainClaims(line, OPTS.claims) : line));
        i++;
        continue;
      }
      if ((m = FENCE_RE.exec(line))) {
        const fence = m[1];
        const body = [];
        i++;
        while (i < lines.length && !lines[i].trim().startsWith(fence)) body.push(lines[i++]);
        i++;
        out.append(h("pre", null, h("code", null, noMarks(body.join("\n")))));
        continue;
      }
      if ((m = heading(line))) {
        const level = Math.min(6, m[0] + (OPTS.headingShift || 0));
        out.append(h(`h${level}`, { class: "md-h", id: uniqueId(HEADING_PREFIX + slugify(m[1]), OPTS.ids) }, inline(m[1])));
        i++;
        continue;
      }
      if (isHr(line)) {
        // A rule separates two things: none at the very top, and never two in a row (runs of "---" in a passage drew
        // as a stack of lines).
        const last = out.childNodes[out.childNodes.length - 1];
        if (last && last.tagName !== "HR") out.append(h("hr"));
        i++;
        continue;
      }
      if (QUOTE_RE.test(line)) {
        const body = [];
        while (i < lines.length && lines[i].trim() && (QUOTE_RE.test(lines[i]) || !startsBlock(lines, i))) {
          body.push(lines[i].replace(QUOTE_RE, ""));
          i++;
        }
        const c = short(body[0]) ? CALLOUT_RE.exec(body[0].trim()) : null;
        if (c) {  // a callout: > [!warning] Optional title
          const word = c[1].toLowerCase();
          const kind = Object.prototype.hasOwnProperty.call(CALLOUT_KIND, word) ? CALLOUT_KIND[word] : "note";
          const title = c[2] && c[2].trim() ? inline(c[2].trim()) : c[1][0].toUpperCase() + c[1].slice(1).toLowerCase();
          const inner = h("div", { class: "callout-body" });
          blocks(body.slice(1), inner, depth + 1);
          out.append(h("div", { class: `callout callout-${kind}`, role: "note" }, h("div", { class: "callout-title" }, title), inner));
          continue;
        }
        const q = h("blockquote");
        blocks(body, q, depth + 1);
        out.append(q);
        continue;
      }
      if (isTableStart(lines, i)) {
        const head = splitRow(lines[i]);
        const aligns = splitRow(lines[i + 1]).map((c) => (c.startsWith(":") && c.endsWith(":") ? "al-center" : c.endsWith(":") ? "al-right" : null));
        i += 2;
        const tbody = h("tbody");
        const blank = (cells) => cells.every((c) => !c.trim());
        let rows = 0;
        while (i < lines.length && lines[i].trim() && lines[i].includes("|")) {
          const cells = splitRow(lines[i]);
          i++;
          if (blank(cells)) continue;  // an empty row (a table the library split apart) draws as a bare line
          tbody.append(h("tr", null, head.map((_, c) => h("td", { class: aligns[c] }, inline(cells[c] || "")))));
          rows++;
        }
        if (!rows && blank(head)) continue;  // no text in any cell: nothing to show, not a box of empty lines
        const table = h("table", null,
          blank(head) ? null : h("thead", null, h("tr", null, head.map((c, n) => h("th", { class: aligns[n] }, inline(c))))), tbody);
        out.append(h("div", { class: "table-wrap" }, table));
        continue;
      }
      const bare = OPTS.bareTables === true ? plainTable(lines, i) : null;  // library documents and guides only
      if (bare) {
        // Two columns are label and value, with no header row. Three or more have a header row, unless the table is
        // the rest of a wide one the library broke apart (a cell's line breaks became new lines): only blank lines
        // separate it from that table, or it starts within CONTINUE_GAP lines of it right after a stray row of 3 or
        // more cells. Then every row is data. The "Source:" line and an infobox's last row are never stray rows.
        const rows = bare.rows;
        let p = i - 1;
        while (p >= 0 && !lines[p].trim()) p--;
        const stray = p >= 0 ? plainCells(lines[p]) : null;
        const continued = rows[0].length > 2
          && (p < bareEnd || (!!stray && stray.length > 2 && i - bareEnd <= CONTINUE_GAP));
        i = bare.end;
        if (rows[0].length > 2) bareEnd = i;
        const table = continued
          ? h("table", null, h("tbody", null, rows.map((r) => h("tr", null, r.map((c) => h("td", null, inline(c)))))))
          : rows[0].length === 2
          ? h("table", { class: "kv" }, h("tbody", null, rows.map((r) => h("tr", null, h("th", { scope: "row" }, inline(r[0])), h("td", null, inline(r[1]))))))
          : h("table", null,
            h("thead", null, h("tr", null, rows[0].map((c) => h("th", null, inline(c))))),
            h("tbody", null, rows.slice(1).map((r) => h("tr", null, r.map((c) => h("td", null, inline(c)))))));
        out.append(h("div", { class: "table-wrap" }, table));
        continue;
      }
      if ((m = LIST_RE.exec(line))) {
        const ordered = /\d/.test(m[2]);
        const list = h(ordered ? "ol" : "ul");
        if (ordered) {
          const start = parseInt(m[2], 10);
          if (start !== 1 && Number.isSafeInteger(start)) list.setAttribute("start", String(start));
        }
        while (i < lines.length) {
          const mm = LIST_RE.exec(lines[i]);
          if (!mm || /\d/.test(mm[2]) !== ordered) break;
          const indent = mm[1].length + mm[2].length + 1;
          const task = TASK_RE.exec(mm[3]);  // - [ ] or - [x]: a box to look at, not to tick
          const item = [task ? mm[3].slice(task[0].length) : mm[3]];
          i++;
          while (i < lines.length) {
            const l = lines[i];
            if (!l.trim()) {
              let j = i + 1;
              while (j < lines.length && !lines[j].trim()) j++;
              if (j < lines.length && leading(lines[j]) >= 2) { while (i < j) { item.push(""); i++; } continue; }
              break;
            }
            const lead = leading(l);
            if (lead >= 2) { item.push(l.replace(/\t/g, "    ").slice(Math.min(lead, indent))); i++; continue; }
            if (startsBlock(lines, i)) break;
            item.push(l);  // a wrapped line of the same item
            i++;
          }
          const li = h("li", task ? { class: "task" } : null);
          if (task) li.append(h("input", { class: "task-box", type: "checkbox", disabled: true, checked: task[1] !== " ", tabindex: "-1" }));
          blocks(item, li, depth + 1);
          list.append(li);
          let j = i;
          while (j < lines.length && !lines[j].trim()) j++;
          if (j < lines.length && LIST_RE.test(lines[j]) && /\d/.test(LIST_RE.exec(lines[j])[2]) === ordered) i = j;
          else break;
        }
        out.append(list);
        continue;
      }
      const para = [];
      while (i < lines.length && lines[i].trim() && !(para.length && startsBlock(lines, i))) para.push(lines[i++].trim());
      out.append(h("p", null, inline(para.join("\n"))));
    }
  }

  // Inline patterns are sticky (matched at a position in the original text, never on a sliced copy), and a span must
  // close within SPAN characters, so a stray * or _ costs a bounded amount instead of a scan to the end of the text.
  const SPAN = 2000;
  const INLINE = {
    code: new RegExp(`(\`+)([^\`][\\s\\S]{0,${SPAN}}?)\\1(?!\`)`, "y"),
    strongStar: new RegExp(`\\*\\*(?=\\S)([\\s\\S]{0,${SPAN}}?\\S)\\*\\*`, "y"),
    strongUnder: new RegExp(`__(?=\\S)([\\s\\S]{0,${SPAN}}?\\S)__(?!\\w)`, "y"),
    emStar: new RegExp(`\\*(?=[^\\s*])([\\s\\S]{0,${SPAN}}?[^\\s*])\\*(?!\\*)`, "y"),
    emUnder: new RegExp(`_(?=[^\\s_])([\\s\\S]{0,${SPAN}}?[^\\s_])_(?!\\w)`, "y"),
    strike: new RegExp(`~~(?=\\S)([\\s\\S]{0,${SPAN}}?\\S)~~`, "y"),
    link: /\[([^\]\n]{1,500})\]\(\s*<?([^)\s>\uE000-\uE002]{1,2000})>?(?:\s+"[^"\n]{0,500}")?\s*\)/y,
    url: /https?:\/\/[^\s<>"'`)\]\uE000-\uE002]{1,2000}/y,
    br: /<br\s*\/?>/iy,
  };
  const matchAt = (re, text, i) => { re.lastIndex = i; return re.exec(text); };
  const escapeRe = (s) => s.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");

  // Plain text, with the numbers to call (OPTS.numbers: the current country's ambulance and police numbers) in red
  // where the text says to call one ("call 998", "dial 112 now"), or where one stands alone in bold. A number that
  // runs on into digits or a dash ("112-555-0100") is left alone.
  function plainText(text, frag, whole) {
    const nums = Array.isArray(OPTS.numbers) ? OPTS.numbers.filter((n) => typeof n === "string" && n) : [];
    if (!nums.length) { frag.append(text); return; }
    if (whole && nums.includes(text.trim())) { frag.append(h("span", { class: "num-inline" }, text)); return; }
    const alt = nums.slice().sort((a, b) => b.length - a.length).map(escapeRe).join("|");
    const re = new RegExp(`(\\b(?:call|dial|ring|phone)\\b[^.\\n\\d]{0,30}?)(${alt})(?![\\d-])`, "gi");
    let at = 0;
    let m;
    while ((m = re.exec(text))) {
      const start = m.index + m[1].length;
      frag.append(text.slice(at, start), h("span", { class: "num-inline" }, m[2]));
      at = start + m[2].length;
    }
    frag.append(text.slice(at));
  }

  // A steps block that didn't parse, drawn as plain text: each statement in it keeps its greying and mark.
  function plainClaims(text, claims) {
    const frag = document.createDocumentFragment();
    const t = str(text);
    let claim = null;
    let buf = "";
    const push = () => { if (buf) frag.append(claim && !claim.backed ? h("span", { class: "unbacked" }, buf) : buf); buf = ""; };
    for (let i = 0; i < t.length; i++) {
      const c = t[i];
      if (c === "\uE000") {
        push();
        const end = t.indexOf("\uE001", i + 1);
        const n = end > i && end - i <= 5 ? Number(t.slice(i + 1, end)) : NaN;
        claim = Array.isArray(claims) && Number.isInteger(n) && claims[n] ? claims[n] : null;
        if (end > i && end - i <= 5) i = end;
      } else if (c === "\uE002") {
        push();
        if (claim && !claim.backed) frag.append(" ", h("span", { class: "claim-mark", title: "No passage the AI was given says this." }, "not from the library"));
        claim = null;
      } else if (c !== "\uE001") buf += c;
    }
    push();
    return frag;
  }

  // A checked statement's end: "not from the library" after one that isn't backed, its source number after one that is.
  function claimEnd(claim) {
    if (!claim) return null;
    if (!claim.backed) return h("span", { class: "claim-mark", title: "No passage the AI was given says this." }, "not from the library");
    if (!claim.n) return null;
    const open = OPTS.onSource;
    const n = claim.n;
    return h("sup", { class: "claim-src" }, h("button", { class: "claim-src-btn", type: "button", title: claim.label || `Source ${n}`,
      "aria-label": `Source ${n}${claim.label ? `: ${claim.label}` : ""}`, onclick: (e) => { if (typeof open === "function") open(n - 1, e.currentTarget); } }, String(n)));
  }

  function inline(text, depth = 0) {
    const frag = document.createDocumentFragment();
    if (depth > MAX_DEPTH) { frag.append(noMarks(text)); return frag; }
    let buf = "";
    let only = true;  // nothing but plain text so far
    const flush = (end = false) => {
      if (!buf) return;
      if (OPTS.claim && !OPTS.claim.backed) {  // a statement not backed: its text greyed (numbers to call stay red)
        const span = h("span", { class: "unbacked" });
        plainText(buf, span, end && only && depth > 0);
        frag.append(span);
      } else plainText(buf, frag, end && only && depth > 0);
      buf = "";
    };
    const sub = (t) => inline(t, depth + 1);
    let i = 0;
    while (i < text.length) {
      const c = text[i];
      let m;
      if (c === "\uE000" || c === "\uE001" || c === "\uE002") {  // a statement's start or end (only with OPTS.claims)
        flush();
        only = false;
        if (c === "\uE000") {
          const end = text.indexOf("\uE001", i + 1);
          const n = end > i && end - i <= 5 ? Number(text.slice(i + 1, end)) : NaN;
          OPTS.claim = OPTS.claims && Number.isInteger(n) && OPTS.claims[n] ? OPTS.claims[n] : null;
          i = end > i && end - i <= 5 ? end + 1 : i + 1;
        } else {
          if (c === "\uE002" && OPTS.claim) { const mark = claimEnd(OPTS.claim); if (mark) frag.append(" ", mark); OPTS.claim = null; }
          i++;
        }
        continue;
      }
      if (c === "\\" && /[\\`*_{}[\]()#+\-.!~|>]/.test(text[i + 1] || "")) { buf += text[i + 1]; i += 2; continue; }
      if (c === "\n") { flush(); only = false; frag.append(h("br")); i++; continue; }
      if (c === "`" && (m = matchAt(INLINE.code, text, i))) {
        flush(); only = false; frag.append(h("code", { class: OPTS.claim && !OPTS.claim.backed ? "unbacked" : null }, noMarks(m[2]))); i += m[0].length; continue;
      }
      if (c === "*" || c === "_") {
        const wordInside = c === "_" && /\w/.test(text[i - 1] || "");
        if (!wordInside) {
          m = matchAt(c === "*" ? INLINE.strongStar : INLINE.strongUnder, text, i);
          if (m) { flush(); only = false; frag.append(h("strong", null, sub(m[1]))); i += m[0].length; continue; }
          m = matchAt(c === "*" ? INLINE.emStar : INLINE.emUnder, text, i);
          if (m) { flush(); only = false; frag.append(h("em", null, sub(m[1]))); i += m[0].length; continue; }
        }
      }
      if (c === "~" && (m = matchAt(INLINE.strike, text, i))) {
        flush(); only = false; frag.append(h("del", null, sub(m[1]))); i += m[0].length; continue;
      }
      if (c === "[" && text[i + 1] === "^" && OPTS.notes && (m = matchAt(FOOTREF, text, i)) && OPTS.notes.defs.has(m[1])) {
        flush();
        only = false;
        const [n, refId] = noteNumber(OPTS.notes.defs.get(m[1]), m[1]);
        frag.append(h("sup", { class: "fn-ref" }, anchorBtn(OPTS.notes.defs.get(m[1]).id, String(n), { id: refId, "aria-label": `Footnote ${n}` })));
        i += m[0].length;
        continue;
      }
      if (c === "!" && text[i + 1] === "[" && (m = matchAt(INLINE.link, text, i + 1))) {
        flush(); only = false; frag.append(image(m[1].trim(), m[2])); i += 1 + m[0].length; continue;
      }
      if (c === "[" && (m = matchAt(INLINE.link, text, i))) {
        flush();
        only = false;
        const label = m[1].trim();
        const g = m[2].startsWith("guide:") ? guideLink(m[2]) : null;
        if (m[2].startsWith("#")) frag.append(anchorBtn(anchorId(m[2].slice(1)), sub(m[1])));  // a jump within the document
        else if (m[2].startsWith("guide:")) {  // another guide: opened by app.js; an unknown one stays plain text
          const known = g && typeof OPTS.onGuide === "function" && (OPTS.guides instanceof Set ? OPTS.guides : new Set(Array.isArray(OPTS.guides) ? OPTS.guides : [])).has(g.slug);
          if (known) {
            const open = OPTS.onGuide;
            frag.append(h("button", { class: "md-anchor guide-link", type: "button", dataset: { guide: g.slug },
              onclick: (e) => { e.preventDefault(); open(g.slug, g.anchor, e.currentTarget); } }, sub(m[1])));
          } else frag.append(sub(m[1]));
        } else if (label === m[2]) frag.append(webLink(m[2]));
        else frag.append(sub(m[1]), " (", webLink(m[2]), ")");
        i += m[0].length;
        continue;
      }
      if (c === "h" && !/\w/.test(text[i - 1] || "") && (m = matchAt(INLINE.url, text, i))) {
        const url = m[0].replace(/[.,;:!?*_]+$/, "");  // kept whole, so a _ or * inside it isn't read as emphasis
        flush(); only = false; frag.append(webLink(url)); i += url.length; continue;
      }
      if (c === "<" && (m = matchAt(INLINE.br, text, i))) { flush(); only = false; frag.append(h("br")); i += m[0].length; continue; }
      buf += c;
      i++;
    }
    flush(true);
    return frag;
  }

  // A button label that changes while the button works ("Check for updates" -> "Checking…"): the shown label sits over
  // a hidden copy of the other one (app.css .swap), so the button keeps one size in both states and its row never
  // shifts. textContent is the shown label only.
  function swapLabel(busy, busyText, idleText) {
    return h("span", { class: "swap", "data-alt": busy ? idleText : busyText }, h("span", null, busy ? busyText : idleText));
  }

  root.SLRender = { h, swapLabel, md, plainClaims, inline: (t) => inline(str(t)), parseSteps, splitAnswer, visibleText, urlText, setWebOpener, setWebCheck, EMERGENCY, outline, jumpTo, ...PURE };
})(typeof window !== "undefined" ? window : globalThis);
