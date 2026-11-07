"""nitrosensed - root service that owns the Acer WMI interface.

Mirrors the Windows NitroSense service + agent: executes hardware requests
from the GUI, remembers the settings and re-applies them at boot, after
resume from suspend, and (power plans) whenever the AC adapter changes.
"""

from __future__ import annotations

import copy
import glob
import json
import logging
import os
import pwd
import re
import socket
import socketserver
import struct
import sys
import threading
import time

from . import encoders as enc
from . import model as model_cfg
from . import power
from .controller import Controller, wait_for_acpi_call
from .protocol import (ALLOWED_USERS_FILE, MAX_REQUEST, SOCKET_DIR, SOCKET_PATH,
                       STATE_DIR, STATE_FILE, default_state)
from .wmi import WmiError

log = logging.getLogger("nitrosense.daemon")

HEX_COLOR = re.compile(r"^#[0-9A-Fa-f]{6}$")


class ValidationError(ValueError):
    pass


def _bool(v) -> bool:
    if isinstance(v, bool):
        return v
    raise ValidationError("expected boolean")


def _int(v, lo: int, hi: int) -> int:
    if isinstance(v, bool) or not isinstance(v, int) or not lo <= v <= hi:
        raise ValidationError(f"expected integer {lo}..{hi}")
    return v


def _choice(v, options) -> str:
    if v not in options:
        raise ValidationError(f"expected one of {', '.join(options)}")
    return v


def validate_keyboard(kb) -> dict:
    if not isinstance(kb, dict):
        raise ValidationError("keyboard must be an object")
    out = {
        "mode": _choice(kb.get("mode"), ("static", "dynamic")),
        "brightness": _int(kb.get("brightness"), 1, 5),
        "effect": _choice(kb.get("effect"), enc.EFFECTS),
        "speed": _int(kb.get("speed"), 1, 9),
        "direction": _int(kb.get("direction"), 1, 2),
    }
    zones_on = kb.get("zones_on")
    colors = kb.get("zone_colors")
    if not (isinstance(zones_on, list) and len(zones_on) == 4):
        raise ValidationError("zones_on must have 4 entries")
    if not (isinstance(colors, list) and len(colors) == 4):
        raise ValidationError("zone_colors must have 4 entries")
    out["zones_on"] = [_bool(z) for z in zones_on]
    for c in colors + [kb.get("effect_color")]:
        if not isinstance(c, str) or not HEX_COLOR.match(c):
            raise ValidationError("colors must be #RRGGBB")
    out["zone_colors"] = [c.upper() for c in colors]
    out["effect_color"] = kb["effect_color"].upper()
    return out


