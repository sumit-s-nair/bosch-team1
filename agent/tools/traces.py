"""Jaeger (read-only), via the frontend-proxy at :8080/jaeger/ui/api."""
from agent.config import JAEGER_URL
from agent.tools import http


def services() -> list:
    return http.get(f"{JAEGER_URL}/api/services").get("data") or []


def find_traces(service: str, lookback: str = "15m", limit: int = 20, error_only: bool = False) -> list:
    params = {"service": service, "lookback": lookback, "limit": limit}
    if error_only:
        params["tags"] = '{"error":"true"}'
    return http.get(f"{JAEGER_URL}/api/traces", params).get("data") or []


def get_trace(trace_id: str) -> dict:
    data = http.get(f"{JAEGER_URL}/api/traces/{trace_id}").get("data") or []
    return data[0] if data else {}
