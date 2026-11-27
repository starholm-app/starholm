"""Shared chat logic for the terminal (chat.py), the app (app/server.py) and the tests (evals/).

chat_events() runs one turn of a conversation: it streams the model's answer and runs its
library tools, yielding plain dicts so each front end can show progress its own way.
Talks only to the local Ollama server, so it works with Wi-Fi off.

Every Ollama call goes through ollama_request(), which is locked to 127.0.0.1:11434: OLLAMA_HOST is ignored, proxy
variables are bypassed and redirects are refused, so questions, memory notes and file excerpts never leave this Mac.
"""
import json
import os
import re
import sys
import unicodedata
import urllib.request

import emergency
from library.search import STOPWORDS, TOPICS, query_words
from memory import _stems, asked_memory, forgets

# ---------- the one Ollama this app talks to ----------

OLLAMA_ADDRESS = ("127.0.0.1", 11434)
HOST = "http://127.0.0.1:11434"  # fixed: never read from OLLAMA_HOST


def _warn_ignored_host():
    """OLLAMA_HOST pointing anywhere but this Mac's own Ollama is ignored, and said so once on stderr."""
    value = os.environ.get("OLLAMA_HOST", "").strip()
    if value and value.removeprefix("http://").rstrip("/") not in ("127.0.0.1:11434", "localhost:11434"):
        print(f"Ignoring OLLAMA_HOST={value!r}: Starholm only talks to Ollama at {HOST}.", file=sys.stderr)


