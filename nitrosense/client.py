"""Client used by the GUI to talk to nitrosensed."""

from __future__ import annotations

import json
import os
import socket
import threading

from .protocol import SOCKET_PATH


class DaemonError(RuntimeError):
    pass


class Client:
    def __init__(self, path: str | None = None, timeout: float = 10.0) -> None:
        self.path = path or os.environ.get("NITROSENSE_SOCKET", SOCKET_PATH)
        self.timeout = timeout
        self._sock: socket.socket | None = None
        self._file = None
        self._lock = threading.Lock()

    def _connect(self) -> None:
        s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        s.settimeout(self.timeout)
        try:
            s.connect(self.path)
        except OSError as e:
            s.close()
            raise DaemonError("NitroSense service is not running "
                              "(sudo systemctl start nitrosensed)") from e
        self._sock = s
        self._file = s.makefile("rwb")

    def close(self) -> None:
        with self._lock:
            self._close()

    def _close(self) -> None:
        for obj in (self._file, self._sock):
            try:
                if obj:
                    obj.close()
            except OSError:
                pass
        self._sock = self._file = None

    def request(self, cmd: str, **kw) -> dict:
        payload = json.dumps({"cmd": cmd, **kw}).encode() + b"\n"
        with self._lock:
            for attempt in (1, 2):
                try:
                    if self._sock is None:
                        self._connect()
                    self._file.write(payload)
                    self._file.flush()
                    line = self._file.readline()
                    if not line:
                        raise OSError("connection closed")
                    resp = json.loads(line)
                    break
                except (OSError, ValueError) as e:
                    self._close()
                    if attempt == 2:
                        if isinstance(e, DaemonError):
                            raise
                        raise DaemonError(f"lost connection to NitroSense service: {e}") from e
        if not resp.get("ok"):
            raise DaemonError(resp.get("error", "request failed"))
        return resp
