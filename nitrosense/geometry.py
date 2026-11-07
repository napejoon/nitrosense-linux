"""WPF binary StreamGeometry (BAML Path.Data) -> SVG path data, and cairo rendering."""

from __future__ import annotations

import math
import re
import struct

_BEGIN, _LINE, _QUAD, _BEZ, _POLYLINE, _POLYQUAD, _POLYBEZ, _ARC, _CLOSED, _FILLRULE = range(10)


def decode_path_data(hexs: str) -> tuple[str, str | None]:
    b = bytes.fromhex(hexs)
    p = 0
    out: list[str] = []
    fill_rule = None

    def typed() -> float:
        nonlocal p
        t = b[p]
        p += 1
        if t == 1:
            return 0.0
        if t == 2:
            return 1.0
        if t == 3:
            return -1.0
        if t == 4:
            v, = struct.unpack_from("<i", b, p)
            p += 4
            return v / 1e6
        if t == 5:
            v, = struct.unpack_from("<d", b, p)
            p += 8
            return v
        raise ValueError(f"bad number tag {t}")

    def rd(scaled: int) -> float:
        nonlocal p
        if scaled:
            v, = struct.unpack_from("<i", b, p)
            p += 4
            return v / 1e6
        return typed()

    def pt(flags: int) -> tuple[float, float]:
        return rd(flags & 0x40), rd(flags & 0x80)

    def fmt(points) -> str:
        return " ".join(f"{x:.6g},{y:.6g}" for x, y in points)

    while p < len(b):
        op = b[p]
        p += 1
        code, fl = op & 0x0F, op & 0xF0
        if code == _BEGIN:
            out.append("M" + fmt([pt(fl)]))
        elif code in (_LINE, _QUAD, _BEZ):
            n = {_LINE: 1, _QUAD: 2, _BEZ: 3}[code]
            pts = [pt(fl)] + [(typed(), typed()) for _ in range(n - 1)]
            out.append({_LINE: "L", _QUAD: "Q", _BEZ: "C"}[code] + fmt(pts))
        elif code in (_POLYLINE, _POLYQUAD, _POLYBEZ):
            cnt, = struct.unpack_from("<i", b, p)
            p += 4
            pts = [(typed(), typed()) for _ in range(cnt)]
            out.append({_POLYLINE: "L", _POLYQUAD: "Q", _POLYBEZ: "C"}[code] + fmt(pts))
        elif code == _ARC:
            x, y = pt(fl)
            w, h = typed(), typed()
            rot = typed()
            f2 = b[p]
            p += 1
            out.append(f"A{w:.6g},{h:.6g} {rot:.6g} {f2 & 1},{(f2 >> 1) & 1} {x:.6g},{y:.6g}")
        elif code == _CLOSED:
            if fl & 0x10:
                out.append("Z")
        elif code == _FILLRULE:
            fill_rule = "nonzero" if fl & 0x10 else "evenodd"
        else:
            raise ValueError(f"unknown geometry op {op:#x}")
    return " ".join(out), fill_rule


_TOKEN = re.compile(r"[MLQCAZ]|-?[0-9.]+(?:e-?[0-9]+)?")


def parse_svg_path(d: str) -> list[tuple]:
    toks = _TOKEN.findall(d)
    cmds: list[tuple] = []
    i = 0
    cur = None
    need = {"M": 2, "L": 2, "Q": 4, "C": 6, "A": 7, "Z": 0}
    while i < len(toks):
        t = toks[i]
        if t in need:
            cur = t
            i += 1
            if cur == "Z":
                cmds.append(("Z",))
                continue
        n = need[cur]
        vals = [float(x) for x in toks[i:i + n]]
        i += n
        cmds.append((cur, *vals))
        if cur == "M":
            cur = "L"
    return cmds


def build_cairo_path(cr, cmds: list[tuple]) -> None:
    x0 = y0 = 0.0
    for c in cmds:
        k = c[0]
        if k == "M":
            cr.move_to(c[1], c[2])
            x0, y0 = c[1], c[2]
        elif k == "L":
            cr.line_to(c[1], c[2])
            x0, y0 = c[1], c[2]
        elif k == "C":
            cr.curve_to(*c[1:7])
            x0, y0 = c[5], c[6]
        elif k == "Q":
            qx, qy, x, y = c[1:5]
            cr.curve_to(x0 + 2 / 3 * (qx - x0), y0 + 2 / 3 * (qy - y0),
                        x + 2 / 3 * (qx - x), y + 2 / 3 * (qy - y), x, y)
            x0, y0 = x, y
        elif k == "A":
            _arc_to(cr, x0, y0, *c[1:8])
            x0, y0 = c[6], c[7]
        elif k == "Z":
            cr.close_path()


def _arc_to(cr, x1, y1, rx, ry, rot, large, sweep, x2, y2) -> None:
    """SVG elliptical arc -> cairo (endpoint to centre parameterisation)."""
    if rx == 0 or ry == 0:
        cr.line_to(x2, y2)
        return
    phi = math.radians(rot)
    cos_p, sin_p = math.cos(phi), math.sin(phi)
    dx, dy = (x1 - x2) / 2, (y1 - y2) / 2
    x1p = cos_p * dx + sin_p * dy
    y1p = -sin_p * dx + cos_p * dy
    rx, ry = abs(rx), abs(ry)
    lam = (x1p ** 2) / rx ** 2 + (y1p ** 2) / ry ** 2
    if lam > 1:
        rx *= math.sqrt(lam)
        ry *= math.sqrt(lam)
    num = rx ** 2 * ry ** 2 - rx ** 2 * y1p ** 2 - ry ** 2 * x1p ** 2
    den = rx ** 2 * y1p ** 2 + ry ** 2 * x1p ** 2
    coef = math.sqrt(max(0.0, num / den)) if den else 0.0
    if int(large) == int(sweep):
        coef = -coef
    cxp, cyp = coef * rx * y1p / ry, -coef * ry * x1p / rx
    cx = cos_p * cxp - sin_p * cyp + (x1 + x2) / 2
    cy = sin_p * cxp + cos_p * cyp + (y1 + y2) / 2

    def ang(ux, uy, vx, vy):
        a = math.atan2(ux * vy - uy * vx, ux * vx + uy * vy)
        return a

    t1 = ang(1, 0, (x1p - cxp) / rx, (y1p - cyp) / ry)
    dt = ang((x1p - cxp) / rx, (y1p - cyp) / ry, (-x1p - cxp) / rx, (-y1p - cyp) / ry)
    if not sweep and dt > 0:
        dt -= 2 * math.pi
    elif sweep and dt < 0:
        dt += 2 * math.pi
    cr.save()
    cr.translate(cx, cy)
    cr.rotate(phi)
    cr.scale(rx, ry)
    if dt > 0:
        cr.arc(0, 0, 1, t1, t1 + dt)
    else:
        cr.arc_negative(0, 0, 1, t1, t1 + dt)
    cr.restore()


def path_bounds(cmds: list[tuple]) -> tuple[float, float, float, float]:
    """Bounding box of all control points (good enough for Stretch=Fill icons)."""
    xs, ys = [], []
    for c in cmds:
        k = c[0]
        if k == "A":
            xs.append(c[6]); ys.append(c[7])
        elif k != "Z":
            vals = c[1:]
            xs.extend(vals[0::2]); ys.extend(vals[1::2])
    if not xs:
        return 0, 0, 0, 0
    return min(xs), min(ys), max(xs), max(ys)
