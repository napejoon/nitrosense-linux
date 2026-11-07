"""GNOME keyboard shortcut: NitroSense key (XF86Launch1) -> open NitroSense.

Run as the desktop user: python3 -m nitrosense.shortcut install|remove|status
"""

from __future__ import annotations

import sys

import gi

gi.require_version("Gio", "2.0")
from gi.repository import Gio  # noqa: E402

SCHEMA = "org.gnome.settings-daemon.plugins.media-keys"
CUSTOM_SCHEMA = SCHEMA + ".custom-keybinding"
PATH = "/org/gnome/settings-daemon/plugins/media-keys/custom-keybindings/nitrosense/"
COMMAND = "/opt/nitrosense/bin/nitrosense"
BINDING = "XF86Launch1"


def _available() -> bool:
    src = Gio.SettingsSchemaSource.get_default()
    return bool(src and src.lookup(SCHEMA, True) and src.lookup(CUSTOM_SCHEMA, True))


def install(command: str = COMMAND, binding: str = BINDING) -> bool:
    if not _available():
        return False
    custom = Gio.Settings.new_with_path(CUSTOM_SCHEMA, PATH)
    custom.set_string("name", "NitroSense")
    custom.set_string("command", command)
    custom.set_string("binding", binding)
    keys = Gio.Settings.new(SCHEMA)
    paths = list(keys.get_strv("custom-keybindings"))
    if PATH not in paths:
        keys.set_strv("custom-keybindings", paths + [PATH])
    Gio.Settings.sync()
    return True


def remove() -> bool:
    if not _available():
        return False
    keys = Gio.Settings.new(SCHEMA)
    paths = [p for p in keys.get_strv("custom-keybindings") if p != PATH]
    keys.set_strv("custom-keybindings", paths)
    custom = Gio.Settings.new_with_path(CUSTOM_SCHEMA, PATH)
    for k in ("name", "command", "binding"):
        custom.reset(k)
    Gio.Settings.sync()
    return True


def status() -> str:
    if not _available():
        return "GNOME media-keys schema not available"
    keys = Gio.Settings.new(SCHEMA)
    if PATH not in keys.get_strv("custom-keybindings"):
        return "not registered"
    c = Gio.Settings.new_with_path(CUSTOM_SCHEMA, PATH)
    return f"{c.get_string('binding')} -> {c.get_string('command')}"


def main(argv: list[str]) -> int:
    action = argv[0] if argv else "status"
    if action == "install":
        ok = install()
        print("NitroSense key shortcut:", status() if ok else "skipped (not a GNOME session)")
        return 0
    if action == "remove":
        remove()
        print("NitroSense key shortcut removed")
        return 0
    print(status())
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
