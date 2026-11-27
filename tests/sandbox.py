"""Make sure a test only ever uses temporary folders (not a test itself: run_all.py runs *_test.py files only).

Importing library/search.py or core.py reads the app's settings and marks the live library build as in use, and the
server writes settings, chats and indexes. A test calls require() before importing any of them: it stops the test
unless the settings, data, library and files folders are all set to folders inside the system's temporary folder.
run_all.py sets them for every test; a test run on its own must set them itself (or be run through run_all.py).
"""
import os
import sys
import tempfile

FOLDERS = ("SURVIVAL_LLM_APP_DIR", "SURVIVAL_LLM_DATA", "SURVIVAL_LLM_LIBRARY", "SURVIVAL_LLM_FILES")


def require():
    temp = os.path.realpath(tempfile.gettempdir())
    bad = [name for name in FOLDERS
           if not os.environ.get(name) or not os.path.realpath(os.environ[name]).startswith(temp + os.sep)]
    if bad:
        sys.exit(f"Refusing to run: {', '.join(bad)} must point inside {temp}, so this test can't touch the app's real "
                 "folders. Run it through tests/run_all.py.")


WINDOWS = sys.platform == "win32"


def mac_only(reason):
    """End a test as skipped on Windows when everything it checks is a macOS mechanism (an .app bundle, /usr/bin/open,
    launchctl, ditto, Quartz). A test that checks some things that apply on Windows too guards only the macOS checks,
    with `if not sandbox.WINDOWS:`. Windows gives no POSIX mode bits (600, 700), so mode checks are guarded that way."""
    if WINDOWS:
        print(f"SKIP on Windows: {reason}")
        sys.exit(0)
