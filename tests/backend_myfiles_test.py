"""Your files are untrusted input: a file over the size cap, a zip-based file that unpacks too far or has too many
parts, XML with a DOCTYPE, a symlink to a file or folder elsewhere, and a file outside the folder are all refused; a
file that takes too long is given up on; parsing happens in a child process; the index is mode 600. No Ollama.

Usage: .venv/bin/python tests/backend_myfiles_test.py"""
import io
import os
import sqlite3
import stat
import sys
import tempfile
import zipfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TMP = tempfile.mkdtemp()
os.environ.update(SURVIVAL_LLM_APP_DIR=os.path.join(TMP, "app"), SURVIVAL_LLM_LIBRARY=os.path.join(TMP, "library"))
os.environ.setdefault("SURVIVAL_LLM_DATA", os.path.join(TMP, "data"))
os.environ.setdefault("SURVIVAL_LLM_FILES", os.path.join(TMP, "files"))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import sandbox  # noqa: E402  (tests/sandbox.py)
sandbox.require()  # temporary folders only
sys.path.insert(0, ROOT)
from library import myfiles  # noqa: E402

FAILS = []


def check(name, ok, detail=""):
    print(("PASS " if ok else "FAIL ") + name + ("" if ok else f"  {detail}"))
    if not ok:
        FAILS.append(name)


def refused(fn, path, want, *args):
    """Whether fn(path) raises Unreadable with the message want."""
    try:
        fn(path, *args)
    except myfiles.Unreadable as exc:
        return str(exc) == want, str(exc)
    except Exception as exc:  # noqa: BLE001
        return False, repr(exc)
    return False, "no exception"


def docx(path, body_xml, extra=()):
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("word/document.xml", body_xml)
        for name, data in extra:
            z.writestr(name, data)


W = 'xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"'
folder = os.path.realpath(os.path.join(TMP, "files"))  # the app always passes Your files resolved
outside = os.path.join(TMP, "outside")
os.makedirs(folder)
os.makedirs(os.path.join(outside, "dir"))
secret = os.path.join(outside, "secret.txt")
with open(secret, "w") as f:
    f.write("The person's private passwords file.\n")
with open(os.path.join(outside, "dir", "inner.txt"), "w") as f:
    f.write("A file in a folder elsewhere.\n")

small = os.path.join(folder, "water.txt")
with open(small, "w") as f:
    f.write("# Storing water\n\nKeep one gallon per person per day for at least three days.\n")
big = os.path.join(folder, "big.txt")
with open(big, "w") as f:
    f.write("x" * 20_000)
os.symlink(secret, os.path.join(folder, "link.txt"))
os.symlink(os.path.join(outside, "dir"), os.path.join(folder, "linkdir"))
bomb = os.path.join(folder, "bomb.docx")
docx(bomb, f"<w:document {W}><w:body><w:p><w:r><w:t>{'A' * 400_000}</w:t></w:r></w:p></w:body></w:document>")
doctype = os.path.join(folder, "entities.docx")
docx(doctype, f'<?xml version="1.0"?><!DOCTYPE d [<!ENTITY a "aaaa">]><w:document {W}><w:body><w:p><w:r>'
              f"<w:t>&a;</w:t></w:r></w:p></w:body></w:document>")
many = os.path.join(folder, "many.docx")
docx(many, f"<w:document {W}><w:body><w:p><w:r><w:t>Hello there.</w:t></w:r></w:p></w:body></w:document>",
     [(f"extra/{n}.xml", "<x/>") for n in range(20)])
good_docx = os.path.join(folder, "good.docx")
docx(good_docx, f"<w:document {W}><w:body><w:p><w:r><w:t>Turn off the main gas valve if you smell gas.</w:t>"
                f"</w:r></w:p></w:body></w:document>")

myfiles.MAX_FILE_BYTES = 10_000
myfiles.MAX_UNZIPPED = 100_000
myfiles.MAX_ZIP_ENTRIES = 10

# What the folder scan sees
found = myfiles._on_disk(folder)
check("a symlinked file is not listed", "link.txt" not in found, sorted(found))
check("a symlinked folder is not followed", not any(n.startswith("linkdir") for n in found), sorted(found))
check("plain files are listed", {"water.txt", "big.txt", "bomb.docx"} <= set(found), sorted(found))
os.symlink(folder, os.path.join(TMP, "files-link"))
check("a Your files folder that became a link is not read", myfiles._on_disk(os.path.join(TMP, "files-link")) == {})
check("a folder reached through a link on its path is not read", os.path.realpath(TMP) == TMP
      or myfiles._on_disk(os.path.join(TMP, "files")) == {})

