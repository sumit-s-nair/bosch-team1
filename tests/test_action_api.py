"""The Action API is the only thing allowed to change the demo. These tests run the real HTTP server
(on a free port) with Docker, the load generator and the flag service faked."""
import copy
import json
import pathlib
import threading
import types

import pytest
import requests

from action_api import executors as ex, server

TOKEN = "test-token"
FLAGS = {"cartFailure": {"state": "ENABLED", "variants": {"on": True, "off": False}, "defaultVariant": "on"},
         "catalogBug": {"state": "ENABLED", "variants": {"on": True, "off": False}, "defaultVariant": "off",
                        "targeting": {"if": [{"in": ["X1", {"var": "product_id"}]}, "on", "off"]}}}


@pytest.fixture
def fake(monkeypatch):
    """Fake docker + demo HTTP controls; records every change."""
    world = {"containers": {"cart": "running", "payment": "exited", "jaeger": "running", "flagd": "running"},
             "restarted": [], "users": 10, "flags": copy.deepcopy(FLAGS), "writes": 0}

    def docker(*args, timeout=60):
        if args[0] == "ps":
            return "\n".join(json.dumps({"Names": n, "State": s}) for n, s in world["containers"].items())
        if args[0] == "restart":
            world["restarted"].append(args[-1]); world["containers"][args[-1]] = "running"; return args[-1]
        raise AssertionError(f"unexpected docker {args}")
    monkeypatch.setattr(ex, "_docker", docker)

    def resp(obj, code=200):
        return types.SimpleNamespace(ok=code < 400, status_code=code, json=lambda: obj)

    def get(url):
        if url.endswith("/loadgen/stats/requests"):
            return resp({"user_count": world["users"]})
        if url.endswith("/feature/api/read"):
            return resp({"flags": copy.deepcopy(world["flags"])})
        raise AssertionError(url)

    def post(url, data=None, json=None):
        if url.endswith("/loadgen/swarm"):
            world["users"] = data["user_count"]; return resp({"success": True})
        if url.endswith("/feature/api/write"):
            world["flags"] = json["data"]["flags"]; world["writes"] += 1; return resp({})
        raise AssertionError(url)
    monkeypatch.setattr(ex, "_http_get", get)
    monkeypatch.setattr(ex, "_http_post", post)
    return world


@pytest.fixture
def api(tmp_path, monkeypatch, fake):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(server, "TARGET_COOLDOWN_S", 0)
    srv, st = server.serve(port=0, token=TOKEN, audit_path=str(tmp_path / "audit.jsonl"))
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    from conftest import ALLOWED_PORTS
    ALLOWED_PORTS.add(srv.server_address[1])          # only this test server is reachable, never the live demo
    base = f"http://127.0.0.1:{srv.server_address[1]}"
    real_post, real_get = requests.post, requests.get
    h = {"Authorization": f"Bearer {TOKEN}"}

    def call(method, path, body=None, auth=True):
        f = real_post if method == "POST" else real_get
        r = f(base + path, json=body, headers=h if auth else {}, timeout=10)
        return r.status_code, r.json()
    yield types.SimpleNamespace(call=call, st=st, base=base, audit=lambda: [json.loads(l) for l in open(st.audit_path)])
    srv.shutdown()


APPROVED = {"approved_by": "sumit", "incident_id": "inc-1"}


def test_health_open_everything_else_needs_token(api):
    assert api.call("GET", "/health", auth=False)[0] == 200
    assert api.call("GET", "/actions", auth=False)[0] == 401


def test_allowlist(api):
    code, body = api.call("GET", "/actions")
    assert code == 200 and set(body["actions"]) == {"restart_service", "throttle_load_generator", "toggle_feature_flag", "scale_service"}


def test_unknown_action_refused_and_audited(api, fake):
    code, body = api.call("POST", "/actions/execute", {"action": "docker_rm", "params": {"service": "cart"}, "approval": APPROVED})
    assert code == 403 and body["status"] == "refused"
    assert api.audit()[-1]["status"] == "refused" and fake["restarted"] == []


def test_dry_run_changes_nothing(api, fake):
    code, body = api.call("POST", "/actions/dry-run", {"action": "restart_service", "params": {"service": "payment"}})
    assert code == 200 and body["status"] == "dry_run" and fake["restarted"] == []


@pytest.mark.parametrize("service,why", [("jaeger", "protected"), ("flagd", "protected"), ("nope", "no container"), ("../x", "invalid")])
def test_restart_validation(api, fake, service, why):
    code, body = api.call("POST", "/actions/execute", {"action": "restart_service", "params": {"service": service}, "approval": APPROVED})
    assert code == 400 and why in body["why"] and fake["restarted"] == []


def test_no_approval_no_change(api, fake):
    for appr in (None, {}, {"approved_by": "  "}):
        code, body = api.call("POST", "/actions/execute", {"action": "restart_service", "params": {"service": "payment"}, "approval": appr})
        assert code == 403 and "approval" in body["why"]
    assert fake["restarted"] == []


