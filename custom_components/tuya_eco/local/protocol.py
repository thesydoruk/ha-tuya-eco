"""Asyncio implementation of the Tuya LAN protocol (versions 3.1 – 3.5).

Framing, AES and HMAC primitives are taken from `tinytuya.core` (MIT); the
transport, dispatching, session-key negotiation and error model are our own so
that nothing blocks the Home Assistant event loop.
"""

from __future__ import annotations

import asyncio
import binascii
from collections.abc import Callable
from dataclasses import dataclass, field
from hashlib import md5, sha256
import hmac
import json
import logging
import os
import time
from typing import Any

from tinytuya.core import command_types as CT, header as H
from tinytuya.core.crypto_helper import AESCipher
from tinytuya.core.exceptions import DecodeError
from tinytuya.core.message_helper import (
    MessagePayload,
    TuyaMessage,
    pack_message,
    parse_header,
    unpack_message,
)

_LOGGER = logging.getLogger(__name__)

TCP_PORT = 6668
CONNECT_TIMEOUT = 5.0
REPLY_TIMEOUT = 5.0
HEARTBEAT_INTERVAL = 10.0
HEARTBEAT_MISSES_BEFORE_CLOSE = 2
MAX_BUFFER = 64 * 1024

SUPPORTED_VERSIONS: tuple[float, ...] = (3.1, 3.2, 3.3, 3.4, 3.5)

# Sentinel sequence numbers for replies that do not echo our seqno.
_SEQ_HEARTBEAT = -100
_SEQ_UPDATEDPS = -101
_SEQ_SESSION = -102


class TuyaLocalError(Exception):
    """Base error of the local protocol."""


class TuyaConnectionError(TuyaLocalError):
    """TCP connection could not be established or was lost."""


class TuyaTimeoutError(TuyaLocalError):
    """The device did not answer in time."""


class TuyaKeyError(TuyaLocalError):
    """The local key (or protocol version) is wrong."""


class TuyaDecodeError(TuyaLocalError):
    """A frame could not be decoded."""


class TuyaDeviceError(TuyaLocalError):
    """The device answered with an error."""


class TuyaListener:
    """Callbacks the device layer implements."""

    def status_updated(self, dps: dict[str, Any]) -> None:
        """Unsolicited or polled DP values arrived."""

    def disconnected(self, exc: Exception | None) -> None:
        """The TCP connection was closed."""


@dataclass(slots=True)
class _Pending:
    cmd: int
    future: asyncio.Future[TuyaMessage | None]
    created: float = field(default_factory=time.monotonic)


