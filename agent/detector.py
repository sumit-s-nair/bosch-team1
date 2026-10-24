"""Rule-based alerting on span metrics + container state (no LLM). Mirrors an error-rate / latency alert."""
import time
import math
from agent.tools import observe
from agent.tools import containers

ERR_THRESHOLD = 0.05        # 5% server errors
LAT_FACTOR = 3.0            # p95 > 3x baseline ...
LAT_MIN_MS = 300            # ... and above 300ms
LAT_ABS_MS = 1500           # fallback when no baseline exists yet
MIN_RPS = 0.005             # quiet services (checkout, payment: ~2 calls/min) must still be able to alert ...
MIN_ERRORS = 2              # ... so instead require at least this many actual errors in the 2-minute window
WINDOW_S = 120
OP_WINDOW = "5m"            # per-operation errors: longer window so rare calls (EmptyCart at checkout) are counted
OP_ERR_THRESHOLD = 0.10
OP_MIN_ERRORS = 2
SPIKE_MIN_RPS = 1.0         # traffic spike: > observe.TRAFFIC_SPIKE_RATIO (3x) baseline and at least this many rps
BASELINE_SPAN_S = 35 * 60   # the baseline window ends 20m ago and is 15m long; a younger demo has a partial baseline


def check(strict=False, original_alerts=None) -> list[dict]:
    alerts = []
    health = observe.service_health_raw(strict=True) if strict else observe.service_health_raw()
    try:
        cs = containers.list_containers()
    except Exception as e:          # docker briefly unavailable: metric-based alerts still count (not when verifying)
        if strict:
            raise
        print(f"(container checks skipped this poll: {str(e)[:100]})")
        cs = None
    if strict:
        def observed(v):
            return (isinstance(v.get("rps"), (int, float)) and math.isfinite(v["rps"]) and v["rps"] > 0
                    and all(isinstance(v.get(k), (int, float)) and math.isfinite(v[k]) for k in ("err_rate", "p95_ms")))
        if not health or not any(observed(v) for v in health.values()):
            raise ValueError("Recovery unverified: no active request/error/latency telemetry")
        if not cs:
            raise ValueError("Recovery unverified: no demo containers observed")
        for alert in original_alerts or []:
            service = alert.get("service")
            if alert.get("type") == "container":
                if not any(c["name"] == service for c in cs):
                    raise ValueError(f"Recovery unverified: container {service} is missing")
            elif alert.get("type") not in ("manual", "memory_leak", "cpu_starved", "container_restarted") \
                    and not observed(health.get(service, {})):
                raise ValueError(f"Recovery unverified: active telemetry missing for {service}")
    spikes = []
    for s, v in health.items():
        errors_in_window = (v["err_rate"] or 0) * v["rps"] * WINDOW_S
        if v["rps"] >= MIN_RPS and v["err_rate"] is not None and v["err_rate"] >= ERR_THRESHOLD \
                and errors_in_window >= MIN_ERRORS and v["err_rate"] > 2 * (v["base_err_rate"] or 0):
            alerts.append({"type": "error_rate", "service": s, "value": round(v["err_rate"], 3),
                           "baseline": v["base_err_rate"]})
        b = v["base_p95_ms"]
        p = v["p95_ms"]
        if p and p == p:
            if b and b == b and p > max(LAT_FACTOR * b, LAT_MIN_MS):
                alerts.append({"type": "latency", "service": s, "value": round(p), "baseline": round(b)})
            elif not (b and b == b) and p > LAT_ABS_MS:   # no baseline yet: absolute fallback
                alerts.append({"type": "latency", "service": s, "value": round(p), "baseline": None})
        if v["base_rps"] and v["base_rps"] > 0.2 and v["rps"] < 0.2 * v["base_rps"]:
            alerts.append({"type": "traffic_drop", "service": s, "value": round(v["rps"], 2),
                           "baseline": round(v["base_rps"], 2)})
        if v["base_rps"] and v["base_rps"] > 0.2 and v["rps"] >= SPIKE_MIN_RPS and v["rps"] > observe.TRAFFIC_SPIKE_RATIO * v["base_rps"]:
            spikes.append({"type": "traffic_spike", "service": s, "value": round(v["rps"], 2),
                           "baseline": round(v["base_rps"], 2)})
    if spikes and not _baseline_partial():
        alerts += spikes
    alerted = {a["service"] for a in alerts if a["type"] == "error_rate"}
    alerts += [a for a in operation_errors() if a["service"] not in alerted]
    if cs is not None:
        for c in cs:
            if c["state"] != "running" or "unhealthy" in c["status"] or "Restarting" in c["status"]:
                alerts.append({"type": "container", "service": c["name"], "value": c["status"]})
        try:
            alerts += restart_alerts(cs)
        except Exception:
            pass
    alerts += resource_alerts()
    return alerts


def _baseline_partial() -> bool:
    """True while the whole demo started less than 35 min ago: its baseline window is only partly filled, so the
    baseline rate reads low and normal traffic would look like a spike. Only asked when a spike is seen."""
    try:
        _, stack, now = observe._starts()
        return stack is not None and now.timestamp() - stack < BASELINE_SPAN_S
    except Exception:
        return False


_UNIT_S = {"second": 1, "minute": 60, "hour": 3600, "day": 86400, "week": 604800}


def _uptime_s(status: str):
    """'Up 8 seconds (health: starting)' -> 8; 'Up About a minute' -> 60; not up -> None."""
    import re
    m = re.match(r"Up (Less than a second|About an? (\w+)|(\d+) (\w+?)s?\b)", status or "")
    if not m:
        return None
    if m.group(1).startswith("Less"):
        return 0
    if m.group(2):
        return _UNIT_S.get(m.group(2), 3600)
    return int(m.group(3)) * _UNIT_S.get(m.group(4), 1)


