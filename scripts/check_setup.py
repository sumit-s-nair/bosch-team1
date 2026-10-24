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
    # test the configured provider itself (no silent fallback), so a bad key or blocked network shows up here
    m = llm._hosted(msgs, [tool], False, 60) if config.LLM_PROVIDER == "openai" else llm.chat(msgs, tools=[tool], think=False)
    calls = [c["function"]["name"] for c in m.get("tool_calls") or []]
    if "container_health" not in calls:
        raise RuntimeError(f"model answered without the tool call (content={m.get('content', '')[:80]!r})")
    model = config.LLM_HOSTED_MODEL if config.LLM_PROVIDER == "openai" else config.LLM_MODEL
    return {"provider": config.LLM_PROVIDER, "model": model, "tool_calling": True}


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
