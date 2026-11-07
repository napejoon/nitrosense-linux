"""Simulated Acer WMI firmware + mock daemon for tests and GUI development."""

from __future__ import annotations

import math
import os
import sys
import tempfile
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from nitrosense import daemon as dmod  # noqa: E402
from nitrosense import encoders as enc  # noqa: E402
from nitrosense import power  # noqa: E402
from nitrosense.wmi import METHODS, WmiError  # noqa: E402


class FakeWmi:
    """Behaves like the AN515-44 firmware for the methods NitroSense uses."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, int]] = []
        self.fan_behavior = {"cpu": 1, "gpu": 1}
        self.fan_speed = {"cpu": 0, "gpu": 0}
        self.coolboost = False
        self.overdrive = False
        self.backlight = (100, False)
        self.t0 = time.monotonic()

    def available(self):
        return True, ""

    def call(self, name: str, value: int) -> int:
        _guid, _mid, size = METHODS[name]
        if value >= 1 << (8 * size):
            raise WmiError("input too large")
        self.calls.append((name, value))
        if name == "SetGamingFanBehavior":
            if value & 1:
                self.fan_behavior["cpu"] = (value >> 16) & 3
            if value & 8:
                self.fan_behavior["gpu"] = (value >> 22) & 3
            return 0
        if name == "SetGamingFanSpeed":
            fan = {1: "cpu", 4: "gpu"}[value & 0xFF]
            self.fan_speed[fan] = (value >> 8) & 0xFF
            return 0
        if name == "GetGamingSysInfo":
            idx = (value >> 8) & 0xFF
            t = time.monotonic() - self.t0
            vals = {enc.SENSOR_CPU_TEMP: 55 + int(8 * math.sin(t / 7)),
                    enc.SENSOR_GPU_TEMP: 48 + int(5 * math.sin(t / 9)),
                    enc.SENSOR_CPU_FAN: self._rpm("cpu"),
                    enc.SENSOR_GPU_FAN: self._rpm("gpu")}
            if idx not in vals:
                return 1
            return vals[idx] << 8
        if name == "SetFunction":
            if value & 0xFF == 7:
                self.coolboost = bool((value >> 16) & 1)
            elif value & 0xFF == 2:
                self.backlight = ((value >> 32) & 0xFF, ((value >> 40) & 0xFF) == 30)
            return 0
        if name == "GetFunction":
            if value == enc.COOLBOOST_GET:
                return int(self.coolboost) << 8
            if value & 0xFF == 1:
                return (self.backlight[0] << 32) | ((30 if self.backlight[1] else 0) << 40)
            return 1
        if name == "GetGamingProfile":
            return (1 if self.overdrive else 0) << 48
        if name == "SetGamingProfile":
            if value & 0xFF == 0x10:
                self.overdrive = bool((value >> 48) & 1)
            return 0
        if name == "GetGamingMiscSetting":
            return 0  # no discrete GPU switch on AN515-44
        return 0

    def _rpm(self, fan: str) -> int:
        b = self.fan_behavior[fan]
        if b == 2:
            return 5200
        if b == 3:
            return int(self.fan_speed[fan] * 55)
        return 2300 if fan == "cpu" else 2100


def make_daemon(state_dir: str | None = None):
    state_dir = state_dir or tempfile.mkdtemp(prefix="nitrosense-test-")
    dmod.STATE_DIR = state_dir
    dmod.STATE_FILE = os.path.join(state_dir, "state.json")
    dmod._allowed_uids = lambda: {0, os.getuid()}
    applied = []
    power.apply_plan = lambda plan: applied.append(plan)
    dmod.power.apply_plan = power.apply_plan
    d = dmod.Daemon()
    d.ctl.wmi = FakeWmi()
    d.ready = True
    d.power_applied_log = applied
    return d


def main() -> None:
    sock = sys.argv[1] if len(sys.argv) > 1 else os.path.join(tempfile.gettempdir(), "ns-test.sock")
    d = make_daemon()
    dmod.start_server(d, sock)
    print("mock nitrosensed on", sock, flush=True)
    while True:
        time.sleep(3600)


if __name__ == "__main__":
    main()
