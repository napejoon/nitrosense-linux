"""Extract NitroSense artwork from the user's own Windows installer package.

Usage: python3 -m nitrosense.extract <NitroSense package dir or .appxbundle> <output dir>

The original NitroSense.exe (a WPF app) is read straight out of the
.appxbundle; its WPF resources provide the background images, the Acer
fonts, the English UI strings and the vector icons (BAML path geometry).
Nothing is executed. Output layout:

    images/100/*.png images/200/*.png  fonts/*.ttf  strings_en.json  icons.json
"""

from __future__ import annotations

import io
import json
import os
import struct
import sys
import zipfile

from .geometry import decode_path_data

# --------------------------------------------------------------- locate exe

def find_bundle(src: str) -> str:
    if os.path.isfile(src):
        return src
    for root, _dirs, files in os.walk(src):
        for name in files:
            if name.lower().endswith(".appxbundle"):
                return os.path.join(root, name)
    raise FileNotFoundError(f"no .appxbundle found under {src}")


def read_nitrosense_exe(bundle_path: str) -> bytes:
    return read_bundle(bundle_path)[0]


ICON_NAMES = ("assets/square44x44logo.targetsize-256.png", "assets/square310x310logo.scale-200.png")


def read_bundle(bundle_path: str) -> tuple[bytes, bytes | None]:
    """-> (Win32/NitroSense.exe, app icon PNG or None)."""
    with zipfile.ZipFile(bundle_path) as bundle:
        appx_names = [n for n in bundle.namelist() if n.lower().endswith("_x64.appx")]
        if not appx_names:
            appx_names = [n for n in bundle.namelist()
                          if n.lower().endswith(".appx") and "scale" not in n.lower()]
        for name in appx_names:
            with zipfile.ZipFile(io.BytesIO(bundle.read(name))) as appx:
                names = {n.replace("\\", "/").lower(): n for n in appx.namelist()}
                exe = next((n for low, n in names.items() if low.endswith("win32/nitrosense.exe")),
                           None)
                if exe is None:
                    continue
                icon = next((appx.read(names[i]) for i in ICON_NAMES if i in names), None)
                return appx.read(exe), icon
    raise FileNotFoundError("Win32/NitroSense.exe not found in the bundle")


# ------------------------------------------------------------- PE / .NET

def _rva_to_offset(pe: bytes, rva: int) -> int:
    e_lfanew, = struct.unpack_from("<I", pe, 0x3C)
    nsec, = struct.unpack_from("<H", pe, e_lfanew + 6)
    optsz, = struct.unpack_from("<H", pe, e_lfanew + 20)
    sec = e_lfanew + 24 + optsz
    for i in range(nsec):
        _name, vsize, va, rsize, raw = struct.unpack_from("<8sIIII", pe, sec + 40 * i)
        if va <= rva < va + max(vsize, rsize):
            return rva - va + raw
    raise ValueError(f"RVA {rva:#x} not mapped")


def managed_resources(pe: bytes) -> list[bytes]:
    """Return the embedded manifest resource blobs of a .NET PE."""
    e_lfanew, = struct.unpack_from("<I", pe, 0x3C)
    magic, = struct.unpack_from("<H", pe, e_lfanew + 24)
    ddir = e_lfanew + 24 + (112 if magic == 0x20B else 96)
    cli_rva, _ = struct.unpack_from("<II", pe, ddir + 14 * 8)
    cli = _rva_to_offset(pe, cli_rva)
    res_rva, res_size = struct.unpack_from("<II", pe, cli + 24)
    base = _rva_to_offset(pe, res_rva)
    blobs, pos = [], 0
    while pos + 4 <= res_size:
        n, = struct.unpack_from("<I", pe, base + pos)
        if n == 0 or pos + 4 + n > res_size:
            break
        blobs.append(pe[base + pos + 4: base + pos + 4 + n])
        pos = (pos + 4 + n + 7) & ~7
    return blobs


def _read7(b: bytes, p: int) -> tuple[int, int]:
    v = s = 0
    while True:
        c = b[p]
        p += 1
        v |= (c & 0x7F) << s
        s += 7
        if not c & 0x80:
            return v, p


