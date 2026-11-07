"""NitroSense main window (Nitro_MainWindow_Octavia layout, 1320x760)."""

from __future__ import annotations

import json
import os
import queue
import threading

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Gdk", "4.0")
from gi.repository import Gdk, Gio, GLib, Gtk  # noqa: E402

from .. import model as model_cfg  # noqa: E402
from ..client import Client, DaemonError  # noqa: E402
from ..protocol import default_state  # noqa: E402
from ..sysmon import CpuLoad, GpuMonitor  # noqa: E402
from . import theme  # noqa: E402
from .controls import (AutoButton, Canvas, FanView, HexRadio, HexToggle, LineChart,  # noqa: E402
                       ModeButton, SpeedSlider, TabButton, TitleButton, draw_text,
                       widget_pos)
from .lighting import PANEL_H, PANEL_W, LightingPanel  # noqa: E402
from .theme import Assets, rgba  # noqa: E402

PREFS = os.path.join(GLib.get_user_config_dir(), "nitrosense", "gui.json")

CSS = f"""
window.nitrosense {{ background-color: transparent; }}
.nitrosense label {{ font-family: {theme.FONT}; }}
.ns-header {{ color: #FFFFFF; font-size: 16px; }}
.ns-mode {{ color: {theme.GRAY_TEXT}; font-size: 14px; }}
.ns-axis {{ color: {theme.GRAY_TEXT}; font-size: 12px; }}
.ns-slider-label {{ color: #FFFFFF; font-size: 18px; }}
.ns-coolboost {{ color: #FFFFFF; font-size: 16px; }}
.ns-temp {{ color: {theme.RED}; font-size: 28px; }}
.ns-usage {{ color: {theme.ORANGE}; font-size: 28px; }}
.ns-usage-pct {{ color: {theme.ORANGE}; font-size: 18px; }}
.ns-status {{ color: {theme.RED}; font-size: 13px; }}
.ns-popup-title {{ color: #000000; font-size: 14px; font-weight: bold; }}
.ns-popup-text {{ color: #000000; font-size: 14px; }}
.ns-popup-unit {{ color: {theme.GRAY_TEXT}; font-size: 16px; }}
.ns-popup-small {{ color: #191919; font-size: 12px; }}
.ns-popup-hint {{ color: {theme.GRAY_TEXT}; font-size: 10px; }}
.ns-panel-title {{ color: #FFFFFF; font-size: 20px; }}
.ns-panel-text {{ color: #FFFFFF; font-size: 15px; }}
.ns-panel-dim {{ color: {theme.GRAY_TEXT}; font-size: 16px; }}
button.ns-more-color {{ background: #C8C8C8; color: #000000; border-radius: 0;
                       border: none; box-shadow: none; font-size: 13px; }}
button.ns-more-color:hover {{ background: #B8B8B8; }}
"""


class Worker:
    """Runs daemon requests off the UI thread; callbacks come back on the UI thread."""

    def __init__(self) -> None:
        self.q: queue.Queue = queue.Queue()
        threading.Thread(target=self._run, daemon=True).start()

    def submit(self, fn, done=None, fail=None) -> None:
        self.q.put((fn, done, fail))

    def _run(self) -> None:
        while True:
            fn, done, fail = self.q.get()
            try:
                res = fn()
            except Exception as e:  # reported on the UI thread
                if fail:
                    GLib.idle_add(fail, e)
                continue
            if done:
                GLib.idle_add(done, res)


def _label(text: str, css: str, xalign: float = 0.0) -> Gtk.Label:
    lbl = Gtk.Label(label=text)
    lbl.add_css_class(css)
    lbl.set_xalign(xalign)
    return lbl


def _picture(path: str | None, w: int, h: int) -> Gtk.Widget:
    if path:
        pic = Gtk.Picture.new_for_filename(path)
        pic.set_can_shrink(False)
        pic.set_content_fit(Gtk.ContentFit.FILL)
    else:
        pic = Gtk.DrawingArea()
        pic.set_draw_func(lambda _a, cr, ww, hh: (cr.set_source_rgba(0.08, 0.08, 0.08, 1),
                                                  cr.paint()))
    pic.set_size_request(w, h)
    return pic


class Separator(Gtk.DrawingArea):
    def __init__(self, w: int, h: int, color: str, alpha: float = 1.0) -> None:
        super().__init__()
        self.set_size_request(w, h)
        self.set_draw_func(lambda _a, cr, ww, hh: (cr.set_source_rgba(*rgba(color, alpha)),
                                                   cr.paint()))


