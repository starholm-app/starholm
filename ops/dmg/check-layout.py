#!/usr/bin/env python3
"""Reads a built .dmg's window settings back from its .DS_Store and checks them against ops/dmg/settings.py.

    <python with ds_store> -I ops/dmg/check-layout.py dist/Starholm-1.0.1.dmg

ops/make-dmg.sh runs it after every build, with the dmgbuild tools' Python (ds_store comes with dmgbuild). It mounts
the image read-only and hidden, prints what Finder will show, and exits 1 if anything is off: the app must sit left of
the Applications alias at the same height, the background must be the image's own Retina .background.tiff, and the
toolbar, sidebar and path bar must be hidden.
"""
import json
import plistlib
import subprocess
import sys
import tempfile
from pathlib import Path

from ds_store import DSStore
from mac_alias import Alias

APP = "Starholm.app"
EXPECT = {"window": (660, 400), "icon_size": 128.0, "app": (180, 200), "applications": (480, 200)}


def read(mount):
    if not (mount / ".DS_Store").exists():
        sys.exit("check-layout: the image has no .DS_Store, so Finder opens it with no layout")
    with DSStore.open(str(mount / ".DS_Store"), "r") as store:
        entries = {}
        for e in store:
            entries[(e.filename, e.code.decode())] = e.value
    bwsp = entries[(".", "bwsp")]
    icvp = entries[(".", "icvp")]
    bwsp = bwsp if isinstance(bwsp, dict) else plistlib.loads(bwsp)
    icvp = icvp if isinstance(icvp, dict) else plistlib.loads(icvp)
    nums = [int(float(n)) for n in bwsp["WindowBounds"].replace("{", " ").replace("}", " ").replace(",", " ").split()]
    alias = Alias.from_bytes(icvp["backgroundImageAlias"]) if icvp.get("backgroundImageAlias") else None
    bg = mount / ".background.tiff"
    reps = subprocess.run(["/usr/bin/tiffutil", "-info", str(bg)], capture_output=True, text=True).stdout if bg.exists() else ""
    sizes = []
    for line in reps.splitlines():
        if "Image Width" in line:
            parts = line.replace(":", " ").split()
            sizes.append(f"{parts[parts.index('Width') + 1]}x{parts[parts.index('Length') + 1]}")
    return {
        "window": nums[2:4],
        "window_origin": nums[0:2],
        "show_toolbar": bwsp.get("ShowToolbar"),
        "show_sidebar": bwsp.get("ShowSidebar"),
        "show_pathbar": bwsp.get("ShowPathbar"),
        "show_status_bar": bwsp.get("ShowStatusBar"),
        "view": bytes(entries.get((".", "icvl"), b"")).decode(),  # icnv: icon view
        "icon_size": icvp.get("iconSize"),
        "text_size": icvp.get("textSize"),
        "background_type": icvp.get("backgroundType"),
        "background_alias_target": alias.target.filename if alias else None,
        "background_tiff_sizes": sizes,
        "app": list(entries[(APP, "Iloc")]),
        "applications": list(entries[("Applications", "Iloc")]),
        "applications_is_symlink_to": str(Path(mount / "Applications").readlink()),
    }


def main():
    if len(sys.argv) != 2:
        sys.exit(__doc__)
    dmg = sys.argv[1]
    with tempfile.TemporaryDirectory() as tmp:
        mount = Path(tmp) / "mnt"
        mount.mkdir()
        subprocess.run(["/usr/bin/hdiutil", "attach", "-quiet", "-readonly", "-nobrowse", "-noautoopen",
                        "-mountpoint", str(mount), dmg], check=True)
        try:
            got = read(mount)
        finally:
            subprocess.run(["/usr/bin/hdiutil", "detach", "-quiet", str(mount)], check=False)
    print(json.dumps(got, indent=2))
    problems = []
    if tuple(got["window"]) != EXPECT["window"]:
        problems.append(f"window is {got['window']}, not {EXPECT['window']}")
    if got["icon_size"] != EXPECT["icon_size"]:
        problems.append(f"icon size is {got['icon_size']}")
    if tuple(got["app"]) != EXPECT["app"] or tuple(got["applications"]) != EXPECT["applications"]:
        problems.append(f"icons at {got['app']} and {got['applications']}")
    if not got["app"][0] < got["applications"][0] or got["app"][1] != got["applications"][1]:
        problems.append("the app is not left of Applications on the same row")
    if got["view"] != "icnv":
        problems.append(f"the window opens in {got['view']!r} view, not icon view")
    if got["show_toolbar"] or got["show_sidebar"] or got["show_pathbar"]:
        problems.append("toolbar, sidebar or path bar showing")
    if got["background_type"] != 2 or got["background_alias_target"] != ".background.tiff":
        problems.append("the background is not the image's .background.tiff")
    if sorted(got["background_tiff_sizes"]) != ["1320x800", "660x400"]:
        problems.append(f"the background TIFF holds {got['background_tiff_sizes']}, not 660x400 and 1320x800")
    if got["applications_is_symlink_to"] != "/Applications":
        problems.append("Applications is not a link to /Applications")
    if problems:
        print("check-layout: " + "; ".join(problems), file=sys.stderr)
        return 1
    print("check-layout: the window layout is as ops/dmg/settings.py sets it")
    return 0


if __name__ == "__main__":
    sys.exit(main())
