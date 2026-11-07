#!/usr/bin/python3 -I
"""Read-only: show what a key sends (evdev keys + scancodes, ACPI/WMI events).

sudo python3 -I tools/keyprobe.py [seconds]
Press the key to identify while it runs. Nothing is changed.
"""

import glob
import os
import select
import struct
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.realpath(__file__))))

from nitrosense.acpievents import AcpiEventListener  # noqa: E402

EV_KEY, EV_MSC, MSC_SCAN = 1, 4, 4
EVENT = struct.Struct("llHHi")


def wed_payload() -> str:
    try:
        with open("/proc/acpi/call", "w") as f:
            f.write("\\_SB.PCI0.WMID._WED 0xbc")
        with open("/proc/acpi/call") as f:
            return f.read().strip().rstrip("\x00")
    except OSError as e:
        return f"(no acpi_call: {e})"


def main() -> int:
    if os.geteuid() != 0:
        print("run with sudo")
        return 2
    seconds = float(sys.argv[1]) if len(sys.argv) > 1 else 20
    devs = {}
    for path in sorted(glob.glob("/dev/input/event*")):
        try:
            fd = os.open(path, os.O_RDONLY | os.O_NONBLOCK)
        except OSError:
            continue
        name = open(f"/sys/class/input/{os.path.basename(path)}/device/name").read().strip()
        devs[fd] = (path, name)
    acpi = AcpiEventListener()
    print(f"listening {seconds:.0f}s on {len(devs)} input devices + ACPI events - press the key now",
          flush=True)
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        r, _, _ = select.select(list(devs) + [acpi], [], [], 0.5)
        for src in r:
            if src is acpi:
                for ev in acpi.read():
                    extra = wed_payload() if ev.type in (0xBC, 0xBD) else ""
                    print(f"ACPI  class={ev.device_class!r} bus={ev.bus_id!r} "
                          f"type={ev.type:#x} data={ev.data:#x} {extra}", flush=True)
                continue
            data = os.read(src, EVENT.size * 64)
            for i in range(0, len(data) - EVENT.size + 1, EVENT.size):
                _s, _u, typ, code, value = EVENT.unpack_from(data, i)
                path, name = devs[src]
                if typ == EV_MSC and code == MSC_SCAN:
                    print(f"SCAN  {name} ({path}) scancode={value & 0xffffffff:#x}", flush=True)
                elif typ == EV_KEY and value in (0, 1):
                    print(f"KEY   {name} ({path}) keycode={code} {'down' if value else 'up'}",
                          flush=True)
    print("done")
    return 0


if __name__ == "__main__":
    sys.exit(main())