class Daemon:
    def __init__(self) -> None:
        self.model = model_cfg.load()
        self.ctl = Controller(adjust=enc.ColorAdjust(*self.model.zone_adjust))
        self.lock = threading.RLock()
        self.state = self._load_state()
        self.ready = False
        self.error = ""
        self._sensor_cache: tuple[float, dict] = (0.0, {})
        self._ac = power.ac_online()
        self.gpu_mode_supported = False
        self.gpu_mode = 0
        self.hw_lcd_overdrive = None
        self.hw_coolboost = None
        self.hw_backlight_timeout = None

    # ------------------------------------------------------------ state
    def _load_state(self) -> dict:
        state = default_state(self.model.zone_default_colors)
        try:
            with open(STATE_FILE) as f:
                saved = json.load(f)
            for key, value in saved.items():
                if key in state:
                    if isinstance(state[key], dict) and isinstance(value, dict):
                        state[key].update(value)
                    else:
                        state[key] = value
        except FileNotFoundError:
            pass
        except (OSError, ValueError) as e:
            log.warning("state file unreadable, using defaults: %s", e)
        return state

    def _save_state(self) -> None:
        os.makedirs(STATE_DIR, exist_ok=True)
        tmp = STATE_FILE + ".tmp"
        with open(tmp, "w") as f:
            json.dump(self.state, f, indent=1)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, STATE_FILE)

    # ------------------------------------------------------------ apply
    def startup(self) -> None:
        if not wait_for_acpi_call():
            self.error = "acpi_call kernel module is not loaded"
            log.error(self.error)
        ok, why = self.ctl.wmi.available()
        if not ok:
            self.error = why
            log.error("hardware interface unavailable: %s", why)
        else:
            self.ready = True
            self.gpu_mode_supported = self.ctl.gpu_mode_supported()
            if self.gpu_mode_supported:
                self.gpu_mode = self.ctl.get_gpu_mode()
            self.hw_lcd_overdrive = self.ctl.get_lcd_overdrive()
            self.hw_coolboost = self.ctl.get_coolboost()
            bl = self.ctl.get_backlight()
            self.hw_backlight_timeout = bl[1] if bl else None
        self.reapply("startup")

    def reapply(self, reason: str) -> None:
        """Restore persisted settings (Windows: NitroSense agent at logon/resume)."""
        log.info("re-applying settings (%s)", reason)
        with self.lock:
            st = copy.deepcopy(self.state)
        if self.ready:
            steps = [
                ("fan", lambda: self.ctl.set_fan_mode(**st["fan"])),
                ("coolboost", lambda: st["coolboost"] is not None and st["fan"]["mode"] == "auto"
                 and self.ctl.set_coolboost(st["coolboost"])),
                ("keyboard", lambda: st["keyboard_applied"] and self.ctl.apply_keyboard(st["keyboard"])),
                ("lcd_overdrive", lambda: st["lcd_overdrive"] is not None
                 and self.ctl.set_lcd_overdrive(st["lcd_overdrive"])),
                ("win_key", lambda: st["win_key"] is not None and self.ctl.set_win_key(st["win_key"])),
                ("backlight_timeout", lambda: st["backlight_timeout"] is not None
                 and self.ctl.set_backlight_timeout(st["backlight_timeout"])),
            ]
            for name, fn in steps:
                try:
                    fn()
                except (WmiError, KeyError, ValueError) as e:
                    log.warning("re-apply %s failed: %s", name, e)
        if st["power_applied"]:
            self.apply_power(force=True)

    def apply_power(self, force: bool = False) -> None:
        ac = power.ac_online()
        if not force and ac == self._ac:
            return
        self._ac = ac
        with self.lock:
            plan = self.state["power"]["ac" if ac else "battery"]
            applied = self.state["power_applied"]
        if applied:
            power.apply_plan(plan)

    # ---------------------------------------------------------- sensors
    def sensors(self) -> dict:
        now = time.monotonic()
        ts, cached = self._sensor_cache
        if now - ts < 0.7:
            return cached
        data = {"cpu_temp": None, "gpu_temp": None, "cpu_fan": None, "gpu_fan": None}
        if self.ready:
            data["cpu_temp"] = self.ctl.read_sensor(enc.SENSOR_CPU_TEMP)
            data["gpu_temp"] = self.ctl.read_sensor(enc.SENSOR_GPU_TEMP)
            data["cpu_fan"] = self.ctl.read_sensor(enc.SENSOR_CPU_FAN)
            data["gpu_fan"] = self.ctl.read_sensor(enc.SENSOR_GPU_FAN)
        if not data["cpu_temp"]:
            data["cpu_temp"] = _hwmon_temp(("k10temp", "coretemp", "zenpower"))
        self._sensor_cache = (now, data)
        return data

    # ---------------------------------------------------------- requests
    def handle(self, req: dict) -> dict:
        cmd = req.get("cmd")
        if cmd == "status":
            with self.lock:
                st = copy.deepcopy(self.state)
            return {"ok": True, "ready": self.ready, "error": self.error,
                    "sensors": self.sensors(), "state": st, "ac": power.ac_online(),
                    "hw": {"lcd_overdrive": self.hw_lcd_overdrive, "coolboost": self.hw_coolboost,
                           "backlight_timeout": self.hw_backlight_timeout,
                           "gpu_mode_supported": self.gpu_mode_supported,
                           "gpu_mode": self.gpu_mode}}
        if cmd == "set_power_plan":
            source = _choice(req.get("source"), ("ac", "battery"))
            plan = _choice(req.get("plan"), power.PLANS)
            with self.lock:
                self.state["power"][source] = plan
                self.state["power_applied"] = True
                self._save_state()
            if (source == "ac") == power.ac_online():
                power.apply_plan(plan)
            return {"ok": True}
        if not self.ready:
            return {"ok": False, "error": self.error or "hardware interface unavailable"}
        handler = getattr(self, "cmd_" + str(cmd), None)
        if handler is None:
            return {"ok": False, "error": f"unknown command {cmd!r}"}
        handler(req)
        return {"ok": True}

    def cmd_set_fan(self, req: dict) -> None:
        with self.lock:
            fan = dict(self.state["fan"])
        fan["mode"] = _choice(req.get("mode", fan["mode"]), ("auto", "max", "custom"))
        for k in ("cpu_auto", "gpu_auto"):
            if k in req:
                fan[k] = _bool(req[k])
        for k in ("cpu_percent", "gpu_percent"):
            if k in req:
                fan[k] = _int(req[k], 0, 100) // 10 * 10
        self.ctl.set_fan_mode(**fan)
        with self.lock:
            self.state["fan"] = fan
            self._save_state()

    def cmd_set_fan_speed(self, req: dict) -> None:
        fan = _choice(req.get("fan"), ("cpu", "gpu"))
        pct = _int(req.get("percent"), 0, 100) // 10 * 10
        with self.lock:
            if self.state["fan"]["mode"] != "custom":
                raise ValidationError("fan speed can only be set in Custom mode")
        self.ctl.set_fan_speed(fan, pct)
        with self.lock:
            self.state["fan"][fan + "_percent"] = pct
            self._save_state()

    def cmd_set_coolboost(self, req: dict) -> None:
        on = _bool(req.get("on"))
        self.ctl.set_coolboost(on)
        self.hw_coolboost = on
        with self.lock:
            self.state["coolboost"] = on
            self._save_state()

    def cmd_set_keyboard(self, req: dict) -> None:
        kb = validate_keyboard(req.get("keyboard"))
        self.ctl.apply_keyboard(kb)
        with self.lock:
            self.state["keyboard"] = kb
            self.state["keyboard_applied"] = True
            self._save_state()

    def cmd_set_lcd_overdrive(self, req: dict) -> None:
        on = _bool(req.get("on"))
        self.ctl.set_lcd_overdrive(on)
        self.hw_lcd_overdrive = on
        with self.lock:
            self.state["lcd_overdrive"] = on
            self._save_state()

    def cmd_set_win_key(self, req: dict) -> None:
        on = _bool(req.get("on"))
        self.ctl.set_win_key(on)
        with self.lock:
            self.state["win_key"] = on
            self._save_state()

    def cmd_set_backlight_timeout(self, req: dict) -> None:
        on = _bool(req.get("on"))
        self.ctl.set_backlight_timeout(on)
        self.hw_backlight_timeout = on
        with self.lock:
            self.state["backlight_timeout"] = on
            self._save_state()

    def cmd_set_gpu_mode(self, req: dict) -> None:
        if not self.gpu_mode_supported:
            raise ValidationError("this model has no discrete-GPU switch")
        on = _bool(req.get("discrete_only"))
        self.ctl.set_gpu_mode(on)
        self.gpu_mode = 1 if on else 2


