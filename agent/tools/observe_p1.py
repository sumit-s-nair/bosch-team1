"""P1 investigation tools (part 1): dependency_latency, error_breakdown.
Same contract as the other tools: numbers computed in code, SHORT text summary, evidence ids [E#].
Merge TOOLS_P1 into your TOOLS dict:   TOOLS.update(TOOLS_P1)"""
from collections import Counter, defaultdict

from agent.evidence import Ledger
from agent.tools import metrics, traces

NOW = "2m"
BASE = "15m offset 20m"
SERVER = 'span_kind="SPAN_KIND_SERVER"'
CLIENT = 'span_kind="SPAN_KIND_CLIENT"'

# dependency_latency thresholds
GAP_DELTA_MS = 50          # client-vs-server gap must grow by at least this vs baseline to be suspicious
NET_DROP_MIN = 10          # dropped packets / errors in 5m on one container before it counts
CALLEE_SLOW_X = 3          # callee's own p95 up >3x baseline -> callee is slow, not the network

# error_breakdown thresholds
MAX_CARDINALITY = 30       # skip attributes with more distinct values than this (ids, timestamps)
MIN_ERRS = 2
MIN_RATE = 0.5
MIN_LIFT = 2.0
IGNORE_ATTRS = {"otel.library.name", "otel.library.version", "span.kind", "internal.span.format",
                "thread.id", "thread.name", "http.user_agent", "user_agent.original", "http.url",
                "net.peer.port", "net.sock.peer.port", "app.session.id", "app.user.id"}


def _ok(x):
    return x is not None and x == x


def _ms(x):
    return "n/a" if not _ok(x) else f"{x:.0f}ms"


def _p95(sel: str, window: str, by: str) -> dict:
    q = (f'histogram_quantile(0.95, sum by ({by}, le)'
         f'(rate(traces_span_metrics_duration_milliseconds_bucket{{{sel}}}[{window}])))')
    out = {}
    for r in metrics.promql(q):
        if _ok(r["value"]):
            out[tuple(r["metric"].get(k, "?") for k in by.split(", "))] = r["value"]
    return out


# ------------------------------------------------------------------ dependency_latency
def dependency_latency(L: Ledger, service: str | None = None) -> str:
    """Caller-side (CLIENT span) p95 vs callee-side (SERVER span) p95 for the same call. A growing gap means time
    is lost on the wire (network latency / packet loss). Also dropped packets and network errors per container."""
    sel_c = f'{CLIENT}' + (f',service_name="{service}"' if service else "")
    by = "service_name, span_name"
    c_now, c_base = _p95(sel_c, NOW, by), _p95(sel_c, BASE, by)
    s_now, s_base = _p95(SERVER, NOW, by), _p95(SERVER, BASE, by)
    srv_rps = {}
    for r in metrics.promql(f'sum by (service_name, span_name)(rate(traces_span_metrics_calls_total{{{SERVER}}}[{NOW}]))'):
        srv_rps[(r["metric"].get("service_name", "?"), r["metric"].get("span_name", "?"))] = r["value"] or 0.0

    scope = service or "all services"
    lines, suspects, unmatched = [f"dependency_latency for {scope} (caller wait vs callee-reported time, last {NOW}):"], 0, 0
    rows = []
    for (caller, op), cp in c_now.items():
        cands = [k for k in s_now if k[1] == op and k[0] != caller]
        if not cands:
            unmatched += 1
            continue
        callee = max(cands, key=lambda k: srv_rps.get(k, 0.0))
        sp = s_now[callee]
        gap = cp - sp
        bgap = (c_base.get((caller, op)) or 0) - (s_base.get(callee) or 0) if (caller, op) in c_base and callee in s_base else 0.0
        bsp = s_base.get(callee)
        rows.append((gap - bgap, caller, callee[0], op, cp, sp, gap, bgap, bsp))
    rows.sort(reverse=True)
    for delta, caller, callee, op, cp, sp, gap, bgap, bsp in rows[:8]:
        callee_slow = _ok(bsp) and bsp > 0 and sp / bsp > CALLEE_SLOW_X
        net = delta >= GAP_DELTA_MS and not callee_slow
        tag = ""
        if net:
            tag, suspects = "  <-- NETWORK SUSPECT (time lost between caller and callee)", suspects + 1
        elif callee_slow:
            tag = "  <-- callee itself slow (not network)"
        txt = (f"{caller} -> {callee} [{op}]: caller waits p95 {_ms(cp)}, callee reports {_ms(sp)}, "
               f"gap {_ms(gap)} (baseline gap {_ms(bgap)})")
        eid = L.add("metric", txt, ref=f"dep_latency:{caller}->{callee}:{op}", anomalous=net or callee_slow)
        lines.append(f"- {txt} [{eid}]{tag}")
    if not rows:
        lines.append("- no client/server span pairs found (service down, no traffic, or call names don't match).")
    if unmatched:
        lines.append(f"({unmatched} outgoing call(s) skipped: no matching server span name, e.g. plain HTTP clients)")

    # packet drops / errors per container
    drops, bad = {}, 0
    try:
        for r in metrics.promql('sum by (container_name, __name__)(increase({__name__=~"container_network_io_usage_(rx|tx)_(dropped|errors).*"}[5m]))'):
            if _ok(r["value"]) and r["value"] >= 1:
                n = r["metric"].get("container_name", "?")
                kind = r["metric"].get("__name__", "").replace("container_network_io_usage_", "").split("_total")[0]
                drops.setdefault(n, {})[kind] = drops.get(n, {}).get(kind, 0) + r["value"]
    except Exception:
        lines.append("Network drop metrics unavailable (check metric names with discover).")
    hot = sorted(((sum(v.values()), n, v) for n, v in drops.items()), reverse=True)[:5]
    if hot:
        lines.append("Dropped packets / network errors per container (last 5m):")
        for tot, n, v in hot:
            a = tot >= NET_DROP_MIN
            bad += a
            d = f"{n}: " + ", ".join(f"{k}={x:.0f}" for k, x in sorted(v.items()))
            lines.append(f"- {d} [{L.add('metric', d, ref=f'net_drops:{n}', anomalous=a)}]" + ("  <-- DROPS" if a else ""))
    else:
        lines.append("No dropped packets or network errors on any container in the last 5m.")

    if suspects or bad:
        lines.append("Assessment: pattern fits a network fault between the services above (latency and/or packet loss). "
                     "No allowlisted fix exists for network faults: DIAGNOSE AND ESCALATE with this evidence; do not restart containers.")
    else:
        lines.append("Assessment: no caller/callee gap or packet loss beyond baseline; network is not the likely cause.")
    return "\n".join(lines)


