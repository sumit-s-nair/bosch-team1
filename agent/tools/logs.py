"""OpenSearch logs (read-only). Index pattern otel-logs-*."""
from agent.config import opensearch_url
from agent.tools import http


def search_logs(service: str | None = None, severity: str | None = None,
                text: str | None = None, minutes: int = 15, size: int = 30) -> list:
    must = [{"range": {"@timestamp": {"gte": f"now-{minutes}m"}}}]
    if service:
        must.append({"term": {"resource.service.name": service}})
    if severity:
        must.append({"match": {"severityText": severity}})
    if text:
        must.append({"match": {"body": text}})
    q = {"size": size, "sort": [{"@timestamp": "desc"}], "query": {"bool": {"must": must}}}
    res = http.post(f"{opensearch_url()}/otel-logs-*/_search", q)
    return [h["_source"] for h in res["hits"]["hits"]]
