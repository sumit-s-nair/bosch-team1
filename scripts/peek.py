"""Live view of what the detector sees, per service (no LLM).   python -m scripts.peek [service]
Shows current vs baseline request rate, error rate and p95, raw error-span counts over 5 min, and which alerts fire."""
import sys

sys.path.insert(0, ".")
from agent import detector                       # noqa: E402
from agent.tools import metrics, observe         # noqa: E402

only = sys.argv[1] if len(sys.argv) > 1 else None
h = observe.service_health_raw()
errs = {r["metric"].get("service_name"): r["value"] for r in metrics.promql(
    'sum by (service_name)(increase(traces_span_metrics_calls_total{status_code="STATUS_CODE_ERROR"}[5m]))')}
pct = lambda x: "  n/a" if x is None else f"{x * 100:5.1f}%"
ms = lambda x: "  n/a" if x is None or x != x else f"{x:6.0f}ms"
print(f"{'service':16} {'rps':>6} {'base':>6} {'err%':>7} {'base':>7} {'p95':>8} {'base':>8} {'err spans 5m':>13}")
for s, v in sorted(h.items(), key=lambda kv: -(kv[1]['err_rate'] or 0)):
    if only and s != only:
        continue
    print(f"{s:16} {v['rps']:6.2f} {(v['base_rps'] or 0):6.2f} {pct(v['err_rate']):>7} {pct(v['base_err_rate']):>7} "
          f"{ms(v['p95_ms']):>8} {ms(v['base_p95_ms']):>8} {errs.get(s, 0):13.0f}")
print("\nalerts now:", [f"{a['type']}:{a['service']}" for a in detector.check()] or "none")
