"""Daemon request handling against the simulated firmware (no root needed)."""

import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from fake_hw import make_daemon  # noqa: E402

from nitrosense import encoders as enc  # noqa: E402
from nitrosense.client import Client, DaemonError  # noqa: E402
from nitrosense import daemon as dmod  # noqa: E402

KB = {"mode": "static", "brightness": 4, "zones_on": [True, False, True, True],
      "zone_colors": ["#FF0000", "#00ff00", "#0000FF", "#FFFFFF"], "effect": "wave",
      "speed": 5, "direction": 1, "effect_color": "#FF0000"}


class DaemonTests(unittest.TestCase):
    def setUp(self):
        self.d = make_daemon()
        self.wmi = self.d.ctl.wmi

    def test_status(self):
        r = self.d.handle({"cmd": "status"})
        self.assertTrue(r["ok"])
        self.assertEqual(r["sensors"]["cpu_fan"], 2300)
        self.assertIsNotNone(r["sensors"]["cpu_temp"])

    def test_fan_modes(self):
        self.d.handle({"cmd": "set_fan", "mode": "max"})
        self.assertEqual(self.wmi.calls[-1], ("SetGamingFanBehavior", enc.fan_mode_all("max")))
        self.d.handle({"cmd": "set_fan", "mode": "custom", "cpu_percent": 73, "gpu_auto": True})
        self.assertEqual(self.wmi.calls[-3:], [
            ("SetGamingFanBehavior", enc.fan_custom_behavior(False, True)),
            ("SetGamingFanSpeed", enc.fan_speed("cpu", 70)),
            ("SetGamingFanSpeed", enc.fan_speed("gpu", 50))])
        self.d.handle({"cmd": "set_fan_speed", "fan": "gpu", "percent": 100})
        self.assertEqual(self.wmi.calls[-1], ("SetGamingFanSpeed", enc.fan_speed("gpu", 100)))

    def test_fan_speed_requires_custom(self):
        with self.assertRaises(dmod.ValidationError):
            self.d.handle({"cmd": "set_fan_speed", "fan": "cpu", "percent": 50})

    def test_keyboard_static_sequence(self):
        self.d.handle({"cmd": "set_keyboard", "keyboard": KB})
        names = [c[0] for c in self.wmi.calls]
        self.assertEqual(names, ["SetGamingKBBacklight"] + ["SetGamingRgbKb"] * 4)
        self.assertEqual(self.wmi.calls[0][1], enc.kb_static_backlight(4))
        # zone 2 is switched off -> written black; zone 3 keeps its colour
        self.assertEqual(self.wmi.calls[2][1], enc.kb_zone_color(2, (0, 0, 0)))
        self.assertEqual(self.wmi.calls[3][1], enc.kb_zone_color(3, (0, 0, 255)))

    def test_keyboard_validation(self):
        bad = dict(KB, brightness=9)
        with self.assertRaises(dmod.ValidationError):
            self.d.handle({"cmd": "set_keyboard", "keyboard": bad})
        bad = dict(KB, zone_colors=["red"] * 4)
        with self.assertRaises(dmod.ValidationError):
            self.d.handle({"cmd": "set_keyboard", "keyboard": bad})
        self.assertEqual(self.wmi.calls, [])

    def test_persist_and_reapply(self):
        self.d.handle({"cmd": "set_fan", "mode": "custom", "cpu_percent": 30})
        self.d.handle({"cmd": "set_keyboard", "keyboard": dict(KB, mode="dynamic")})
        self.d.handle({"cmd": "set_coolboost", "on": True})
        state_dir = dmod.STATE_DIR
        d2 = make_daemon(state_dir)
        d2.reapply("test")
        names = [c[0] for c in d2.ctl.wmi.calls]
        self.assertIn("SetGamingKBBacklight", names)
        self.assertEqual(d2.ctl.wmi.calls[0], ("SetGamingFanBehavior",
                                                enc.fan_custom_behavior(False, False)))
        # CoolBoost is only re-applied in Auto mode (NitroSense disables it otherwise)
        self.assertNotIn(("SetFunction", enc.coolboost_set(True)), d2.ctl.wmi.calls)

    def test_unsupported_features_rejected(self):
        # fresh daemon: firmware reported neither Overdrive active nor a readable timeout
        with self.assertRaises(dmod.ValidationError):
            self.d.handle({"cmd": "set_lcd_overdrive", "on": True})
        with self.assertRaises(dmod.ValidationError):
            self.d.handle({"cmd": "set_backlight_timeout", "on": True})
        self.assertEqual(self.wmi.calls, [])

    def test_power_plan(self):
        r = self.d.handle({"cmd": "set_power_plan", "source": "ac", "plan": "performance"})
        self.assertTrue(r["ok"])
        self.assertEqual(self.d.state["power"]["ac"], "performance")

    def test_unknown_and_bad_types(self):
        self.assertFalse(self.d.handle({"cmd": "rm -rf"})["ok"])
        with self.assertRaises(dmod.ValidationError):
            self.d.handle({"cmd": "set_coolboost", "on": 1})


class SocketTests(unittest.TestCase):
    def test_roundtrip(self):
        d = make_daemon()
        path = os.path.join(tempfile.mkdtemp(), "s.sock")
        server = dmod.start_server(d, path)
        try:
            c = Client(path)
            self.assertTrue(c.request("status")["ok"])
            c.request("set_fan", mode="max")
            with self.assertRaises(DaemonError):
                c.request("set_fan", mode="turbo")
            self.assertTrue(c.request("status")["ok"])  # connection still usable
        finally:
            server.shutdown()


if __name__ == "__main__":
    unittest.main()
