"""Client for railheadd's unix socket (one JSON request per line, one JSON reply per line)."""

from __future__ import annotations

import hashlib
import json
import socket
from pathlib import Path
from typing import Any

from railhead.task import state_root


class DaemonError(RuntimeError):
    def __init__(self, code: str, message: str = "") -> None:
        super().__init__(f"{code}: {message}" if message else code)
        self.code = code
        self.message = message


def socket_path(state_dir: Path | None = None) -> Path:
    """``<state>/rh.sock``, or a short hashed path in /tmp when that exceeds AF_UNIX's ~104-byte limit."""
    state = Path(state_dir or state_root()).resolve()
    p = state / "rh.sock"
    if len(str(p)) <= 100:
        return p
    return Path("/tmp") / f"rh-{hashlib.sha1(str(state).encode()).hexdigest()[:12]}.sock"


class Client:
    def __init__(self, state_dir: Path | None = None, timeout: float = 1800.0) -> None:
        self.path = socket_path(state_dir)
        self.timeout = timeout

    def request(self, op: str, **kw: Any) -> Any:
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as s:
            s.settimeout(self.timeout)
            s.connect(str(self.path))
            s.sendall(json.dumps({"op": op, **kw}).encode() + b"\n")
            buf = b""
            while not buf.endswith(b"\n"):
                chunk = s.recv(1 << 20)
                if not chunk:
                    break
                buf += chunk
        if not buf:
            raise DaemonError("ERR_DAEMON", "daemon closed the connection without replying")
        reply = json.loads(buf)
        if not reply.get("ok"):
            raise DaemonError(reply.get("code", "ERR_UNKNOWN"), reply.get("msg", ""))
        return reply.get("result")