def _hwmon_temp(names) -> int | None:
    for hw in glob.glob("/sys/class/hwmon/hwmon*"):
        try:
            with open(os.path.join(hw, "name")) as f:
                if f.read().strip() not in names:
                    continue
            with open(os.path.join(hw, "temp1_input")) as f:
                return round(int(f.read()) / 1000)
        except (OSError, ValueError):
            continue
    return None


def _allowed_uids() -> set[int]:
    uids = {0}
    try:
        with open(ALLOWED_USERS_FILE) as f:
            for line in f:
                name = line.strip()
                if name and not name.startswith("#"):
                    try:
                        uids.add(pwd.getpwnam(name).pw_uid)
                    except KeyError:
                        pass
    except OSError:
        pass
    return uids


class _Handler(socketserver.StreamRequestHandler):
    daemon_ref: Daemon

    def handle(self) -> None:
        creds = self.connection.getsockopt(socket.SOL_SOCKET, socket.SO_PEERCRED,
                                           struct.calcsize("3i"))
        _pid, uid, _gid = struct.unpack("3i", creds)
        if uid not in _allowed_uids():
            self._send({"ok": False, "error": "permission denied"})
            return
        while True:
            line = self.rfile.readline(MAX_REQUEST)
            if not line:
                return
            try:
                req = json.loads(line)
                if not isinstance(req, dict):
                    raise ValidationError("request must be an object")
                resp = self.daemon_ref.handle(req)
            except (ValidationError, ValueError) as e:
                resp = {"ok": False, "error": str(e)}
            except WmiError as e:
                log.warning("hardware error: %s", e)
                resp = {"ok": False, "error": f"hardware error: {e}"}
            except Exception as e:  # keep the service alive
                log.exception("request failed")
                resp = {"ok": False, "error": f"internal error: {e}"}
            self._send(resp)

    def _send(self, obj: dict) -> None:
        self.wfile.write(json.dumps(obj).encode() + b"\n")
        self.wfile.flush()


