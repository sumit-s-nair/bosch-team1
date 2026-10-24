"""No test may reach a real telemetry or model service, including the live demo on localhost.
The only exception: ports a test registers in ALLOWED_PORTS (the Action API tests' own server)."""
import socket

import pytest

ALLOWED_PORTS: set[int] = set()


@pytest.fixture(autouse=True)
def block_network(monkeypatch, tmp_path_factory):
    real_connect = socket.socket.connect

    def guarded(self, address, *args, **kwargs):
        if isinstance(address, tuple) and address[0] in ("127.0.0.1", "::1") and address[1] in ALLOWED_PORTS:
            return real_connect(self, address, *args, **kwargs)
        raise AssertionError(f"Network access is forbidden in offline tests ({address})")
    monkeypatch.setattr(socket.socket, "connect", guarded)
    monkeypatch.setenv("ACTION_API_URL", "")      # never the real Action API from .env; tests that need one set it
    # a real <repo>/STOP must not change test results: point the repo-root kill file somewhere that never exists
    from agent import actions
    from action_api import server as action_server
    nowhere = str(tmp_path_factory.mktemp("no-stop") / "STOP")
    monkeypatch.setattr(actions, "KILL_FILE", nowhere)
    monkeypatch.setattr(action_server, "REPO_STOP", nowhere)
    yield
    ALLOWED_PORTS.clear()