# ------------------------------------------------------------------ error_breakdown
def _attrs(s: dict) -> dict:
    a = dict(s.get("attrs") or s.get("tags") or {})
    if s.get("http_status") not in (None, "", 0):
        a.setdefault("http.status_code", s["http_status"])
    if s.get("rpc_status") not in (None, ""):
        a.setdefault("rpc.grpc.status_code", s["rpc_status"])
    return {k: str(v) for k, v in a.items() if v is not None and k not in IGNORE_ATTRS}


def error_breakdown(L: Ledger, service: str, operation: str | None = None, minutes: int = 5) -> str:
    """Which span attribute values (product id, route, status code, ...) the errors of one service concentrate on.
    Separates 'one product always fails' (concentrated) from 'random fraction fails' (spread evenly)."""
    plain = traces.find_spans(service, minutes=minutes, limit=200, operation=operation)
    try:
        err_only = traces.find_spans(service, minutes=minutes, limit=50, error_only=True, operation=operation)
    except Exception:
        err_only = []
    mine = [s for s in plain if s["service"] == service] or plain
    errs = list({(s["trace_id"], s["span_id"]): s
                 for s in [x for x in mine if x["error"]] + [x for x in err_only if x["service"] == service]}.values())
    scope = f"{service}{' / ' + operation if operation else ''}"
    if not mine:
        return f"No spans for '{scope}' in the last {minutes}m."
    n, ne = len(mine), sum(1 for s in mine if s["error"])
    overall = ne / n
    head = f"{scope}: {ne}/{n} sampled spans errored ({overall*100:.0f}%) in last {minutes}m."
    if not errs:
        return head + " No error spans found; errors may be rare: try another operation or check error_logs."
    lines = [head]

    by_op = Counter((s["name"], s["message"][:80] or str(_attrs(s).get("http.status_code", "error"))) for s in errs)
    lines.append("Failing operations:")
    for (name, msg), c in by_op.most_common(4):
        lines.append(f"- {name}: \"{msg}\" x{c}")

    # attribute concentration, computed on the plain sample so rates are not inflated by the error-only fetch
    tot, err = defaultdict(Counter), defaultdict(Counter)
    for s in mine:
        for k, v in _attrs(s).items():
            tot[k][v] += 1
            if s["error"]:
                err[k][v] += 1
    useful = {k for k in tot if len(tot[k]) <= MAX_CARDINALITY}
    findings = []
    for k in useful:
        k_tot, k_err = sum(tot[k].values()), sum(err[k].values())
        if not k_err:
            continue
        base = k_err / k_tot
        for v, nt in tot[k].items():
            ne_v = err[k][v]
            rate = ne_v / nt
            if ne_v >= MIN_ERRS and rate >= MIN_RATE and rate / base >= MIN_LIFT:
                findings.append((rate, ne_v, k, v, nt, ne_v / k_err))
    if not any(_attrs(s) for s in mine):
        lines.append("No span attributes available in the sampled spans (find_spans must return span tags as 'attrs').")
    elif findings:
        findings.sort(reverse=True)
        lines.append("Errors concentrate on:")
        for rate, ne_v, k, v, nt, share in findings[:5]:
            d = f"{service}: {k}={v} fails {rate*100:.0f}% ({ne_v}/{nt} spans), {share*100:.0f}% of all errors carrying this attribute"
            lines.append(f"- {d} [{L.add('trace', d, ref=f'err_attr:{service}:{k}={v}', anomalous=True)}]")
    else:
        d = f"{service}: errors ({overall*100:.0f}% of spans) are NOT tied to any single attribute value"
        lines.append(f"- {d}: intermittent/random failure, not a bad input [{L.add('trace', d, ref=f'err_spread:{service}', anomalous=overall > 0)}]")
    return "\n".join(lines)


TOOLS_P1 = {
    "dependency_latency": (dependency_latency, "Caller wait time vs callee-reported time per call, plus dropped packets per container. Detects network latency / packet loss between services. Ends in diagnose+escalate (no fix).", {"service": "string?"}),
    "error_breakdown": (error_breakdown, "Which attribute values (product id, route, status code) one service's errors concentrate on; tells 'one bad input' from 'random intermittent'.", {"service": "string", "operation": "string?"}),
}