_warn_ignored_host()


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    """A redirect from "Ollama" would point somewhere else: refuse it (urllib raises HTTPError for the 3xx)."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


# ProxyHandler({}) replaces urllib's default, which reads http_proxy, HTTP_PROXY, all_proxy and friends.
_opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), _NoRedirect())


def ollama_request(path, body=None, timeout=900, method=None):
    """Open HOST + path (a GET, or a POST of body as JSON; method="DELETE" for Ollama's delete) on the local Ollama,
    with no proxy. Returns the response."""
    if not (isinstance(path, str) and path.startswith("/api/") and "://" not in path and "@" not in path):
        raise ValueError(f"not an Ollama API path: {path!r}")
    if method not in (None, "DELETE"):
        raise ValueError(f"not a method the app uses: {method!r}")
    data = None if body is None else json.dumps(body).encode()
    req = urllib.request.Request(HOST + path, data=data, headers={"Content-Type": "application/json"} if data else {},
                                 method=method)
    return _opener.open(req, timeout=timeout)


# ---------- which model: chosen by hand, never switched by the app ----------

# The model the app uses until the person picks another in the app (settings.json "model"). Chosen by hand after
# comparing models on this project's own questions; the app never switches it.
DEFAULT_MODEL = "gemma4:26b-a4b-it-qat"
# The checkout this code runs from. The app keeps everything it writes inside it, never in the home folder.
REPO = os.path.dirname(os.path.realpath(__file__))
# Settings, memory, saved chats and the file index (folder mode 700, files mode 600).
APP_DIR = os.environ.get("SURVIVAL_LLM_APP_DIR") or os.path.join(REPO, "app-data")
SETTINGS = os.path.join(APP_DIR, "settings.json")


def chosen_model():
    """The model the person picked in the app, else DEFAULT_MODEL, else None. Never a guess from what's installed."""
    try:
        with open(SETTINGS, encoding="utf-8") as f:
            value = json.load(f).get("model")
    except (OSError, ValueError, AttributeError):
        value = None
    return value if isinstance(value, str) and value else DEFAULT_MODEL


NUM_CTX = 32768  # the context every request asks for; a different one would make Ollama reload the model
MAX_TOOL_ROUNDS = 6
# How much of an article one read_article call gives the model (characters, from the passage it asked for). Reading
# took about half of each answer's time with qwen3.8:27b-mlx (80 seconds against a 45-second target); a smaller
# read keeps answers quick.
READ_CHARS = 3000
# Longest reply the model may write in one go, set by the person's "Length limit" (tokens; -1 = no limit). A model
# stuck repeating itself would otherwise run, and drain the battery, until someone noticed (Qwen 3.6 once went an
# hour on one question). With no limit, only the Stop button ends a stuck answer.
LENGTH_LIMITS = {"normal": 8192, "long": 32768, "none": -1}
# How much it may reason before it answers, set by the person's "Reasoning" setting: tokens of reasoning in one round,
# after which its reasoning is cut and it has to answer from what it has (-1 = no limit). A model reasoning in circles
# would otherwise go on until the length limit and never answer. "off" asks it not to reason at all.
REASONING_LIMITS = {"off": 0, "short": 1000, "medium": 4000, "long": 16000, "none": -1}

BASE_PROMPT = """You are an offline assistant running entirely on this computer, with no internet access: you can't go online, call or contact anyone for the person. The person asking may be dealing with a power outage or another emergency, possibly running on battery."""

LIBRARY_PROMPT = """

You have an offline library: search it with search_library and read results with read_article. The ids are for your tools; don't show them to the person. It holds: {labels}.{personal} If the person asks to see a source or the full instructions, open it for them with show_article."""

# The no-match rule, only when a library is loaded; without one the model answers from what it knows.
NO_MATCH_RULE = ('- If the library has nothing useful, say "I couldn\'t find this in your library." and nothing else, '
                 'unless they need emergency help now: then add one more line, "Call {number} now."')
# With a library, answers say only what the library backs (the owner, 12 November 2026: "Yes, strict"); the one exception
# is an estimate of a number nobody has measured, labelled as one. Without a library, the earlier wording stays.
STRICT_PARTIAL_RULE = ("- When the library backs only part of an answer, give only what it backs, except a labelled estimate "
                       "of an unmeasured number (rule above), and say plainly which part you couldn't find in the library; "
                       "don't fill the gap from your own knowledge. Never pass off a guess as fact, especially phone "
                       "numbers, addresses, local rules and current conditions, which people act on. This never overrides "
                       "the safety rules above: always name real dangers and say when to call {call}.")
NO_LIBRARY_PARTIAL_RULE = '- When the library only partly backs an answer, fill gaps from what you know, as long as you say so plainly ("From what I know:" or "I\'m guessing here:"). Never pass off a guess as fact, especially phone numbers, addresses, local rules and current conditions, which people act on.'
LABELLED_ESTIMATE_RULE = ("- When asked for a number nobody has measured (a probability, a risk in micromorts, an amount), estimate "
                          "it anyway, even when the library has nothing on it: reason it out from what you know, give a number or a "
                          "range, say plainly that it is an "
                          "estimate, and say how confident you are.")
NO_LIBRARY_ESTIMATE_RULE = '- When asked for a number nobody has measured (a probability, a risk in micromorts, an amount), estimate it anyway: reason it out from what you know, give a number or a range, and say how confident you are.'
# Which answers the safety, Follow-ups and Sources rules cover: with a library, all but the no-match answer.
SAFETY_SCOPE = (" This applies to every answer except the no-match answer, which is only its line, plus the call line "
                "when they need emergency help now.")
EVERY_ANSWER = "every answer except the no-match answer (a bullets-only answer included)"
# Right after the section 7 rules: the model check of 12 November 2026 found lead-in sentences and 5 or 6 bullets.
OPENING_RULE = ("- {apart}othing comes before the first bullet: no greeting and no lead-in sentence. That opening list never "
                "has more than 4 bullets.")
NO_LIBRARY_RULE = '- There is no library to search: answer from what you know, and say so plainly ("From what I know:").'

RULES = """

How to answer:
- {search_rule}
- Start with 2 to 4 short bullets, each one complete action, most urgent first. If they need emergency help now, that is the first bullet. Put any "don't" or "never" inside the same bullet.
- Then explain in plain English: short numbered steps when the order matters, short paragraphs otherwise. Don't add warning sections; anything critical belongs in the first bullets.
- For a short factual question, give only the bullets.
- When the answer is a series of yes-or-no choices, add a `steps` block (format below). Never write pictures, SVG, HTML or diagram code.
{no_match_rule}
{opening_rule}
- Put safety first. Name real dangers (carbon monoxide, fire, electrical hazards, spoiled food, unsafe water, injuries) and say plainly when to call {call}.{safety_scope}
- Never shorten a safety instruction so that it loses its condition. When a source or a safety instruction says "only if", "unless" or "never", or gives a limit or an amount, keep that in the same bullet or step, even if that makes the bullet longer.
- Carbon monoxide kills people in every blackout. Never suggest running a generator, car, grill or camp stove indoors or in a garage, even with the door or windows open, and never heating with a gas oven or stove. Heaters must be made for indoor use, with a working CO alarm. Give no "if you must" exceptions to these.
{estimate_rule} For risks of dying, look up the library's micromort references and death-rate tables first. Missing data is never a reason to withhold a number. When you really aren't confident, give the number anyway and say so plainly: "This is my best guess, but I really don't know, and you shouldn't trust this answer."
{partial_rule}
- Don't add disclaimers like "I'm not a doctor" or "consult a professional"; the app already says AI can make mistakes. Do say when someone needs emergency help now.
- After {every_answer}, write "Follow-ups:" on its own line, then up to three short questions the person might ask next, one per line starting with "- ".{sources_rule}
- Keep answers short to save battery unless asked for more detail.

Format of a steps block: each question is "N? text"; each branch is "yes:" or "no:", what to do, then "-> N" or "-> end". For example:
```steps
1? Is the water cloudy or brown?
yes: Let it settle for 30 minutes and filter it through a clean cloth. -> 2
no: No need to filter it first. -> 2
2? Can you boil water?
yes: Bring it to a rolling boil for 1 minute and let it cool. -> end
no: Use bleach instead. -> 3
3? Do you have plain, unscented bleach?
yes: Add 8 drops of 6% bleach per 4 litres and wait 30 minutes. -> end
no: Don't drink it untreated. Use stored water first. -> end
```"""

TOOLS = [
    {"type": "function", "function": {
        "name": "search_library",
        "description": "Keyword search over the offline library. Returns the best-matching passages, each with an id "
                       "for read_article. Use a few specific keywords, not a full sentence.",
        "parameters": {"type": "object", "properties": {
            "query": {"type": "string", "description": "Keywords, e.g. 'generator carbon monoxide garage'"},
            "topic": {"type": "string", "enum": ["any", *TOPICS],
                      "description": "Optional: only search one kind of source"}},
            "required": ["query"]}}},
    {"type": "function", "function": {
        "name": "read_article",
        "description": f"Read about {READ_CHARS:,} characters of an article, starting at a search result's passage.",
        "parameters": {"type": "object", "properties": {
            "id": {"type": "integer", "description": "The id from search_library"}},
            "required": ["id"]}}},
    {"type": "function", "function": {
        "name": "show_article",
        "description": "Open an article on the person's screen so they can read it themselves, scrolled to the "
                       "passage with this id. Use when they ask to see the source or the full instructions.",
        "parameters": {"type": "object", "properties": {
            "id": {"type": "integer", "description": "The id from search_library or read_article"}},
            "required": ["id"]}}},
]

# Qwen drafts an answer before it searches when its reasoning is fully off, so with Reasoning off it still gets a brief
# think (held to the short limit); Qwen's highest effort goes with the long and unlimited settings.
THINK_OFF = {"qwen": "low"}
THINK_LONG = {"qwen": "xhigh"}
SAMPLING = {
    "qwen": {"temperature": 0.7, "top_p": 0.8, "top_k": 20},
    "nemotron": {"temperature": 0.7, "top_p": 0.95},
    "granite": {"temperature": 0.3, "top_p": 0.9},
    "gemma": {"temperature": 0.7, "top_p": 0.95, "top_k": 64},
}
_shown = {}  # model -> Ollama's /api/show answer


def _show(model):
    if model not in _shown:
        try:
            _shown[model] = _post("/api/show", {"model": model})
        except (OSError, ValueError):
            return {}  # not cached: Ollama may just not be running yet
    return _shown[model]


def family(model):
    """Which SAMPLING/THINK settings fit a model: from its name, else from the family Ollama reports."""
    known = next((f for f in SAMPLING if f in model.lower()), "")
    if known:
        return known
    details = _show(model).get("details") or {}
    names = " ".join([details.get("family") or ""] + (details.get("families") or [])).lower()
    return next((f for f in SAMPLING if f in names), "")


def _post(path, body, stream=False):
    resp = ollama_request(path, body, timeout=900)
    if stream:
        return resp
    with resp:
        return json.load(resp)


def capabilities(model):
    return set(_show(model).get("capabilities") or [])


def think_setting(model, caps, reasoning="off"):
    """Ollama's "think" for this model at this Reasoning setting: None for a model that can't reason, False for none."""
    if "thinking" not in caps:
        return None
    fam = family(model)
    if REASONING_LIMITS.get(reasoning, 0) == 0:
        return THINK_OFF.get(fam, False)
    if reasoning in ("long", "none"):
        return THINK_LONG.get(fam, True)
    return "low" if fam == "qwen" and reasoning == "short" else True


def system_prompt(library, tools=True, call=None, quotes=None):
    """library: a Library (its sources get listed for the model) or None. call: the number to tell people to call for
    an ambulance, with words saying whose it is; by default the current country's (emergency.py). quotes: "tools" or
    "fetch" adds the quote-marker rule of that answer mode (QUOTE_RULES); None, the default, adds nothing."""
    prompt = _system_prompt(library, tools, call)
    if library is None or quotes not in QUOTE_RULES:
        return prompt
    return prompt + QUOTE_RULES[quotes].replace("{call}", call or emergency.call_line())


def _system_prompt(library, tools=True, call=None):
    has_library = library is not None
    call = call or emergency.call_line()
    no_match = NO_MATCH_RULE.format(number=emergency.call_number())
    if has_library and tools:
        labels = ", ".join(library.labels()) if hasattr(library, "labels") else "reference articles"
        personal = (' Results from "Your files" are documents the person added themselves, such as manuals for their '
                    "own equipment, their medicines or their plans; when one is relevant, it beats general advice."
                    if getattr(library, "has_personal", False) else "")
        return BASE_PROMPT + LIBRARY_PROMPT.format(labels=labels, personal=personal) + RULES.format(
            call=call, no_match_rule=no_match, safety_scope=SAFETY_SCOPE, every_answer=EVERY_ANSWER,
            opening_rule=OPENING_RULE.format(apart="Apart from the no-match answer, n"),
            partial_rule=STRICT_PARTIAL_RULE.format(call=call), estimate_rule=LABELLED_ESTIMATE_RULE,
            search_rule="Before answering any factual, medical, safety or how-to question, search the library, even "
                        "if you think you know the answer, so the person can check where it came from. Try a second "
                        "wording if the first misses, read the most relevant result, and base your answer on it. "
                        "When sources disagree, trust Ready.gov, CDC, MedlinePlus, the UK medicines guide, WikEM and WikiMed over "
                        "wikiHow and Q&A sites.",
            sources_rule='\n- End every answer except the no-match answer with one short line, "Sources:", naming the articles you used. The app also shows the '
                         "person every article you opened, so name only articles you actually read, and never make one "
                         'up. Write "Sources: none (from my own knowledge)" only for an estimate you labelled as one, with '
                         "nothing from the library behind it.")
    if has_library:
        return BASE_PROMPT + RULES.format(
            call=call, no_match_rule=no_match, safety_scope=SAFETY_SCOPE, every_answer=EVERY_ANSWER,
            opening_rule=OPENING_RULE.format(apart="Apart from the no-match answer, n"),
            partial_rule=STRICT_PARTIAL_RULE.format(call=call), estimate_rule=LABELLED_ESTIMATE_RULE,
            search_rule="Library excerpts may be attached to the question; base your answer only on them.",
            sources_rule='\n- End every answer except the no-match answer with one short line, "Sources:", naming the excerpts you used, or "Sources: none (from my '
                         'own knowledge)" only for an estimate you labelled as one, with no excerpt behind it. The person '
                         "sees the excerpts too, so never name anything else.")
    return BASE_PROMPT + RULES.format(
        call=call, no_match_rule=NO_LIBRARY_RULE, safety_scope="", every_answer="every answer (a bullets-only answer included)",
        opening_rule=OPENING_RULE.format(apart="N"), partial_rule=NO_LIBRARY_PARTIAL_RULE,
        estimate_rule=NO_LIBRARY_ESTIMATE_RULE, search_rule="Answer from what you know; you cannot look anything up.",
        sources_rule="\n- Do not cite sources you cannot check.")


# ---------- grounded answers: answer modes and the quote check ----------
#
# The answer mode is a setting (settings.json "answers"."mode"), so the approaches can be compared in a test:
#   standard      the tool loop as it has always been, with no quote markers (chat_events' and chat.py's default)
#   tools-quoted  the same tool loop, and the model marks each statement with the passage sentence it rests on
#   fetch-quoted  the app finds the passages first (search words from one short model step), hands them to the model
#                 with the question, and the model marks each statement the same way
# In the quoted modes code checks every marker against the passages the model was given (annotate below): a statement
# whose quote is found there is "backed", any other statement is "not backed" and the page shows it greyed. No second
# model pass is involved.
ANSWER_MODES = ("standard", "tools-quoted", "fetch-quoted")
QUOTED = {"tools-quoted": "tools", "fetch-quoted": "fetch"}
_QUOTE_COMMON = (
    "\n\nMarking what the library says:\n"
    "- After each statement that a passage backs, copy the sentence from the passage that it rests on, word for word, "
    "inside double square brackets with {label}, like this: {example} The app checks every marker against the passages "
    "and hides it from the person, so they read your answer without it.\n"
    "- One marker per statement, right after it, on the same line; a line with two sentences gets a marker after each "
    "sentence a passage backs. Copy the passage's words exactly, without "
    "rewording, shortening or adding to them; leave out a marker rather than make one up.\n"
    "- A statement no passage backs gets no marker. The app shows it as not from the library.\n"
    "- Put no marker in a heading, a question in a steps block, the Follow-ups or Sources lines, or the no-match "
    "answer. In a steps block, put the marker just before the \"->\".")
# fetch-quoted: every sentence of the passages handed over carries a number ([S12]), and the model cites numbers
# ([[S12]]) instead of copying the sentence. Copied quotes roughly doubled what it wrote. Code maps each number
# back to its sentence (annotate).
FETCH_CITE_RULES = (
    "\n\nMarking what the library says:\n"
    "- Every sentence of the library passages starts with its number in square brackets, like [S12]. After each "
    "statement that a passage sentence backs, write that sentence's number inside double square brackets, like this: "
    "\"Boil the water for at least one minute. [[S12]]\" For a statement two sentences back, write [[S12]][[S14]]. "
    "The app checks every number against the passages and hides it from the person, so they read your answer "
    "without it.\n"
    "- One marker per statement, right after it, on the same line; a line with two sentences gets a marker after each "
    "sentence a passage backs. Cite only a sentence that says what your statement says, with the same numbers and "
    "conditions; leave out a marker rather than guess.\n"
    "- A statement no passage backs gets no marker. The app shows it as not from the library.\n"
    "- Put no marker in a heading, a question in a steps block, the Follow-ups or Sources lines, or the no-match "
    "answer. In a steps block, put the marker just before the \"->\".")
# fetch-quoted on emergency questions: run 4 (13 November) gave thin power line and carbon monoxide answers in a forum's
# words ("Stay the hell away") and told mild CO cases "fresh air and light duty" though a more cautious passage was there.
FETCH_EMERGENCY_RULES = (
    "\n\nWhen someone may be in danger now (a downed power line, carbon monoxide, heavy bleeding, fire, heat stroke, "
    "poisoning, someone collapsed or not breathing):\n"
    "- Give every step the passages give for that danger, in order, not just the first few: what to do now, what never "
    "to do, distances, times, amounts, when to call, and what to do in each separate situation the passages describe. "
    "Keep each step's condition (\"unless\", \"only if\", \"never\").\n"
    "- Where passages differ, follow the most cautious one first, and prefer official sources (Ready.gov, CDC, "
    "MedlinePlus, WHO, IFRC, Red Cross, NHS) over forums and Q&A sites. Never say a case is mild or needs help only "
    "when severe if another passage is more cautious.\n"
    "- Write each step as a short, calm, plain instruction in your own words. Never copy slang, swearing or a forum's "
    "tone.\n"
    "- Use only what the passages say; when they leave a step out, don't add it.")
# fetch-quoted answers, 14 November runs 1 and 2: judges marked clarity down for steps that repeat the bullets, hospital
# treatment and long tables of figures (a carbon monoxide answer listed every ppm level). Run 4: a water answer listed
# three bleach amounts from three passages side by side, one of them meant for cleaning surfaces.
FETCH_SHAPE_RULES = (
    "\n\nKeeping it usable:\n"
    "- The steps after the bullets add to them; never repeat a bullet there.\n"
    "- Leave out what the person can't act on now (hospital treatment, background, long tables or lists of figures) "
    "unless they asked for it.\n"
    "- An amount (a dose, a number of drops, a time, a distance) comes only from a passage about the same use as the "
    "question. When passages give different amounts for it, give the one from the most official source, plainly; "
    "never list different amounts side by side for the person to choose from.")
# fetch-quoted, sl-proof 14 November: a "not breathing" answer followed a library article's choking steps though the
# checked CPR guide was handed too, and a wet-wood fire answer passed on an old book's "start your fire in your tent".
# sl-fixes run 9: the CPR answer kept a MedlinePlus line's "call 911". {call} is the app's own number (emergency.py).
FETCH_SOURCE_RULES = (
    "\n\nWhich passages to follow:\n"
    "- When a passage from the Starholm guides covers the person's situation, follow its steps in its order. Use "
    "other passages only to add what it leaves out, never to change or reorder its steps, and leave out their own "
    "procedure for the same situation when it differs from the guide's.\n"
    "- When a passage names another country's emergency number, write {call} in its place.\n"
    "- Leave out any tip that a passage or guide warns against, and any tip that burns fuel (a fire, stove, heater, "
    "grill or engine) in a tent, vehicle or closed room. This rule only takes things out: drop such a tip silently, "
    "and never add a step or warning because of it.")
QUOTE_RULES = {
    "tools": _QUOTE_COMMON.format(
        label="the article's id from read_article, a colon and the quote",
        example="\"Boil the water for at least one minute. [[1403312: Bring water to a rolling boil for at least one "
                "minute.]]\""),
    "fetch": FETCH_CITE_RULES + FETCH_EMERGENCY_RULES + FETCH_SOURCE_RULES + FETCH_SHAPE_RULES,
}

# The step that writes search words (fetch-quoted): no reasoning, a short reply, the same num_ctx as the answer.
SEARCH_WORDS_PROMPT = ("You write search words for an offline library of articles about first aid, emergencies, "
                       "repairs and everyday know-how. Reply with 1 to 3 lines, each a different set of 2 to 6 "
                       "keywords for the person's question, most useful first. Write keywords only: no sentences, no "
                       "numbering, no quotes, nothing else.")
SEARCH_WORDS_TOKENS = 48
MAX_QUERIES = 3
FETCH_HITS = 8          # search results per set of search words (the panel shows them all)
FETCH_PASSAGES = 6      # passages handed to the model, one per article at most
FETCH_ONE = 3000        # characters of one passage, at most (READ_CHARS)
FETCH_CHARS = 12000     # characters of all passages together: with the prompt and a long chat, well inside NUM_CTX
# The amounts rule is said again here, after the passages: in the 14 November final run Gemma ignored it in the system
# prompt and gave a surface-cleaning bleach ratio next to the drinking-water doses. The leave-out rule is too: with it in
# the system prompt alone, a wet-wood answer still said "start your fire in your tent" (sl-fixes run 1).
FETCH_NOTE = ("Library passages for this question. Use only these; cite their sentence numbers as the instructions "
              "say. Take each amount (a dose, a number of drops, a time, a distance) only from a passage about the same "
              "use as the question, and give one amount for each thing, from the most official source. Never work an amount "
              "out yourself (no doubling, halving or converting): write it as the passage gives it. Leave out any tip "
              "that a passage or guide warns against, and any tip that burns fuel (a fire, stove, heater, grill or "
              "engine) in a tent, vehicle or closed room, even when a passage gives it. Drop such a tip silently: "
              "write no warning or \"never\" line about it.")

# The interface's marks for a checked statement, put into the answer where it starts and ends: private-use
# characters, which no model output keeps (annotate takes any out first) and no Markdown pattern reads.
# START + the claim's index + MID, then the statement, then END.
CLAIM_START, CLAIM_MID, CLAIM_END = "", "", ""
CLAIM_MARKS = re.compile("\\d{0,4}|[-]")
# A quote marker as the model writes it: [[P2: words]] or [[1403312: words]] (a bare [[P2]] counts, with no quote),
# or, in fetch-quoted, sentence numbers: [[S12]], [[S12, S14]].
MARKER_HEAD = re.compile(r"\[\[[ \t]*([A-Za-z]{0,2}[ \t]?\d{1,12}(?:[ \t]*[,;][ \t]*[A-Za-z]{0,2}[ \t]?\d{1,12})*)[ \t]*"
                         r"(?:[:：][ \t]*|(?=\]\]))")
SENTENCE_CITE = re.compile(r"S\d{1,4}(?:[,;]S\d{1,4})*")  # a marker's label (spaces out, upper case) citing sentences
# Sentence numbers as the model also writes them: "[S12]" copied as the passages show it, "[[S5], [S67]]",
# "[[S8],,, [S129]]", "[S3] [S4]". annotate turns each run into one marker, [[S5,S67]].
# A sentence number with words after it ("[[S19 (implicit from safety rules)]]") is the model saying the sentence
# doesn't quite back it: it becomes NOTED_MARKER, which still ends the statement but backs nothing.
CITE_NOTE = re.compile(r"\[\[[ \t]*S\d{1,4}[ \t]*[(\-–—][^\[\]\n:]{0,80}\]{1,2}")
NOTED_LABEL = "ZZ0"
NOTED_MARKER = f"[[{NOTED_LABEL}]]"
# A range ("[[S3-S5]]"): every sentence in it, each of which must have been handed over.
CITE_RANGE = re.compile(r"\[\[[ \t]*S(\d{1,4})[ \t]*[-–—][ \t]*S?(\d{1,4})[ \t]*\]\]")
MAX_RANGE = 20
# A marker with no sentence number or label ("[[Safety Rule]]"): taken out of a fetch-quoted answer, backing nothing.
FREE_MARKER = re.compile(r"[ \t]*\[\[[^\[\]\n]{1,80}\]\]")
CITE_GROUP = re.compile(r"\[{1,2}[ \t]*S\d{1,4}(?:[ \t]*\]{0,2}[ \t]*[,;]+[ \t]*\[{0,2}[ \t]*S\d{1,4}"
                        r"|[ \t]*\]{1,2}[ \t]*\[{1,2}[ \t]*S\d{1,4})*[ \t]*\]{1,2}")
MAX_QUOTE = 1000        # characters; a longer "quote" is not one
# Every piece of a quote (a quote with "..." is several) must carry at least MIN_QUOTE_WORDS words and MIN_QUOTE_CHARS
# characters to back anything: three common words ("for one minute") turn up in almost any passage, while six words in
# a row, copied exactly, rarely appear anywhere but where they were copied from. A statement whose source sentence is
# shorter shows as not from the library: the check errs towards greying.
MIN_QUOTE_WORDS = 6
MIN_QUOTE_CHARS = 30
MAX_CLAIM_TEXT = 400    # characters of a statement kept in the claims list
# A model sentence left alone only when it is nothing but an instruction to call emergency help: "Call 998 now.",
# "Dial 112 or 999 immediately.", "Call emergency services." A number counts only when it is one the emergency module
# holds on the current country's card (held_numbers); "Call 911 now." in the UAE is checked like any statement.
# Anything joined to it, by a comma, semicolon, dash, "then" or "if", makes it an ordinary statement too.
# The no-match answer is not recognised by its look either: the page draws its own lines in its place (annotate is
# skipped for it in chat_events, and done carries no_match: true).
CALL_STATEMENT = re.compile(r"^[*_ \t]*(?:call|dial|phone|ring)[ \t]+(?:(?P<nums>\d(?:[\d ]{0,6}\d)?(?:[ \t]+or[ \t]+"
                            r"\d(?:[\d ]{0,6}\d)?|[ \t]*/[ \t]*\d(?:[\d ]{0,6}\d)?)*)|(?:(?:the|your)[ \t]+)?(?:local[ \t]+)?"
                            r"emergency[ \t]+(?:number|services)|an[ \t]+ambulance)"
                            r"(?:[ \t]+(?:now|right away|immediately|at once))?[ \t]*[.!]?[*_ \t]*$", re.I)


def held_numbers():
    """The emergency numbers on the current country's card, as digit strings without spaces ("10 111" is "10111");
    none without a card."""
    try:
        current = emergency.view().get("current") or {}
    except Exception:  # noqa: BLE001 - no numbers known: no call line is left alone
        return set()
    out = set()
    values = [n.get("number") for n in current.get("numbers") or [] if isinstance(n, dict)]
    for value in values + [current.get("ambulance"), current.get("police")]:
        out |= {run.replace(" ", "") for run in re.findall(r"\d[\d ]*\d|\d", value or "")}
    return out


def emergency_call_target():
    """Who the no-match answer's call line names, as emergency.py decides it (and the page's callTarget): {"numbers":
    [to call], "text": "your local emergency number" when there is no card}. The page draws the call line from this,
    never from the model's words."""
    none = {"numbers": [], "text": emergency.LOCAL}
    try:
        current = emergency.view().get("current")
    except Exception:  # noqa: BLE001 - nothing known: the words, not a number
        return none
    numbers = [r.strip() for r in re.findall(r"\d[\d ]*\d|\d", (current or {}).get("ambulance") or "")]
    return {"numbers": numbers, "text": None} if numbers else none


def call_only(sentence, numbers):
    """Whether a sentence is nothing but an instruction to call emergency help, with only held numbers in it."""
    found = CALL_STATEMENT.search(sentence)
    if not found:
        return False
    nums = found.group("nums")
    return not nums or all(n.replace(" ", "") in numbers for n in re.split(r"[ \t]*(?:\bor\b|/)[ \t]*", nums) if n.strip())
_FENCE = re.compile(r"^ {0,3}(`{3,}|~{3,})[ \t]*([\w+#-]*)")
_HEADING = re.compile(r"^ {0,3}#{1,6}(?:[ \t]|$)")
_RULE = re.compile(r"^ {0,3}([-*_])(?:[ \t]*\1){2,}[ \t]*$")
_TABLE_SEP = re.compile(r"^\s*\|?\s*:?-+:?\s*(?:\|\s*:?-+:?\s*)*\|?\s*$")
_LINE_PREFIX = re.compile(r"[ \t]*(?:>[ \t]?)*[ \t]*(?:(?:[-*+•]|\d{1,9}[.)])[ \t]+(?:\[[ xX]\][ \t]+)?)?")
_STEP_QUESTION = re.compile(r"^\s*\d{1,3}\s*\?")
_STEP_BRANCH = re.compile(r"^\s*(?:yes|no)\s*:\s*", re.I)
_SENTENCE_BREAK = re.compile(r"[.!?][\"'”’)\]*_]*([ \t]+)(?=[*_\"'“‘(]*[A-Z])")  # "**Stay away.** A line..." is two
_LEADING_PUNCT = re.compile(r"^[ \t]*[.,;:!?)\]\"'’”…]+")
_QUOTE_CHARS = str.maketrans({"‘": "'", "’": "'", "‚": "'", "‛": "'", "“": '"', "”": '"', "„": '"', "‟": '"',
                              "‐": "-", "‑": "-", "‒": "-", "–": "-", "—": "-", "―": "-", "−": "-"})


def _sentences(text):
    """text cut into sentences; the space between two goes with the second."""
    out, pos = [], 0
    for m in _SENTENCE_BREAK.finditer(text):
        out.append(text[pos:m.start(1)])
        pos = m.start(1)
    return out + [text[pos:]]


def normalise_quote(text):
    """Text as the quote check compares it: compatibility forms folded (so "…" is "..."), lower case, plain quotes
    and dashes, Markdown emphasis, heading and table marks taken out, whitespace collapsed."""
    text = unicodedata.normalize("NFKC", str(text)).casefold().translate(_QUOTE_CHARS)
    return " ".join(re.sub(r"[*_`#>|]+", " ", text).split())


def quote_parts(quote):
    """The pieces of a quote to look for, in order: a quote with "..." in it is several pieces of one passage."""
    text = normalise_quote(quote).strip(" \"'")
    return [p for p in (part.strip(" .,;:!?\"'") for part in re.split(r"\.{3,}|\[\s*\]", text)) if p]


def quote_long_enough(quote):
    """Whether a quote can back a statement: at least one piece, and every piece at least MIN_QUOTE_WORDS words and
    MIN_QUOTE_CHARS characters (a quote of nothing but "..." has no pieces, and backs nothing)."""
    parts = quote_parts(quote)
    return bool(parts) and all(len(p.split()) >= MIN_QUOTE_WORDS and len(p) >= MIN_QUOTE_CHARS for p in parts)


def quote_found(quote, passage_norm):
    """Whether every piece of quote appears, in order, in one passage (already normalised); never for a quote that
    isn't long enough to back anything."""
    if not quote_long_enough(quote):
        return False
    at = 0
    for part in quote_parts(quote):
        at = passage_norm.find(part, at)
        if at < 0:
            return False
        at += len(part)
    return True


def find_markers(line):
    """The quote markers in one line: [(start, end, label, quote, status)], status "ok", "short" (no quote, or a piece
    too short to back anything), "long" (over MAX_QUOTE) or "unclosed" (no "]]" on the line: only its opening
    "[[P2:" counts as the marker, and the text after it stays as written). Scanned with find, so a hostile line costs
    linear time."""
    out, pos = [], 0
    while True:
        i = line.find("[[", pos)
        if i < 0:
            return out
        head = MARKER_HEAD.match(line, i)
        if not head:
            pos = i + 2
            continue
        label = re.sub(r"[ \t]", "", head.group(1)).upper()
        j = line.find("]]", head.end())
        if j < 0:
            out.append((i, head.end(), label, "", "unclosed"))
            return out
        quote = line[head.end():j]
        cites = bool(SENTENCE_CITE.fullmatch(label)) and not quote.strip()  # [[S12]]: numbers, no quote
        status = "long" if len(quote) > MAX_QUOTE else "ok" if cites or quote_long_enough(quote) else "short"
        out.append((i, j + 2, label, quote, status))
        pos = j + 2


def strip_quote_markers(text):
    """text without any quote marker (of an unclosed one, only its opening "[[P2:")."""
    lines = []
    for line in str(text).split("\n"):
        for start, end, *_ in reversed(find_markers(line)):
            line = line[:start].rstrip(" \t") + line[end:] if end == len(line) else line[:start] + line[end:]
        lines.append(line)
    return "\n".join(lines)


def strip_claim_marks(text):
    """An annotated answer as plain text: the statement marks taken out."""
    return CLAIM_MARKS.sub("", text or "")


def passage_sentences(passages):
    """fetch-quoted: the passages' texts with every sentence numbered for the model ("[S12] Boil it."), and what each
    number stands for: ([text per passage], {"S12": (passage, sentence)}). Numbers run on across the passages in the
    order given, so the same passages always get the same numbers; a line with no letters or digits gets none."""
    texts, numbered = [], {}
    for p in passages or []:
        lines = []
        for line in (p.get("text") or "").split("\n"):
            head = re.match(r"[ \t]*(?:#{1,6}[ \t]+|(?:[-*+•]|\d{1,9}[.)])[ \t]+)?", line).group()  # "- ", "## ": first
            out, line = [head], line[len(head):]
            for piece in (_sentences(line) if re.search(r"\w", line) else [line]):
                if not re.search(r"\w", piece):
                    out.append(piece)
                    continue
                label = f"S{len(numbered) + 1}"
                numbered[label] = (p, piece.strip())
                out.append(f"{piece[:len(piece) - len(piece.lstrip())]}[{label}] {piece.strip()}")
            lines.append("".join(out))
        texts.append("\n".join(lines))
    return texts, numbered


NUMBER = re.compile(r"\d+(?:[.,]\d+)*")
NUMBER_WORDS = {w: str(n) for n, w in enumerate(
    "zero one two three four five six seven eight nine ten eleven twelve thirteen fourteen fifteen sixteen seventeen "
    "eighteen nineteen twenty".split())} | {"thirty": "30", "forty": "40", "fifty": "50", "sixty": "60", "ninety": "90"}


def _numbers(text, skip=(), words=False):
    """The numbers written in digits in text ("1,000" is "1000"), less those in skip (the emergency numbers); with
    words, also those written as a word ("six" is "6")."""
    found = {n.replace(",", "") for n in NUMBER.findall(text or "")}
    if words:
        found |= {NUMBER_WORDS[w] for w in re.findall(r"[a-z]+", (text or "").lower()) if w in NUMBER_WORDS}
    return found - set(skip)


def cite_backs(statement, sentence, numbers=()):
    """Whether a cited sentence can back a statement, as far as code can tell: every number the statement writes in
    digits is in the sentence (an emergency number aside), and the two share a topic word (topic_keys). It shows the
    sentence fits, not that it says the same thing. (backed, why)."""
    if not _numbers(strip_quote_markers(statement), numbers) <= _numbers(sentence, words=True):
        return False, "a number not in the cited sentence"
    said, cited = normalise_quote(strip_quote_markers(statement)), normalise_quote(sentence)
    if not on_topic(cited, topic_keys(said)):
        return False, "cited sentence shares no word with it"
    # A condition never comes from nowhere: "unless", "only" and "except" in the statement must be in the sentence too,
    # and a statement that forbids something needs a sentence that does.
    if any(re.search(rf"\b{w}\b", said) and not re.search(rf"\b{w}\b", cited) for w in ("unless", "only", "except")):
        return False, "a condition not in the cited sentence"
    if NEGATION.search(said) and not NEGATION.search(cited):
        return False, "a never or not that the cited sentence lacks"
    return True, "ok"


NEGATION = re.compile(r"\b(?:never|not|no|nor|none|nothing|without|avoid|cannot)\b|n't\b|\bdo ?n.?t\b")


class _Check:
    """One answer's quote check: the passages it was given and the claims found so far."""

    def __init__(self, passages, numbers=(), drop_calls=False, numbered=False):
        self.numbers = set(numbers)  # emergency numbers a call-only sentence may name (held_numbers)
        self.drop_calls = drop_calls  # the app adds its own call line: a call-only sentence of the model's goes
        # passages: [{"label", "id", "title", "source", "text"}]: what the model was given, by the label it quotes
        self.passages = [{**p, "norm": normalise_quote(p.get("text") or "")} for p in passages]
        # fetch-quoted only: "S12" -> (passage, sentence); in tools-quoted a sentence number backs nothing
        self.cited = passage_sentences(passages)[1] if numbered else {}
        self.claims, self.markers, self.ok = [], 0, 0

    def verify(self, markers, statement=""):
        """(backed, source, quote, why, sources) for a statement from its markers (sources: every passage it rests on,
        source the first): backed when one marker's quote is found in
        a passage it was given, the labelled one first, or when the sentences its numbered markers cite ([[S12]]) were
        all handed over and together pass cite_backs; quote is then those sentences."""
        why, quote = "no marker", ""
        labels = [k for m in markers if m[4] == "ok" and SENTENCE_CITE.fullmatch(m[2]) for k in re.split("[,;]", m[2])]
        if labels:
            if not all(k in self.cited for k in labels):
                return False, None, " ".join(labels), "cited sentence not handed over", []
            hits = [self.cited[k] for k in dict.fromkeys(labels)]
            sentence = " ".join(text for _p, text in hits)
            backed, why = cite_backs(statement, sentence, self.numbers)
            sources = list({p.get("id"): {k: p.get(k) for k in ("id", "title", "source", "official")}
                            for p, _text in hits}.values()) if backed else []
            return backed, (sources[0] if sources else None), sentence[:300], why, sources
        for _start, _end, label, text, status in markers:
            if label == NOTED_LABEL:
                why = "the marker says it is not quite what the sentence says"
                continue
            quote = quote or text.strip()[:300]
            if status != "ok":
                why = {"short": "quote too short", "long": "quote too long", "unclosed": "marker not closed"}[status]
                continue
            tries = sorted(self.passages, key=lambda p: str(p.get("label", "")).upper() != label)
            hit = next((p for p in tries if quote_found(text, p["norm"])), None)
            if hit:
                source = {k: hit.get(k) for k in ("id", "title", "source", "official")}
                return True, source, text.strip()[:300], "ok", [source]
            why = "quote not in the passages it was given"
        return False, None, quote, why, []

    def statement(self, text, markers):
        """A statement and the markers after it: its claim's index, or None when it is left alone (no letters or
        digits, or a line telling the person to call for help)."""
        core = text.strip()
        if not re.search(r"\w", core) or call_only(core, self.numbers):
            return None
        backed, source, quote, why, sources = self.verify(markers, core)
        self.claims.append({"n": len(self.claims), "text": strip_quote_markers(core)[:MAX_CLAIM_TEXT],
                            "backed": backed, "source": source, "sources": sources, "quote": quote, "why": why})
        return len(self.claims) - 1

    def line(self, content):
        """One line's content (after its list or quote prefix), its markers taken out and each statement wrapped in
        claim marks."""
        found = find_markers(content)
        self.markers += len(found)
        self.ok += sum(m[4] == "ok" for m in found)
        units, pos = [], 0  # [text, [markers]]: a stretch of text and the markers right after it
        for m in found:
            text = content[pos:m[0]]
            if units and units[-1][1] and not text.strip():
                units[-1][1].append(m)  # two markers in a row back the same statement
            else:
                units.append([text, [m]])
            pos = m[1]
        units.append([content[pos:], []])
        out = []  # strings and statements: {"lead", "core", "trail", "claim"}
        last = None  # the last statement on this line, for punctuation written after its marker
        for text, markers in units:
            lead = _LEADING_PUNCT.match(text)
            if lead and last is not None:
                last["core"] += lead.group().strip()
                last["trail"] = ""
                if last["claim"] is not None:
                    self.claims[last["claim"]]["text"] = strip_quote_markers(last["core"])[:MAX_CLAIM_TEXT]
                text = text[lead.end():]
            if last is not None and last["trail"] and text[:1].isspace():
                last["trail"] = ""  # the marker between them went: one space, not two
            pieces = _sentences(text)
            for k, piece in enumerate(pieces):
                if not piece:
                    continue
                core = piece.strip()
                if not core:
                    out.append(piece)
                    continue
                left = piece[:len(piece) - len(piece.lstrip())]
                right = piece[len(piece.rstrip()):]
                mine = markers if k == len(pieces) - 1 else []
                if self.drop_calls and call_only(core, self.numbers):
                    continue  # the app's own call line says this once, under the answer
                last = {"lead": left, "core": core, "trail": right, "claim": self.statement(core, mine)}
                out.append(last)
        text = "".join(p if isinstance(p, str) else
                       p["lead"] + (p["core"] if p["claim"] is None
                                    else f"{CLAIM_START}{p['claim']}{CLAIM_MID}{p['core']}{CLAIM_END}") + p["trail"]
                       for p in out)
        return text.rstrip() if found else text  # no space left where a marker ended the line


def _range(m):
    lo, hi = int(m.group(1)), int(m.group(2))
    return "[[" + ",".join(f"S{n}" for n in range(lo, hi + 1)) + "]]" if lo < hi <= lo + MAX_RANGE else NOTED_MARKER


def _sentence_markers(text):
    """fetch-quoted: sentence numbers in every shape the model writes them, as markers annotate checks: a range spelled
    out, a number with the model's own note as NOTED_MARKER, a run of numbers as one marker, and a double-bracketed
    marker with no number taken out."""
    text = CITE_RANGE.sub(_range, text)
    text = CITE_NOTE.sub(NOTED_MARKER, text)
    text = CITE_GROUP.sub(lambda m: "[[" + ",".join(re.findall(r"S\d+", m.group(0))) + "]]", text)
    return FREE_MARKER.sub(lambda m: m.group(0) if MARKER_HEAD.match(m.group(0).lstrip(" \t")) else "", text)


def _outside_code(text, fix):
    """text with fix applied everywhere but fenced code blocks and `inline code` (a shell's [[ -f x ]] stays)."""
    out, fence = [], None
    for line in text.split("\n"):
        opened = _FENCE.match(line)
        if fence:
            fence = None if line.strip().startswith(fence) else fence
            out.append(line)
        elif opened:
            fence = opened.group(1)
            out.append(line)
        else:
            out.append("".join(part if k % 2 else fix(part) for k, part in enumerate(re.split(r"(`[^`\n]*`)", line))))
    return "\n".join(out)


def annotate(answer, passages, mode=None, numbers=(), drop_calls=False):
    """Check an answer's quote markers against the passages the model was given. Returns (marked, claims, grounding):
    marked is the answer with the markers taken out and each statement between CLAIM_START n CLAIM_MID and
    CLAIM_END; claims[n] is {"n", "text", "backed", "source", "sources", "quote", "why"}; grounding is {"mode", "followed",
    "markers", "malformed", "statements", "backed"}. "followed" is false when the answer has statements but not one
    marker: the model ignored the format, and every statement counts as not backed.
    Left alone (no marks, not counted): headings, code, table header rows, steps questions, a sentence that is only an
    instruction to call emergency help (call_only), and everything from a Follow-ups line on. A Sources line only
    loses its markers. drop_calls (the app adds its own call line under the answer): such a sentence is taken out, and
    a line left empty by that goes too."""
    check = _Check(passages, numbers, drop_calls, numbered=mode == "fetch-quoted")
    out, fence, stop = [], None, False
    answer = CLAIM_MARKS.sub("", answer or "")
    if check.cited:  # sentence numbers in any of the shapes the model writes them: one marker each run
        answer = _outside_code(answer, _sentence_markers)
    lines = answer.split("\n")
    for i, line in enumerate(lines):
        nxt = lines[i + 1] if i + 1 < len(lines) else ""
        if stop:
            out.append(strip_quote_markers(line))
            continue
        if fence:
            if line.strip().startswith(fence[0]):
                fence = None
                out.append(line)
            elif fence[1] == "steps" and _STEP_BRANCH.match(line) and not _STEP_QUESTION.match(line):
                head = _STEP_BRANCH.match(line).end()
                arrow = max(line.rfind("->"), line.rfind("→"))
                arrow = arrow if arrow > head else len(line)
                out.append(line[:head] + check.line(line[head:arrow]).rstrip()
                           + (" " + line[arrow:].lstrip() if arrow < len(line) else ""))
            else:
                out.append(strip_quote_markers(line))
            continue
        opened = _FENCE.match(line)
        if opened:
            fence = (opened.group(1), opened.group(2).lower())
            out.append(line)
            continue
        if FOLLOW_UPS.match(line):
            stop = True
            out.append(strip_quote_markers(line))
            continue
        bare = strip_quote_markers(line)
        if (not bare.strip() or SOURCES_LINE.match(bare) or _HEADING.match(bare) or _RULE.match(bare)
                or _TABLE_SEP.match(bare)
                or ("|" in bare and _TABLE_SEP.match(strip_quote_markers(nxt)) and "-" in nxt)):
            out.append(bare)
            continue
        if line.lstrip().startswith("|"):  # a table row: the cells between its first and last pipe
            first = line.index("|") + 1
            last = line.rstrip().rfind("|")
            last = last if last >= first else len(line)
            out.append(line[:first] + check.line(line[first:last]) + line[last:])
            continue
        prefix = _LINE_PREFIX.match(line).group()
        body = check.line(line[len(prefix):])
        if drop_calls and not body.strip():
            continue  # the line held nothing but a call instruction, now the app's own line under the answer
        out.append(prefix + body)
    claims = check.claims
    grounding = {"mode": mode, "followed": check.ok > 0 or not claims, "markers": check.markers,
                 "malformed": check.markers - check.ok, "statements": len(claims),
                 "backed": sum(c["backed"] for c in claims)}
    return "\n".join(out), claims, grounding


def parse_search_words(text, question):
    """The search words the short step wrote: up to MAX_QUERIES lines of keywords, each at most 8 words. The question
    itself when it wrote nothing usable."""
    queries = []
    for line in strip_reasoning(text or "").split("\n"):
        words = re.findall(r"[\w][\w'-]*", re.sub(r"^\s*(?:[-*•]|\d{1,2}[.)])\s*", "", line))[:8]
        query = " ".join(words)[:200]
        if words and query.lower() not in (q.lower() for q in queries):
            queries.append(query)
        if len(queries) == MAX_QUERIES:
            break
    return queries or [question[:MAX_QUERY_CHARS]]


MAX_QUERY_CHARS = 500


def search_words(model, caps, question, earlier, options):
    """One short model step that writes only search words (no reasoning, no tools, a small output limit). Yields
    ticks while it runs; returns the queries."""
    ask = "".join(f"Earlier question in this chat: {q[:300]}\n" for q in earlier[-2:]) + f"Question: {question[:2000]}"
    think = False if "thinking" in caps else None
    reply = []
    stream = _stream(model, [{"role": "system", "content": SEARCH_WORDS_PROMPT}, {"role": "user", "content": ask}],
                     None, think, {**options, "num_predict": SEARCH_WORDS_TOKENS, "temperature": 0.2})
    try:
        for chunk in stream:
            reply.append(chunk.get("message", {}).get("content") or "")
            yield {"type": "tick"}
    finally:
        if hasattr(stream, "close"):
            stream.close()
    return parse_search_words("".join(reply), question)


FETCH_LONG = 2  # the first hits picked get the passages after their own too, up to FETCH_ONE characters


def topic_keys(*texts):
    """The words a passage must share with the question (or the first search words) to be handed over:
    (prefixes, words). Words of four letters or more count by their first five letters ("burns" for "burning");
    three-letter words and short all-capital ones (CPR, AED, ORS, CO) count as whole words. Stopwords don't count."""
    prefixes, words = set(), set()
    for text in texts:
        for token in re.findall(r"[\w']+", text or ""):
            low = token.lower()
            if low in STOPWORDS or len(low) < 2:
                continue
            if len(low) >= 4 and not (token.isupper() and len(token) <= 4):
                prefixes.add(low[:5])
            elif len(low) == 3 or token.isupper():
                words.add(low)
    return prefixes, words


def on_topic(text, keys):
    """Whether lower-cased text shares a key with the question (topic_keys); always, when there are no keys."""
    prefixes, words = keys
    if not prefixes and not words:
        return True
    return any(p in text for p in prefixes) or any(re.search(rf"\b{re.escape(w)}\b", text) for w in words)


# A question the code treats as an emergency: when the app finds nothing for it (fetch-quoted), the answer still ends
# with the app's own call line, drawn from the stored numbers (done.emergency_call, or no_match_call on a no-match
# answer), whatever the model wrote.
EMERGENCY_QUESTION = re.compile(
    r"\b(?:cpr|aed|not breathing|stopped breathing|can'?t breathe|cannot breathe|choking|unconscious|unresponsive|"
    r"collapsed?|passed out|bleeding|blood is (?:spurting|pouring)|heart attack|chest pain|stroke|seizure|overdose|"
    r"poison(?:ed|ing)?|swallowed|anaphyla\w*|allergic reaction|drown\w*|electrocut\w*|severe burn|suicid\w*|"
    r"carbon monoxide|no pulse|turning blue)\b", re.I)


def is_emergency(question):
    return bool(EMERGENCY_QUESTION.search(question or ""))


# A question about making water safe to drink (bleach, iodine, tablets, boiling). The same chemical has stronger
# doses for other uses, and the library mixes them: on 14 November 2026 a bleach answer gave a surface-cleaning ratio
# (½ teaspoon a cup) from a passage that lists cleaning dilutions next to the drinking dose. On such a question a
# passage or guide section that gives a cleaning use (surfaces, laundry, cleaning dilutions such as 1:50) is not
# handed; the water guide and the drinking-water passages are. A question that itself asks about cleaning is not one.
DRINKING_WATER_QUESTION = re.compile(r"\bwater\b", re.I)
WATER_TREATMENT = re.compile(r"\b(?:drink\w*|potable|purif\w*|disinfect\w*|treat\w*|safe|bleach|chlorin\w*|iodine|"
                             r"tablets?|boil\w*|contaminat\w*)\b", re.I)
CLEANING_USE = re.compile(
    r"\b(?:(?:clean|disinfect|sanitiz|sanitis|wip)\w*\s+(?:[\w']+[\s-]+){0,4}?(?:surfaces?(?![\s-]+water)|countertops?|"
    r"floors?|toys|laundry|clothes|clothing|bedding)\b|surfaces?\s+(?:contaminated|or objects?)\b|"
    r"(?:household|general)\s+cleaning\b|for\s+cleaning\b|laundry\b|1\s*:\s*(?:10|50|100)\b|"
    r"(?:rinse|sanitiz|sanitis|disinfect)\w*\s+(?:them|(?:the\s+|your\s+)?(?:containers?|bottles?|jugs?|tanks?))\s+"
    r"(?:with|using)\b)", re.I)
# A question about cleaning: cleaning, washing, laundry, surfaces, mopping, or disinfecting (sanitizing, rinsing)
# anything but the water itself ("disinfect the floors", "to disinfect after a flood", "disinfect toys"). Disinfecting
# water or "it", or the verb at the end of the question, stays a drinking-water question.
CLEANING_QUESTION = re.compile(
    r"\b(?:clean\w*|wash\w*|laundry|surfaces?|mop\w*)\b|"
    r"\b(?:disinfect(?:ing|s)?|saniti[sz](?:e|es|ing)|rins(?:e|es|ing))\b(?!\s*(?:[?.!,;]|$))"
    r"(?!\s+(?:(?:the|my|your|our|this|that|some|a|an|its|all)\s+)?(?:(?:drinking|tap|well|rain|stream|river|lake|"
    r"pond|creek|spring|clear|cloudy|murky|dirty|contaminated|stored|bottled|untreated|flood|this|that)\s+)?water\b)"
    r"(?!\s+(?:it|them)\b)", re.I)
# A question about storing water: the water guide's storage section (which rinses containers with bleach) is its answer.
STORAGE_QUESTION = re.compile(r"\bstor(?:e|es|ing|age)\b", re.I)


def is_drinking_water_question(question):
    q = question or ""
    return bool(DRINKING_WATER_QUESTION.search(q) and WATER_TREATMENT.search(q) and not CLEANING_QUESTION.search(q)
                and not STORAGE_QUESTION.search(q))


def for_another_use(question, text):
    """Whether a passage gives a cleaning use of what a drinking-water question asks about (see CLEANING_USE)."""
    return is_drinking_water_question(question) and bool(CLEANING_USE.search(text or ""))


def fetch_passages(library, queries, question=""):
    """Search the library with each set of words and pick the passages to hand the model: whole passages from the
    top hits, taken in turn from each search, one per article, at most FETCH_PASSAGES and FETCH_CHARS in all. A hit
    whose title and passage share no word with the question or the first search words is left out (a third search
    finds taffy recipes for "salt sugar water"). The first FETCH_LONG picked come with the passages that follow
    theirs, up to FETCH_ONE characters, as read_article gives them in the tool loop: one article's best-matching
    passage is often not the one with the answer. Yields a search event per search and a read event per passage
    picked; returns [{"label", "id", "title", "source", "official", "text"}]."""
    keys = topic_keys(question, queries[0] if queries else "")
    lead = guide_lead(question, queries)
    lists = []
    for query in with_question(queries, question):
        hits = library.search(query, limit=FETCH_HITS)
        yield {"type": "search", "query": query, "topic": "any",
               "results": [{k: h[k] for k in ("id", "title", "source", "official")} for h in hits]}
        lists.append(hits)
    order, ids, articles = [], set(), set()
    for rank in range(FETCH_HITS):
        for hits in lists:
            if rank < len(hits) and hits[rank]["id"] not in ids and (hits[rank]["title"], hits[rank]["source"]) not in articles:
                ids.add(hits[rank]["id"])
                articles.add((hits[rank]["title"], hits[rank]["source"]))
                order.append(hits[rank])
    picked, total = [], 0
    for p in lead:
        if for_another_use(question, p["text"]):
            continue
        picked.append({"label": "", **p})
        total += len(p["text"])
    for hit in order:
        doc = library.read(hit["id"], max_chars=1)  # max_chars=1: just the passage itself, whole
        low = f"{(doc or {}).get('title', '')} {(doc or {}).get('text', '')}".lower()
        if not doc or not on_topic(low, keys):
            continue
        if len(picked) < FETCH_LONG:
            longer = library.read(hit["id"], max_chars=FETCH_ONE) or doc
            # the passages after it give a cleaning use (for_another_use): just the passage itself
            doc = doc if for_another_use(question, longer.get("text", "")[:FETCH_ONE]) else longer
        text = (doc or {}).get("text", "")[:FETCH_ONE].strip()
        if not text or for_another_use(question, f"{doc['title']} {text}") or (picked and total + len(text) > FETCH_CHARS):
            continue
        total += len(text)
        picked.append({"label": f"P{len(picked) + 1}", "id": hit["id"], "title": doc["title"], "source": doc["source"],
                       "official": doc["official"], "text": text})
        if len(picked) == FETCH_PASSAGES:
            break
    picked = safety_order(picked, question, queries)
    for p in picked:  # in the order handed over, so the sources are numbered as the passages are labelled
        yield {"type": "read", **{k: p[k] for k in ("id", "title", "source", "official")}}
    return picked


# ---------- the guides in fetch-quoted: searched at query time, first on safety questions ----------
# The illustrated guides in guides/ are not in the library index, so the library search never finds them. Their
# sections are searched here, in code, with each question: on a safety question (SAFETY_QUESTION) the best one or two
# sections that match well lead the passages handed to the model, and the library's official and medical sources come
# before forums, Q&A sites and encyclopedias. A guide section's id is "guide:<slug>#<heading id>", the same address a
# [text](guide:slug#heading) link in a guide uses, so the page opens the guide at that section.
GUIDES_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "guides")
GUIDE_SOURCE = "Starholm guides"
SAFETY_FIRST = True     # False: no guides and no reordering (the fetch step as it was before 14 November 2026)
GUIDE_LEAD = 2          # guide sections at most, ahead of the library's passages
GUIDE_MIN_SCORE = 20.0  # how well a section must match (guide_score) to be handed at all: on 14 November 2026 every
                        # section scoring 20 or more was on topic, and most below 18 were not
