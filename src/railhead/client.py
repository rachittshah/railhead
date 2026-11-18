"""Client for railheadd's unix socket (one JSON request per line, one JSON reply per line)."""

from __future__ import annotations

import hashlib
import json
import os
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


def _short(p: Path, tag: str) -> Path:
    """``p``, or a short hashed path in /tmp when ``p`` exceeds AF_UNIX's ~104-byte limit."""
    if len(str(p)) <= 100:
        return p
    return Path("/tmp") / f"rh-{hashlib.sha1(str(p.parent).encode()).hexdigest()[:12]}-{tag}.sock"


def agent_socket_path(state_dir: Path | None, slot: int) -> Path:
    """The socket of company slot ``slot`` of a Waybill coordinator: ``<state>/run/agent-<slot>.sock``."""
    state = Path(state_dir or state_root()).resolve()
    return _short(state / "run" / f"agent-{int(slot)}.sock", f"a{int(slot)}")


def control_socket_path(state_dir: Path | None) -> Path:
    """The operator socket of a Waybill coordinator: ``<state>/run/control.sock``."""
    state = Path(state_dir or state_root()).resolve()
    return _short(state / "run" / "control.sock", "ctl")


def env_socket() -> Path | None:
    """The socket that ``RAILHEAD_SOCKET`` (a path) or ``RAILHEAD_SLOT`` (a Waybill company slot under the
    state root) selects, or None. With either set, ``rh`` talks to a Waybill coordinator and never starts
    a single-agent daemon."""
    if env := os.environ.get("RAILHEAD_SOCKET"):
        return Path(env)
    if (slot := os.environ.get("RAILHEAD_SLOT")) is not None and slot != "":
        return agent_socket_path(None, int(slot))
    return None


class Client:
    def __init__(self, state_dir: Path | None = None, timeout: float = 1800.0) -> None:
        env = env_socket() if state_dir is None else None
        self.path = env or socket_path(state_dir)
        # A wait in a shared world lasts until every other agent waits: allow a long time.
        self.timeout = timeout if env is None else max(timeout, 86400.0)

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
