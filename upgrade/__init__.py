"""The optional upgrade: the offline key check (upgrade/key.py), which paid features it unlocks, and the download and
install of the content packs (upgrade/packs.py). See docs/packs.md for the pack format."""

# The website and the help address the Upgrade page links to. Links open in the person's own browser or mail app;
# the app itself never contacts the website.
SITE = "https://starholm.app"
RESEND_URL = SITE + "/resend"
HELP_EMAIL = "hello@starholm.app"

# The guides that stay free without a key. Everything else in the upgrade is listed in docs/packs.md.
FREE_GUIDES = ("cpr-and-choking", "bleeding-burns-and-heat-stroke", "leaving-home-in-a-hurry")
