"""Admin Port protocol v3 (OpenTTD 15.x) message encoding/decoding.

Layouts are taken from network_admin.cpp at tag 15.2/15.3 (see docs/research/03-openttd-source.md §1.3).
"""

import pytest

from railhead import admin_protocol as ap
from railhead.wire import PacketWriter, encode_packet


def test_join_packet_layout():
    assert ap.encode_join("pw", "railhead", "0.1") == encode_packet(0, b"pw\x00railhead\x000.1\x00")


def test_update_frequency_uses_uint16_type_and_mask():
    pkt = ap.encode_update_frequency(ap.UpdateType.GAMESCRIPT, ap.Freq.AUTOMATIC)
    assert pkt == encode_packet(2, b"\x09\x00\x40\x00")


def test_poll_uses_uint8_type_and_uint32_id():
    pkt = ap.encode_poll(ap.UpdateType.COMPANY_ECONOMY, 0xFFFFFFFF)
    assert pkt == encode_packet(3, b"\x03\xff\xff\xff\xff")


def test_rcon_rejects_overlong_command():
    with pytest.raises(ValueError):
        ap.encode_rcon("x" * 500)


def test_gamescript_requires_compact_ascii_json_without_floats():
    assert ap.encode_gamescript({"m": "ping", "id": 3}) == encode_packet(6, b'{"m":"ping","id":3}\x00')
    with pytest.raises(ValueError):
        ap.encode_gamescript({"x": 1.5})
    with pytest.raises(ValueError):
        ap.encode_gamescript({"s": "x" * 9000})


def test_decode_protocol_lists_allowed_frequencies():
    w = PacketWriter().u8(3)
    w.bool(True).u16(0).u16(0x3F)  # DATE
    w.bool(True).u16(9).u16(0x40)  # GAMESCRIPT
    w.bool(False)
    msg = ap.decode(103, w.getvalue())
    assert isinstance(msg, ap.ServerProtocol)
    assert msg.version == 3
    assert msg.frequencies == {ap.UpdateType.DATE: 0x3F, ap.UpdateType.GAMESCRIPT: 0x40}


def test_decode_welcome():
    w = (
        PacketWriter()
        .string("railhead-0")
        .string("15.3")
        .bool(True)
        .string("")
        .u32(12345)
        .u8(0)
        .u32(712_000)
        .u16(128)
        .u16(64)
    )
    msg = ap.decode(104, w.getvalue())
    assert msg == ap.ServerWelcome(
        server_name="railhead-0", revision="15.3", dedicated=True, seed=12345, landscape=0,
        start_date=712_000, map_x=128, map_y=64,
    )


def test_decode_rcon_and_rcon_end_and_gamescript():
    assert ap.decode(120, PacketWriter().u16(1).string("hello").getvalue()) == ap.ServerRcon(colour=1, line="hello")
    assert ap.decode(125, PacketWriter().string("date").getvalue()) == ap.ServerRconEnd(command="date")
    gs = ap.decode(124, PacketWriter().string('{"t":"ready"}').getvalue())
    assert gs == ap.ServerGameScript(data={"t": "ready"})


def test_decode_error_and_date_and_pong():
    assert ap.decode(102, b"\x0a") == ap.ServerError(code=10)
    assert ap.decode(107, PacketWriter().u32(730_000).getvalue()) == ap.ServerDate(date=730_000)
    assert ap.decode(126, PacketWriter().u32(77).getvalue()) == ap.ServerPong(token=77)


def test_decode_company_economy():
    w = PacketWriter().u8(1).u64(500_000).u64(300_000).i64(-1200).u16(40)
    w.u64(900_000).u16(350).u16(30)
    w.u64(800_000).u16(300).u16(20)
    msg = ap.decode(117, w.getvalue())
    assert msg.company == 1 and msg.money == 500_000 and msg.loan == 300_000 and msg.income == -1200
    assert msg.quarters[0] == ap.QuarterStats(company_value=900_000, performance=350, delivered_cargo=30)


def test_decode_cmd_logging():
    w = PacketWriter().u32(1).u8(0).u16(42).u16(3).raw(b"\x01\x02\x03").u32(999)
    msg = ap.decode(127, w.getvalue())
    assert msg == ap.ServerCmdLogging(client_id=1, company=0, cmd_id=42, args=b"\x01\x02\x03", frame=999)


def test_decode_unknown_packet_is_preserved_not_dropped():
    msg = ap.decode(250, b"\x01\x02")
    assert msg == ap.Unknown(ptype=250, payload=b"\x01\x02")
