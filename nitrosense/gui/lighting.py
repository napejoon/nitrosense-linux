"""Keyboard lighting panel (Popup_Lighting + LightingDynamicUI)."""

from __future__ import annotations

import copy
import math

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Gdk", "4.0")
from gi.repository import Gdk, Gtk  # noqa: E402

from .. import encoders as enc  # noqa: E402
from . import theme  # noqa: E402
from .controls import (Canvas, HexRadio, HexToggle, SpeedSlider, TitleButton,  # noqa: E402
                       draw_text, widget_pos)
from .theme import rgba  # noqa: E402

BASIC_COLORS = ["#FF0000", "#FFA000", "#FFFF00", "#00FF00", "#00AEC7",
                "#001EFF", "#FF00FF", "#AA00FF", "#FFFFFF"]

PANEL_W, PANEL_H = 860, 480


class ColorSwatch(Canvas):
    """color_button_ControlTemplate: small square showing the zone colour."""

    def __init__(self, color: str, size: int = 20) -> None:
        super().__init__(size, size)
        self.color = color
        self.selected = False

    def set_color(self, color: str) -> None:
        self.color = color
        self.queue_draw()

    def draw(self, cr) -> None:
        s = self.w
        cr.set_source_rgba(*rgba(self.color, None if self.enabled else 0.4))
        cr.rectangle(2, 2, s - 4, s - 4)
        cr.fill()
        if self.hover or self.selected:
            cr.set_source_rgba(1, 1, 1, 0.9)
            cr.set_line_width(1.5)
            cr.rectangle(0.75, 0.75, s - 1.5, s - 1.5)
            cr.stroke()


class KeyboardView(Gtk.DrawingArea):
    """Stylised 4-zone keyboard showing the current colours."""

    def __init__(self) -> None:
        super().__init__()
        self.set_size_request(736, 170)
        self.set_content_width(736)
        self.set_content_height(170)
        self.colors = ["#FF0000"] * 4
        self.enabled = [True] * 4
        self.dynamic = False
        self.effect = enc.EFFECT_WAVE
        self.effect_color = "#FF0000"
        self.rainbow_wave = True
        self.phase = 0.0
        self.set_draw_func(lambda _a, cr, w, h: self.draw(cr))
        self.add_tick_callback(self._tick)

    def _tick(self, _w, _clock) -> bool:
        if self.dynamic:
            self.phase = (self.phase + 0.02) % 1.0
            self.queue_draw()
        return True

    def _zone_color(self, zone: int, col_frac: float) -> tuple[float, float, float, float]:
        if not self.dynamic:
            if not self.enabled[zone]:
                return 0.12, 0.12, 0.12, 1
            return rgba(self.colors[zone])
        base = rgba(self.effect_color)
        p = self.phase
        if self.effect == enc.EFFECT_BREATHING:
            k = 0.25 + 0.75 * (0.5 + 0.5 * math.sin(2 * math.pi * p))
            return base[0] * k, base[1] * k, base[2] * k, 1
        if self.effect == enc.EFFECT_NEON or (self.effect == enc.EFFECT_WAVE and self.rainbow_wave):
            h = (col_frac - p) % 1.0 if self.effect == enc.EFFECT_WAVE else p
            r, g, b = _hsv(h)
            return r, g, b, 1
        if self.effect == enc.EFFECT_WAVE:
            k = 0.25 + 0.75 * (0.5 + 0.5 * math.sin(2 * math.pi * (col_frac - p)))
            return base[0] * k, base[1] * k, base[2] * k, 1
        if self.effect == enc.EFFECT_SHIFTING:
            k = 1.0 if abs(((col_frac - p) % 1.0) - 0.5) < 0.12 else 0.25
            return base[0] * k, base[1] * k, base[2] * k, 1
        if self.effect == enc.EFFECT_ZOOM:
            k = 1.0 if abs(abs(col_frac - 0.5) * 2 - p) < 0.15 else 0.25
            return base[0] * k, base[1] * k, base[2] * k, 1
        return base

    def draw(self, cr) -> None:
        # keyboard body
        cr.set_source_rgba(*rgba("#1A1A1A"))
        _rounded(cr, 0, 0, 736, 170, 10)
        cr.fill()
        cols = 16
        zone_cols = [(0, 5), (5, 9), (9, 13), (13, 16)]  # 4 zones like the Nitro keyboard
        kw, kh, gap = 736 / cols, 170 / 6, 4
        for row in range(6):
            for c in range(cols):
                zone = next(i for i, (a, b) in enumerate(zone_cols) if a <= c < b)
                r, g, b, _ = self._zone_color(zone, c / (cols - 1))
                x, y = c * kw + gap / 2, row * kh + gap / 2
                cr.set_source_rgba(0.09, 0.09, 0.09, 1)
                _rounded(cr, x, y, kw - gap, kh - gap, 3)
                cr.fill()
                cr.set_source_rgba(r, g, b, 0.85)
                cr.set_line_width(1.5)
                _rounded(cr, x + 1, y + 1, kw - gap - 2, kh - gap - 2, 3)
                cr.stroke()
        if not self.dynamic:
            for i, (a, b) in enumerate(zone_cols):
                draw_text(cr, f"{i + 1}", (a + b) / 2 * kw, 170 - 22, 12, "#808080",
                          align="center")


