"""OpenTTD TCP packet framing and field codec.

Framing is ``[uint16 LE total_size][uint8 type][payload]`` where ``total_size`` counts the
3 header bytes. Integers are little-endian, bools are one byte, strings are UTF-8 + NUL.
"""

from __future__ import annotations

import struct
from collections.abc import Iterator

HEADER_SIZE = 3
MAX_PACKET_SIZE = 0xFFFF


class WireError(ValueError):
    """Malformed or impossible bytes on the wire."""


def encode_packet(ptype: int, payload: bytes) -> bytes:
    size = HEADER_SIZE + len(payload)
    if size > MAX_PACKET_SIZE:
        raise WireError(f"packet too large: {size} > {MAX_PACKET_SIZE}")
    return struct.pack("<HB", size, ptype) + payload


class PacketWriter:
    def __init__(self) -> None:
        self._buf = bytearray()

    def _pack(self, fmt: str, value: int) -> PacketWriter:
        self._buf += struct.pack(fmt, value)
        return self

    def u8(self, v: int) -> PacketWriter:
        return self._pack("<B", v)

    def u16(self, v: int) -> PacketWriter:
        return self._pack("<H", v)

    def u32(self, v: int) -> PacketWriter:
        return self._pack("<I", v)

    def u64(self, v: int) -> PacketWriter:
        return self._pack("<Q", v)

    def i64(self, v: int) -> PacketWriter:
        return self._pack("<q", v)

    def bool(self, v: bool) -> PacketWriter:
        return self._pack("<B", 1 if v else 0)

    def string(self, s: str) -> PacketWriter:
        raw = s.encode("utf-8")
        if b"\x00" in raw:
            raise WireError("strings may not contain NUL")
        self._buf += raw + b"\x00"
        return self

    def raw(self, b: bytes) -> PacketWriter:
        self._buf += b
        return self

    def getvalue(self) -> bytes:
        return bytes(self._buf)


class PacketReader:
    def __init__(self, data: bytes) -> None:
        self._data = data
        self._pos = 0

    def _unpack(self, fmt: str) -> int:
        n = struct.calcsize(fmt)
        if self._pos + n > len(self._data):
            raise WireError(f"truncated field: need {n} bytes at offset {self._pos}")
        (v,) = struct.unpack_from(fmt, self._data, self._pos)
        self._pos += n
        return v

    def u8(self) -> int:
        return self._unpack("<B")

    def u16(self) -> int:
        return self._unpack("<H")

    def u32(self) -> int:
        return self._unpack("<I")

    def u64(self) -> int:
        return self._unpack("<Q")

    def i64(self) -> int:
        return self._unpack("<q")

    def bool(self) -> bool:
        return self._unpack("<B") != 0

    def string(self) -> str:
        end = self._data.find(b"\x00", self._pos)
        if end < 0:
            raise WireError(f"unterminated string at offset {self._pos}")
        s = self._data[self._pos : end].decode("utf-8", errors="replace")
        self._pos = end + 1
        return s

    def take(self, n: int) -> bytes:
        if self._pos + n > len(self._data):
            raise WireError(f"truncated buffer: need {n} bytes at offset {self._pos}")
        b = self._data[self._pos : self._pos + n]
        self._pos += n
        return b

    def rest(self) -> bytes:
        b = self._data[self._pos :]
        self._pos = len(self._data)
        return b

    def remaining(self) -> int:
        return len(self._data) - self._pos


class FrameDecoder:
    """Incremental decoder: feed arbitrary byte chunks, get complete ``(type, payload)`` packets."""

    def __init__(self) -> None:
        self._buf = bytearray()

    def feed(self, chunk: bytes) -> Iterator[tuple[int, bytes]]:
        self._buf += chunk
        while len(self._buf) >= 2:
            (size,) = struct.unpack_from("<H", self._buf, 0)
            if size < HEADER_SIZE:
                raise WireError(f"impossible packet size {size}")
            if len(self._buf) < size:
                return
            ptype = self._buf[2]
            payload = bytes(self._buf[HEADER_SIZE:size])
            del self._buf[:size]
            yield ptype, payload