class InfoIcon(Canvas):
    def __init__(self, assets: Assets) -> None:
        super().__init__(16, 16)
        self.assets = assets

    def draw(self, cr) -> None:
        self.assets.draw(cr, "ShowCoolBoosterInformationiconStyle", 0, 0,
                         alpha=1.0 if self.hover else 0.85)


class Popup(Gtk.Fixed):
    """Light grey NitroSense popup (#DADADA) with an arrow pointing at its button."""

    def __init__(self, width: int, height: int, arrow_x: float) -> None:
        super().__init__()
        self.pw, self.ph, self.arrow_x = width, height, arrow_x
        bg = Gtk.DrawingArea()
        bg.set_size_request(width, height)
        bg.set_draw_func(self._draw)
        self.bg = bg
        self.put(bg, 0, 0)

    def resize(self, height: int) -> None:
        self.ph = height
        self.bg.set_size_request(self.pw, height)
        self.bg.queue_draw()

    def _draw(self, _a, cr, w, h) -> None:
        cr.set_source_rgba(0, 0, 0, 0.35)
        cr.rectangle(1, 16, self.pw - 2, self.ph - 15)
        cr.fill()
        cr.set_source_rgba(*rgba(theme.POPUP_BG))
        cr.rectangle(2, 15, self.pw - 4, self.ph - 17)
        cr.fill()
        cr.move_to(self.arrow_x - 8, 16)
        cr.line_to(self.arrow_x, 8)
        cr.line_to(self.arrow_x + 8, 16)
        cr.close_path()
        cr.fill()