def _hsv(h: float) -> tuple[float, float, float]:
    i = int(h * 6) % 6
    f = h * 6 - int(h * 6)
    q, t = 1 - f, f
    return [(1, t, 0), (q, 1, 0), (0, 1, t), (0, q, 1), (t, 0, 1), (1, 0, q)][i]


def _rounded(cr, x, y, w, h, r) -> None:
    cr.new_sub_path()
    cr.arc(x + w - r, y + r, r, -math.pi / 2, 0)
    cr.arc(x + w - r, y + h - r, r, 0, math.pi / 2)
    cr.arc(x + r, y + h - r, r, math.pi / 2, math.pi)
    cr.arc(x + r, y + r, r, math.pi, 3 * math.pi / 2)
    cr.close_path()


class PanelBackground(Gtk.DrawingArea):
    def __init__(self) -> None:
        super().__init__()
        self.set_size_request(PANEL_W, PANEL_H)
        self.set_draw_func(lambda _a, cr, w, h: self.draw(cr))

    def draw(self, cr) -> None:
        cut = 18
        def shape(inset):
            w, h = PANEL_W - 2 * inset, PANEL_H - 2 * inset
            cr.move_to(inset + cut, inset)
            cr.line_to(inset + w - cut, inset)
            cr.line_to(inset + w, inset + cut)
            cr.line_to(inset + w, inset + h - cut)
            cr.line_to(inset + w - cut, inset + h)
            cr.line_to(inset + cut, inset + h)
            cr.line_to(inset, inset + h - cut)
            cr.line_to(inset, inset + cut)
            cr.close_path()
        shape(0)
        cr.set_source_rgba(*rgba(theme.PANEL_FRAME))
        cr.fill()
        shape(2)
        cr.set_source_rgba(*rgba(theme.PANEL_BG))
        cr.fill()


