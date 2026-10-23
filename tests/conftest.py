"""No test may accidentally reach a real telemetry or model service."""
import pytest


@pytest.fixture(autouse=True)
def block_network(monkeypatch):
    import socket
    def fail(*args, **kwargs):
        raise AssertionError('Network access is forbidden in offline tests')
    monkeypatch.setattr(socket.socket, 'connect', fail)
