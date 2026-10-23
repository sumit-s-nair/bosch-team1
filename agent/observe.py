"""LLM-facing investigation tools. Each returns a SHORT text summary with evidence ids [E#].
Numbers are computed here in code; the model only reads the conclusions."""
import time
from collections import Counter, defaultdict
from datetime import datetime, timezone

import re

from agent import flagguard
from agent.evidence import Ledger
from agent.tools import containers, logs, metrics, traces

NOW = "2m"                      # current window
BASE = "15m offset 20m"         # baseline window (before a fresh incident)
SERVER = 'span_kind="SPAN_KIND_SERVER"'
ERR = 'status_code="STATUS_CODE_ERROR"'
DEMO_SERVICES_SKIP = {"load-generator", "jaeger", "otelcol-contrib", "flagd", "flagd-ui", "telemetry-docs"}
# docker_stats metrics: container_cpu_utilization_ratio is already PERCENT (100 = one full core),
# container_memory_percent_ratio is percent of the container memory limit.
CPU_HOT_PCT = 80
MEM_HOT_PCT = 92
MEM_GROWTH_Q = "deriv(container_memory_usage_total_bytes[5m]) * 60 / 1048576"  # MB per minute
# minutes until the container hits its memory limit at the current growth rate (only for growing containers)
MEM_TTL_Q = ("(container_memory_usage_limit_bytes - container_memory_usage_total_bytes)"
             " / (deriv(container_memory_usage_total_bytes[5m]) * 60) and deriv(container_memory_usage_total_bytes[5m]) > 0")
LEAK_TTL_MIN = 30
NOT_A_CULPRIT_FOR_RESOURCES = {"load-generator"}   # synthetic traffic source; its own memory/CPU is not an incident



def _byservice(q: str, label="service_name") -> dict:
    try:
        return {r["metric"].get(label, "?"): r["value"] for r in metrics.promql(q) if r["value"] is not None}
    except Exception:
        return {}


def _fmt_pct(x):
    return "n/a" if x is None else f"{x*100:.1f}%"


def _fmt_ms(x):
    return "n/a" if x is None or x != x else f"{x:.0f}ms"


# ------------------------------------------------------------------ service health
def service_health_raw() -> dict:
    """Numbers per service. Used by both the detector and the LLM tool."""
    rate = lambda w, extra="": f'sum by (service_name)(rate(traces_span_metrics_calls_total{{{SERVER}{extra}}}[{w}]))'
    p95 = lambda w: (f'histogram_quantile(0.95, sum by (service_name, le)'
                     f'(rate(traces_span_metrics_duration_milliseconds_bucket{{{SERVER}}}[{w}])))')
    rps, erps = _byservice(rate(NOW)), _byservice(rate(NOW, "," + ERR))
    brps, berps = _byservice(rate(BASE)), _byservice(rate(BASE, "," + ERR))
    lat, blat = _byservice(p95(NOW)), _byservice(p95(BASE))
    out = {}
    for s in set(rps) | set(brps):
        if s in DEMO_SERVICES_SKIP:
            continue
        r = rps.get(s, 0.0)
        out[s] = {"rps": r,
                  "err_rate": (erps.get(s, 0.0) / r) if r > 0 else None,
                  "p95_ms": lat.get(s),
                  "base_rps": brps.get(s),
                  "base_err_rate": (berps.get(s, 0.0) / brps[s]) if brps.get(s) else None,
                  "base_p95_ms": blat.get(s)}
    return out


