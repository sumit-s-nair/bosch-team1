"""LLM-facing investigation tools. Each returns a SHORT text summary with evidence ids [E#].
Numbers are computed here in code; the model only reads the conclusions."""
import time
import json
from functools import wraps
from collections import Counter, defaultdict
from datetime import datetime, timezone

import re

from agent import flagguard
from agent.evidence import Ledger
from agent.tools import containers, logs, metrics, traces, metric_details

NOW = "2m"                      # current window
BASE = "15m offset 20m"         # baseline window (before a fresh incident)
SERVER = 'span_kind="SPAN_KIND_SERVER"'
ERR = 'status_code="STATUS_CODE_ERROR"'
DEMO_SERVICES_SKIP = {"load-generator", "jaeger", "otelcol-contrib", "flagd", "flagd-ui", "telemetry-docs"}
# docker_stats metrics: container_cpu_utilization_ratio is already PERCENT (100 = one full core),
# container_memory_percent_ratio is percent of the container memory limit.
CPU_HOT_PCT = 80
MEM_HOT_PCT = 95
MEM_GROWTH_Q = "deriv(container_memory_usage_total_bytes[5m]) * 60 / 1048576"  # MB per minute
# minutes until the container hits its memory limit at the current growth rate (only for growing containers)
MEM_TTL_Q = ("(container_memory_usage_limit_bytes - container_memory_usage_total_bytes)"
             " / (deriv(container_memory_usage_total_bytes[10m]) * 60) and deriv(container_memory_usage_total_bytes[10m]) > 0")
LEAK_TTL_MIN = 30
MEMORY_GROWTH_THRESHOLD_PCT = 20.0  # Plain growth hint, not a diagnosis.
TRAFFIC_SPIKE_RATIO = 3.0  # Plain volume hint, not a diagnosis.
# synthetic traffic source + the monitoring stack (JVMs like OpenSearch fill their heap to the limit by design):
# their own memory/CPU is not an incident in the shop
NOT_A_CULPRIT_FOR_RESOURCES = {"load-generator", "otel-collector", "opensearch", "prometheus", "jaeger", "grafana",
                               "opamp-server", "telemetry-docs"}
LEAK_MIN_MEM_PCT = 70           # a leak only matters once the container is this close to its limit
WARMUP_MIN = 15                 # memory grows after a (re)start; no leak verdicts during warm-up
STACK_START_WINDOW_S = 120      # containers started within 2 min of the bulk = "the demo started", not a change


