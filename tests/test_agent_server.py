"""Agent server (the dashboard's backend): the browser answers the agent's questions, the gate stays in the agent.
Offline: recorded scenarios, scripted model, faked Action API. No Docker, LLM or network."""
import json
import os
import socket
import threading
import time
import types

import pytest
import requests

from agent import llm, pipeline, server
from eval import offline, run_offline, scenarios


@pytest.fixture(autouse=True)
def isolate(monkeypatch, tmp_path):
    from agent.tools import containers, logs, metrics, traces
    for mod, attrs in ((metrics, ("promql", "metric_names")), (containers, ("list_containers", "inspect", "stats")),
                       (traces, ("_api", "services", "find_spans", "get_trace")), (logs, ("search_logs", "fields"))):
        for a in attrs:
            monkeypatch.setattr(mod, a, getattr(mod, a))
    monkeypatch.chdir(tmp_path)          # runs/ lives in a temp dir


def model_says(monkeypatch, service, category, conf=0.9):
    def chat(messages, tools=None, think=True, json_mode=False, timeout=0):
        if not json_mode:
            return {"role": "assistant", "content": "enough"}
        text = "\n".join(m["content"] for m in messages if isinstance(m.get("content"), str))
        ids = [l.split("[E")[1].split("]")[0] for l in text.splitlines() if service in l and "[E" in l]
        return {"role": "assistant", "content": json.dumps({
            "summary": f"{service} broken", "root_cause": {"service": service, "category": category, "description": "x"},
            "confidence": conf, "evidence": [{"id": f"E{i}", "why": "w"} for i in ids[:3]]})}
    monkeypatch.setattr(llm, "chat", chat)


def offline_agent(name):
    """An Agent whose runs execute the real pipeline on a recorded scenario with the simulated Action API."""
    sc = scenarios.get(name)
    offline.install(sc)

    def heal(p):
        for action, t in sc["expected"].get("fixes", []):
            if p["action"] == action and t in ("*", p["params"].get("service") or "*"):
                offline.install(scenarios.get("healthy"))

    def runner(mode, ask_fn, incident):
        return pipeline.run(mode=mode, ask_fn=ask_fn, incident=incident, on_executed=heal,
                            client_factory=lambda inc: run_offline.SimulatedActionClient(inc, sc),
                            validate_kw={"settle_s": 0, "polls": 1, "poll_s": 0})
    return server.Agent(root="runs", runner=runner)


def browser(agent, answers: dict, seen: list, timeout=30):
    """Play the dashboard: poll /api/pending and answer each question by its kind."""
    t0 = time.time()
    while time.time() - t0 < timeout:
        code, body = server.handle(agent, "GET", "/api/pending", {})
        q = body["pending"]
        if q:
            seen.append(q["kind"])
            assert server.handle(agent, "POST", "/api/answer", {"id": q["id"], "answer": answers[q["kind"]]})[0] == 200
        elif not agent.running():
            return
        time.sleep(0.02)
    raise AssertionError("run did not finish")


def report(agent):
    return json.load(open(f"runs/{agent.run_id}/report.json"))


def test_browser_approves_and_agent_fixes(monkeypatch):
    model_says(monkeypatch, "payment", "container_down")
    agent, seen = offline_agent("container_stopped"), []
    code, body = server.handle(agent, "POST", "/api/run", {"mode": "now"})
    assert code == 202 and body["run_id"] == agent.run_id
    browser(agent, {"approve": "yes", "name": "ruthu"}, seen)
    rep = report(agent)
    assert seen == ["approve", "name"]                       # the same questions the terminal would ask
    assert rep["outcome"] == "fixed and verified" and rep["approval"]["by"] == "ruthu"
    assert os.path.exists(f"runs/{agent.run_id}/approval.json")    # the agent's own approval record
    inc = server.handle(agent, "GET", f"/api/incidents/{agent.run_id}", {})[1]
    assert inc["status"] == "Resolved" and inc["plan"]["dry_run_preview"] and inc["diagnosis"]["evidence"]


def test_browser_reject_means_nothing_executes(monkeypatch):
    model_says(monkeypatch, "payment", "container_down")
    agent, seen = offline_agent("container_stopped"), []
    server.handle(agent, "POST", "/api/run", {"mode": "now"})
    browser(agent, {"approve": "no: wrong service"}, seen)
    rep = report(agent)
    assert seen == ["approve"] and rep["action_result"] is None
    assert not os.path.exists(f"runs/{agent.run_id}/actions.jsonl")
    assert server.handle(agent, "GET", "/api/incidents", {})[1]["incidents"][0]["status"] == "Rejected"


def test_feature_off_is_approved_from_the_browser_without_naming_a_flag(monkeypatch):
    model_says(monkeypatch, "product-catalog", "service_error")
    agent, seen = offline_agent("product_catalog_one_product"), []
    server.handle(agent, "POST", "/api/run", {"mode": "now"})
    browser(agent, {"approve": "yes", "name": "ruthu"}, seen)
    assert seen == ["approve", "name"]                          # the Action API picks the flags; nobody types one
    assert report(agent)["action"]["params"] == {"service": "product-catalog", "state": "off"}


def test_low_confidence_escalation_is_acknowledged_not_approved(monkeypatch):
    model_says(monkeypatch, "payment", "container_down", conf=0.4)
    agent, seen = offline_agent("container_stopped"), []
    server.handle(agent, "POST", "/api/run", {"mode": "now"})
    browser(agent, {"acknowledge": ""}, seen)
    assert seen == ["acknowledge"] and report(agent)["outcome"] == "escalated to human"


