"""ACPI event listener over generic netlink ("acpi_event" family, "acpi_mc_group").

The kernel's wmi driver forwards every WMI notification (for Acer: Notify(WMID, 0xBC))
as an ACPI netlink event; this is the same stream acpid reads.
"""

from __future__ import annotations

import os
import socket
import struct
from dataclasses import dataclass

NETLINK_GENERIC = 16
SOL_NETLINK = 270
NETLINK_ADD_MEMBERSHIP = 1
GENL_ID_CTRL = 0x10
CTRL_CMD_GETFAMILY = 3
CTRL_ATTR_FAMILY_ID = 1
CTRL_ATTR_FAMILY_NAME = 2
CTRL_ATTR_MCAST_GROUPS = 7
CTRL_ATTR_MCAST_GRP_NAME = 1
CTRL_ATTR_MCAST_GRP_ID = 2
NLM_F_REQUEST = 1
NLMSG_ERROR = 2
ACPI_GENL_ATTR_EVENT = 1
_EVENT = struct.Struct("<20s15sxII")  # struct acpi_genl_event


@dataclass(frozen=True)
class AcpiEvent:
    device_class: str
    bus_id: str
    type: int
    data: int


def _attrs(buf: bytes) -> dict[int, bytes]:
    out, p = {}, 0
    while p + 4 <= len(buf):
        ln, typ = struct.unpack_from("<HH", buf, p)
        if ln < 4:
            break
        out[typ & 0x3FFF] = buf[p + 4:p + ln]
        p += (ln + 3) & ~3
    return out


def _attr(typ: int, payload: bytes) -> bytes:
    a = struct.pack("<HH", 4 + len(payload), typ) + payload
    return a + b"\0" * ((-len(a)) % 4)


class AcpiEventListener:
    def __init__(self) -> None:
        self.sock = socket.socket(socket.AF_NETLINK, socket.SOCK_RAW, NETLINK_GENERIC)
        self.sock.bind((0, 0))
        family, group = self._resolve()
        self.family = family
        self.sock.setsockopt(SOL_NETLINK, NETLINK_ADD_MEMBERSHIP, group)

    def _resolve(self) -> tuple[int, int]:
        body = struct.pack("<BBH", CTRL_CMD_GETFAMILY, 1, 0) + _attr(CTRL_ATTR_FAMILY_NAME,
                                                                      b"acpi_event\0")
        msg = struct.pack("<IHHII", 16 + len(body), GENL_ID_CTRL, NLM_F_REQUEST, 1, 0) + body
        self.sock.send(msg)
        data = self.sock.recv(65536)
        ln, typ = struct.unpack_from("<IH", data, 0)
        if typ == NLMSG_ERROR:
            err, = struct.unpack_from("<i", data, 16)
            raise OSError(-err, "acpi_event netlink family not available")
        attrs = _attrs(data[20:ln])
        family, = struct.unpack("<H", attrs[CTRL_ATTR_FAMILY_ID][:2])
        group = None
        for grp in _attrs(attrs.get(CTRL_ATTR_MCAST_GROUPS, b"")).values():
            g = _attrs(grp)
            if g.get(CTRL_ATTR_MCAST_GRP_NAME, b"").rstrip(b"\0") == b"acpi_mc_group":
                group, = struct.unpack("<I", g[CTRL_ATTR_MCAST_GRP_ID][:4])
        if group is None:
            raise OSError("acpi_mc_group not found")
        return family, group

    def fileno(self) -> int:
        return self.sock.fileno()

    def read(self) -> list[AcpiEvent]:
        data = self.sock.recv(65536)
        events, p = [], 0
        while p + 16 <= len(data):
            ln, typ = struct.unpack_from("<IH", data, p)
            if ln < 16:
                break
            if typ == self.family:
                attrs = _attrs(data[p + 20:p + ln])
                raw = attrs.get(ACPI_GENL_ATTR_EVENT)
                if raw and len(raw) >= _EVENT.size:
                    cls, bus, etype, edata = _EVENT.unpack_from(raw)
                    events.append(AcpiEvent(cls.split(b"\0")[0].decode(errors="replace"),
                                            bus.split(b"\0")[0].decode(errors="replace"),
                                            etype, edata))
            p += (ln + 3) & ~3
        return events

    def close(self) -> None:
        self.sock.close()


if __name__ == "__main__":  # debugging: print ACPI events
    lst = AcpiEventListener()
    print(f"listening (family {lst.family}, pid {os.getpid()})", flush=True)
    while True:
        for ev in lst.read():
            print(ev, flush=True)
