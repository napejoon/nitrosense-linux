"""Build the app icon from NitroSense's own tile (the black NitroSense keycap with its N).

Done at install time from the user's own NitroSense package, so no Acer artwork is
stored in this repository. The N is recoloured red with a glow and the keycap gets a
red halo, like the backlit NitroSense key on the keyboard.

python3 -m nitrosense.iconmaker <source tile png> <icon theme dir, e.g. /usr/share/icons/hicolor>
writes <dir>/<size>x<size>/apps/io.github.napejoon.NitroSense.png for common sizes.
"""

from __future__ import annotations

import os
import sys

import cairo

from . import APP_ID

SIZES = (16, 24, 32, 48, 64, 128, 256, 512)
RED = (1.0, 0.09, 0.07)


def _pixels(surf: cairo.ImageSurface) -> tuple[list[float], list[float], int, int]:
    """-> (alpha 0..1, luminance 0..255 unpremultiplied) per pixel."""
    surf.flush()
    w, h, stride = surf.get_width(), surf.get_height(), surf.get_stride()
    data = surf.get_data()
    alpha, lum = [0.0] * (w * h), [0.0] * (w * h)
    for y in range(h):
        row = y * stride
        for x in range(w):
            b, g, r, a = data[row + 4 * x: row + 4 * x + 4]
            i = y * w + x
            alpha[i] = a / 255
            lum[i] = (0.3 * r + 0.59 * g + 0.11 * b) * 255 / a if a else 0.0
    return alpha, lum, w, h


def _blur(mask: list[float], w: int, h: int, radius: int, passes: int = 3) -> list[float]:
    """Separable box blur (3 passes ~ gaussian)."""
    cur = mask[:]
    for _ in range(passes):
        tmp = [0.0] * (w * h)
        for y in range(h):
            row = y * w
            acc = sum(cur[row + min(max(i, 0), w - 1)] for i in range(-radius, radius + 1))
            for x in range(w):
                tmp[row + x] = acc / (2 * radius + 1)
                acc += cur[row + min(x + radius + 1, w - 1)] - cur[row + max(x - radius, 0)]
        out = [0.0] * (w * h)
        for x in range(w):
            acc = sum(tmp[min(max(i, 0), h - 1) * w + x] for i in range(-radius, radius + 1))
            for y in range(h):
                out[y * w + x] = acc / (2 * radius + 1)
                acc += tmp[min(y + radius + 1, h - 1) * w + x] - tmp[max(y - radius, 0) * w + x]
        cur = out
    return cur


def _mask_surface(mask: list[float], w: int, h: int, rgb, gain: float = 1.0) -> cairo.ImageSurface:
    surf = cairo.ImageSurface(cairo.FORMAT_ARGB32, w, h)
    stride = surf.get_stride()
    buf = bytearray(stride * h)
    for y in range(h):
        for x in range(w):
            a = min(1.0, mask[y * w + x] * gain)
            o = y * stride + 4 * x
            buf[o:o + 4] = bytes((int(rgb[2] * a * 255), int(rgb[1] * a * 255),
                                  int(rgb[0] * a * 255), int(a * 255)))
    surf.get_data()[:] = buf
    surf.mark_dirty()
    return surf


def build(source_png: str, size: int = 256) -> cairo.ImageSurface:
    src = cairo.ImageSurface.create_from_png(source_png)
    work = 256
    # keycap scaled to 82 % so the halo fits inside the icon
    cap = cairo.ImageSurface(cairo.FORMAT_ARGB32, work, work)
    cr = cairo.Context(cap)
    k = 0.82 * work / max(src.get_width(), src.get_height())
    cr.translate((work - src.get_width() * k) / 2, (work - src.get_height() * k) / 2)
    cr.scale(k, k)
    cr.set_source_surface(src, 0, 0)
    cr.paint()

    alpha, lum, w, h = _pixels(cap)
    # the N is the light part of the keycap (body ~30-45, N face ~90-115, bevel up to white)
    n_mask = [min(1.0, max(0.0, (lum[i] - 62) / 40)) * alpha[i] for i in range(w * h)]
    n_shade = [min(1.0, max(0.0, (lum[i] - 62) / 150)) for i in range(w * h)]
    halo = _blur(alpha, w, h, 7)
    n_glow = _blur(n_mask, w, h, 4)

    out = cairo.ImageSurface(cairo.FORMAT_ARGB32, work, work)
    cr = cairo.Context(out)
    cr.set_source_surface(_mask_surface(halo, w, h, RED, 0.9), 0, 0)   # red halo
    cr.paint()
    cr.set_source_surface(cap, 0, 0)                                    # keycap
    cr.paint()
    # N: red, keeping the bevel shading, with a soft glow
    cr.set_source_surface(_mask_surface(n_glow, w, h, RED, 1.4), 0, 0)
    cr.paint()
    stride = out.get_stride()
    out.flush()
    data = out.get_data()
    for y in range(h):
        for x in range(w):
            i = y * w + x
            m = n_mask[i]
            if m <= 0:
                continue
            o = y * stride + 4 * x
            b, g, r, a = data[o:o + 4]
            shade = 0.55 + 0.45 * n_shade[i]
            nr, ng, nb = (RED[0] * shade + 0.25 * n_shade[i], RED[1] * shade + 0.2 * n_shade[i] ** 2,
                          RED[2] * shade + 0.2 * n_shade[i] ** 2)
            na = a / 255
            data[o:o + 4] = bytes((
                int(b * (1 - m) + min(1.0, nb) * 255 * na * m),
                int(g * (1 - m) + min(1.0, ng) * 255 * na * m),
                int(r * (1 - m) + min(1.0, nr) * 255 * na * m), a))
    out.mark_dirty()
    if size == work:
        return out
    scaled = cairo.ImageSurface(cairo.FORMAT_ARGB32, size, size)
    cr = cairo.Context(scaled)
    cr.scale(size / work, size / work)
    cr.set_source_surface(out, 0, 0)
    cr.get_source().set_filter(cairo.FILTER_BEST)
    cr.paint()
    return scaled


def main(argv: list[str]) -> int:
    if len(argv) != 2:
        print(__doc__)
        return 2
    source, outdir = argv
    base = build(source, 256)
    for size in SIZES:
        if size == 256:
            img = base
        else:
            img = cairo.ImageSurface(cairo.FORMAT_ARGB32, size, size)
            cr = cairo.Context(img)
            cr.scale(size / 256, size / 256)
            cr.set_source_surface(base, 0, 0)
            cr.get_source().set_filter(cairo.FILTER_BEST)
            cr.paint()
        path = os.path.join(outdir, f"{size}x{size}", "apps", f"{APP_ID}.png")
        os.makedirs(os.path.dirname(path), exist_ok=True)
        img.write_to_png(path)
    print(f"icon written for sizes {', '.join(map(str, SIZES))}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
