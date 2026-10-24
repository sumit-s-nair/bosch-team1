"""Read-only integration check:  python -m scripts.check_setup [--skip-llm]"""
import argparse
import json
import sys

sys.path.insert(0, ".")
from agent import config, llm
from agent.tools import containers, logs, metrics, traces


def c_containers():
    rows = containers.list_containers()
    if not rows:
        raise RuntimeError("no containers on the demo network - is the demo up?")
    return {"count": len(rows), "not_running": [r["name"] for r in rows if r["state"] != "running"]}


def c_prom():
    names = metrics.metric_names("span_metrics")
    if not names:
        raise RuntimeError("no span metrics yet - wait for load")
    return {"span_metrics": names[:4]}


def c_jaeger():
    names = traces.services()
    if not names:
        raise RuntimeError("no trace services - wait for load")
    return {"api": traces._api()[0], "services": len(names)}


def c_logs():
    hits = logs.search_logs(minutes=15, size=1)
    return {"fields": logs.fields(), "url": config.opensearch_url(), "recent_docs": len(hits)}


def c_llm():
    tool = {"type": "function", "function": {"name": "container_health", "description": "Container states",
                                             "parameters": {"type": "object", "properties": {}, "required": []}}}
    msgs = [{"role": "user", "content": "Connection test: call container_health with no arguments."}]
    report, ok_any = {}, False
    for pr in config.llm_chain():          # test each provider on its own, so a bad key or busy model shows up
        try:
            m = llm._ollama(msgs, [tool], False, False, 120) if pr["name"] == "local" else llm._hosted(msgs, [tool], False, 60, pr)
            good = "container_health" in [c["function"]["name"] for c in m.get("tool_calls") or []]
            report[f"{pr['name']}:{pr['model']}"] = "OK" if good else "answered without the tool call"
            ok_any |= good
        except Exception as e:
            report[f"{pr['name']}:{pr['model']}"] = f"FAILED {str(e)[:120]}"
    if not ok_any:
        raise RuntimeError(json.dumps(report))
    return report


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--skip-llm", action="store_true")
    a = ap.parse_args()
    checks = [("Containers", c_containers), ("Prometheus", c_prom), ("Jaeger", c_jaeger), ("OpenSearch logs", c_logs)]
    if not a.skip_llm:
        checks.append(("LLM tool calling", c_llm))
    ok = True
    for name, fn in checks:
        try:
            print(f"[ OK ] {name}: {json.dumps(fn())}")
        except Exception as e:
            ok = False
            print(f"[FAIL] {name}: {type(e).__name__}: {e}")
    print("\nALL GOOD" if ok else "\nSome checks failed (see above)")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
