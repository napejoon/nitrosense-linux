"""Per-model configuration from the NitroSense package's Plugs/<model>/*.ini.

The Windows installer copies Plugs/<product name>/ to C:\\ProgramData\\OEM\\NitroSense;
the Linux installer copies it to /etc/nitrosense/model.
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass, field

MODEL_DIR = os.environ.get("NITROSENSE_MODEL_DIR", "/etc/nitrosense/model")


def _read_ini(path: str) -> dict[str, dict[str, str]]:
    data: dict[str, dict[str, str]] = {}
    section = None
    try:
        with open(path, encoding="utf-8-sig", errors="replace") as f:
            for raw in f:
                line = raw.strip()
                if not line or line.startswith((";", "#")):
                    continue
                m = re.fullmatch(r"\[(.+)\]", line)
                if m:
                    section = m.group(1).strip()
                    data.setdefault(section, {})
                    continue
                if section is None:
                    continue
                m = re.fullmatch(r"([^=:]+)[=:](.*)", line)  # NitroSense uses both '=' and ':'
                if m:
                    data[section][m.group(1).strip()] = m.group(2).strip()
    except OSError:
        pass
    return data


@dataclass
class ModelConfig:
    year: int = 2020
    machine_type: int = 1
    lighting_type: int = 1
    zone_default_colors: list[str] = field(default_factory=lambda: ["#FF0000"] * 4)
    zone_adjust: tuple[float, float, float] = (1.0, 1.0, 1.0)
    fan_cpu: bool = True
    fan_gpu: bool = True
    advanced: tuple[str, ...] = ("Animation", "LCD", "Temperature")
    keyboard: tuple[str, ...] = ("Backlight", "Sticky_Key", "Windowskey1")
    found: bool = False

    @property
    def wave_color_enabled(self) -> bool:
        # LightingDynamicUI.Light_Effects_RadioButton_Checked: Year >= 2020 -> rainbow wave
        return self.year < 2020


def _funs(section: dict[str, str]) -> tuple[str, ...]:
    try:
        count = int(section.get("Count", "0"))
    except ValueError:
        count = 0
    return tuple(section[f"FUN{i}"] for i in range(1, count + 1) if f"FUN{i}" in section)


def load(directory: str = MODEL_DIR) -> ModelConfig:
    cfg = ModelConfig()
    feature = _read_ini(os.path.join(directory, "Feature.ini"))
    hw = _read_ini(os.path.join(directory, "HW_Support.ini"))
    cfg.found = bool(feature or hw)
    try:
        cfg.year = int(feature.get("YearType", {}).get("Year", cfg.year))
    except ValueError:
        pass
    try:
        cfg.machine_type = int(feature.get("MachineType", {}).get("Type", cfg.machine_type))
    except ValueError:
        pass
    try:
        cfg.lighting_type = int(hw.get("LightingType", {}).get("Type", cfg.lighting_type))
    except ValueError:
        pass
    zones = hw.get("ZoneDefaultColor", {})
    colors = [zones.get(f"Zone{i}", "") for i in range(1, 5)]
    if all(re.fullmatch(r"#[0-9A-Fa-f]{6}", c) for c in colors):
        cfg.zone_default_colors = [c.upper() for c in colors]
    adj = hw.get("ZoneColorAdjust", {})
    try:
        cfg.zone_adjust = tuple(min(1.0, max(0.0, float(adj.get(k, 1.0)))) for k in "RGB")
    except ValueError:
        pass
    fans = hw.get("FanSupport", {})
    cfg.fan_cpu = fans.get("CPU", "1") != "0"
    cfg.fan_gpu = fans.get("GPU", "1") != "0"
    if "AdvanceSetting" in hw:
        cfg.advanced = _funs(hw["AdvanceSetting"])
    if "KeyboardSetting" in hw:
        cfg.keyboard = _funs(hw["KeyboardSetting"])
    return cfg
