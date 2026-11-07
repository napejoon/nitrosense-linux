#!/bin/bash
# Read-only dump of the firmware tables NitroSense-Linux needs, plus the two
# Ubuntu packages used to call the Acer WMI methods (acpi-call-dkms) and to
# disassemble the tables (acpica-tools). Run with: sudo bash collect-acpi.sh
set -euo pipefail
OUT="$(cd "$(dirname "$0")" && pwd)/acpi-dump"
mkdir -p "$OUT"

apt-get install -y acpica-tools acpi-call-dkms

cat /sys/firmware/acpi/tables/DSDT > "$OUT/dsdt.dat"
for t in /sys/firmware/acpi/tables/SSDT*; do
    cat "$t" > "$OUT/$(basename "$t").dat"
done
cat /sys/bus/wmi/devices/05901221-D566-11D1-B2F0-00A0C9062910/bmof > "$OUT/bmof.bin"
cat /sys/firmware/dmi/entries/170-0/raw > "$OUT/dmi170.bin"

cd "$OUT"
iasl -e SSDT*.dat -d dsdt.dat > iasl.log 2>&1 || true
if [ -n "${SUDO_USER:-}" ]; then chown -R "$SUDO_USER": "$OUT"; fi

modprobe acpi_call && echo "acpi_call loaded OK" || echo "acpi_call FAILED to load"
ls -la "$OUT"
