"""The family emergency plan: one small form the household fills in on this Mac (app/static/family-plan.js).

It is kept in family-plan.json in the app's settings folder (app-data/ in the checkout, or $SURVIVAL_LLM_APP_DIR),
readable by this user only (mode 600), and is never sent anywhere: the app reads it to show the form, to print the
plan, and to fill in the meeting place and contacts on the printed emergency pack and wallet cards.

This module is self-contained on purpose, so a later licence switch can turn the feature off in one place.
"""
import json
import os
import time

from memory import _write_file as write_private

MAX_FILE = 200_000  # bytes read from the plan file
MAX_TEXT = 500  # characters in any one field
MAX_PEOPLE = 20
# The plan's fields, in the order the form and the printed page show them: short text. The people's fields are one
# line; the boxes under Who to call, Where to meet and Where things are (MULTILINE) keep their line breaks.
PERSON_FIELDS = ("name", "blood", "medical")
FIELDS = ("contact_outside", "contact_nearby", "meet_near", "meet_away", "shelter", "school_work",
          "documents", "go_bag", "water", "medicines")
MULTILINE = FIELDS


def app_dir():
    """The app's settings folder, as core.APP_DIR and emergency.app_dir() find it."""
    return os.environ.get("SURVIVAL_LLM_APP_DIR") or os.path.join(os.path.dirname(os.path.realpath(__file__)), "app-data")


def path():
    return os.path.join(app_dir(), "family-plan.json")


def _text(value, lines=False):
    """A field's value as clean text: a string, control characters taken out, at most MAX_TEXT characters. One line,
    unless lines: then line breaks stay (as \n), with spaces at the ends of each line trimmed."""
    if not isinstance(value, str):
        return ""
    value = value.replace("\r\n", "\n").replace("\r", "\n")
    keep = "\n" if lines else ""
    clean = "".join(ch if ch in keep else " " if ch in "\t\n" else ch for ch in value if ch in "\t\n" or ord(ch) >= 32)
    if lines:
        clean = "\n".join(line.strip() for line in clean.split("\n"))
    return clean.strip()[:MAX_TEXT]


def clean(body):
    """The plan as it is stored, from what the page sent: only the known fields, each short text (line breaks kept in MULTILINE fields), and up to
    MAX_PEOPLE people (rows left fully empty are dropped); see _text. ValueError when body isn't a plan at all."""
    if not isinstance(body, dict):
        raise ValueError("The plan must be an object.")
    people = body.get("people", [])
    if not isinstance(people, list):
        raise ValueError("people must be a list.")
    if len(people) > MAX_PEOPLE:
        raise ValueError(f"A plan holds at most {MAX_PEOPLE} people.")
    rows = []
    for person in people:
        if not isinstance(person, dict):
            raise ValueError("Each person must be an object.")
        row = {k: _text(person.get(k)) for k in PERSON_FIELDS}
        if any(row.values()):
            rows.append(row)
    return {"people": rows, **{k: _text(body.get(k), k in MULTILINE) for k in FIELDS}}


def empty():
    return clean({})


def load():
    """{"plan": the saved plan (an empty one when none is saved or the file is damaged), "saved": Unix seconds or
    None}."""
    try:
        with open(path(), "rb") as f:
            data = json.loads(f.read(MAX_FILE).decode("utf-8"))
        return {"plan": clean(data.get("plan")), "saved": data.get("saved") if isinstance(data.get("saved"), (int, float)) else None}
    except (OSError, ValueError, UnicodeDecodeError, AttributeError):
        return {"plan": empty(), "saved": None}


def save(body, now=None):
    """Store the plan (clean(body)) and return what load() would now return. ValueError for a body that isn't a plan;
    OSError when the file can't be written."""
    plan = clean(body)
    saved = int(now if now is not None else time.time())
    write_private(path(), json.dumps({"plan": plan, "saved": saved}, ensure_ascii=False, indent=1))
    return {"plan": plan, "saved": saved}
