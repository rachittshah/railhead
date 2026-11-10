"""Python side of the RailheadBridge GameScript RPC.

Wire (JSON over the Admin Port, see gamescript/game/RailheadBridge/main.nut):
  py -> gs  {"id": N, "m": method, "a": {...}}
  gs -> py  {"t":"r","id":N,"ok":true,"v":value}            final reply
            {"t":"r","id":N,"ok":false,"code":"ERR_..","msg":"..."}
            {"t":"p","id":N,"rows":[...]}                       partial rows (list replies are chunked)
            {"t":"ev", ...}                                      unsolicited event (kept in .events)
"""

from __future__ import annotations

import queue
import time
from collections import deque
from typing import Any

from railhead import admin_protocol as ap
from railhead.admin_client import AdminClient

PROTOCOL_VERSION = 1


class BridgeError(RuntimeError):
    def __init__(self, code: str, message: str = "", data: Any = None) -> None:
        super().__init__(f"{code}: {message}" if message else code)
        self.code = code
        self.message = message
        self.data = data


class Bridge:
    def __init__(self, admin: AdminClient) -> None:
        self.admin = admin
        self._next_id = 1
        self.hello: dict | None = None
        self.events: deque[dict] = deque(maxlen=10_000)
        admin.subscribe(ap.UpdateType.GAMESCRIPT, ap.Freq.AUTOMATIC)

    def _new_id(self) -> int:
        i = self._next_id
        self._next_id += 1
        return i

    def wait_ready(self, timeout: float = 30.0) -> dict:
        """Poll ``hello`` until the GS answers (messages sent before the GS VM starts are dropped)."""
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            try:
                self.hello = self.call("hello", timeout=min(1.0, max(0.05, deadline - time.monotonic())))
                return self.hello
            except TimeoutError:
                continue
        raise TimeoutError("RailheadBridge GameScript did not answer hello")

    def call(self, method: str, timeout: float = 30.0, **args: Any) -> Any:
        """Convenience for trusted callers. Arguments from a player or a log go through :meth:`request`:
        here an argument named ``timeout`` would set the wait instead of reaching the GameScript."""
        return self.request(method, args, timeout=timeout)

    def request(self, method: str, args: dict[str, Any] | None = None, timeout: float = 30.0) -> Any:
        """Send ``method`` with ``args`` as one dict (no name can collide with ``timeout``).

        A request that cannot be encoded (floats, too large, not an object) raises ``BridgeError``
        ERR_BAD_ARG before anything is sent: like a GameScript refusal, it changed nothing."""
        if args is not None and not isinstance(args, dict):
            raise BridgeError("ERR_BAD_ARG", f"arguments of {method!r} must be an object")
        rid = self._new_id()
        msg = {"id": rid, "m": method, "a": dict(args or {})}
        try:
            ap.dumps_gs(msg)
        except (ValueError, TypeError) as e:
            raise BridgeError("ERR_BAD_ARG", str(e)) from None
        self.admin.send_gs(msg)
        rows: list | None = None
        deadline = time.monotonic() + timeout
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise TimeoutError(f"bridge call {method!r} timed out after {timeout}s")
            try:
                msg = self.admin.recv_gs(timeout=min(remaining, 0.5))
            except queue.Empty:
                if not self.admin.alive:
                    raise ConnectionError("admin connection closed") from None
                continue
            if not isinstance(msg, dict):
                continue
            t = msg.get("t")
            if t == "ev":
                self.events.append(msg)
                continue
            if msg.get("id") != rid:
                continue  # stale reply from an earlier timed-out call
            if t == "p":
                rows = (rows or []) + list(msg.get("rows", []))
                continue
            if t == "r":
                if not msg.get("ok"):
                    raise BridgeError(msg.get("code", "ERR_UNKNOWN"), msg.get("msg", ""), msg.get("data"))
                if msg.get("rows"):
                    return rows or []
                return msg.get("v")

    def drain_events(self) -> list[dict]:
        """Collect queued events without blocking."""
        while True:
            try:
                msg = self.admin.recv_gs(timeout=0)
            except queue.Empty:
                break
            if isinstance(msg, dict) and msg.get("t") == "ev":
                self.events.append(msg)
        out = list(self.events)
        self.events.clear()
        return out