def parse_resources(blob: bytes) -> dict[str, bytes]:
    """Parse a System.Resources .resources stream (stream/byte[] entries only)."""
    magic, _hver, skip = struct.unpack_from("<III", blob, 0)
    if magic != 0xBEEFCACE:
        raise ValueError("not a .resources stream")
    p = 12 + skip
    _ver, count, ntypes = struct.unpack_from("<III", blob, p)
    p += 12
    for _ in range(ntypes):
        n, p = _read7(blob, p)
        p += n
    p += (-p) % 8
    p += 4 * count
    positions = struct.unpack_from(f"<{count}I", blob, p)
    p += 4 * count
    data_section, = struct.unpack_from("<I", blob, p)
    p += 4
    out: dict[str, bytes] = {}
    for rel in positions:
        q = p + rel
        n, q = _read7(blob, q)
        name = blob[q:q + n].decode("utf-16-le")
        q += n
        off, = struct.unpack_from("<I", blob, q)
        d = data_section + off
        tcode, d = _read7(blob, d)
        if tcode in (0x20, 0x21):  # byte[] / Stream
            ln, = struct.unpack_from("<I", blob, d)
            out[name] = blob[d + 4: d + 4 + ln]
    return out


# ---------------------------------------------------------------- BAML

KT = {50: "Border", 55: "Button", 66: "Canvas", 74: "CheckBox", 108: "ControlTemplate",
      180: "DrawingBrush", 183: "DrawingGroup", 191: "Ellipse", 241: "GeometryDrawing",
      252: "GradientStop", 254: "Grid", 281: "Image", 346: "Label", 350: "Line",
      358: "LinearGradientBrush", 442: "Path", 483: "Polygon", 503: "RadioButton",
      514: "Rectangle", 524: "ResourceDictionary", 556: "Setter", 578: "SolidColorBrush",
      601: "StackPanel", 620: "Style", 638: "TextBlock", 688: "Trigger", 728: "Viewbox"}
KM = {4: "Background", 47: "Height", 48: "HorizontalAlignment", 49: "Margin", 54: "Name",
      56: "VerticalAlignment", 57: "Width", 75: "Source", 86: "Background", 87: "Data",
      100: "Fill", 101: "Stroke", 102: "StrokeThickness", 110: "Text", 135: "Visibility"}
SER_BRUSH, SER_PATH = 744, 746
SIZED = {5, 6, 15, 16, 17, 18, 20, 25, 27, 28, 29, 30, 31, 32, 36, 38, 51, 52}
FIXED = {1: 6, 3: 3, 7: 2, 9: 2, 11: 2, 13: 2, 33: 4, 34: 4, 35: 6, 37: 4, 39: 9, 40: 9,
         44: 2, 45: 4, 46: 2, 48: 3, 50: 2, 53: 8, 54: 4, 55: 3, 56: 4}


class Node:
    __slots__ = ("type", "attrs", "children", "key")

    def __init__(self, type_: str) -> None:
        self.type = type_
        self.attrs: dict[str, object] = {}
        self.children: list[Node] = []
        self.key: str | None = None

    def walk(self):
        yield self
        for c in self.children:
            yield from c.walk()


def _brush(data: bytes) -> str | None:
    if not data:
        return None
    if data[0] == 2:  # string form, e.g. "#FFE60000"
        n, p = _read7(data, 1)
        return data[p:p + n].decode("ascii", "replace")
    if data[0] == 1 and len(data) >= 5:  # known colour as ARGB uint
        argb, = struct.unpack_from("<I", data, 1)
        return "#%08X" % argb
    return None


