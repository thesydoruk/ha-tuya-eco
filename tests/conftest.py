"""Shared fixtures and hooks."""

from __future__ import annotations

import sys

import pytest
import pytest_socket

# The Home Assistant test plugin blocks socket creation for every test. Two cases
# need real sockets: (1) the protocol/device tests talk to a fake device over TCP
# loopback (marked `enable_socket`), and (2) on Windows the asyncio event loop
# itself needs a TCP socketpair. We wrap `disable_socket` so those cases keep
# working while every other test stays sandboxed.
_ORIGINAL_DISABLE = pytest_socket.disable_socket
_ALLOW_SOCKETS = False


def _disable_socket(allow_unix_socket: bool = False) -> None:
    if _ALLOW_SOCKETS or sys.platform == "win32":
        return
    _ORIGINAL_DISABLE(allow_unix_socket)


pytest_socket.disable_socket = _disable_socket


@pytest.hookimpl(tryfirst=True)
def pytest_runtest_setup(item: pytest.Item) -> None:
    """Remember whether the test asked for real sockets."""
    global _ALLOW_SOCKETS  # noqa: PLW0603
    _ALLOW_SOCKETS = item.get_closest_marker("enable_socket") is not None
    if _ALLOW_SOCKETS:
        pytest_socket.enable_socket()


@pytest.fixture(autouse=True)
def auto_enable_custom_integrations(enable_custom_integrations):  # noqa: PT004
    """Enable custom integrations for all tests."""
    return