def service_overview(L: Ledger) -> str:
    """All services: request rate, error rate and p95 latency now vs baseline. Anomalies first."""
    h = service_health_raw()
    if not h:
        return "No span metrics returned from Prometheus."
    rows = []
    for s, v in h.items():
        score = 0.0
        if v["err_rate"] is not None:
            score += max(0.0, v["err_rate"] - (v["base_err_rate"] or 0)) * 10
        if v["p95_ms"] and v["base_p95_ms"] and v["base_p95_ms"] == v["base_p95_ms"] and v["base_p95_ms"] > 0:
            score += max(0.0, v["p95_ms"] / v["base_p95_ms"] - 1)
        if v["base_rps"] and v["rps"] < 0.3 * v["base_rps"]:
            score += 2  # traffic collapsed
        rows.append((score, s, v))
    rows.sort(reverse=True)
    lines = ["service | rps (base) | error% (base) | p95 (base) | evidence"]
    for score, s, v in rows:
        anom = score >= 0.5 or (v["err_rate"] or 0) >= 0.05
        eid = L.add("metric", f"{s}: rps={v['rps']:.2f} err={_fmt_pct(v['err_rate'])} "
                    f"p95={_fmt_ms(v['p95_ms'])} (baseline err={_fmt_pct(v['base_err_rate'])}, "
                    f"p95={_fmt_ms(v['base_p95_ms'])})", ref=f"span_metrics:{s}", value=v, anomalous=anom)
        flag = "  <-- ANOMALY" if anom else ""
        lines.append(f"{s} | {v['rps']:.2f} ({(v['base_rps'] or 0):.2f}) | {_fmt_pct(v['err_rate'])} "
                     f"({_fmt_pct(v['base_err_rate'])}) | {_fmt_ms(v['p95_ms'])} ({_fmt_ms(v['base_p95_ms'])}) "
                     f"| [{eid}]{flag}")
    return "\n".join(lines)


# ------------------------------------------------------------------ per-service operations
def service_operations(L: Ledger, service: str) -> str:
    """Per-operation error rate and p95 for one service, including its outgoing (CLIENT) calls to dependencies."""
    sel = f'service_name="{service}"'
    tot = metrics.promql(f'sum by (span_name, span_kind)(rate(traces_span_metrics_calls_total{{{sel}}}[{NOW}]))')
    err = {(r["metric"]["span_name"], r["metric"]["span_kind"]): r["value"] or 0.0 for r in metrics.promql(
        f'sum by (span_name, span_kind)(rate(traces_span_metrics_calls_total{{{sel},{ERR}}}[{NOW}]))')}
    p95 = {(r["metric"]["span_name"], r["metric"]["span_kind"]): r["value"] for r in metrics.promql(
        f'histogram_quantile(0.95, sum by (span_name, span_kind, le)'
        f'(rate(traces_span_metrics_duration_milliseconds_bucket{{{sel}}}[{NOW}])))')}
    rows = []
    for r in tot:
        k = (r["metric"]["span_name"], r["metric"]["span_kind"])
        if not r["value"] or r["value"] <= 0 or flagguard.is_flag_text(k[0]):
            continue
        e = err.get(k, 0.0) / r["value"]
        rows.append((e, p95.get(k) or 0, k, r["value"]))
    if not rows:
        return f"No span metrics for service '{service}' in the last {NOW} (service down or no traffic?)."
    rows.sort(key=lambda x: (x[0], x[1]), reverse=True)
    lines = [f"{service} operations (last {NOW}), worst first:"]
    for e, p, (name, kind), rps in rows[:12]:
        eid = L.add("metric", f"{service} {kind.replace('SPAN_KIND_', '')} '{name}': err={e*100:.1f}% "
                    f"p95={_fmt_ms(p)} rps={rps:.2f}", ref=f"span_metrics:{service}:{name}",
                    anomalous=e >= 0.05 or (p or 0) > 1000)
        lines.append(f"- {kind.replace('SPAN_KIND_', '')} {name}: err {e*100:.1f}%, p95 {_fmt_ms(p)}, {rps:.2f} rps [{eid}]")
    return "\n".join(lines)


