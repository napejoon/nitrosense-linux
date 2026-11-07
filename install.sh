#!/bin/bash
# NitroSense for Linux installer.
#   sudo ./install.sh "/path/to/Nitro Sense_Acer_..._W10x64_A"
# The path is your own NitroSense download for Windows; artwork, fonts, strings
# and the per-model settings are read from it (nothing is executed).
set -euo pipefail

if [ "$(id -u)" -ne 0 ]; then
    echo "Run with sudo: sudo $0 <NitroSense package dir>" >&2
    exit 1
fi
PKG="${1:-}"
if [ -z "$PKG" ] || [ ! -e "$PKG" ]; then
    echo "usage: sudo $0 <path to the NitroSense Windows package folder>" >&2
    exit 1
fi
SRC="$(cd "$(dirname "$0")" && pwd)"
USER_NAME="${SUDO_USER:-}"
PRODUCT="$(cat /sys/class/dmi/id/product_name 2>/dev/null || true)"
VENDOR="$(cat /sys/class/dmi/id/sys_vendor 2>/dev/null || true)"

echo "==> Machine: $VENDOR $PRODUCT"
if [ "$VENDOR" != "Acer" ]; then
    echo "This is not an Acer machine; refusing to install." >&2
    exit 1
fi

echo "==> Installing dependencies"
apt-get install -y python3-gi python3-gi-cairo python3-cairo gir1.2-gtk-4.0 acpi-call-dkms

echo "==> Loading acpi_call (signed by DKMS with your enrolled MOK key)"
if ! modprobe acpi_call; then
    echo "acpi_call could not be loaded. With Secure Boot on, the DKMS module must be" >&2
    echo "signed with an enrolled MOK key (see README)." >&2
    exit 1
fi
echo acpi_call > /etc/modules-load.d/nitrosense.conf

echo "==> Read-only hardware probe (getters only)"
if ! python3 -I "$SRC/tools/probe.py"; then
    echo "Probe failed; the service was NOT installed." >&2
    exit 1
fi

echo "==> Copying program to /opt/nitrosense"
rm -rf /opt/nitrosense
install -d /opt/nitrosense/bin
cp -r "$SRC/nitrosense" /opt/nitrosense/
find /opt/nitrosense -name '__pycache__' -prune -exec rm -rf {} +
install -m 0755 "$SRC/bin/nitrosense" "$SRC/bin/nitrosensed" /opt/nitrosense/bin/
chown -R root:root /opt/nitrosense
chmod -R go-w /opt/nitrosense

echo "==> Extracting NitroSense artwork from the Windows package"
rm -rf /usr/share/nitrosense/assets
install -d /usr/share/nitrosense/assets
(cd /opt/nitrosense && python3 -I -c 'import sys; sys.path.insert(0, "."); from nitrosense.extract import main; sys.exit(main(["x", sys.argv[1], sys.argv[2]]))' \
    "$PKG" /usr/share/nitrosense/assets)

echo "==> Model configuration"
install -d /etc/nitrosense/model
PLUG="$(find "$PKG" -type d -path "*/Plugs/$PRODUCT" | head -n1 || true)"
if [ -n "$PLUG" ]; then
    cp "$PLUG"/*.ini /etc/nitrosense/model/
    echo "    using Plugs/$PRODUCT"
else
    echo "    WARNING: no Plugs/$PRODUCT in the package, using AN515-44 defaults"
fi
if [ -n "$USER_NAME" ] && [ "$USER_NAME" != "root" ]; then
    grep -qx "$USER_NAME" /etc/nitrosense/allowed-users 2>/dev/null || echo "$USER_NAME" >> /etc/nitrosense/allowed-users
    echo "    allowed user: $USER_NAME"
fi

echo "==> Icon and menu entry"
ICON_SRC="/usr/share/nitrosense/assets/icon.png"
if [ -f "$ICON_SRC" ]; then
    install -D -m 0644 "$ICON_SRC" /usr/share/icons/hicolor/256x256/apps/nitrosense.png
    gtk-update-icon-cache -q /usr/share/icons/hicolor 2>/dev/null || true
fi
install -m 0644 "$SRC/data/nitrosense.desktop" /usr/share/applications/nitrosense.desktop
ln -sf /opt/nitrosense/bin/nitrosense /usr/local/bin/nitrosense

echo "==> Service"
install -m 0644 "$SRC/data/nitrosensed.service" /etc/systemd/system/nitrosensed.service
systemctl daemon-reload
systemctl enable nitrosensed.service
systemctl restart nitrosensed.service
sleep 2
systemctl --no-pager --lines=8 status nitrosensed.service || true

echo
echo "Done. Start NitroSense from the app menu or run: nitrosense"
