"""Unprivileged system monitoring: CPU load and NVIDIA GPU load/temperature.

Windows NitroSense reads CPU load from the "% Processor Time" counter and
GPU load from the "GPU Engine" counters, and shows "Discrete GPU is idle"
when the dGPU is powered down. The NVIDIA GPU is queried through NVML only
while it is already awake, so monitoring never wakes it up.
"""

from __future__ import annotations

import ctypes
import glob
import os


class CpuLoad:
    def __init__(self) -> None:
        self._prev = self._read()

    @staticmethod
    def _read() -> tuple[int, int]:
        with open("/proc/stat") as f:
            fields = [int(x) for x in f.readline().split()[1:]]
        idle = fields[3] + (fields[4] if len(fields) > 4 else 0)
        return sum(fields), idle

    def sample(self) -> float:
        total, idle = self._read()
        pt, pi = self._prev
        self._prev = (total, idle)
        dt = total - pt
        if dt <= 0:
            return 0.0
        return max(0.0, min(100.0, 100.0 * (dt - (idle - pi)) / dt))


def _nvidia_pci_devices() -> list[str]:
    devs = []
    for d in glob.glob("/sys/bus/pci/devices/*"):
        try:
            with open(os.path.join(d, "vendor")) as f:
                if f.read().strip() != "0x10de":
                    continue
            with open(os.path.join(d, "class")) as f:
                if not f.read().strip().startswith("0x03"):
                    continue
            devs.append(d)
        except OSError:
            continue
    return devs


class _NvmlUtil(ctypes.Structure):
    _fields_ = [("gpu", ctypes.c_uint), ("memory", ctypes.c_uint)]


class GpuMonitor:
    """Reports (load %, temperature) for the NVIDIA dGPU, or idle=True."""

    def __init__(self) -> None:
        self.pci = _nvidia_pci_devices()
        self._nvml = None
        self._handle = None
        self.name = ""

    @property
    def present(self) -> bool:
        return bool(self.pci)

    def awake(self) -> bool:
        for d in self.pci:
            try:
                with open(os.path.join(d, "power", "runtime_status")) as f:
                    if f.read().strip() != "active":
                        return False
            except OSError:
                pass
        return bool(self.pci)

    def _init_nvml(self) -> bool:
        if self._handle is not None:
            return True
        try:
            lib = ctypes.CDLL("libnvidia-ml.so.1")
            if lib.nvmlInit_v2() != 0:
                return False
            handle = ctypes.c_void_p()
            if lib.nvmlDeviceGetHandleByIndex_v2(0, ctypes.byref(handle)) != 0:
                return False
            name = ctypes.create_string_buffer(96)
            if lib.nvmlDeviceGetName(handle, name, 96) == 0:
                self.name = name.value.decode(errors="replace")
            self._nvml, self._handle = lib, handle
            return True
        except OSError:
            return False

    @staticmethod
    def runtime_d3_enabled() -> bool:
        """True when the driver may power the dGPU down (then never hold it open)."""
        for path in glob.glob("/proc/driver/nvidia/gpus/*/power"):
            try:
                with open(path) as f:
                    for line in f:
                        if line.startswith("Runtime D3 status:"):
                            return "Enabled" in line
            except OSError:
                pass
        return False

    def _shutdown_nvml(self) -> None:
        if self._nvml is not None:
            try:
                self._nvml.nvmlShutdown()
            except OSError:
                pass
        self._nvml = self._handle = None

    def sample(self) -> tuple[bool, float | None, int | None]:
        """-> (idle, load %, temp C)."""
        if not self.present:
            return True, None, None
        if not self.awake():
            self._shutdown_nvml()
            return True, None, None
        if not self._init_nvml():
            return False, None, None
        util = _NvmlUtil()
        temp = ctypes.c_uint()
        load = None
        t = None
        if self._nvml.nvmlDeviceGetUtilizationRates(self._handle, ctypes.byref(util)) == 0:
            load = float(util.gpu)
        if self._nvml.nvmlDeviceGetTemperature(self._handle, 0, ctypes.byref(temp)) == 0:
            t = int(temp.value)
        if self.runtime_d3_enabled():
            # do not keep /dev/nvidia* open, or the GPU could never suspend
            self._shutdown_nvml()
        return False, load, t
