"""Synchronous Admin Port client with a background reader thread.

One reader thread decodes packets and routes them: RCON output to the pending rcon call,
GameScript JSON to ``gs_queue``, everything else to ``events``. Sends are serialized by a lock.
"""

from __future__ import annotations

import queue
import socket
import threading
import time
from typing import Any

from railhead import admin_protocol as ap
from railhead.wire import FrameDecoder


class AdminClient:
    def __init__(self, sock: socket.socket) -> None:
        self._sock = sock
        self._send_lock = threading.Lock()
        self._rcon_lock = threading.Lock()
        self._rcon_lines: list[str] = []
        self._rcon_done = threading.Event()
        self.gs_queue: queue.Queue[Any] = queue.Queue()
        self.events: queue.Queue[Any] = queue.Queue()
        self.protocol: ap.ServerProtocol | None = None
        self.welcome: ap.ServerWelcome | None = None
        self.last_date: int | None = None
        self.economy: dict[int, ap.ServerCompanyEconomy] = {}
        self._welcome_evt = threading.Event()
        self._error: ap.ServerError | None = None
        self._closed = threading.Event()
        self._reader = threading.Thread(target=self._read_loop, name="admin-reader", daemon=True)

    # ------------------------------------------------------------------ lifecycle
    @classmethod
    def connect(
        cls, host: str, port: int, password: str, name: str = "railhead", version: str = "0.1", timeout: float = 15.0
    ) -> AdminClient:
        sock = socket.create_connection((host, port), timeout=timeout)
        sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
        sock.settimeout(None)
        client = cls(sock)
        client._reader.start()
        client._send(ap.encode_join(password, name, version))
        deadline = time.monotonic() + timeout
        while not client._welcome_evt.is_set():
            if client._error is not None or client._closed.is_set():
                client.close()
                code = client._error.code if client._error else None
                raise ConnectionError(f"admin join refused (error code {code})")
            if time.monotonic() > deadline:
                client.close()
                raise TimeoutError("admin join timed out waiting for SERVER_WELCOME")
            client._welcome_evt.wait(0.05)
        return client

    def close(self) -> None:
        if self._closed.is_set():
            return
        try:
            self._send(ap.encode_quit())
        except OSError:
            pass
        self._closed.set()
        try:
            self._sock.shutdown(socket.SHUT_RDWR)
        except OSError:
            pass
        self._sock.close()

    def __enter__(self) -> AdminClient:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    @property
    def alive(self) -> bool:
        return not self._closed.is_set()

    # ------------------------------------------------------------------ io
    def _send(self, data: bytes) -> None:
        with self._send_lock:
            self._sock.sendall(data)

    def _read_loop(self) -> None:
        dec = FrameDecoder()
        try:
            while not self._closed.is_set():
                chunk = self._sock.recv(65536)
                if not chunk:
                    break
                for ptype, payload in dec.feed(chunk):
                    self._dispatch(ap.decode(ptype, payload))
        except OSError:
            pass
        finally:
            self._closed.set()
            self._rcon_done.set()

    def _dispatch(self, msg: Any) -> None:
        if isinstance(msg, ap.ServerRcon):
            self._rcon_lines.append(msg.line)
        elif isinstance(msg, ap.ServerRconEnd):
            self._rcon_done.set()
        elif isinstance(msg, ap.ServerGameScript):
            self.gs_queue.put(msg.data)
        elif isinstance(msg, ap.ServerProtocol):
            self.protocol = msg
        elif isinstance(msg, ap.ServerWelcome):
            self.welcome = msg
            self._welcome_evt.set()
            self.events.put(msg)
        elif isinstance(msg, ap.ServerError):
            self._error = msg
            self.events.put(msg)
        elif isinstance(msg, ap.ServerDate):
            self.last_date = msg.date
        elif isinstance(msg, ap.ServerCompanyEconomy):
            self.economy[msg.company] = msg
            self.events.put(msg)
        else:
            self.events.put(msg)

    # ------------------------------------------------------------------ api
    def rcon(self, command: str, timeout: float = 30.0) -> list[str]:
        """Run a console command on the server and return its output lines."""
        with self._rcon_lock:
            self._rcon_lines = []
            self._rcon_done.clear()
            self._send(ap.encode_rcon(command))
            if not self._rcon_done.wait(timeout):
                raise TimeoutError(f"rcon {command!r} timed out")
            if self._closed.is_set() and not self._rcon_lines:
                raise ConnectionError("admin connection closed during rcon")
            return list(self._rcon_lines)

    def subscribe(self, update_type: ap.UpdateType, freq: int = ap.Freq.AUTOMATIC) -> None:
        self._send(ap.encode_update_frequency(update_type, freq))

    def poll(self, update_type: ap.UpdateType, ident: int = 0xFFFFFFFF) -> None:
        self._send(ap.encode_poll(update_type, ident))

    def send_gs(self, obj: dict) -> None:
        self._send(ap.encode_gamescript(obj))

    def recv_gs(self, timeout: float | None = None) -> Any:
        """Next GameScript JSON message (raises ``queue.Empty`` on timeout)."""
        return self.gs_queue.get(timeout=timeout)

    def ping(self, token: int = 1, timeout: float = 5.0) -> float:
        start = time.monotonic()
        self._send(ap.encode_ping(token))
        while True:
            remaining = timeout - (time.monotonic() - start)
            if remaining <= 0:
                raise TimeoutError("ping timed out")
            msg = self.events.get(timeout=remaining)
            if isinstance(msg, ap.ServerPong) and msg.token == token:
                return time.monotonic() - start