def parse_baml(b: bytes) -> Node:
    hdr, = struct.unpack_from("<I", b, 0)
    p = 4 + hdr + 12
    types: dict[int, str] = {}
    attrs: dict[int, str] = {}
    strings: dict[int, str] = {}
    root = Node("#root")
    stack = [root]
    pending_keys: list[str] = []

    def tname(i: int) -> str:
        return KT.get(0x10000 - i, f"KT{0x10000 - i}") if i & 0x8000 else types.get(i, f"T{i}")

    def aname(i: int) -> str:
        return KM.get(0x10000 - i, f"KM{0x10000 - i}") if i & 0x8000 else attrs.get(i, f"A{i}")

    def sstr(q: int) -> tuple[str, int]:
        n, q = _read7(b, q)
        return b[q:q + n].decode("utf-8", "replace"), q + n

    while p < len(b):
        t = b[p]
        p += 1
        start = p
        if t in SIZED:
            size, _ = _read7(b, p)
            end = start + size
            _, p = _read7(b, p)
        elif t in FIXED:
            end = p + FIXED[t]
        elif t in (2,):
            break
        else:
            end = p
        cur = stack[-1]
        if t in (3, 48):  # ElementStart / StaticResourceStart
            tid, = struct.unpack_from("<H", b, p)
            node = Node(tname(tid))
            cur.children.append(node)
            if len(stack) == 2 and stack[1].type == "ResourceDictionary" and pending_keys:
                node.key = pending_keys.pop(0)
            stack.append(node)
        elif t in (4, 49):
            if len(stack) > 1:
                stack.pop()
        elif t == 5:  # Property
            aid, = struct.unpack_from("<H", b, p)
            val, _ = sstr(p + 2)
            cur.attrs[aname(aid)] = val
        elif t == 6:  # PropertyCustom
            aid, ser = struct.unpack_from("<HH", b, p)
            data = b[p + 4:end]
            ser_id = 0x10000 - ser if ser & 0x8000 else ser
            if ser_id == SER_PATH:
                cur.attrs[aname(aid)] = ("geometry", data.hex())
            elif ser_id == SER_BRUSH:
                cur.attrs[aname(aid)] = _brush(data)
        elif t == 36:  # PropertyWithConverter
            aid, = struct.unpack_from("<H", b, p)
            val, _ = sstr(p + 2)
            cur.attrs[aname(aid)] = val
        elif t == 25:  # DefAttribute (x:Name, x:Uid, ...)
            val, q = sstr(p)
            nid, = struct.unpack_from("<H", b, q)
            known = {1: "Name", 2: "Uid"}
            name = known.get(0x10000 - nid, "Attr") if nid & 0x8000 else strings.get(nid, "Attr")
            cur.attrs["x:" + name] = val
        elif t == 29 or t == 30:
            tid, _asm = struct.unpack_from("<HH", b, p)
            name, _ = sstr(p + 4)
            types[tid] = name.rsplit(".", 1)[-1]
        elif t == 31:
            aid, _own = struct.unpack_from("<HH", b, p)
            name, _ = sstr(p + 5)
            attrs[aid] = name
        elif t == 32:
            sid, = struct.unpack_from("<H", b, p)
            val, _ = sstr(p + 2)
            strings[sid] = val
        elif t == 38:  # DefAttributeKeyString
            vid, = struct.unpack_from("<H", b, p)
            if len(stack) == 2:
                pending_keys.append(strings.get(vid, f"S{vid}"))
        elif t == 39:  # DefAttributeKeyType
            tid, = struct.unpack_from("<H", b, p)
            if len(stack) == 2:
                pending_keys.append("{" + tname(tid) + "}")
        elif t == 40:  # KeyElementStart: a key built from markup, e.g. DataTemplateKey
            tid, = struct.unpack_from("<H", b, p)
            if len(stack) == 2:
                pending_keys.append("{" + tname(tid) + "}")
            stack.append(Node("#key"))  # detached: its children are not entries
        elif t == 41:  # KeyElementEnd
            if len(stack) > 1 and stack[-1].type == "#key":
                stack.pop()
        elif t in (16, 17):  # Text / TextWithConverter
            val, _ = sstr(p)
            cur.attrs["#text"] = cur.attrs.get("#text", "") + val
        elif t == 51:  # TextWithId
            vid, = struct.unpack_from("<H", b, p)
            cur.attrs["#text"] = cur.attrs.get("#text", "") + strings.get(vid, "")
        p = end
    return root


# ------------------------------------------------------------- products

