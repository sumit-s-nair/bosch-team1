"""P2/P3 investigation tools in one file: logs_for_trace, get_trace, queue_health.  Tests are at the bottom.
Same contract as the other tools: numbers computed in code, SHORT text summary, evidence ids [E#].
Read-only. Register later with:  TOOLS.update(TOOLS_P2)
Run the tests:  python -m pytest agent/tools/observe_p2.py -v"""
import re
from collections import Counter, defaultdict

from agent import config, flagguard
from agent.evidence import Ledger
from agent.tools import containers, http, logs, metrics, traces

HEX_ID = re.compile(r"^[0-9a-fA-F]{16,32}$")
TRACE_ID_FIELDS = ("traceId", "traceID", "trace_id")
WARN_PLUS = {"WARN", "WARNING", "ERROR", "FATAL", "CRITICAL"}
MAX_LOG_LINES, MAX_WARN_LINES = 25, 10
MAX_TREE_LINES, MAX_DEPTH = 40, 8
LAG_HIGH = 100                        # records behind before a consumer group counts as lagging
STALE_S = 60                          # metric not updated for this long -> broker/consumer likely down
KAFKA_CONTAINERS = ("kafka", "accounting", "fraud-detection")   # broker + the demo's two consumers


def _ms(x):
    return f"{x:.0f}ms"


# ------------------------------------------------------------------ logs_for_trace
def logs_for_trace(L: Ledger, trace_id: str, minutes: int = 120) -> str:
    """Every log line carrying one trace id, across services, oldest first. Ties a log to a failing trace."""
    trace_id = (trace_id or "").strip().lower()
    if not HEX_ID.match(trace_id):
        return f"'{trace_id}' is not a valid trace id (expected 16-32 hex characters)."
    sf, lf, bf = logs.fields()
    tf = logs.time_field()
    query = {"size": 200, "sort": [{tf: {"order": "asc", "unmapped_type": "date"}}],
             "query": {"bool": {"must": [{"range": {tf: {"gte": f"now-{max(1, min(int(minutes), 1440))}m"}}}],
                                "should": [{"match_phrase": {f: trace_id}} for f in TRACE_ID_FIELDS],
                                "minimum_should_match": 1}}}
    hits = http.post(f"{config.opensearch_url()}/{logs.index()}/_search", query).get("hits", {}).get("hits", [])
    if not hits:
        return (f"No log lines carry trace id {trace_id} in the last {minutes}m "
                "(logs may lack trace context, or the trace is older than this window).")
    rows = []
    for h in hits:
        src = h.get("_source", {})
        svc = logs.value_at(src, sf) or "?"
        if svc in logs.NOISE_SERVICES:          # includes flagd: its logs would reveal flag state
            continue
        rows.append({"id": h.get("_id"), "ts": str(logs.value_at(src, tf) or ""), "service": svc,
                     "sev": str(logs.value_at(src, lf) or "").upper(),
                     "body": flagguard.scrub(str(logs.value_at(src, bf))[:200])})
    if not rows:
        return f"No application log lines carry trace id {trace_id} in the last {minutes}m."
    warn = [r for r in rows if r["sev"] in WARN_PLUS]
    shown = warn[:MAX_WARN_LINES]
    shown += [r for r in rows if r not in shown][:MAX_LOG_LINES - len(shown)]
    shown.sort(key=lambda r: r["ts"])
    svcs = Counter(r["service"] for r in rows)
    head = (f"{len(rows)} log lines for trace {trace_id} across {len(svcs)} services "
            f"({', '.join(f'{s} x{c}' for s, c in svcs.most_common())}); {len(warn)} are WARN+.")
    out = [head]
    for r in shown:
        if r["sev"] in WARN_PLUS:
            eid = L.add("log", f"{r['service']} {r['sev']} (trace {trace_id}): {r['body']}", ref=f"log:{r['id']}", anomalous=True)
            out.append(f"- {r['service']} [{r['sev']}] {r['body']} [{eid}]")
        else:
            out.append(f"- {r['service']} [{r['sev'] or '-'}] {r['body']}")
    if len(rows) > len(shown):
        out.append(f"... {len(rows) - len(shown)} more lines not shown")
    return "\n".join(out)


