"""Starholm covers: style C2, "Contours over colour", in the style guide's five cover colours.

Writes one SVG per cover into app/static/covers/, the same art in the light and the dark theme:
  <slug>.svg        a guide on the Guides shelf, from guides/index.json
  read-<name>.svg   the five backgrounds of the Library's "Something to read" row, used in turn

guides/index.json is the one source of cover data: each guide's cover.palette picks its colours and its slug seeds the
drawing, so the same guide always gets the same cover. The SVG files are generated from it and kept in git, so the app
needs no build step. Run this after adding a guide or changing a palette:

    python3 ops/covers.py           # write the covers
    python3 ops/covers.py --check   # exit 1 if any committed cover is missing, stale or extra

Plain Python 3, no packages. A cover is a soft, blurred wash of the cover colour's ground and washes with contour lines
over it, drawn from a generated height field. The colours are the style guide's (docs/design/spec/tokens.css, --cover-*):
Navy, Chocolate, Ember, Sand and Stone. guides/index.json still names the eight older palettes, and PALETTES maps each
to one of the five, as the design system's table does. The text sits on it in the cover colour's ink (app.css .cv-<name>
sets --g-ink), and its contrast is guaranteed by construction: every colour that can end up under the text (each ground
and wash colour, every linear-light mix of two of them, which is what the blur makes, and the contour lines at their
opacity, stacked where lines cross) is checked against the ink; where the worst case is under 4.6 to 1, a band of the
ground colour covers the text zone at the smallest opacity that passes. tests/interface_reader_test.py re-runs this and
checks the committed files match.
"""
import json
import math
import os
import random
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT = os.path.join(ROOT, "app", "static", "covers")
INDEX = os.path.join(ROOT, "guides", "index.json")

W, H = 300, 450              # viewBox; shown at about 150 x 225 CSS px (2x pixels)
TEXT_ZONE = 0.66             # on the shelf, the text never reaches below 66% of the cover's height
MIN_CONTRAST = 4.6           # the target by calculation, a little over the 4.5 the covers must keep

# The five cover colours (docs/design/spec/tokens.css): the ground, three washes, the ink of the text, the contour lines'
# colour and their opacities (minor lines, then every fourth, heavier line). app.css .cv-<name> repeats ground and ink.
SCHEMES = {
    "navy":      {"ground": "#22344F", "wash": ["#2C4163", "#1C2C44", "#34496B"], "ink": "#F5F3EE", "line": "#F5F3EE", "op": (0.16, 0.30)},
    "chocolate": {"ground": "#3A2A20", "wash": ["#4A372A", "#2F221A", "#56402F"], "ink": "#F5F3EE", "line": "#D9CDB8", "op": (0.18, 0.32)},
    "ember":     {"ground": "#8C3E14", "wash": ["#9A4518", "#7A3511", "#A04A1C"], "ink": "#F5F3EE", "line": "#F5F3EE", "op": (0.16, 0.28)},
    "sand":      {"ground": "#D9CDB8", "wash": ["#CFC1A8", "#E4DAC8", "#C8B99C"], "ink": "#3A2A20", "line": "#3A2A20", "op": (0.18, 0.32)},
    "stone":     {"ground": "#DAD6CE", "wash": ["#CFCAC0", "#E6E3DD", "#C6C1B6"], "ink": "#22344F", "line": "#22344F", "op": (0.16, 0.30)},
}
# The palette names guides/index.json uses (app/server.py GUIDE_PALETTES, render.js PALETTES) and their cover colour.
PALETTES = {"ember": "ember", "dune": "sand", "gulf": "navy", "night": "navy", "palm": "stone", "clay": "chocolate",
            "steel": "stone", "dawn": "sand"}
ORDER = ["ember", "dune", "gulf", "night", "palm", "clay", "steel", "dawn"]   # render.js PALETTES
READ_ORDER = ["navy", "sand", "ember", "stone", "chocolate"]                  # render.js READS, the reading row's turn
BLUR = 42


