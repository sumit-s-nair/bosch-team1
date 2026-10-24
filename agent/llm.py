"""Model client: local Ollama /api/chat (Qwen3 8B). Nothing leaves the laptop; no API keys."""
import requests

from agent import config


def chat(messages: list[dict], tools: list[dict] | None = None, think: bool = True,
         json_mode: bool = False, timeout: int | None = None) -> dict:
    """Returns the assistant message: {"role", "content", "thinking"?, "tool_calls"?: [{"function": {"name", "arguments": {...}}}]}"""
    body = {"model": config.LLM_MODEL, "messages": messages, "stream": False, "think": think,
            "options": {"temperature": 0.1, "num_ctx": config.LLM_NUM_CTX}}
    if tools:
        body["tools"] = tools
    if json_mode:
        body["format"] = "json"
    r = requests.post(f"{config.LLM_API_BASE}/api/chat", json=body, timeout=timeout or config.LLM_TIMEOUT)
    if r.status_code >= 400:
        raise RuntimeError(f"Ollama {r.status_code}: {r.text[:300]}")
    return r.json()["message"]
