"""No test may reach a real telemetry or model service, including the live demo on localhost.
The only exception: ports a test registers in ALLOWED_PORTS (the Action API tests' own server)."""
import socket

import pytest

ALLOWED_PORTS: set[int] = set()


@pytest.fixture(autouse=True)
def block_network(monkeypatch):
    real_connect = socket.socket.connect

    def guarded(self, address, *args, **kwargs):
        if isinstance(address, tuple) and address[0] in ("127.0.0.1", "::1") and address[1] in ALLOWED_PORTS:
            return real_connect(self, address, *args, **kwargs)
        raise AssertionError(f"Network access is forbidden in offline tests ({address})")
    monkeypatch.setattr(socket.socket, "connect", guarded)
    yield
    ALLOWED_PORTS.clear()