# ---------------------------------------------------------------- colour maths
def hx(c):
    c = c.lstrip("#")
    return tuple(int(c[i:i + 2], 16) / 255 for i in (0, 2, 4))


def tohex(t):
    return "#" + "".join("%02x" % max(0, min(255, round(v * 255))) for v in t)


def lin(v):
    return v / 12.92 if v <= 0.04045 else ((v + 0.055) / 1.055) ** 2.4


def unlin(v):
    return 12.92 * v if v <= 0.0031308 else 1.055 * v ** (1 / 2.4) - 0.055


def lum(t):
    r, g, b = (lin(v) for v in t)
    return 0.2126 * r + 0.7152 * g + 0.0722 * b


def contrast(a, b):
    la, lb = lum(a), lum(b)
    return (max(la, lb) + 0.05) / (min(la, lb) + 0.05)


def over(top, a, bot):  # sRGB "normal" compositing
    return tuple(a * t + (1 - a) * b for t, b in zip(top, bot))


def mixlin(a, b, t):
    return tuple(unlin(lin(x) * (1 - t) + lin(y) * t) for x, y in zip(a, b))


def worst_case(colours, ink, scrim=None):
    """The lowest contrast of the ink over each listed colour and every linear-light mix of any two, under the scrim."""
    ink = hx(ink)
    cs = [hx(c) for c in colours]
    pts = list(cs)
    for i in range(len(cs)):
        for j in range(i + 1, len(cs)):
            pts += [mixlin(cs[i], cs[j], k / 10) for k in range(1, 10)]
    worst = 99
    for p in pts:
        q = over(hx(scrim[0]), scrim[1], p) if scrim else p
        worst = min(worst, contrast(ink, q))
    return worst


def scrim_for(colours, ink, scrim_colour):
    s = 0.0
    while worst_case(colours, ink, (scrim_colour, s) if s else None) < MIN_CONTRAST and s < 0.95:
        s = round(s + 0.02, 2)
    return s


def f1(v):
    return ("%.1f" % v).rstrip("0").rstrip(".")


# ---------------------------------------------------------------- drawing
def blobs(p, rnd):
    """Five large colour shapes, blurred into a soft wash."""
    a, b, c = p
    centres = [(rnd.uniform(0, 120), rnd.uniform(280, 450)), (rnd.uniform(180, 300), rnd.uniform(0, 160)),
               (rnd.uniform(140, 300), rnd.uniform(220, 400)), (rnd.uniform(0, 140), rnd.uniform(40, 200)),
               (rnd.uniform(60, 240), rnd.uniform(380, 470))]
    return "".join(f'<ellipse cx="{f1(cx)}" cy="{f1(cy)}" rx="{f1(rnd.uniform(90, 150))}" ry="{f1(rnd.uniform(80, 140))}" fill="{col}"/>'
                   for col, (cx, cy) in zip([a, b, c, a, b], centres))


def height_field(rnd):
    hills = [(rnd.uniform(-40, 340), rnd.uniform(-40, 490), rnd.uniform(60, 150), rnd.uniform(0.6, 1.4) * rnd.choice([1, 1, -0.6]))
             for _ in range(6)]
    tilt = (rnd.uniform(-1, 1) * 0.002, rnd.uniform(-1, 1) * 0.002)

    def f(x, y):
        return sum(a * math.exp(-((x - hx_) ** 2 + (y - hy_) ** 2) / (2 * s * s)) for hx_, hy_, s, a in hills) + tilt[0] * x + tilt[1] * y
    return f


