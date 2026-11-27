"""Which way of printing a guide's figures gives ink-friendly pages: a manual check, run on the Mac (it opens an
offscreen WebKit view, so it is not part of tests/run_all.py and needs a logged-in session).

The figures are SVGs shown through <img>, with their own dark colours under @media (prefers-color-scheme: dark). This
prints test pages with WebKit's print operation, as the booklets do (app/server.py render_guide_pdf) and as the
Print button does through pywebview, with the view in dark appearance, then measures how much of each printed page
is dark. Each case prints RUNS times. Result on 13 November 2026 (macOS, WKWebView, dark appearance):

  - the figure as it is: prints with its dark background (about half the page dark)
  - a print rule inside the SVG (@media print): the same; the image keeps the screen's media and appearance
  - the dark rule wrapped in @media screen, color-scheme: light on the <img>, a <picture> print <source>: dark too
    (tried once each while choosing; not repeated here)
  - the same file a second time as name.svg#print, hidden on screen and shown only by the page's print stylesheet:
    light on every run. This is what render.js and app.css do now. In this check the #print copy prints light even
    for a figure without :target rules, because WebKit draws a copy that was never on screen afresh for the print.
    The SVG's own ":target" rules, restating its light colours, make that hold whatever WebKit caches or the
    appearance is, so every figure gets them.

With the whole app.css linked, WebKit printed even the bare figure light on most runs and dark on some, so the page
here carries app.css's two figure rules inline (read from app.css) to keep the cases apart.

Usage: .venv/bin/python tests/figure_print_check.py [guide slug]   (default: preparing-a-home-shelter)
Exit status 0 when the page as render.js builds it prints every figure (ink on the page, so a blank page fails),
light on every run, and the bare figure prints dark (so the check could tell them apart); 1 otherwise."""
import os
import re
import shutil
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
A5 = (419.53, 595.28)
RUNS = 3


def child(folder, page, out_pdf):
    """Print folder/page to out_pdf from an offscreen WKWebView in dark appearance, then end the process."""
    import AppKit
    import Foundation
    import objc
    import WebKit
    from PyObjCTools import AppHelper

    app = AppKit.NSApplication.sharedApplication()
    app.setActivationPolicy_(AppKit.NSApplicationActivationPolicyProhibited)
    keep = {}

    class Done(Foundation.NSObject):
        @objc.typedSelector(b"v@:@Z^v")
        def printOperationDidRun_success_contextInfo_(self, op, ok, ctx):
            AppHelper.stopEventLoop()

    def start():
        dark = AppKit.NSAppearance.appearanceNamed_(AppKit.NSAppearanceNameDarkAqua)
        win = AppKit.NSWindow.alloc().initWithContentRect_styleMask_backing_defer_(
            Foundation.NSMakeRect(-30000, -30000, 600, 850), AppKit.NSWindowStyleMaskBorderless, AppKit.NSBackingStoreBuffered, False)
        win.setReleasedWhenClosed_(False)
        win.setAppearance_(dark)
        wv = WebKit.WKWebView.alloc().initWithFrame_configuration_(Foundation.NSMakeRect(0, 0, 600, 850),
                                                                  WebKit.WKWebViewConfiguration.alloc().init())
        wv.setAppearance_(dark)
        win.setContentView_(wv)
        keep.update(win=win, wv=wv)
        wv.loadFileURL_allowingReadAccessToURL_(Foundation.NSURL.fileURLWithPath_(os.path.join(folder, page)),
                                                Foundation.NSURL.fileURLWithPath_(folder))
        AppHelper.callLater(2.0, paint)  # local files: loaded well within this

    def paint():
        # Draw the page on screen first, as the app has before anyone presses Print: the figure, painted in the dark
        # appearance, is what WebKit then tends to reuse for the print.
        keep["wv"].takeSnapshotWithConfiguration_completionHandler_(None, lambda image, error: AppHelper.callLater(0.5, print_now))

    def print_now():
        info = AppKit.NSPrintInfo.sharedPrintInfo().copy()
        info.setJobDisposition_(AppKit.NSPrintSaveJob)
        info.dictionary()[AppKit.NSPrintJobSavingURL] = Foundation.NSURL.fileURLWithPath_(out_pdf)
        info.setPaperSize_(Foundation.NSMakeSize(*A5))
        for side in ("Top", "Bottom", "Left", "Right"):
            getattr(info, f"set{side}Margin_")(0)
        info.setHorizontalPagination_(AppKit.NSPrintingPaginationModeFit)
        info.setVerticalPagination_(AppKit.NSPrintingPaginationModeAutomatic)
        op = keep["wv"].printOperationWithPrintInfo_(info)
        op.setShowsPrintPanel_(False)
        op.setShowsProgressPanel_(False)
        op.view().setFrame_(Foundation.NSMakeRect(0, 0, A5[0], A5[1]))
        keep["done"] = Done.alloc().init()
        op.runOperationModalForWindow_delegate_didRunSelector_contextInfo_(keep["win"], keep["done"],
                                                                           b"printOperationDidRun:success:contextInfo:", None)

    AppHelper.callAfter(start)
    AppHelper.callLater(30, AppHelper.stopEventLoop)
    AppHelper.runEventLoop(installInterrupt=True)