def restart_alerts(cs: list | None = None) -> list[dict]:
    """A container that just (re)started on its own while the rest of the demo has been up for a while: a crash
    or crash loop. Cheap (docker ps only); restarts done by our own Action API are not alerts."""
    rows = [(c["name"], _uptime_s(c["status"])) for c in (cs if cs is not None else containers.list_containers())]
    ups = sorted(u for _, u in rows if u is not None)
    if len(ups) < 5 or ups[len(ups) // 2] < 15 * 60:        # the demo itself just started: nothing to compare with
        return []
    api = observe._api_restarts()
    out = []
    for name, up in rows:
        if up is not None and up < 300 and not observe._restarted_by_us(name, time.time() - up, api):
            out.append({"type": "container_restarted", "service": name, "value": f"up {up}s while the demo has been up longer"})
    return out


def operation_errors() -> list[dict]:
    """One operation failing while the service as a whole looks fine (e.g. only EmptyCart fails)."""
    q = lambda extra, w: (f'sum by (service_name, span_name)(increase(traces_span_metrics_calls_total'
                          f'{{{observe.SERVER}{extra}}}{observe.metrics.rng(w)}))')
    key = lambda r: (r["metric"].get("service_name"), r["metric"].get("span_name"))
    try:
        tot = {key(r): r["value"] for r in observe.metrics.promql(q("", OP_WINDOW)) if r["value"]}
        err = {key(r): r["value"] for r in observe.metrics.promql(q("," + observe.ERR, OP_WINDOW)) if r["value"]}
        btot = {key(r): r["value"] for r in observe.metrics.promql(q("", observe.BASE)) if r["value"]}
        berr = {key(r): r["value"] for r in observe.metrics.promql(q("," + observe.ERR, observe.BASE)) if r["value"]}
    except Exception:
        return []
    out = []
    for (svc, op), e in err.items():
        if svc in observe.DEMO_SERVICES_SKIP or observe.flagguard.is_flag_text(op) or not tot.get((svc, op)):
            continue
        rate = e / tot[(svc, op)]
        base = berr.get((svc, op), 0) / btot[(svc, op)] if btot.get((svc, op)) else 0.0
        if e >= OP_MIN_ERRORS and rate >= OP_ERR_THRESHOLD and rate > 2 * base:
            out.append({"type": "operation_errors", "service": svc, "operation": op, "value": round(rate, 3),
                        "baseline": round(base, 3)})
    return out


def resource_alerts() -> list[dict]:
    """Memory about to run out (sustained growth near the limit) or CPU starved / throttled. Same rules as the tools."""
    memp = observe._byservice("container_memory_percent_ratio", "container_name")
    ttl = observe._byservice(observe.MEM_TTL_Q, "container_name")
    throttled = observe._byservice(observe.THROTTLE_Q, "container_name")
    cpu = observe._byservice("container_cpu_utilization_ratio", "container_name")
    skip = observe.NOT_A_CULPRIT_FOR_RESOURCES
    leak_cand = [n for n, t in ttl.items() if t < observe.LEAK_TTL_MIN and memp.get(n, 0) >= observe.LEAK_MIN_MEM_PCT and n not in skip]
    cpu_cand = [n for n in set(throttled) | set(cpu) if n not in skip
                and (throttled.get(n, 0) > observe.THROTTLE_HOT or cpu.get(n, 0) > 50)]
    if not leak_cand and not cpu_cand:
        return []
    out, now = [], time.time()
    for n in sorted(set(leak_cand) | set(cpu_cand)):
        try:
            info = containers.inspect(n)
        except Exception:
            continue
        if not info.get("running"):
            continue
        if n in leak_cand:
            try:
                started = observe.datetime.fromisoformat(info["started_at"][:26].rstrip("Z") + "+00:00").timestamp()
            except Exception:
                started = None
            if started is None or now - started >= observe.WARMUP_MIN * 60:
                out.append({"type": "memory_leak", "service": n, "value": f"{memp[n]:.0f}% of limit, full in ~{ttl[n]:.0f} min"})
        if n in cpu_cand:
            limit = info.get("cpu_limit_cores")
            of_limit = cpu.get(n, 0) / (limit * 100) * 100 if limit else None
            if throttled.get(n, 0) > observe.THROTTLE_HOT or (of_limit is not None and of_limit > observe.CPU_LIMIT_HOT_PCT):
                out.append({"type": "cpu_starved", "service": n,
                            "value": f"throttled {throttled.get(n, 0) * 100:.0f}%" + (f", {of_limit:.0f}% of its CPU limit" if of_limit else "")})
    return out


def key(a: dict) -> tuple:
    return (a["type"], a["service"], a.get("operation"))


def label(a: dict) -> str:
    return f"{a['type']}:{a['service']}" + (f"/{a['operation']}" if a.get("operation") else "")


def wait_for_alert(poll_s: int = 10, confirm: int = 2) -> list[dict]:
    """Block until the same alert key fires on `confirm` consecutive polls."""
    streak: dict = {}
    while True:
        try:
            alerts = check()
        except Exception as e:          # a failed poll (docker/prometheus hiccup) must not stop the watch
            print(time.strftime("%H:%M:%S"), f"poll failed ({type(e).__name__}: {str(e)[:120]}), retrying")
            time.sleep(poll_s)
            continue
        keys = {key(a) for a in alerts}
        streak = {k: streak.get(k, 0) + 1 for k in keys}
        firing = [a for a in alerts if streak[key(a)] >= confirm]
        print(time.strftime("%H:%M:%S"), "alerts:", [label(a) for a in alerts] or "none")
        if firing:
            return firing
        time.sleep(poll_s)
