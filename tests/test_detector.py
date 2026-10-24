"""Detector: per-operation errors, memory leaks and CPU starvation alert; normal noise does not."""
from datetime import datetime, timedelta, timezone

from agent import detector
from agent.tools import containers, metrics, observe


def quiet_services(monkeypatch):
    monkeypatch.setattr(observe, "service_health_raw", lambda: {})
    monkeypatch.setattr(containers, "list_containers", lambda: [])


def test_one_failing_operation_alerts(monkeypatch):
    quiet_services(monkeypatch)

    def promql(q):
        if "increase(traces_span_metrics_calls_total" not in q:
            return []
        base = "offset" in q
        err = "STATUS_CODE_ERROR" in q
        rows = {("cart", "oteldemo.CartService/EmptyCart"): (8, 2), ("cart", "oteldemo.CartService/GetCart"): (400, 0),
                ("flagd", "Resolve"): (10, 10)}
        return [{"metric": {"service_name": s, "span_name": o}, "value": (0 if base and err else (e if err else t))}
                for (s, o), (t, e) in rows.items()]
    monkeypatch.setattr(metrics, "promql", promql)
    alerts = detector.check()
    assert [(a["type"], a["service"], a["operation"]) for a in alerts] == \
        [("operation_errors", "cart", "oteldemo.CartService/EmptyCart")]


def _containers(monkeypatch, started_min_ago=60, cpu_limit=None):
    now = datetime.now(timezone.utc)
    monkeypatch.setattr(containers, "inspect", lambda n: {
        "running": True, "status": "running", "restart_count": 0, "exit_code": 0, "oom_killed": False,
        "cpu_limit_cores": cpu_limit,
        "started_at": (now - timedelta(minutes=started_min_ago)).isoformat().replace("+00:00", "Z")})


def test_memory_leak_alerts_after_warmup_only(monkeypatch):
    quiet_services(monkeypatch)
    monkeypatch.setattr(metrics, "promql", lambda q:
                        [{"metric": {"container_name": "email"}, "value": 8.0}] if "container_memory_usage_limit_bytes" in q else
                        [{"metric": {"container_name": "email"}, "value": 88.0}] if "memory_percent" in q else [])
    _containers(monkeypatch, started_min_ago=60)
    assert [a["type"] for a in detector.check()] == ["memory_leak"]
    _containers(monkeypatch, started_min_ago=5)
    assert detector.check() == []


def test_cpu_starved_alerts_but_monitoring_stack_does_not(monkeypatch):
    quiet_services(monkeypatch)
    monkeypatch.setattr(metrics, "promql", lambda q:
                        [{"metric": {"container_name": "ad"}, "value": 0.6},
                         {"metric": {"container_name": "opensearch"}, "value": 0.9}] if "throttl" in q else [])
    _containers(monkeypatch, cpu_limit=0.5)
    assert [(a["type"], a["service"]) for a in detector.check()] == [("cpu_starved", "ad")]


def test_crash_restart_alerts_but_our_own_restart_and_demo_start_do_not(monkeypatch, tmp_path):
    import json as _json
    import time as _time
    monkeypatch.setattr(observe, "service_health_raw", lambda: {})
    monkeypatch.setattr(metrics, "promql", lambda q: [])
    monkeypatch.setattr(detector, "operation_errors", lambda: [])
    up = {n: "Up 2 hours" for n in ("cart", "payment", "checkout", "frontend", "quote", "email")}
    up["ad"] = "Up 8 seconds (health: starting)"
    up["payment"] = "Up 30 seconds"
    monkeypatch.setattr(containers, "list_containers", lambda: [{"name": n, "state": "running", "status": st} for n, st in up.items()])
    audit = tmp_path / "audit.jsonl"           # our Action API restarted payment 30 s ago
    audit.write_text(_json.dumps({"t": _time.strftime("%Y-%m-%d %H:%M:%S", _time.localtime(_time.time() - 30)),
                                  "action": "restart_service", "params": {"service": "payment"}, "status": "executed"}) + "\n")
    monkeypatch.setenv("ACTION_API_AUDIT", str(audit))
    assert [(a["type"], a["service"]) for a in detector.check()] == [("container_restarted", "ad")]
    monkeypatch.setattr(containers, "list_containers", lambda: [{"name": n, "state": "running", "status": "Up 3 minutes"} for n in up])
    assert detector.check() == []            # whole demo just started: not an incident