# ------------------------------------------------------------------ get_trace
def get_trace(L: Ledger, trace_id: str) -> str:
    """Compact span tree of ONE trace: which hop failed (origin) and which span spent the most own time."""
    trace_id = (trace_id or "").strip().lower()
    if not HEX_ID.match(trace_id):
        return f"'{trace_id}' is not a valid trace id (expected 16-32 hex characters)."
    try:
        spans = traces.get_trace(trace_id)
    except Exception as e:
        return f"Could not fetch trace {trace_id}: {e}"
    spans = [dict(x, message=flagguard.scrub(x["message"])) for x in (spans or []) if not flagguard.is_flag_span(x)]
    if not spans:
        return f"Trace {trace_id} not found (expired, or not sampled)."
    ids = {s["span_id"] for s in spans}
    kids = defaultdict(list)
    for s in spans:
        kids[s["parent_id"] if s["parent_id"] in ids else None].append(s)
    for v in kids.values():
        v.sort(key=lambda s: s.get("start_ns", 0))
    err_parents = {s["parent_id"] for s in spans if s["error"]}
    origins = {s["span_id"] for s in spans if s["error"] and s["span_id"] not in err_parents}
    self_ms = {s["span_id"]: max(0.0, s["duration_ms"] - sum(c["duration_ms"] for c in kids.get(s["span_id"], [])))
               for s in spans}
    tree, seen = [], set()

    def walk(s, depth):
        if s["span_id"] in seen or len(tree) >= MAX_TREE_LINES:
            return
        seen.add(s["span_id"])
        mark = ""
        if s["error"]:
            mark = f"  ERROR: {s['message'][:80] or 'status=error'}" + ("  <-- origin" if s["span_id"] in origins else "")
        tree.append(f"{'  ' * depth}{s['service']} / {s['name']} {_ms(s['duration_ms'])}{mark}")
        if depth < MAX_DEPTH:
            for c in kids.get(s["span_id"], []):
                walk(c, depth + 1)

    for r in kids[None]:
        walk(r, 0)
    n_err = sum(1 for s in spans if s["error"])
    out = [f"Trace {trace_id}: {len(spans)} spans across {len({s['service'] for s in spans})} services, {n_err} errored."] + tree
    if len(seen) < len(spans):
        out.append(f"... {len(spans) - len(seen)} spans not shown")
    for s in spans:
        if s["span_id"] in origins:
            eid = L.add("trace", f"trace {trace_id}: error origin {s['service']} '{s['name']}': {s['message'][:120] or 'status=error'}",
                        ref=f"trace:{trace_id}", anomalous=True)
            out.append(f"Failure origin: {s['service']} / {s['name']} [{eid}]")
    top = max(spans, key=lambda s: self_ms[s["span_id"]])
    eid = L.add("trace", f"trace {trace_id}: most own time in {top['service']} '{top['name']}' {_ms(self_ms[top['span_id']])}",
                ref=f"trace:{trace_id}")
    out.append(f"Most own time (excluding children): {top['service']} / {top['name']} {_ms(self_ms[top['span_id']])} [{eid}]")
    return "\n".join(out)


