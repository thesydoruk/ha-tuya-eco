"""Tuya LAN wire format: framing, checksums and ciphers.

Two frame layouts exist:

* ``55AA`` (protocol 3.1 – 3.4)::

    00 00 55 AA | seqno u32 | cmd u32 | length u32 | [retcode u32] | payload | crc32 u32 or HMAC-SHA256 (3.4) | 00 00 AA 55

  ``length`` counts everything after the header (retcode, payload, checksum, suffix).
  The retcode is only present in frames *sent by the device*.

* ``6699`` (protocol 3.5)::

    00 00 66 99 | 00 00 | seqno u32 | cmd u32 | length u32 | iv[12] | AES-GCM(retcode? + payload) | tag[16] | 00 00 99 66

  The header bytes after the prefix are the GCM associated data.

Payload encryption (AES-128-ECB with PKCS#7, or the 3.1 base64/MD5 scheme) is
the protocol layer's business; this module only knows frames and primitives.
"""

from __future__ import annotations

import binascii
from dataclasses import dataclass
from enum import IntEnum
import hashlib
import hmac
import os
import struct

from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

PREFIX_55AA = 0x000055AA
SUFFIX_55AA = 0x0000AA55
PREFIX_6699 = 0x00006699
SUFFIX_6699 = 0x00009966
PREFIX_55AA_BIN = b"\x00\x00\x55\xaa"
PREFIX_6699_BIN = b"\x00\x00\x66\x99"
SUFFIX_55AA_BIN = b"\x00\x00\xaa\x55"
SUFFIX_6699_BIN = b"\x00\x00\x99\x66"

_HDR_55AA = ">4I"  # prefix, seqno, cmd, length
_HDR_6699 = ">IHIII"  # prefix, unknown, seqno, cmd, length
_RETCODE = ">I"
_END_CRC = ">2I"  # crc32, suffix
_END_HMAC = ">32sI"  # hmac, suffix
_END_GCM = ">16sI"  # tag, suffix
_HDR_55AA_LEN = struct.calcsize(_HDR_55AA)
_HDR_6699_LEN = struct.calcsize(_HDR_6699)
GCM_IV_LEN = 12
GCM_TAG_LEN = 16

MAX_PAYLOAD_LENGTH = 1440  # sanity ceiling against a desynchronised stream
PROTOCOL_3x_HEADER = 12 * b"\x00"  # follows the 3-byte version string


class Cmd(IntEnum):
    """LAN command types."""

    SESS_KEY_NEG_START = 0x03
    SESS_KEY_NEG_RESP = 0x04
    SESS_KEY_NEG_FINISH = 0x05
    CONTROL = 0x07
    STATUS = 0x08
    HEART_BEAT = 0x09
    DP_QUERY = 0x0A
    CONTROL_NEW = 0x0D
    DP_QUERY_NEW = 0x10
    UPDATEDPS = 0x12
    UDP_NEW = 0x13
    REQ_DEVINFO = 0x25
    LAN_EXT_STREAM = 0x40


# Commands whose payload never gets the "3.x" + 12 NUL version header.
NO_PROTOCOL_HEADER_CMDS: frozenset[int] = frozenset(
    {
        Cmd.DP_QUERY,
        Cmd.DP_QUERY_NEW,
        Cmd.UPDATEDPS,
        Cmd.HEART_BEAT,
        Cmd.SESS_KEY_NEG_START,
        Cmd.SESS_KEY_NEG_RESP,
        Cmd.SESS_KEY_NEG_FINISH,
        Cmd.LAN_EXT_STREAM,
    }
)

# Key of the encrypted discovery broadcasts (credit: tuya-convert).
UDP_KEY = hashlib.md5(b"yGAdlopoPVldABfn").digest()  # noqa: S324 - protocol constant


class WireError(ValueError):
    """Malformed or unverifiable frame."""


@dataclass(slots=True, frozen=True)
class Header:
    """Parsed frame header."""

    prefix: int
    seqno: int
    cmd: int
    length: int
    total_length: int


@dataclass(slots=True, frozen=True)
class Frame:
    """A received frame."""

    seqno: int
    cmd: int
    retcode: int
    payload: bytes
    prefix: int
    crc_ok: bool = True
    iv: bytes | None = None


# ---------------------------------------------------------------------- ciphers


def pkcs7_pad(data: bytes, block: int = 16) -> bytes:
    """PKCS#7 padding."""
    n = block - len(data) % block
    return data + bytes([n]) * n


