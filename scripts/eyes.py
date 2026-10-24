"""Run every investigation tool against the live demo (no LLM) and print what the agent would see.
  python -m scripts.eyes                  # all tools, focus service picked from the worst anomaly
  python -m scripts.eyes checkout         # focus per-service tools on 'checkout'
Also saves runs/eyes-<time>.txt
"""
import os
import sys
import time

sys.path.insert(0, ".")
from agent import detector
from agent.tools import observe
from agent.tools import observe_db
from agent.evidence import Ledger

L = Ledger()
buf = []


def show(title, fn, *args):
    t = time.time()
    try:
        out = fn(L, *args)
    except Exception as e:
        out = f"ERROR {type(e).__name__}: {e}"
    block = f"\n===== {title} ({time.time()-t:.1f}s) =====\n{out}"
    print(block)
    buf.append(block)


alerts = detector.check()
print("ALERTS:", alerts or "none")
buf.append(f"ALERTS: {alerts}")

focus = sys.argv[1] if len(sys.argv) > 1 else None
if not focus:
    svc_alerts = [a for a in alerts if a["type"] != "container"]
    focus = svc_alerts[0]["service"] if svc_alerts else "frontend"

show("service_overview", observe.service_overview)
show("container_health", observe.container_health)
show("recent_changes", observe.recent_changes)
show("resource_usage", observe.resource_usage)
show("cache_health", observe.cache_health)
show("db_health", observe_db.db_health)
show(f"service_operations({focus})", observe.service_operations, focus)
show(f"trace_errors({focus})", observe.trace_errors, focus)
show("error_logs(all)", observe.error_logs)
show(f"error_logs({focus})", observe.error_logs, focus)

os.makedirs("runs", exist_ok=True)
path = f"runs/eyes-{time.strftime('%H%M%S')}.txt"
open(path, "w", encoding="utf-8").write("\n".join(buf))
print(f"\nSaved {path} ({len(L.items)} evidence items)")