# ------------------------------------------------------------------ queue_health
def queue_health(L: Ledger) -> str:
    """Kafka: broker/consumer containers, consumer-group lag (and whether it is growing), group members. Full mode only."""
    out, bad = [], 0
    try:
        kc = [c for c in containers.list_containers() if any(k in c["name"] for k in KAFKA_CONTAINERS)]
    except Exception:
        kc = []
    for c in kc:
        if c.get("state") != "running":
            d = f"{c['name']} is {c.get('state')} ({c.get('status')})"
            bad += 1
            out.append(f"- {d} [{L.add('container', d, ref='container:' + c['name'], anomalous=True)}]")
    try:
        names = metrics.metric_names("kafka")
    except Exception:
        names = []
    lag_m = next((n for n in names if "lag" in n and "max" not in n), None) or next((n for n in names if "lag" in n), None)
    mem_m = next((n for n in names if "group_members" in n), None)
    if not names and not kc:
        return ("No Kafka metrics or containers found: Kafka is not part of this run (minimal mode) or not instrumented. "
                "This is not evidence of a fault.")
    if lag_m:
        by = "group, topic"
        lag = {tuple(r["metric"].get(k, "?") for k in ("group", "topic")): r["value"] or 0.0
               for r in metrics.promql(f"sum by ({by})({lag_m})")}
        grow = {tuple(r["metric"].get(k, "?") for k in ("group", "topic")): r["value"] or 0.0
                for r in metrics.promql(f"sum by ({by})({lag_m}) - sum by ({by})({lag_m} offset 5m)")}
        age = (metrics.promql(f"time() - max(timestamp({lag_m}))") or [{"value": None}])[0]["value"]
        if age is not None and age > STALE_S:
            bad += 1
            d = f"kafka lag metrics stale for {age:.0f}s: broker or consumer may be down"
            out.append(f"- {d} [{L.add('metric', d, ref='kafka:stale', anomalous=True)}]")
        for k, v in sorted(lag.items(), key=lambda x: -x[1])[:5]:
            g = grow.get(k, 0.0)
            hot = v >= LAG_HIGH and g > 0
            bad += hot
            d = f"consumer group {k[0]} topic {k[1]}: lag {v:.0f} records ({g:+.0f} in 5m)"
            out.append(f"- {d} [{L.add('metric', d, ref=f'kafka_lag:{k[0]}:{k[1]}', anomalous=hot)}]" + ("  <-- LAGGING AND GROWING" if hot else ""))
    else:
        out.append("- no consumer-lag metric exposed (kafka metrics present: " + ", ".join(names[:4]) + ")")
    if mem_m:
        for r in metrics.promql(f"sum by (group)({mem_m})"):
            if (r["value"] or 0) == 0:
                bad += 1
                d = f"consumer group {r['metric'].get('group', '?')} has 0 members: no live consumer"
                out.append(f"- {d} [{L.add('metric', d, ref='kafka_members:' + r['metric'].get('group', '?'), anomalous=True)}]")
    return (f"Kafka: {bad} problem(s) found." if bad else "Kafka: no lag, dead consumers or stopped containers found.") + ("\n" + "\n".join(out) if out else "")


TOOLS_P2 = {
    "logs_for_trace": (logs_for_trace, "Every log line (all services) carrying one trace id. Use a trace id from trace_errors to tie logs to a failing trace.", {"trace_id": "string"}),
    "get_trace": (get_trace, "Compact span tree of one trace id: which hop failed (origin) and which span is slowest.", {"trace_id": "string"}),
    "queue_health": (queue_health, "Kafka consumer lag, dead consumers and stopped broker/consumer containers (full mode only).", {}),
}


# ====================================================================== tests (offline, backends faked)
# run: python -m pytest agent/tools/observe_p2.py -v
TID = "0af7651916cd43dd8448eb211c80319c"


def _span(sid, svc, name, parent=None, err=False, msg="", dur=10.0, start=0):
    return {"trace_id": TID, "span_id": sid, "parent_id": parent, "service": svc, "name": name, "kind": "2",
            "error": err, "message": msg, "duration_ms": dur, "start_ns": start}


def _fake_logs(monkeypatch, hits, seen=None):
    monkeypatch.setattr(logs, "fields", lambda: ("resource.service.name", "severityText", "body"))
    monkeypatch.setattr(logs, "time_field", lambda: "@timestamp")
    monkeypatch.setattr(logs, "index", lambda: "otel-logs-*")
    monkeypatch.setattr(config, "opensearch_url", lambda: "http://x")

    def post(url, body):
        if seen is not None:
            seen.append(body)
        return {"hits": {"hits": hits}}
    monkeypatch.setattr(http, "post", post)


def _hit(i, svc, sev, body):
    return {"_id": f"d{i}", "_source": {"@timestamp": f"2026-01-01T00:00:{i:02d}Z", "resource.service.name": svc,
                                        "severityText": sev, "body": body}}


