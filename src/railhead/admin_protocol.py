"""OpenTTD Admin Port protocol v3 — message encoders and decoders.

Source of truth: OpenTTD 15.2/15.3 ``src/network/network_admin.cpp`` and ``core/tcp_admin.h``
(evidence in docs/research/03-openttd-source.md §1). Only the messages Railhead needs are typed;
everything else decodes to :class:`Unknown` so nothing is silently dropped.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from enum import IntEnum
from typing import Any

from railhead.wire import PacketReader, PacketWriter, encode_packet

PROTOCOL_VERSION = 3
MAX_RCON_BYTES = 499  # NETWORK_RCONCOMMAND_LENGTH - 1
MAX_GS_JSON_BYTES = 8999  # NETWORK_GAMESCRIPT_JSON_LENGTH - 1 (admin -> GS)


class AdminPacket(IntEnum):
    JOIN = 0
    QUIT = 1
    UPDATE_FREQUENCY = 2
    POLL = 3
    CHAT = 4
    RCON = 5
    GAMESCRIPT = 6
    PING = 7


class ServerPacket(IntEnum):
    FULL = 100
    BANNED = 101
    ERROR = 102
    PROTOCOL = 103
    WELCOME = 104
    NEWGAME = 105
    SHUTDOWN = 106
    DATE = 107
    COMPANY_NEW = 113
    COMPANY_INFO = 114
    COMPANY_UPDATE = 115
    COMPANY_REMOVE = 116
    COMPANY_ECONOMY = 117
    COMPANY_STATS = 118
    RCON = 120
    CONSOLE = 121
    CMD_NAMES = 122
    GAMESCRIPT = 124
    RCON_END = 125
    PONG = 126
    CMD_LOGGING = 127


class UpdateType(IntEnum):
    DATE = 0
    CLIENT_INFO = 1
    COMPANY_INFO = 2
    COMPANY_ECONOMY = 3
    COMPANY_STATS = 4
    CHAT = 5
    CONSOLE = 6
    CMD_NAMES = 7
    CMD_LOGGING = 8
    GAMESCRIPT = 9


class Freq(IntEnum):
    POLL = 0x01
    DAILY = 0x02
    WEEKLY = 0x04
    MONTHLY = 0x08
    QUARTERLY = 0x10
    ANNUALLY = 0x20
    AUTOMATIC = 0x40


# ---------------------------------------------------------------- encoders (admin -> server)


def encode_join(password: str, name: str, version: str) -> bytes:
    return encode_packet(AdminPacket.JOIN, PacketWriter().string(password).string(name).string(version).getvalue())


def encode_quit() -> bytes:
    return encode_packet(AdminPacket.QUIT, b"")


def encode_update_frequency(update_type: UpdateType, freq: int) -> bytes:
    return encode_packet(AdminPacket.UPDATE_FREQUENCY, PacketWriter().u16(int(update_type)).u16(int(freq)).getvalue())


def encode_poll(update_type: UpdateType, ident: int = 0) -> bytes:
    return encode_packet(AdminPacket.POLL, PacketWriter().u8(int(update_type)).u32(ident).getvalue())


def encode_rcon(command: str) -> bytes:
    if len(command.encode("utf-8")) > MAX_RCON_BYTES:
        raise ValueError(f"rcon command longer than {MAX_RCON_BYTES} bytes")
    return encode_packet(AdminPacket.RCON, PacketWriter().string(command).getvalue())


def _reject_floats(obj: Any) -> None:
    if isinstance(obj, float):
        raise ValueError("GameScript JSON cannot carry floats; use fixed-point integers")
    if isinstance(obj, dict):
        for v in obj.values():
            _reject_floats(v)
    elif isinstance(obj, (list, tuple)):
        for v in obj:
            _reject_floats(v)


def dumps_gs(obj: dict) -> str:
    """Serialize for the GS: compact, ASCII-only (the server rewrites \\n\\t\\r to '?'), no floats."""
    if not isinstance(obj, dict):
        raise ValueError("GameScript JSON root must be an object")
    _reject_floats(obj)
    s = json.dumps(obj, separators=(",", ":"), ensure_ascii=True)
    if len(s) > MAX_GS_JSON_BYTES:
        raise ValueError(f"GameScript JSON is {len(s)} bytes > {MAX_GS_JSON_BYTES}")
    return s


def encode_gamescript(obj: dict) -> bytes:
    return encode_packet(AdminPacket.GAMESCRIPT, PacketWriter().string(dumps_gs(obj)).getvalue())


def encode_ping(token: int) -> bytes:
    return encode_packet(AdminPacket.PING, PacketWriter().u32(token).getvalue())


# ---------------------------------------------------------------- decoded messages (server -> admin)


@dataclass(frozen=True)
class Unknown:
    ptype: int
    payload: bytes


@dataclass(frozen=True)
class ServerError:
    code: int


@dataclass(frozen=True)
class ServerProtocol:
    version: int
    frequencies: dict = field(default_factory=dict)


@dataclass(frozen=True)
class ServerWelcome:
    server_name: str
    revision: str
    dedicated: bool
    seed: int
    landscape: int
    start_date: int
    map_x: int
    map_y: int


@dataclass(frozen=True)
class ServerNewGame:
    pass


@dataclass(frozen=True)
class ServerShutdown:
    pass


@dataclass(frozen=True)
class ServerDate:
    date: int


@dataclass(frozen=True)
class ServerCompanyNew:
    company: int


@dataclass(frozen=True)
class ServerCompanyInfo:
    company: int
    name: str
    manager: str
    colour: int
    inaugurated_year: int
    is_ai: bool
    quarters_of_bankruptcy: int


@dataclass(frozen=True)
class ServerCompanyRemove:
    company: int
    reason: int


@dataclass(frozen=True)
class QuarterStats:
    company_value: int
    performance: int
    delivered_cargo: int


@dataclass(frozen=True)
class ServerCompanyEconomy:
    company: int
    money: int
    loan: int
    income: int
    delivered_cargo: int
    quarters: tuple


@dataclass(frozen=True)
class ServerRcon:
    colour: int
    line: str


@dataclass(frozen=True)
class ServerRconEnd:
    command: str


@dataclass(frozen=True)
class ServerConsole:
    origin: str
    text: str


@dataclass(frozen=True)
class ServerGameScript:
    data: Any


@dataclass(frozen=True)
class ServerPong:
    token: int


@dataclass(frozen=True)
class ServerCmdNames:
    names: dict


@dataclass(frozen=True)
class ServerCmdLogging:
    client_id: int
    company: int
    cmd_id: int
    args: bytes
    frame: int


def _signed64(v: int) -> int:
    return v - (1 << 64) if v >= 1 << 63 else v


def decode(ptype: int, payload: bytes) -> Any:
    r = PacketReader(payload)
    if ptype == ServerPacket.ERROR:
        return ServerError(code=r.u8())
    if ptype == ServerPacket.PROTOCOL:
        version = r.u8()
        freqs: dict[UpdateType | int, int] = {}
        while r.remaining() and r.bool():
            t, mask = r.u16(), r.u16()
            try:
                freqs[UpdateType(t)] = mask
            except ValueError:
                freqs[t] = mask
        return ServerProtocol(version=version, frequencies=freqs)
    if ptype == ServerPacket.WELCOME:
        name, rev, dedicated = r.string(), r.string(), r.bool()
        r.string()  # legacy map name, always empty
        return ServerWelcome(name, rev, dedicated, r.u32(), r.u8(), r.u32(), r.u16(), r.u16())
    if ptype == ServerPacket.NEWGAME:
        return ServerNewGame()
    if ptype == ServerPacket.SHUTDOWN:
        return ServerShutdown()
    if ptype == ServerPacket.DATE:
        return ServerDate(date=r.u32())
    if ptype == ServerPacket.COMPANY_NEW:
        return ServerCompanyNew(company=r.u8())
    if ptype == ServerPacket.COMPANY_INFO:
        cid, name, manager, colour = r.u8(), r.string(), r.string(), r.u8()
        r.bool()  # legacy "protected"
        year, is_ai = r.u32(), r.bool()
        qob = r.u8() if r.remaining() else 0
        return ServerCompanyInfo(cid, name, manager, colour, year, is_ai, qob)
    if ptype == ServerPacket.COMPANY_REMOVE:
        return ServerCompanyRemove(company=r.u8(), reason=r.u8())
    if ptype == ServerPacket.COMPANY_ECONOMY:
        cid, money, loan, income, delivered = r.u8(), r.u64(), r.u64(), r.i64(), r.u16()
        quarters = []
        while r.remaining() >= 12:
            quarters.append(QuarterStats(company_value=r.u64(), performance=r.u16(), delivered_cargo=r.u16()))
        return ServerCompanyEconomy(cid, _signed64(money), loan, income, delivered, tuple(quarters))
    if ptype == ServerPacket.RCON:
        return ServerRcon(colour=r.u16(), line=r.string())
    if ptype == ServerPacket.RCON_END:
        return ServerRconEnd(command=r.string())
    if ptype == ServerPacket.CONSOLE:
        return ServerConsole(origin=r.string(), text=r.string())
    if ptype == ServerPacket.GAMESCRIPT:
        raw = r.string()
        try:
            data = json.loads(raw)
        except json.JSONDecodeError:
            data = {"t": "_unparseable", "raw": raw}
        return ServerGameScript(data=data)
    if ptype == ServerPacket.PONG:
        return ServerPong(token=r.u32())
    if ptype == ServerPacket.CMD_NAMES:
        names = {}
        while r.remaining() and r.bool():
            cid = r.u16()
            names[cid] = r.string()
        return ServerCmdNames(names=names)
    if ptype == ServerPacket.CMD_LOGGING:
        client_id, company, cmd_id = r.u32(), r.u8(), r.u16()
        args = r.take(r.u16())
        return ServerCmdLogging(client_id, company, cmd_id, args, r.u32())
    return Unknown(ptype=ptype, payload=payload)
