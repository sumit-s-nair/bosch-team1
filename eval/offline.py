"""Offline adapter: serve a recorded scenario through the same raw-client functions the tools use
(metrics.promql, traces.find_spans, logs.search_logs, containers.*), so observe/brain/detector run unchanged.
Used for development and evaluation when the live demo is not running."""
import re
from datetime import datetime, timedelta, timezone

from agent.tools import containers, logs, metrics, traces


def _rows(label, mapping):
    return [{"metric": {label: k}, "value": float(v)} for k, v in mapping.items() if v is not None]


def _fake_promql(sc):
    S = sc["services"]

    def promql(q):
        if re.search(r"\[[^\]]*offset", q):    # like real Prometheus: offset inside a range selector is a parse error
            raise ValueError(f"bad_data: invalid PromQL (offset inside range selector): {q[:120]}")
        base = "offset" in q
        err = "STATUS_CODE_ERROR" in q
        if "traces_span_metrics" in q:
            m = re.search(r'service_name="([^"]+)"', q)
            if m:  # per-operation query for one service
                svc = m.group(1)
                v = S.get(svc)
                if not v or not v["rps"]:
                    return []
                out = []
                for kind, name, share in _ops(svc):
                    o = sc["ops"].get((svc, name), {})
                    rps = v["rps"] * share * o.get("rps_mult", 1)
                    labels = {"span_name": name, "span_kind": f"SPAN_KIND_{kind}"}
                    if "histogram_quantile" in q:
                        val = o.get("p95", v["p95"] if kind == "SERVER" else (v["p95"] or 0) * 0.8)
                    elif err:
                        val = rps * o.get("err", 0.0)
                    else:
                        val = rps
                    out.append({"metric": labels, "value": float(val or 0)})
                return out
            if "histogram_quantile" in q:
                return _rows("service_name", {s: (v["base_p95"] if base else v["p95"]) for s, v in S.items()})
            if err:
                return _rows("service_name", {s: (v["base_rps"] * v["base_err"] if base else v["rps"] * v["err"]) for s, v in S.items()})
            return _rows("service_name", {s: (v["base_rps"] if base else v["rps"]) for s, v in S.items()})
        if "container_memory_usage_limit_bytes" in q:          # minutes-to-limit (leak) query
            return _rows("container_name", sc["mem_ttl"])
        if "deriv(container_memory_usage_total_bytes" in q:
            return _rows("container_name", sc["mem_growth"])
        if "container_cpu_utilization_ratio" in q:
            return _rows("container_name", {c: sc["cpu"].get(c, 1) for c in _running(sc)})
        if "container_memory_percent_ratio" in q:
            return _rows("container_name", {c: sc["mem"].get(c, 30) for c in _running(sc)})
        if "redis" in q:
            r = sc["redis"]
            if not r.get("up"):
                return []   # cache down: series stop updating
            if "time() - max(timestamp" in q:
                return [{"metric": {}, "value": 5.0}]
            if "rejected" in q:
                return [{"metric": {}, "value": float(r.get("rejected", 0))}]
            if "clients" in q:
                return [{"metric": {}, "value": float(r.get("clients", 3))}]
            if "memory" in q:
                return [{"metric": {}, "value": 1.4e6}]
            return [{"metric": {}, "value": 7200.0}]
        return []
    return promql


def _ops(svc):
    from eval.scenarios import OPS
    return OPS.get(svc, [("SERVER", f"{svc} request", 1)])


def _running(sc):
    return [c for c, v in sc["containers"].items() if v["state"] == "running"]