class TuyaLanProtocol(asyncio.Protocol):
    """One TCP connection to one Tuya device (or gateway)."""

    def __init__(
        self,
        device_id: str,
        local_key: str,
        version: float,
        listener: TuyaListener,
        *,
        node_id: str | None = None,
        dps_to_request: list[int] | None = None,
    ) -> None:
        """Initialise protocol state (no I/O)."""
        if version not in SUPPORTED_VERSIONS:
            raise ValueError(f"Unsupported protocol version {version}")
        self.device_id = device_id
        self.node_id = node_id
        self.version = version
        self.version_bytes = f"{version:.1f}".encode()
        self.version_header = self.version_bytes + H.PROTOCOL_3x_HEADER
        self.real_key = local_key.encode("latin1")
        self.key = self.real_key  # replaced by session key for 3.4/3.5
        self._listener = listener
        self.device22 = version == 3.2
        self.dps_to_request: dict[str, None] = {str(dp): None for dp in (dps_to_request or [1])}

        self.transport: asyncio.Transport | None = None
        self._buffer = b""
        self._seqno = 1
        self._pending: dict[int, _Pending] = {}
        self._write_lock = asyncio.Lock()
        self._exchange_lock = asyncio.Lock()
        self._heartbeat_task: asyncio.Task[None] | None = None
        self._closed = False
        self.session_ready = version < 3.4
        self.last_rx = time.monotonic()

    # ------------------------------------------------------------------ transport

    @property
    def connected(self) -> bool:
        """True while the TCP transport is open."""
        return self.transport is not None and not self.transport.is_closing()

    def connection_made(self, transport: asyncio.BaseTransport) -> None:
        """Store the transport."""
        self.transport = transport  # type: ignore[assignment]

    def connection_lost(self, exc: Exception | None) -> None:
        """Fail all pending requests and notify the listener."""
        self.transport = None
        self._closed = True
        if self._heartbeat_task:
            self._heartbeat_task.cancel()
            self._heartbeat_task = None
        for pending in list(self._pending.values()):
            if not pending.future.done():
                pending.future.set_exception(TuyaConnectionError(f"connection lost: {exc or 'closed'}"))
        self._pending.clear()
        try:
            self._listener.disconnected(exc)
        except Exception:  # noqa: BLE001 - never let listener errors kill the loop
            _LOGGER.exception("%s: listener.disconnected raised", self.device_id)

    def data_received(self, data: bytes) -> None:
        """Re-assemble frames from the TCP stream and dispatch them."""
        self.last_rx = time.monotonic()
        self._buffer += data
        if len(self._buffer) > MAX_BUFFER:
            _LOGGER.warning("%s: receive buffer overflow, resetting", self.device_id)
            self._buffer = b""
            return
        while self._buffer:
            # Re-sync on a known prefix.
            idx55 = self._buffer.find(H.PREFIX_55AA_BIN)
            idx66 = self._buffer.find(H.PREFIX_6699_BIN)
            candidates = [i for i in (idx55, idx66) if i >= 0]
            if not candidates:
                self._buffer = b""
                return
            start = min(candidates)
            if start:
                self._buffer = self._buffer[start:]
            try:
                header = parse_header(self._buffer)
            except DecodeError:
                if len(self._buffer) < 20:
                    return  # need more bytes for a header
                self._buffer = self._buffer[4:]
                continue
            if len(self._buffer) < header.total_length:
                return
            frame = self._buffer[: header.total_length]
            self._buffer = self._buffer[header.total_length :]
            try:
                hmac_key = self.key if self.version >= 3.4 else None
                msg = unpack_message(frame, hmac_key=hmac_key, header=header)
            except Exception as err:  # noqa: BLE001
                _LOGGER.debug("%s: frame unpack failed: %s", self.device_id, err)
                continue
            self._dispatch(msg)

    async def close(self) -> None:
        """Close the connection."""
        self._closed = True
        if self._heartbeat_task:
            self._heartbeat_task.cancel()
            self._heartbeat_task = None
        if self.transport:
            self.transport.close()

    # ------------------------------------------------------------------ dispatch

    def _dispatch(self, msg: TuyaMessage) -> None:
        pending: _Pending | None = None
        if msg.cmd in (CT.SESS_KEY_NEG_RESP, CT.SESS_KEY_NEG_START):
            pending = self._pending.pop(_SEQ_SESSION, None)
        elif msg.cmd == CT.HEART_BEAT:
            pending = self._pending.pop(_SEQ_HEARTBEAT, None)
        elif msg.seqno in self._pending:
            pending = self._pending.pop(msg.seqno)
        elif self.version >= 3.5:
            # 3.5 devices reply with their own global seqno: match by command.
            for seq, cand in list(self._pending.items()):
                if cand.cmd == msg.cmd:
                    pending = self._pending.pop(seq)
                    break

        if msg.cmd == CT.STATUS:
            # Unsolicited push (also the reply to CONTROL on some firmware).
            self._handle_status_push(msg)
            if pending is None:
                # A DP_QUERY may be answered with STATUS by old firmware.
                for seq, cand in list(self._pending.items()):
                    if cand.cmd in (CT.DP_QUERY, CT.DP_QUERY_NEW, CT.CONTROL_NEW):
                        pending = self._pending.pop(seq)
                        break
        if pending is not None and not pending.future.done():
            pending.future.set_result(msg)
        elif pending is None and msg.cmd not in (CT.STATUS, CT.CONTROL_NEW, CT.CONTROL):
            _LOGGER.debug("%s: unmatched frame cmd=%s seq=%s", self.device_id, msg.cmd, msg.seqno)

    def _handle_status_push(self, msg: TuyaMessage) -> None:
        try:
            decoded = self._decode_payload(msg.payload)
        except TuyaLocalError as err:
            _LOGGER.debug("%s: cannot decode status push: %s", self.device_id, err)
            return
        if not isinstance(decoded, dict):
            return
        dps = decoded.get("dps")
        if isinstance(dps, dict) and self._accepts(decoded):
            try:
                self._listener.status_updated(dps)
            except Exception:  # noqa: BLE001
                _LOGGER.exception("%s: listener.status_updated raised", self.device_id)

    def _accepts(self, decoded: dict[str, Any]) -> bool:
        """Filter gateway pushes that belong to another sub-device."""
        cid = decoded.get("cid")
        if self.node_id:
            return cid in (None, self.node_id)
        return cid is None

    # ------------------------------------------------------------------ payload

    def _build_payload(self, command: int, data: Any = None) -> MessagePayload:
        """Build the JSON payload for `command` (mirrors tinytuya payload_dict)."""
        now = int(time.time())
        cmd_out = command
        body: dict[str, Any]
        if self.version >= 3.4:
            if command in (CT.CONTROL, CT.CONTROL_NEW):
                cmd_out = CT.CONTROL_NEW
                body = {"protocol": 5, "t": now, "data": {}}
                if self.node_id:
                    body["data"]["cid"] = self.node_id
                    body["data"]["ctype"] = 0
                if data is not None:
                    body["data"]["dps"] = data
            elif command in (CT.DP_QUERY, CT.DP_QUERY_NEW):
                cmd_out = CT.DP_QUERY_NEW
                body = {}
                if self.node_id:
                    body = {"cid": self.node_id}
            elif command == CT.HEART_BEAT:
                body = {"gwId": self.device_id, "devId": self.device_id}
            elif command == CT.UPDATEDPS:
                body = {"dpId": data if data is not None else [18, 19, 20]}
                if self.node_id:
                    body["cid"] = self.node_id
            else:
                body = {"gwId": self.device_id, "devId": self.device_id, "uid": self.device_id, "t": str(now)}
        else:
            if command == CT.CONTROL:
                body = {"devId": self.device_id, "uid": self.device_id, "t": str(now)}
                if data is not None:
                    body["dps"] = data
            elif command == CT.DP_QUERY:
                body = {"gwId": self.device_id, "devId": self.device_id, "uid": self.device_id, "t": str(now)}
                if self.device22:
                    cmd_out = CT.CONTROL_NEW
                    body = {"devId": self.device_id, "uid": self.device_id, "t": str(now)}
                    body["dps"] = dict(self.dps_to_request)
            elif command == CT.HEART_BEAT:
                body = {"gwId": self.device_id, "devId": self.device_id}
            elif command == CT.UPDATEDPS:
                body = {"dpId": data if data is not None else [18, 19, 20]}
            else:
                body = {"gwId": self.device_id, "devId": self.device_id, "uid": self.device_id, "t": str(now)}
            if self.node_id:
                body["cid"] = self.node_id
        raw = json.dumps(body, separators=(",", ":")).encode()
        return MessagePayload(cmd_out, raw)

    def _encode(self, msg: MessagePayload) -> tuple[int, bytes]:
        """Encrypt + frame a payload. Returns (seqno, bytes)."""
        payload = msg.payload
        seqno = self._seqno
        self._seqno += 1
        if self.version >= 3.4:
            if msg.cmd not in H.NO_PROTOCOL_HEADER_CMDS:
                payload = self.version_header + payload
            if self.version >= 3.5:
                tmsg = TuyaMessage(seqno, msg.cmd, None, payload, 0, True, H.PREFIX_6699_VALUE, True)
                return seqno, pack_message(tmsg, hmac_key=self.key)
            payload = AESCipher(self.key).encrypt(payload, use_base64=False)
            tmsg = TuyaMessage(seqno, msg.cmd, 0, payload, 0, True, H.PREFIX_55AA_VALUE, False)
            return seqno, pack_message(tmsg, hmac_key=self.key)
        if self.version >= 3.2:
            payload = AESCipher(self.key).encrypt(payload, use_base64=False)
            if msg.cmd not in H.NO_PROTOCOL_HEADER_CMDS:
                payload = self.version_header + payload
        elif msg.cmd == CT.CONTROL:
            payload = AESCipher(self.key).encrypt(payload)  # base64
            pre = b"data=" + payload + b"||lpv=" + H.PROTOCOL_VERSION_BYTES_31 + b"||" + self.key
            digest = md5(pre).hexdigest()
            payload = H.PROTOCOL_VERSION_BYTES_31 + digest[8:][:16].encode("latin1") + payload
        tmsg = TuyaMessage(seqno, msg.cmd, 0, payload, 0, True, H.PREFIX_55AA_VALUE, False)
        return seqno, pack_message(tmsg)

    def _decode_payload(self, payload: bytes) -> dict[str, Any] | None:
        """Decrypt and parse a received payload (mirrors tinytuya._decode_payload)."""
        if not payload:
            return None
        cipher = AESCipher(self.key)
        if self.version == 3.4:
            try:
                payload = cipher.decrypt(payload, False, decode_text=False)
            except Exception as err:
                raise TuyaKeyError(f"3.4 payload decrypt failed: {err}") from err

        if payload.startswith(H.PROTOCOL_VERSION_BYTES_31):
            payload = payload[len(H.PROTOCOL_VERSION_BYTES_31) :]
            try:
                payload = cipher.decrypt(payload[16:], decode_text=False)
            except Exception as err:
                raise TuyaKeyError(f"3.1 payload decrypt failed: {err}") from err
        elif self.version >= 3.2:
            if payload.startswith(self.version_bytes) or (self.device22 and (len(payload) & 0x0F) != 0):
                payload = payload[len(self.version_header) :]
            if self.version < 3.4:
                try:
                    payload = cipher.decrypt(payload, False, decode_text=False)
                except Exception as err:
                    raise TuyaKeyError(f"3.x payload decrypt failed: {err}") from err
        elif not payload.startswith(b"{"):
            raise TuyaDecodeError(f"unexpected payload {payload!r}")

        if b"data unvalid" in payload:
            # Device needs the "device22" query style (CONTROL_NEW with dps list).
            self.device22 = True
            raise TuyaDeviceError("data unvalid")

        try:
            text = payload.decode()
        except UnicodeDecodeError as err:
            raise TuyaKeyError(f"payload is not UTF-8 (wrong key?): {payload[:16]!r}") from err
        try:
            decoded = json.loads(text)
        except json.JSONDecodeError as err:
            raise TuyaDecodeError(f"invalid JSON {text[:80]!r}") from err
        if isinstance(decoded, dict):
            data = decoded.get("data")
            if "dps" not in decoded and isinstance(data, dict) and "dps" in data:
                decoded["dps"] = data["dps"]
                if "cid" in data and "cid" not in decoded:
                    decoded["cid"] = data["cid"]
            return decoded
        return None

    # ------------------------------------------------------------------ exchange

    async def _write(self, data: bytes) -> None:
        if not self.connected or self.transport is None:
            raise TuyaConnectionError("not connected")
        async with self._write_lock:
            self.transport.write(data)
            await asyncio.sleep(0.05)  # devices choke on back-to-back frames

    async def _send_and_wait(
        self, msg: MessagePayload, wait_key: int | None, timeout: float = REPLY_TIMEOUT
    ) -> TuyaMessage | None:
        """Send one frame and wait for the matching reply (or none)."""
        seqno, data = self._encode(msg)
        if wait_key is None:
            await self._write(data)
            return None
        key = seqno if wait_key == 0 else wait_key
        loop = asyncio.get_running_loop()
        fut: asyncio.Future[TuyaMessage | None] = loop.create_future()
        self._pending[key] = _Pending(msg.cmd, fut)
        try:
            await self._write(data)
            return await asyncio.wait_for(fut, timeout)
        except TimeoutError as err:
            raise TuyaTimeoutError(f"no reply to cmd {msg.cmd} in {timeout}s") from err
        finally:
            self._pending.pop(key, None)

    async def exchange(self, command: int, data: Any = None, *, wait: bool = True) -> dict[str, Any] | None:
        """Send `command` and return the decoded reply (``None`` for plain ACKs)."""
        if self.version >= 3.4 and not self.session_ready:
            await self.negotiate_session_key()
        async with self._exchange_lock:
            payload = self._build_payload(command, data)
            if command == CT.HEART_BEAT:
                wait_key: int | None = _SEQ_HEARTBEAT
            elif command == CT.UPDATEDPS:
                wait_key = None
            else:
                wait_key = 0 if wait else None
            msg = await self._send_and_wait(payload, wait_key)
        if msg is None:
            return None
        if msg.retcode not in (0, None):
            _LOGGER.debug("%s: cmd %s retcode %s payload=%r", self.device_id, command, msg.retcode, msg.payload[:64])
            if not msg.payload:
                raise TuyaDeviceError(f"device rejected cmd {command} (retcode {msg.retcode})")
        if not msg.payload:
            return None
        try:
            return self._decode_payload(msg.payload)
        except TuyaDeviceError:
            if command == CT.DP_QUERY and self.version < 3.4:
                # Retry once with the device22 query style.
                async with self._exchange_lock:
                    payload = self._build_payload(command, data)
                    msg = await self._send_and_wait(payload, 0)
                if msg is None or not msg.payload:
                    return None
                return self._decode_payload(msg.payload)
            raise

    # ------------------------------------------------------------------ session key

    async def negotiate_session_key(self) -> None:
        """Perform the 3.4/3.5 three-way handshake. Raises TuyaKeyError on mismatch."""
        if self.version < 3.4:
            self.session_ready = True
            return
        async with self._exchange_lock:
            self.key = self.real_key
            self.session_ready = False
            local_nonce = os.urandom(16)
            step1 = MessagePayload(CT.SESS_KEY_NEG_START, local_nonce)
            try:
                resp = await self._send_and_wait(step1, _SEQ_SESSION)
            except TuyaTimeoutError as err:
                raise TuyaKeyError("no session-key response (wrong key, version or busy device)") from err
            except TuyaConnectionError as err:
                raise TuyaKeyError(f"device closed the connection during the handshake ({err})") from err
            if resp is None or resp.cmd != CT.SESS_KEY_NEG_RESP or (resp.retcode not in (0, None) and not resp.payload):
                raise TuyaKeyError("device rejected the session-key request (wrong key or version)")
            payload = resp.payload
            if self.version == 3.4:
                try:
                    payload = AESCipher(self.real_key).decrypt(payload, False, decode_text=False)
                except Exception as err:
                    raise TuyaKeyError(f"session-key decrypt failed: {err}") from err
            if len(payload) < 48:
                raise TuyaKeyError("session-key response too short")
            remote_nonce = payload[:16]
            expected = hmac.new(self.real_key, local_nonce, sha256).digest()
            if not hmac.compare_digest(expected, payload[16:48]):
                raise TuyaKeyError("session-key HMAC mismatch: local key is wrong")
            finish = MessagePayload(CT.SESS_KEY_NEG_FINISH, hmac.new(self.real_key, remote_nonce, sha256).digest())
            await self._send_and_wait(finish, None)
            xored = bytes(a ^ b for a, b in zip(local_nonce, remote_nonce, strict=True))
            cipher = AESCipher(self.real_key)
            if self.version == 3.4:
                self.key = cipher.encrypt(xored, False, pad=False)
            else:
                self.key = cipher.encrypt(xored, use_base64=False, pad=False, iv=local_nonce[:12])[12:28]
            self.session_ready = True
            _LOGGER.debug("%s: session key negotiated", self.device_id)

    # ------------------------------------------------------------------ high level

    async def status(self) -> dict[str, Any]:
        """Query all DPs. Returns the dps dict (may be empty for IR blasters)."""
        decoded = await self.exchange(CT.DP_QUERY)
        if decoded is None:
            return {}
        dps = decoded.get("dps")
        if not isinstance(dps, dict):
            return {}
        return dps

    async def set_dps(self, dps: dict[str, Any]) -> dict[str, Any] | None:
        """Write one or more DPs."""
        return await self.exchange(CT.CONTROL, dps)

    async def update_dps(self, dp_ids: list[int] | None = None) -> None:
        """Ask the device to refresh (push) the given DPs (3.2+)."""
        if self.version < 3.2:
            return
        await self.exchange(CT.UPDATEDPS, dp_ids, wait=False)

    async def heartbeat(self) -> None:
        """Send a heartbeat and wait for the echo."""
        await self.exchange(CT.HEART_BEAT)

    def start_heartbeat(self) -> None:
        """Start the keep-alive loop."""
        if self._heartbeat_task is None:
            self._heartbeat_task = asyncio.get_running_loop().create_task(self._heartbeat_loop())

    async def _heartbeat_loop(self) -> None:
        misses = 0
        try:
            while not self._closed:
                await asyncio.sleep(HEARTBEAT_INTERVAL)
                if not self.connected:
                    return
                try:
                    await self.heartbeat()
                    misses = 0
                except TuyaTimeoutError:
                    misses += 1
                    if misses >= HEARTBEAT_MISSES_BEFORE_CLOSE:
                        _LOGGER.debug("%s: heartbeat lost, closing", self.device_id)
                        if self.transport:
                            self.transport.close()
                        return
                except TuyaConnectionError:
                    return
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001
            _LOGGER.exception("%s: heartbeat loop crashed", self.device_id)
            if self.transport:
                self.transport.close()


