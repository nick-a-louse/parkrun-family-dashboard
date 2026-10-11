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


# ---------------------------------------------------------------- icons

ICON_SIZES = (32, 180, 192, 512)
_icon_cache: dict[tuple, bytes] = {}


def build_icon(path: Path, size: int) -> bytes:
    """A square PNG icon at `size` px from any user-supplied image.

    Trims surrounding empty space to a small even margin, and for small sizes
    thickens thin line art so it stays visible once shrunk (a 664 px drawing with
    8 px lines would otherwise fade to sub-pixel lines at 32 px).
    """
    from PIL import Image, ImageFilter

    img = Image.open(path).convert("RGBA")
    bg = _background(img)
    if bg[3] < 250:  # transparent background: flatten onto white so tabs in any theme can see it
        flat = Image.new("RGBA", img.size, (255, 255, 255, 255))
        flat.alpha_composite(img)
        img, bg = flat, (255, 255, 255, 255)
    rgb = img.convert("RGB")

    # Ink mask: pixels clearly different from the background
    from PIL import ImageChops
    diff = ImageChops.difference(rgb, Image.new("RGB", rgb.size, bg[:3])).convert("L")
    ink = diff.point(lambda v: 255 if v > 60 else 0)
    box = ink.getbbox()
    if not box:
        box = (0, 0, *rgb.size)

    # Square crop around the content with a 6% margin
    x0, y0, x1, y1 = box
    side = int(max(x1 - x0, y1 - y0) * 1.12) or 1
    cx, cy = (x0 + x1) / 2, (y0 + y1) / 2
    canvas = Image.new("RGB", (side, side), bg[:3])
    canvas.paste(rgb.crop((int(cx - side / 2), int(cy - side / 2), int(cx + side / 2), int(cy + side / 2))), (0, 0))
    # paste of an out-of-bounds crop leaves black corners; refill them with the background
    mask_out = Image.new("L", (side, side), 0)
    mask_out.paste(255, (max(0, int(side / 2 - cx)), max(0, int(side / 2 - cy)),
                         min(side, int(side / 2 - cx + rgb.size[0])), min(side, int(side / 2 - cy + rgb.size[1]))))
    canvas = Image.composite(canvas, Image.new("RGB", (side, side), bg[:3]), mask_out)

    # Estimate stroke width: ink area / half its outline length
    cink = ImageChops.difference(canvas, Image.new("RGB", canvas.size, bg[:3])).convert("L").point(
        lambda v: 255 if v > 60 else 0)
    area = sum(1 for v in cink.getdata() if v)
    edge = sum(1 for v in ImageChops.difference(cink, cink.filter(ImageFilter.MinFilter(3))).getdata() if v)
    stroke = (2 * area / edge) if edge else side
    scale = side / size
    wanted = 1.6 * scale            # aim for lines ~1.6 px wide at the output size
    if stroke < wanted and size <= 64:
        grow = int(round((wanted - stroke) / 2)) * 2 + 1
        ink_darker = sum(bg[:3]) > 380  # dark drawing on a light background
        filt = ImageFilter.MinFilter(grow) if ink_darker else ImageFilter.MaxFilter(grow)
        canvas = canvas.filter(filt)

    out = canvas.resize((size, size), Image.LANCZOS)
    buf = io.BytesIO()
    out.save(buf, "PNG", optimize=True)
    return buf.getvalue()


def get_icon(path: Path, size: int) -> bytes:
    key = (str(path), path.stat().st_mtime, size)
    with _lock:
        if key not in _icon_cache:
            if len(_icon_cache) > 16:
                _icon_cache.clear()
            _icon_cache[key] = build_icon(path, size)
        return _icon_cache[key]
