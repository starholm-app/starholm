"""The Mac window opens where it was closed (app/server.py: frame_record, restored_geometry, save_window_frame,
window_place). With two screens, "Restart now" used to open the window on whichever screen macOS called main. Now the
frame, the size inside the title bar and the screen are kept in window-frame.json on close and on quit, and the window
opens there again, but only when a screen with exactly that frame is still connected. The whole window, title bar
included, stays inside that screen's visible frame (below the menu bar, clear of the Dock), and restarting again and
again never changes its size or place. Elsewhere, or with nothing saved, the window opens centred as before.
Stand-ins for pywebview, AppKit's screens and the NSWindow: no window opens.

Usage: .venv/bin/python tests/window_frame_test.py"""
import json
import os
import sys
import tempfile
from types import SimpleNamespace

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
TMP = tempfile.mkdtemp()
for name, sub in (("SURVIVAL_LLM_APP_DIR", "app"), ("SURVIVAL_LLM_DATA", "data"),
                  ("SURVIVAL_LLM_LIBRARY", "library"), ("SURVIVAL_LLM_FILES", "files")):
    os.environ.setdefault(name, os.path.join(TMP, sub))
sys.path.insert(0, HERE)
import sandbox  # noqa: E402  (tests/sandbox.py)
sandbox.require()
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "app"))
import server  # noqa: E402

failures = []


def check(name, ok, detail=""):
    print(("ok   " if ok else "FAIL ") + name + (f": {detail}" if detail and not ok else ""))
    if not ok:
        failures.append(name)


TITLE = 28  # a Mac title bar
# macOS coordinates: origin at the bottom left of the main screen, y going up. Each screen: (frame, visible frame).
MAIN = ((0, 0, 1512, 982), (0, 0, 1512, 949))  # a laptop: 33 pt menu bar, the Dock hidden
LEFT = ((-2560, -200, 2560, 1440), (-2560, -130, 2560, 1345))  # an external screen to its left: menu bar 25, Dock 70


def window_at(screen, x, y, width, height):
    """A window as pywebview's create_window places it: its top-left corner x, y from the screen's top left, width and
    height inside the title bar. Returns its (frame, content) in global coordinates."""
    sx, sy, sw, sh = screen[0]
    top = sy + sh - y
    return (sx + x, top - height - TITLE, width, height + TITLE), (sx + x, top - height - TITLE, width, height)


def visible_ok(screen, frame):
    vx, vy, vw, vh = screen[1]
    x, y, w, h = frame
    return vx <= x and x + w <= vx + vw and vy <= y and y + h <= vy + vh


# A window on the external screen, 100 across and 50 down from its top left, 1200 x 900 inside the title bar.
frame, content = window_at(LEFT, 100, 50, 1200, 900)
saved = server.frame_record(frame, content, LEFT[0])
check("frame_record keeps the frame, the inside size and the screen",
      saved == {"frame": list(frame), "content": [1200, 900], "screen": list(LEFT[0])}, saved)
check("frame_record rounds to whole points",
      server.frame_record((1.4, 2.6, 3.5, 40.4), (0, 0, 3.5, 12.4), MAIN[0])["content"] == [4, 12])
place = server.restored_geometry(saved, [MAIN, LEFT])
check("the window goes back to the external screen, at the same size inside",
      place == {"screen": 1, "x": 100, "y": 50, "width": 1200, "height": 900}, place)
check("the screen's order in the list doesn't matter", server.restored_geometry(saved, [LEFT, MAIN])["screen"] == 0)
check("that screen unplugged: opens as usual", server.restored_geometry(saved, [MAIN]) is None)
check("screens rearranged: opens as usual",
      server.restored_geometry(saved, [MAIN, ((-2560, 0, 2560, 1440), (-2560, 70, 2560, 1345))]) is None)

# Restart after restart: what the window is saved as must give back the same window. Saving the frame (title bar
# included) as create_window's size grew the window by the title bar each time, until the clamp put it under the
# menu bar.
for screen, start in ((MAIN, (40, 60, 1100, 800)), (MAIN, (0, 33, 1512, 949 - TITLE)), (LEFT, (100, 50, 1200, 900))):
    frame, content = window_at(screen, *start)
    places = []
    for _ in range(5):
        place = server.restored_geometry(server.frame_record(frame, content, screen[0]), [screen])
        places.append(place)
        frame, content = window_at(screen, place["x"], place["y"], place["width"], place["height"])
    check(f"five restarts change nothing ({start})", all(p == places[0] for p in places) and
          (places[0]["x"], places[0]["y"], places[0]["width"], places[0]["height"]) == start, places)
    check(f"the whole window is inside the visible frame ({start})", visible_ok(screen, frame), frame)