# ------------------------------------------------------------------ traces
def trace_errors(L: Ledger, service: str, operation: str | None = None, minutes: int = 5) -> str:
    """Sample recent traces through `service`; group error spans and find the deepest failing span (likely origin).
    Rare failures (e.g. ~10% of ad requests, only EmptyCart) can miss a plain sample, so error traces are fetched too."""
    spans = traces.find_spans(service, minutes=minutes, limit=100, operation=operation)
    try:
        spans += traces.find_spans(service, minutes=minutes, limit=30, error_only=True, operation=operation)
    except Exception:
        pass   # error filter unsupported by this Jaeger; the plain sample still stands
    spans = list({(s["trace_id"], s["span_id"]): s for s in spans}.values())   # drop duplicates across both samples
    spans = [dict(s, message=flagguard.scrub(s["message"])) for s in spans if not flagguard.is_flag_span(s)]
    scope = f"{service}{' / ' + operation if operation else ''}"
    if not spans:
        return f"No traces found for '{scope}' in the last {minutes}m."
    by_trace = defaultdict(list)
    for s in spans:
        by_trace[s["trace_id"]].append(s)
    n_traces = len(by_trace)
    err_traces = 0
    origins = Counter()
    samples = {}
    for tid, ss in by_trace.items():
        errs = [s for s in ss if s["error"]]
        if not errs:
            continue
        err_traces += 1
        err_ids = {s["span_id"] for s in errs}
        parents_with_err_child = {s["parent_id"] for s in errs}
        # origin = error span none of whose children errored
        for s in errs:
            if s["span_id"] not in parents_with_err_child:
                key = (s["service"], s["name"], s["message"][:120] or f"http={s['http_status']} grpc={s['rpc_status']}")
                origins[key] += 1
                samples.setdefault(key, (tid, s))
    slow = sorted(spans, key=lambda s: s["duration_ms"], reverse=True)[:3]
    lines = [f"{n_traces} traces through {scope} in last {minutes}m, {err_traces} contain errors."]
    if not err_traces:
        lines.append("No errors in this sample, which does NOT mean there are none: errors may be rare. "
                     "Check service_operations for an operation with err > 0 and call trace_errors with that operation, "
                     "and check error_logs.")
    for (svc, name, msg), c in origins.most_common(5):
        tid, s = samples[(svc, name, msg)]
        eid = L.add("trace", f"error origin {svc} '{name}': {msg} (seen in {c} traces, e.g. trace {tid})",
                    ref=f"trace:{tid}", anomalous=True)
        lines.append(f"- origin {svc} / {name}: \"{msg}\" in {c} traces, e.g. trace {tid} [{eid}]")
    for s in slow:
        eid = L.add("trace", f"slow span {s['service']} '{s['name']}' {s['duration_ms']:.0f}ms in trace {s['trace_id']}",
                    ref=f"trace:{s['trace_id']}")
        lines.append(f"- slowest: {s['service']} / {s['name']} {s['duration_ms']:.0f}ms [{eid}]")
    return "\n".join(lines)


# ------------------------------------------------------------------ logs
ACCESS_RE = re.compile(r'"(?P<method>[A-Z]+) (?P<path>\S+) [^"]*" (?P<code>\d{3}) (?P<flags>\S+)')
GATEWAY = "frontend-proxy"


def _gateway_errors(minutes: int) -> list:
    """Envoy access logs have no severity; parse status code / response flags from the body."""
    out = []
    for r in logs.search_logs(service=GATEWAY, minutes=minutes, size=200):
        m = ACCESS_RE.search(r["body"])
        if not m or flagguard.is_flag_path(m["path"]) or m["path"].startswith(("/jaeger", "/grafana", "/loadgen")):
            continue
        if m["code"].startswith("5") or m["code"] == "000" or m["flags"] not in ("-", ""):
            path = re.sub(r"/[0-9A-Z]{6,}\b", "/{id}", m["path"].split("?")[0])
            out.append((path, m["code"], m["flags"], r))
    return out