class MainWindow(Gtk.ApplicationWindow):
    def __init__(self, app: Gtk.Application) -> None:
        super().__init__(application=app, title="NitroSense")
        self.add_css_class("nitrosense")
        self.set_decorated(False)
        self.set_resizable(False)
        self.set_default_size(1320, 760)
        self.set_icon_name("nitrosense")

        provider = Gtk.CssProvider()
        provider.load_from_string(CSS)
        Gtk.StyleContext.add_provider_for_display(Gdk.Display.get_default(), provider,
                                                  Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION)

        self.assets = Assets()
        self.model = model_cfg.load()
        theme.register_fonts(self.assets.fonts_dir())
        self.t = self.assets.text
        self.client = Client()
        self.worker = Worker()
        self.cpu_load = CpuLoad()
        self.gpu = GpuMonitor()
        self.prefs = self._load_prefs()
        self.state = default_state(self.model.zone_default_colors)
        self.hw: dict = {}
        self.ac_online = True
        self._poll_busy = False
        self._status_is_error = False
        self._dgpu_supported_seen = None

        overlay = Gtk.Overlay()
        self.set_child(overlay)
        self.root = Gtk.Fixed()
        overlay.set_child(self.root)
        self.overlay = overlay

        self._build_frame()
        self._build_fan_page()
        self._build_monitor_page()
        self._build_settings_popup()
        self._build_coolboost_popup()
        self._build_lighting()

        click = Gtk.GestureClick()
        click.set_propagation_phase(Gtk.PropagationPhase.CAPTURE)
        click.connect("pressed", self._global_click)
        self.add_controller(click)
        keys = Gtk.EventControllerKey()
        keys.connect("key-pressed", self._key)
        self.add_controller(keys)

        self._poll()
        GLib.timeout_add(1000, self._poll)

    # ================================================================ frame
    def _build_frame(self) -> None:
        a = self.assets
        self.root.put(_picture(a.image("img_bg_nitro.png"), 1320, 760), 0, 0)
        self.root.put(_picture(a.image("img_bg_newboard_nitro.png"), 1152, 624), 84, 92)
        if not a.image("img_bg_nitro.png"):
            self.root.put(_label("NITROSENSE", "ns-header"), 600, 60)
        handle = Gtk.WindowHandle()
        handle.set_size_request(1320, 92)
        self.root.put(handle, 0, 0)

        close = TitleButton(a, "CloseButton_ControlTemplate", 26, 20, tooltip="Close")
        close.on_click = lambda *_: self.close()
        self.root.put(close, 1209, 57)
        mini = TitleButton(a, "MinimizeButton_ControlTemplate", 28, 20, offset=(0, 16),
                           tooltip="Minimize")
        mini.on_click = lambda *_: self.minimize()
        self.root.put(mini, 1166, 57)
        self.setting_btn = TitleButton(a, "SettingButton_ControlTemplate", 26, 26,
                                       tooltip="Settings")
        self.setting_btn.on_click = lambda *_: self._toggle_settings()
        self.root.put(self.setting_btn, 1125, 54)
        light = TitleButton(a, "LightingButton_ControlTemplate", 35, 26, offset=(2, 1),
                            tooltip="Keyboard Lighting")
        light.on_click = lambda *_: self._open_lighting()
        if self.model.lighting_type != 0:
            self.root.put(light, 1025, 54)

        self.status_label = _label("", "ns-status", 0.5)
        self.status_label.set_size_request(1152, 20)
        self.root.put(self.status_label, 84, 724)

    # ============================================================ fan page
    def _build_fan_page(self) -> None:
        a, t = self.assets, self.t
        ox, oy = 84, 92
        self.root.put(_label(t("MUI_Fan_Control", "Fan Control"), "ns-header"), ox + 38, oy + 26)

        # CoolBoost (right aligned at margin 0,32,32,0)
        cb_text = t("MUI_Cool_Booster", "CoolBoost™")
        lbl = _label(cb_text, "ns-coolboost")
        tw = _text_width(self, cb_text, 16)
        right = ox + 1152 - 32
        self.coolboost = HexToggle(a, self._coolboost_toggled)
        self.root.put(self.coolboost, right - 52, oy + 32)
        self.root.put(lbl, right - 52 - 10 - tw, oy + 30)
        self.cb_info = InfoIcon(a)
        self.cb_info.on_click = lambda *_: self._toggle_coolboost_info()
        self.root.put(self.cb_info, right - 52 - 10 - tw - 5 - 16, oy + 34)

        names = {"auto": t("MUI_Auto", "Auto"), "max": t("MUI_Max", "Max"),
                 "custom": t("MUI_Custom", "Custom")}
        keys = {"auto": "Nitro_FanModeAutoStyle", "max": "Nitro_FanModeMaxStyle",
                "custom": "Nitro_FanModeCustomStyle"}
        self.fan_buttons: dict[str, ModeButton] = {}
        for i, mode in enumerate(("auto", "max", "custom")):
            b = ModeButton(a, keys[mode], names[mode])
            b.on_click = lambda w, m=mode: self._fan_mode_clicked(m)
            self.fan_buttons[mode] = b
            self.root.put(b, ox + 36, oy + 78 + i * 68)
            if i < 2:
                self.root.put(Separator(220, 2, "#333333", 0.7), ox + 36, oy + 78 + 56 + 5 + i * 68)

        # CPU (column 3, x=288..448) and GPU (column 6, x=880..1040) custom controls
        self.custom_widgets = []
        for fan, col_x, right_align in (("cpu", 288, True), ("gpu", 880, False)):
            text = t("MUI_CPU", "CPU") if fan == "cpu" else t("MUI_GPU_NAME", "GPU")
            lab = _label(text, "ns-slider-label")
            lw = _text_width(self, text, 18)
            self.root.put(lab, ox + col_x + (160 - lw if right_align else 0), oy + 151)
            slider = SpeedSlider(a, 0, 10, on_release=lambda w, v, f=fan: self._fan_speed(f, v))
            self.root.put(slider, ox + col_x, oy + 183)
            auto = AutoButton(a, t("MUI_Auto", "Auto"),
                              on_toggle=lambda w, on, f=fan: self._fan_auto(f, on))
            self.root.put(auto, ox + col_x + (80 if right_align else 0), oy + 223)
            setattr(self, f"{fan}_slider", slider)
            setattr(self, f"{fan}_auto", auto)
            self.custom_widgets += [lab, slider, auto]

        self.cpu_fan = FanView(a.image("img_fan.png"))
        self.gpu_fan = FanView(a.image("img_fan.png"))
        if self.model.fan_cpu:
            self.root.put(self.cpu_fan, ox + 448, oy + 60)
        if self.model.fan_gpu:
            self.root.put(self.gpu_fan, ox + 664, oy + 60)

    # ======================================================== monitoring
    def _build_monitor_page(self) -> None:
        a, t = self.assets, self.t
        self.root.put(_label(t("MUI_PowerPlan", "Power Plan"), "ns-header"), 84 + 38, 92 + 294)
        self.root.put(_label(t("MUI_Monitoring", "Monitoring"), "ns-header"), 84 + 310, 92 + 294)
        ox, oy = 84, 404

        # ---------------- power plan (left, 312 wide)
        self.root.put(_label(t("MUI_Mode", "Mode"), "ns-mode"), ox + 41, oy + 10)
        self.tabs: list = []
        self.tab_ac = TabButton(a, t("MUI_Pluggedin", "AC"), self.tabs)
        self.tab_bat = TabButton(a, t("MUI_Battery", "Battery"), self.tabs)
        self.tab_ac.on_select = lambda *_: self._show_plan_tab("ac")
        self.tab_bat.on_select = lambda *_: self._show_plan_tab("battery")
        self.root.put(self.tab_ac, ox + 18, oy + 42)
        self.root.put(self.tab_bat, ox + 18 + 120, oy + 42)
        plans = (("powersaver", "PowerPlan_PowerSaver_RadioButton_Style",
                  t("MUI_PowerSaver", "Power Saver")),
                 ("balance", "PowerPlan_Balance_RadioButton_Style", t("MUI_Balance", "Balance")),
                 ("performance", "PowerPlan_HighPerformance_RadioButton_Style",
                  t("MUI_High-Performance", "High-Performance")))
        self.plan_buttons: dict[str, ModeButton] = {}
        for i, (plan, key, text) in enumerate(plans):
            b = ModeButton(a, key, text, width=257, icon_x=7, text_x=45)
            b.on_click = lambda w, p=plan: self._plan_clicked(p)
            self.plan_buttons[plan] = b
            self.root.put(b, ox + 18, oy + 42 + 32 + 6 + i * 72)
            if i < 2:
                self.root.put(Separator(220, 2, "#333333", 0.7), ox + 18,
                              oy + 42 + 32 + 6 + 56 + 5 + i * 72)
        self.plan_source = "ac"

        # ---------------- monitoring (right)
        mx = ox + 312
        self.unit_label = _label("", "ns-mode")
        self.root.put(self.unit_label, mx, oy + 10)
        self.charts = {}
        self.minmax = {}
        for name, row_y, title in (("cpu", 40, t("MUI_CPU", "CPU")),
                                   ("gpu", 172, t("MUI_GPU_NAME", "GPU"))):
            ly = oy + row_y - (29 if name == "cpu" else 27) + 6
            labels = []
            for j, txt in enumerate((t("MUI_Min", "Min"), "--", t("MUI_Max", "Max"), "--")):
                lab = _label(txt, "ns-axis")
                self.root.put(lab, mx + 578 + j * 35.5, ly)
                labels.append(lab)
            self.minmax[name] = (labels[1], labels[3])
            chart = LineChart(a, title)
            self.root.put(chart, mx, oy + row_y)
            self.charts[name] = chart
            temp = _label("--°", "ns-temp")
            self.root.put(temp, mx + 735, oy + row_y + 4)
            usage = _label("--", "ns-usage")
            pct = _label("%", "ns-usage-pct")
            box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=2)
            box.append(usage)
            pct.set_valign(Gtk.Align.END)
            pct.set_margin_bottom(5)
            box.append(pct)
            self.root.put(box, mx + 735, oy + row_y + 55)
            setattr(self, f"{name}_temp_label", temp)
            setattr(self, f"{name}_usage_label", usage)

    # =================================================== settings popup
    def _build_settings_popup(self) -> None:
        a, t = self.assets, self.t
        # Placement=Bottom of the gear, HorizontalOffset=-231, VerticalOffset=5
        self.settings = Popup(340, 306, arrow_x=231 + 13)
        rows: list[tuple[str, int]] = []
        y = 25

        def row(widget_label, height=36, border=True, bold=False):
            nonlocal y
            lab = _label(widget_label, "ns-popup-title" if bold else "ns-popup-text")
            lab.set_size_request(230, height)
            lab.set_wrap(True)
            lab.set_valign(Gtk.Align.CENTER)
            box = Gtk.Fixed()
            box.put(lab, 0, 0)
            if border:
                box.put(Separator(315, 1, theme.POPUP_LINE), 0, height - 1)
            self.settings.put(box, 15, y)
            r = (box, y, height)
            y += height
            return r

        self.settings_rows = {}
        self.settings_rows["advanced"] = row(t("MUI_Advanced_Settings", "Advanced Settings"),
                                             25, False, True)
        self.settings_rows["sticky"] = row(t("MUI_Sticky_Key", "Sticky keys"))
        self.settings_rows["winkey"] = row(t("MUI_Win_And_Menu_Key", "Windows and menu key"))
        self.settings_rows["unit"] = row(t("MUI_Unit_Temperature", "Temperature units"))
        self.settings_rows["dgpu"] = row(t("MUI_Discrete_GPU", "Discrete GPU only"))
        self.settings_rows["overdrive"] = row(t("MUI_LCD_Overdrive", "LCD Overdrive"))
        self.settings_rows["kbtitle"] = row(t("MUI_Keyboard_Settings", "Keyboard Settings"),
                                            25, False, True)
        self.settings_rows["backlight"] = row(t("MUI_Backlight_Timeout",
                                                "Backlight off after 30 seconds"), border=False)

        def toggle_at(name, cb):
            box = self.settings_rows[name][0]
            tg = HexToggle(a, cb)
            box.put(tg, 315 - 52, (self.settings_rows[name][2] - 20) / 2)
            return tg

        self.sticky_toggle = toggle_at("sticky", self._sticky_toggled)
        self.winkey_toggle = toggle_at("winkey", self._winkey_toggled)
        self.dgpu_toggle = toggle_at("dgpu", self._dgpu_toggled)
        self.overdrive_toggle = toggle_at("overdrive", self._overdrive_toggled)
        self.backlight_toggle = toggle_at("backlight", self._backlight_toggled)

        unit_box = self.settings_rows["unit"][0]
        self.unit_group: list = []
        self.unit_c = HexRadio(a, self.unit_group, lambda *_: self._set_unit("C"))
        self.unit_f = HexRadio(a, self.unit_group, lambda *_: self._set_unit("F"))
        unit_box.put(self.unit_c, 204, 8)
        unit_box.put(_label("˚C", "ns-popup-unit"), 233, 5)
        unit_box.put(self.unit_f, 268, 8)
        unit_box.put(_label("˚F", "ns-popup-unit"), 297, 5)

        self.sticky_settings = None
        src = Gio.SettingsSchemaSource.get_default()
        if src and src.lookup("org.gnome.desktop.a11y.keyboard", True):
            self.sticky_settings = Gio.Settings.new("org.gnome.desktop.a11y.keyboard")
            self.sticky_toggle.set_active(self.sticky_settings.get_boolean("stickykeys-enable"))
            self.sticky_settings.connect(
                "changed::stickykeys-enable",
                lambda s, k: self.sticky_toggle.set_active(s.get_boolean(k)))
        (self.unit_f if self.prefs.get("unit") == "F" else self.unit_c).set_checked(True)
        self.settings.set_visible(False)
        self.root.put(self.settings, 1125 - 231, 54 + 26 + 5 - 15)
        self._layout_settings()

    def _layout_settings(self) -> None:
        """Hide rows the hardware does not support and re-flow the popup."""
        m = self.model
        visible = {"dgpu": bool(self.hw.get("gpu_mode_supported")),
                   "sticky": self.sticky_settings is not None and "Sticky_Key" in m.keyboard,
                   "winkey": any(k.startswith("Windowskey") for k in m.keyboard),
                   "unit": "Temperature" in m.advanced,
                   "overdrive": "LCD" in m.advanced and bool(self.hw.get("lcd_overdrive_supported")),
                   "backlight": "Backlight" in m.keyboard
                   and bool(self.hw.get("backlight_timeout_supported"))}
        visible["kbtitle"] = visible["backlight"]
        y = 25
        for name, (box, _y, h) in self.settings_rows.items():
            show = visible.get(name, True)
            box.set_visible(show)
            if show:
                self.settings.move(box, 15, y)
                y += h
        self.settings.resize(y + 10)

    def _layout_settings_if_changed(self) -> None:
        flags = tuple(bool(self.hw.get(k)) for k in ("gpu_mode_supported", "lcd_overdrive_supported",
                                                     "backlight_timeout_supported"))
        if flags != self._dgpu_supported_seen:
            self._dgpu_supported_seen = flags
            self._layout_settings()

    # =============================================== coolboost info popup
    def _build_coolboost_popup(self) -> None:
        t = self.t
        pop = Popup(276, 115, arrow_x=34)
        msg = _label(t("MUI_Cool_Booster_Notification",
                       "If CoolBoost™ is ON, it increases the maximum fan speed."),
                     "ns-popup-small")
        msg.set_wrap(True)
        msg.set_size_request(256, -1)
        msg.set_max_width_chars(40)
        pop.put(msg, 10, 26)
        hint = _label(t("MUI_CoolBoost_ToolTip", "*This feature only supports Auto mode."),
                      "ns-popup-hint")
        hint.set_wrap(True)
        hint.set_size_request(256, -1)
        pop.put(hint, 10, 88)
        pop.set_visible(False)
        self.cb_popup = pop
        self.root.put(pop, 0, 0)

    def _toggle_coolboost_info(self) -> None:
        pos = widget_pos(self.cb_info, self.root)
        if pos is None:
            return
        x, y = pos
        self.root.move(self.cb_popup, x - 30 + 8 - 4, y + 16 + 5 - 10)
        self.cb_popup.set_visible(not self.cb_popup.get_visible())

    # ======================================================== lighting
    def _build_lighting(self) -> None:
        self.light_layer = Gtk.Fixed()
        mask = Gtk.DrawingArea()
        mask.set_size_request(1320, 760)
        bg_path = self.assets.image("img_bg_nitro.png")
        self._mask_surface = None
        if bg_path:
            import cairo
            self._mask_surface = cairo.ImageSurface.create_from_png(bg_path)

        def draw_mask(_a, cr, w, h):
            cr.set_source_rgba(0, 0, 0, 0.7)
            if self._mask_surface is not None:
                cr.mask_surface(self._mask_surface, 0, 0)
            else:
                cr.paint()

        mask.set_draw_func(draw_mask)
        self.light_layer.put(mask, 0, 0)
        self.lighting = LightingPanel(self.assets, self._apply_keyboard, self._close_lighting,
                                      list(self.prefs.get("recent_colors", [])),
                                      self._save_recent, self.model.wave_color_enabled)
        self.light_layer.put(self.lighting, (1320 - PANEL_W) // 2, (760 - PANEL_H) // 2 + 6)
        self.light_layer.set_visible(False)
        self.light_layer.set_can_target(True)
        self.overlay.add_overlay(self.light_layer)

    def _open_lighting(self) -> None:
        self._close_popups()
        self.lighting.load(self.state["keyboard"])
        self.light_layer.set_visible(True)

    def _close_lighting(self) -> None:
        self.lighting.close_popups()
        self.light_layer.set_visible(False)

    def _apply_keyboard(self, kb: dict) -> None:
        self.state["keyboard"] = kb
        self._send("set_keyboard", keyboard=kb)

    def _save_recent(self, colors: list[str]) -> None:
        self.prefs["recent_colors"] = colors
        self._save_prefs()

    # ===================================================== interaction
    def _global_click(self, gesture, _n, x, y) -> None:
        """Close popups when clicking outside of them (WPF StaysOpen=False)."""
        for pop, anchor in ((self.settings, self.setting_btn), (self.cb_popup, self.cb_info)):
            if not pop.get_visible():
                continue
            if _inside(pop, self.root, x, y) or _inside(anchor, self.root, x, y):
                continue
            pop.set_visible(False)
        if self.light_layer.get_visible():
            cp = self.lighting.color_popup
            # a press outside the colour popup closes it; releasing on a swatch reopens it
            if cp.get_visible() and not _inside(cp, self.root, x, y):
                cp.set_visible(False)

    def _key(self, _c, keyval, _code, _state) -> bool:
        if keyval == Gdk.KEY_Escape:
            if self.light_layer.get_visible():
                if self.lighting.color_popup.get_visible():
                    self.lighting.close_popups()
                else:
                    self._close_lighting()
                return True
            self._close_popups()
            return True
        return False

    def _close_popups(self) -> None:
        self.settings.set_visible(False)
        self.cb_popup.set_visible(False)

    def _toggle_settings(self) -> None:
        show = not self.settings.get_visible()
        self._close_popups()
        if show:
            self._layout_settings()
        self.settings.set_visible(show)

    # ------------------------------------------------------------- fan
    def _fan_mode_clicked(self, mode: str) -> None:
        if self.state["fan"]["mode"] == mode:
            return
        self.state["fan"]["mode"] = mode
        self._update_fan_ui()
        f = self.state["fan"]
        self._send("set_fan", mode=mode, cpu_auto=f["cpu_auto"], gpu_auto=f["gpu_auto"],
                   cpu_percent=f["cpu_percent"], gpu_percent=f["gpu_percent"])

    def _fan_speed(self, fan: str, value: int) -> None:
        self.state["fan"][f"{fan}_percent"] = value * 10
        self._send("set_fan_speed", fan=fan, percent=value * 10)

    def _fan_auto(self, fan: str, on: bool) -> None:
        self.state["fan"][f"{fan}_auto"] = on
        self._update_fan_ui()
        f = self.state["fan"]
        self._send("set_fan", mode="custom", cpu_auto=f["cpu_auto"], gpu_auto=f["gpu_auto"],
                   cpu_percent=f["cpu_percent"], gpu_percent=f["gpu_percent"])

    def _coolboost_toggled(self, _w, on: bool) -> None:
        self.state["coolboost"] = on
        self._send("set_coolboost", on=on)

    def _update_fan_ui(self) -> None:
        f = self.state["fan"]
        for mode, b in self.fan_buttons.items():
            b.set_checked(mode == f["mode"])
        custom = f["mode"] == "custom"
        for w in self.custom_widgets:
            w.set_visible(custom)
        for fan in ("cpu", "gpu"):
            slider, auto = getattr(self, f"{fan}_slider"), getattr(self, f"{fan}_auto")
            auto.set_active(f[f"{fan}_auto"])
            slider.set_enabled(not f[f"{fan}_auto"])
            if not slider.pressed:
                slider.set_value(f[f"{fan}_percent"] // 10)
        self.coolboost.set_enabled(f["mode"] == "auto")

    # ------------------------------------------------------- power plan
    def _show_plan_tab(self, source: str) -> None:
        self.plan_source = source
        (self.tab_ac if source == "ac" else self.tab_bat).set_selected(True)
        current = self.state["power"][source]
        for plan, b in self.plan_buttons.items():
            b.set_checked(plan == current)

    def _plan_clicked(self, plan: str) -> None:
        self.state["power"][self.plan_source] = plan
        self._show_plan_tab(self.plan_source)
        self._send("set_power_plan", source=self.plan_source, plan=plan)

    # --------------------------------------------------------- settings
    def _sticky_toggled(self, _w, on: bool) -> None:
        if self.sticky_settings is not None:
            self.sticky_settings.set_boolean("stickykeys-enable", on)

    def _winkey_toggled(self, _w, on: bool) -> None:
        self.state["win_key"] = on
        self._send("set_win_key", on=on)

    def _overdrive_toggled(self, _w, on: bool) -> None:
        self.state["lcd_overdrive"] = on
        self._send("set_lcd_overdrive", on=on)
        if on and not self.prefs.get("overdrive_reminded"):
            self.prefs["overdrive_reminded"] = True
            self._save_prefs()
            self._show_status(self.t("MUI_Reminder_Overdrive",
                                     "To get the best Overdrive performance, please keep "
                                     "maximum screen refresh rate."), error=False)

    def _backlight_toggled(self, _w, on: bool) -> None:
        self.state["backlight_timeout"] = on
        self._send("set_backlight_timeout", on=on)

    def _dgpu_toggled(self, w, on: bool) -> None:
        dialog = Gtk.AlertDialog(
            message=self.t("MUI_Discrete_GPU", "Discrete GPU only"),
            detail="The change takes effect after a restart.",
            buttons=[self.t("MUI_Restart_Later", "Restart Later"),
                     self.t("MUI_Restart_Now", "Restart Now"), "Cancel"],
            cancel_button=2, default_button=1)

        def done(dlg, res):
            try:
                choice = dlg.choose_finish(res)
            except GLib.Error:
                choice = 2
            if choice == 2:
                w.set_active(not on)
                return
            reboot = choice == 1

            def after(_r):
                if reboot:
                    Gio.Subprocess.new(["systemctl", "reboot"], Gio.SubprocessFlags.NONE)
            self._send("set_gpu_mode", after=after, discrete_only=on)

        dialog.choose(self, None, done)

    def _set_unit(self, unit: str) -> None:
        self.prefs["unit"] = unit
        self._save_prefs()
        for c in self.charts.values():
            c.unit = unit
        self._render_temps()

    # ======================================================== daemon I/O
    def _send(self, cmd: str, after=None, **kw) -> None:
        def ok(resp):
            if self._status_is_error:
                self._show_status("")
            if after:
                after(resp)
            return False

        def fail(e):
            self._show_status(str(e))
            self._poll(force=True)
            return False

        self.worker.submit(lambda: self.client.request(cmd, **kw), ok, fail)

    def _poll(self, force: bool = False) -> bool:
        if self._poll_busy and not force:
            return True
        self._poll_busy = True

        def fetch():
            gpu_idle, gpu_load, gpu_temp = self.gpu.sample()
            cpu = self.cpu_load.sample()
            try:
                status = self.client.request("status")
                err = None
            except DaemonError as e:
                status, err = None, str(e)
            return status, err, cpu, gpu_idle, gpu_load, gpu_temp

        self.worker.submit(fetch, self._apply_status, lambda e: setattr(self, "_poll_busy", False))
        return True

    def _apply_status(self, res) -> bool:
        self._poll_busy = False
        status, err, cpu_load, gpu_idle, gpu_load, nv_temp = res
        sensors = {}
        if status is None:
            self._show_status(err or "NitroSense service unavailable")
        else:
            first = not getattr(self, "_got_status", False)
            self._got_status = True
            self.state = status["state"]
            self.hw = status.get("hw", {})
            self.ac_online = status.get("ac", True)
            sensors = status.get("sensors", {})
            if not status.get("ready"):
                self._show_status("Hardware interface unavailable: " + (status.get("error") or ""))
            elif self.status_label.get_label().startswith(("NitroSense service", "lost conn",
                                                           "Hardware interface")):
                self._show_status("")
            self._layout_settings_if_changed()
            self._update_fan_ui()
            if first:
                self._show_plan_tab("ac" if self.ac_online else "battery")
            else:
                self._show_plan_tab(self.plan_source)
            hw_cb = self.hw.get("coolboost")
            self.coolboost.set_active(self.state["coolboost"] if self.state["coolboost"] is not None
                                      else bool(hw_cb))
            od = self.hw.get("lcd_overdrive")
            self.overdrive_toggle.set_active(od if od is not None else bool(self.state["lcd_overdrive"]))
            self.winkey_toggle.set_active(True if self.state["win_key"] is None
                                          else self.state["win_key"])
            bl = self.hw.get("backlight_timeout")
            self.backlight_toggle.set_active(bl if bl is not None
                                             else bool(self.state["backlight_timeout"]))
            self.dgpu_toggle.set_active(self.hw.get("gpu_mode") == 1)
            for name, enabled in (("overdrive", status.get("ready")),
                                  ("winkey", status.get("ready")),
                                  ("backlight", status.get("ready"))):
                getattr(self, f"{name}_toggle").set_enabled(bool(enabled))

        self.cpu_fan.set_rpm(sensors.get("cpu_fan"))
        self.gpu_fan.set_rpm(sensors.get("gpu_fan"))
        cpu_temp = sensors.get("cpu_temp")
        gpu_temp = sensors.get("gpu_temp") or nv_temp
        if gpu_idle and self.gpu.present:
            gpu_temp, gpu_load = None, None
            self.charts["gpu"].idle_text = self.t("MUI_GPU_Idle", "Discrete GPU is idle.")
        else:
            self.charts["gpu"].idle_text = None
        self.charts["cpu"].push(cpu_temp, cpu_load)
        self.charts["gpu"].push(gpu_temp, gpu_load)
        self._current = {"cpu": (cpu_temp, cpu_load), "gpu": (gpu_temp, gpu_load)}
        self._render_temps()
        return False

    def _render_temps(self) -> None:
        unit = self.prefs.get("unit", "C")
        sym = "°C" if unit == "C" else "°F"
        self.unit_label.set_label(f"{self.t('MUI_TEMPERATURE', 'Temperature')} ({sym}) / "
                                  f"{self.t('MUI_Loading', 'Loading')} (%)")
        cur = getattr(self, "_current", {})
        for name in ("cpu", "gpu"):
            temp, load = cur.get(name, (None, None))
            getattr(self, f"{name}_temp_label").set_label(f"{_conv(temp, unit)}°")
            getattr(self, f"{name}_usage_label").set_label(
                "--" if load is None else str(round(load)))
            vals = [v for v in self.charts[name].temp if v is not None]
            lo, hi = self.minmax[name]
            lo.set_label(f"{_conv(min(vals), unit)}°" if vals else "--")
            hi.set_label(f"{_conv(max(vals), unit)}°" if vals else "--")

    def _show_status(self, text: str, error: bool = True) -> None:
        self._status_is_error = bool(text) and error
        self.status_label.set_label(text)
        if not error and text:
            GLib.timeout_add_seconds(8, lambda: (self.status_label.get_label() == text
                                                 and self.status_label.set_label(""), False)[1])

    # ============================================================ prefs
    def _load_prefs(self) -> dict:
        try:
            with open(PREFS) as f:
                data = json.load(f)
                return data if isinstance(data, dict) else {}
        except (OSError, ValueError):
            return {}

    def _save_prefs(self) -> None:
        os.makedirs(os.path.dirname(PREFS), exist_ok=True)
        tmp = PREFS + ".tmp"
        with open(tmp, "w") as f:
            json.dump(self.prefs, f)
        os.replace(tmp, PREFS)


def _conv(c, unit: str) -> str:
    if c is None:
        return "--"
    return str(round(c * 9 / 5 + 32)) if unit == "F" else str(round(c))


def _inside(widget: Gtk.Widget, root: Gtk.Widget, x: float, y: float) -> bool:
    pos = widget_pos(widget, root)
    if pos is None:
        return False
    wx, wy = pos
    w, h = widget.get_width(), widget.get_height()
    return wx <= x <= wx + w and wy <= y <= wy + h


def _text_width(widget: Gtk.Widget, text: str, size: float) -> int:
    layout = widget.create_pango_layout(text)
    from gi.repository import Pango
    desc = Pango.FontDescription.from_string(theme.FONT)
    desc.set_absolute_size(size * Pango.SCALE)
    layout.set_font_description(desc)
    return layout.get_pixel_size()[0]


class App(Gtk.Application):
    def __init__(self) -> None:
        super().__init__(application_id="local.nitrosense.NitroSense",
                         flags=Gio.ApplicationFlags.DEFAULT_FLAGS)

    def do_activate(self) -> None:
        win = self.get_active_window()
        if win is None:
            win = MainWindow(self)
        win.present()


def main() -> int:
    return App().run(None)
