"""Terminal chat with a local model, answering the way the app does by default (core.py, "fetch-quoted").

Usage: chat.py [--model NAME] [question ...]   (default: the model chosen in the app, else core.DEFAULT_MODEL)
With a question it answers once and exits; without one it opens a chat.
Each answer is checked the way the app checks it: the app finds the passages (library and guides), the model cites
the sentences it uses, and code marks any statement no passage backs with "(not from the library)". The answer is
shown whole once it is checked, with its sources listed under it.
In a chat: /sources lists what the last answer used, /open N opens one, /reasoning shows its reasoning, /new,
/think off|short|medium|long|none sets how much it may reason before answering, /limit normal|long|none sets how long
an answer may run, /bye.
"""
import datetime
import os
import re
import subprocess
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from core import (GUIDES_DIR, LENGTH_LIMITS, REASONING_LIMITS, _sources_block, capabilities, chat_events,  # noqa: E402
                  chosen_model, installed_models, strip_claim_marks, system_prompt, trim)
from library.search import DB_PATH, Library  # noqa: E402
from memory import prompt_section  # noqa: E402

DIM, BOLD, RESET = "\033[2m", "\033[1m", "\033[0m"
MODE = "fetch-quoted"  # the app's default answer mode (app/server.py ANSWER_DEFAULTS)
NO_MATCH = "I couldn't find this in your library."  # the app's own line (app/static/app.js)
NOT_BACKED = "(not from the library)"  # the app greys such a statement and adds "not from the library"
# Terminal control characters (C0, DEL, C1). A title, search or answer holding ESC sequences could rewrite the screen,
# change the window title or hide text, so none of them is printed.
_CONTROL = re.compile(r"[\x00-\x1f\x7f-\x9f]")
_CONTROL_KEEP_LINES = re.compile(r"[\x00-\x08\x0b-\x1f\x7f-\x9f]")
# A checked statement as core.annotate marks it: CLAIM_START n CLAIM_MID statement CLAIM_END.
_CLAIM = re.compile("(\\d{1,4})([^-]*)")


def clean(text):
    """One line of text from the library or the model, safe to print: control characters become spaces."""
    return _CONTROL.sub(" ", str(text))


def clean_text(text):
    """Longer text (an answer, reasoning) safe to print: control characters go, newlines and tabs stay."""
    return _CONTROL_KEEP_LINES.sub("", str(text))


def call_text(target):
    """The call line the app draws from a call target (core.emergency_call_target), word for word as render.js does:
    "Call 998 now." or "Call your local emergency number now."."""
    target = target or {}
    numbers = target.get("numbers") or []
    if numbers:
        return f"Call {' or '.join(numbers)} now."
    return f"Call {target.get('text') or 'your local emergency number'} now."


def source_label(source):
    """A source as the list shows it: "title (source name)". A guide section's title already holds the guide's title
    and the section's heading, and its source name is the guides'."""
    return f"{source['title']} ({source['source']})" if source.get("source") else str(source["title"])


def source_number(source, sources):
    """The 1-based place of a claim's source in the list of sources, or 0: by id, else by title and source name, as
    render.js sourceIndex does."""
    if not isinstance(source, dict):
        return 0
    same = lambda a, b: a is not None and b is not None and (a == b or str(a) == str(b))  # noqa: E731  (12 and "12")
    for n, s in enumerate(sources, 1):
        if same(s.get("id"), source.get("id")):
            return n
    for n, s in enumerate(sources, 1):
        if str(s.get("title") or "") == str(source.get("title") or "") and str(s.get("source") or "") == str(source.get("source") or ""):
            return n
    return 0


def shown_answer(done):
    """The text of an answer as the terminal shows it, from the done event of core.chat_events: the checked text with
    each statement no passage backs followed by "(not from the library)", and each backed one by the numbers of its
    sources in the list under the answer; no Sources line (the list is printed apart); the app's own call line under
    it when the event carries one. The no-match answer is the app's two lines, never the model's words."""
    if done.get("no_match"):
        lines = [NO_MATCH]
        if done.get("no_match_call"):
            lines.append(call_text(done["no_match_call"]))
        return "\n".join(lines)
    sources = done.get("sources") or []
    claims = done.get("claims") or []

    def mark(found):
        index = int(found.group(1))
        claim = claims[index] if index < len(claims) else None
        if claim and not claim.get("backed"):
            return f"{found.group(2)} {NOT_BACKED}"
        cited = sorted({n for n in (source_number(s, sources) for s in claim.get("sources") or [claim.get("source")])
                        if n}) if claim else []
        return found.group(2) + (f" [{', '.join(map(str, cited))}]" if cited else "")

    text = done.get("marked") or done.get("answer") or ""
    text = strip_claim_marks(_CLAIM.sub(mark, text))
    block = _sources_block(text)
    if block:
        text = text[:block[0]]
    text = text.strip("\n")
    if text and done.get("emergency_call"):
        text += "\n\n" + call_text(done["emergency_call"])
    return text