def rdp(pts, eps):
    """Ramer-Douglas-Peucker: drop points that sit within eps of the line through their neighbours."""
    if len(pts) < 3:
        return pts
    (x0, y0), (x1, y1) = pts[0], pts[-1]
    dx, dy = x1 - x0, y1 - y0
    n = math.hypot(dx, dy) or 1e-9
    i, dmax = 0, 0
    for k in range(1, len(pts) - 1):
        d = (abs(dy * pts[k][0] - dx * pts[k][1] + x1 * y0 - y1 * x0) / n if (dx or dy)
             else math.hypot(pts[k][0] - x0, pts[k][1] - y0))
        if d > dmax:
            i, dmax = k, d
    if dmax > eps:
        return rdp(pts[:i + 1], eps)[:-1] + rdp(pts[i:], eps)
    return [pts[0], pts[-1]]


def contours(f, levels, step=6):
    """Marching squares over a grid a little larger than the cover; each level's segments chained into polylines."""
    nx, ny = W // step + 3, H // step + 3
    xs = [(-step + i * step) for i in range(nx)]
    ys = [(-step + j * step) for j in range(ny)]
    g = [[f(x, y) for x in xs] for y in ys]
    out = {}
    for lv in levels:
        segs = []
        for j in range(ny - 1):
            for i in range(nx - 1):
                v = [g[j][i], g[j][i + 1], g[j + 1][i + 1], g[j + 1][i]]
                pts = [(xs[i], ys[j]), (xs[i + 1], ys[j]), (xs[i + 1], ys[j + 1]), (xs[i], ys[j + 1])]
                cross = []
                for e in range(4):
                    a, b = v[e], v[(e + 1) % 4]
                    if (a < lv) != (b < lv):
                        t = (lv - a) / (b - a)
                        (x0, y0), (x1, y1) = pts[e], pts[(e + 1) % 4]
                        cross.append((round(x0 + t * (x1 - x0), 1), round(y0 + t * (y1 - y0), 1)))
                if len(cross) == 2:
                    segs.append(tuple(cross))
                elif len(cross) == 4:
                    segs += [(cross[0], cross[1]), (cross[2], cross[3])]
        adj = {}
        for k, (p, q) in enumerate(segs):
            adj.setdefault(p, []).append(k)
            adj.setdefault(q, []).append(k)
        used, lines = set(), []
        for k in range(len(segs)):
            if k in used:
                continue
            used.add(k)
            line = list(segs[k])
            for end in (1, 0):
                while True:
                    tip = line[-1] if end else line[0]
                    nxt = [m for m in adj.get(tip, []) if m not in used]
                    if not nxt:
                        break
                    m = nxt[0]
                    used.add(m)
                    p, q = segs[m]
                    nq = q if p == tip else p
                    if end:
                        line.append(nq)
                    else:
                        line.insert(0, nq)
            lines.append(rdp(line, 0.9))
        out[lv] = lines
    return out


def path_d(lines):
    out = []
    for ln in lines:
        if len(ln) < 2:
            continue
        x, y = round(ln[0][0]), round(ln[0][1])
        rel = []
        for px, py in ln[1:]:
            nx_, ny_ = round(px), round(py)
            if (nx_, ny_) != (x, y):
                rel.append(f"{nx_ - x} {ny_ - y}")
                x, y = nx_, ny_
        if rel:
            out.append(f"M{round(ln[0][0])} {round(ln[0][1])}l" + " ".join(rel).replace(" -", "-"))
    return "".join(out)