# Reading, in a child process
title, text = myfiles.extract_isolated(small, folder)
check("a plain file is read in the child process", title == "Storing water" and "one gallon" in text, (title, text))
title, text = myfiles.extract_isolated(good_docx, folder)
check("a Word file within the caps is read", "main gas valve" in text, text)
ok, got = refused(myfiles.extract_isolated, big, myfiles.TOO_BIG, folder)
check("a file over the size cap is refused", ok, got)
ok, got = refused(myfiles.extract_isolated, os.path.join(folder, "link.txt"), myfiles.NOT_A_FILE, folder)
check("a symlink is refused before reading", ok, got)
ok, got = refused(myfiles.extract, os.path.join(folder, "link.txt"), myfiles.NOT_A_FILE)
check("a symlink is refused when opening too (O_NOFOLLOW)", ok, got)
ok, got = refused(myfiles.extract_isolated, secret, myfiles.NOT_A_FILE, folder)
check("a file outside the folder is refused", ok, got)
ok, got = refused(myfiles.extract_isolated, bomb, myfiles.TOO_BIG_UNPACKED, folder)
check("a Word file that unpacks past the cap is refused (limits reach the child)", ok, got)
ok, got = refused(myfiles.extract_isolated, many, myfiles.TOO_BIG_UNPACKED, folder)
check("a zip with too many parts is refused", ok, got)
ok, got = refused(myfiles.extract_isolated, doctype, myfiles.DAMAGED, folder)
check("XML with a DOCTYPE or entities is refused", ok, got)
ok, got = refused(myfiles.extract_isolated, os.path.join(folder, "missing.txt"), myfiles.DAMAGED, folder)
check("a missing file is refused cleanly", ok, got)

# The same DOCTYPE in UTF-16: the byte check can't see it, the expat pass does
utf16 = os.path.join(folder, "entities16.docx")
docx(utf16, ('<?xml version="1.0" encoding="UTF-16"?><!DOCTYPE d [<!ENTITY a "aaaa">]>'
             f"<w:document {W}><w:body><w:p><w:r><w:t>&a;</w:t></w:r></w:p></w:body></w:document>").encode("utf-16"))
ok, got = refused(myfiles.extract_isolated, utf16, myfiles.DAMAGED, folder)
check("XML with a DOCTYPE in UTF-16 is refused", ok, got)
try:
    myfiles._xml('<?xml version="1.0" encoding="UTF-16"?><!DOCTYPE x><x/>'.encode("utf-16"))
    check("_xml refuses a UTF-16 DOCTYPE in this process too", False, "no exception")
except myfiles.Unreadable:
    check("_xml refuses a UTF-16 DOCTYPE in this process too", True)
check("_xml still reads plain UTF-16 XML", myfiles._xml('<?xml version="1.0" encoding="UTF-16"?><x>hi</x>'
                                                        .encode("utf-16")).text == "hi")

# UTF-16 without a byte-order mark: still refused
utf16le = os.path.join(folder, "entities16le.docx")
docx(utf16le, ('<?xml version="1.0" encoding="UTF-16"?><!DOCTYPE d [<!ENTITY a "aaaa">]>'
               f"<w:document {W}><w:body><w:p><w:r><w:t>&a;</w:t></w:r></w:p></w:body></w:document>")
     .encode("utf-16-le"))
ok, got = refused(myfiles.extract_isolated, utf16le, myfiles.DAMAGED, folder)
check("XML with a DOCTYPE in UTF-16 without a byte-order mark is refused", ok, got)

# A broken title part: the document is read without its title. A DOCTYPE there still refuses the file.
body = (f"<w:document {W}><w:body><w:p><w:r><w:t>Store water in clean, sealed containers.</w:t></w:r></w:p>"
        "</w:body></w:document>")
broken_meta = os.path.join(folder, "broken-title.docx")
docx(broken_meta, body, [("docProps/core.xml", "<cp:coreProperties><dc:title>Unclosed")])
title, text = myfiles.extract_isolated(broken_meta, folder)
check("a document with broken metadata is still read", "sealed containers" in text and title, (title, text))
doctype_meta = os.path.join(folder, "doctype-title.docx")
docx(doctype_meta, body, [("docProps/core.xml", '<?xml version="1.0"?><!DOCTYPE x [<!ENTITY t "T">]><x>&t;</x>')])
ok, got = refused(myfiles.extract_isolated, doctype_meta, myfiles.DAMAGED, folder)
check("a DOCTYPE in the metadata still refuses the file", ok, got)
doctype16_meta = os.path.join(folder, "doctype16-title.docx")
docx(doctype16_meta, body, [("docProps/core.xml", '<?xml version="1.0" encoding="UTF-16"?><!DOCTYPE x><x/>'
                             .encode("utf-16-le"))])