def sources_text(done):
    """The sources an answer used, one numbered line each, or the line saying there were none."""
    if done.get("no_match") or not (done.get("answer") or "").strip():
        return ""
    sources = done.get("sources") or []
    if not sources:
        return "Sources: none (no passage the model was given backs what it says)"
    return "Sources:\n" + "\n".join(f"  {n}. {clean(source_label(s))}" for n, s in enumerate(sources, 1))


def open_article(library, passage_id):
    """Write the article to a private Markdown file, marking the cited passage, and open it in TextEdit. A guide
    section (an id like "guide:slug#heading") opens the guide's own file."""
    if isinstance(passage_id, str) and passage_id.startswith("guide:"):
        slug = passage_id[6:].split("#")[0]
        path = os.path.join(GUIDES_DIR, slug, "guide.md")
        if re.fullmatch(r"[a-z0-9-]{1,80}", slug) and os.path.isfile(path):
            subprocess.run(["/usr/bin/open", "-t", path])
            print(f"{DIM}  · opened guide: {clean(slug)}{RESET}")
        return
    doc = library.document(passage_id) if library else None
    if not doc:
        return
    lines = [f"# {doc['title']}", f"Source: {doc['source']}" + (f" | {doc['url']}" if doc["url"] else ""), ""]
    for part in doc["parts"]:
        if part["id"] == passage_id:
            lines += ["", ">>> THE PASSAGE THE ANSWER CAME FROM <<<", ""]
        lines.append(part["text"])
        lines.append("")
    fd, path = tempfile.mkstemp(prefix=f"survival-llm-article-{int(doc['doc_id'])}-", suffix=".md")  # mode 600
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))
    subprocess.run(["/usr/bin/open", "-t", path])
    print(f"{DIM}  · opened: {clean(doc['title'])} ({clean(doc['source'])}){RESET}")


def run_turn(model, library, messages, level, caps, length="normal"):
    """Print one answer: what the app is doing as it goes, then the checked answer and its sources once it is done.
    The model's words are not streamed: they hold citation markers until the check has run. Returns the sources it
    used and its reasoning (for /reasoning)."""
    sources, reasoning = [], []  # reasoning: what it thought, with the searches in between
    thinking, thoughts, writing = False, 0, False  # in a stretch of reasoning; how many stretches; answer started
    for ev in chat_events(model, library, messages, reasoning=level, caps=caps, length=length, mode=MODE):
        kind = ev["type"]
        if kind == "retry":
            writing = False  # what streamed so far was not the answer
        if kind != "tick":
            thinking = thinking and kind == "thinking"
        if kind == "thinking":
            if not thinking and ev["text"].strip():
                print(f"{DIM}  · thinking{'' if thoughts else ' (/reasoning shows it)'}{RESET}", flush=True)
                reasoning.append("")
                thinking, thoughts = True, thoughts + 1
            if thinking:
                reasoning[-1] += ev["text"]
        elif kind == "search":
            print(f"{DIM}  · searching: {clean(ev['query'])}{RESET}", flush=True)
            reasoning.append(f"· searching: {ev['query']}")
        elif kind == "read":
            print(f"{DIM}  · reading: {clean(ev['title'])} ({clean(ev['source'])}){RESET}", flush=True)
            reasoning.append(f"· reading: {ev['title']} ({ev['source']})")
        elif kind == "think_limit":
            print(f"{DIM}  · its thinking reached the limit; answering from what it has{RESET}", flush=True)
            reasoning.append("· thinking reached the limit")
        elif kind == "token":
            if not writing and ev["text"].strip():
                print(f"{DIM}  · writing the answer{RESET}", flush=True)
                writing = True
        elif kind == "done":
            sources = ev["sources"]
            answer = shown_answer(ev)
            print()
            if answer:
                print(clean_text(answer))
            listed = sources_text(ev)
            if listed:
                print(f"\n{DIM}{listed}{RESET}")
            if ev.get("follow_ups"):
                print(f"\n{DIM}Follow-ups: " + " | ".join(clean(q) for q in ev["follow_ups"]) + RESET)
            speed = f" · {ev['tokens'] / ev['seconds']:.0f} tok/s" if ev["seconds"] else ""
            note = " · /sources lists what it used" if sources else ""
            if ev.get("cut_off"):
                print(f"\n{DIM}[stopped: the answer ran too long; try asking again]{RESET}")
            elif ev.get("unfinished"):
                print(f"\n{DIM}[it did not write an answer; try asking again]{RESET}")
            print(f"\n{DIM}[{ev['tokens']} tokens{speed}{note}]{RESET}")
    return sources, "\n\n".join(r.strip() for r in reasoning if r.strip()) if thoughts else ""


