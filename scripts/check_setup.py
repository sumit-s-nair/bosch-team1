"""Checkpoint 0 sanity check: demo up, telemetry readable, LLM key works.
Run from repo root:  python -m scripts.check_setup
"""
import sys
sys.path.insert(0, ".")

from agent import config
from agent.tools import containers, logs, metrics, traces

ok_all = True


def check(name, fn):
    global ok_all
    try:
        msg = fn()
        print(f"[ OK ] {name}: {msg}")
    except Exception as e:
        ok_all = False
        print(f"[FAIL] {name}: {type(e).__name__}: {e}")


def c_containers():
    cs = containers.list_containers()
    bad = [c["name"] for c in cs if c["state"] != "running"]
    if not cs:
        raise RuntimeError("no containers on network opentelemetry-demo - is the demo up?")
    return f"{len(cs)} containers, not running: {bad or 'none'}"


def c_prom():
    up = metrics.promql("up")
    calls = metrics.metric_names("calls")
    return f"{len(up)} 'up' series; span-metric names: {calls[:3]}"


def c_jaeger():
    s = traces.services()
    return f"{len(s)} services: {sorted(s)[:8]}..."


def c_logs():
    hits = logs.search_logs(minutes=10, size=3)
    return f"{len(hits)} recent log docs at {config.opensearch_url()}"


def c_llm():
    import litellm
    r = litellm.completion(model=config.LLM_MODEL,
                           messages=[{"role": "user", "content": "Reply with just: pong"}],
                           max_tokens=5)
    return f"{config.LLM_MODEL} -> {r.choices[0].message.content!r}"


check("Containers", c_containers)
check("Prometheus", c_prom)
check("Jaeger", c_jaeger)
check("OpenSearch logs", c_logs)
check("LLM", c_llm)
print("\nALL GOOD" if ok_all else "\nSome checks failed (see above)")
sys.exit(0 if ok_all else 1)
