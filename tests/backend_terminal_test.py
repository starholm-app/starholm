"""The terminal chat and memory defaults: titles, searches and answers are printed without terminal control characters,
no model is picked by itself, the AI's writes to memory start off, and memory files are mode 600. No Ollama.

Usage: .venv/bin/python tests/backend_terminal_test.py"""
import contextlib
import io
import os
import stat
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TMP = tempfile.mkdtemp()
os.environ.update(SURVIVAL_LLM_APP_DIR=os.path.join(TMP, "app"), SURVIVAL_LLM_LIBRARY=os.path.join(TMP, "library"))
os.environ.setdefault("SURVIVAL_LLM_DATA", os.path.join(TMP, "data"))
os.environ.setdefault("SURVIVAL_LLM_FILES", os.path.join(TMP, "files"))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import sandbox  # noqa: E402  (tests/sandbox.py)
sandbox.require()  # temporary folders only
sys.path.insert(0, ROOT)
import chat  # noqa: E402
import core  # noqa: E402
import memory  # noqa: E402

FAILS = []


def check(name, ok, detail=""):
    print(("PASS " if ok else "FAIL ") + name + ("" if ok else f"  {detail}"))
    if not ok:
        FAILS.append(name)


hostile = "Water\x1b]0;pwned\x07 safety\x1b[2J\r\x9b31m"
check("clean removes every control character", not any(ord(c) < 32 or 127 <= ord(c) < 160
                                                        for c in chat.clean(hostile)), repr(chat.clean(hostile)))
check("clean keeps the words", "Water" in chat.clean(hostile) and "safety" in chat.clean(hostile))
check("clean_text keeps newlines and tabs", chat.clean_text("a\n\tb\x1b[0m") == "a\n\tb[0m", repr(chat.clean_text("a\n\tb\x1b[0m")))


def fake_stream(model, messages, tools, think, options):
    yield {"message": {"content": "Boil it.\x1b[2J Then cool it."}}
    yield {"done": True, "eval_count": 1, "eval_duration": 1}


core._stream = fake_stream
out = io.StringIO()
with contextlib.redirect_stdout(out):
    chat.run_turn("granite4.2:8b", None, [{"role": "system", "content": "s"}, {"role": "user", "content": "Water?"}],
                  "off", set())
printed = out.getvalue()
check("the printed answer has no ESC sequences from the model", "\x1b[2J" not in printed and "Then cool it." in printed,
      repr(printed))

# The app keeps its data inside the checkout by default, never in the home folder
import importlib  # noqa: E402

saved_app_dir = os.environ.pop("SURVIVAL_LLM_APP_DIR")
importlib.reload(core)
check("settings default to app-data/ in the checkout", core.REPO == os.path.realpath(ROOT)
      and core.APP_DIR == os.path.join(core.REPO, "app-data"), (core.REPO, core.APP_DIR))
os.environ["SURVIVAL_LLM_APP_DIR"] = saved_app_dir
importlib.reload(core)
core._stream = fake_stream
check("SURVIVAL_LLM_APP_DIR still overrides it", core.APP_DIR == saved_app_dir)

# The model is chosen by hand: DEFAULT_MODEL until the person picks another; with no default, nothing is picked
check("chosen_model is DEFAULT_MODEL with no setting", core.chosen_model() == core.DEFAULT_MODEL, core.chosen_model())
default_model, core.DEFAULT_MODEL = core.DEFAULT_MODEL, None
check("chosen_model is None with no setting and no default", core.chosen_model() is None, core.chosen_model())
core.DEFAULT_MODEL = default_model
memory._write_file(core.SETTINGS, '{"model": "granite4.2:8b"}')
check("chosen_model reads the app's choice", core.chosen_model() == "granite4.2:8b", core.chosen_model())

# Memory: the AI's writes start off; the files are mode 600
m = memory.Memory(os.path.join(TMP, "app", "mem.txt"), os.path.join(TMP, "app", "s.json"))
check("the AI's writes are off by default", m.ai_writes is False)
check("so a plan from the model changes nothing", list(m.apply(core.memory_plan('{"remember": ["Has a cat."]}'))) == [])
m.set_ai_writes(True)
check("turned on, they work", [e["action"] for e in m.apply(core.memory_plan('{"remember": ["Has a cat."]}'))] == ["add"])
if not sandbox.WINDOWS:  # Windows has no POSIX mode bits or umask
    check("the memory file is mode 600", stat.S_IMODE(os.stat(m.path).st_mode) == 0o600, oct(os.stat(m.path).st_mode))
    check("the settings file is mode 600", stat.S_IMODE(os.stat(m.settings_path).st_mode) == 0o600)
    old = os.umask(0)
    try:
        memory._write_file(os.path.join(TMP, "app", "other.txt"), "x")
    finally:
        os.umask(old)
    check("mode 600 whatever the umask", stat.S_IMODE(os.stat(os.path.join(TMP, "app", "other.txt")).st_mode) == 0o600)
target = os.path.join(TMP, "target.txt")
with open(target, "w") as f:
    f.write("keep")
os.symlink(target, os.path.join(TMP, "app", "linked.txt.part"))
memory._write_file(os.path.join(TMP, "app", "linked.txt"), "new")
check("a planted .part symlink isn't written through", open(target).read() == "keep")

print(f"\n{len(FAILS)} failed" if FAILS else "\nall passed")
sys.exit(1 if FAILS else 0)