GUIDE_NEAR = 0.75       # a second section only when it scores at least this share of the best one
GUIDE_SITUATIONS_MAX = 40  # phrases one section may list in index.json's "situations"
GUIDE_SKIP = {"sources", "read next", "contents"}  # sections that are lists of links, not advice
# Safety questions: the emergencies the app gives its call line for (EMERGENCY_QUESTION), and hazards where the
# wrong advice can kill. Only the order and the guides in the handed passages depend on it.
HAZARD_QUESTION = re.compile(
    r"\b(?:power ?lines?|live wires?|downed (?:line|wire)s?|electric(?:al)? shock|generators?|gas leak|smell(?:s|ing)? "
    r"gas|fumes|exhaust|engine running|tourniquet|heat ?stroke|hypotherm\w*|frostbite|fire|smoke|flood\w*|"
    r"nuclear|radiation|fallout|explosion|chemical|toxic|poison\w*|burn(?:ed|t|s)?|stopped sweating)\b", re.I)
# Sources that lead the rest on a safety question: government, medical and first-aid publishers (library/search.py's
# OFFICIAL, by label), after the guides.
FIRST_SOURCES = {"Ready.gov (FEMA)", "CDC Travelers' Health", "MedlinePlus (NIH)", "UK Medicines A to Z (public sector information)",
                 "Military medicine manuals", "WikEM", "Medical Library", "Water Treatment Library",
                 "Post Disaster Resource Library", "Food for Preppers", "U.S. government survival and first-aid manuals",
                 "IFRC and WHO first-aid and emergency care guides", "USDA Complete Guide to Home Canning"}