async def connect(
    host: str,
    device_id: str,
    local_key: str,
    version: float,
    listener: TuyaListener,
    *,
    port: int = TCP_PORT,
    node_id: str | None = None,
    dps_to_request: list[int] | None = None,
    timeout: float = CONNECT_TIMEOUT,
) -> TuyaLanProtocol:
    """Open a TCP connection and (for 3.4/3.5) negotiate the session key."""
    loop = asyncio.get_running_loop()
    factory: Callable[[], TuyaLanProtocol] = lambda: TuyaLanProtocol(  # noqa: E731
        device_id, local_key, version, listener, node_id=node_id, dps_to_request=dps_to_request
    )
    try:
        async with asyncio.timeout(timeout):
            _, protocol = await loop.create_connection(factory, host, port)
    except TimeoutError as err:
        raise TuyaConnectionError(f"connect to {host}:{port} timed out") from err
    except OSError as err:
        raise TuyaConnectionError(f"connect to {host}:{port} failed: {err}") from err
    try:
        await protocol.negotiate_session_key()
    except TuyaLocalError:
        await protocol.close()
        raise
    return protocol


def udp_key() -> bytes:
    """The well-known key used for encrypted discovery broadcasts."""
    return md5(b"yGAdlopoPVldABfn").digest()


def hexlify(data: bytes) -> str:
    """Debug helper."""
    return binascii.hexlify(data).decode()
