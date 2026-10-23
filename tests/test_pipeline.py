"""Offline tests of the investigation pipeline. Backends and the model are faked; network is blocked (conftest)."""
import json

import pytest

from agent import brain, detector, llm, observe
from agent.evidence import Ledger
from agent.schema import validate_output
from agent.tools import containers, http, metrics, traces


# ---------------------------------------------------------------- fakes
def otlp(service, spans):
    return {"resource": {"attributes": [{"key": "service.name", "value": {"stringValue": service}}]},
            "scopeSpans": [{"spans": spans}]}


def span(sid, name, parent=None, error=False, msg="", dur_ms=1):
    s = {"traceId": "t1", "spanId": sid, "name": name, "kind": 2,
         "startTimeUnixNano": "0", "endTimeUnixNano": str(int(dur_ms * 1e6)),
         "status": {"code": "STATUS_CODE_ERROR" if error else "STATUS_CODE_UNSET", "message": msg}}
    if parent:
        s["parentSpanId"] = parent
    return s


PAYMENT_FAILURE = {"result": {"resourceSpans": [
    otlp("checkout", [span("a", "PlaceOrder", error=True, dur_ms=5),
                      span("b", "oteldemo.PaymentService/Charge", parent="a", error=True)]),
    otlp("payment", [span("c", "charge", parent="b", error=True, msg="Invalid token")]),
]}}


@pytest.fixture
def healthy(monkeypatch):
    def promql(q):
        if "STATUS_CODE_ERROR" in q:
            return []
        if "histogram_quantile" in q:
            return [{"metric": {"service_name": "checkout"}, "value": 40.0}]
        if "deriv(" in q:          # memory flat -> no growth, no leak
            return []
        if "container_" in q:
            return [{"metric": {"container_name": "checkout"}, "value": 5.0}]
        return [{"metric": {"service_name": "checkout"}, "value": 2.0}]
    monkeypatch.setattr(metrics, "promql", promql)
    monkeypatch.setattr(containers, "list_containers", lambda: [{"name": "checkout", "state": "running", "status": "Up 1 hour"}])
    monkeypatch.setattr(containers, "inspect", lambda n: {"running": True, "status": "running", "restart_count": 0,
                                                          "exit_code": 0, "oom_killed": False,
                                                          "started_at": "2026-01-01T00:00:00Z"})


def scripted_llm(monkeypatch, replies):
    it = iter(replies)
    monkeypatch.setattr(llm, "chat", lambda *a, **k: next(it))


def diagnosis(**kw):
    d = {"summary": "s", "root_cause": {"service": "payment", "category": "service_error", "description": "d"},
         "confidence": 0.9, "evidence": [{"id": "E1", "why": "w"}]}
    d.update(kw)
    return d


# ---------------------------------------------------------------- safety guards
@pytest.mark.parametrize("url", ["http://localhost:8080/feature/api", "http://flagd:8013/x", "http://localhost:8016/"])
def test_flag_sources_refused(url):
    with pytest.raises(http.ForbiddenSource):
        http.get(url)


def test_post_only_to_search():
    with pytest.raises(http.ForbiddenSource):
        http.post("http://localhost:9200/otel-logs-*/_delete_by_query", {})


def test_docker_is_read_only():
    with pytest.raises(ValueError):
        containers._docker("restart", "payment")


# ---------------------------------------------------------------- tools
def test_trace_origin_is_deepest_failing_span(monkeypatch):
    monkeypatch.setattr(traces, "_api", lambda: ("v3", "http://x"))
    monkeypatch.setattr(http, "get", lambda url, params=None: PAYMENT_FAILURE)
    L = Ledger()
    out = observe.trace_errors(L, "checkout")
    assert "origin payment / charge" in out and "Invalid token" in out
    assert L.get("E1")["anomalous"] and L.get("E1")["ref"] == "trace:t1"


def test_healthy_system_has_no_alerts(healthy):
    assert detector.check() == []


# ---------------------------------------------------------------- validation
def test_invented_evidence_dropped():
    L = Ledger()
    L.add("trace", "payment charge failing", ref="trace:t1", anomalous=True)
    d = brain.validate(diagnosis(evidence=[{"id": "E1", "why": "x"}, {"id": "E99", "why": "made up"}]), L)
    assert [e["id"] for e in d["evidence"]] == ["E1"]
    assert d["decision"] == "propose_fix"


def test_no_abnormal_evidence_caps_confidence():
    L = Ledger()
    L.add("metric", "payment rps=1 err=0%", ref="span_metrics:payment", anomalous=False)
    d = brain.validate(diagnosis(), L)
    assert d["confidence"] <= 0.3 and d["decision"] == "escalate"


def test_blamed_service_must_appear_in_evidence():
    L = Ledger()
    L.add("trace", "cart errors", ref="trace:t1", anomalous=True)
    d = brain.validate(diagnosis(), L)
    assert d["confidence"] <= 0.4 and d["decision"] == "escalate"


def test_schema():
    assert validate_output(diagnosis())[0]
    assert not validate_output(diagnosis(confidence=2))[0]
    assert not validate_output({})[0]


# ---------------------------------------------------------------- full loop
def test_healthy_manual_page_is_no_incident(healthy, monkeypatch):
    scripted_llm(monkeypatch, [
        {"role": "assistant", "content": "nothing to check"},
        {"role": "assistant", "content": json.dumps(diagnosis(
            root_cause={"service": "load-generator", "category": "resource_exhaustion", "description": "x"},
            confidence=0.8))},
    ])
    d = brain.investigate([{"type": "manual", "service": "unknown"}], log=lambda *a: None)
    assert d["decision"] == "no_incident"


def test_invalid_json_twice_escalates(healthy, monkeypatch):
    scripted_llm(monkeypatch, [{"role": "assistant", "content": ""},
                               {"role": "assistant", "content": "not json"},
                               {"role": "assistant", "content": "{\"still\": \"wrong\"}"}])
    d = brain.investigate([{"type": "error_rate", "service": "checkout"}], log=lambda *a: None)
    assert d["decision"] == "escalate" and d["confidence"] == 0


def test_llm_tool_calls_are_executed(healthy, monkeypatch):
    scripted_llm(monkeypatch, [
        {"role": "assistant", "content": "", "tool_calls": [{"function": {"name": "service_operations", "arguments": {"service": "checkout"}}}]},
        {"role": "assistant", "content": "done"},
        {"role": "assistant", "content": json.dumps(diagnosis())},
    ])
    d = brain.investigate([{"type": "error_rate", "service": "checkout"}], log=lambda *a: None)
    assert [t["tool"] for t in d["tool_trail"] if t["by"] == "llm"] == ["service_operations"]
