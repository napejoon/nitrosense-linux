# NitroSense for Linux

A Linux port of Acer **NitroSense 3.01.3056** for the Nitro AN515-44 (and the
other `MachineType=1` Nitro models in the same package), rebuilt from the
Windows app's own layout, artwork and hardware calls.

| Windows NitroSense | Linux |
|---|---|
| WPF window `Nitro_MainWindow_Octavia` 1320x760 | GTK 4 window, same coordinates, same images/icons |
| GUI -> named pipe -> NitroSense service -> WMI | GUI -> Unix socket -> `nitrosensed` -> `acpi_call` -> same ACPI WMI method |
| `AcerGamingFunction` / `APGeAction` WMI classes | `\_SB.PCI0.WMID.WMBH` / `.WMAA` with bit-identical inputs |
| Windows power schemes | CPU governor + boost + power-profiles-daemon |
| NitroSense agent re-applies settings | `nitrosensed` re-applies at boot and after resume |

## Features

- Fan control: Auto / Max / Custom, per-fan CPU/GPU speed (0-100 % in 10 % steps) with per-fan Auto, CoolBoost (Auto mode only, as on Windows)
- Live CPU/GPU fan RPM (rotating fans), CPU/GPU temperature and load with 12-minute charts, min/max
- Power plan per AC / battery: Power Saver, Balance, High-Performance (switches automatically when you plug/unplug)
- Keyboard lighting (4 zones): static colours per zone, zone on/off, brightness 1-5; dynamic Breathing, Wave, Zoom, Shifting, Neon with speed, direction and colour
- Settings: Sticky keys, Windows (Super) and menu key lock, °C/°F, LCD Overdrive, keyboard backlight off after 30 s, Discrete-GPU-only switch (shown only if the BIOS supports it)

Not ported (Windows-only): Waves/DTS sound modes, NVIDIA GeForce Experience button, Planet9 promotion.

## Install

You need the NitroSense package for Windows (from Acer's support site); the
installer reads the artwork, fonts and per-model `Plugs/<model>/*.ini` from it.

```bash
sudo ./install.sh "/path/to/Nitro Sense_Acer_3.01.3056_W10x64_A"
```

It installs `python3-gi-cairo`, `gir1.2-gtk-4.0` and `acpi-call-dkms`. With Secure
Boot on, DKMS signs `acpi_call` with your enrolled MOK key (same as for other DKMS
modules). Then start **NitroSense** from the app menu or run `nitrosense`.

Uninstall: `sudo ./uninstall.sh` (fans go back to Auto, CPU policy to Balance).

## Layout

```
nitrosense/encoders.py   bit-exact WMI inputs (one function per NitroSense method)
nitrosense/wmi.py        whitelisted WMI calls through /proc/acpi/call
nitrosense/controller.py call sequences (e.g. static lighting = backlight, 4 zone colours, zone mask)
nitrosense/daemon.py     root service: socket API, state, re-apply on boot/resume/AC change
nitrosense/power.py      power plans
nitrosense/extract.py    reads artwork/strings/icons out of the Windows .appxbundle (BAML/.resources)
nitrosense/gui/          GTK 4 user interface
tests/                   encoder tests (values from the decompiled IL) + simulated firmware
```

Run the tests with `python3 -m unittest discover -s tests`.

## Safety

The service only calls the twelve WMI methods NitroSense uses, with fixed input
sizes and validated arguments; only root and the users in
`/etc/nitrosense/allowed-users` can talk to it. If your BIOS numbers the WMI
methods differently, `/etc/nitrosense/wmi.json` can override them, e.g.
`{"methods": {"SetGamingLEDBehavior": {"id": 24, "size": 8}}}`.

## Disclaimer

Unofficial project, not affiliated with or endorsed by Acer. "Acer", "Nitro",
"NitroSense" and "CoolBoost" are trademarks of Acer Inc. This repository contains
no Acer code, firmware or artwork; the installer reads the artwork from the
NitroSense package you downloaded yourself. Tested on a Nitro AN515-44
(BIOS V1.04, Ubuntu 24.04, kernel 7.0). Use at your own risk.
