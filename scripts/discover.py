"""Read-only discovery of metric names/labels, trace API, log field mappings -> runs/discovery.json.
Never reads feature-flag configuration.   python -m scripts.discover
"""
import json
import os
import sys

sys.path.insert(0, ".")
from agent import config
from agent.tools import containers, http, logs, metrics, traces

out = {}


def grab(k, fn):
    try:
        out[k] = fn()
    except Exception as e:
        out[k] = {"error": f"{type(e).__name__}: {str(e)[:400]}"}


def series(name):
    r = http.get(f"{config.prometheus_url()}/api/v1/series", {"match[]": name})["data"]
    return {"count": len(r), "sample_labels": r[:3]}


grab("metric_names", metrics.metric_names)
for m in ["traces_span_metrics_calls_total", "traces_span_metrics_duration_milliseconds_bucket",
          "container_cpu_utilization_ratio", "container_memory_percent_ratio", "container_memory_usage_total_bytes"]:
    grab(f"series:{m}", lambda m=m: series(m))
grab("jaeger_api", lambda: list(traces._api()))
grab("jaeger_services", traces.services)
grab("log_fields_resolved", logs.fields)
grab("log_sample", lambda: logs.search_logs(minutes=10, size=3))
grab("containers", containers.list_containers)

os.makedirs("runs", exist_ok=True)
with open("runs/discovery.json", "w") as f:
    json.dump(out, f, indent=1, default=str)
print("wrote runs/discovery.json:",
      {k: ("ERROR" if isinstance(v, dict) and "error" in v else len(v) if isinstance(v, (list, dict)) else v) for k, v in out.items()})