def dark_share(pdf, below=64):
    """For each page of a PDF, the share of it (0 to 1) that is dark (luminance under below/255; 0.25 by default)."""
    import Foundation
    import Quartz
    doc = Quartz.CGPDFDocumentCreateWithURL(Foundation.NSURL.fileURLWithPath_(pdf))
    shares = []
    for i in range(1, Quartz.CGPDFDocumentGetNumberOfPages(doc) + 1):
        page = Quartz.CGPDFDocumentGetPage(doc, i)
        box = Quartz.CGPDFPageGetBoxRect(page, Quartz.kCGPDFMediaBox)
        w, h = int(box.size.width), int(box.size.height)
        ctx = Quartz.CGBitmapContextCreate(None, w, h, 8, w * 4, Quartz.CGColorSpaceCreateDeviceRGB(),
                                           Quartz.kCGImageAlphaPremultipliedLast)
        Quartz.CGContextSetRGBFillColor(ctx, 1, 1, 1, 1)
        Quartz.CGContextFillRect(ctx, box)
        Quartz.CGContextDrawPDFPage(ctx, page)
        buf = Quartz.CGBitmapContextGetData(ctx).as_buffer(w * h * 4)
        dark = sum(1 for o in range(0, w * h * 4, 16) if 0.2126 * buf[o] + 0.7152 * buf[o + 1] + 0.0722 * buf[o + 2] < below)
        shares.append(dark / (w * h / 4))
    return shares


def main():
    slug = sys.argv[1] if len(sys.argv) > 1 else "preparing-a-home-shelter"
    folder = os.path.join(ROOT, "guides", slug)
    figures = sorted(n for n in os.listdir(folder) if n.endswith(".svg"))
    tmp = tempfile.mkdtemp()
    try:
        # app.css's rules for figures, inline and verbatim, screen and print: linking the whole stylesheet made WebKit
        # print even the bare figure light on most runs, so the check could no longer tell the cases apart.
        css = open(os.path.join(ROOT, "app", "static", "app.css"), encoding="utf-8").read()
        printed = re.search(r"@media print \{(.*)\n\}\s*$", css, re.S).group(1)
        fig = re.compile(r"md-img|\.md img")
        screen_rules = [r for r in re.findall(r"^\.[^{\n]*\{[^}]*\}", css[:css.index("@media print {")], re.M) if fig.search(r.split("{")[0])]
        print_rules = [r.strip() for r in re.findall(r"^  \.[^{\n]*\{[^}]*\}", printed, re.M) if fig.search(r.split("{")[0])]
        if not any("md-img-print" in r for r in screen_rules) or not any("md-img-screen" in r for r in print_rules):
            print("FAIL app.css lacks the figure rules (print copy hidden on screen, screen copy hidden in print)")
            sys.exit(1)
        style = (f"img{{display:block;width:100%;height:auto}} {' '.join(screen_rules)} "
                 f"@media print {{ @page {{ size: A5 portrait; margin: 0; }} {' '.join(print_rules)} }}")
        for n in figures:
            shutil.copy(os.path.join(folder, n), tmp)
            raw = open(os.path.join(folder, n), encoding="utf-8").read()
            with open(os.path.join(tmp, "printrule-" + n), "w", encoding="utf-8") as f:  # a print rule inside the SVG
                f.write(raw.replace("</style>", "@media print{.bg{fill:#ffffff}}\n</style>", 1))
        head = f'<!doctype html><html><head><meta charset="utf-8"><style>{style}</style></head><body>'
        pages = {
            "the figure as it is": "".join(f'<p><img src="{n}"></p>' for n in figures),
            "a print rule inside the SVG": "".join(f'<p><img src="printrule-{n}"></p>' for n in figures),
            "as render.js builds it (screen copy, and name.svg#print for print)": "".join(
                f'<p><img class="md-img md-img-screen" src="{n}"><img class="md-img md-img-print" src="{n}#print"></p>'
                for n in figures),
        }
        results = {}
        for i, (name, body) in enumerate(pages.items()):
            with open(os.path.join(tmp, f"page{i}.html"), "w", encoding="utf-8") as f:
                f.write(f'{head}<div class="reading"><div class="reading-scroll"><article class="reading-col"><div class="md">'
                        f"{body}</div></article></div></div></body></html>")
            results[name] = []
            for run in range(RUNS):
                pdf = os.path.join(tmp, f"page{i}-{run}.pdf")
                subprocess.run([sys.executable, os.path.abspath(__file__), "--child", tmp, f"page{i}.html", pdf], timeout=60, check=True)
                # (dark share of the darkest page, ink on all pages together: anything not near white)
                results[name].append((max(dark_share(pdf)), sum(dark_share(pdf, below=235))))
            print(f"{name}: on each run, the darkest page's dark share and the ink on all pages: "
                  + ", ".join(f"{d * 100:.0f}% dark, {k * 100:.0f}% ink" for d, k in results[name]))
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    bare, built = list(results.values())[0], list(results.values())[2]
    # Each figure must be on the printed page: a blank page is light too. At least 1.5% of a page of ink per figure.
    drawn = all(k >= 0.015 * len(figures) for _, k in built)
    light = all(d < 0.05 for d, _ in built)
    apart = all(d > 0.15 for d, _ in bare)
    ok = drawn and light and apart
    print(f"{'PASS' if drawn else 'FAIL'} {slug}: every figure is on the printed page as render.js builds it")
    print(f"{'PASS' if light else 'FAIL'} {slug}: its figures print light on every run as render.js builds them")
    print(f"{'PASS' if apart else 'FAIL'} the bare figure prints dark, so the check tells the cases apart")
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "--child":
        child(*sys.argv[2:5])
    else:
        main()