class LightingPanel(Gtk.Fixed):
    """860x480 panel; calls on_apply(keyboard_dict) whenever the user changes something."""

    def __init__(self, assets, on_apply, on_close, recent_colors: list[str],
                 on_recent_changed, wave_color_enabled: bool = False) -> None:
        super().__init__()
        self.assets = assets
        self.wave_color_enabled = wave_color_enabled
        self.on_apply = on_apply
        self.on_close = on_close
        self.recent = recent_colors
        self.on_recent_changed = on_recent_changed
        self.kb: dict = {}
        self._color_target: tuple[str, int] | None = None
        self.set_size_request(PANEL_W, PANEL_H)
        self.put(PanelBackground(), 0, 0)

        t = assets.text
        title = Gtk.Label(label=f"{t('MUI_Keyboard', 'Keyboard')} {t('MUI_Lighting', 'Lighting')}")
        title.add_css_class("ns-panel-title")
        self.put(title, 24, 18)
        close = TitleButton(assets, "CloseButton_ControlTemplate", 26, 20)
        close.on_click = lambda *_: self.on_close()
        self.put(close, PANEL_W - 26 - 22, 20)

        # Static / Dynamic
        self.mode_group: list = []
        self.static_radio = HexRadio(assets, self.mode_group, self._mode_selected)
        self.dynamic_radio = HexRadio(assets, self.mode_group, self._mode_selected)
        self.put(self.static_radio, 24, 66)
        self.put(_label(t("MUI_Static", "Static"), "ns-panel-text"), 58, 64)
        self.put(self.dynamic_radio, 160, 66)
        self.put(_label(t("MUI_Dynamic", "Dynamic"), "ns-panel-text"), 194, 64)

        # brightness 1..5
        self.put(_label("☼", "ns-panel-dim"), 540, 62)
        self.brightness = SpeedSlider(assets, 1, 5, width=200, on_release=self._brightness)
        self.put(self.brightness, 566, 62)
        self.put(_label("☀", "ns-panel-text"), 774, 62)

        self.keyboard = KeyboardView()
        self.put(self.keyboard, 62, 106)

        # ---- static zone controls (Light_keyboard_Color)
        self.static_box = Gtk.Fixed()
        self.zone_toggles: list[HexToggle] = []
        self.zone_swatches: list[ColorSwatch] = []
        x0 = 62 + (736 - (4 * 160 + 3 * 36)) / 2
        for i in range(4):
            x = x0 + i * 196
            lbl = _label(t(f"MUI_Z{i + 1}", f"Zone {i + 1}"), "ns-panel-dim")
            lbl.set_size_request(160, -1)
            lbl.set_xalign(0.5)
            self.static_box.put(lbl, x, 0)
            tog = HexToggle(assets, lambda w, on, i=i: self._zone_toggled(i, on))
            sw = ColorSwatch("#FF0000")
            sw.on_click = lambda w, i=i: self._open_colors(("zone", i), w)
            self.static_box.put(tog, x + 36, 38)
            self.static_box.put(sw, x + 36 + 52 + 16, 38)
            self.zone_toggles.append(tog)
            self.zone_swatches.append(sw)
            if i < 3:
                line = Gtk.DrawingArea()
                line.set_size_request(2, 70)
                line.set_draw_func(lambda _a, cr, w, h: (cr.set_source_rgba(*rgba("#333333")),
                                                         cr.paint()))
                self.static_box.put(line, x + 160 + 17, 0)
        self.put(self.static_box, 0, 300)

        # ---- dynamic controls (LightingDynamicUI)
        self.dynamic_box = Gtk.Fixed()
        self.dynamic_box.put(_label(t("MUI_Light_Effects", "Light Effects"), "ns-panel-dim"), 0, 0)
        self.effect_group: list = []
        self.effect_radios: dict[str, HexRadio] = {}
        names = {enc.EFFECT_BREATHING: t("MUI_Breathing", "Breathing"),
                 enc.EFFECT_WAVE: t("MUI_Wave", "Wave"), enc.EFFECT_ZOOM: t("MUI_Zoom", "Zoom"),
                 enc.EFFECT_SHIFTING: t("MUI_Shifting", "Shifting"),
                 enc.EFFECT_NEON: t("MUI_Neon", "Neon")}
        for i, eff in enumerate(enc.EFFECTS):
            r = HexRadio(assets, self.effect_group, lambda w, e=eff: self._effect(e))
            self.effect_radios[eff] = r
            x = (i % 3) * 130
            y = 30 + (i // 3) * 34
            self.dynamic_box.put(r, x, y)
            self.dynamic_box.put(_label(names[eff], "ns-panel-text"), x + 34, y - 2)

        self.dynamic_box.put(_label(t("MUI_Speed", "Speed"), "ns-panel-dim"), 420, 0)
        self.speed = SpeedSlider(assets, 1, 9, width=180, on_release=self._speed)
        self.dynamic_box.put(self.speed, 420, 30)

        self.direction_box = Gtk.Fixed()
        self.direction_box.put(_label(t("MUI_Direction", "Direction"), "ns-panel-dim"), 0, 0)
        self.dir_left = _DirectionButton(assets, "Drection4wayLeftStyle")
        self.dir_right = _DirectionButton(assets, "Drection4wayRightStyle")
        self.dir_left.on_click = lambda *_: self._direction(enc.DIRECTION_LEFT)
        self.dir_right.on_click = lambda *_: self._direction(enc.DIRECTION_RIGHT)
        self.direction_box.put(self.dir_left, 0, 26)
        self.direction_box.put(self.dir_right, 70, 26)
        self.dynamic_box.put(self.direction_box, 640, 0)

        self.color_box = Gtk.Fixed()
        self.color_box.put(_label(t("MUI_Basic_Colors", "Basic colors"), "ns-panel-dim"), 0, 0)
        self.effect_swatches: list[ColorSwatch] = []
        for i, c in enumerate(BASIC_COLORS):
            sw = ColorSwatch(c, 24)
            sw.on_click = lambda w, c=c: self._effect_color(c)
            self.color_box.put(sw, i * 32, 28)
            self.effect_swatches.append(sw)
        more = ColorSwatch("#808080", 24)
        more.on_click = lambda w: self._open_colors(("effect", 0), w)
        self.more_swatch = more
        self.color_box.put(more, 9 * 32 + 8, 28)
        self.color_box.put(_label("…", "ns-panel-text"), 9 * 32 + 15, 26)
        self.dynamic_box.put(self.color_box, 420, 72)
        self.put(self.dynamic_box, 62, 300)

        # colour popup (colors_Popup)
        self.color_popup = _ColorPopup(assets, self._color_chosen, self._more_colors)
        self.color_popup.set_visible(False)
        self.put(self.color_popup, 0, 0)

    # ------------------------------------------------------------ state
    def load(self, kb: dict) -> None:
        self.kb = copy.deepcopy(kb)
        self._refresh()

    def _refresh(self) -> None:
        kb = self.kb
        dynamic = kb.get("mode") == "dynamic"
        (self.dynamic_radio if dynamic else self.static_radio).set_checked(True)
        self.brightness.set_value(kb.get("brightness", 5))
        self.static_box.set_visible(not dynamic)
        self.dynamic_box.set_visible(dynamic)
        for i in range(4):
            self.zone_toggles[i].set_active(kb["zones_on"][i])
            self.zone_swatches[i].set_color(kb["zone_colors"][i])
            self.zone_swatches[i].set_enabled(kb["zones_on"][i])
        eff = kb.get("effect", enc.EFFECT_WAVE)
        self.effect_radios[eff].set_checked(True)
        self.speed.set_value(kb.get("speed", 5))
        self.direction_box.set_visible(enc.EFFECT_HAS_DIRECTION[eff])
        # LightingDynamicUI: colours always shown; disabled for Neon, and for Wave on
        # models from 2020 on (their firmware draws a rainbow wave)
        colors_on = eff not in (enc.EFFECT_NEON,) and not (
            eff == enc.EFFECT_WAVE and not self.wave_color_enabled)
        for sw in self.effect_swatches + [self.more_swatch]:
            sw.set_enabled(colors_on)
        self.dir_left.selected = kb.get("direction") == enc.DIRECTION_LEFT
        self.dir_right.selected = kb.get("direction") != enc.DIRECTION_LEFT
        self.dir_left.queue_draw()
        self.dir_right.queue_draw()
        for sw in self.effect_swatches:
            sw.selected = colors_on and sw.color.upper() == kb.get("effect_color", "").upper()
            sw.queue_draw()
        kv = self.keyboard
        kv.dynamic, kv.effect = dynamic, eff
        kv.colors, kv.enabled = list(kb["zone_colors"]), list(kb["zones_on"])
        kv.effect_color = kb.get("effect_color", "#FF0000")
        kv.rainbow_wave = not self.wave_color_enabled
        kv.queue_draw()

    def _changed(self) -> None:
        self._refresh()
        self.on_apply(copy.deepcopy(self.kb))

    # ---------------------------------------------------------- handlers
    def _mode_selected(self, w) -> None:
        self.kb["mode"] = "dynamic" if w is self.dynamic_radio else "static"
        self._changed()

    def _brightness(self, _w, v) -> None:
        self.kb["brightness"] = int(v)
        self._changed()

    def _zone_toggled(self, i: int, on: bool) -> None:
        self.kb["zones_on"][i] = on
        self._changed()

    def _effect(self, eff: str) -> None:
        self.kb["effect"] = eff
        self._changed()

    def _speed(self, _w, v) -> None:
        self.kb["speed"] = int(v)
        self._changed()

    def _direction(self, d: int) -> None:
        self.kb["direction"] = d
        self._changed()

    def _effect_color(self, c: str) -> None:
        self.kb["effect_color"] = c
        self._remember(c)
        self._changed()

    def _open_colors(self, target, widget) -> None:
        if target[0] == "zone" and not self.kb["zones_on"][target[1]]:
            return
        if target[0] == "effect":
            self._color_target = target
            self._more_colors()
            return
        self._color_target = target
        self.color_popup.set_recent(self.recent)
        pos = widget_pos(widget, self)
        if pos is None:
            return
        x, y = pos
        px = max(10, min(PANEL_W - 290, x - 130))
        self.move(self.color_popup, px, y - 150)
        self.color_popup.set_visible(True)

    def _color_chosen(self, color: str) -> None:
        self.color_popup.set_visible(False)
        if not self._color_target:
            return
        kind, i = self._color_target
        if kind == "zone":
            self.kb["zone_colors"][i] = color
        else:
            self.kb["effect_color"] = color
        self._remember(color)
        self._changed()

    def _more_colors(self) -> None:
        self.color_popup.set_visible(False)
        dialog = Gtk.ColorDialog(with_alpha=False, title=self.assets.text("MUI_More_Color", "More color"))
        kind, i = self._color_target or ("effect", 0)
        cur = self.kb["zone_colors"][i] if kind == "zone" else self.kb.get("effect_color", "#FF0000")
        initial = Gdk.RGBA()
        initial.parse(cur)

        def done(dlg, res):
            try:
                c = dlg.choose_rgba_finish(res)
            except Exception:
                return
            hexc = "#%02X%02X%02X" % (round(c.red * 255), round(c.green * 255), round(c.blue * 255))
            self._color_chosen(hexc)

        dialog.choose_rgba(self.get_root(), initial, None, done)

    def _remember(self, color: str) -> None:
        color = color.upper()
        if color in BASIC_COLORS:
            return
        if color in self.recent:
            self.recent.remove(color)
        self.recent.insert(0, color)
        del self.recent[9:]
        self.on_recent_changed(self.recent)

    def close_popups(self) -> None:
        self.color_popup.set_visible(False)


class _DirectionButton(Canvas):
    def __init__(self, assets, key) -> None:
        super().__init__(61, 37)
        self.assets, self.key = assets, key
        self.selected = False

    def draw(self, cr) -> None:
        bg = "#80E60000" if self.selected else "#80333333" if self.hover else "#80000000"
        self.assets.draw(cr, self.key, 0, 0, names=("btn",), color=bg)
        self.assets.draw(cr, self.key, 0, 0, names=("icon_Effect",),
                         color=theme.WHITE if self.selected else "#B2B2B2")


class _ColorPopup(Gtk.Fixed):
    """colors_Popup: 280x140, basic colours, recent colours, More color..."""

    def __init__(self, assets, on_pick, on_more) -> None:
        super().__init__()
        self.set_size_request(280, 140)
        bg = Gtk.DrawingArea()
        bg.set_size_request(280, 140)
        bg.set_draw_func(lambda _a, cr, w, h: (cr.set_source_rgba(*rgba(theme.POPUP_BG)),
                                               cr.paint()))
        self.put(bg, 0, 0)
        self.put(_label(assets.text("MUI_Basic_Colors", "Basic colors"), "ns-popup-text"), 8, 4)
        for i, c in enumerate(BASIC_COLORS):
            sw = ColorSwatch(c, 24)
            sw.on_click = lambda w, c=c: on_pick(c)
            self.put(sw, 8 + i * 29, 26)
        self.put(_label(assets.text("MUI_Recent_Colors", "Recent colors"), "ns-popup-text"), 8, 52)
        self.recent: list[ColorSwatch] = []
        for i in range(9):
            sw = ColorSwatch("#00000000", 24)
            sw.on_click = lambda w: w.color != "#00000000" and on_pick(w.color)
            self.put(sw, 8 + i * 29, 74)
            self.recent.append(sw)
        more = Gtk.Button(label=assets.text("MUI_More_Color", "More color..."))
        more.add_css_class("ns-more-color")
        more.set_size_request(264, 28)
        more.connect("clicked", lambda *_: on_more())
        self.put(more, 8, 106)

    def set_recent(self, colors: list[str]) -> None:
        for i, sw in enumerate(self.recent):
            c = colors[i] if i < len(colors) else "#00000000"
            sw.set_color(c)
            sw.set_enabled(i < len(colors))


def _label(text: str, css: str) -> Gtk.Label:
    lbl = Gtk.Label(label=text)
    lbl.add_css_class(css)
    lbl.set_xalign(0)
    return lbl
