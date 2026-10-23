"""An asyncio emulation of a Tuya device for protocol versions 3.1 – 3.5.

It speaks the real wire format (built with the integration's own `wire` module,
whose byte-compatibility with real devices is pinned by `test_wire.py` golden
vectors) so that the protocol code is exercised end-to-end over a TCP socket.
"""

from __future__ import annotations

import asyncio
import base64
import json
import struct
from typing import Any

from custom_components.tuya_eco.local import wire
from custom_components.tuya_eco.local.wire import Cmd, Frame, WireError

PROTOCOL_3x_HEADER = wire.PROTOCOL_3x_HEADER


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
        self._last_session_key = self.real_key

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
                        header = wire.parse_header(buffer)
                    except WireError:
                        buffer = b""
                        break
                    if len(buffer) < header.total_length:
                        break
                    frame_bytes, buffer = buffer[: header.total_length], buffer[header.total_length :]
                    key = session_key if self.version >= 3.4 else None
                    try:
                        frame = wire.unpack(frame_bytes, key, header, has_retcode=False)
                    except WireError:
                        # wrong key on 3.5: GCM fails → drop the link like real firmware
                        writer.close()
                        return
                    try:
                        session_key = await self._process(frame, writer, session_key)
                    except (WireError, ValueError, AssertionError, KeyError, UnicodeDecodeError):
                        # wrong key: real firmware answers with an error retcode (or drops the link)
                        self._send(writer, frame.cmd, b"", session_key, frame.seqno, retcode=1)
        except (ConnectionResetError, asyncio.CancelledError):
            pass
        finally:
            if writer in self.writers:
                self.writers.remove(writer)
            writer.close()

    async def _process(self, frame: Frame, writer: asyncio.StreamWriter, session_key: bytes) -> bytes:
        if frame.cmd == Cmd.SESS_KEY_NEG_START:
            local_nonce = self._decrypt_raw(frame.payload, self.real_key)
            self._client_nonce = local_nonce
            self._my_nonce = b"fedcba9876543210"
            payload = self._my_nonce + wire.hmac_sha256(self.real_key, local_nonce)
            self._send(writer, Cmd.SESS_KEY_NEG_RESP, payload, self.real_key, frame.seqno, raw=True)
            return self.real_key
        if frame.cmd == Cmd.SESS_KEY_NEG_FINISH:
            expected = wire.hmac_sha256(self.real_key, self._my_nonce)
            got = frame.payload if self.version >= 3.5 else self._decrypt_raw(frame.payload, self.real_key)
            assert got == expected
            if self.version == 3.4:
                return wire.session_key_34(self.real_key, self._client_nonce, self._my_nonce)
            return wire.session_key_35(self.real_key, self._client_nonce, self._my_nonce)

        body = self._decode(frame, session_key)
        self.received.append((frame.cmd, body))
        if frame.cmd == Cmd.HEART_BEAT:
            self._send(writer, Cmd.HEART_BEAT, b"", session_key, frame.seqno)
        elif frame.cmd in (Cmd.DP_QUERY, Cmd.DP_QUERY_NEW) or (
            frame.cmd == Cmd.CONTROL_NEW
            and self.version < 3.4
            and "dps" in body
            and all(v is None for v in body["dps"].values())
        ):
            if self.answer_dp_query:
                self._send(writer, frame.cmd, json.dumps({"dps": self.dps}).encode(), session_key, frame.seqno)
            else:
                self._send(writer, frame.cmd, b"", session_key, frame.seqno, retcode=1)
        elif frame.cmd in (Cmd.CONTROL, Cmd.CONTROL_NEW):
            new = body.get("dps") or body.get("data", {}).get("dps") or {}
            self.dps.update(new)
            if self.reply_to_control:
                self._send(writer, frame.cmd, b"", session_key, frame.seqno)
                self.push(new, writer, session_key)
        return session_key

    # ------------------------------------------------------------------ helpers

    def push(
        self, dps: dict[str, Any], writer: asyncio.StreamWriter | None = None, session_key: bytes | None = None
    ) -> None:
        """Send an unsolicited STATUS frame."""
        key = session_key or self._last_session_key
        payload = json.dumps({"dps": dps, "t": 0}).encode()
        for w in [writer] if writer else list(self.writers):
            self._send(w, Cmd.STATUS, payload, key, 0, with_header=self.version >= 3.3)

    def _decrypt_raw(self, payload: bytes, key: bytes) -> bytes:
        if self.version >= 3.5:
            return payload  # already decrypted by unpack (GCM)
        return wire.aes_ecb_decrypt(key, payload)

    def _decode(self, frame: Frame, session_key: bytes) -> dict[str, Any]:
        payload = frame.payload
        if self.version == 3.4:
            payload = wire.aes_ecb_decrypt(session_key, payload)
        if self.version >= 3.4:
            if payload.startswith(self.version_bytes):
                payload = payload[15:]
        elif self.version >= 3.2:
            if payload.startswith(self.version_bytes):
                payload = payload[15:]
            payload = wire.aes_ecb_decrypt(self.real_key, payload)
        elif payload.startswith(b"3.1"):
            payload = wire.aes_ecb_decrypt(self.real_key, base64.b64decode(payload[3 + 16 :]))
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
            writer.write(wire.pack_6699(self.seqno, cmd, payload, key, retcode=retcode))
            return
        if self.version == 3.4:
            if payload:
                if with_header:
                    payload = self.version_bytes + PROTOCOL_3x_HEADER + payload
                payload = wire.aes_ecb_encrypt(key, payload)
            writer.write(wire.pack_55aa(seqno, cmd, struct.pack(">I", retcode) + payload, hmac_key=key))
            return
        if payload and not raw and self.version >= 3.2:
            payload = wire.aes_ecb_encrypt(self.real_key, payload)
            if with_header:
                payload = self.version_bytes + PROTOCOL_3x_HEADER + payload
        writer.write(wire.pack_55aa(seqno, cmd, struct.pack(">I", retcode) + payload))


__all__ = ["FakeTuyaDevice"]
