"""The hosted-model adapter converts the agent's (Ollama-shaped) history and answers both ways."""
import json
import types

from agent import config, llm


def test_history_to_openai_pairs_tool_results_with_call_ids():
    hist = [{"role": "system", "content": "s"}, {"role": "user", "content": "u"},
            {"role": "assistant", "content": "", "tool_calls": [
                {"function": {"name": "service_overview", "arguments": {}}},
                {"function": {"name": "trace_errors", "arguments": {"service": "payment"}}}]},
            {"role": "tool", "tool_name": "service_overview", "content": "a"},
            {"role": "tool", "tool_name": "trace_errors", "content": "b"}]
    out = llm.to_openai(hist)
    ids = [c["id"] for c in out[2]["tool_calls"]]
    assert [m["tool_call_id"] for m in out[3:]] == ids
    assert json.loads(out[2]["tool_calls"][1]["function"]["arguments"]) == {"service": "payment"}


def test_answer_from_openai_back_to_agent_shape():
    msg = {"content": None, "tool_calls": [{"id": "x1", "type": "function",
                                            "function": {"name": "db_health", "arguments": "{}"}}]}
    assert llm.from_openai(msg) == {"role": "assistant", "content": "",
                                    "tool_calls": [{"id": "x1", "function": {"name": "db_health", "arguments": {}}}]}


def test_hosted_failure_falls_back_to_local(monkeypatch):
    monkeypatch.setattr(config, "LLM_PROVIDER", "openai")
    monkeypatch.setenv("LLM_FALLBACK_TO_LOCAL", "1")
    monkeypatch.setattr(llm, "_hosted", lambda *a: (_ for _ in ()).throw(RuntimeError("no network")))
    monkeypatch.setattr(llm, "_ollama", lambda *a: {"role": "assistant", "content": "local"})
    assert llm.chat([{"role": "user", "content": "x"}])["content"] == "local"