def main():
    args = sys.argv[1:]
    model = None
    if args[:1] == ["--model"]:
        if len(args) < 2:
            sys.exit("Use --model NAME.")
        model, args = args[1], args[2:]
    model = model or chosen_model()
    if not model:  # never a guess: the model is chosen by hand
        names = [m for m in installed_models() or [] if not any(x in m for x in ("embed", "bge-", "nomic", "minilm"))]
        listed = f" Installed: {', '.join(clean(n) for n in names)}." if names else ""
        sys.exit(f"No model chosen. Pick one in the app, or run with --model NAME.{listed}")
    library = None
    if os.path.exists(DB_PATH):
        try:
            library = Library()
            if library.stats()[0] == 0:
                library = None
        except Exception:
            library = None
    caps = capabilities(model)
    # The app's instructions for fetch-quoted: the app looks things up, so the model gets no tools; then today's date.
    prompt = system_prompt(library, False, quotes="fetch") + "\n\n" + prompt_section([], False, (), datetime.date.today())
    messages = [{"role": "system", "content": prompt}]
    level, length = "off", "normal"  # the Reasoning setting, and the length limit

    if args:  # one question, one answer
        messages.append({"role": "user", "content": " ".join(args)})
        run_turn(model, library, messages, level, caps)
        return

    try:
        import readline  # noqa: F401  (arrow keys and history in input())
    except ImportError:
        pass
    docs, n_sources = library.stats() if library else (0, 0)
    lib_note = f"library: {docs:,} articles from {n_sources} sources" if library else "no library found"
    print(f"{BOLD}{clean(model.removesuffix(':latest'))}{RESET} {DIM}· offline · {lib_note}{RESET}")
    print(f"{DIM}Ask anything. /sources shows what the last answer used, /open N opens one, /reasoning shows how it "
          f"reasoned, /new starts over, /think sets how much it may reason, /limit sets the answer length limit, /bye quits.{RESET}")
    last_sources, last_reasoning = [], ""
    while True:
        try:
            question = input("\n> ").strip()
        except (EOFError, KeyboardInterrupt):
            print()
            return
        if not question:
            continue
        if question in ("/bye", "/quit", "/exit"):
            return
        if question == "/new":
            messages, last_sources = messages[:1], []
            print(f"{DIM}Started over.{RESET}")
            continue
        if question.startswith("/think"):
            pick = question[6:].strip()
            if pick in REASONING_LIMITS:
                level = pick
                budget = REASONING_LIMITS[pick]
                print(f"{DIM}Reasoning: {pick}" + (f" (up to {budget:,} tokens, then it answers)" if budget > 0 else
                      " (no limit; Ctrl+C stops a model stuck reasoning in circles)" if budget < 0 else "") + f".{RESET}")
            else:
                print(f"{DIM}Use /think off, short, medium, long or none (now: {level}).{RESET}")
            continue
        if question.startswith("/limit"):
            pick = question[6:].strip()
            if pick in LENGTH_LIMITS:
                length = pick
                print(f"{DIM}Length limit: {pick}{' (use Ctrl+C to stop a stuck answer)' if pick == 'none' else ''}.{RESET}")
            else:
                print(f"{DIM}Use /limit normal, /limit long or /limit none (now: {length}).{RESET}")
            continue
        if question == "/reasoning":
            print(f"{DIM}{clean_text(last_reasoning)}{RESET}" if last_reasoning else
                  f"{DIM}The last answer had no reasoning. /think makes the model reason before it answers.{RESET}")
            continue
        if question == "/sources":
            if not last_sources:
                print(f"{DIM}The last answer did not use anything from the library.{RESET}")
            for n, s in enumerate(last_sources, 1):
                print(f"  {n}. {clean(source_label(s))}")
            continue
        if question.startswith("/open"):
            pick = question[5:].strip() or "1"
            if pick.isdigit() and 1 <= int(pick) <= len(last_sources):
                open_article(library, last_sources[int(pick) - 1]["id"])
            else:
                print(f"{DIM}Use /open N, where N is a number from /sources.{RESET}")
            continue
        messages.append({"role": "user", "content": question})
        trim(messages)
        print()
        last_reasoning = ""
        try:
            used, last_reasoning = run_turn(model, library, messages, level, caps, length)
            last_sources = used or last_sources
        except KeyboardInterrupt:
            print(f"\n{DIM}[stopped]{RESET}")
        except OSError as exc:
            print(f"\n{DIM}[could not reach the local model: {clean(exc)}. Is Ollama running? Try: "
                  f"brew services start ollama]{RESET}")


if __name__ == "__main__":
    main()
