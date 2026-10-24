"""Database investigation tool: db_health for the Postgres database astronomy-db behind product-catalog.
Same contract as the other tools: numbers computed in code, SHORT text summary, evidence ids [E#].
Read-only: no SQL and no direct Postgres connection; uses Prometheus (postgresql receiver + span metrics)
and docker inspect. Registered in agent/tools/observe.py via TOOLS.update(TOOLS_DB)."""
from datetime import datetime, timezone

from agent.evidence import Ledger
from agent.tools import containers, metrics

NOW = "2m"                      # current window
BASE = "15m offset 20m"         # baseline window (before a fresh incident)
ERR = 'status_code="STATUS_CODE_ERROR"'

# Postgres (astronomy-db) behind product-catalog; postgresql receiver metric names (check with scripts.discover)
DB_CONTAINER = "astronomy-db"
DB_CLIENT_SERVICES = "product-catalog|accounting"
DB_PROBE_METRICS = ("postgresql_backends", "postgresql_commits_total", "postgresql_db_size_bytes",
                    "postgresql_operations_total")
DB_SPAN_WORDS = ("select", "insert", "update", "delete", "begin", "commit", "query", "postgres", "sql", "db")
DB_STALE_S = 60
DB_SLOW_RATIO, DB_SLOW_MIN_MS = 5, 100       # p95 > 5x baseline and > 100ms
DB_ERR_HOT = 0.05
DB_CONN_HOT = 0.90                           # backends >= 90% of max_connections
DB_ROLLBACK_RATIO, DB_ROLLBACK_MIN = 3, 0.1  # rollbacks/s > 3x baseline and > 0.1/s
DB_SPIKE_RATIO = 10                          # max p95 over 5m > 10x median -> periodic spikes


def _byservice(q: str, label: str) -> dict:
    try:
        return {r["metric"].get(label, "?"): r["value"] for r in metrics.promql(q) if r["value"] is not None}
    except Exception:
        return {}


def _fmt_ms(x):
    return "n/a" if x is None or x != x else f"{x:.0f}ms"


def _sum(q: str):
    """Sum of an instant query, or None when the query fails or returns nothing."""
    try:
        vals = [r["value"] for r in metrics.promql(q) if r["value"] is not None]
    except Exception:
        return None
    return sum(vals) if vals else None


def _pick(names: set, base: str):
    """Exact metric name, else the first discovered name with that prefix (exporters add unit suffixes)."""
    return base if base in names else next((n for n in sorted(names) if n.startswith(base)), None)


def _by_span(q: str) -> dict:
    try:
        return {(r["metric"].get("service_name", "?"), r["metric"].get("span_name", "?")): r["value"]
                for r in metrics.promql(q) if r["value"] is not None}
    except Exception:
        return {}


def _slow(p95, base):
    return bool(p95 and base and p95 > DB_SLOW_RATIO * base and p95 > DB_SLOW_MIN_MS)


