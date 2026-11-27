"""Reasoning written out as ordinary content never reaches the person: a draft, then a line that is exactly
"</think>", then the real answer (qwen3.8:27b-mlx did this in the app on 12 November 2026). The stream clears the draft
with a retry event once the line arrives, and the answer, the saved chat, the model's own context, the follow-ups and
the Sources line hold only what follows it. No Ollama: a fake model (core._stream).

Usage: .venv/bin/python tests/backend_reasoning_leak_test.py"""
import http.client
import json
import os
import sys
import tempfile
import threading

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TMP = tempfile.mkdtemp()
os.environ.update(SURVIVAL_LLM_APP_DIR=os.path.join(TMP, "app"), SURVIVAL_LLM_LIBRARY=os.path.join(TMP, "library"),
                  SURVIVAL_LLM_FILES=os.path.join(TMP, "files"))
os.environ.setdefault("SURVIVAL_LLM_DATA", os.path.join(TMP, "data"))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import sandbox  # noqa: E402  (tests/sandbox.py)
sandbox.require()
os.environ.pop("OLLAMA_HOST", None)
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "app"))
import core  # noqa: E402
import server  # noqa: E402

FAILS = []


def check(name, ok, detail=""):
    print(("PASS " if ok else "FAIL ") + name + ("" if ok else f"  {detail}"))
    if not ok:
        FAILS.append(name)


DRAFT = "- Draft: boil it.\n\nFollow-ups:\n- Draft question?\n\nSources: Draft source\n"
REAL = "- Boil it for one minute.\n- Let it cool covered.\n\nFollow-ups:\n- What about bleach?\n\nSources: none"
PIECES = [["- Draft", ": boil it.\n\nFollow-ups:\n- Draft question?\n\nSources: Draft source\n", "</th", "ink>", "\n",
           "\n- Boil it for one minute.\n- Let it cool covered.\n\nFollow-ups:\n- What about bleach?\n\nSources: none"]]


def fake_stream(model, messages, tools, think, options):
    for piece in PIECES[0]:
        yield {"message": {"content": piece}}
    yield {"done": True, "eval_count": 5, "eval_duration": 1_000_000}


core._stream = fake_stream


def run(pieces, library=None):
    PIECES[0] = pieces
    messages = [{"role": "system", "content": "x"}, {"role": "user", "content": "How long do I boil water?"}]
    events = [e for e in core.chat_events("granite4.2:8b", library, messages, caps=set()) if e["type"] != "tick"]
    return events, messages


# ---- the guard on its own ----
check("strip_reasoning drops everything up to and including a '</think>' line",
      core.strip_reasoning(DRAFT + "</think>\n\n" + REAL) == REAL)
check("the last '</think>' line counts", core.strip_reasoning("a\n</think>\nb\n</think>\nc") == "c")
check("'</think>' inside a sentence is left alone", core.strip_reasoning("Close it with </think> here.\nOk.")
      == "Close it with </think> here.\nOk.")
check("no line, no change", core.strip_reasoning(REAL) == REAL)
check("while streaming, a '</think>' line counts only once its newline arrives",
      core.reasoning_end("draft\n</think>", final=False) is None and core.reasoning_end("draft\n</think>\n", final=False)
      == len("draft\n</think>") and core.reasoning_end("draft\n</think>") == len("draft\n</think>"))
check("a '</think>' line ending in a carriage return counts too",
      core.strip_reasoning("draft\r\n</think>\r\nreal") == "real"
      and core.reasoning_end("draft\r\n</think>\r", final=False) is not None)
check("a saved chat's drafts are never sent back to the model",
      server.history({"messages": [{"role": "user", "content": "Q?"},
                                   {"role": "assistant", "content": "draft\n</think>\nreal answer"}]})
      == [{"role": "user", "content": "Q?"}, {"role": "assistant", "content": "real answer"}])
check("a question that happens to hold the line is left as the person wrote it",
      server.history({"messages": [{"role": "user", "content": "a\n</think>\nb"}]})[0]["content"] == "a\n</think>\nb")
check("a partly written '</thi' is nothing yet", core.reasoning_end("draft\n</thi", final=False) is None)