def pkcs7_unpad(data: bytes) -> bytes:
    """Strip PKCS#7 padding; raises WireError on garbage (typical for a wrong key)."""
    if not data:
        raise WireError("empty block")
    n = data[-1]
    if n < 1 or n > 16 or len(data) < n or data[-n:] != bytes([n]) * n:
        raise WireError("invalid padding (wrong key?)")
    return data[:-n]


def aes_ecb_encrypt(key: bytes, data: bytes, *, pad: bool = True) -> bytes:
    """AES-128-ECB."""
    if pad:
        data = pkcs7_pad(data)
    encryptor = Cipher(algorithms.AES(key), modes.ECB()).encryptor()  # noqa: S305 - protocol requirement
    return encryptor.update(data) + encryptor.finalize()


def aes_ecb_decrypt(key: bytes, data: bytes, *, unpad: bool = True) -> bytes:
    """AES-128-ECB."""
    if len(data) % 16:
        raise WireError(f"ciphertext length {len(data)} is not a multiple of 16")
    decryptor = Cipher(algorithms.AES(key), modes.ECB()).decryptor()  # noqa: S305
    raw = decryptor.update(data) + decryptor.finalize()
    return pkcs7_unpad(raw) if unpad else raw


def aes_gcm_encrypt(key: bytes, iv: bytes, data: bytes, aad: bytes | None = None) -> bytes:
    """AES-128-GCM → ciphertext || tag(16)."""
    return AESGCM(key).encrypt(iv, data, aad)


def aes_gcm_decrypt(key: bytes, iv: bytes, data: bytes, tag: bytes, aad: bytes | None = None) -> bytes:
    """AES-128-GCM; raises WireError when the tag does not verify."""
    try:
        return AESGCM(key).decrypt(iv, data + tag, aad)
    except Exception as err:  # InvalidTag
        raise WireError("GCM authentication failed (wrong key?)") from err


def hmac_sha256(key: bytes, data: bytes) -> bytes:
    """HMAC-SHA256 digest."""
    return hmac.new(key, data, hashlib.sha256).digest()


# ---------------------------------------------------------------------- framing


def parse_header(data: bytes) -> Header:
    """Parse the header at the start of `data`."""
    if data[:4] == PREFIX_6699_BIN:
        if len(data) < _HDR_6699_LEN:
            raise WireError("not enough data for a 6699 header")
        prefix, _unknown, seqno, cmd, length = struct.unpack(_HDR_6699, data[:_HDR_6699_LEN])
        total = _HDR_6699_LEN + length + len(SUFFIX_6699_BIN)
    else:
        if len(data) < _HDR_55AA_LEN:
            raise WireError("not enough data for a 55AA header")
        prefix, seqno, cmd, length = struct.unpack(_HDR_55AA, data[:_HDR_55AA_LEN])
        if prefix != PREFIX_55AA:
            raise WireError(f"unknown prefix {prefix:08X}")
        total = _HDR_55AA_LEN + length
    if length > MAX_PAYLOAD_LENGTH:
        raise WireError(f"implausible frame length {length}")
    return Header(prefix, seqno, cmd, length, total)


def pack_55aa(seqno: int, cmd: int, payload: bytes, hmac_key: bytes | None = None) -> bytes:
    """Build a 55AA frame (CRC32, or HMAC-SHA256 when `hmac_key` is given — protocol 3.4)."""
    end_fmt = _END_HMAC if hmac_key else _END_CRC
    length = len(payload) + struct.calcsize(end_fmt)
    data = struct.pack(_HDR_55AA, PREFIX_55AA, seqno, cmd, length) + payload
    if hmac_key:
        check: bytes | int = hmac_sha256(hmac_key, data)
    else:
        check = binascii.crc32(data) & 0xFFFFFFFF
    return data + struct.pack(end_fmt, check, SUFFIX_55AA)


def pack_6699(
    seqno: int, cmd: int, payload: bytes, key: bytes, *, retcode: int | None = None, iv: bytes | None = None
) -> bytes:
    """Build a 6699 (protocol 3.5) frame. `retcode` is only used by devices (and tests)."""
    iv = iv or os.urandom(GCM_IV_LEN)
    plain = (struct.pack(_RETCODE, retcode) if retcode is not None else b"") + payload
    length = len(plain) + GCM_IV_LEN + GCM_TAG_LEN
    header = struct.pack(_HDR_6699, PREFIX_6699, 0, seqno, cmd, length)
    body = aes_gcm_encrypt(key, iv, plain, header[4:])
    return header + iv + body + SUFFIX_6699_BIN


def _strip_retcode(payload: bytes) -> tuple[int, bytes]:
    """6699 frames carry the retcode inside the ciphertext, but not always: detect it."""
    if (
        len(payload) >= 4
        and payload[:1] != b"{"
        and not payload.startswith(b"3.")
        and (payload[4:5] == b"{" or payload[4:6] == b"3." or not payload[4:])
    ):
        return struct.unpack(_RETCODE, payload[:4])[0], payload[4:]
    return 0, payload