# A window as big as the screen, or partly under the menu bar: the whole window, title bar included, comes back inside
# the visible frame.
for frame in ((-100, -100, 4000, 3000), (200, 900, 800, 600), (1400, -400, 800, 600)):
    content = (frame[0], frame[1], frame[2], frame[3] - TITLE)
    place = server.restored_geometry(server.frame_record(frame, content, MAIN[0]), [MAIN])
    placed, _ = window_at(MAIN, place["x"], place["y"], place["width"], place["height"])
    check(f"kept below the menu bar and inside the screen ({frame})", visible_ok(MAIN, placed), placed)
place = server.restored_geometry(server.frame_record((10, 10, 50, 80), (10, 10, 50, 52), MAIN[0]), [MAIN])
check("never smaller than the window's minimum", (place["width"], place["height"]) == server.WINDOW_MIN, place)
for bad in ({}, {"frame": [1, 2, 3], "content": [1, 1], "screen": list(MAIN[0])},
            {"frame": "x", "content": [1, 1], "screen": list(MAIN[0])},
            {"frame": [0, 0, 0, 0], "content": [0, 0], "screen": list(MAIN[0])},
            {"frame": [0, 0, 800, 600], "content": [800, 700], "screen": list(MAIN[0])},
            {"frame": [0, 0, 800, 600], "screen": list(MAIN[0])}, None, []):
    check(f"a damaged record opens as usual ({bad!r})", server.restored_geometry(bad, [MAIN]) is None)


# save_window_frame and window_place, with a stand-in NSWindow, AppKit screens and pywebview.
def ns_rect(x, y, w, h):
    return SimpleNamespace(origin=SimpleNamespace(x=x, y=y), size=SimpleNamespace(width=w, height=h))


frame, content = window_at(LEFT, 100, 50, 1200, 900)
ns = SimpleNamespace(frame=lambda: ns_rect(*frame), contentRectForFrameRect_=lambda f: ns_rect(*content),
                     screen=lambda: SimpleNamespace(frame=lambda: ns_rect(*LEFT[0])))
server.native_window = lambda window: ns
server.mac_screens = lambda: [MAIN, LEFT]
screens = [SimpleNamespace(x=s[0][0], y=s[0][1], width=s[0][2], height=s[0][3]) for s in (MAIN, LEFT)]
fake_webview = SimpleNamespace(screens=screens)

server.PLATFORM = "darwin"
check("nothing saved yet: opens as usual", server.window_place(fake_webview) == {})
server.save_window_frame(object())
with open(server.WINDOW_FRAME, encoding="utf-8") as f:
    check("save_window_frame keeps the frame, the inside size and the screen", json.load(f) == saved)
if not sandbox.WINDOWS:
    check("window-frame.json is private (600)", os.stat(server.WINDOW_FRAME).st_mode & 0o777 == 0o600)
place = server.window_place(fake_webview)
check("window_place gives create_window the screen, the place and the inside size",
      place == {"screen": screens[1], "x": 100, "y": 50, "width": 1200, "height": 900}, place)
check("webview.screens as a function works too", server.window_place(SimpleNamespace(screens=lambda: screens)) == place)
server.mac_screens = lambda: [MAIN]
check("AppKit and pywebview disagree on the screens: opens as usual", server.window_place(fake_webview) == {})
server.mac_screens = lambda: [MAIN, LEFT]

ns.screen = lambda: None  # a window with no screen (being closed): keeps the last record
server.save_window_frame(object())
with open(server.WINDOW_FRAME, encoding="utf-8") as f:
    check("a window with no screen leaves the record alone", json.load(f) == saved)
server.native_window = lambda window: None
server.save_window_frame(object())  # must not raise

with open(server.WINDOW_FRAME, "w", encoding="utf-8") as f:
    f.write("{not json")
check("a damaged file opens as usual", server.window_place(fake_webview) == {})

server.PLATFORM = "win32"
check("Windows: window_place does nothing", server.window_place(fake_webview) == {})
os.remove(server.WINDOW_FRAME)
server.native_window = lambda window: ns
ns.screen = lambda: SimpleNamespace(frame=lambda: ns_rect(*LEFT[0]))
server.save_window_frame(object())
check("Windows: save_window_frame writes nothing", not os.path.exists(server.WINDOW_FRAME))

if failures:
    print(f"\n{len(failures)} failed")
    sys.exit(1)
print("\nall passed")