def is_safety_question(question):
    return is_emergency(question) or bool(HAZARD_QUESTION.search(question or ""))


def heading_id(text):
    """A heading's id as the page makes it (render.js slugify): lower-case letters and digits joined by dashes."""
    s = re.sub(r"[\W_]+", "-", unicodedata.normalize("NFKC", str(text)[:400]).lower())
    s = re.sub(r"^-", "", s)[:80]
    return re.sub(r"-$", "", s) or "section"


def guide_plain(markdown):
    """A guide section as plain text for the model and the quote check: a figure becomes its description, a link its
    words, and emphasis marks, callout markers and quote marks at line starts go."""
    text = re.sub(r"!\[([^\]]*)\]\([^)]*\)", r"\1", markdown)
    text = re.sub(r"\[([^\]]+)\]\([^)]*\)", r"\1", text)
    text = re.sub(r"(?m)^>\s?\[![\w-]+\]\s*", "", text)
    text = re.sub(r"(?m)^>\s?", "", text)
    return re.sub(r"\*\*|__", "", text).strip()


_GUIDE_CACHE = {}


def _mtime(path):
    """A file's modification time, or None when it is not there (a guide listed for the upgrade, not downloaded)."""
    try:
        return os.path.getmtime(path)
    except OSError:
        return None


