"""Golden-vector tests of the wire format.

The hex constants were produced with tinytuya 1.20.0 (the reference implementation)
so that our independent implementation stays byte-compatible with real devices.
"""

from __future__ import annotations

import pytest

from custom_components.tuya_eco.local import wire
from custom_components.tuya_eco.local.wire import Cmd, WireError

KEY = b"abcdef0123456789"
RAW = b'{"devId":"bf01","uid":"bf01","t":"1700000000","dps":{"1":true}}'

F33 = bytes.fromhex(
    "000055aa000000050000000700000057332e330000000000000000000000001ca71c6a77c09b76460d1f0da8467f09bf050f9dcfb4a3f24e585b9fa938866a4e2dd469e051822c573ec64bed50bcf2818447d05679ba7e701dd52dbc3c91cf13637c5f0000aa55"
)
F34 = bytes.fromhex(
    "000055aa0000000700000010000000341ceec67c1af23821b063e9a853a398fe4bdadd888c1a1c099a334132ffd78f7dc582685178ab5412f6e3a13338365b600000aa55"
)
F35 = bytes.fromhex(
    "00006699000000000009000000100000001e3031323334353637383961625e787fddff9efc8d982bd3dccf33125f8acf00009966"
)
F35R = bytes.fromhex(
    "000066990000000003e900000010000000323031323334353637383961622505ee417dd5174cac98d2264498ec041ffb5993f9afd602af983e83414fe684f7dd9df5ec9800009966"
)
UDP = bytes.fromhex(
    "000055aa00000000000000130000007c00000000d09766676f3369eb10b5e9f132fd802a32f571165c139a681964ee1e9a856137bbadc06c25bf6933f127c9c9ca0ac516a483be81e77f2e71f4307bd81685095e7e6fd47351a96972bdc91a04690e1ee1d20b3c9a4b4a86c1182a00bb9b389e726eda8eea40e93b1e3fc14a2570e1827933a48c640000aa55"
)
UDP_JSON = '{"ip":"192.168.1.10","gwId":"bf01","active":2,"ablilty":0,"encrypt":true,"productKey":"pk","version":"3.3"}'


def test_udp_key() -> None:
    assert wire.UDP_KEY.hex() == "6c1ec8e2bb9bb59ab50b0daf649b410a"


def test_pack_33_control_matches_reference() -> None:
    payload = b"3.3" + wire.PROTOCOL_3x_HEADER + wire.aes_ecb_encrypt(KEY, RAW)
    assert wire.pack_55aa(5, Cmd.CONTROL, payload) == F33


def test_unpack_33_client_frame() -> None:
    frame = wire.unpack(F33, None, has_retcode=False)
    assert frame.seqno == 5 and frame.cmd == Cmd.CONTROL and frame.crc_ok
    assert frame.payload.startswith(b"3.3")
    assert wire.aes_ecb_decrypt(KEY, frame.payload[15:]) == RAW


def test_pack_34_hmac_matches_reference() -> None:
    payload = wire.aes_ecb_encrypt(KEY, b"{}")
    assert wire.pack_55aa(7, Cmd.DP_QUERY_NEW, payload, hmac_key=KEY) == F34
    frame = wire.unpack(F34, KEY, has_retcode=False)
    assert frame.crc_ok and wire.aes_ecb_decrypt(KEY, frame.payload) == b"{}"
    bad = wire.unpack(F34, b"ffffffffffffffff", has_retcode=False)
    assert not bad.crc_ok


def test_pack_35_gcm_matches_reference() -> None:
    assert wire.pack_6699(9, Cmd.DP_QUERY_NEW, b"{}", KEY, iv=b"0123456789ab") == F35
    frame = wire.unpack(F35, KEY, has_retcode=False)
    assert frame.payload == b"{}" and frame.retcode == 0 and frame.seqno == 9


def test_unpack_35_device_reply_with_retcode() -> None:
    frame = wire.unpack(F35R, KEY)
    assert frame.seqno == 1001 and frame.retcode == 0
    assert frame.payload == b'{"dps":{"1":true}}'
    assert wire.pack_6699(1001, Cmd.DP_QUERY_NEW, b'{"dps":{"1":true}}', KEY, retcode=0, iv=b"0123456789ab") == F35R


def test_unpack_35_wrong_key() -> None:
    with pytest.raises(WireError):
        wire.unpack(F35, b"ffffffffffffffff", has_retcode=False)


def test_decode_udp_6667() -> None:
    assert wire.decode_udp(UDP) == UDP_JSON


def test_decode_udp_plain_6666() -> None:
    frame = wire.pack_55aa(0, Cmd.UDP_NEW, b"\x00\x00\x00\x00" + UDP_JSON.encode())
    assert wire.decode_udp(frame) == UDP_JSON


def test_decode_udp_7000_gcm() -> None:
    frame = wire.pack_6699(1, Cmd.UDP_NEW, UDP_JSON.encode(), wire.UDP_KEY, retcode=0)
    assert wire.decode_udp(frame) == UDP_JSON


def test_session_keys_match_reference() -> None:
    ln, rn = b"0123456789abcdef", b"fedcba9876543210"
    assert wire.session_key_34(KEY, ln, rn).hex() == "a4b7c598600a65b4e058989b7084e506"
    assert wire.session_key_35(KEY, ln, rn).hex() == "7351b81150a37c33d0b5bc0b36ff9a68"


def test_parse_header_rejects_garbage() -> None:
    with pytest.raises(WireError):
        wire.parse_header(b"\x01\x02\x03\x04" + b"\x00" * 20)
    with pytest.raises(WireError):
        wire.parse_header(b"\x00\x00\x55\xaa\x00\x00\x00\x01\x00\x00\x00\x0a\xff\xff\xff\xff")


def test_unpad_detects_wrong_key() -> None:
    with pytest.raises(WireError):
        wire.aes_ecb_decrypt(b"ffffffffffffffff", wire.aes_ecb_encrypt(KEY, RAW))


def test_discovery_probe_is_decodable() -> None:
    probe = wire.discovery_probe("192.168.1.2")
    assert wire.decode_udp(probe) == '{"from":"app","ip":"192.168.1.2"}'