def cover(scheme, seed, full_scrim=False):
    """One cover's SVG text, its worst-case title contrast by calculation, and the band opacity it needed."""
    sc = SCHEMES[scheme]
    ground, ink, line = sc["ground"], sc["ink"], sc["line"]
    minor_op, major_op = sc["op"]
    rnd = random.Random(seed)
    f = height_field(rnd)
    lo, hi = -0.6, 1.6
    levels = [lo + k * (hi - lo) / 16 for k in range(1, 16)]
    cs = contours(f, levels)
    defs = f'<filter id="b" x="-50%" y="-50%" width="200%" height="200%"><feGaussianBlur stdDeviation="{BLUR}"/></filter>'
    body = f'<rect width="{W}" height="{H}" fill="{ground}"/><g filter="url(#b)">{blobs(sc["wash"], random.Random(seed + 1))}</g>'
    minor = [lv for k, lv in enumerate(levels) if k % 4 != 3]
    major = [lv for k, lv in enumerate(levels) if k % 4 == 3]
    body += f'<path d="{path_d([ln for lv in minor for ln in cs[lv]])}" stroke="{line}" stroke-opacity="{minor_op}" stroke-width="1.1" fill="none"/>'
    body += f'<path d="{path_d([ln for lv in major for ln in cs[lv]])}" stroke="{line}" stroke-opacity="{major_op}" stroke-width="2" fill="none"/>'
    # every colour that can sit under the text: each ground with every combination of line layers on it (crossings)
    layers = [(line, minor_op), (line, major_op)]
    under = []
    for u in [ground] + sc["wash"]:
        for m in range(1 << len(layers)):
            c = hx(u)
            for k, (lc, a) in enumerate(layers):
                if m >> k & 1:
                    c = over(hx(lc), a, c)
            under.append(tohex(c))
    s = scrim_for(under, ink, ground)
    if s > 0:
        stops = (f'<stop offset="0" stop-color="{ground}" stop-opacity="{s}"/><stop offset="1" stop-color="{ground}" stop-opacity="{s}"/>'
                 if full_scrim else
                 f'<stop offset="0" stop-color="{ground}" stop-opacity="{s}"/><stop offset="{TEXT_ZONE}" stop-color="{ground}" stop-opacity="{s}"/>'
                 f'<stop offset=".9" stop-color="{ground}" stop-opacity="0"/>')
        defs += f'<linearGradient id="s" x1="0" y1="0" x2="0" y2="1">{stops}</linearGradient>'
        body += f'<rect width="{W}" height="{H}" fill="url(#s)"/>'
    text = (f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {W} {H}" preserveAspectRatio="xMidYMid slice">'
            f"<defs>{defs}</defs>{body}</svg>\n")
    return text, worst_case(under, ink, (ground, s) if s else None), s


def seed_of(name):
    return sum(map(ord, name)) * 7


def build():
    """Every cover the app needs: {file name: (svg text, worst-case contrast, scrim opacity)}."""
    out = {}
    with open(INDEX, encoding="utf-8") as fh:
        guides = json.load(fh)
    for g in guides:
        palette = (g.get("cover") or {}).get("palette")
        if palette not in PALETTES:
            raise SystemExit(f"{g.get('slug')}: cover.palette {palette!r} is not one of {', '.join(ORDER)}")
        out[f"{g['slug']}.svg"] = cover(PALETTES[palette], seed_of(g["slug"]))
    for scheme in READ_ORDER:   # the reading row: its title sits mid-cover and its source at the foot, so a full-height band
        out[f"read-{scheme}.svg"] = cover(scheme, seed_of(f"read-{scheme}") + 3, full_scrim=True)
    return out


def main(argv):
    covers = build()
    have = set(os.listdir(OUT)) if os.path.isdir(OUT) else set()
    if "--check" in argv:
        bad = sorted(n for n, (t, _, _) in covers.items()
                     if n not in have or open(os.path.join(OUT, n), encoding="utf-8").read() != t)
        extra = sorted(have - set(covers))
        if bad or extra:
            print("stale or missing:", ", ".join(bad) or "none", "| extra:", ", ".join(extra) or "none")
            return 1
        print(f"{len(covers)} covers up to date")
        return 0
    os.makedirs(OUT, exist_ok=True)
    for n in have - set(covers):
        os.remove(os.path.join(OUT, n))
    for n, (t, _, _) in covers.items():
        with open(os.path.join(OUT, n), "w", encoding="utf-8") as fh:
            fh.write(t)
    worst = min(w for _, w, _ in covers.values())
    total = sum(len(t.encode()) for t, _, _ in covers.values())
    print(f"{len(covers)} covers, {total / 1024:.1f} KB, worst-case title contrast {worst:.2f} to 1")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