def guide_sections(folder=None):
    """Every guide's sections: [{"id", "title", "source", "official", "text", "words"}], read once per folder and
    again when a guide changes. A section is a heading of level 2 or 3 with what follows it, up to FETCH_ONE
    characters; the guide's own title and contents list are left out, as are its Sources and Read next lists."""
    folder = folder or GUIDES_DIR
    try:
        with open(os.path.join(folder, "index.json"), encoding="utf-8") as f:
            index = json.load(f)
        stamp = (os.path.getmtime(os.path.join(folder, "index.json")),) + tuple(
            (g.get("slug"), _mtime(os.path.join(folder, g.get("slug", ""), "guide.md")))
            for g in index if isinstance(g, dict) and re.fullmatch(r"[a-z0-9-]{1,80}", str(g.get("slug")))
            and _mtime(os.path.join(folder, g.get("slug", ""), "guide.md")) is not None)  # a body not on disk: skipped
    except (OSError, ValueError, TypeError, AttributeError):
        return []
    if _GUIDE_CACHE.get(folder, (None,))[0] == stamp:
        return _GUIDE_CACHE[folder][1]
    out = []
    for g in index:
        slug = g.get("slug") if isinstance(g, dict) else None
        if not isinstance(slug, str) or not re.fullmatch(r"[a-z0-9-]{1,80}", slug):
            continue
        try:
            with open(os.path.join(folder, slug, "guide.md"), encoding="utf-8") as f:
                lines = f.read(2_000_000).split("\n")
        except OSError:
            continue
        title, used, current = str(g.get("title") or slug), set(), None
        sections = []
        for line in lines:
            m = re.match(r"^(#{1,6})\s+(.+?)\s*#*\s*$", line)
            if m:
                base, n = heading_id(m.group(2)), 0
                hid = "h-" + base
                while hid in used:  # render.js uniqueId: a repeated heading gets -1, -2...
                    n += 1
                    hid = f"h-{base}-{n}"
                used.add(hid)
                current = None
                if len(m.group(1)) in (2, 3) and m.group(2).strip().lower() not in GUIDE_SKIP:
                    current = {"heading": m.group(2).strip(), "anchor": base if n == 0 else f"{base}-{n}", "lines": []}
                    sections.append(current)
                continue
            if current is not None:
                current["lines"].append(line)
        for sec in sections:
            body = guide_plain("\n".join(sec["lines"]))
            if not body:
                continue
            text = sec["heading"] + "\n\n" + body
            if len(text) > FETCH_ONE:  # cut at the last paragraph or line that fits
                cut = max(text.rfind("\n\n", 0, FETCH_ONE), text.rfind("\n", 0, FETCH_ONE))
                text = text[:cut if cut > 0 else FETCH_ONE].rstrip()
            out.append({"id": f"guide:{slug}#{sec['anchor']}", "title": f"{title}: {sec['heading']}",
                        "source": GUIDE_SOURCE, "official": False, "text": text,
                        "words": set(), "situations": situation_phrases(g, sec["anchor"])})
    for sec in out:  # the words each section holds, by the prefixes topic_keys uses, with its title's counted apart
        sec["words"] = _guide_words(sec["text"])
        sec["title_words"] = _guide_words(sec["title"])
    _GUIDE_CACHE[folder] = (stamp, out)
    return out


def situation_phrases(entry, anchor):
    """The phrases an index entry's "situations" lists for one section ({"<section anchor>": ["phrase", ...]}),
    lower-case, as situation_text writes them: the words people use for an emergency the section covers, which are
    often not the guide's own ("isn't breathing" or "unresponsive" for CPR)."""
    found = (entry.get("situations") if isinstance(entry.get("situations"), dict) else {}).get(anchor)
    if not isinstance(found, list):
        return []
    return [situation_text(p) for p in found if isinstance(p, str) and p.strip()][:GUIDE_SITUATIONS_MAX]


def situation_text(text):
    """Text as situations are matched: lower-case, curly apostrophes straight, "n't" as " not" ("isn't" is "is not",
    "can't" is "ca not", the same on both sides), runs of spaces as one."""
    text = (text or "").lower().replace("\u2019", "'").replace("n't", " not")
    return re.sub(r"\s+", " ", text).strip()


def guide_situations(question, folder=None):
    """The guide sections whose listed situations the question names, one per guide: when several sections of a
    guide list it (CPR for an adult, CPR for a child or a baby), the one the question's own words match best
    (guide_score), the first listed on a tie."""
    sections = guide_sections(folder)
    asked = situation_text(question)
    named = [sec for sec in sections if any(
        re.search(rf"(?<![\w']){re.escape(p)}(?![\w'])", asked) for p in sec.get("situations", []))]
    if not named:
        return []
    words, counts = _guide_words(question), _guide_counts(sections)
    best = {}
    for sec in named:
        guide = sec["id"].split("#", 1)[0]
        score = guide_score(sec, words, counts, len(sections))
        if guide not in best or score > best[guide][0]:
            best[guide] = (score, sec)
    return [sec for score, sec in best.values()]


def _guide_words(text):
    """A text's words for matching guide sections: lower-case, a plural "s" dropped ("lines" is "line"), cut to five
    letters ("burns", "burning" and "burned" are one); stopwords and one-letter words left out."""
    out = set()
    for token in re.findall(r"[\w']+", (text or "").lower()):
        if token in STOPWORDS or len(token) < 2:
            continue
        if len(token) > 3 and token.endswith("s") and not token.endswith("ss"):
            token = token[:-1]
        out.add(token[:5])
    return out


def _guide_counts(sections):
    """How many sections hold each word (guide_score's rarity)."""
    counts = {}
    for sec in sections:
        for w in sec["words"] | sec["title_words"]:
            counts[w] = counts.get(w, 0) + 1
    return counts


def guide_score(section, wanted, counts, total):
    """How well a guide section matches the question's words (prefixes, as topic_keys gives them): each word it
    holds counts by how rare it is among the sections, double in the guide's or section's title."""
    import math
    score = 0.0
    for w in wanted:
        if w in section["words"] or w in section["title_words"]:
            idf = math.log(1 + total / (1 + counts.get(w, 0)))
            score += idf * (2 if w in section["title_words"] else 1)
    return score


def guide_matches(question, queries, folder=None):
    """The guide sections that match the question and its search words well enough, best first: [(score, section)].
    A section must share at least two of the question's own words."""
    sections = guide_sections(folder)
    if not sections:
        return []
    asked = _guide_words(question)
    wanted = asked | _guide_words(" ".join(queries or []))
    counts = _guide_counts(sections)
    scored = []
    for sec in sections:
        if len(asked & (sec["words"] | sec["title_words"])) < 2:
            continue
        score = guide_score(sec, wanted, counts, len(sections))
        if score >= GUIDE_MIN_SCORE:
            scored.append((score, sec))
    return sorted(scored, key=lambda x: -x[0])


def guide_lead(question, queries, folder=None):
    """The guide sections to hand first, GUIDE_LEAD at most: those whose situations the question names
    (guide_situations), then the best match by words, and the next best when it scores at least GUIDE_NEAR of it.
    [{"id", "title", "source", "official", "text"}]"""
    if not SAFETY_FIRST:
        return []
    matches = guide_matches(question, queries, folder)
    chosen = guide_situations(question, folder) + [sec for score, sec in matches[:GUIDE_LEAD]
                                                    if score >= GUIDE_NEAR * matches[0][0]]
    out, seen = [], set()
    for sec in chosen:
        if sec["id"] not in seen and len(out) < GUIDE_LEAD:
            seen.add(sec["id"])
            out.append({k: sec[k] for k in ("id", "title", "source", "official", "text")})
    return out


FETCH_QUESTION = True   # also search the question as typed, after the model's search words


def with_question(queries, question):
    """The searches the fetch step runs: the model's search words, then, on a safety question, the question itself,
    whose own words the model's can drift from (a power line question searched for "electrical hazard emergency")."""
    if FETCH_QUESTION and SAFETY_FIRST and question and is_safety_question(question) \
            and question.strip().lower() not in {q.strip().lower() for q in queries}:
        return list(queries) + [question[:MAX_QUERY_CHARS]]
    return list(queries)


def question_overlap(question, passage):
    """How many of the words of a question (topic_keys: a question, or it with its search words) a passage's title
    and text hold."""
    prefixes, words = topic_keys(question)
    low = f"{passage.get('title', '')} {passage.get('text', '')}".lower()
    return sum(p in low for p in prefixes) + sum(bool(re.search(rf"\b{re.escape(w)}\b", low)) for w in words)


def safety_order(picked, question, queries=()):
    """The passages handed to the model, labelled P1, P2... in order. Guide sections come first, best match first. On
    a safety question the official and medical sources (FIRST_SOURCES) come next, then the rest; within each of those
    two groups the passages holding more of the question's words come first, then more of its search words (a passage
    that matched one search on a stray word goes below one on the question's topic)."""
    if SAFETY_FIRST:
        safety = is_safety_question(question)
        group = lambda p: 0 if p["source"] == GUIDE_SOURCE else 2 if safety and p["source"] not in FIRST_SOURCES else 1  # noqa: E731
        words = " ".join(queries)
        fit = lambda p: 2 * question_overlap(question, p) + question_overlap(words, p)  # noqa: E731
        picked = sorted(picked, key=lambda p: (group(p), -fit(p) if safety and group(p) else 0))
    return [{**p, "label": f"P{i}"} for i, p in enumerate(picked, 1)]


# fetch-quoted labels the passages P1, P2... for the model's markers. Run 2 (13 November) found one answer that also
# named them in its text ("Note: P1 advises..., but P2 and P4 say..."), which means nothing to the person. Only the
# shapes a citation takes are touched: labels alone in brackets or joined by slashes at the end of a clause ("(P1, P3).")
# go, like ID_REMARK; labels before a reporting verb ("P2 and P4 say") or after "according to", "per", "see" or "from"
# become their passages' titles. P1, P2 and P3 are also respirator classes ("N95 (P2) mask", "FFP3/P3", "a P3
# respirator"), so a label with a mask word near it is never touched.
_LABEL = r"P[1-9]\d?"
_LABELS = rf"(?:[Pp]assages?[ \t]+)?{_LABEL}(?:[ \t]*(?:,[ \t]*and|,|/|&|and)[ \t]*{_LABEL})*"
PASSAGE_LABEL = re.compile(rf"\b{_LABEL}\b")
PASSAGE_LABEL_REMARK = re.compile(  # "(P1, P3)." or "P1/P2/P4." at the end of a clause: out
    rf"[ \t]*(?:[(\[][ \t]*{_LABELS}[ \t]*[)\]]|\b{_LABEL}(?:/{_LABEL})+\b)(?=[ \t]*(?:[.,;:!?]|$))")
PASSAGE_LABEL_CITED = re.compile(  # "P2 and P4 say", "according to P1": the titles
    rf"\b(?:(?P<cue>(?:[Aa]ccording to|[Pp]er|[Ss]ee|[Ff]rom|[Aa]s in)[ \t]+){_LABELS}\b|{_LABELS}\b(?=[ \t]+"
    r"(?:also[ \t]+|both[ \t]+)?(?:says?|advises?|notes?|states?|recommends?|warns?|mentions?|suggests?|describes?|"
    r"explains?|adds?|agrees?|disagrees?|shows?|lists?|stresses|emphasi[sz]es?|cautions?|confirms?|differs?)\b))")
RATING_WORD = re.compile(r"\b(?:class(?:es)?|types?|rated|ratings?|grades?)\s*[(\[]?$", re.I)  # "class P1/P2"
MASK_WORD = re.compile(r"\b(?:masks?|respirators?|filters?|filtering|cartridges?|FFP\d?|K?N9[59]|N100|[PR]100|R95)\b",
                       re.I)


def _outside_markers(parts, fix):
    """A line from its pieces, text and marker in turn, ending with text. fix gets the text between the markers joined
    by a placeholder, so a sentence a marker splits is still seen whole; the markers come back unchanged."""
    texts, marks = parts[0::2], parts[1::2]
    if "\ue010" in "".join(texts):  # the placeholder in the text itself: leave the line alone
        return "".join(parts)
    pieces = fix("\ue010".join(texts)).split("\ue010")
    if len(pieces) != len(texts):
        return "".join(parts)
    return "".join(piece + (marks[k] if k < len(marks) else "") for k, piece in enumerate(pieces))


def name_passage_labels(text, passages, question=""):
    """text with the passage labels it cites in its own words taken out or replaced by the passage's title. Its
    markers, its closing Sources list, a label with a mask word within 30 characters, and a label that the question or a
    passage itself uses ("P1 parking", a "P1" triage category) stay as written, as does a label of no passage it was
    given."""
    titles = {p["label"]: p.get("title") or "" for p in passages or [] if p.get("label")}
    used = (question or "") + "\n" + "\n".join(p.get("text") or "" for p in passages or [])
    labels = {k: v for k, v in titles.items() if v and not re.search(rf"\b{re.escape(k)}\b", used)}
    if not labels or not PASSAGE_LABEL.search(text or ""):
        return text

    def plain(piece):
        def ours(m):
            near = piece[max(0, m.start() - 30):m.end() + 30]
            return all(k in labels for k in PASSAGE_LABEL.findall(m.group(0))) and not MASK_WORD.search(near) \
                and not RATING_WORD.search(piece[:m.start()].rstrip())

        def titled(m):
            if not ours(m):
                return m.group(0)
            body = re.sub(r"\b[Pp]assages?[ \t]+", "", m.group(0)[len(m.group("cue") or ""):])
            return (m.group("cue") or "") + PASSAGE_LABEL.sub(lambda k: f'"{labels[k.group(0)]}"', body)

        piece = PASSAGE_LABEL_REMARK.sub(lambda m: "\ue011" if ours(m) else m.group(0), piece)  # marks where it went
        return PASSAGE_LABEL_CITED.sub(titled, piece)

    def sentences(piece):  # a sentence that mentions a mask word anywhere keeps all its labels
        if "\ue011" in piece:
            return piece
        piece = "".join(part if MASK_WORD.search(part) else plain(part)
                        for part in re.split(r"(?<=[.!?])(?=[ \t]+)", piece))
        # "Store it cool. P1/P2/P4." -> "Store it cool.", not "Store it cool..": punctuation either side of a label
        # taken out is one mark, the one after it
        return re.sub(r"[.,;:!?]?\ue011[ \t]*(?=[.,;:!?])|\ue011", "", piece)

    block = _sources_block(text)
    cut = block[0] if block else len(text)
    out = []
    for line in text[:cut].split("\n"):
        pos, kept = 0, []
        for start, end, *_ in find_markers(line):
            kept += [line[pos:start], line[start:end]]
            pos = end
        out.append(_outside_markers(kept + [line[pos:]], sentences))
    return "\n".join(out) + text[cut:]


def passages_block(question, passages):
    """The question with the passages it is to be answered from, labelled P1, P2..., each sentence numbered for the
    markers (passage_sentences)."""
    if not passages:
        return question + "\n\n" + FETCH_NOTE + "\n\n(The library search found nothing for this question.)"
    texts = passage_sentences(passages)[0]
    return question + "\n\n" + FETCH_NOTE + "\n\n" + "\n\n".join(
        f"[{p['label']}] {p['title']} ({p['source']})\n{text}" for p, text in zip(passages, texts))


def documents_for(library, results, max_chars=6000):
    """The documents a search found, for the panel: each result with its passage in full (at most max_chars)."""
    out = []
    for r in results or []:
        doc = library.read(r["id"], max_chars=1) if library else None
        if doc:
            out.append({"id": r["id"], "title": doc["title"], "source": doc["source"], "official": doc["official"],
                        "passage": doc["text"][:max_chars]})
    return out


BAD_ID = "The id must be a number from search_library."
NO_PASSAGE = "No passage with that id."
# Models sometimes open an id they never saw (Lightning opened "How to Be an Organized Artist" that way, then cited it).
UNSEEN_ID = "No search result has that id. Use an id from your search_library results, or answer from what you have."
TOOL_ERRORS = {BAD_ID, NO_PASSAGE, UNSEEN_ID}
KNOWN_ID = re.compile(r"\[id (\d+)\]|read_article\(id=(\d+)\)")  # ids the tools have shown the model


def _ids(text):
    return {int(a or b) for a, b in KNOWN_ID.findall(text or "")}


def _args(call):
    args = call.get("function", {}).get("arguments") or {}
    if isinstance(args, str):
        try:
            args = json.loads(args or "{}")
        except ValueError:
            args = {}
    return args if isinstance(args, dict) else {}


def _used_ids(messages):
    """Passages it read or showed earlier in this chat. Their ids stay usable after trim() has shortened those results
    to a title, as the model still sees them in its own earlier calls."""
    used, pending = set(), []
    for m in messages:
        if m.get("role") == "assistant":
            pending = list(m.get("tool_calls") or [])
        elif m.get("role") == "tool" and pending:
            call = pending.pop(0)
            if call.get("function", {}).get("name") in ("read_article", "show_article") \
                    and (m.get("content") or "") not in TOOL_ERRORS:
                try:
                    used.add(int(_args(call).get("id")))
                except (TypeError, ValueError):
                    pass
    return used


