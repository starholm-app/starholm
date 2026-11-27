# dmgbuild settings for Starholm's .dmg, read by ops/make-dmg.sh (dmgbuild -s ops/dmg/settings.py -D app=<Starholm.app>).
# dmgbuild runs this file with `defines` set from the -D options.
#
# The window: 660 x 400 points, no toolbar, sidebar, path bar or status bar. Starholm sits on the left and the
# Applications alias on the right, both 128 pt, with background.png (and background@2x.png for Retina, which dmgbuild
# joins into one .background.tiff) drawing the arrow and "Drag Starholm to Applications" between and below them.
# ops/dmg/check-layout.py reads the result back from the image's .DS_Store. Change both, and make-background.py, together.
import os.path

HERE = defines["settings_dir"]  # noqa: F821 (dmgbuild provides `defines`)
APP = defines["app"]  # noqa: F821
APP_NAME = os.path.basename(APP.rstrip("/"))

format = "ULMO"          # LZMA-compressed, read-only; macOS 10.15 and later open it
filesystem = "HFS+"
files = [APP]
symlinks = {"Applications": "/Applications"}
hide_extensions = [APP_NAME]

background = os.path.join(HERE, "background.png")
window_rect = ((200, 120), (660, 400))
default_view = "icon-view"
show_toolbar = False
show_sidebar = False
show_pathbar = False
show_status_bar = False
show_tab_view = False
show_icon_preview = False
show_item_info = False
arrange_by = None
icon_size = 128
text_size = 13
icon_locations = {
    APP_NAME: (180, 200),
    "Applications": (480, 200),
}
