"""Right after the demo starts, nothing should look like an incident: containers that started together are
normal startup, memory warm-up is not a leak, and monitoring containers are not culprits."""
from datetime import datetime, timedelta, timezone

import pytest

from agent.evidence import Ledger
from agent.tools import containers, metrics, observe


def setup(monkeypatch, starts_min_ago: dict, restarts=None, promql=None):
    now = datetime.now(timezone.utc)
    restarts = restarts or {}
    monkeypatch.setattr(containers, "list_containers",
                        lambda: [{"name": n, "state": "running", "status": "Up"} for n in starts_min_ago])
    monkeypatch.setattr(containers, "inspect", lambda n: {
        "running": True, "status": "running", "restart_count": restarts.get(n, 0), "exit_code": 0, "oom_killed": False,
        "started_at": (now - timedelta(minutes=starts_min_ago[n])).isoformat().replace("+00:00", "Z")})
    monkeypatch.setattr(metrics, "promql", promql or (lambda q: []))


NAMES = ["cart", "payment", "checkout", "frontend", "opensearch", "astronomy-db", "product-catalog", "ad", "email", "quote"]


def test_whole_demo_starting_is_not_an_incident(monkeypatch):
    setup(monkeypatch, {n: 23 for n in NAMES}, restarts={"product-catalog": 1})
    L = Ledger()
    out = observe.recent_changes(L) + observe.container_health(L)
    assert "whole demo started together" in out
    assert not any(i["anomalous"] for i in L.items.values()), out


def test_a_container_restarting_on_its_own_is_caught(monkeypatch):
    starts = {n: 60 for n in NAMES}
    starts["cart"] = 2
    setup(monkeypatch, starts, restarts={"cart": 3})
    L = Ledger()
    out = observe.recent_changes(L) + observe.container_health(L)
    assert "cart restarted on its own" in out and "restarts=3" in out
    assert [i["ref"] for i in L.items.values() if i["anomalous"]] == ["container:cart", "container:cart"]


def test_memory_warmup_and_monitoring_containers_are_not_leaks(monkeypatch):
    def promql(q):
        if "container_memory_usage_limit_bytes" in q:      # minutes to limit
            return [{"metric": {"container_name": n}, "value": 5.0} for n in ("payment", "opensearch", "cart")]
        if "container_memory_percent_ratio" in q:
            return [{"metric": {"container_name": "payment"}, "value": 40.0},     # far from its limit
                    {"metric": {"container_name": "opensearch"}, "value": 100.0},  # JVM heap, monitoring
                    {"metric": {"container_name": "cart"}, "value": 85.0}]         # a real leak
        return []
    starts = {n: 60 for n in NAMES}
    setup(monkeypatch, starts, promql=promql)
    L = Ledger()
    out = observe.container_health(L)
    flagged = {i["ref"] for i in L.items.values() if i["anomalous"]}
    assert flagged == {"container:cart"}, out


def test_no_leak_verdict_during_warmup(monkeypatch):
    def promql(q):
        if "container_memory_usage_limit_bytes" in q:
            return [{"metric": {"container_name": "cart"}, "value": 5.0}]
        if "container_memory_percent_ratio" in q:
            return [{"metric": {"container_name": "cart"}, "value": 85.0}]
        return []
    starts = {n: 60 for n in NAMES}
    starts["cart"] = 5
    setup(monkeypatch, starts, promql=promql)
    L = Ledger()
    out = observe.container_health(L)
    assert "hits limit" not in out


def test_stopped_container_shows_state_not_stale_memory(monkeypatch):
    now = datetime.now(timezone.utc)
    monkeypatch.setattr(containers, "list_containers", lambda: [{"name": "payment", "state": "exited", "status": "Exited (137)"}])
    monkeypatch.setattr(containers, "inspect", lambda n: {
        "running": False, "status": "exited", "restart_count": 0, "exit_code": 137, "oom_killed": False,
        "started_at": (now - timedelta(minutes=90)).isoformat().replace("+00:00", "Z")})

    def promql(q):   # stale series from before the stop
        if "container_memory_usage_limit_bytes" in q:
            return [{"metric": {"container_name": "payment"}, "value": 5.0}]
        if "container_memory_percent_ratio" in q:
            return [{"metric": {"container_name": "payment"}, "value": 86.0}]
        return []
    monkeypatch.setattr(metrics, "promql", promql)
    out = observe.container_health(Ledger())
    assert "not OOM-killed" in out and "killed by a signal" in out
    assert "memory" not in out.replace("OOM", "")
