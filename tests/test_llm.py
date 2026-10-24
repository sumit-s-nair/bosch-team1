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


def test_chain_falls_through_to_the_next_provider(monkeypatch):
    monkeypatch.setattr(config, "llm_chain", lambda: [{"name": "groq", "model": "m1", "base": "b", "key": "k"},
                                                     {"name": "gemini", "model": "m2", "base": "b", "key": "k"},
                                                     {"name": "local", "model": "qwen"}])
    calls = []

    def hosted(messages, tools, json_mode, timeout, pr):
        calls.append(pr["name"])
        if pr["name"] == "groq":
            raise RuntimeError("429 rate limited")
        return {"role": "assistant", "content": "from " + pr["name"]}
    monkeypatch.setattr(llm, "_hosted", hosted)
    assert llm.chat([{"role": "user", "content": "x"}])["content"] == "from gemini"
    assert calls == ["groq", "gemini"] and llm.last_provider == "gemini:m2"


def test_chain_only_uses_providers_with_keys(monkeypatch):
    for k in ("GROQ_API_KEY", "GEMINI_API_KEY", "XAI_API_KEY"):
        monkeypatch.delenv(k, raising=False)
    monkeypatch.setenv("GROQ_API_KEY", "g")
    monkeypatch.setenv("LLM_CHAIN", "groq,gemini,local")
    assert [p["name"] for p in config.llm_chain()] == ["groq", "local"]