def _starts() -> tuple[dict, float | None, datetime]:
    """Start time per container, plus when the stack as a whole started (if most containers started together)."""
    now = datetime.now(timezone.utc)
    starts, info = {}, {}
    for c in containers.list_containers():
        i = containers.inspect(c["name"])
        info[c["name"]] = i
        try:
            starts[c["name"]] = datetime.fromisoformat(i["started_at"][:26].rstrip("Z") + "+00:00").timestamp()
        except Exception:
            pass
    stack = None
    if starts:
        ts = sorted(starts.values())
        med = ts[len(ts) // 2]
        if sum(abs(t - med) <= STACK_START_WINDOW_S for t in ts) >= 0.6 * len(ts):
            stack = med
    return {"starts": starts, "info": info}, stack, now


def _api_restarts() -> dict:
    """Restarts our own Action API performed (from its audit log), so the agent does not blame its own fixes."""
    import os
    from agent import config
    path = config.setting("ACTION_API_AUDIT", "runs/action_api_audit.jsonl")
    out = {}
    if not os.path.exists(path):
        return out
    try:
        for line in open(path, encoding="utf-8").read().splitlines()[-500:]:
            r = json.loads(line)
            if r.get("status") == "executed" and r.get("action") == "restart_service":
                t = datetime.strptime(r["t"], "%Y-%m-%d %H:%M:%S").astimezone().timestamp()
                out.setdefault((r.get("params") or {}).get("service"), []).append(t)
    except Exception:
        pass
    return out


def _restarted_by_us(name, t, api=None) -> bool:
    return any(abs(t - x) <= 120 for x in (api if api is not None else _api_restarts()).get(name, []))


def _individual_restart(name, starts, stack, api=None) -> bool:
    """Started on its own: not as part of the whole demo starting, and not by an approved Action API restart."""
    t = starts.get(name)
    return (t is not None and (stack is None or t - stack > STACK_START_WINDOW_S)
            and not _restarted_by_us(name, t, api))
# CPU starvation: usage as % of the container's OWN cpu limit (docker --cpus), and CFS throttling ratio
CPU_LIMIT_HOT_PCT = 85
THROTTLE_HOT = 0.20
THROTTLE_Q = ("rate(container_cpu_throttling_data_throttled_periods_total[2m])"
              " / rate(container_cpu_throttling_data_periods_total[2m])")
# a service is "silent" when it had at least this much traffic before and now reports < 10% of it
SILENT_MIN_BASE_RPS = 0.05



def _byservice(q: str, label="service_name", strict=False) -> dict:
    try:
        return {r["metric"].get(label, "?"): r["value"] for r in metrics.promql(q) if r["value"] is not None}
    except Exception:
        if strict:
            raise
        return {}


def _fmt_pct(x):
    return "n/a" if x is None else f"{x*100:.1f}%"


def _fmt_ms(x):
    return "n/a" if x is None or x != x else f"{x:.0f}ms"


# ------------------------------------------------------------------ service health
def service_health_raw(window=None, strict=False) -> dict:
    """Numbers per service. Used by both the detector and the LLM tool."""
    rate = lambda w, extra="": f'sum by (service_name)(rate(traces_span_metrics_calls_total{{{SERVER}{extra}}}{metrics.rng(w)}))'
    p95 = lambda w: (f'histogram_quantile(0.95, sum by (service_name, le)'
                     f'(rate(traces_span_metrics_duration_milliseconds_bucket{{{SERVER}}}{metrics.rng(w)})))')
    current = window or NOW
    baseline = f"1h offset {window}" if window else BASE
    query = lambda q: _byservice(q, strict=strict)
    rps, erps = query(rate(current)), query(rate(current, "," + ERR))
    brps, berps = query(rate(baseline)), query(rate(baseline, "," + ERR))
    lat, blat = query(p95(current)), query(p95(baseline))
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


def _bounded_tool(fn):
    @wraps(fn)
    def call(*args, **kwargs):
        try:
            result = fn(*args, **kwargs)
            return metric_details.compact(result) if isinstance(result, dict) else result[:2500]
        except Exception as exc:
            return {"error": f"{type(exc).__name__}: {str(exc)[:400]}"}
    return call


@_bounded_tool
def service_overview(L: Ledger, window=None, service=None, group_by=None):
    """All services: request rate, error rate and p95 latency now vs baseline. Anomalies first."""
    if window is not None:
        metric_details.window_ok(window)
    if group_by is not None:
        result = metric_details.traffic(service, group_by, window or "5m", TRAFFIC_SPIKE_RATIO)
        for item in result['items']:
            detail = f"{item['service']} {group_by} {item[group_by]}: {item['rate_per_s']} rps, baseline={item['baseline_rate_per_s']}, ratio={item['ratio']}"
            item['evidence_id'] = L.add('metric', detail, ref=f"{result['metric']}:{item[group_by]}", value=dict(item),
                                        anomalous=bool(item.get('spike')))
        return result
    h = service_health_raw(window=window, strict=True)
    if service is not None:
        h = {name: values for name, values in h.items() if name == service}
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
    for score, s, v in rows[:10]:
        anom = score >= 0.5 or (v["err_rate"] or 0) >= 0.05
        eid = L.add("metric", f"{s}: rps={v['rps']:.2f} err={_fmt_pct(v['err_rate'])} "
                    f"p95={_fmt_ms(v['p95_ms'])} (baseline err={_fmt_pct(v['base_err_rate'])}, "
                    f"p95={_fmt_ms(v['base_p95_ms'])})", ref=f"span_metrics:{s}", value=v, anomalous=anom)
        flag = "  <-- ANOMALY" if anom else ""
        lines.append(f"{s} | {v['rps']:.2f} ({(v['base_rps'] or 0):.2f}) | {_fmt_pct(v['err_rate'])} "
                     f"({_fmt_pct(v['base_err_rate'])}) | {_fmt_ms(v['p95_ms'])} ({_fmt_ms(v['base_p95_ms'])}) "
                     f"| [{eid}]{flag}")
    lines += silent_services(L, h)
    return "\n".join(lines)


def silent_services(L: Ledger, h: dict | None = None) -> list[str]:
    """Services that had traffic in the baseline but now report (almost) no spans: crashed, hung or cut off."""
    h = h if h is not None else service_health_raw()
    silent = {s: v for s, v in h.items()
              if (v["base_rps"] or 0) >= SILENT_MIN_BASE_RPS and v["rps"] < 0.1 * v["base_rps"]}
    if not silent:
        return ["Silent services: none (every service that had traffic before still reports spans)."]
    try:
        state = {c["name"]: c["status"] for c in containers.list_containers()}
    except Exception:
        state = {}
    out = ["Silent services (had traffic before, now little or none) - check container_health and their callers:"]
    for s, v in sorted(silent.items()):
        d = (f"{s} went SILENT: {v['rps']:.2f} rps now vs {v['base_rps']:.2f} rps baseline "
             f"(container: {state.get(s, 'container not found')})")
        out.append(f"- {d} [{L.add('metric', d, ref=f'span_metrics:{s}', value=v, anomalous=True)}]")
    return out


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
def _is_leak(name, memp, ttl, starts, now_ts) -> bool:
    started = starts.get(name)
    warm = started is not None and now_ts - started < WARMUP_MIN * 60
    return (name not in NOT_A_CULPRIT_FOR_RESOURCES and not warm and ttl.get(name, 1e9) < LEAK_TTL_MIN
            and memp.get(name, 0) >= LEAK_MIN_MEM_PCT)


def container_health(L: Ledger) -> str:
    """Docker state of every demo container: stopped, restarting, OOM-killed, restarted on its own, CPU/mem pressure."""
    cs = containers.list_containers()
    cpu = _byservice("container_cpu_utilization_ratio", "container_name")
    memp = _byservice("container_memory_percent_ratio", "container_name")
    grow = _byservice(MEM_GROWTH_Q, "container_name")
    ttl = _byservice(MEM_TTL_Q, "container_name")
    throttled = _byservice(THROTTLE_Q, "container_name")
    st, stack, now = _starts()
    starts = st["starts"]
    lines, healthy = [], 0
    if stack is not None and now.timestamp() - stack < 1800:
        d = f"the whole demo started together {(now.timestamp() - stack) / 60:.0f} min ago (normal startup, baselines still filling)"
        lines.append(f"- note: {d} [{L.add('change', d, ref='stack-start', anomalous=False)}]")
    for c in cs:
        name = c["name"]
        info = st["info"].get(name) or containers.inspect(name)
        on_its_own = _individual_restart(name, starts, stack)
        issues = []
        running = info.get("running")
        if not running:
            code = info.get("exit_code")
            meaning = {0: "exited cleanly", 137: "killed by a signal (e.g. docker stop/kill)",
                       143: "terminated by SIGTERM (e.g. docker stop)", 139: "crashed (segfault)"}.get(code, "exited with an error")
            oom = "OOM-KILLED (out of memory)" if info.get("oom_killed") else "not OOM-killed"
            issues.append(f"NOT RUNNING (status={info['status']}, exit={code}: {meaning}; {oom})")
        elif info.get("oom_killed"):
            issues.append("OOM-KILLED")
        if info.get("restart_count") and on_its_own:
            issues.append(f"restarts={info['restart_count']}")
        if on_its_own and name in starts and now.timestamp() - starts[name] < 900:
            issues.append(f"restarted on its own {(now.timestamp() - starts[name]) / 60:.1f} min ago")
        if "unhealthy" in c["status"]:
            issues.append("healthcheck UNHEALTHY")
        if not running:      # stale CPU/memory readings from before it stopped would mislead: report state only
            eid = L.add("container", f"{name}: " + "; ".join(issues), ref=f"container:{name}", value=info, anomalous=True)
            lines.append(f"- {name}: {'; '.join(issues)} [{eid}]")
            continue
        limit, used = info.get("cpu_limit_cores"), cpu.get(name)   # used = % of ONE core
        if limit and used is not None and name not in NOT_A_CULPRIT_FOR_RESOURCES:
            of_limit = used / (limit * 100) * 100
            if of_limit > CPU_LIMIT_HOT_PCT:
                issues.append(f"CPU STARVED: using {of_limit:.0f}% of its {limit:g}-CPU limit ({used:.0f}% of a core)")
        elif used is not None and used > CPU_HOT_PCT and name not in NOT_A_CULPRIT_FOR_RESOURCES:
            issues.append(f"CPU {used:.0f}% of a core")
        if throttled.get(name, 0) > THROTTLE_HOT and name not in NOT_A_CULPRIT_FOR_RESOURCES:
            issues.append(f"CPU THROTTLED in {throttled[name]*100:.0f}% of scheduling periods"
                          + (f" (limit {limit:g} CPU)" if limit else ""))
        if memp.get(name, 0) > MEM_HOT_PCT and name not in NOT_A_CULPRIT_FOR_RESOURCES:
            issues.append(f"memory {memp[name]:.0f}% of limit")
        if _is_leak(name, memp, ttl, starts, now.timestamp()):
            issues.append(f"memory growing {grow.get(name, 0):.1f} MB/min, hits limit in ~{ttl[name]:.0f} min")
        if issues:
            eid = L.add("container", f"{name}: " + "; ".join(issues), ref=f"container:{name}", value=info, anomalous=True)
            lines.append(f"- {name}: {'; '.join(issues)} [{eid}]")
        else:
            healthy += 1
    head = f"{len(cs)} containers, {healthy} with no issues."
    return head + ("\n" + "\n".join(lines) if lines else "")


@_bounded_tool
def resource_usage(L: Ledger, service=None, window="10m"):
    """Top containers by CPU, memory (% of limit) and memory growth rate (leak signal)."""
    metric_details.window_ok(window)
    cpu = _byservice("container_cpu_utilization_ratio", "container_name", strict=True)
    memp = _byservice("container_memory_percent_ratio", "container_name", strict=True)
    grow = _byservice(MEM_GROWTH_Q, "container_name", strict=True)
    lines = ["Top CPU (% of one core):"]
    for n, v in sorted(cpu.items(), key=lambda x: -x[1])[:5]:
        lines.append(f"- {n}: {v:.0f}% [{L.add('metric', f'{n} cpu={v:.0f}% of a core', ref=f'container_cpu:{n}')}]")
    lines.append("Top memory (% of container limit):")
    for n, v in sorted(memp.items(), key=lambda x: -x[1])[:5]:
        lines.append(f"- {n}: {v:.0f}% [{L.add('metric', f'{n} mem={v:.0f}% of limit', ref=f'container_mem:{n}')}]")
    ttl = _byservice(MEM_TTL_Q, "container_name", strict=True)
    lines.append("Fastest memory growth (MB/min over 5m; minutes until limit):")
    for n, v in sorted(grow.items(), key=lambda x: -x[1])[:3]:
        t = ttl.get(n)
        leak = t is not None and t < LEAK_TTL_MIN and n not in NOT_A_CULPRIT_FOR_RESOURCES and memp.get(n, 0) >= LEAK_MIN_MEM_PCT
        d = f"{n} memory growth {v:+.1f} MB/min, limit in {'~%.0f min' % t if t else 'n/a'}"
        lines.append(f"- {d}{'  <-- LEAK RISK' if leak else ''} [{L.add('metric', d, ref=f'container_mem_growth:{n}', anomalous=leak)}]")
    runtime = metric_details.runtime(service, window, MEMORY_GROWTH_THRESHOLD_PCT)
    result = {"summary": "\n".join(lines), "runtime": runtime}
    for kind in ('gc', 'heap', 'process_cpu'):
        if kind in runtime:
            value = runtime[kind]
            now, base = value.get('now'), value.get('baseline')
            anom = (kind == 'heap' and bool(runtime.get('memory_growing'))) or \
                   (kind in ('gc', 'process_cpu') and now is not None and base is not None and base > 0 and now > 3 * base)
            value['evidence_id'] = L.add('metric', f"{service} runtime {kind}: {json.dumps(value)}", ref=value['metric'],
                                         value=dict(value), anomalous=anom)
    return result


def recent_changes(L: Ledger, minutes: int = 30) -> str:
    """Deployment-history proxy: containers (re)started on their own in the last N minutes.
    The whole demo starting together is reported once, as normal startup, not as a change."""
    st, stack, now = _starts()
    lines = []
    if stack is not None and now.timestamp() - stack <= minutes * 60:
        d = (f"the whole demo started together {(now.timestamp() - stack) / 60:.0f} min ago "
             f"({sum(abs(t - stack) <= STACK_START_WINDOW_S for t in st['starts'].values())} containers): normal startup, not a change")
        lines.append(f"- {d} [{L.add('change', d, ref='stack-start', anomalous=False)}]")
    api = _api_restarts()
    for name, t in sorted(st["starts"].items()):
        age = (now.timestamp() - t) / 60
        if age <= minutes and _restarted_by_us(name, t, api):
            d = f"{name} restarted {age:.1f} min ago by an approved Action API fix (not a fault)"
            lines.append(f"- {d} [{L.add('change', d, ref=f'container:{name}', anomalous=False)}]")
            continue
        if age <= minutes and _individual_restart(name, st["starts"], stack, api):
            rc = st["info"][name].get("restart_count")
            eid = L.add("change", f"{name} restarted on its own {age:.1f} min ago, restarts={rc}",
                        ref=f"container:{name}", anomalous=True)
            lines.append(f"- {name} restarted on its own {age:.1f} min ago (restarts={rc}) [{eid}]")
    return (f"Changes in last {minutes} min:\n" + "\n".join(lines)) if lines else f"No container (re)starts in the last {minutes} min."


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
    "service_overview": (service_overview, "Service rates and latency; use group_by='route' or 'client' when request volume changes to locate the source. Also lists services that went silent. Call first.", {"window": "string?", "service": "string?", "group_by": "string?"}),
    "service_operations": (service_operations, "Per-operation error % and latency for ONE service, incl. its outgoing calls to dependencies.", {"service": "string"}),
    "trace_errors": (trace_errors, "Sample recent traces through a service (optionally one operation from service_operations) and find which downstream span the errors originate from.", {"service": "string", "operation": "string?"}),
    "error_logs": (error_logs, "Recent WARN/ERROR log lines grouped by message. Optional service filter.", {"service": "string?"}),
    "container_health": (container_health, "Docker state of all containers: stopped, restarting, OOM, recently restarted, CPU starved vs its own limit / throttled, memory pressure.", {}),
    "resource_usage": (resource_usage, "Container resources; pass service to inspect discovered GC, heap and process CPU when CPU, memory or latency looks wrong.", {"service": "string?", "window": "string?"}),
    "recent_changes": (recent_changes, "Deployment history proxy: containers (re)started recently.", {}),
    "cache_health": (cache_health, "Health of the Valkey/Redis cache used by the cart service.", {}),
}

from agent.tools.observe_db import TOOLS_DB   # noqa: E402  (separate tool modules)
from agent.tools.observe_p1 import TOOLS_P1   # noqa: E402
from agent.tools.observe_p2 import TOOLS_P2   # noqa: E402
TOOLS.update(TOOLS_DB)
TOOLS.update(TOOLS_P1)
TOOLS.update(TOOLS_P2)