def test_one_run_at_a_time_and_stale_answers_ignored():
    release = threading.Event()
    agent = server.Agent(root="runs", runner=lambda mode, ask_fn, incident: (ask_fn("Approve this action? type 'yes'"), release.wait(5)))
    assert server.handle(agent, "POST", "/api/run", {"mode": "now"})[0] == 202
    assert server.handle(agent, "POST", "/api/run", {"mode": "now"})[0] == 409
    while not agent.pending:
        time.sleep(0.01)
    assert server.handle(agent, "POST", "/api/answer", {"id": "not-it", "answer": "yes"})[0] == 409
    assert agent.pending is not None                          # the wrong id did not release the agent
    assert server.handle(agent, "POST", "/api/answer", {"id": agent.pending["id"], "answer": "no"})[0] == 200
    release.set()
    agent.thread.join(5)
    assert server.handle(agent, "POST", "/api/run", {"mode": "bogus"})[0] == 400


def test_incident_paths_are_validated():
    agent = server.Agent(root="runs")
    os.makedirs("runs/20260101-000000")
    assert server.handle(agent, "GET", "/api/incidents/..", {})[0] == 404
    assert server.handle(agent, "GET", "/api/incidents/20260101-000000", {})[1]["status"] == "Interrupted"


def test_kill_switch_is_proxied_with_the_token_server_side(monkeypatch):
    agent = server.Agent(root="runs")
    code, body = server.handle(agent, "GET", "/api/kill", {})
    assert code == 503 and body["kill_switch"] is None         # no Action API: say so, never fake a state
    monkeypatch.setenv("ACTION_API_URL", "http://action-api.test")
    monkeypatch.setenv("ACTION_API_TOKEN", "secret-token")
    calls = []

    def fake(method, url, json=None, timeout=0, headers=None):
        calls.append((method, url, json, headers))
        return types.SimpleNamespace(status_code=200, json=lambda: {"kill_switch": bool((json or {}).get("on"))})
    monkeypatch.setattr(requests, "request", fake)
    code, body = server.handle(agent, "POST", "/api/kill", {"on": True, "by": "ruthu"})
    assert code == 200 and body == {"kill_switch": True}
    assert calls[-1][:3] == ("POST", "http://action-api.test/kill", {"on": True, "by": "ruthu"})
    assert calls[-1][3]["Authorization"] == "Bearer secret-token" and "secret-token" not in json.dumps(body)


def test_cors_only_for_the_local_dashboard():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
    from conftest import ALLOWED_PORTS
    ALLOWED_PORTS.add(port)                    # only this test server is reachable
    srv, _ = server.serve(port=port, agent=server.Agent(root="runs"))
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    try:
        ok = requests.options(f"http://127.0.0.1:{port}/api/answer", headers={"Origin": "http://localhost:3000"})
        bad = requests.get(f"http://127.0.0.1:{port}/api/health", headers={"Origin": "http://evil.example"})
        assert ok.status_code == 204 and ok.headers["Access-Control-Allow-Origin"] == "http://localhost:3000"
        assert bad.status_code == 200 and "Access-Control-Allow-Origin" not in bad.headers
    finally:
        srv.shutdown()


def test_static_ui_is_served_and_confined():
    from agent import server
    hit = server.static_file("/")
    assert hit and hit[0].startswith("text/html") and b"Incident Zero" in hit[1]
    assert server.static_file("/style.css")[0].startswith("text/css")
    assert server.static_file("/app.js")[0].startswith("text/javascript")
    for bad in ("/../../.env", "/..%2f..%2f.env", "/../main.js", "/nope.html", "/../package.json"):
        assert server.static_file(bad) is None


def test_conversation_records_model_tools_and_human(monkeypatch):
    model_says(monkeypatch, "payment", "container_down")
    agent, seen = offline_agent("container_stopped"), []
    server.handle(agent, "POST", "/api/run", {"mode": "now"})
    browser(agent, {"approve": "yes", "name": "ruthu"}, seen)
    code, body = server.handle(agent, "GET", f"/api/incidents/{agent.run_id}/conversation", {})
    kinds = [c["kind"] for c in body["conversation"]]
    assert code == 200 and body["timeline"]
    for k in ("sweep", "prompt", "diagnosis", "validation", "question", "answer"):
        assert k in kinds, k
    answers = [c["text"] for c in body["conversation"] if c["kind"] == "answer"]
    assert answers == ["yes", "ruthu"]
    assert server.handle(agent, "GET", "/api/incidents/../x/conversation", {})[0] == 404
    assert "flagd" not in json.dumps(body["conversation"]).lower()     # the model's view is flag-free, so is this


def test_live_view_reports_errors_instead_of_failing(monkeypatch):
    from agent import detector
    from agent.tools import containers, observe
    monkeypatch.setattr(server, "_live", {"at": 0.0, "data": None})
    monkeypatch.setattr(detector, "check", lambda *a, **k: [{"type": "error_rate", "service": "cart", "value": 0.5, "baseline": 0}])
    monkeypatch.setattr(observe, "service_health_raw", lambda *a, **k: {"cart": {"rps": 1.0, "err_rate": 0.5}})
    def boom():
        raise RuntimeError("docker down")
    monkeypatch.setattr(containers, "list_containers", boom)
    code, body = server.handle(server.Agent(root="runs"), "GET", "/api/live", {})
    assert code == 200 and body["alerts"][0]["label"] == "error_rate:cart" and body["services"]["cart"]["rps"] == 1.0
    assert body["containers"] == [] and "docker" in body["errors"][0]
