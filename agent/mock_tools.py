"""Synthetic fixtures only; never used by live tools."""
import os

def empty(): return os.getenv("MOCK_MODE") == "empty"
def query_metrics(service, window="5m"):
    return {} if empty() else {"service": service, "window": window, "request_rate": 20, "error_rate": 12, "p95_ms": 950, "baseline": {"request_rate": 20, "error_rate": 0, "p95_ms": 80}}
def query_traces(service, lookback="15m", errors_only=True, limit=10):
    return [] if empty() else [{"trace_id": "mock-trace-001", "service": "payment", "operation": "Charge", "duration_ms": 950, "error_tags": {"error": True, "http.status_code": 500}, "message": "payment provider connection refused"}]
def query_logs(service, level="ERROR", contains="", minutes=15, limit=15):
    return [] if empty() else ["2026-01-01T00:00:00Z payment ERROR payment provider connection refused"]
def container_health(): return {} if empty() else {"containers": [{"name": "payment", "status": "running", "restart_count": 0}]}
def deploy_history(): return {"events": [], "note": "no deploy history source"}
