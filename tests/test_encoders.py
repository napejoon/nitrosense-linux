"""Encoders must produce exactly what NitroSense 3.01.3056 sends (values derived from its IL)."""

import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from nitrosense import encoders as enc  # noqa: E402


class FanTests(unittest.TestCase):
    def test_all_modes(self):
        # CommonFunction.set_all_fan_mode: 9 | 4259840 / 8519680 / 12779520
        self.assertEqual(enc.fan_mode_all("auto"), 9 | 4259840)
        self.assertEqual(enc.fan_mode_all("max"), 9 | 8519680)
        self.assertEqual(enc.fan_mode_all("custom"), 9 | 12779520)

    def test_custom_behavior(self):
        # set_all_custom_fan_state: 9 | cpu<<16 | gpu<<22, auto=1 custom=3
        self.assertEqual(enc.fan_custom_behavior(False, False), 9 | 3 << 16 | 3 << 22)
        self.assertEqual(enc.fan_custom_behavior(True, False), 9 | 1 << 16 | 3 << 22)
        self.assertEqual(enc.fan_custom_behavior(False, True), 9 | 3 << 16 | 1 << 22)

    def test_speed(self):
        # set_single_custom_fan_speed: id (cpu 1, gpu 4) | percent << 8
        self.assertEqual(enc.fan_speed("cpu", 70), 1 | 70 << 8)
        self.assertEqual(enc.fan_speed("gpu", 100), 4 | 100 << 8)
        self.assertEqual(enc.fan_speed("gpu", 250), 4 | 100 << 8)

    def test_sysinfo(self):
        self.assertEqual(enc.sysinfo_query(enc.SENSOR_GPU_TEMP), 1 | 10 << 8)
        self.assertEqual(enc.sysinfo_value(2350 << 8), 2350)
        self.assertIsNone(enc.sysinfo_value((2350 << 8) | 3))


class MiscTests(unittest.TestCase):
    def test_coolboost(self):
        self.assertEqual(enc.coolboost_set(True), 7 | 1 << 16)
        self.assertEqual(enc.coolboost_set(False), 7)
        self.assertEqual(enc.COOLBOOST_GET, 519)
        self.assertTrue(enc.coolboost_decode(1 << 8))
        self.assertIsNone(enc.coolboost_decode(1))

    def test_backlight(self):
        self.assertEqual(enc.backlight_get(0), 1 | 524288)
        # Backlightoff_Checkbox_Checked: 2 | hk<<8 | 524288 | pct<<32 | 32985348833280
        self.assertEqual(enc.backlight_timeout_set(1, 100, True),
                         2 | 1 << 8 | 524288 | 100 << 32 | 32985348833280)
        self.assertEqual(enc.backlight_timeout_set(1, 100, False),
                         2 | 1 << 8 | 524288 | 100 << 32)
        self.assertEqual(enc.backlight_decode(100 << 32 | 30 << 40), (100, True))

    def test_profile(self):
        self.assertEqual(enc.win_key_set(True), 2 | 1 << 24)
        self.assertEqual(enc.win_key_set(False), 2)
        self.assertEqual(enc.lcd_overdrive_set(True), 16 | 281474976710656)
        self.assertEqual(enc.lcd_overdrive_set(False), 16)
        self.assertTrue(enc.lcd_overdrive_decode(1 << 48))

    def test_gpu_mode(self):
        self.assertEqual(enc.gpu_mode_set(True), 2 | 1 << 8)
        self.assertEqual(enc.gpu_mode_set(False), 2 | 2 << 8)
        self.assertTrue(enc.gpu_mode_supported(3 << 8))
        self.assertFalse(enc.gpu_mode_supported(0))


class KeyboardTests(unittest.TestCase):
    def test_static(self):
        self.assertEqual(enc.kb_static_backlight(5), 100 << 16)
        self.assertEqual(enc.kb_static_backlight(1), 0)
        self.assertEqual(enc.kb_zone_color(1, (0xFF, 0x80, 0x01)), 1 | 0xFF << 8 | 0x80 << 16 | 1 << 24)
        self.assertEqual(enc.kb_zone_color(4, (0, 0, 255)), 8 | 255 << 24)
        with self.assertRaises(ValueError):
            enc.kb_zone_color(5, (0, 0, 0))

    def test_zone_enable(self):
        # Change_Zone_Status: 8 | 1<<40 | 1<<41 | 1<<42 | 1<<43
        self.assertEqual(enc.kb_zone_enable((True, True, True, True)),
                         8 | 1099511627776 | 2199023255552 | 4398046511104 | 8796093022208)
        self.assertEqual(enc.kb_zone_enable((False, True, False, False)), 8 | 2199023255552)

    def test_dynamic(self):
        rgb = (10, 20, 30)
        color = 10 << 40 | 20 << 48 | 30 << 56
        # Set_Dynamic_Keyboard branches (pattern_select -> mode byte)
        self.assertEqual(enc.kb_dynamic_backlight("breathing", 5, 5, 1, rgb),
                         1 | 5 << 8 | 100 << 16 | color)
        self.assertEqual(enc.kb_dynamic_backlight("neon", 3, 3, 1, rgb), 2 | 3 << 8 | 50 << 16)
        self.assertEqual(enc.kb_dynamic_backlight("wave", 9, 4, 2, rgb),
                         3 | 9 << 8 | 75 << 16 | 134217728 | 2 << 32 | color)
        self.assertEqual(enc.kb_dynamic_backlight("shifting", 1, 2, 1, rgb),
                         4 | 1 << 8 | 25 << 16 | 1 << 32 | color)
        self.assertEqual(enc.kb_dynamic_backlight("zoom", 7, 5, 2, rgb),
                         5 | 7 << 8 | 100 << 16 | color)

    def test_fits_input_sizes(self):
        from nitrosense.wmi import METHODS
        v = enc.kb_dynamic_backlight("wave", 9, 5, 2, (255, 255, 255))
        self.assertLess(v, 1 << (8 * METHODS["SetGamingKBBacklight"][2]))
        self.assertLess(enc.kb_zone_color(4, (255, 255, 255)), 1 << (8 * METHODS["SetGamingRgbKb"][2]))


class AcpiCallParsing(unittest.TestCase):
    def test_parse(self):
        from nitrosense.wmi import WmiError, parse_acpi_call_result
        self.assertEqual(parse_acpi_call_result("{0x00, 0x2e, 0x09, 0x00}"), bytes([0, 0x2E, 9, 0]))
        self.assertEqual(parse_acpi_call_result("0x1a\n"), 0x1A)
        with self.assertRaises(WmiError):
            parse_acpi_call_result("Error: AE_NOT_FOUND")
        with self.assertRaises(WmiError):
            parse_acpi_call_result("not called")


if __name__ == "__main__":
    unittest.main()