ok, got = refused(myfiles.extract_isolated, doctype16_meta, myfiles.DAMAGED, folder)
check("a UTF-16 DOCTYPE in the metadata still refuses the file", ok, got)

# A parse that needs too much memory is stopped (here the limit is below what Python itself needs to start)
check("the parent can read a process's memory", myfiles._rss_bytes(os.getpid()) > 0)
myfiles.MAX_PARSE_MEMORY = 1024 ** 2
ok, got = refused(myfiles.extract_isolated, small, myfiles.TOO_MUCH_MEMORY, folder)
check("a parse over the memory limit is stopped", ok, got)
myfiles.MAX_PARSE_MEMORY = 2 * 1024 ** 3

# A lying zip header: the size it claims is small, the data is bigger. The budget counts what comes out.
lying = io.BytesIO()
with zipfile.ZipFile(lying, "w", zipfile.ZIP_DEFLATED) as z:
    z.writestr("word/document.xml", "B" * 200_000)
budget = myfiles._Budget()
zf = zipfile.ZipFile(io.BytesIO(lying.getvalue()))
zf.getinfo("word/document.xml").file_size = 10  # what a forged header would say
try:
    budget.read(zf, "word/document.xml")
    check("the unpack budget counts real bytes, not the header", False, "no exception")
except myfiles.Unreadable as exc:
    check("the unpack budget counts real bytes, not the header", str(exc) == myfiles.TOO_BIG_UNPACKED, str(exc))
except zipfile.BadZipFile:  # zipfile itself noticed the size mismatch: also refused
    check("the unpack budget counts real bytes, not the header", True)

# A file that takes too long is given up on (the child can't even start within this limit)
myfiles.PARSE_TIMEOUT = 0.001
ok, got = refused(myfiles.extract_isolated, small, myfiles.TOO_SLOW, folder)
check("a file over the time limit is given up on", ok, got)
myfiles.PARSE_TIMEOUT = 120

# The child runs isolated: python -I, a clean environment, never this process
check("the child is started with python -I", "-I" in myfiles.extract_isolated.__code__.co_consts)

# A full sync (the files are dated a minute ago, so the sync doesn't wait for them to finish copying in)
for name in os.listdir(folder):
    if sandbox.WINDOWS and os.path.islink(os.path.join(folder, name)):
        continue  # Windows can't set a link's own times
    times = (os.path.getmtime(secret) - 60,) * 2
    if sandbox.WINDOWS:
        os.utime(os.path.join(folder, name), times)  # a plain file here; Windows has no follow_symlinks for utime
    else:
        os.utime(os.path.join(folder, name), times, follow_symlinks=False)
# A full sync: the symlink is skipped, the big file is listed with the reason, and the index is mode 600
db = os.path.join(TMP, "app", "my-files.sqlite")
myfiles.sync(folder, db)
items = {it["name"]: it for it in myfiles.items(db)}
check("the sync skips the symlinks", "link.txt" not in items and not any(n.startswith("linkdir") for n in items),
      sorted(items))
check("the sync reads the plain file", items.get("water.txt", {}).get("status") == "ready", items.get("water.txt"))
check("the sync lists the big file with the reason", items.get("big.txt", {}).get("error") == myfiles.TOO_BIG,
      items.get("big.txt"))
check("the sync lists the bomb as unreadable", items.get("bomb.docx", {}).get("status") == "unreadable",
      items.get("bomb.docx"))
if not sandbox.WINDOWS:  # Windows has no POSIX mode bits
    check("the index is mode 600", stat.S_IMODE(os.stat(db).st_mode) == 0o600, oct(os.stat(db).st_mode))
    check("the index's folder is mode 700", stat.S_IMODE(os.stat(os.path.dirname(db)).st_mode) == 0o700,
          oct(os.stat(os.path.dirname(db)).st_mode))
conn = sqlite3.connect(db)
texts = " ".join(r[0].decode("utf-8", "replace") for r in conn.execute("SELECT data FROM files"))
conn.close()
check("nothing from outside the folder reached the index", "passwords" not in texts and "elsewhere" not in texts)

print(f"\n{len(FAILS)} failed" if FAILS else "\nall passed")
sys.exit(1 if FAILS else 0)
