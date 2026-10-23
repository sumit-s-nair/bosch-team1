"""Every known route by which feature-flag state could reach the model must be blocked."""
import pytest

from agent import flagguard, observe
from agent.evidence import Ledger
from agent.tools import http, logs, traces


def test_flag_urls_refused():
    for url in ("http://localhost:8080/feature/", "http://flagd:8013/", "http://localhost:8016/ofrep/v1"):
        with pytest.raises(http.ForbiddenSource):
            http.get(url)


def test_flagd_spans_and_flag_messages_hidden_from_traces(monkeypatch):
    spans = [
        {"trace_id": "t1", "span_id": "a", "parent_id": None, "service": "checkout", "name": "PlaceOrder",
         "kind": "2", "error": True, "message": "", "http_status": None, "rpc_status": None, "duration_ms": 5, "start_ns": 0},
        {"trace_id": "t1", "span_id": "b", "parent_id": "a", "service": "checkout",
         "name": "flagd.evaluation.v1.Service/ResolveBoolean", "kind": "3", "error": True,
         "message": "paymentFailure", "http_status": None, "rpc_status": None, "duration_ms": 1, "start_ns": 0},
        {"trace_id": "t1", "span_id": "c", "parent_id": "a", "service": "flagd", "name": "resolve",
         "kind": "2", "error": True, "message": "flag paymentFailure=on", "http_status": None, "rpc_status": None,
         "duration_ms": 1, "start_ns": 0},
        {"trace_id": "t1", "span_id": "d", "parent_id": "a", "service": "payment", "name": "charge",
         "kind": "2", "error": True, "message": "feature flag enabled: fail", "http_status": None,
         "rpc_status": None, "duration_ms": 1, "start_ns": 0},
    ]
    monkeypatch.setattr(traces, "find_spans", lambda *a, **k: list(spans))
    out = observe.trace_errors(Ledger(), "checkout")
    assert "paymentFailure" not in out and "ResolveBoolean" not in out and "flagd" not in out.lower()
    assert "payment / charge" in out and flagguard.REDACTED in out


def test_flag_logs_and_flag_ui_access_logs_hidden(monkeypatch):
    def fake(service=None, min_severity=None, **k):
        if service == "frontend-proxy":
            return [{"id": "1", "service": "frontend-proxy", "severity": None,
                     "body": '[t] "POST /feature/api/write-to-file HTTP/1.1" 500 - x'},
                    {"id": "2", "service": "frontend-proxy", "severity": None,
                     "body": '[t] "POST /api/checkout HTTP/1.1" 503 UF upstream_reset'}]
        return [{"id": "3", "service": "product-catalog", "severity": "ERROR",
                 "body": "productCatalogFailure feature flag is enabled"},
                {"id": "4", "service": "flagd", "severity": "ERROR", "body": "flag changed"}]
    monkeypatch.setattr(logs, "search_logs", fake)
    out = observe.error_logs(Ledger())
    assert "/feature" not in out and "productCatalogFailure" not in out and "flag changed" not in out
    assert "/api/checkout -> HTTP 503 (envoy flags UF)" in out


def test_flagd_excluded_from_log_queries():
    assert "flagd" in logs.NOISE_SERVICES and "flagd-ui" in logs.NOISE_SERVICES
