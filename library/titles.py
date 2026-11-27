"""The title helpers the app and the free build need, whether or not this copy holds the cleaning rules.

library/cleanup.py holds the cleaning rules and stays in the private repository. The app's Library page, reader and
the free (raw) build only need its title helpers: sort_key(), sort_letter(), display_title(), OTHERS_MARK and a few
more. cleanup() returns library/cleanup.py when it is here, so a private build works exactly as before. Without it
(the public repository) it returns this module, whose helpers are plain versions with no per-source rules. A name
that only the cleaning rules have (clean_document(), clean_title() ...) raises CleaningRulesMissing, which only a
non-raw build reaches: the free library converts with raw=True and never calls them.
"""
import importlib
import json
import os
import re
import sys
import unicodedata

HERE = os.path.dirname(os.path.abspath(__file__))
SWITCH_FILE = os.path.join(HERE, "source-switch.json")


class CleaningRulesMissing(RuntimeError):
    """A cleaning rule was asked for in a copy without library/cleanup.py."""


def cleanup():
    """library/cleanup.py when this copy has it, else this module's plain stand-ins. Only a missing cleanup.py
    falls back: an import error inside it is raised, so a broken private build never runs without its rules."""
    try:
        return importlib.import_module("library.cleanup")  # library/ is a namespace package: import by full name
    except ModuleNotFoundError as exc:
        if exc.name != "library.cleanup":
            raise
        return sys.modules[__name__]


OTHERS_MARK = "#"  # a sort key that does not start with a-z starts with this, so "#" titles come before A
PDF_GROUP = ()  # no collections are deduplicated without the rules


def sources_off(path=None):
    """The keys switched off in source-switch.json ({"off": [keys]}); none when it is missing or unreadable."""
    try:
        with open(path or SWITCH_FILE, encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, ValueError):
        return frozenset()
    off = data.get("off") if isinstance(data, dict) else None
    return frozenset(k for k in off if isinstance(k, str)) if isinstance(off, list) else frozenset()


def trim_tiers(path=None):
    """No Wikipedia trim without its rules."""
    return frozenset()


def sort_key(key, title):
    """Lower case, accents and leading punctuation removed, numbers compared by value; "#" first when the key does
    not start with a-z."""
    s = " ".join(str(title or "").split())
    while s and (not s[0].isalnum() or unicodedata.category(s[0]) == "Lm"):
        s = s[1:]
    s = "".join(c for c in unicodedata.normalize("NFKD", s.lower()) if not unicodedata.combining(c))
    s = re.sub(r"\d+", lambda m: m.group().zfill(10), s)
    if not ("a" <= s[:1] <= "z"):
        s = OTHERS_MARK + s
    return s


def sort_letter(key):
    """The letter a sort key sits under in the A to Z bar: "A" to "Z", or "#"."""
    c = key[:1]
    return c.upper() if "a" <= c <= "z" else OTHERS_MARK


def display_title(key, title):
    """A title as the app shows it: as stored."""
    return title or ""


def file_title(path):
    """A title from a file name: "files/Foraging_(1).pdf" -> "Foraging (1)"."""
    name = os.path.basename(str(path or ""))
    return " ".join(re.sub(r"(?i)\.pdf$", "", name).replace("_", " ").split())


def content_key(text):
    """The text without any whitespace."""
    return re.sub(r"\s+", "", text)


def wikihow_spacing(text):
    """wikiHow text as the dump has it."""
    return text


def __getattr__(name):
    if name.startswith("__"):
        raise AttributeError(name)
    raise CleaningRulesMissing(f"library/cleanup.py is not in this copy, so cleanup.{name} is unavailable; "
                               "only the free (raw) library can be built here")
