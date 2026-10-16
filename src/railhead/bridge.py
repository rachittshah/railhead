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
        rid = self._new_id()
        self.admin.send_gs({"id": rid, "m": method, "a": args})
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
