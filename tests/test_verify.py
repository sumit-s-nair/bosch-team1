"""Verification judges the fix by the service it targeted; alerts from another fault do not count against it."""
from agent import verify


def test_fixed_service_healthy_but_other_fault_remains(monkeypatch):
    monkeypatch.setattr(verify.detector, "check", lambda **k: [{"type": "memory_leak", "service": "ad"}])
    monkeypatch.setattr(verify, "target_healthy", lambda s: (True, "no errors"))
    r = verify.recovered([{"type": "error_rate", "service": "product-catalog"}], target="product-catalog",
                         settle_s=0, polls=2, poll_s=0)
    assert r["target_recovered"] and not r["recovered"] and r["other_alerts"][0]["service"] == "ad"


def test_fixed_service_still_failing(monkeypatch):
    monkeypatch.setattr(verify.detector, "check", lambda **k: [{"type": "error_rate", "service": "payment"}])
    monkeypatch.setattr(verify, "target_healthy", lambda s: (False, "5 error spans"))
    r = verify.recovered([{"type": "error_rate", "service": "payment"}], target="payment", settle_s=0, polls=2, poll_s=0)
    assert not r["target_recovered"] and not r["recovered"]


def test_everything_clear(monkeypatch):
    monkeypatch.setattr(verify.detector, "check", lambda **k: [])
    monkeypatch.setattr(verify, "target_healthy", lambda s: (True, "ok"))
    assert verify.recovered([{"type": "container", "service": "payment"}], target="payment", settle_s=0, polls=2, poll_s=0)["recovered"]


def test_callers_still_alerting_from_old_window_do_not_count_as_another_fault(monkeypatch):
    """frontend's 5-minute error alert lingers after product-catalog is fixed; it has no errors in the last minute."""
    monkeypatch.setattr(verify.detector, "check", lambda **k: [{"type": "operation_errors", "service": "frontend"}])
    monkeypatch.setattr(verify, "target_healthy", lambda s: (True, "no errors"))     # product-catalog and frontend
    r = verify.recovered([{"type": "error_rate", "service": "product-catalog"}], target="product-catalog",
                         settle_s=0, polls=2, poll_s=0)
    assert r["recovered"] and r["other_alerts"] == []


def test_a_real_second_fault_still_counts(monkeypatch):
    monkeypatch.setattr(verify.detector, "check", lambda **k: [{"type": "error_rate", "service": "cart"}])
    monkeypatch.setattr(verify, "target_healthy", lambda s: (s == "product-catalog", "x"))
    r = verify.recovered([{"type": "error_rate", "service": "product-catalog"}], target="product-catalog",
                         settle_s=0, polls=2, poll_s=0)
    assert r["target_recovered"] and not r["recovered"] and r["other_alerts"][0]["service"] == "cart"
