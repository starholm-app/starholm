# Third-party notices

Starholm's code is under the MIT License in [LICENSE](LICENSE). That file also keeps the copyright notice of the
open-source project Starholm's code started from, as the MIT License requires. This page lists what else Starholm
uses or ships that other people made, and the license each comes with.

## Library content

The library is plain-text copies of documents from other publishers. Each source keeps its own license. The app
shows that license with every article. [SOURCES.md](SOURCES.md) is the full list, with what each source covers and where it
comes from. In short:

| License | Sources |
|---|---|
| Public domain (U.S. government works) | MedlinePlus (NLM-written pages only), the 1979 text of Nuclear War Survival Skills, CDC Travelers' Health, Ready.gov (FEMA), military medicine manuals, U.S. government survival and first-aid manuals, USDA Complete Guide to Home Canning, the government risk tables (SSA, CDC, NHTSA, NWS) |
| [CC BY-SA](https://creativecommons.org/licenses/by-sa/4.0/) (2.5, 3.0 or 4.0) | Wikipedia, WikiMed, Appropedia, Energypedia, WikiCiv, Gardenology (3.0), Stack Exchange |
| [Open Government Licence v3.0](https://www.nationalarchives.gov.uk/doc/open-government-licence/version/3/) | UK Medicines A to Z (a copy of the nhs.uk medicines pages, not presented as the NHS website) |
| [CC BY-NC-SA 3.0](https://creativecommons.org/licenses/by-nc-sa/3.0/) (non-commercial) | wikiHow (2018 text dump), iFixit |
| [CC BY-NC-SA 3.0 IGO](https://creativecommons.org/licenses/by-nc-sa/3.0/igo/) (non-commercial) | WHO-ICRC Basic Emergency Care, WHO Managing Complications in Pregnancy and Childbirth, WHO Guidelines for Drinking-water Quality. WHO and the ICRC do not endorse Starholm. |
| © IFRC 2020, copies for non-commercial use with the source acknowledged | IFRC International First Aid, Resuscitation and Education Guidelines 2020 |
| Reproduction permitted without further permission; free copies given to emergency services may not be resold; government names and seals only on a complete, unchanged copy | Emergency Response Guidebook 2024 (U.S. DOT/PHMSA, Transport Canada, Mexico's SICT, Argentina's CIQUIME). The library holds its text without seals. |
| Copyright (c) 1986 by Cresson H. Kearny, reproduced under his notice, which is kept in the text | The 1986 additions to Nuclear War Survival Skills |
| Mixed: each document keeps its publisher's rights | Kiwix's five "zimgit" PDF collections, included free with the paid library and in the free library's source picker |

Most of these sources reach Starholm as ZIM files packaged by [Kiwix](https://kiwix.org) and openZIM. Text under a
ShareAlike license stays under that license, credited to its authors. Each article keeps its source
address, and Stack Exchange posts and iFixit guides keep their authors' names.

## Maps

The offline maps are OpenStreetMap map files packaged by Kiwix. Map data © OpenStreetMap contributors, available under
the [Open Database License](https://opendatacommons.org/licenses/odbl/). The vector tiles and map style come from
[OpenFreeMap](https://openfreemap.org) (MIT) and [OpenMapTiles](https://openmaptiles.org) (schema and design under
[CC BY 4.0](https://creativecommons.org/licenses/by/4.0/)). The Maps tab shows "Map data © OpenStreetMap contributors ·
© OpenMapTiles · OpenFreeMap" with every map.

## Code shipped in this repository

| Component | Version | License | Where |
|---|---|---|---|
| [MapLibre GL JS](https://github.com/maplibre/maplibre-gl-js) | 5.24.0 | BSD-3-Clause | `app/static/maplibre-gl-csp.js`, `maplibre-gl-csp-worker.js`, `maplibre-gl.css`; the license is in `app/static/maplibre-gl-LICENSE.txt` |

## Fonts

The optional Starholm font pack uses two typefaces, both under the
[SIL Open Font License 1.1](https://openfontlicense.org/):

| Font | Source |
|---|---|
| Crimson Pro | <https://github.com/Fonthausen/CrimsonPro> |
| Mulish | <https://github.com/googlefonts/mulish> |

The app uses your system font unless you pick the pack. The font files aren't in this repository today. Each one
ships with its `OFL.txt` beside it when it is added.

## Python packages

Installed by `pip` from `requirements.lock` and `requirements-build.lock`, never copied into this repository.

| Package | Version | License |
|---|---|---|
| babel | 2.18.0 | BSD-3-Clause |
| bottle | 0.13.4 | MIT |
| certifi | 2026.10.23 | MPL-2.0 |
| cffi | 2.1.1 | MIT-0 |
| charset-normalizer | 3.5.2 | MIT |
| courlan | 1.4.0 | Apache-2.0 |
| cryptography | 50.0.2 | Apache-2.0 or BSD-3-Clause |
| dateparser | 1.5.0 | BSD-3-Clause |
| htmldate | 1.11.0 | Apache-2.0 |
| justext | 3.0.2 | BSD-2-Clause |
| libzim | 4.1.0 | GPL-3.0-or-later |
| lxml | 6.1.3 | BSD-3-Clause |
| lxml-html-clean | 0.4.6 | BSD-3-Clause |
| proxy-tools | 0.1.0 | MIT |
| pycparser | 3.12 | BSD-3-Clause |
| pyobjc-core and the pyobjc-framework packages (Cocoa, Quartz, Security, UniformTypeIdentifiers, WebKit) | 13.0 | MIT |
| pypdf | 7.0.1 | BSD-3-Clause |
| python-dateutil | 4.0.0 | BSD-3-Clause |
| pytz | 2026.6 | MIT |
| pywebview | 6.2.1 | BSD-3-Clause |
| regex | 2026.11.9 | Apache-2.0 and CNRI-Python |
| tld | 0.13.2 | MPL-1.1, GPL-2.0-only or LGPL-2.1-or-later (your choice) |
| trafilatura | 2.3.1 | Apache-2.0 |
| truststore | 0.10.5 | MIT |
| typing-extensions | 4.16.0 | PSF-2.0 |
| tzdata | 2026.6 | Apache-2.0 |
| tzlocal | 5.4.5 | MIT |
| urllib3 | 2.8.0 | MIT |
| packaging (build only) | 26.3 | Apache-2.0 or BSD-2-Clause |
| setuptools (build only) | 84.2.1 | MIT |
| wheel (build only) | 0.48.0 | MIT |

libzim is GPL-3.0-or-later, and its PyPI wheel includes the libzim C++ library and the libraries it needs. Starholm
imports it in two places: to read Kiwix files when the library is built on your computer, and to read the offline map
files. Starholm's own code stays under the MIT License, which the GPL allows inside a GPL program. The source
repository does not contain libzim; `pip` installs it from PyPI. A packaged app that bundles libzim is a combined work,
so whoever distributes that package must do it under GPL-3.0: ship the GPL text with it and offer the complete source
of the package, libzim included. The MIT code inside it keeps its MIT License.

## The downloadable Mac app

`ops/build-mac-app.sh` builds `Starholm.app`. Besides this repository's code and the Python packages above (of the
build-only ones, `packaging` stays inside the app), the app bundles a Python runtime and two Poppler programs. Their
licence files ship inside the app: CPython's in `Contents/Resources/python/lib/python3.13/LICENSE.txt`, each
package's in its `.dist-info` folder, and Poppler's and FreeType's in `Contents/Resources/poppler/licenses/`.

Because the app bundles libzim (GPL-3.0-or-later), the app as a whole is distributed under GPL-3.0, as the section
above explains. Its complete corresponding source is this repository at the release's commit, the packages pinned in
`requirements.lock` and `requirements-build.lock`, and the upstream releases named below. `ops/build-mac-app.sh`
pins each of those releases by URL and SHA-256.

| Component | Version | License | Where it comes from |
|---|---|---|---|
| [CPython](https://www.python.org) | 3.13.18 | PSF-2.0 | [python-build-standalone](https://github.com/astral-sh/python-build-standalone) release `20261110`, `cpython-3.13.18+20261110-aarch64-apple-darwin-install_only_stripped.tar.gz` |
| OpenSSL, inside that Python | 3.5.9 | Apache-2.0 | the same python-build-standalone release |
| SQLite, inside that Python | 3.53.1 | Public domain | the same |
| libffi, inside that Python | as built by that release | MIT | the same |
| XZ Utils (liblzma), inside that Python | 5.8.4 | 0BSD | the same |
| bzip2, inside that Python | 1.0.8 | bzip2 licence (BSD-style) | the same |
| Expat, inside that Python | 2.10.0 | MIT | the same |
| mpdecimal, inside that Python | 4.0.0 | BSD-2-Clause | the same |
| HACL\* (hash functions), inside that Python | as built by that release | MIT or Apache-2.0 | the same |
| [Poppler](https://poppler.freedesktop.org) (`pdftotext`, `pdfinfo`) | 26.11.0 | GPL-2.0-only or GPL-3.0-only (both licence texts ship with it) | built by `ops/build-mac-app.sh` from <https://poppler.freedesktop.org/poppler-26.11.0.tar.xz>, SHA-256 41c32680160addf5a8dd7a830e4869c01e9f6bee1abb91e44fabd1ba9d7828fb, unchanged |
| [FreeType](https://freetype.org), linked into those two programs | 2.14.3 | FreeType License (FTL) or GPL-2.0, at your choice; used here under the FTL | built from <https://download.savannah.gnu.org/releases/freetype/freetype-2.14.3.tar.xz>, SHA-256 36bc4f1cc413335368ee656c42afca65c5a3987e8768cc28cf11ba775e785a5f, unchanged |

The app does not bundle Tcl/Tk, which the build removes with `tkinter`, or GNU gdbm, which the build removes with
Python's `_dbm` module. zlib, libedit and ncurses come from macOS itself.
CMake and Ninja (`ops/mac-build-tools.lock`) only compile Poppler during the build and are not in the app.
dmgbuild, ds_store and mac_alias (`ops/mac-dmg-tools.lock`, all MIT) only lay out the `.dmg` window and are not in it.

## The downloadable Windows app (not released yet)

`ops/build-windows-app.ps1` builds the Windows app and its installer. It bundles the same kinds of parts as the Mac
app, from `requirements-windows.lock` instead of `requirements.lock`: the same package versions, except that
pywebview needs pythonnet and clr-loader on Windows in place of pyobjc. The app as a whole is distributed under
GPL-3.0 for the same reason as the Mac app. Licence files ship in the install folder: CPython's in
`python\LICENSE.txt`, each package's in its `.dist-info` folder, and Poppler's, FreeType's and zlib's in
`poppler\licenses\`.

| Component | Version | License | Where it comes from |
|---|---|---|---|
| [CPython](https://www.python.org) and the libraries inside it (OpenSSL, SQLite, libffi, XZ Utils, bzip2, Expat, mpdecimal, HACL\*, zlib) | 3.13.18 | PSF-2.0, and each library's licence as listed for the Mac app | [python-build-standalone](https://github.com/astral-sh/python-build-standalone) release `20261110`, `cpython-3.13.18+20261110-x86_64-pc-windows-msvc-install_only_stripped.tar.gz` |
| pythonnet | 3.2.1 | MIT | PyPI, pinned by hash in `requirements-windows.lock` |
| clr-loader | 0.3.1 | MIT | the same |
| Microsoft WebView2 SDK files inside pywebview (`Microsoft.Web.WebView2.Core.dll`, `Microsoft.Web.WebView2.WinForms.dll`, `WebView2Loader.dll`) | as shipped in pywebview 6.2.1 | BSD-3-Clause | pywebview's wheel |
| Poppler (`pdftotext.exe`, `pdfinfo.exe`) | 26.11.0 | GPL-2.0-only or GPL-3.0-only | built by `ops/build-windows-app.ps1` from the same source as the Mac app's, unchanged |
| FreeType, linked into those two programs | 2.14.3 | FreeType License (FTL), as for the Mac app | the same source as the Mac app's, unchanged |
| [zlib](https://zlib.net), linked into those two programs | 1.4 | zlib License | built from <https://github.com/madler/zlib/releases/download/v1.4/zlib-1.4.tar.xz>, SHA-256 4f11c512053d7bab8d9e9b89bde3575add3e4c50a383b42ef6cb837035fc5a7c, unchanged |

The app does not bundle Tcl/Tk, which the build removes with `tkinter`. WebView2 itself, the Microsoft Edge engine
that draws the window, comes with Windows and is not in the app. Inno Setup makes the installer during the build;
the installer it produces holds Inno Setup's own setup and uninstall program, under the Inno Setup License.

## The upgrade key service

The service that signs upgrade keys runs on Cloudflare Workers. Its code isn't part of the public repository. It is built
and tested with these development tools, none of which ship in the app. Licenses are as each package states on npm:

| Package | Version | License |
|---|---|---|
| @cloudflare/vitest-plugin | 1.8.5 | MIT |
| @types/node | 26.6.6 | MIT |
| typescript | 7.0.2 | Apache-2.0 |
| vitest | 4.1.12 | MIT |
| wrangler | 4.165.0 | MIT or Apache-2.0 (your choice) |

## Programs and models you install yourself

Starholm doesn't include an AI model or the program that runs it. It talks to [Ollama](https://ollama.com) (MIT License), which you
install yourself. If you ask it to, Starholm downloads the official Ollama from Ollama's GitHub releases and installs
it for you, unchanged. The model you download through Ollama comes with its own license. Read it on the model's page in
the Ollama library before you use the model. PDF text is read with Poppler's `pdftotext` (GPL), also installed
separately.