def run_tool(library, name, args):
    """Run one tool call. Returns (text for the model, event for the screen or None)."""
    if name == "search_library":
        query, topic = str(args.get("query", "")), args.get("topic") or "any"
        hits = library.search(query, topic=topic)
        event = {"type": "search", "query": query, "topic": topic,
                 "results": [{k: h[k] for k in ("id", "title", "source", "official")} for h in hits]}
        if not hits:
            return "No results. Try different or fewer keywords.", event
        return "\n\n".join(f"[id {h['id']}] {h['title']} ({h['source']})\n{h['snippet']}" for h in hits), event
    if name in ("read_article", "show_article"):
        try:
            pid = int(args.get("id", 0))
        except (TypeError, ValueError):
            return BAD_ID, None
        doc = library.read(pid, max_chars=READ_CHARS)
        if not doc:
            return NO_PASSAGE, None
        event = {"type": "read" if name == "read_article" else "show", "id": pid,
                 "title": doc["title"], "source": doc["source"], "official": doc["official"]}
        if name == "show_article":
            return f"Opened \"{doc['title']}\" ({doc['source']}) on screen.", event
        more = f"\n\n[More of this article: read_article(id={doc['next_id']})]" if doc["next_id"] else ""
        return f"{doc['title']} ({doc['source']})\n{doc['url'] or ''}\n\n{doc['text']}{more}", event
    return f"Unknown tool {name}", None


def _stream(model, messages, tools, think, options):
    body = {"model": model, "messages": messages, "stream": True, "keep_alive": "15m", "options": options}
    if tools:
        body["tools"] = tools
    if think is not None:
        body["think"] = think
    with _post("/api/chat", body, stream=True) as resp:
        for line in resp:
            yield json.loads(line)


def attach_excerpts(library, messages):
    """For models without tool support: put the top library passages into the question itself."""
    question = messages[-1]["content"]
    hits = library.search(question, limit=4)
    blocks = []
    for h in hits:
        doc = library.read(h["id"], max_chars=2500)
        blocks.append(f"[{doc['title']} ({doc['source']})]\n{doc['text']}")
    if blocks:
        messages[-1] = {"role": "user", "content": question + "\n\nLibrary excerpts:\n\n" + "\n\n".join(blocks)}
    return [{"type": "read", **{k: h[k] for k in ("id", "title", "source", "official")}} for h in hits]


TOOL_MARKUP = ("<tool_call", "<function")
# A model's reasoning written out as ordinary content: a draft, then a line that is exactly "</think>", then the real
# answer (seen from qwen3.8:27b-mlx in the app on 12 November 2026, and from Granite in an earlier citation run).
THINK_END = re.compile(r"^[ \t\r]*</think>[ \t\r]*$", re.MULTILINE)


def reasoning_end(text, final=True):
    """Where the real answer starts in text that holds reasoning written out as content: just after its last line
    that is exactly "</think>"; None when there is none. While streaming (final=False) the line counts only once the
    newline after it has arrived, so a "</think>" still being written isn't taken for the whole line."""
    found = None
    for m in THINK_END.finditer(text):
        if final or m.group().endswith("\r") or text[m.end():m.end() + 1] in ("\r", "\n"):
            found = m
    return found.end() if found else None


def strip_reasoning(text):
    """text without any reasoning written out as content: everything up to and including its last line that is
    exactly "</think>" is dropped."""
    cut = reasoning_end(text or "")
    return text if cut is None else text[cut:].lstrip("\r\n")
SOURCES_LINE = re.compile(r"^[*_# ]*Sources?(?:[*_ ]*:.*|[*_ ]*)$", re.IGNORECASE | re.MULTILINE)  # or a "Sources" heading
LIST_ITEM = re.compile(r"\s*([-*•–+]|\d+[.)])\s")
ID_ROW = re.compile(r"\s*\[ids?\s*\d{5,}\]", re.I)
# A passage id the way models write one: "1403312", "[id 1403312]", "(ids 1403312 and 1403313, etc.)", "passage
# #1403312", "read_article(id=1403312)", "id **1403312**".
_ONE_ID = r"(?:\b(?:passages?|ids?)\b\s*[:=#]?\s*|#)?[*_]*\b\d{5,}\b[*_]*"
ID_REF = re.compile(r"(\bread_article\(\s*(?:id\s*=\s*)?)?\[?" + _ONE_ID + r"(?:\s*(?:,|and|&|/)\s*" + _ONE_ID
                    + r")*(?:,?\s*etc\b\.?)?\]?(?(1)\s*\)?)", re.I)
ID_REMARK = re.compile(r"[ \t]*[(\[]\s*(?:passage\s+)?ids?\s*:?\s*\d{5,}(?:\s*(?:,|and)\s*\d{5,})*(?:,?\s*etc\.?)?\s*[)\]]",
                       re.I)
LOOKUP_PREAMBLE = re.compile(r"(?:let me|i'll|i will|i need to|i should|now i|next,? i|first,? i)\b[^\n]{0,200}?"
                             r"\b(?:search|look|check|read|open|find)\b[^\n]{0,200}$", re.I)
INLINE_SOURCES = re.compile(r"([.!?])[ \t]+((?:\*\*)?Sources?(?:\*\*)?:[^\n]*?)\s*\Z")
# A Sources line made of tool calls ("Sources: search_library (generator carbon monoxide garage), read_article (...)"),
# written out instead of looking anything up: Lightning opened an answer with one in an early Windows test.
TOOL_SOURCES = re.compile(r"^[*_# ]*Sources?\b[^\n]*\b(?:search_library|read_article|show_article)\b[^\n]*(?:\n|\Z)",
                          re.IGNORECASE | re.MULTILINE)
# Said before the last round, after the lookups run out. Without it some models (Granite) answer the last
# round with yet another lookup written out as text, and the person gets no answer at all.
FINAL_NUDGE = ("(Note from the app, not the person: you have used all your library lookups. Answer the question "
               "now from what you already read, and list your sources. Do not call any more tools.)")
THINK_NUDGE = ("(Note from the app, not the person: you have reasoned long enough. Answer the question now from what "
               "you already have, briefly, and list your sources.)")
# When the person asks it to look something up and it answers from memory, it is asked once more, told to search.
SEARCH_NUDGE = ("(Note from the app, not the person: they asked you to look this up. Search the library with "
                "search_library before you answer.)")
ASKED_SEARCH = re.compile(r"\b(?:look (?:it|that|this|them|those) up|look up|search (?:the library|for|it|that|this)|"
                          r"check (?:the library|your sources|a source)|look (?:in|through) the library|"
                          r"find (?:a|the|me a|some) sources?|what does the library say|in the library)\b", re.I)
# Said with the best passages its searches found, when it looked things up but wrote no answer: Granite searched six
# times for "A power line is down in my front yard" and then wrote another lookup instead of answering (issue #2).
RESCUE_NOTE = ("(Note from the app, not the person: you can't look anything else up. Answer the question now from "
               "these library excerpts only, say plainly what they don't cover, and list your sources.)")
# After an answer, the model gets one short request of its own to update its notes about the person (memory.py).
# Asked while answering, models put the question first and rarely saved anything; asked for tool calls after the
# answer, Nemotron Super said "nothing to save" about "I have type 1 diabetes"; given the whole chat, Lightning lost
# such facts among the library results. A short request with just what the person said, answered in JSON, worked
# best for every model, and Ollama still has the chat cached for the next question.
MEMORY_CHECK = """You keep short notes about the person using Starholm, an offline assistant app, so it knows them in later chats. Your notes now:
{notes}

{earlier}Their last message: "{said}"

Save only what they said in their last message about themselves or their household that will still be true in later chats and isn't in your notes yet: who lives with them and their ages, health conditions, medicines, allergies, pregnancy, pets, their equipment, where they live, what one of their files is or how much it matters, how they like answers. Don't save their question, what is happening today (a power cut, today's symptoms), facts from a library, or guesses. When they ask you to forget or delete something, put the notes it names under "forget", as written; never rewrite a note to say it's gone.

Reply with only a JSON object:
{"forget": [notes to delete because they asked you to forget them, as written in your notes],
 "change": [{"old": a note as written in your notes, "new": how it should read now}, for facts that changed],
 "remember": [new facts, one per note]}
Use empty lists when there is nothing to do.

Examples:
- "My daughter Maya is 7 and allergic to penicillin. She has an earache tonight." gives {"forget": [], "change": [], "remember": ["Daughter Maya (7) is allergic to penicillin."]} (the earache passes, so it's left out).
- "I'm on blood thinners. Can I take ibuprofen for my back?" gives {"forget": [], "change": [], "remember": ["Takes blood thinners."]} (a fact mentioned on the way to a question still counts).
- "Please forget where I live." with the note "Lives in Austin, Texas." gives {"forget": ["Lives in Austin, Texas."], "change": [], "remember": []}
- "Forget about my cats." with the note "Has two cats." gives {"forget": ["Has two cats."], "change": [], "remember": []}
- "The generator manual is the most important of my files." gives {"forget": [], "change": [], "remember": ["Most important file: the generator manual."]}
- "My doctor raised my metformin to 1000 mg." with the note "Takes metformin 500 mg twice a day." gives {"forget": [], "change": [{"old": "Takes metformin 500 mg twice a day.", "new": "Takes metformin 1000 mg twice a day."}], "remember": []}
- "Delete the note about my bike." with the note "Has an e-bike for errands." gives {"forget": ["Has an e-bike for errands."], "change": [], "remember": []}
- "I sold the Subaru and bought a Ford pickup." with the note "Drives a Subaru Outback." gives {"forget": [], "change": [{"old": "Drives a Subaru Outback.", "new": "Drives a Ford pickup."}], "remember": []}
- "The power went out. How long is my fridge safe?" gives {"forget": [], "change": [], "remember": []}"""
PERSONAL = re.compile(r"\b(i|i'm|im|i've|ive|i'd|my|me|mine|myself|we|we're|we've|our|ours|us|forget|remember|remind|"
                      r"daughter|son|wife|husband|partner|kids?|child|children|baby|mom|dad|mother|father|grand\w+|"
                      r"family|household|roommates?)\b", re.I)