# ---- the stream, the answer, the follow-ups and the model's context ----
events, messages = run(PIECES[0])
types = [e["type"] for e in events]
retry = types.index("retry") if "retry" in types else -1
done = next(e for e in events if e["type"] == "done")
after = "".join(e["text"] for e in events[retry + 1:] if e["type"] == "token")
check("the draft streams, then a retry event (why: reasoning) clears it", retry > 0 and events[retry] ==
      {"type": "retry", "why": "reasoning"} and "token" in types[:retry], types)
check("after the retry, only the real answer streams", after.lstrip("\n") == REAL, repr(after))
check("done.answer is only the real answer", done["answer"].startswith("- Boil it for one minute.")
      and "Draft" not in done["answer"] and "</think>" not in done["answer"], done["answer"])
check("follow-ups come from the real answer only", done["follow_ups"] == ["What about bleach?"], done["follow_ups"])
check("the model's own context keeps only the real answer", "Draft" not in messages[-1]["content"]
      and "</think>" not in messages[-1]["content"], messages[-1])

events, _ = run(["Thinking it over, the library has nothing.\n", "</think>\n",
                 "I couldn't find this in your library.\nCall 998 now."])
done = next(e for e in events if e["type"] == "done")
check("the no-match form is recognised after a draft", done["answer"] ==
      "I couldn't find this in your library.\nCall 998 now.", done["answer"])
events, _ = run([DRAFT, "</think>"])
done = next(e for e in events if e["type"] == "done")
check("'</think>' as the very last line: the draft is cleared, nothing of it is kept",
      "retry" in [e["type"] for e in events] and "Draft" not in done["answer"], done)
events, _ = run([REAL])
check("an answer with no reasoning streams as before, with no retry", "retry" not in [e["type"] for e in events]
      and next(e for e in events if e["type"] == "done")["answer"] == "- Boil it for one minute.\n- Let it cool "
      "covered.\n\nSources: none")
check("the memory step reads past written-out reasoning",
      core.memory_plan(core.strip_reasoning('{"remember": ["draft note"]}\n</think>\n{"remember": ["Has a dog."]}'))
      ["remember"] == ["Has a dog."])

for form in ("I couldn't find this in your library.", "I couldn't find this in your library.\nCall 998 now."):
    events, _ = run([form + "\n\nFollow-ups:\n- One?\n- Two?\n- Three?\n\nSources: none"])
    done = next(e for e in events if e["type"] == "done")
    check(f"no follow-ups with the no-match form {form.splitlines()[-1]!r}", done["follow_ups"] == []
          and done["answer"].startswith(form), done)
events, _ = run(["I couldn't find this in your library, but here is what helps.\n\nFollow-ups:\n- One?\n\nSources: x"])
check("an answer that only starts like the no-match line keeps its follow-ups",
      next(e for e in events if e["type"] == "done")["follow_ups"] == ["One?"])

# ---- through the server: the saved chat holds no draft ----
server.installed_models = lambda: ["granite4.2:8b"]
server.loaded_models = lambda: []
server.start_ollama = lambda: None
server.capabilities = lambda model: set()
httpd = server.make_server(0)
server.PORT = httpd.server_address[1]
server.private_dir(server.APP_DIR)
threading.Thread(target=httpd.serve_forever, daemon=True).start()
PIECES[0] = [DRAFT, "</think>\n", REAL]
chat_id = "c" * 32
conn = http.client.HTTPConnection("127.0.0.1", server.PORT, timeout=30)
conn.request("POST", "/api/chat", body=json.dumps({"conversation": chat_id, "message": "Boil?", "model": "granite4.2:8b"}),
             headers={"X-Token": server.TOKEN, "Content-Type": "application/json"})
stream = conn.getresponse().read().decode()
conn.close()
saved = server.load_chat(chat_id)
answer = saved["messages"][1]
check("the saved chat holds only the real answer and its follow-ups", "Draft" not in answer["content"]
      and answer["follow_ups"] == ["What about bleach?"], answer)
check("the stream carried the retry event", '"why": "reasoning"' in stream)
check("no 'Started over' step is saved for cleared reasoning",
      not any(s.get("why") == "reasoning" for s in answer.get("steps", [])), answer.get("steps"))

print(f"{'all passed' if not FAILS else f'{len(FAILS)} failed'}")
sys.exit(1 if FAILS else 0)
