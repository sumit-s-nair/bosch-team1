"""Prometheus (read-only)."""
from agent.config import PROMETHEUS_URL
from agent.tools import http


def promql(query: str) -> list:
    """Instant PromQL query. Returns [{'metric': {...}, 'value': float}]."""
    data = http.get(f"{PROMETHEUS_URL}/api/v1/query", {"query": query})
    return [{"metric": r["metric"], "value": float(r["value"][1])}
            for r in data["data"]["result"]]


def metric_names(match: str = "") -> list:
    names = http.get(f"{PROMETHEUS_URL}/api/v1/label/__name__/values")["data"]
    return [n for n in names if match in n]