def chat_events(model, library, messages, reasoning="off", caps=None, length="normal", memory=None,
                mode="standard", thinking=True):
    """Answer the last user message. Yields thinking/search/read/show/token/tick/done events (thinking and token
    events carry the model's reasoning and answer as they stream), then, with a memory the person lets the AI write
    to, memory_check and memory events from a short step after the answer.
    mode: one of ANSWER_MODES (see "grounded answers" above); "standard", the default, is the tool loop as it always
    was (the app itself passes its setting, fetch-quoted unless the person changes it). In the quoted modes the done event also carries "marked", "claims" and "grounding" (annotate), and in
    fetch-quoted a search_words event comes first. thinking=False: no reasoning at all, for a model that can reason
    (the per-model "thinking off" setting, for the tests)."""
    caps = capabilities(model) if caps is None else caps
    can_call = "tools" in caps
    tools = TOOLS if (library and can_call) else None
    question = messages[-1]["content"]
    think = think_setting(model, caps, reasoning) if thinking else (False if "thinking" in caps else None)
    quoted = QUOTED.get(mode) if library else None
    given = []  # in the quoted modes: the passages the model was given, which its quotes are checked against
    limit = LENGTH_LIMITS.get(length, LENGTH_LIMITS["normal"])
    # With Reasoning off, a model that reasons anyway (Qwen's brief think) is held to the short limit.
    think_cap = REASONING_LIMITS.get(reasoning, 0) or REASONING_LIMITS["short"]
    # The length limit is for the answer: reasoning comes on top of it, since Ollama counts both.
    options = {"num_predict": -1 if limit < 0 or think_cap < 0 else limit + think_cap,
               **SAMPLING.get(family(model), {"temperature": 0.7}), "num_ctx": NUM_CTX}
    sources, seen, tokens, seconds, cut_off, looked, unopened, named = {}, {}, 0, 0.0, False, False, [], None
    dropped = False
    earlier = any(m.get("role") == "tool" for m in messages)  # lookups from earlier questions in this chat
    known = set().union(_used_ids(messages), *(_ids(m.get("content")) for m in messages if m.get("role") == "tool"))
    read_before = _read_earlier(messages)
    asked_at = None  # fetch-quoted: where the question sits, with the passages added for this answer only
    if quoted == "fetch":
        tools = None  # the app looks things up; the model only answers
        asked = [m["content"] for m in messages[:-1] if m.get("role") == "user" and isinstance(m.get("content"), str)]
        queries = yield from search_words(model, caps, question, asked, options)
        yield {"type": "search_words", "queries": queries}
        given = yield from _tracked(fetch_passages(library, queries, question), sources, seen)
        looked, asked_at = True, len(messages) - 1
        messages[asked_at] = {"role": "user", "content": passages_block(question, given)}
    elif library and not can_call:
        for event in attach_excerpts(library, messages):
            sources.setdefault(event["id"], {k: event.get(k) for k in ("title", "source", "official")})
            yield event
    must_search = bool(tools) and bool(ASKED_SEARCH.search(question))  # they asked it to look something up
    round_no, nudge = 0, None  # nudge: a note from the app for the next round
    while True:
        answer_now = nudge == THINK_NUDGE
        use_tools = tools if round_no < MAX_TOOL_ROUNDS and not answer_now else None  # last round: must answer
        if tools and not use_tools and not nudge:
            nudge = FINAL_NUDGE
        if nudge:
            messages.append({"role": "user", "content": nudge})
        content, calls, final, held, thought, over = [], [], {}, [], 0, False
        stream = _stream(model, messages, use_tools, False if answer_now else think, options)
        for chunk in stream:
            msg = chunk.get("message", {})
            if msg.get("thinking"):
                yield {"type": "thinking", "text": msg["thinking"]}  # its reasoning, which the person can read
                thought += 1  # it streams about a token at a time
                if 0 < think_cap < thought and not content and not calls and not answer_now:
                    over = True
                    break
            elif not msg.get("content"):
                yield {"type": "tick"}  # nothing to show: lets the server notice a closed page
            delta = msg.get("content") or ""
            if delta:
                content.append(delta)
                cut = reasoning_end("".join(content), final=False)
                if cut is not None:  # what streamed so far was its reasoning: clear it, keep only what follows
                    rest = "".join(content)[cut:].lstrip("\r\n")
                    yield {"type": "retry", "why": "reasoning"}
                    content, held, delta = [rest] if rest else [], [], rest
            if delta:
                # Some models write a tool call out as text before Ollama turns it into tool_calls; keep that
                # markup off the screen, and hold back text that may still turn out to be the start of it.
                text = "".join(content).lstrip()
                if tools and text.startswith(TOOL_MARKUP):  # also on the last round, where it can't be run
                    yield {"type": "tick"}
                elif tools and any(t.startswith(text) for t in TOOL_MARKUP):
                    held.append(delta)
                else:
                    yield {"type": "token", "text": "".join(held) + delta}
                    held = []
            calls.extend(msg.get("tool_calls") or [])
            if chunk.get("done"):
                final = chunk
        if hasattr(stream, "close"):
            stream.close()  # stops Ollama generating when the loop above ended early
        if reasoning_end("".join(content)) is not None:  # "</think>" as the very last line
            yield {"type": "retry", "why": "reasoning"}
            content, held = [strip_reasoning("".join(content))], []
            if content[0]:
                yield {"type": "token", "text": content[0]}
        sent, nudge = nudge, None
        if sent:
            messages.pop()  # keep the app's notes out of the saved conversation
        if over:  # reasoning in circles: answer now from what it has, with no more reasoning or lookups
            yield {"type": "think_limit", "limit": think_cap}
            nudge = THINK_NUDGE
            continue
        if held and not calls:
            yield {"type": "token", "text": "".join(held)}
        tokens += final.get("eval_count", 0)
        cut_off = cut_off or final.get("done_reason") == "length"
        seconds += final.get("eval_duration", 0) / 1e9
        if not use_tools:
            dropped = bool(calls)
            calls = []  # the last round is for answering: a lookup asked for anyway isn't run
        if must_search and use_tools and not looked and not calls:  # asked to look it up, it answered from memory
            must_search, nudge = False, SEARCH_NUDGE
            yield {"type": "retry", "why": "search"}  # what streamed so far isn't the answer
            continue
        entry = {"role": "assistant", "content": "".join(content)}
        if calls:
            entry["tool_calls"] = calls
        messages.append(entry)
        if not calls:
            break
        looked = True
        for call in calls:
            fn, args = call.get("function", {}), _args(call)
            try:
                pid = int(args.get("id"))
            except (TypeError, ValueError):
                pid = None
            if fn.get("name") in ("read_article", "show_article") and pid is not None and pid not in known:
                result, event = UNSEEN_ID, None
            else:
                result, event = run_tool(library, fn.get("name"), args)
            known |= _ids(result)
            if quoted and event:
                given.extend(_given(event, result))
            if event:
                if event["type"] in ("read", "show"):
                    sources.setdefault(event["id"], {k: event.get(k) for k in ("title", "source", "official")})
                for hit in event.get("results", []):
                    seen.setdefault(hit["id"], {k: hit.get(k) for k in ("title", "source", "official")})
                yield event
            messages.append({"role": "tool", "tool_name": fn.get("name"), "content": result})
        round_no += 1
    if asked_at is not None:
        messages[asked_at] = {"role": "user", "content": question}  # the passages were for this answer only
    answer, unfinished, discarded = messages[-1]["content"], False, None
    # No answer: a lookup written out as text, or cut off inside one, nothing at all, a tool's error repeated as the
    # answer, or "Let me check one more source." before a lookup it wasn't given.
    no_answer = lambda text: text.lstrip().startswith(TOOL_MARKUP) or not text.strip() \
        or text.strip().strip('"') in TOOL_ERRORS or bool(dropped and LOOKUP_PREAMBLE.match(text.strip()))  # noqa: E731
    if tools and no_answer(answer):
        discarded = answer  # kept for the eval logs
        rescue = yield from _rescue(model, library, messages, question, sources, seen, options)
        if rescue:
            tokens, seconds, cut_off = tokens + rescue["tokens"], seconds + rescue["seconds"], cut_off or rescue["cut_off"]
            if not no_answer(rescue["text"]):
                answer = messages[-1]["content"] = rescue["text"]  # the saved chat keeps the answer it gave
    answer = strip_reasoning(answer)  # belt and braces: reasoning never reaches the answer or the saved chat
    if quoted == "fetch" and given:  # "P2 and P4 say..." in its text: the passages' titles, before the check
        answer = name_passage_labels(answer, given, question)
    claims = grounding = None
    no_match = bool(quoted) and is_no_match(strip_quote_markers(answer))
    no_match_call = None
    # fetch-quoted found nothing to hand the model, on an emergency question: the app's own call line, always.
    emergency_call = emergency_call_target() if quoted == "fetch" and not given and is_emergency(question) else None
    if no_match:  # the page draws its own no-match lines in place of the model's: nothing of the model's text shows
        answer = strip_quote_markers(answer)
        # Asked to call for help (a call line after the no-match line, of any length): who to call, from the stored
        # numbers, so the page's call line never depends on the model's words.
        if re.search(r"(?im)^[ \t]*Call [^\n]+ now\.[ \t]*$", answer):
            no_match_call = emergency_call_target()
        no_match_call, emergency_call = no_match_call or emergency_call, None
        claims, grounding = [], {"mode": mode, "followed": True, "markers": 0, "malformed": 0, "statements": 0,
                                 "backed": 0}
    elif quoted:  # markers out, each statement marked as backed or not (the marks stay in until the done event)
        answer, claims, grounding = annotate(answer, given, mode, held_numbers(), drop_calls=bool(emergency_call))
    if quoted == "fetch":  # its sources are the passages a statement rests on; the others it was only shown
        backing = {s["id"] for c in claims or [] if c["backed"] for s in c.get("sources") or [c.get("source") or {}]
                   if s.get("id") is not None}
        for pid in [p for p in sources if p not in backing]:
            seen.setdefault(pid, sources.pop(pid))
    answer, follow_ups = split_follow_ups(answer)
    if is_no_match(answer):
        follow_ups = []  # the no-match answer has none
    read = _one_per_article([{"id": k, **v} for k, v in sources.items()])
    found = _one_per_article(read + [{"id": k, **v} for k, v in seen.items()])  # read first, then the rest
    if no_answer(answer):
        answer, unfinished = "", True  # nothing to show
    elif library:
        answer = TOOL_SOURCES.sub("", answer).strip("\n")  # the Sources line added below says what it really read
        # The model names the sources it used, and the screen shows every article it opened. Models still name sources
        # they never opened (evals/citation_check.py), so a Sources line that does is replaced by what it read.
        if read:
            line = "Sources: " + "; ".join(f"{s['title']} ({s['source']})" for s in read[:6])
        elif quoted == "fetch":
            line = "Sources: none (no passage it was given backs what it says)"
        elif looked:
            line = "Sources: none opened (it only saw the search results below)"
        elif earlier:
            line = "Sources: what it read earlier in this chat"
        else:
            line = "Sources: none (from its own knowledge)"
        # Articles it only saw in search results count too: they exist, and it saw their excerpts.
        shown, ids = read_before + [{"id": k, **v} for k, v in seen.items()], set(sources) | set(seen)
        by_id = {**seen, **sources}
        # fetch-quoted: the app handed it every passage, so the labels it was given (P1, P2...) count as opened.
        labels = {p["label"]: p for p in given} if quoted == "fetch" else {}
        made_up = lambda items: [n for n in items or [] if not _opened(n, read, shown, ids, labels)]  # noqa: E731
        # "...add it to the file. Sources: X" at the very end goes on a line of its own, but only when X names what it
        # read or saw: in "Heat one room. Source: a wood stove, never a grill indoors" it is part of the answer.
        moved = INLINE_SOURCES.sub(r"\1\n\n\2", answer)
        if moved != answer and cited_sources(moved) and not made_up(cited_sources(moved)):
            answer = moved
        named = cited_sources(answer)
        unopened = made_up(named)
        # "OK, I'll forget where you live." answers a request, not a question: no "from its own knowledge" line. Only
        # when the message asks nothing else and the reply reads as a short acknowledgment ("Remember my son weighs
        # 16 kg. how much Tylenol can he have" still gets the line).
        just_memory = not named and not read and not looked and not earlier and asked_memory(question) \
            and not INFO_ASK.search(question) and ACKNOWLEDGED.search(answer.replace("’", "'")) and len(answer) < 300
        # fetch-quoted: always the app's own line, naming the passages its statements rest on.
        if quoted == "fetch" or ((not named or unopened) and not just_memory):
            answer = _with_sources_line(answer, line)
        elif named and any(_known_ids(item, by_id) for item in named):  # named by id, meaningless to the person
            answer = _with_sources_line(answer, "Sources: " + "; ".join(_titled(item, by_id) for item in named))
        # "(ids 1403312, 1400192, etc.)" in the text goes; "(ID 4471203)" from the person's insurance card doesn't.
        block, pool = _sources_block(answer), known | ids | {s["id"] for s in read_before if s.get("id") is not None}
        cut = block[0] if block else len(answer)
        answer = ID_REMARK.sub(lambda m: "" if _known_ids(m.group(0), pool, every=True) else m.group(0), answer[:cut]) \
            + answer[cut:]
    done = {"type": "done", "tokens": tokens, "seconds": seconds, "answer": answer, "cut_off": cut_off,
            "unfinished": unfinished, "unopened": unopened, "named": named if library and not unfinished else None,
            "sources": read, "seen": found[len(read):], "think": think, "discarded": discarded,
            "follow_ups": follow_ups if not unfinished else []}
    if quoted:
        # An answer cut off at the length limit keeps its claims like any other: what it wrote was checked. One with
        # no answer at all (unfinished) shows no text, so it has nothing to mark.
        done.update(answer=strip_claim_marks(answer), marked=answer if claims and answer else None,
                    claims=claims if answer else [], grounding=grounding, no_match=no_match,
                    no_match_call=no_match_call, emergency_call=emergency_call)
    yield done
    # Only when they said something about themselves or their family, or asked it to remember or forget something: the
    # step takes a few seconds. Not after an answer cut off at the length limit, which "Keep going" continues. It reads
    # only what the person said, so an answer that didn't finish doesn't stop it.
    if memory is not None and memory.ai_writes and not cut_off and _about_them(question, messages, memory):
        try:
            yield from memory_check(model, messages, memory, think_setting(model, caps), options)
        except Exception:  # noqa: BLE001 - the answer is out; a failed memory step only means nothing new was saved
            yield {"type": "memory_failed"}


def _tracked(events, sources, seen):
    """Pass on fetch_passages' events, noting what it found (seen) and handed the model (sources); returns its
    passages."""
    while True:
        try:
            event = next(events)
        except StopIteration as stop:
            return stop.value
        for hit in event.get("results", []):
            seen.setdefault(hit["id"], {k: hit.get(k) for k in ("title", "source", "official")})
        if event["type"] == "read":
            sources.setdefault(event["id"], {k: event.get(k) for k in ("title", "source", "official")})
        yield event


SEARCH_BLOCK = re.compile(r"\[id (\d+)\] [^\n]*\n(.*?)(?=\n\n\[id \d+\] |\Z)", re.S)


def _given(event, result):
    """What a tool gave the model, as passages its quotes are checked against (tools-quoted): the text read_article
    returned, or each search result's snippet."""
    meta = lambda e: {k: e.get(k) for k in ("id", "title", "source", "official")}  # noqa: E731
    if event["type"] == "read":  # the article's own text: not the title, address and "[More of this article...]" lines
        body = re.sub(r"\n\n\[More of this article: read_article\(id=\d+\)\]\s*\Z", "", re.sub(r"\A[^\n]*\n[^\n]*\n\n", "", result))
        return [{"label": str(event["id"]), **meta(event), "text": body}]
    if event["type"] == "search":
        by_id = {r["id"]: r for r in event.get("results", [])}
        return [{"label": pid, **meta(by_id[int(pid)]), "text": text} for pid, text in SEARCH_BLOCK.findall(result)
                if int(pid) in by_id]
    return []


# The two no-match answers: the line alone, or the line and "Call <number> now." (a Sources line may follow).
NO_MATCH_ANSWER = re.compile(r"\s*I couldn['’]t find this in your library\.(?:[ \t]*\r?\n\s*Call [^\n]+ now\.)?\s*\Z")


def is_no_match(answer):
    """Whether an answer is one of the two no-match forms, apart from its Sources line."""
    block = _sources_block(answer or "")
    return bool(NO_MATCH_ANSWER.match((answer or "")[:block[0]] if block else (answer or "")))


MAX_FOLLOW_UPS = 3
FOLLOW_UPS = re.compile(r"^[ \t>]*[*_#]*[ \t]*(?:follow[- ]?ups?|suggested (?:follow[- ]up )?questions)[*_ ]*:[*_ \t]*(.*)$",
                        re.IGNORECASE | re.MULTILINE)


def split_follow_ups(answer):
    """The answer without its "Follow-ups:" block, and the questions in it (at most three). The block counts only near
    the end: after it may come nothing but the Sources line and its list."""
    found = list(FOLLOW_UPS.finditer(answer or ""))
    if not found:
        return answer, []
    head = found[-1]
    items = [head.group(1)] if head.group(1).strip() else []
    end = head.end()
    for row in answer[end:].split("\n")[1:]:
        item = LIST_ITEM.match(row)
        if item:
            items.append(row[item.end():])
        elif row.strip():
            break
        elif items:
            end = min(len(answer), end + 1 + len(row))
            break
        end = min(len(answer), end + 1 + len(row))
    rest = answer[end:].strip()
    if rest and not (SOURCES_LINE.match(rest.split("\n", 1)[0]) and len(rest) <= 2000):
        return answer, []
    out = []
    for item in items:
        text = " ".join(re.sub(r"[\x00-\x1f\x7f]", " ", item).split()).strip(" *_\"'“”")[:200].strip()
        if text and text.lower() not in (o.lower() for o in out):
            out.append(text)
    kept = (answer[:head.start()].rstrip() + ("\n\n" + rest if rest else "")).strip("\n")
    return kept, out[:MAX_FOLLOW_UPS]


def _rescue(model, library, messages, question, sources, seen, options):
    """It looked things up but wrote no answer: show it the best passages its searches found (what it read first, else
    the top search results, else a search for the question) and ask once more, without tools or reasoning. Yields a
    read event for each passage it is given; returns {"text", "tokens", "seconds", "cut_off"}, or None with nothing to
    give it."""
    picks = list(sources) + [pid for pid in seen if pid not in sources]
    picks = picks or [h["id"] for h in library.search(question, limit=4)]
    blocks = []
    for pid in picks:
        doc = library.read(pid, max_chars=2500)
        if not doc:
            continue
        blocks.append(f"[{doc['title']} ({doc['source']})]\n{doc['text']}")
        if pid not in sources:
            sources[pid] = {k: doc[k] for k in ("title", "source", "official")}
            yield {"type": "read", "id": pid, **sources[pid]}
        if len(blocks) == 4:
            break
    if not blocks:
        return None
    yield {"type": "retry", "why": "excerpts"}  # what streamed so far isn't the answer
    messages.append({"role": "user", "content": RESCUE_NOTE + "\n\n" + "\n\n".join(blocks)})
    content, final = [], {}
    try:
        for chunk in _stream(model, messages, None, False, options):
            msg = chunk.get("message", {})
            if msg.get("thinking"):
                yield {"type": "thinking", "text": msg["thinking"]}
            if msg.get("content"):
                content.append(msg["content"])
                cut = reasoning_end("".join(content), final=False)
                if cut is not None:  # its reasoning, written out: clear it, keep only what follows
                    rest = "".join(content)[cut:].lstrip("\r\n")
                    yield {"type": "retry", "why": "reasoning"}
                    content = [rest] if rest else []
                    if rest:
                        yield {"type": "token", "text": rest}
                else:
                    yield {"type": "token", "text": msg["content"]}
            elif not msg.get("thinking"):
                yield {"type": "tick"}
            if chunk.get("done"):
                final = chunk
    finally:
        messages.pop()  # the note and its excerpts stay out of the saved conversation
    return {"text": strip_reasoning("".join(content)), "tokens": final.get("eval_count", 0),
            "seconds": final.get("eval_duration", 0) / 1e9, "cut_off": final.get("done_reason") == "length"}


# The notes in MEMORY_CHECK's examples: Granite once saved the generator-manual example's note for
# someone's EU2200i manual.
EXAMPLE_NOTES = {n.lower() for n in re.findall(r'"(?:remember": \[|new": )"([^"]+)"', MEMORY_CHECK)}
NOT_NAMES = set("How What When Where Why Which Who Whose Can Could Should Would Will Does Did Do The This That These "
                "Those Is Are Was Were Has Have Had Takes Lives Uses Owns Keeps Their They There Please Also And But "
                "Then Actually Okay Yes Our Its Any Some Every".split())


def _about_them(question, messages, memory):
    """Whether the person's message may say something about them worth a note: "my", a family word or a memory
    request, or someone their notes name ("Leo is allergic to peanuts too"), or he/she right after they spoke about
    their family ("Actually he's 9, not 8")."""
    if PERSONAL.search(question) or asked_memory(question):
        return True
    notes = " ".join(memory.notes())
    if any(re.search(rf"\b{w}\b", notes) for w in set(re.findall(r"\b[A-Z][a-z]{2,}\b", question)) - NOT_NAMES):
        return True
    said = [m["content"] for m in messages if m.get("role") == "user"]
    return bool(re.search(r"\b(he|she|him|her|his|hers)\b|\b(he|she)['’]s\b", question, re.I)) \
        and any(PERSONAL.search(m) for m in said[-5:-1])