def test_restart_executes_and_starts_stopped_container(api, fake):
    code, body = api.call("POST", "/actions/execute", {"action": "restart_service", "params": {"service": "payment"},
                                                       "reason": "container_down", "approval": APPROVED})
    assert code == 200 and body["status"] == "executed" and fake["restarted"] == ["payment"]
    assert body["result"] == {"service": "payment", "state_before": "exited", "state_after": "running"}
    assert api.audit()[-1]["approval"]["approved_by"] == "sumit"


def test_kill_switch_endpoint_and_file(api, fake):
    api.call("POST", "/kill", {"on": True, "by": "sumit"})
    assert api.call("POST", "/actions/execute", {"action": "restart_service", "params": {"service": "cart"}, "approval": APPROVED})[0] == 423
    api.call("POST", "/kill", {"on": False, "by": "sumit"})
    pathlib.Path("STOP").touch()
    assert api.call("POST", "/actions/execute", {"action": "restart_service", "params": {"service": "cart"}, "approval": APPROVED})[0] == 423
    assert fake["restarted"] == []


def test_rate_limit(api, fake, monkeypatch):
    monkeypatch.setattr(server, "MAX_EXECUTES_PER_10_MIN", 2)
    codes = [api.call("POST", "/actions/execute", {"action": "restart_service", "params": {"service": "cart"}, "approval": APPROVED})[0]
             for _ in range(3)]
    assert codes == [200, 200, 429] and len(fake["restarted"]) == 2


def test_cooldown_on_same_target(api, fake, monkeypatch):
    monkeypatch.setattr(server, "TARGET_COOLDOWN_S", 60)
    a = api.call("POST", "/actions/execute", {"action": "restart_service", "params": {"service": "cart"}, "approval": APPROVED})[0]
    b = api.call("POST", "/actions/execute", {"action": "restart_service", "params": {"service": "cart"}, "approval": APPROVED})[0]
    assert (a, b) == (200, 429)


def test_throttle_and_undo(api, fake):
    code, body = api.call("POST", "/actions/execute", {"action": "throttle_load_generator", "params": {"users": 2}, "approval": APPROVED})
    assert code == 200 and fake["users"] == 2 and body["undo_available"]
    assert api.call("POST", "/actions/undo", {"call_id": body["call_id"], "approval": APPROVED})[0] == 200
    assert fake["users"] == 10


def test_flag_off_respects_targeting_and_never_returns_flag_contents(api, fake):
    code, body = api.call("POST", "/actions/execute", {"action": "toggle_feature_flag",
                                                       "params": {"flag": "catalogBug", "state": "off"}, "approval": APPROVED})
    assert code == 200 and fake["flags"]["catalogBug"]["targeting"]["if"][1] == "off"
    text = json.dumps(body)
    assert "variants" not in text and "targeting" not in text and "cartFailure" not in text
    assert body["result"] == {"flag": "catalogBug", "state": "off"}


def test_flag_undo_restores_previous_variant(api, fake):
    body = api.call("POST", "/actions/execute", {"action": "toggle_feature_flag", "params": {"flag": "cartFailure", "state": "off"},
                                                 "approval": APPROVED})[1]
    assert fake["flags"]["cartFailure"]["defaultVariant"] == "off"
    assert api.call("POST", "/actions/undo", {"call_id": body["call_id"], "approval": APPROVED})[0] == 200
    assert fake["flags"]["cartFailure"]["defaultVariant"] == "on"
    assert "_restore_variant" not in json.dumps(api.audit())       # the previous variant never leaves the API


def test_unknown_flag_does_not_list_flags(api, fake):
    code, body = api.call("POST", "/actions/execute", {"action": "toggle_feature_flag", "params": {"flag": "guess", "state": "off"},
                                                       "approval": APPROVED})
    assert code == 502 and "cartFailure" not in json.dumps(body) and fake["writes"] == 0


def test_internal_params_rejected(api, fake):
    code, _ = api.call("POST", "/actions/execute", {"action": "toggle_feature_flag",
                                                    "params": {"flag": "cartFailure", "state": "off", "_restore_variant": "on"},
                                                    "approval": APPROVED})
    assert code == 400 and fake["writes"] == 0


def test_scale_refused_on_compose(api):
    code, body = api.call("POST", "/actions/dry-run", {"action": "scale_service", "params": {"service": "cart", "replicas": 2}})
    assert code == 400 and "Kubernetes" in body["why"]


def test_agent_client_end_to_end(api, fake, tmp_path, monkeypatch):
    from agent import actions
    from agent.incident import Incident
    monkeypatch.setenv("ACTION_API_URL", api.base)
    monkeypatch.setenv("ACTION_API_TOKEN", TOKEN)
    c = actions.ActionClient(Incident(str(tmp_path / "runs")))
    plan = {"action": "throttle_load_generator", "params": {"users": 5}, "reason": "traffic_spike"}
    assert "dry run: dry_run" in c.preview(plan)
    r = c.execute(plan, approved_by="sumit")
    assert r["status"] == "executed" and fake["users"] == 5
    assert c.undo(r["call_id"], approved_by="sumit")["status"] == "executed" and fake["users"] == 10


def test_agent_package_never_imports_action_api():
    for f in pathlib.Path("agent").rglob("*.py") if pathlib.Path("agent").exists() else []:
        text = f.read_text(encoding="utf-8")
        assert "import action_api" not in text and "from action_api" not in text, f
