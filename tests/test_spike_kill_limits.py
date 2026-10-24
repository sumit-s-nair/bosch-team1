"""Traffic-spike alert, expected effects of a throttle, the repo-root kill switch, and the investigation limits."""
import json
import time
from datetime import datetime, timezone

import pytest

from action_api import server as action_server
from agent import actions, brain, detector, llm, server, verify
from agent.tools import containers, observe


def svc(rps, base_rps):
    return {"rps": rps, "base_rps": base_rps, "err_rate": 0.0, "base_err_rate": 0.0, "p95_ms": 50.0, "base_p95_ms": 50.0}


@pytest.fixture
def world(monkeypatch):
    """Fake service health; the demo started long ago unless a test says otherwise."""
    state = {"health": {}, "stack_age_s": None}
    monkeypatch.setattr(observe, "service_health_raw", lambda *a, **k: state["health"])
    monkeypatch.setattr(containers, "list_containers", lambda: [])
    now = datetime.now(timezone.utc)
    monkeypatch.setattr(observe, "_starts", lambda: ({}, None if state["stack_age_s"] is None
                                                     else now.timestamp() - state["stack_age_s"], now))
    return state


# ---------------------------------------------------------------- traffic spike
def test_homepage_flood_raises_a_traffic_spike(world):
    world["health"] = {"frontend": svc(rps=8.0, base_rps=1.5), "cart": svc(rps=1.0, base_rps=1.0)}
    assert detector.check() == [{"type": "traffic_spike", "service": "frontend", "value": 8.0, "baseline": 1.5}]


@pytest.mark.parametrize("rps, base", [(2.5, 1.5),      # only 1.7x baseline
                                       (0.9, 0.25),     # 3.6x, but below SPIKE_MIN_RPS
                                       (5.0, None)])    # no baseline yet
def test_no_spike_for_normal_or_tiny_traffic(world, rps, base):
    world["health"] = {"frontend": svc(rps=rps, base_rps=base)}
    assert detector.check() == []


def test_no_spike_while_the_demo_warms_up(world):
    world["health"] = {"frontend": svc(rps=8.0, base_rps=1.5)}
    world["stack_age_s"] = 10 * 60          # baseline window only partly filled -> it reads low
    assert detector.check() == []
    world["stack_age_s"] = 60 * 60
    assert detector.check()[0]["type"] == "traffic_spike"


# ---------------------------------------------------------------- throttle verification
def test_traffic_drop_after_a_throttle_is_the_fix_working(monkeypatch):
    spike = [{"type": "traffic_spike", "service": "frontend", "value": 8.0, "baseline": 1.5}]
    drop = {"type": "traffic_drop", "service": "frontend", "value": 0.25, "baseline": 1.5}
    monkeypatch.setattr(detector, "check", lambda strict=False, original_alerts=None: [drop])
    kw = {"settle_s": 0, "polls": 2, "poll_s": 0}
    assert verify.recovered(spike, **kw)["recovered"] is False                  # any other fix: a new alert
    v = verify.recovered(spike, expected=("traffic_drop",), **kw)
    assert v["recovered"] is True and v["polls"][-1]["expected_from_fix"] == [drop]   # recorded, not hidden


# ---------------------------------------------------------------- kill switch
def test_repo_root_stop_works_from_any_folder(monkeypatch, tmp_path):
    repo_stop = tmp_path / "repo" / "STOP"
    repo_stop.parent.mkdir()
    elsewhere = tmp_path / "ui" / "dashboard"
    elsewhere.mkdir(parents=True)
    monkeypatch.chdir(elsewhere)                    # agent / Action API started from another folder
    monkeypatch.setattr(actions, "KILL_FILE", str(repo_stop))
    monkeypatch.setattr(action_server, "REPO_STOP", str(repo_stop))
    state = action_server.State("t", str(tmp_path / "audit.jsonl"))
    assert not actions.kill_switch_on() and not state.killed()
    repo_stop.touch()
    assert actions.kill_switch_on() and state.killed()   # one file stops both


def test_kill_switch_prompt_says_release_not_start():
    assert server.prompt_kind("Action API kill switch is on. Release the kill switch and press Enter to check again, "
                              "or type 'skip' to escalate without acting: ") == "action_api_down"


# ---------------------------------------------------------------- investigation limits
@pytest.fixture
def quiet_tools(monkeypatch):
    monkeypatch.setattr(observe, "service_health_raw", lambda *a, **k: {})
    monkeypatch.setattr(containers, "list_containers", lambda: [])
    monkeypatch.setattr(brain, "_run_tool", lambda L, name, args: f"{name}: nothing abnormal")


def greedy_model(monkeypatch):
    """A model that always wants one more tool; counts its tool turns."""
    turns = []

    def chat(messages, tools=None, think=True, json_mode=False, timeout=0):
        if json_mode:
            return {"role": "assistant", "content": json.dumps({"summary": "s", "root_cause": {
                "service": "unknown", "category": "unknown", "description": "d"}, "confidence": 0.1, "evidence": []})}
        turns.append(1)
        return {"role": "assistant", "content": "", "tool_calls": [{"function": {"name": "error_logs", "arguments": {}}}]}
    monkeypatch.setattr(llm, "chat", chat)
    return turns


def test_at_most_eight_tool_turns(monkeypatch, quiet_tools):
    turns = greedy_model(monkeypatch)
    d = brain.investigate([{"type": "manual", "service": "unknown"}], log=lambda *a: None)
    assert brain.MAX_TOOL_STEPS == 8 and len(turns) == 8
    assert not any("time budget" in n for n in d["validation_notes"])


def test_time_budget_stops_tool_calls_and_says_so(monkeypatch, quiet_tools):
    turns = greedy_model(monkeypatch)
    monkeypatch.setattr(brain, "TOOL_TIME_BUDGET_S", 0)
    monkeypatch.setattr(brain.time, "time", iter(range(0, 1000, 5)).__next__)   # every clock read moves 5s on
    d = brain.investigate([{"type": "manual", "service": "unknown"}], log=lambda *a: None)
    assert turns == [] and any("time budget" in n for n in d["validation_notes"])
