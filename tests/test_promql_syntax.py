"""Every PromQL query the tools send must be valid range syntax. A baseline written as rate(x[15m offset 20m])
is rejected by Prometheus and silently produced empty baselines in the first live run."""
import re

from agent.evidence import Ledger
from agent.tools import containers, metrics, observe, observe_db, observe_p1


def test_rng_helper():
    assert metrics.rng("2m") == "[2m]"
    assert metrics.rng("15m offset 20m") == "[15m] offset 20m"


def test_no_offset_inside_brackets(monkeypatch):
    sent = []
    monkeypatch.setattr(metrics, "promql", lambda q: sent.append(q) or [])
    monkeypatch.setattr(metrics, "metric_names", lambda match="": [])
    monkeypatch.setattr(containers, "list_containers", lambda: [])
    monkeypatch.setattr(containers, "inspect", lambda n: {})
    L = Ledger()
    observe.service_health_raw()
    observe.service_health_raw(window="5m")
    observe_p1.dependency_latency(L)
    observe_db.db_health(L)
    assert sent, "no queries captured"
    bad = [q for q in sent if re.search(r"\[[^\]]*offset", q)]
    assert not bad, bad
    assert any("] offset 20m" in q for q in sent)     # baselines are really requested
