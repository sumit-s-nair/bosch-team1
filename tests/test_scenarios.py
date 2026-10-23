"""Every recorded incident scenario must fire an alert and surface the expected service in abnormal evidence
(no LLM). Guards the tools/detector against regressions. Scenario data lives in eval/ only."""
import pytest

from eval import run_offline, scenarios


@pytest.mark.parametrize("name", scenarios.names())
def test_scenario_signal(name, monkeypatch):
    # install() patches raw clients module-wide; monkeypatch restores them after each test
    from agent.tools import containers, logs, metrics, traces
    for mod, attrs in ((metrics, ("promql", "metric_names")), (containers, ("list_containers", "inspect", "stats")),
                       (traces, ("_api", "services", "find_spans", "get_trace")), (logs, ("search_logs", "fields"))):
        for a in attrs:
            monkeypatch.setattr(mod, a, getattr(mod, a))
    r = run_offline.tool_check(scenarios.get(name))
    assert r["ok"], r


def test_no_flag_text_reaches_tools(monkeypatch, capsys):
    from agent.tools import containers, logs, metrics, traces
    for mod, attrs in ((metrics, ("promql", "metric_names")), (containers, ("list_containers", "inspect", "stats")),
                       (traces, ("_api", "services", "find_spans", "get_trace")), (logs, ("search_logs", "fields"))):
        for a in attrs:
            monkeypatch.setattr(mod, a, getattr(mod, a))
    for name in ("product_catalog_one_product", "ad_gc_pauses"):
        run_offline.tool_check(scenarios.get(name), verbose=True)
    out = capsys.readouterr().out.lower()
    assert "feature flag" not in out and "admanualgc" not in out
