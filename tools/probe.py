#!/usr/bin/python3 -I
"""Read-only hardware probe: only getter methods, nothing is changed.

sudo python3 -I tools/probe.py
Exit status 0 when the sensors answer with plausible values.
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.realpath(__file__))))

from nitrosense import encoders as enc  # noqa: E402
from nitrosense.controller import read_hotkey_number  # noqa: E402
from nitrosense.wmi import AcerWmi, WmiError  # noqa: E402


def main() -> int:
    if os.geteuid() != 0:
        print("run as root (sudo)")
        return 2
    wmi = AcerWmi()
    ok, why = wmi.available()
    if not ok:
        print("hardware interface unavailable:", why)
        return 1
    print("WMBH path:", wmi._path("7A4DDFE7-5B5D-40B4-8595-4408E0CC7F56"))
    good = True
    for name, idx, lo, hi in (("CPU temperature (C)", enc.SENSOR_CPU_TEMP, 15, 105),
                              ("GPU temperature (C)", enc.SENSOR_GPU_TEMP, 0, 105),
                              ("CPU fan (RPM)", enc.SENSOR_CPU_FAN, 0, 9000),
                              ("GPU fan (RPM)", enc.SENSOR_GPU_FAN, 0, 9000)):
        try:
            raw = wmi.call("GetGamingSysInfo", enc.sysinfo_query(idx))
            val = enc.sysinfo_value(raw)
        except WmiError as e:
            val, raw = None, str(e)
        plausible = val is not None and lo <= val <= hi
        good &= plausible or idx == enc.SENSOR_GPU_TEMP
        print(f"  {name:22s} {val!s:>6}   raw={raw if isinstance(raw, str) else hex(raw)}"
              f"{'' if plausible else '   <-- unexpected'}")
    for label, method, value, decode in (
            ("CoolBoost", "GetFunction", enc.COOLBOOST_GET, enc.coolboost_decode),
            ("LCD overdrive", "GetGamingProfile", enc.LCD_OVERDRIVE_GET, enc.lcd_overdrive_decode),
            ("Backlight (pct, 30s-off)", "GetFunction", enc.backlight_get(read_hotkey_number()),
             enc.backlight_decode),
            ("GPU switch supported", "GetGamingMiscSetting", enc.GPU_MODE_SUPPORT_GET,
             enc.gpu_mode_supported)):
        try:
            raw = wmi.call(method, value)
            print(f"  {label:22s} {decode(raw)!s:>6}   raw={raw:#x}")
        except WmiError as e:
            print(f"  {label:22s}  error: {e}")
    print("hotkey number (DMI 0xAA):", read_hotkey_number())
    print("RESULT:", "OK" if good else "sensor values look wrong - not safe to continue")
    return 0 if good else 1


if __name__ == "__main__":
    sys.exit(main())
