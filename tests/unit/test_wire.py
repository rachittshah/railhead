"""Wire-level codec for OpenTTD's TCP packet framing (used by the Admin Port).

Framing: [uint16 LE total_size][uint8 type][payload...], total_size includes the 3 header bytes.
Fields: little-endian integers, bools as uint8, strings as UTF-8 terminated by NUL.
"""

import pytest

from railhead.wire import FrameDecoder, PacketReader, PacketWriter, WireError, encode_packet


def test_encode_packet_prefixes_total_size_and_type():
    assert encode_packet(7, b"\x01\x02") == b"\x05\x00\x07\x01\x02"


def test_encode_packet_rejects_oversize_payload():
    with pytest.raises(WireError):
        encode_packet(1, b"x" * 0xFFFF)


def test_writer_reader_round_trip_all_field_types():
    w = PacketWriter()
    w.u8(250).u16(65000).u32(4_000_000_000).u64(2**63 + 5).i64(-42).bool(True).string("Gare du Nord ✓")
    r = PacketReader(w.getvalue())
    assert r.u8() == 250
    assert r.u16() == 65000
    assert r.u32() == 4_000_000_000
    assert r.u64() == 2**63 + 5
    assert r.i64() == -42
    assert r.bool() is True
    assert r.string() == "Gare du Nord ✓"
    assert r.remaining() == 0


def test_reader_raises_on_truncated_integer():
    with pytest.raises(WireError):
        PacketReader(b"\x01").u16()


def test_reader_raises_on_unterminated_string():
    with pytest.raises(WireError):
        PacketReader(b"abc").string()


def test_writer_rejects_nul_inside_string():
    with pytest.raises(WireError):
        PacketWriter().string("a\x00b")


def test_frame_decoder_handles_split_and_coalesced_packets():
    a = encode_packet(104, b"hello")
    b = encode_packet(124, b'{"k":1}\x00')
    stream = a + b
    dec = FrameDecoder()
    out = []
    # feed byte-by-byte: worst-case fragmentation
    for i in range(len(stream)):
        out.extend(dec.feed(stream[i : i + 1]))
    assert out == [(104, b"hello"), (124, b'{"k":1}\x00')]
    # feed all at once: coalesced
    assert list(FrameDecoder().feed(stream)) == [(104, b"hello"), (124, b'{"k":1}\x00')]


def test_frame_decoder_rejects_impossible_size_header():
    with pytest.raises(WireError):
        list(FrameDecoder().feed(b"\x02\x00\x01"))


def test_reader_take_reads_fixed_length_bytes_and_checks_bounds():
    r = PacketReader(b"\x01\x02\x03\x04")
    assert r.take(3) == b"\x01\x02\x03"
    with pytest.raises(WireError):
        r.take(2)