STYLE_KEYS = [
    "CloseButton_ControlTemplate", "MinimizeButton_ControlTemplate",
    "SettingButton_ControlTemplate", "LightingButton_ControlTemplate",
    "Nitro_FanModeAutoStyle", "Nitro_FanModeMaxStyle", "Nitro_FanModeCustomStyle",
    "PowerPlan_PowerSaver_RadioButton_Style", "PowerPlan_Balance_RadioButton_Style",
    "PowerPlan_HighPerformance_RadioButton_Style",
    "ShowCoolBoosterInformationiconStyle", "SettingRadioButton_Style",
    "SettingCheckBox_ControlTemplate", "LightDynamicRadioButtionStyle",
    "Drection4wayLeftStyle", "Drection4wayRightStyle", "ColorRadioButtonStyle",
    "SliderThumb", "AutoCheckBoxStyle", "PowerPlan_TabItem_ControlTemplate",
]


def _path_record(n: Node) -> dict | None:
    data = n.attrs.get("Data")
    if not (isinstance(data, tuple) and data[0] == "geometry"):
        return None
    try:
        d, fill_rule = decode_path_data(data[1])
    except (ValueError, struct.error):
        return None
    rec = {"d": d, "fill_rule": fill_rule or "nonzero"}
    for k in ("Name", "x:Name", "Width", "Height", "Left", "Top", "Margin", "Fill", "Stroke",
              "StrokeThickness", "Stretch", "Opacity"):
        if k in n.attrs and isinstance(n.attrs[k], str):
            rec[k.replace("x:", "")] = n.attrs[k]
    return rec


def extract_icons(style_root: Node) -> dict:
    dict_node = next((c for c in style_root.children if c.type == "ResourceDictionary"), None)
    out = {}
    if dict_node is None:
        return out
    for entry in dict_node.children:
        if entry.key in STYLE_KEYS:
            paths = [r for r in (_path_record(n) for n in entry.walk()) if r]
            out[entry.key] = paths
    return out


def extract_strings(lang_root: Node) -> dict:
    dict_node = next((c for c in lang_root.children if c.type == "ResourceDictionary"), None)
    out = {}
    if dict_node is None:
        return out
    for entry in dict_node.children:
        if entry.key and "#text" in entry.attrs:
            out[entry.key] = entry.attrs["#text"]
    return out


def extract(src: str, outdir: str) -> dict:
    exe, icon = read_bundle(find_bundle(src))
    resources: dict[str, bytes] = {}
    for blob in managed_resources(exe):
        try:
            resources.update(parse_resources(blob))
        except (ValueError, struct.error):
            continue
    if not resources:
        raise RuntimeError("no WPF resources found in NitroSense.exe")

    counts = {"images": 0, "fonts": 0}
    for name, data in resources.items():
        low = name.lower()
        if low.startswith(("images/100/", "images/200/")) and low.endswith(".png") \
                and "/splash/" not in low:
            dest = os.path.join(outdir, low)
            counts["images"] += 1
        elif low.startswith("font/") and low.endswith(".ttf"):
            dest = os.path.join(outdir, "fonts", os.path.basename(low))
            counts["fonts"] += 1
        else:
            continue
        os.makedirs(os.path.dirname(dest), exist_ok=True)
        with open(dest, "wb") as f:
            f.write(data)

    strings = extract_strings(parse_baml(resources["lang/en.baml"]))
    icons = extract_icons(parse_baml(resources["style/100/psstyle.baml"]))
    with open(os.path.join(outdir, "strings_en.json"), "w") as f:
        json.dump(strings, f, indent=1, ensure_ascii=False)
    with open(os.path.join(outdir, "icons.json"), "w") as f:
        json.dump(icons, f, indent=1)
    if icon:
        with open(os.path.join(outdir, "icon.png"), "wb") as f:
            f.write(icon)
    counts["strings"] = len(strings)
    counts["icons"] = sum(len(v) for v in icons.values())
    return counts


def main(argv: list[str]) -> int:
    if len(argv) != 3:
        print(__doc__)
        return 2
    os.makedirs(argv[2], exist_ok=True)
    counts = extract(argv[1], argv[2])
    print("extracted:", ", ".join(f"{k}={v}" for k, v in counts.items()))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
