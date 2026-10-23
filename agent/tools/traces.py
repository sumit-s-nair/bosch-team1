"""Jaeger (read-only). Supports Jaeger v2's /api/v3 (OTLP JSON) and the legacy /api.
Everything is normalised to flat span dicts:
  {trace_id, span_id, parent_id, service, name, kind, error, message, duration_ms, start_ns}
"""
import time
from functools import lru_cache
from urllib.parse import quote

from agent import config, flagguard
from agent.tools import http

CANDIDATE_BASES = [config.jaeger_url(), "http://localhost:8080/jaeger/ui", "http://localhost:8080/jaeger"]


@lru_cache(maxsize=1)
def _api():
    """Find a working API flavour once. Returns (flavour, base)."""
    errors = []
    for base in dict.fromkeys(CANDIDATE_BASES):
        for flavour, path in (("v3", "/api/v3/services"), ("legacy", "/api/services")):
            try:
                http.get(base + path)
                return flavour, base
            except Exception as e:
                errors.append(f"{base}{path}: {e}")
    raise RuntimeError("No Jaeger API found. Tried: " + " | ".join(errors))


def services() -> list:
    flavour, base = _api()
    if flavour == "v3":
        return http.get(f"{base}/api/v3/services").get("services") or []
    return http.get(f"{base}/api/services").get("data") or []


# ---------- normalisation ----------
def _clean_attrs(attrs: dict) -> dict:
    """Span attributes for the tools, minus anything flag-related (keys like feature_flag.*, or values naming flags)."""
    return {k: flagguard.scrub(v) if isinstance(v, str) else v for k, v in attrs.items()
            if not flagguard.is_flag_text(k) and v is not None}


def _safe(spans: list) -> list:
    """Every span leaving this module: flagd spans and calls to flagd removed, messages scrubbed."""
    return [dict(s, message=flagguard.scrub(s["message"])) for s in spans if not flagguard.is_flag_span(s)]


def _attr_val(v: dict):
    for k in ("stringValue", "intValue", "boolValue", "doubleValue"):
        if k in v:
            return v[k]
    return None


def _otlp_spans(payload) -> list:
    results = payload if isinstance(payload, list) else [payload]
    spans = []
    for res in results:
        res = res.get("result", res)
        for rs in res.get("resourceSpans", []):
            rattrs = {a["key"]: _attr_val(a["value"]) for a in rs.get("resource", {}).get("attributes", [])}
            svc = rattrs.get("service.name", "?")
            for ss in rs.get("scopeSpans", []):
                for s in ss.get("spans", []):
                    st = s.get("status", {}) or {}
                    code = str(st.get("code", ""))
                    attrs = {a["key"]: _attr_val(a["value"]) for a in s.get("attributes", [])}
                    msg = st.get("message") or ""
                    for ev in s.get("events", []) or []:
                        if ev.get("name") == "exception":
                            ea = {a["key"]: _attr_val(a["value"]) for a in ev.get("attributes", [])}
                            msg = msg or ea.get("exception.message", "")
                    spans.append({
                        "trace_id": s.get("traceId"), "span_id": s.get("spanId"),
                        "parent_id": s.get("parentSpanId") or None, "service": svc,
                        "name": s.get("name"), "kind": str(s.get("kind", "")),
                        "error": code in ("2", "STATUS_CODE_ERROR"),
                        "message": msg[:300],
                        "http_status": attrs.get("http.response.status_code") or attrs.get("http.status_code"),
                        "rpc_status": attrs.get("rpc.grpc.status_code"),
                        "duration_ms": (int(s.get("endTimeUnixNano", 0)) - int(s.get("startTimeUnixNano", 0))) / 1e6,
                        "start_ns": int(s.get("startTimeUnixNano", 0)),
                        "attrs": _clean_attrs(attrs),
                    })
    return spans


def _legacy_spans(traces: list) -> list:
    spans = []
    for t in traces:
        procs = {k: v.get("serviceName") for k, v in (t.get("processes") or {}).items()}
        for s in t.get("spans", []):
            tags = {g["key"]: g.get("value") for g in s.get("tags", [])}
            msg = tags.get("otel.status_description") or ""
            for lg in s.get("logs", []) or []:
                f = {x["key"]: x.get("value") for x in lg.get("fields", [])}
                msg = msg or f.get("exception.message", "")
            refs = s.get("references") or []
            spans.append({
                "trace_id": s.get("traceID"), "span_id": s.get("spanID"),
                "parent_id": refs[0]["spanID"] if refs else None,
                "service": procs.get(s.get("processID"), "?"), "name": s.get("operationName"),
                "kind": str(tags.get("span.kind", "")),
                "error": tags.get("error") in (True, "true") or tags.get("otel.status_code") == "ERROR",
                "message": str(msg)[:300],
                "http_status": tags.get("http.response.status_code") or tags.get("http.status_code"),
                "rpc_status": tags.get("rpc.grpc.status_code"),
                "duration_ms": s.get("duration", 0) / 1000, "start_ns": s.get("startTime", 0) * 1000,
                "attrs": _clean_attrs(tags),
            })
    return spans


# ---------- queries ----------
def find_spans(service: str, minutes: int = 10, limit: int = 30, error_only: bool = False,
               operation: str | None = None) -> list:
    """Spans from recent traces that touched `service` (all services in those traces are included)."""
    flavour, base = _api()
    if flavour == "v3":
        now = time.time()
        fmt = lambda t: time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(t))
        params = {"query.service_name": service, "query.start_time_min": fmt(now - minutes * 60),
                  "query.start_time_max": fmt(now + 5), "query.num_traces": limit}
        if operation:
            params["query.operation_name"] = operation
        if error_only:
            params["query.attributes"] = '{"error":"true"}'
        try:
            spans = _otlp_spans(http.get(f"{base}/api/v3/traces", params))
        except Exception as e:
            if "404" in str(e):   # v3 returns 404 when no traces match
                return []
            raise
    else:
        params = {"service": service, "lookback": f"{minutes}m", "limit": limit}
        if operation:
            params["operation"] = operation
        if error_only:
            params["tags"] = '{"error":"true"}'
        spans = _legacy_spans(http.get(f"{base}/api/traces", params).get("data") or [])
    return _safe(spans)


def get_trace(trace_id: str) -> list:
    flavour, base = _api()
    if flavour == "v3":
        return _safe(_otlp_spans(http.get(f"{base}/api/v3/traces/{quote(trace_id, safe='')}")))
    return _safe(_legacy_spans(http.get(f"{base}/api/traces/{quote(trace_id, safe='')}").get("data") or []))