def error_logs(L: Ledger, service: str | None = None, minutes: int = 10) -> str:
    """Recent WARN+ log lines grouped by service and message, plus gateway (envoy) 5xx/upstream failures."""
    rows = [r for r in logs.search_logs(service=service, min_severity=13, minutes=minutes, size=200)
            if not flagguard.is_flag_service(r["service"])]
    lines = []
    if rows:
        groups, sample = Counter(), {}
        for r in rows:
            key = (r["service"], r["severity"], flagguard.scrub(r["body"][:160]))
            groups[key] += 1
            sample.setdefault(key, r)
        lines.append(f"{len(rows)} WARN+ log lines in last {minutes}m (top groups):")
        for (svc, sev, body), c in groups.most_common(8):
            r = sample[(svc, sev, body)]
            eid = L.add("log", f"{svc} {sev} x{c}: {body}", ref=f"log:{r['id']}", anomalous=True)
            lines.append(f"- {svc} [{sev}] x{c}: {body} [{eid}]")
    if service in (None, GATEWAY):
        gw = _gateway_errors(minutes)
        if gw:
            groups = Counter((p, c, f) for p, c, f, _ in gw)
            first = {}
            for p, c, f, r in gw:
                first.setdefault((p, c, f), r)
            lines.append(f"Gateway ({GATEWAY}) failed requests in last {minutes}m:")
            for (p, c, f), n in groups.most_common(6):
                eid = L.add("log", f"{GATEWAY} access log: {p} -> HTTP {c} flags={f} x{n}",
                            ref=f"log:{first[(p, c, f)]['id']}", anomalous=True)
                lines.append(f"- {p} -> HTTP {c} (envoy flags {f}) x{n} [{eid}]")
    if not lines:
        return f"No WARN/ERROR logs{' for ' + service if service else ''} and no gateway errors in the last {minutes}m."
    return "\n".join(lines)


# ------------------------------------------------------------------ containers / infra
def container_health(L: Ledger) -> str:
    """Docker state of every demo container: stopped, restarting, OOM-killed, recently (re)started, CPU/mem pressure."""
    cs = containers.list_containers()
    cpu = _byservice("container_cpu_utilization_ratio", "container_name")
    memp = _byservice("container_memory_percent_ratio", "container_name")
    grow = _byservice(MEM_GROWTH_Q, "container_name")
    ttl = _byservice(MEM_TTL_Q, "container_name")
    now = datetime.now(timezone.utc)
    lines, healthy = [], 0
    for c in cs:
        info = containers.inspect(c["name"])
        issues = []
        if not info.get("running"):
            issues.append(f"NOT RUNNING (status={info['status']}, exit={info['exit_code']})")
        if info.get("oom_killed"):
            issues.append("OOM-KILLED")
        if info.get("restart_count"):
            issues.append(f"restarts={info['restart_count']}")
        try:
            started = datetime.fromisoformat(info["started_at"][:26].rstrip("Z") + "+00:00")
            age = (now - started).total_seconds()
            if age < 900:
                issues.append(f"started {age/60:.1f} min ago")
        except Exception:
            pass
        if "unhealthy" in c["status"]:
            issues.append("healthcheck UNHEALTHY")
        if cpu.get(c["name"], 0) > CPU_HOT_PCT and c["name"] != "load-generator":
            issues.append(f"CPU {cpu[c['name']]:.0f}% of a core")
        if memp.get(c["name"], 0) > MEM_HOT_PCT:
            issues.append(f"memory {memp[c['name']]:.0f}% of limit")
        if ttl.get(c["name"], 1e9) < LEAK_TTL_MIN and c["name"] not in NOT_A_CULPRIT_FOR_RESOURCES:
            issues.append(f"memory growing {grow.get(c['name'], 0):.1f} MB/min, hits limit in ~{ttl[c['name']]:.0f} min")
        if issues:
            eid = L.add("container", f"{c['name']}: " + "; ".join(issues), ref=f"container:{c['name']}",
                        value=info, anomalous=True)
            lines.append(f"- {c['name']}: {'; '.join(issues)} [{eid}]")
        else:
            healthy += 1
    head = f"{len(cs)} containers, {healthy} with no issues."
    return head + ("\n" + "\n".join(lines) if lines else "")