def db_health(L: Ledger) -> str:
    """Postgres (astronomy-db) behind product-catalog: container, metric freshness, connections, locks,
    database query latency from span metrics, and a verdict computed here in code."""
    ref = f"db_health:{DB_CONTAINER}"
    add = lambda d, anom=False, value=None, r=ref: L.add("metric", d, ref=r, value=value, anomalous=anom)
    lines = []
    down = slow = locks_bad = errors_bad = False
    seen = 0          # sources that returned data; none -> verdict 'unknown', never a false 'healthy'

    # 1. container state
    try:
        info = containers.inspect(DB_CONTAINER)
        age_min = None
        try:
            started = datetime.fromisoformat(info["started_at"][:26].rstrip("Z") + "+00:00")
            age_min = (datetime.now(timezone.utc) - started).total_seconds() / 60
        except Exception:
            pass
        down, seen = not info.get("running"), seen + 1
        anom = down or bool(info.get("oom_killed")) or bool(info.get("restart_count")) or (age_min is not None and age_min < 15)
        d = (f"{DB_CONTAINER}: status={info.get('status')}, exit={info.get('exit_code')}, "
             f"restarts={info.get('restart_count')}, oom={info.get('oom_killed')}"
             + (f", started {age_min:.1f} min ago" if age_min is not None and age_min < 15 else ""))
        lines.append(f"{d} [{L.add('container', d, ref=f'container:{DB_CONTAINER}', value=info, anomalous=anom)}]")
    except Exception:
        lines.append(f"{DB_CONTAINER} container: not available")

    # 2. metric freshness
    try:
        names = set(metrics.metric_names("postgresql"))
    except Exception:
        names = set()
    pg = {b: _pick(names, b) for b in ("postgresql_backends", "postgresql_connection_max", "postgresql_deadlocks",
                                       "postgresql_database_locks", "postgresql_rollbacks")}
    probe = next((n for n in (_pick(names, b) for b in DB_PROBE_METRICS) if n), None) or next(iter(sorted(names)), None)
    if not probe:
        lines.append("postgres metrics: not available (judge by container + query latency)")
    else:
        age, seen = _sum(f"time() - max(timestamp({probe}))"), seen + 1
        if age is None or age > DB_STALE_S:
            down = True
            d = (f"postgres metrics STALE for {age:.0f}s" if age is not None else "postgres metrics STALE (no samples in 5+ min)")
            d += " - database may be down or unreachable"
            lines.append(f"{d} [{add(d, True, age)}]")
        else:
            d = f"postgres metrics fresh ({probe} {age:.0f}s old)"
            lines.append(f"{d} [{add(d, False, age)}]")

    # 3. connections
    if pg["postgresql_backends"]:
        now, base = _sum(f"sum({pg['postgresql_backends']})"), _sum(f"sum({pg['postgresql_backends']} offset 20m)")
        mx = _sum(f"sum({pg['postgresql_connection_max']})") if pg["postgresql_connection_max"] else None
        if now is None:
            lines.append("connections: not available")
        else:
            anom = (now == 0 and (base or 0) > 0) or bool(mx and now >= DB_CONN_HOT * mx)
            d = (f"connections {now:.0f} (baseline {base:.0f})" if base is not None else f"connections {now:.0f}")
            d += f", {now / mx * 100:.0f}% of max {mx:.0f}" if mx else ""
            lines.append(f"{d} [{add(d, anom, {'now': now, 'base': base, 'max': mx})}]")
    else:
        lines.append("connections: not available")

    # 4. deadlocks / locks / rollbacks
    lock_lines = []
    if pg["postgresql_deadlocks"]:
        dl = _sum(f"sum(increase({pg['postgresql_deadlocks']}[5m]))")
        if dl is not None:
            locks_bad |= dl > 0
            d = f"deadlocks in last 5m: {dl:.0f}"
            lock_lines.append(f"{d} [{add(d, dl > 0, dl)}]")
    if pg["postgresql_database_locks"]:
        now = _byservice(f"sum by (mode)({pg['postgresql_database_locks']})", "mode")
        base = _byservice(f"sum by (mode)({pg['postgresql_database_locks']} offset 20m)", "mode")
        if now:
            new_excl = [m for m, v in now.items() if "exclusive" in m.lower() and v > 0 and not base.get(m)]
            locks_bad |= bool(new_excl)
            d = "locks by mode: " + ", ".join(f"{m}={v:.0f} (base {base.get(m, 0):.0f})" for m, v in sorted(now.items())[:6])
            d += f" - NEW exclusive locks: {', '.join(new_excl)}" if new_excl else ""
            lock_lines.append(f"{d} [{add(d, bool(new_excl), now)}]")
    if pg["postgresql_rollbacks"]:
        rb, brb = _sum(f"sum(rate({pg['postgresql_rollbacks']}[{NOW}]))"), _sum(f"sum(rate({pg['postgresql_rollbacks']}{metrics.rng(BASE)}))")
        if rb is not None:
            anom = rb > DB_ROLLBACK_MIN and rb > DB_ROLLBACK_RATIO * (brb or 0)
            d = f"rollbacks {rb:.2f}/s (baseline {(brb or 0):.2f}/s)"
            lock_lines.append(f"{d} [{add(d, anom, {'now': rb, 'base': brb})}]")
    lines += lock_lines or ["deadlocks/locks/rollbacks: not available"]

    # 5. query latency from span metrics (works without postgres metrics)
    client = f'span_kind="SPAN_KIND_CLIENT",service_name=~"{DB_CLIENT_SERVICES}"'
    bucket = lambda sel, w, by: f"sum by ({by}, le)(rate(traces_span_metrics_duration_milliseconds_bucket{{{sel}}}{metrics.rng(w)}))"
    p95q = lambda sel, w, by="service_name, span_name": f"histogram_quantile(0.95, {bucket(sel, w, by)})"
    calls = _by_span(f"sum by (service_name, span_name)(rate(traces_span_metrics_calls_total{{{client}}}[{NOW}]))")
    db_spans = [k for k, v in calls.items() if v > 0 and any(w in k[1].lower() for w in DB_SPAN_WORDS)]
    if db_spans:
        seen += 1
        errs = _by_span(f"sum by (service_name, span_name)(rate(traces_span_metrics_calls_total{{{client},{ERR}}}[{NOW}]))")
        p95, bp95 = _by_span(p95q(client, NOW)), _by_span(p95q(client, BASE))
        rows = []
        for k in db_spans:
            p, b, e = p95.get(k), bp95.get(k), errs.get(k, 0.0) / calls[k]
            rows.append(((p / b) if p and b else 0, k, p, b, e))
        rows.sort(key=lambda r: r[0], reverse=True)
        for _, (svc, name), p, b, e in rows[:3]:
            is_slow, is_err = _slow(p, b), e >= DB_ERR_HOT
            slow |= is_slow and not is_err
            errors_bad |= is_err
            d = f"db query '{name[:60]}' ({svc}) p95 {_fmt_ms(p)} (baseline {_fmt_ms(b)}), errors {e*100:.1f}%"
            lines.append(f"{d} [{add(d, is_slow or is_err, {'p95': p, 'base_p95': b, 'err': e}, f'span_metrics:{svc}:{name}')}]")
        # periodic spikes: worst 5m max of 1m-p95 vs its 5m median, over the db spans
        sub = f"{p95q(client, '1m')}[5m:30s]"
        mx, med = _by_span(f"max_over_time({sub})"), _by_span(f"quantile_over_time(0.5, {sub})")
        spikes = [(mx[k] / med[k], k) for k in db_spans if mx.get(k) and med.get(k) and mx[k] > DB_SPIKE_RATIO * med[k]]
        if spikes:
            ratio, (svc, name) = max(spikes)
            slow = True
            d = (f"latency spikes are periodic/intermittent - consistent with lock contention "
                 f"('{name[:60]}' max p95 {mx[(svc, name)]:.0f}ms = {ratio:.0f}x its 5m median)")
            lines.append(f"{d} [{add(d, True, ratio, f'span_metrics:{svc}:{name}')}]")
    else:
        lines.append("db query spans: not available (no database CLIENT spans from product-catalog/accounting)")
    server = 'span_kind="SPAN_KIND_SERVER",service_name="product-catalog"'
    sp, bsp = _by_span(p95q(server, NOW)), _by_span(p95q(server, BASE))
    for (svc, name), p in sorted(sp.items()):
        if name.endswith(("GetProduct", "ListProducts")):
            b = bsp.get((svc, name))
            d = f"user-facing: product-catalog {name.rsplit('/', 1)[-1]} p95 {_fmt_ms(p)} (baseline {_fmt_ms(b)})"
            lines.append(f"{d} [{add(d, _slow(p, b), {'p95': p, 'base_p95': b}, f'span_metrics:{svc}:{name}')}]")

    # 6. verdict (code, not the LLM)
    if down:
        v = "database DOWN"
    elif not seen:
        v = "database status unknown (no container, metric or span data)"
    elif slow or locks_bad:
        v = "database SLOW (likely lock contention)"
    elif errors_bad:
        v = "database errors"
    else:
        v = "database looks healthy"
    d = f"VERDICT: {v} ({DB_CONTAINER} Postgres behind product-catalog)"
    verdict = f"{d} [{add(d, v not in ('database looks healthy', 'database status unknown (no container, metric or span data)'))}]"
    return "\n".join(lines)[:1850] + "\n" + verdict   # keep the verdict even if the body is cut


TOOLS_DB = {
    "db_health": (db_health, "Health of the Postgres database (astronomy-db) behind product-catalog: up?, metric freshness, connections, "
                  "deadlocks/locks, database query latency vs baseline, and a verdict (down / slow-lock contention / healthy). "
                  "Use when product-catalog has errors or latency.", {}),
}