class _Server(socketserver.ThreadingMixIn, socketserver.UnixStreamServer):
    daemon_threads = True


def start_server(d: Daemon, path: str) -> _Server:
    directory = os.path.dirname(path)
    os.makedirs(directory, exist_ok=True)
    if directory == SOCKET_DIR:
        os.chmod(directory, 0o755)
    if os.path.exists(path):
        os.unlink(path)
    _Handler.daemon_ref = d
    server = _Server(path, _Handler)
    os.chmod(path, 0o666)  # access is checked per connection with SO_PEERCRED
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server


def main() -> int:
    logging.basicConfig(level=os.environ.get("NITROSENSE_LOG", "INFO"),
                        format="%(levelname)s %(name)s: %(message)s")
    if os.geteuid() != 0:
        print("nitrosensed must run as root", file=sys.stderr)
        return 1

    import gi
    gi.require_version("Gio", "2.0")
    from gi.repository import Gio, GLib

    d = Daemon()
    d.startup()

    server = start_server(d, SOCKET_PATH)

    loop = GLib.MainLoop()

    def on_sleep(_conn, _sender, _path, _iface, _signal, params, *_a):
        going_down = params.unpack()[0]
        if not going_down:
            GLib.timeout_add_seconds(2, lambda: (d.reapply("resume"), False)[1])

    try:
        bus = Gio.bus_get_sync(Gio.BusType.SYSTEM, None)
        bus.signal_subscribe("org.freedesktop.login1", "org.freedesktop.login1.Manager",
                             "PrepareForSleep", "/org/freedesktop/login1", None,
                             Gio.DBusSignalFlags.NONE, on_sleep)
    except GLib.Error as e:
        log.warning("cannot watch suspend/resume: %s", e)

    GLib.timeout_add_seconds(2, lambda: (d.apply_power(), True)[1])
    GLib.unix_signal_add(GLib.PRIORITY_DEFAULT, 15, lambda: (loop.quit(), False)[1])
    GLib.unix_signal_add(GLib.PRIORITY_DEFAULT, 2, lambda: (loop.quit(), False)[1])
    log.info("nitrosensed ready (hardware %s)", "OK" if d.ready else "UNAVAILABLE: " + d.error)
    loop.run()
    server.shutdown()
    try:
        os.unlink(SOCKET_PATH)
    except OSError:
        pass
    return 0


if __name__ == "__main__":
    sys.exit(main())
