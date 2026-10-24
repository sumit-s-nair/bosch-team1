"""All phases end to end on an offline scenario with a scripted model and scripted human answers."""
import json
import os

import pytest

from agent import actions, llm
from eval import offline, run_offline, scenarios


@pytest.fixture(autouse=True)
def isolate(monkeypatch, tmp_path):
    from agent.tools import containers, logs, metrics, traces
    for mod, attrs in ((metrics, ("promql", "metric_names")), (containers, ("list_containers", "inspect", "stats")),
                       (traces, ("_api", "services", "find_spans", "get_trace")), (logs, ("search_logs", "fields"))):
        for a in attrs:
            monkeypatch.setattr(mod, a, getattr(mod, a))
    monkeypatch.chdir(tmp_path)          # runs/ and STOP live in a temp dir


def model_says(monkeypatch, service, category, conf=0.9):
    def chat(messages, tools=None, think=True, json_mode=False, timeout=0):
        if not json_mode:
            return {"role": "assistant", "content": "enough"}
        # cite every abnormal fact the sweep produced that mentions the service
        text = "\n".join(m["content"] for m in messages if isinstance(m.get("content"), str))
        ids = [l.split("[E")[1].split("]")[0] for l in text.splitlines() if service in l and "[E" in l]
        return {"role": "assistant", "content": json.dumps({
            "summary": f"{service} broken", "root_cause": {"service": service, "category": category, "description": "x"},
            "confidence": conf, "evidence": [{"id": f"E{i}", "why": "w"} for i in ids[:3]]})}
    monkeypatch.setattr(llm, "chat", chat)


def answers(*replies):
    it = iter(replies)
    return lambda prompt="": next(it)


def run(monkeypatch, scenario, replies):
    monkeypatch.setattr("builtins.input", answers(*replies))
    run_offline.run_pipeline(scenario)
    inc = sorted(os.listdir("runs"))[-1]
    return json.load(open(f"runs/{inc}/report.json")), f"runs/{inc}"


def test_approved_correct_fix_recovers(monkeypatch):
    model_says(monkeypatch, "payment", "container_down")
    rep, d = run(monkeypatch, "container_stopped", ["yes", "sumit"])
    assert rep["outcome"] == "fixed and verified"
    assert rep["action"]["action"] == "restart_service" and rep["action"]["params"] == {"service": "payment"}
    for f in ("alert.json", "diagnosis.json", "plan.json", "approval.json", "actions.jsonl", "validation.json",
              "report.json", "report.md", "timeline.jsonl"):
        assert os.path.exists(f"{d}/{f}"), f
    phases = [json.loads(l)["phase"] for l in open(f"{d}/timeline.jsonl")]
    assert phases.index("detect") < phases.index("investigate") < phases.index("human") < phases.index("fix") \
        < phases.index("validate") < phases.index("report")


def test_rejected_means_nothing_executes(monkeypatch):
    model_says(monkeypatch, "payment", "container_down")
    rep, d = run(monkeypatch, "container_stopped", ["no"])
    assert rep["action_result"] is None and not os.path.exists(f"{d}/actions.jsonl")
    assert "rejected" in rep["outcome"]


def test_wrong_target_not_recovered_escalates(monkeypatch):
    model_says(monkeypatch, "checkout", "container_down")       # restarts checkout, but payment is the one down
    rep, _ = run(monkeypatch, "container_stopped", ["yes", "sumit"])
    assert rep["outcome"] == "fix applied but not recovered: escalated"


def test_kill_switch_blocks_execution(monkeypatch):
    model_says(monkeypatch, "payment", "container_down")
    open("STOP", "w").close()
    rep, _ = run(monkeypatch, "container_stopped", ["yes", "sumit"])
    assert rep["action_result"] is None or rep["action_result"]["status"] == "refused"


def test_low_confidence_escalates_without_plan(monkeypatch):
    model_says(monkeypatch, "payment", "container_down", conf=0.4)
    rep, _ = run(monkeypatch, "container_stopped", [""])
    assert rep["action"] is None and rep["outcome"] == "escalated to human"


def test_action_outside_allowlist_refused(tmp_path):
    from agent.incident import Incident
    c = actions.ActionClient(Incident(str(tmp_path)), dry_run=True)
    r = c.execute({"action": "docker_rm", "params": {}, "reason": "x"}, approved_by="sumit")
    assert r["status"] == "refused"