def resource_usage(L: Ledger) -> str:
    """Top containers by CPU, memory (% of limit) and memory growth rate (leak signal)."""
    cpu = _byservice("container_cpu_utilization_ratio", "container_name")
    memp = _byservice("container_memory_percent_ratio", "container_name")
    grow = _byservice(MEM_GROWTH_Q, "container_name")
    lines = ["Top CPU (% of one core):"]
    for n, v in sorted(cpu.items(), key=lambda x: -x[1])[:5]:
        lines.append(f"- {n}: {v:.0f}% [{L.add('metric', f'{n} cpu={v:.0f}% of a core', ref=f'container_cpu:{n}')}]")
    lines.append("Top memory (% of container limit):")
    for n, v in sorted(memp.items(), key=lambda x: -x[1])[:5]:
        lines.append(f"- {n}: {v:.0f}% [{L.add('metric', f'{n} mem={v:.0f}% of limit', ref=f'container_mem:{n}')}]")
    ttl = _byservice(MEM_TTL_Q, "container_name")
    lines.append("Fastest memory growth (MB/min over 5m; minutes until limit):")
    for n, v in sorted(grow.items(), key=lambda x: -x[1])[:3]:
        t = ttl.get(n)
        leak = t is not None and t < LEAK_TTL_MIN and n not in NOT_A_CULPRIT_FOR_RESOURCES
        d = f"{n} memory growth {v:+.1f} MB/min, limit in {'~%.0f min' % t if t else 'n/a'}"
        lines.append(f"- {d}{'  <-- LEAK RISK' if leak else ''} [{L.add('metric', d, ref=f'container_mem_growth:{n}', anomalous=leak)}]")
    return "\n".join(lines)


def recent_changes(L: Ledger, minutes: int = 30) -> str:
    """Deployment-history proxy: containers (re)created or restarted in the last N minutes, with image tag."""
    now = datetime.now(timezone.utc)
    lines = []
    for c in containers.list_containers():
        info = containers.inspect(c["name"])
        try:
            started = datetime.fromisoformat(info["started_at"][:26].rstrip("Z") + "+00:00")
        except Exception:
            continue
        age = (now - started).total_seconds() / 60
        if age <= minutes:
            eid = L.add("change", f"{c['name']} (re)started {age:.1f} min ago, restarts={info['restart_count']}",
                        ref=f"container:{c['name']}", anomalous=True)
            lines.append(f"- {c['name']} started {age:.1f} min ago (restarts={info['restart_count']}) [{eid}]")
    return ("Changes in last %d min:\n" % minutes + "\n".join(lines)) if lines else f"No container (re)starts in the last {minutes} min."


def cache_health(L: Ledger) -> str:
    """Valkey/Redis cache used by the cart service: up?, clients, rejected connections, memory."""
    q = lambda m: (metrics.promql(m) or [{"value": None}])[0]["value"]
    up, clients, rej, mem = (q("redis_uptime_seconds_total"), q("redis_clients_connected"),
                             q('increase(redis_connections_rejected_total[5m])'), q("redis_memory_used_bytes"))
    age = q("time() - max(timestamp(redis_uptime_seconds_total))")
    stale = age is None or age > 60
    detail = (f"valkey uptime={up}s clients={clients} rejected_5m={rej} mem={mem} "
              f"{'(METRICS STALE: cache may be down)' if stale else ''}")
    return f"{detail} [{L.add('metric', detail, ref='redis', anomalous=stale or (rej or 0) > 0)}]"


TOOLS = {
    "service_overview": (service_overview, "All services: request rate, error % and p95 latency now vs baseline. Call first.", {}),
    "service_operations": (service_operations, "Per-operation error % and latency for ONE service, incl. its outgoing calls to dependencies.", {"service": "string"}),
    "trace_errors": (trace_errors, "Sample recent traces through a service (optionally one operation from service_operations) and find which downstream span the errors originate from.", {"service": "string", "operation": "string?"}),
    "error_logs": (error_logs, "Recent WARN/ERROR log lines grouped by message. Optional service filter.", {"service": "string?"}),
    "container_health": (container_health, "Docker state of all containers: stopped, restarting, OOM, recently restarted, CPU/memory pressure.", {}),
    "resource_usage": (resource_usage, "Top containers by CPU and memory right now.", {}),
    "recent_changes": (recent_changes, "Deployment history proxy: containers (re)started recently.", {}),
    "cache_health": (cache_health, "Health of the Valkey/Redis cache used by the cart service.", {}),
}