def _spans(sc, chains):
    out, n = [], 0
    for chain, origin, msg, slow_idx, slow_ms, attrs in sc["traces"]:
        path = chains[chain] if isinstance(chain, str) else chain
        n += 1
        tid = f"{n:032x}"
        if origin is not None:
            origin = origin % len(path)
            path = path[:origin + 1]
        parent = None
        for i, (service, name) in enumerate(path):
            sid = f"{n:08x}{i:08x}"
            is_err = origin is not None
            dur = 5.0
            if slow_idx is not None and i <= slow_idx:
                dur = slow_ms if i == slow_idx else slow_ms + 20
            out.append({"trace_id": tid, "span_id": sid, "parent_id": parent, "service": service, "name": name,
                        "kind": "", "error": is_err,
                        "message": msg if (is_err and i == origin) else ("" if not is_err else "rpc error: downstream failure"),
                        "http_status": None, "rpc_status": None, "duration_ms": dur, "start_ns": 0,
                        "attrs": attrs})
            parent = sid
    return out


def install(sc):
    """Patch the raw clients to serve scenario `sc` (a dict from eval/scenarios.py)."""
    from eval import scenarios
    spans = _spans(sc, scenarios.CHAINS)
    now = datetime.now(timezone.utc)

    metrics.promql = _fake_promql(sc)
    metrics.metric_names = lambda match="": [n for n in ("traces_span_metrics_calls_total",) if match in n]

    def list_containers():
        return [{"name": c, "id": c[:12], "state": v["state"], "status": v["status"]} for c, v in sc["containers"].items()]

    def inspect(name):
        v = sc["containers"][name]
        started = (now - timedelta(minutes=v["started_min_ago"])).isoformat().replace("+00:00", "Z")
        return {"name": name, "status": v["state"], "health": None, "running": v["state"] == "running",
                "restart_count": v["restart_count"], "exit_code": v["exit_code"], "oom_killed": v["oom_killed"],
                "started_at": started, "finished_at": None}
    containers.list_containers = list_containers
    containers.inspect = inspect
    containers.stats = lambda: []

    traces._api = lambda: ("offline", "scenario")
    traces.services = lambda: sorted({s["service"] for s in spans})

    def find_spans(service, minutes=10, limit=30, error_only=False, operation=None):
        tids = {s["trace_id"] for s in spans if s["service"] == service and (not operation or s["name"] == operation)}
        if error_only:
            tids &= {s["trace_id"] for s in spans if s["error"]}
        tids = sorted(tids)[:limit]
        return [dict(s) for s in spans if s["trace_id"] in tids]
    traces.find_spans = find_spans
    traces.get_trace = lambda tid: [dict(s) for s in spans if s["trace_id"] == tid]

    log_rows = []
    for i, (svc, sev, num, body, count) in enumerate(sc["logs"]):
        for j in range(count):
            log_rows.append({"id": f"log-{i}-{j}", "ts": now.isoformat(), "service": svc, "severity": sev,
                             "severity_number": num, "body": body, "trace_id": None})
    # gateway access logs: 5xx for failing frontend calls
    for chain, origin, *_ in sc["traces"]:
        if origin is not None and (chain if isinstance(chain, str) else "") in scenarios.CHAINS:
            path = scenarios.CHAINS[chain][0][1].split(" ", 1)[-1]
            method = scenarios.CHAINS[chain][0][1].split(" ", 1)[0]
            log_rows.append({"id": f"gw-{len(log_rows)}", "ts": now.isoformat(), "service": "frontend-proxy",
                             "severity": None, "severity_number": None, "trace_id": None,
                             "body": f'[{now.isoformat()}] "{method} {path} HTTP/1.1" 500 - via_upstream - "-" 120 80 15 14 "-" "python-requests" "x" "frontend-proxy:8080" "172.18.0.9:8080" frontend 172.18.0.2:50000 172.18.0.2:8080 172.18.0.1:40000 - -'})

    def search_logs(service=None, min_severity=None, text=None, minutes=15, size=30):
        rows = [r for r in log_rows if (not service or r["service"] == service)
                and (not min_severity or (r["severity_number"] or 0) >= min_severity)
                and (not text or text.lower() in r["body"].lower())
                and r["service"] not in logs.NOISE_SERVICES]
        return [{k: v for k, v in r.items() if k != "severity_number"} for r in rows[:size]]
    logs.search_logs = search_logs
    logs.fields = lambda: ("resource.service.name", "severity.text", "body")