def test_app_fault_proposes_flag_off_and_human_names_flag(monkeypatch):
    model_says(monkeypatch, "product-catalog", "service_error")
    rep, d = run(monkeypatch, "product_catalog_one_product", ["yes", "someFlag", "sumit"])
    assert rep["action"]["action"] == "toggle_feature_flag"
    assert rep["approval"]["params_from_human"]["flag"] == "someFlag"     # came from the human, not the agent
    assert rep["outcome"] == "fixed and verified"


def test_no_flag_named_means_rejected(monkeypatch):
    model_says(monkeypatch, "payment", "service_error")
    rep, _ = run(monkeypatch, "payment_failure", ["yes", ""])
    assert rep["action_result"] is None and "no flag" in rep["approval"]["reason"]


def test_restart_does_not_fix_an_app_fault(monkeypatch):
    model_says(monkeypatch, "product-catalog", "container_down")       # wrong category -> restart
    rep, _ = run(monkeypatch, "product_catalog_one_product", ["yes", "sumit"])
    assert rep["action"]["action"] == "restart_service"
    assert rep["outcome"] == "fix applied but not recovered: escalated"


def test_cache_fault_targets_the_cache_not_its_caller():
    diag = {"decision": "propose_fix",
            "root_cause": {"service": "cart", "category": "cache_unavailable", "description": "cache down"},
            "evidence": [{"id": "E3", "ref": "container:valkey-cart", "observed": "valkey-cart NOT RUNNING"}]}
    assert actions.plan(diag)["params"] == {"service": "valkey-cart"}
    diag["evidence"] = []
    assert actions.plan(diag) is None      # cannot tell which cache -> escalate


def model_sequence(monkeypatch, answers):
    """One (service, category) per investigation round."""
    it = iter(answers)
    state = {}

    def chat(messages, tools=None, think=True, json_mode=False, timeout=0):
        if not json_mode:
            return {"role": "assistant", "content": "enough"}
        service, category = next(it)
        text = "\n".join(m["content"] for m in messages if isinstance(m.get("content"), str))
        state.setdefault("prompts", []).append(text)
        ids = [l.split("[E")[1].split("]")[0] for l in text.splitlines() if service in l and "[E" in l]
        return {"role": "assistant", "content": json.dumps({
            "summary": f"{service} broken", "root_cause": {"service": service, "category": category, "description": "x"},
            "confidence": 0.9, "evidence": [{"id": f"E{i}", "why": "w"} for i in ids[:3]]})}
    monkeypatch.setattr(llm, "chat", chat)
    return state


def test_second_round_fixes_what_the_first_missed(monkeypatch):
    st = model_sequence(monkeypatch, [("checkout", "container_down"), ("payment", "container_down")])
    rep, d = run(monkeypatch, "container_stopped", ["yes", "sumit", "yes", "sumit"])
    assert rep["outcome"] == "fixed and verified" and len(rep["rounds"]) == 2
    assert rep["rounds"][0]["recovered"] is False and rep["action"]["params"] == {"service": "payment"}
    assert "did NOT recover" in st["prompts"][-1]            # round 2 knew what was tried
    assert os.path.exists(f"{d}/diagnosis-round2.json")


def test_same_fix_is_never_repeated(monkeypatch):
    model_sequence(monkeypatch, [("checkout", "container_down"), ("checkout", "container_down")])
    rep, _ = run(monkeypatch, "container_stopped", ["yes", "sumit"])          # no second approval is asked
    assert rep["outcome"] == "fix applied but not recovered: escalated" and len(rep["rounds"]) == 2


def test_action_api_down_asks_then_escalates_without_acting(monkeypatch):
    model_says(monkeypatch, "payment", "container_down")
    monkeypatch.setattr(run_offline.SimulatedActionClient, "health", lambda self: (False, "Action API unreachable"))
    calls = []
    monkeypatch.setattr(run_offline.SimulatedActionClient, "execute", lambda self, p, by: calls.append(p))
    rep, _ = run(monkeypatch, "container_stopped", ["", "skip", ""])          # retry once, skip, acknowledge escalation
    assert calls == [] and rep["outcome"] == "escalated to human"
    assert any("Action API unreachable" in n for n in json.load(open(sorted(
        f"runs/{x}/diagnosis.json" for x in os.listdir("runs"))[-1]))["validation_notes"])


def test_failed_action_is_reported_as_not_applied(monkeypatch):
    model_says(monkeypatch, "payment", "container_down")
    monkeypatch.setattr(run_offline.SimulatedActionClient, "execute",
                        lambda self, p, by: {"status": "failed", "why": "Action API unreachable"})
    rep, _ = run(monkeypatch, "container_stopped", ["yes", "sumit"])
    assert rep["outcome"] == "fix not applied (Action API unreachable): escalated"