def unpack(
    data: bytes, key: bytes | None = None, header: Header | None = None, *, has_retcode: bool | None = True
) -> Frame:
    """Parse one complete frame.

    `key` is the HMAC key for 55AA/3.4 frames (None → CRC32) and the GCM key for 6699.
    `has_retcode` says whether the frame carries the 4-byte return code: frames from a
    device do (True), frames from a client don't (False), and for UDP broadcasts it is
    detected (None).
    """
    header = header or parse_header(data)
    if len(data) < header.total_length:
        raise WireError("not enough data for the frame")
    if header.prefix == PREFIX_55AA:
        end_fmt = _END_HMAC if key else _END_CRC
        end_len = struct.calcsize(end_fmt)
        retcode_len = 4 if has_retcode else 0
        body_end = header.total_length - end_len
        if body_end < _HDR_55AA_LEN + retcode_len:
            raise WireError("frame too short")
        check, _suffix = struct.unpack(end_fmt, data[body_end : header.total_length])
        if key:
            crc_ok = hmac.compare_digest(hmac_sha256(key, data[:body_end]), check)
        else:
            crc_ok = (binascii.crc32(data[:body_end]) & 0xFFFFFFFF) == check
        retcode = struct.unpack(_RETCODE, data[_HDR_55AA_LEN : _HDR_55AA_LEN + 4])[0] if has_retcode else 0
        payload = data[_HDR_55AA_LEN + retcode_len : body_end]
        return Frame(header.seqno, header.cmd, retcode, payload, PREFIX_55AA, crc_ok)

    if not key:
        raise WireError("a key is required to unpack 6699 frames")
    body = data[_HDR_6699_LEN : header.total_length - len(SUFFIX_6699_BIN)]
    if len(body) < GCM_IV_LEN + GCM_TAG_LEN:
        raise WireError("6699 frame too short")
    iv, cipher, tag = body[:GCM_IV_LEN], body[GCM_IV_LEN:-GCM_TAG_LEN], body[-GCM_TAG_LEN:]
    plain = aes_gcm_decrypt(key, iv, cipher, tag, data[4:_HDR_6699_LEN])
    if has_retcode is None:
        retcode, payload = _strip_retcode(plain)
    elif has_retcode and len(plain) >= 4:
        retcode, payload = struct.unpack(_RETCODE, plain[:4])[0], plain[4:]
    else:
        retcode, payload = 0, plain
    return Frame(header.seqno, header.cmd, retcode, payload, PREFIX_6699, True, iv)


# ---------------------------------------------------------------------- discovery


def decode_udp(datagram: bytes) -> str:
    """Decode a discovery broadcast (ports 6666 plain, 6667 ECB, 7000 GCM) to JSON text."""
    try:
        header = parse_header(datagram)
    except WireError:
        return aes_ecb_decrypt(UDP_KEY, datagram).decode()
    if header.prefix == PREFIX_6699:
        return unpack(datagram, UDP_KEY, header, has_retcode=None).payload.decode().rstrip("\x00")
    payload = unpack(datagram, None, header).payload
    if payload[:1] == b"{" and payload[-1:] == b"}":
        return payload.decode()
    return aes_ecb_decrypt(UDP_KEY, payload).decode()


def discovery_probe(source_ip: str) -> bytes:
    """The broadcast that makes 3.4/3.5 devices announce themselves (port 7000)."""
    payload = f'{{"from":"app","ip":"{source_ip}"}}'.encode()
    return pack_6699(0, Cmd.REQ_DEVINFO, payload, UDP_KEY)


# ---------------------------------------------------------------------- session keys


def session_key_34(real_key: bytes, local_nonce: bytes, remote_nonce: bytes) -> bytes:
    """Protocol 3.4 session key: AES-ECB(real_key, local ⊕ remote)."""
    xored = bytes(a ^ b for a, b in zip(local_nonce, remote_nonce, strict=True))
    return aes_ecb_encrypt(real_key, xored, pad=False)


def session_key_35(real_key: bytes, local_nonce: bytes, remote_nonce: bytes) -> bytes:
    """Protocol 3.5 session key: first 16 bytes of AES-GCM(real_key, iv=local_nonce[:12], local ⊕ remote)."""
    xored = bytes(a ^ b for a, b in zip(local_nonce, remote_nonce, strict=True))
    return aes_gcm_encrypt(real_key, local_nonce[:GCM_IV_LEN], xored)[:16]