def test_logs_for_trace_flags_warn_lines_and_queries_trace_id(monkeypatch):
    seen = []
    _fake_logs(monkeypatch, [_hit(1, "checkout", "INFO", "placing order"),
                             _hit(2, "payment", "ERROR", "Invalid token"),
                             _hit(3, "checkout", "WARN", "payment failed")], seen)
    L = Ledger()
    out = logs_for_trace(L, TID.upper())
    assert "3 log lines" in out and "2 are WARN+" in out and "payment [ERROR] Invalid token" in out
    assert sum(e["anomalous"] for e in L.items.values()) == 2
    assert {"match_phrase": {"traceId": TID}} in seen[0]["query"]["bool"]["should"]


def test_logs_for_trace_no_hits_and_bad_id(monkeypatch):
    _fake_logs(monkeypatch, [])
    assert "No log lines carry trace id" in logs_for_trace(Ledger(), TID)
    monkeypatch.setattr(http, "post", lambda *a: (_ for _ in ()).throw(AssertionError("must not query")))
    assert "not a valid trace id" in logs_for_trace(Ledger(), "x\" OR 1=1")


def test_get_trace_finds_origin_and_tree(monkeypatch):
    sp = [_span("a", "checkout", "PlaceOrder", err=True, dur=100, start=1),
          _span("b", "checkout", "Charge", parent="a", err=True, dur=90, start=2),
          _span("c", "payment", "charge", parent="b", err=True, msg="Invalid token", dur=80, start=3),
          _span("d", "cart", "GetCart", parent="a", dur=5, start=4)]
    monkeypatch.setattr(traces, "get_trace", lambda t: sp)
    L = Ledger()
    out = get_trace(L, TID)
    assert "4 spans across 3 services, 3 errored" in out
    assert "    payment / charge 80ms  ERROR: Invalid token  <-- origin" in out      # indented under Charge
    assert "Failure origin: payment / charge" in out and "Most own time" in out
    assert [e["anomalous"] for e in L.items.values()].count(True) == 1


def test_get_trace_missing_and_bad_id(monkeypatch):
    monkeypatch.setattr(traces, "get_trace", lambda t: [])
    assert "not found" in get_trace(Ledger(), TID)
    assert "not a valid trace id" in get_trace(Ledger(), "nope")


def _kafka(monkeypatch, lag=0.0, grow=0.0, members=2.0, stale=1.0, names=("kafka_consumer_group_lag", "kafka_consumer_group_members"),
           cont=()):
    monkeypatch.setattr(metrics, "metric_names", lambda m="": list(names))
    monkeypatch.setattr(containers, "list_containers", lambda: list(cont))

    def promql(q):
        if "offset 5m" in q:
            return [{"metric": {"group": "accounting", "topic": "orders"}, "value": grow}]
        if "timestamp(" in q:
            return [{"metric": {}, "value": stale}]
        if "group_members" in q:
            return [{"metric": {"group": "accounting"}, "value": members}]
        return [{"metric": {"group": "accounting", "topic": "orders"}, "value": lag}]
    monkeypatch.setattr(metrics, "promql", promql)


def test_queue_health_lagging_and_growing(monkeypatch):
    _kafka(monkeypatch, lag=5000, grow=800)
    L = Ledger()
    out = queue_health(L)
    assert "LAGGING AND GROWING" in out and "lag 5000" in out and any(e["anomalous"] for e in L.items.values())


def test_queue_health_dead_consumer_and_stopped_container(monkeypatch):
    _kafka(monkeypatch, members=0.0, cont=[{"name": "accounting", "state": "exited", "status": "Exited (1)"}])
    out = queue_health(Ledger())
    assert "0 members" in out and "accounting is exited" in out


def test_queue_health_stale_metrics(monkeypatch):
    _kafka(monkeypatch, stale=300.0)
    assert "stale for 300s" in queue_health(Ledger())


def test_queue_health_healthy_and_absent(monkeypatch):
    _kafka(monkeypatch, lag=3, grow=-2)
    assert "no lag, dead consumers" in queue_health(Ledger())
    _kafka(monkeypatch, names=())
    assert "not part of this run" in queue_health(Ledger())


def test_tools_dict_shape():
    assert TOOLS_P2["logs_for_trace"][2] == TOOLS_P2["get_trace"][2] == {"trace_id": "string"}
    assert TOOLS_P2["queue_health"][2] == {}


if __name__ == "__main__":
    import sys
    import pytest
    sys.exit(pytest.main([__file__, "-v"]))
