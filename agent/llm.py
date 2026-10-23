"""Thin client for Ollama's native /api/chat (tool calling + Qwen3 thinking control)."""
import requests
from agent.config import LLM_API_BASE, LLM_MODEL, LLM_NUM_CTX, LLM_TIMEOUT


def chat(messages: list[dict], tools: list[dict] | None = None, think: bool = True,
         json_mode: bool = False, timeout: int = LLM_TIMEOUT) -> dict:
    """Returns the assistant message: {"role", "content", "thinking"?, "tool_calls"?: [{"function": {"name", "arguments": {...}}}]}"""
    body = {"model": LLM_MODEL, "messages": messages, "stream": False, "think": think,
            "options": {"temperature": 0.1, "num_ctx": LLM_NUM_CTX}}
    if tools:
        body["tools"] = tools
    if json_mode:
        body["format"] = "json"
    r = requests.post(f"{LLM_API_BASE}/api/chat", json=body, timeout=timeout)
    if r.status_code >= 400:
        raise RuntimeError(f"Ollama {r.status_code}: {r.text[:300]}")
    return r.json()["message"]
