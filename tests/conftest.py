"""Test-wide guard: no test may open a network socket.

Every apipulse slice states "no network access in tests". httpx.MockTransport,
sqlite3 and pyarrow never touch sockets, so any attempt here is a bug in the test.
"""

import socket

import pytest


class _NetworkBlocked(RuntimeError):
    pass


@pytest.fixture(autouse=True)
def _no_network(monkeypatch):
    def _refuse(*args, **kwargs):
        raise _NetworkBlocked("network access is disabled in tests")

    monkeypatch.setattr(socket.socket, "connect", _refuse)
    monkeypatch.setattr(socket.socket, "connect_ex", _refuse)
    monkeypatch.setattr(socket, "create_connection", _refuse)
