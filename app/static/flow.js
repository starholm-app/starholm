// Starholm interface: the timing and bookkeeping parts of asking a question, kept free of the DOM so the checks
// in tests/interface_flow_check.js can run them with node.
//
// - createFound: the documents a question found, for the side panel, as they arrive (the "documents" event comes
//   right after each library search, before the answer is written).
// - createPoller: the status check, which never runs while the window is hidden.
// - createRenderer: re-renders a streaming answer at most once per animation frame and at least MIN_GAP ms apart.
// - shouldWarm: whether to load the model ahead of the first question (never with "Documents only" on).
// - createFollow: whether the chat keeps scrolling down with a streaming answer, or stays where the person scrolled.
// Nothing here touches the page: app.js passes in the timers and reads the results.
(function (root) {
  "use strict";

  const str = (v) => (typeof v === "string" ? v : v == null ? "" : String(v));
  const isId = (id) => Number.isSafeInteger(Number(id)) && Number(id) > 0;
  const MAX_FOUND = 24;  // documents kept for one question
  const MAX_PASSAGE = 6000;  // characters of one passage (the server sends at most this)

  // ---------- documents found for one question ----------
  // add(ev) takes a "documents" event ({early, documents: [{id, title, source, official, key, url, passage}]}) and
  // returns true when the list changed. One document per article (title and source), at most MAX_FOUND.
  // - early: true is the quick search on the question as typed, sent before the model starts: the list shows it at
  //   once and is "refining" until the model's own results (or the fetch step's) come.
  // - Every other event is the model's: its documents take the top of the list, after the model's earlier ones, and
  //   the quick results not among them move below. Nothing is ever taken out, so a document being read stays.
  // A saved "Documents only" question carries the same list without passages (they are loaded when shown).
  function createFound() {
    const items = [];
    const articleOf = (d) => `${str(d.title)}\u0000${str(d.source)}`;
    const clean = (d, early) => ({ id: Number(d.id), title: str(d.title) || "Untitled", source: str(d.source), official: d.official === true,
      key: str(d.key), url: str(d.url), passage: str(d.passage).slice(0, MAX_PASSAGE), early });
    const found = {
      items,
      refining: false,  // only the quick search's results so far
      add(ev) {
        const early = !!(ev && ev.early === true);
        const incoming = [];
        const seen = new Set();
        for (const d of Array.isArray(ev && ev.documents) ? ev.documents : []) {
          if (!d || typeof d !== "object" || !isId(d.id) || seen.has(articleOf(d))) continue;
          seen.add(articleOf(d));
          incoming.push(clean(d, early));
        }
        const before = items.map((d) => `${d.id}:${d.early}`).join(",");
        if (early) {
          const have = new Set(items.map(articleOf));
          items.push(...incoming.filter((d) => !have.has(articleOf(d))));
          if (!items.some((d) => !d.early)) found.refining = items.length > 0;
        } else {
          const model = items.filter((d) => !d.early);
          const inModel = new Set(model.map(articleOf));
          const fresh = incoming.filter((d) => !inModel.has(articleOf(d))).map((d) => {
            const old = items.find((x) => articleOf(x) === articleOf(d));  // a quick result promoted: keep its passage if the new one has none
            return old && !d.passage ? { ...d, passage: old.passage } : d;
          });
          const freshSet = new Set(fresh.map(articleOf));
          const rest = items.filter((d) => d.early && !freshSet.has(articleOf(d)));
          items.splice(0, items.length, ...model, ...fresh, ...rest);
          found.refining = false;
        }
        while (items.length > MAX_FOUND) {  // the last ones go, never the one being read
          const at = items.length - 1 - [...items].reverse().findIndex((d) => articleOf(d) !== found.reading);
          items.splice(at, 1);
        }
        return items.map((d) => `${d.id}:${d.early}`).join(",") !== before;
      },
      done() { found.refining = false; },  // the answer finished: the list is as good as it gets
      reading: null,  // the article opened from this list for reading, kept whatever comes after
      opened(d) { found.reading = d ? articleOf(d) : null; },
    };
    return found;
  }

  // ---------- the status check ----------
  // load(): fetch and show the status, returning how many ms to wait before the next check (0: none).
  // schedule(fn, ms) / cancel(handle): setTimeout and clearTimeout. isHidden(): document.hidden.
  // tick() checks now (unless hidden) and schedules the next one; visibility() is for the visibilitychange event:
  // hidden cancels the next check, visible checks at once.
  function createPoller({ load, schedule, cancel, isHidden }) {
    let timer = null;
    let busy = false;
    const stop = () => { if (timer !== null) { cancel(timer); timer = null; } };
    const poller = {
      async tick() {
        stop();
        if (busy || isHidden()) return;
        busy = true;
        let wait = 0;
        try { wait = await load(); } finally { busy = false; }
        stop();
        if (wait > 0 && !isHidden()) timer = schedule(() => { timer = null; poller.tick(); }, wait);
      },
      visibility() { if (isHidden()) stop(); else poller.tick(); },
      get pending() { return timer !== null; },
    };
    return poller;
  }

  // ---------- re-rendering a streaming answer ----------
  // request() asks for a render; renders happen inside an animation frame, at most one per frame, and each starts at
  // least MIN_GAP ms after the last one ended (longer when one render is slow: three times its cost, up to a second).
  const MIN_GAP = 100;
  function createRenderer(run, { raf, setTimer, now }) {
    let queued = false;
    let stopped = false;
    let last = -Infinity;
    let cost = 0;
    const frame = () => raf(() => {
      queued = false;
      if (stopped) return;
      const t0 = now();
      run();
      last = now();
      cost = last - t0;
    });
    return {
      request() {
        if (queued || stopped) return;
        queued = true;
        const gap = Math.max(MIN_GAP, Math.min(1000, cost * 3));
        setTimer(frame, Math.max(0, last + gap - now()));
      },
      stop() { stopped = true; },
    };
  }

  // ---------- following a streaming answer down ----------
  // The chat follows the answer while it is written, as long as the view is at the bottom (within FOLLOW_GAP px).
  // Any move up by the person stops that at once and the view stays where they put it while text keeps coming;
  // scrolling back to the bottom (or "Jump to latest", which calls reset()) follows again. m is the scroller's
  // numbers: {scrollTop, scrollHeight, clientHeight}.
  // - intent(up, m): a wheel, key, touch or scrollbar press, seen before the scroll it causes. It counts before any
  //   scroll event, so a render that lands between the two can't pull the view back down. Ignored when the view
  //   can't go up anyway (scrollTop 0), so a stray wheel over a short answer doesn't stop the following.
  // - scrolled(m): a scroll event. Up and away from the bottom: stop following; at the bottom: follow again. A view
  //   pushed up because the content got shorter still sits at the bottom, so it keeps following.
  // - target(m): where to put scrollTop after the content changed: the bottom while following, else null (leave it,
  //   so what the person is reading doesn't move); wrote(top) records a scrollTop written by the page itself.
  // - showJump(m): whether to offer "Jump to latest" (not following, and there is more below).
  const FOLLOW_GAP = 24;
  function createFollow({ gap = FOLLOW_GAP } = {}) {
    let following = true;
    let lastTop = null;
    const below = (m) => m.scrollHeight - m.scrollTop - m.clientHeight;
    return {
      get following() { return following; },
      intent(up, m) { if (up && m && m.scrollTop > 0) following = false; return following; },
      scrolled(m) {
        const away = below(m);
        if (lastTop !== null && m.scrollTop < lastTop - 1 && away > 2) following = false;
        else if (away <= gap) following = true;
        lastTop = m.scrollTop;
        return following;
      },
      target(m) { return following ? Math.max(0, m.scrollHeight - m.clientHeight) : null; },
      wrote(top) { lastTop = top; },
      showJump(m) { return !following && below(m) > gap; },
      reset() { following = true; lastTop = null; },
    };
  }

  // ---------- loading the model ahead of the first question ----------
  // s: /api/status. warmed: the model already asked for. Never with "Documents only" on: no model runs until Summarise.
  function shouldWarm(s, warmed) {
    if (!s || !s.ollama || !s.model_installed) return false;
    if (s.answers && s.answers.documents_only === true) return false;
    const loaded = Array.isArray(s.loaded) && s.loaded.includes(s.model);
    const warming = Array.isArray(s.warming) && s.warming.includes(s.model);
    return !loaded && !warming && warmed !== s.model;
  }

  // ---------- the guided questions on the welcome screen (app/static/guided-tree.js) ----------
  // The step reached by following path (the indexes of the options chosen, from the top) through tree:
  // {depth, question: the label of the question answered last ("" at the top), call: show the emergency number first
  // (this question or one above it is marked call),
  //  options: [{label, kind: "question"|"guide"|"search"|"ask", guide, anchor, search, ask, index}]}, or null when the
  // path leads nowhere or ends on an end point. kind tells app.js what an option does.
  const GUIDED_DEPTH = 3;  // levels of questions, counting the top one
  function guidedKind(o) {
    if (o && Array.isArray(o.options)) return "question";
    if (o && typeof o.guide === "string") return "guide";
    if (o && typeof o.search === "string") return "search";
    if (o && typeof o.ask === "string") return "ask";
    return null;
  }
  function guidedStep(tree, path) {
    let node = tree;
    let call = tree && tree.call === true;  // a question marked call keeps the number first on every step under it
    for (const i of Array.isArray(path) ? path : []) {
      const next = node && Array.isArray(node.options) ? node.options[i] : null;
      if (guidedKind(next) !== "question") return null;
      node = next;
      call = call || node.call === true;
    }
    if (!node || !Array.isArray(node.options)) return null;
    const depth = Array.isArray(path) ? path.length : 0;
    return {
      depth,
      question: depth ? str(node.label) : "",
      call,
      options: node.options.map((o, index) => ({ label: str(o && o.label), kind: guidedKind(o), index,
        guide: o && typeof o.guide === "string" ? o.guide : null, anchor: o && typeof o.anchor === "string" ? o.anchor : null,
        search: o && typeof o.search === "string" ? o.search : null, ask: o && typeof o.ask === "string" ? o.ask : null }))
        .filter((o) => o.label && o.kind),
    };
  }
  // What a step shows, in order: the emergency number first when someone may be in danger ("call"), then the question
  // answered ("question"), its options, and Back / Start over ("nav") below any step but the top.
  function guidedBlocks(step) {
    if (!step) return [];
    return [step.call ? "call" : null, step.depth ? "question" : null, "options", step.depth ? "nav" : null].filter(Boolean);
  }
  // Every end point with the path to it ([{path, depth, labels, option, call}]), for the checks. call: a question on
  // the way showed the emergency number first.
  function guidedLeaves(tree, path = [], labels = [], out = [], call = false) {
    const step = guidedStep(tree, path);
    if (!step) return out;
    const shown = call || step.call;
    for (const o of step.options) {
      if (o.kind === "question") guidedLeaves(tree, [...path, o.index], [...labels, o.label], out, shown);
      else out.push({ path: [...path, o.index], depth: path.length + 1, labels: [...labels, o.label], option: o, call: shown });
    }
    return out;
  }

  const API = { createFound, createPoller, createRenderer, createFollow, shouldWarm, guidedStep, guidedBlocks, guidedLeaves, GUIDED_DEPTH,
    MIN_GAP, MAX_FOUND, FOLLOW_GAP };
  if (typeof module !== "undefined" && module.exports) module.exports = API;
  root.SLFlow = API;
})(typeof window !== "undefined" ? window : globalThis);
