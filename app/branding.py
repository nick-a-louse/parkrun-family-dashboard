"""Turn a user-supplied logo into a theme-coloured cut-out, sized to match the page text.

Logos often arrive as a dark wordmark on a solid white square. To make the logo
take the page's text colour (and so follow light/dark mode), we build an alpha
mask from it: pixels far from the background colour become opaque, the
background becomes transparent. The page then paints the mask with the text
colour using CSS ``mask-image``.

To size it like text we measure the wordmark's x-height band: the run of rows
where ink spreads across most of the logo's width (the lowercase letters). The
page sets the logo so that band is exactly 1ex tall and its bottom sits on the
text baseline; anything above (a tree, ascenders) or below (descenders) sticks
out naturally, as it would in type.
"""
from __future__ import annotations

import io
import threading
from dataclasses import dataclass
from pathlib import Path

_lock = threading.Lock()
_cache: dict[tuple, "Logo"] = {}


@dataclass
class Logo:
    png: bytes          # trimmed, transparent mask (white on transparent)
    width: int
    height: int
    xheight_top: int    # rows within the trimmed image
    baseline: int

    @property
    def css(self) -> dict:
        """Sizes in ex units, so the lowercase band matches the surrounding text."""
        xh = max(1, self.baseline - self.xheight_top)
        return {
            "width": round(self.width / xh, 3),
            "height": round(self.height / xh, 3),
            "drop": round((self.height - self.baseline) / xh, 3),   # how far it hangs below the baseline
        }


def _background(img) -> tuple[int, int, int, int]:
    w, h = img.size
    corners = [img.getpixel(p) for p in [(0, 0), (w - 1, 0), (0, h - 1), (w - 1, h - 1)]]
    return max(set(corners), key=corners.count)


def build(path: Path, scale: float = 1.0) -> Logo:
    from PIL import Image

    img = Image.open(path).convert("RGBA")
    bg = _background(img)
    px = img.load()
    w, h = img.size
    has_alpha = bg[3] < 250

    # Ink colour: average of the pixels furthest from the background
    def dist(p):
        return abs(p[0] - bg[0]) + abs(p[1] - bg[1]) + abs(p[2] - bg[2])

    if has_alpha:
        alpha = Image.new("L", (w, h))
        a = alpha.load()
        for y in range(h):
            for x in range(w):
                a[x, y] = px[x, y][3]
    else:
        far = max(dist(px[x, y]) for y in range(0, h, 2) for x in range(0, w, 2)) or 1
        alpha = Image.new("L", (w, h))
        a = alpha.load()
        for y in range(h):
            for x in range(w):
                p = px[x, y]
                a[x, y] = 0 if p[3] < 10 else min(255, int(255 * dist(p) / (far * 0.85)))

    box = alpha.point(lambda v: 255 if v > 40 else 0).getbbox()
    if not box:
        raise ValueError("Logo image looks empty")
    alpha = alpha.crop(box)
    w, h = alpha.size

    # x-height band: rows where ink spans most of the width (the lowercase letters)
    a = alpha.load()
    spans = []
    for y in range(h):
        xs = [x for x in range(0, w, 2) if a[x, y] > 100]
        spans.append((xs[-1] - xs[0]) if len(xs) > 1 else 0)
    wide = [y for y, s in enumerate(spans) if s > 0.6 * w]
    if wide:
        # longest consecutive run of wide rows
        runs, start = [], wide[0]
        for prev, cur in zip(wide, wide[1:]):
            if cur != prev + 1:
                runs.append((start, prev))
                start = cur
        runs.append((start, wide[-1]))
        top, base = max(runs, key=lambda r: r[1] - r[0])
    else:  # no clear lowercase band: treat the whole logo as text height
        top, base = 0, h
    if scale and scale != 1.0:  # manual tweak: make the logo larger (>1) or smaller (<1), baseline unchanged
        top = int(base - (base - top) / scale)

    out = Image.new("RGBA", (w, h), (255, 255, 255, 0))
    out.putalpha(alpha)
    buf = io.BytesIO()
    out.save(buf, "PNG", optimize=True)
    return Logo(png=buf.getvalue(), width=w, height=h, xheight_top=top, baseline=base)


def get(path: Path, scale: float = 1.0) -> Logo:
    key = (str(path), path.stat().st_mtime, scale)
    with _lock:
        if key not in _cache:
            _cache.clear()
            _cache[key] = build(path, scale)
        return _cache[key]