def memory_check(model, messages, memory, think, options):
    """Let the model save, change or delete notes about the person, from what they just said (and, for context, a
    few of their earlier messages in this chat)."""
    said = [m["content"].split("\n\nLibrary excerpts:", 1)[0]  # models without tools get excerpts added to questions
            for m in messages if m.get("role") == "user"]
    notes = "\n".join(f"- {n}" for n in memory.notes()) or "(none yet)"
    earlier = "".join(f'Earlier in this chat they said: "{m[:300]}"\n' for m in said[-4:-1])
    fill = {"notes": notes, "earlier": earlier + ("\n" if earlier else ""), "said": said[-1] if said else ""}
    prompt = re.sub(r"\{(notes|earlier|said)\}", lambda m: fill[m.group(1)], MEMORY_CHECK)  # one pass: notes may hold braces
    yield {"type": "memory_check"}
    reply = []
    # The same num_ctx as the answer: a different one would make Ollama load the model again.
    for chunk in _stream(model, [{"role": "user", "content": prompt}], None, think,
                         {**options, "num_predict": 600, "temperature": 0.3}):
        reply.append(chunk.get("message", {}).get("content") or "")
        yield {"type": "tick"}
    changed = False
    plan = memory_plan(strip_reasoning("".join(reply)))
    words = _stems(said[-1] if said else "")
    copied = lambda note: note.strip().lower() in EXAMPLE_NOTES and not _stems(note) <= words  # noqa: E731
    asked = lambda note: _is_question(note, said[-1] if said else "")  # noqa: E731
    plan["remember"] = [n for n in plan["remember"] if not copied(n) and not asked(n)]  # not a fact about them
    plan["change"] = [c for c in plan["change"] if not copied(c["new"]) and not asked(c["new"])]
    for event in memory.apply(forgets(plan, said[-1] if said else "", memory.notes())):
        changed = True
        yield event
    if not changed and asked_memory(said[-1] if said else ""):
        yield {"type": "memory_unchanged"}  # they asked it to remember or forget something, and nothing changed


def _is_question(note, said):
    """Whether a note the model wants to keep is the person's question rather than a fact about them: Lightning saved
    "Where can I safely run a generator?" both times it was asked (an early Windows test)."""
    text, ask = note.strip(" \"'“”‘’"), said.strip(" \"'“”‘’")
    return text.endswith("?") or (ask.endswith("?") and _stems(text) == _stems(ask))


def memory_plan(reply):
    """The model's JSON reply as {"forget": [...], "change": [{"old", "new"}], "remember": [...]}; empty if unreadable.
    Takes the first JSON object in the reply that has any of those keys, and a single item where a list belongs."""
    plan, decoder = {}, json.JSONDecoder()
    # Small slips that make the rest unreadable: a stray quote before the closing brace, a comma before it.
    reply = reply + "\n" + re.sub(r",\s*([}\]])", r"\1", re.sub(r'(\])\s*"\s*(\})', r"\1\2", reply))
    for i, ch in enumerate(reply):
        if ch != "{":
            continue
        try:
            found = decoder.raw_decode(reply, i)[0]
        except ValueError:
            continue
        if isinstance(found, dict) and {"forget", "change", "remember"} & set(found):
            plan = found
            break

    def items(key):
        value = plan.get(key)
        return value if isinstance(value, list) else [value] if isinstance(value, (str, dict)) else []

    texts = lambda key: [x for x in items(key) if isinstance(x, str) and x.strip()]  # noqa: E731
    return {"forget": texts("forget"), "remember": texts("remember"),
            "change": [c for c in items("change") if isinstance(c, dict)
                       and isinstance(c.get("old"), str) and isinstance(c.get("new"), str)]}


def _sources_block(answer):
    """The answer's closing list of sources as (start, end, items), or None. That is the last "Sources" line or heading,
    with any list right under it and at most a short remark after; a lone "Source:" line counts only as the very last
    line, since one higher up may be part of the answer ("Source: rain barrel")."""
    found = list(SOURCES_LINE.finditer(answer))
    if not found:
        return None
    line = found[-1]
    plural = bool(re.match(r"^[*_# ]*Sources", line.group(0), re.IGNORECASE))
    head = re.sub(r"^[*_# ]*Sources?[*_ ]*:?", "", line.group(0), flags=re.IGNORECASE)
    items, end = [p.strip(" *_.") for p in _split_outside_brackets(head)], line.end()
    if plural:
        bare = not [i for i in items if i]  # "Sources:" alone: the rows right under it are its list, bulleted or not
        for row in answer[end:].split("\n")[1:]:
            if LIST_ITEM.match(row):
                items.append(row[LIST_ITEM.match(row).end():].strip(" *_."))
            elif ID_ROW.match(row):  # "[id 1403312] Water storage (Ready.gov)", the way search results list them
                items.append(row.strip(" *_."))
            elif bare and row.strip() and not row.lstrip().startswith("|") \
                    and ("(" in row or not row.rstrip(" *_").endswith((".", "!", "?"))):  # a sentence is the answer's
                items += [p.strip(" *_.") for p in _split_outside_brackets(row)]
            elif row.strip():
                break
            elif bare and any(items):
                break  # a blank line ends a list without bullets
            end = min(len(answer), end + 1 + len(row))
    tail = answer[end:].strip()
    if (tail and not plural) or len(tail) > 300:
        return None
    return line.start(), end, [item for item in items if item]


def cited_sources(answer):
    """What the answer's closing Sources line names, item by item; None if it has none."""
    block = _sources_block(answer)
    return block[2] if block else None


def _split_outside_brackets(text, separators=(";", " | ")):
    """Split text at the separators, but not inside parentheses or brackets."""
    parts, depth, start, i = [], 0, 0, 0
    while i < len(text):
        depth += text[i] in "([" and 1 or text[i] in ")]" and -1 or 0
        sep = next((x for x in separators if depth <= 0 and text.startswith(x, i)), None)
        if sep:
            parts.append(text[start:i])
            start = i = i + len(sep)
            continue
        i += 1
    return parts + [text[start:]]


GENERIC = STOPWORDS | set("manual manuals guide guides guideline guidelines guidance article articles page pages "
                          "section chapter book document entry entries website site library offline pdf via id "
                          "information info recommendation recommendations".split())


def _content_words(text):
    return {w for w in re.findall(r"[a-z0-9]+", re.sub(r"\bid\s*\d+", " ", text.lower())) if w not in GENERIC}


TRAILING_PARENS = re.compile(r"^(.*?)\s*\(((?:[^()]|\([^()]*\))*)\)\s*$")


PUBLISHERS = re.compile(r"\b(mayo|webmd|healthline|cleveland clinic|johns hopkins|harvard|red cross|aap|fda|epa|osha|"
                        r"noaa|usda|nhs|cdc|nih|fema|ready\.?gov|medlineplus|wikem|wikimed|wikipedia|wikihow|ifixit|"
                        r"stack exchange|appropedia|energypedia|ifrc|your files)\b", re.IGNORECASE)


def _claims_source(paren, articles):
    """Whether words in parentheses name who published a source ("Mayo Clinic", "Ready.gov", a library collection)
    rather than describe it ("FEMA's advice on bleach" is caught too; "Emergency Visual Signals" isn't)."""
    words = _content_words(paren)
    return bool(words) and (bool(PUBLISHERS.search(paren) or re.search(r"\bWHO\b", paren))
                            or any(words <= _content_words(a["source"]) for a in articles))


def _names_article(text, article, articles, full=None):
    """Whether one named source is this article: (nearly) its whole title or a shortened form of it, or just its
    collection's name. A publisher in parentheses at the end of the full item must be the article's own."""
    title, source = _content_words(article["title"]), _content_words(article["source"])
    found = TRAILING_PARENS.match(full or text)
    if found and _claims_source(found.group(2), articles) and not _content_words(found.group(2)) <= title | source:
        return False  # "Fever (Mayo Clinic)" when it read MedlinePlus
    found = TRAILING_PARENS.match(text)
    words = _content_words(found.group(1) if found else text)
    if not words:
        return bool(found)
    allowed = 1 if len(title) > 2 else 0  # a short title fits inside many made-up ones ("Fever in Children")
    if title and len(title & words) >= max(1, 0.8 * len(title)) and len(words - title - source) <= allowed:
        return True
    if title and len(words - source) >= 2 and words - source <= title:  # a shortened title
        return True
    return words <= source  # just the collection: fine if it read something from it


def _opened(item, read, read_before=(), opened_ids=(), labels=()):
    """Whether a source the model named is one it opened: by passage id, by title or by its collection's name.
    Models describe a source after a dash or colon; a list of several in one item must check out one by one.
    labels (fetch-quoted only): the passages it was given, by label; an item naming only those ("P1, P3", "[P2]", or
    "P1 (MedlinePlus)" when P1 is from MedlinePlus) is opened, while "P9", "P1 (Made-up Source)" or a title it never
    saw still isn't."""
    if re.match(r"none\b", item.strip(" *_\"'“”‘’").lower()):  # saying it used none is never a made-up source
        return True
    named = re.findall(r"\bP[1-9]\d?\b", item)
    if labels and named and all(n in labels for n in named):
        # "P1 (MedlinePlus)": the brackets right after a label must name that passage's own title or collection
        fits = lambda m: _content_words(m.group(2)) <= _content_words(  # noqa: E731
            f"{labels[m.group(1)].get('title') or ''} {labels[m.group(1)].get('source') or ''}")
        rest = re.sub(r"\b(P[1-9]\d?)[ \t]*\(([^()]*)\)", lambda m: " " if fits(m) else m.group(0), item)
        if not re.search(r"\w", re.sub(r"\bP[1-9]\d?\b|\band\b|\bpassages?\b", " ", rest, flags=re.I)):
            return True
    ids = [int(n) for n in re.findall(r"\b\d{5,}\b", item)]
    if ids and all(i in opened_ids for i in ids):
        return True
    text = re.sub(r"\[([^\]]+)\]\([^)]*\)", r"\1", item)  # a Markdown link: its text, not its address
    text = re.sub(r"https?://\S+", " ", text).strip(" *_.\"'“”‘’")
    articles = list(read) + list(read_before)
    collections = [_content_words(a["source"]) for a in articles]
    # "WikEM: ACE inhibitor dose table": before the colon or dash is just a collection's name, so what comes after
    # names the article, and has to be one it opened (a made-up title passed the check in an early Windows test).
    collection_only = lambda head: bool(_content_words(head)) and any(_content_words(head) <= c for c in collections)  # noqa: E731
    variants = [(text, text)]  # (what names the source, the text whose closing parentheses must fit)
    variants += [(text.split(sep, 1)[0],) * 2 for sep in (" – ", " — ", " - ")  # after a dash: a description
                 if sep in text and not collection_only(text.split(sep, 1)[0])]
    if ": " in text and not collection_only(text.split(": ", 1)[0]):  # a colon may be part of the title, so the
        variants.append((text.split(": ", 1)[0], text))                 # full item's parentheses still count
    if any(_names_article(v.strip(" \"'“”‘’"), a, articles, full) for v, full in variants for a in articles):
        return True
    parts = [p for p in _split_outside_brackets(text, (", ", " and ", " & ")) if p.strip()]
    return len(parts) > 1 and all(_opened(p, read, read_before, opened_ids, labels) for p in parts)


def _read_earlier(messages):
    """Articles opened for earlier questions in this chat ({title, source}), from the tool results kept in it."""
    found = []
    for m in messages:
        if m.get("role") == "tool" and m.get("tool_name") == "read_article":
            first = (m.get("content") or "").split("\n", 1)[0]
            parsed = re.match(r"^(.*) \(((?:[^()]|\([^()]*\))*)\)$", first)
            if parsed:
                found.append({"title": parsed.group(1), "source": parsed.group(2)})
    return found


# A message that asks for information as well as "remember this", and a reply that only acknowledges a memory request.
INFO_ASK = re.compile(r"\?|\b(how (much|many|long|often|do|does|can|should)|what|which|tell me|explain|is it safe|safe "
                      r"to|can (i|he|she|we|they|you)|should (i|he|she|we|they)|doses?|dosage)\b", re.I)
ACKNOWLEDGED = re.compile(r"\b(i'll remember|i will remember|i've noted|noted|got it|i'll keep|i've saved|saved|"
                          r"i've forgotten|forgotten|i'll forget|deleted|removed|no longer|won't remember)\b", re.I)


def _with_sources_line(answer, line):
    """The answer with its closing Sources block (the line and any list right under it) replaced by line, or with
    line added at the end when it has none."""
    block = _sources_block(answer)
    if not block:
        return f"{answer.rstrip()}\n\n{line}"
    after = answer[block[1]:].strip()
    return f"{answer[:block[0]].rstrip()}\n\n{line}" + (f"\n\n{after}" if after else "")


def _known_ids(text, known, every=False):
    """The passage ids text names that are in known (with every: [] unless all its numbers are)."""
    found = [int(n) for n in re.findall(r"\b\d{5,}\b", text) if len(n) < 19]
    hits = [n for n in found if n in known]
    return hits if hits and (not every or len(hits) == len(found)) else []


def _titled(item, by_id):
    """A Sources item that names passages by id, in words: the ids go, and if nothing else is left, the titles of those
    passages take their place ("1403312 (Ready.gov)" becomes "Generator Safety (Ready.gov)"). Numbers that aren't
    passage ids ("Champion 100891 Owner's Manual") stay."""
    passages = []

    def drop(m):
        hits = _known_ids(m.group(0), by_id, every=True)
        passages.extend(by_id[n] for n in hits)
        return " " if hits else m.group(0)

    rest = ID_REF.sub(drop, item)
    for _ in range(2):
        rest = re.sub(r"[(\[][\s,;:*_]*[)\]]", "", rest)  # brackets left empty
        rest = re.sub(r"[\s,;:]+(?=[)\]])|(?<=[(\[])[\s,;:]+", "", rest)  # "(Ready.gov, )"
    rest = re.sub(r"\s{2,}", " ", rest).strip(" ,;:–—-*_#")
    if re.sub(r"\([^)]*\)", "", rest).strip(" ,;:–—-\"'“”*_#"):  # it still names the article itself
        return rest
    named = _one_per_article([p for p in passages if p.get("title")])
    return "; ".join(f"{p['title']} ({p['source']})" for p in named) or rest


def _one_per_article(items):
    """Several passages of one article share its title; list each article once, at its first passage."""
    out, keys = [], set()
    for item in items:
        if (item["title"], item["source"]) not in keys:
            keys.add((item["title"], item["source"]))
            out.append(item)
    return out


def trim(messages, budget=60000):
    """Blank out old tool results so long chats stay inside the context window."""
    total = sum(len(m.get("content") or "") for m in messages)
    for m in messages[1:-6]:
        if total <= budget:
            break
        # A tool's error stays: it is short, and trimmed it would look like a result (_used_ids).
        if m["role"] == "tool" and m["content"] not in TOOL_ERRORS \
                and not m["content"].endswith("[old result removed]"):
            total -= len(m["content"])
            title = m["content"].split("\n", 1)[0] if m.get("tool_name") == "read_article" else ""
            m["content"] = f"{title}\n[old result removed]" if title else "[old result removed]"  # keeps what it read


def installed_models():
    try:
        with ollama_request("/api/tags", timeout=5) as resp:
            return [m["name"] for m in json.load(resp).get("models", [])]
    except (OSError, ValueError, AttributeError, KeyError, TypeError):
        return None  # Ollama is not running, or answered with something that isn't a model list
