"""An asyncio emulation of a Tuya device for protocol versions 3.1 – 3.5.

It speaks the real wire format (built with tinytuya's packing helpers) so that
the integration's protocol code is exercised end-to-end over a TCP socket.
"""

from __future__ import annotations

import asyncio
from hashlib import sha256
import hmac
import json
import struct
from typing import Any

from tinytuya import (
    CONTROL,
    CONTROL_NEW,
    DP_QUERY,
    DP_QUERY_NEW,
    HEART_BEAT,
    PREFIX_55AA_VALUE,
    PREFIX_6699_BIN,
    PREFIX_6699_VALUE,
    SESS_KEY_NEG_FINISH,
    SESS_KEY_NEG_RESP,
    SESS_KEY_NEG_START,
    STATUS,
    UPDATEDPS,
    AESCipher,
    TuyaMessage,
    pack_message,
    parse_header,
    unpack_message,
)

PROTOCOL_3x_HEADER = 12 * b"\x00"


class FakeTuyaDevice:
    """TCP server behaving like a Tuya device."""

    def __init__(self, device_id: str, local_key: str, version: float, dps: dict[str, Any] | None = None) -> None:
        self.device_id = device_id
        self.real_key = local_key.encode("latin1")
        self.version = version
        self.version_bytes = f"{version:.1f}".encode()
        self.dps: dict[str, Any] = dict(dps or {"1": True, "2": 50})
        self.server: asyncio.AbstractServer | None = None
        self.port = 0
        self.writers: list[asyncio.StreamWriter] = []
        self.received: list[tuple[int, Any]] = []
        self.answer_dp_query = True
        self.reply_to_control = True
        self.seqno = 1000

    # ------------------------------------------------------------------ lifecycle

    async def start(self) -> None:
        self.server = await asyncio.start_server(self._handle, "127.0.0.1", 0)
        self.port = self.server.sockets[0].getsockname()[1]

    async def stop(self) -> None:
        for writer in list(self.writers):
            writer.close()
        if self.server:
            self.server.close()
            await self.server.wait_closed()

    async def drop_connections(self) -> None:
        for writer in list(self.writers):
            writer.close()
            with contextlib_suppress():
                await writer.wait_closed()
        self.writers.clear()

    # ------------------------------------------------------------------ connection

    async def _handle(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        self.writers.append(writer)
        session_key = self.real_key
        buffer = b""
        try:
            while True:
                data = await reader.read(4096)
                if not data:
                    break
                buffer += data
                while buffer:
                    try:
                        header = parse_header(buffer)
                    except Exception:
                        buffer = b""
                        break
                    if len(buffer) < header.total_length:
                        break
                    frame, buffer = buffer[: header.total_length], buffer[header.total_length :]
                    hmac_key = session_key if self.version >= 3.4 else None
                    msg = unpack_message(frame, hmac_key=hmac_key, header=header, no_retcode=True)
                    try:
                        session_key = await self._process(msg, writer, session_key)
                    except (ValueError, AssertionError, KeyError, UnicodeDecodeError):
                        # wrong key: real firmware answers with an error retcode (or drops the link)
                        self._send(writer, msg.cmd, b"", session_key, msg.seqno, retcode=1)
        except (ConnectionResetError, asyncio.CancelledError):
            pass
        finally:
            if writer in self.writers:
                self.writers.remove(writer)
            writer.close()

    async def _process(self, msg: TuyaMessage, writer: asyncio.StreamWriter, session_key: bytes) -> bytes:
        if msg.cmd == SESS_KEY_NEG_START:
            local_nonce = self._decrypt_raw(msg.payload, self.real_key)
            self._client_nonce = local_nonce
            self._my_nonce = b"fedcba9876543210"
            payload = self._my_nonce + hmac.new(self.real_key, local_nonce, sha256).digest()
            self._send(writer, SESS_KEY_NEG_RESP, payload, self.real_key, msg.seqno, raw=True)
            return self.real_key
        if msg.cmd == SESS_KEY_NEG_FINISH:
            expected = hmac.new(self.real_key, self._my_nonce, sha256).digest()
            assert msg.payload == expected or self._decrypt_raw(msg.payload, self.real_key) == expected
            xored = bytes(a ^ b for a, b in zip(self._client_nonce, self._my_nonce, strict=True))
            cipher = AESCipher(self.real_key)
            if self.version == 3.4:
                return cipher.encrypt(xored, False, pad=False)
            return cipher.encrypt(xored, use_base64=False, pad=False, iv=self._client_nonce[:12])[12:28]

        body = self._decode(msg, session_key)
        self.received.append((msg.cmd, body))
        if msg.cmd == HEART_BEAT:
            self._send(writer, HEART_BEAT, b"", session_key, msg.seqno)
        elif msg.cmd in (DP_QUERY, DP_QUERY_NEW) or (
            msg.cmd == CONTROL_NEW
            and self.version < 3.4
            and "dps" in body
            and all(v is None for v in body["dps"].values())
        ):
            if self.answer_dp_query:
                self._send(writer, msg.cmd, json.dumps({"dps": self.dps}).encode(), session_key, msg.seqno)
            else:
                self._send(writer, msg.cmd, b"", session_key, msg.seqno, retcode=1)
        elif msg.cmd in (CONTROL, CONTROL_NEW):
            new = body.get("dps") or body.get("data", {}).get("dps") or {}
            self.dps.update(new)
            if self.reply_to_control:
                self._send(writer, msg.cmd, b"", session_key, msg.seqno)
                self.push(new, writer, session_key)
        elif msg.cmd == UPDATEDPS:
            pass
        return session_key

    # ------------------------------------------------------------------ helpers

    def push(
        self, dps: dict[str, Any], writer: asyncio.StreamWriter | None = None, session_key: bytes | None = None
    ) -> None:
        """Send an unsolicited STATUS frame."""
        key = session_key or self._session_keys_for_push()
        payload = json.dumps({"dps": dps, "t": 0}).encode()
        for w in [writer] if writer else list(self.writers):
            self._send(w, STATUS, payload, key, 0, with_header=self.version >= 3.3)

    def _session_keys_for_push(self) -> bytes:
        return getattr(self, "_last_session_key", self.real_key)

    def _decrypt_raw(self, payload: bytes, key: bytes) -> bytes:
        if self.version >= 3.5:
            return payload  # already decrypted by unpack_message (GCM)
        return AESCipher(key).decrypt(payload, False, decode_text=False)

    def _decode(self, msg: TuyaMessage, session_key: bytes) -> dict[str, Any]:
        payload = msg.payload
        if self.version == 3.4:
            payload = AESCipher(session_key).decrypt(payload, False, decode_text=False)
        if self.version >= 3.4:
            if payload.startswith(self.version_bytes):
                payload = payload[15:]
        elif self.version >= 3.2:
            if payload.startswith(self.version_bytes):
                payload = payload[15:]
            payload = AESCipher(self.real_key).decrypt(payload, False, decode_text=False)
        elif payload.startswith(b"3.1"):
            payload = AESCipher(self.real_key).decrypt(payload[3 + 16 :], decode_text=False)
        if not payload:
            return {}
        return json.loads(payload)

    def _send(
        self,
        writer: asyncio.StreamWriter,
        cmd: int,
        payload: bytes,
        key: bytes,
        seqno: int,
        *,
        retcode: int = 0,
        raw: bool = False,
        with_header: bool = False,
    ) -> None:
        self._last_session_key = key
        if self.version >= 3.5:
            if payload and with_header:
                payload = self.version_bytes + PROTOCOL_3x_HEADER + payload
            self.seqno += 1
            msg = TuyaMessage(self.seqno, cmd, retcode, payload, 0, True, PREFIX_6699_VALUE, True)
            writer.write(pack_message(msg, hmac_key=key))
            return
        if self.version == 3.4:
            if payload:
                if with_header:
                    payload = self.version_bytes + PROTOCOL_3x_HEADER + payload
                payload = AESCipher(key).encrypt(payload, False)
            frame_payload = struct.pack(">I", retcode) + payload
            msg = TuyaMessage(seqno, cmd, 0, frame_payload, 0, True, PREFIX_55AA_VALUE, False)
            writer.write(pack_message(msg, hmac_key=key))
            return
        if payload and not raw and self.version >= 3.2:
            payload = AESCipher(self.real_key).encrypt(payload, False)
            if with_header:
                payload = self.version_bytes + PROTOCOL_3x_HEADER + payload
        frame_payload = struct.pack(">I", retcode) + payload
        msg = TuyaMessage(seqno, cmd, 0, frame_payload, 0, True, PREFIX_55AA_VALUE, False)
        writer.write(pack_message(msg))


class contextlib_suppress:
    """Tiny async-safe suppress for close errors."""

    async def __aenter__(self) -> None:
        return None

    async def __aexit__(self, *exc: object) -> bool:
        return True

    def __enter__(self) -> None:
        return None

    def __exit__(self, *exc: object) -> bool:
        return True


__all__ = ["PREFIX_6699_BIN", "FakeTuyaDevice"]
