"""Offline tests for dependency_latency and error_breakdown (backends faked; network blocked by conftest)."""
import itertools
import random

from agent.evidence import Ledger
from agent.tools.observe_p1 import dependency_latency, error_breakdown
from agent.tools import metrics, traces

CALL = "oteldemo.CartService/GetCart"


def row(v, **lab):
    return {"metric": lab, "value": v}


def fake_promql(cn, cb, sn, sb, net=()):
    def promql(q):
        if "container_network" in q:
            return list(net)
        if "histogram_quantile" in q:
            base = "offset" in q
            if "SPAN_KIND_CLIENT" in q:
                return [row(cb if base else cn, service_name="checkout", span_name=CALL)]
            return [row(sb if base else sn, service_name="cart", span_name=CALL)]
        if "calls_total" in q:
            return [row(5.0, service_name="cart", span_name=CALL)]
        return []
    return promql


def run_dep(monkeypatch, *a, **k):
    monkeypatch.setattr(metrics, "promql", fake_promql(*a, **k))
    L = Ledger()
    return dependency_latency(L, "checkout"), L


def test_network_latency_flagged_and_escalates(monkeypatch):
    out, L = run_dep(monkeypatch, 620, 30, 25, 22)
    assert "NETWORK SUSPECT" in out and "ESCALATE" in out and "checkout -> cart" in out
    assert any(e["anomalous"] for e in L.items.values())


def test_healthy_not_flagged(monkeypatch):
    out, L = run_dep(monkeypatch, 32, 30, 24, 22)
    assert "SUSPECT" not in out and "network is not the likely cause" in out


def test_callee_slow_is_not_network(monkeypatch):
    out, _ = run_dep(monkeypatch, 900, 30, 880, 22)
    assert "callee itself slow" in out and "NETWORK SUSPECT" not in out


def test_packet_drops_flagged(monkeypatch):
    net = [row(240, container_name="cart", __name__="container_network_io_usage_rx_dropped_total")]
    out, _ = run_dep(monkeypatch, 32, 30, 24, 22, net=net)
    assert "DROPS" in out and "cart: rx_dropped=240" in out and "ESCALATE" in out


# ---------------------------------------------------------------- error_breakdown
_ids = itertools.count()


def span(err, pid=None, msg=""):
    i = next(_ids)
    return {"trace_id": f"t{i}", "span_id": f"s{i}", "parent_id": None, "service": "product-catalog",
            "name": "GetProduct", "kind": "2", "error": err, "message": msg, "http_status": None,
            "rpc_status": None, "duration_ms": 5.0, "attrs": {"app.product.id": pid, "http.route": "/api/products"}}


def fake_spans(monkeypatch, ss):
    monkeypatch.setattr(traces, "find_spans",
                        lambda *a, **k: [s for s in ss if s["error"]] if k.get("error_only") else ss)


def test_single_product_concentration(monkeypatch):
    ss = [span(True, "OLJCESPC7Z", msg="Fail Feature Flag Enabled") for _ in range(6)]
    ss += [span(False, p) for p in ["A1", "B2", "C3", "D4"] * 10]
    fake_spans(monkeypatch, ss)
    out = error_breakdown(Ledger(), "product-catalog")
    assert "app.product.id=OLJCESPC7Z fails 100%" in out and "Fail Feature Flag" in out


def test_intermittent_random_not_attributed(monkeypatch):
    random.seed(1)
    fake_spans(monkeypatch, [span(random.random() < 0.1, random.choice(["A1", "B2", "C3", "D4"])) for _ in range(200)])
    assert "NOT tied to any single attribute" in error_breakdown(Ledger(), "product-catalog")


def test_no_attrs_is_reported(monkeypatch):
    fake_spans(monkeypatch, [dict(span(i % 2 == 0), attrs={}) for i in range(20)])
    assert "No span attributes available" in error_breakdown(Ledger(), "product-catalog")


def test_no_spans(monkeypatch):
    fake_spans(monkeypatch, [])
    assert "No spans" in error_breakdown(Ledger(), "product-catalog")


def test_tools_dict_shape():
    from agent.tools.observe_p1 import TOOLS_P1
    assert TOOLS_P1["dependency_latency"][2] == {"service": "string?"}
    assert TOOLS_P1["error_breakdown"][2] == {"service": "string", "operation": "string?"}
