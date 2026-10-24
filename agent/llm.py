"""Model client. Two providers, same interface (the rest of the agent only sees Ollama-shaped messages):
  LLM_PROVIDER=ollama   local Ollama /api/chat (default; Qwen3 8B, no data leaves the laptop)
  LLM_PROVIDER=openai   any OpenAI-compatible hosted API: Gemini, Grok, OpenAI, Groq ... (LLM_HOSTED_BASE_URL,
                        LLM_HOSTED_MODEL, LLM_API_KEY). Faster and stronger; needs network; declare it in the team form.
If the hosted call fails and LLM_FALLBACK_TO_LOCAL=1, the call is retried on local Ollama.
"""
import json

import requests

from agent import config


def chat(messages: list[dict], tools: list[dict] | None = None, think: bool = True,
         json_mode: bool = False, timeout: int | None = None) -> dict:
    """Returns the assistant message: {"role", "content", "thinking"?, "tool_calls"?: [{"function": {"name", "arguments": {...}}}]}"""
    timeout = timeout or config.LLM_TIMEOUT
    if config.LLM_PROVIDER == "openai":
        try:
            return _hosted(messages, tools, json_mode, timeout)
        except Exception:
            if config.setting("LLM_FALLBACK_TO_LOCAL") != "1":
                raise
    return _ollama(messages, tools, think, json_mode, timeout)


# ------------------------------------------------------------------ local Ollama
def _ollama(messages, tools, think, json_mode, timeout):
    body = {"model": config.LLM_MODEL, "messages": messages, "stream": False, "think": think,
            "options": {"temperature": 0.1, "num_ctx": config.LLM_NUM_CTX}}
    if tools:
        body["tools"] = tools
    if json_mode:
        body["format"] = "json"
    r = requests.post(f"{config.LLM_API_BASE}/api/chat", json=body, timeout=timeout)
    if r.status_code >= 400:
        raise RuntimeError(f"Ollama {r.status_code}: {r.text[:300]}")
    return r.json()["message"]


# ------------------------------------------------------------------ OpenAI-compatible hosted API
def to_openai(messages: list[dict]) -> list[dict]:
    """Ollama-shaped history -> OpenAI shape (tool calls need ids, tool results need the matching tool_call_id)."""
    out, pending, n = [], [], 0
    for m in messages:
        if m.get("role") == "assistant" and m.get("tool_calls"):
            calls = []
            for c in m["tool_calls"]:
                n += 1
                cid = c.get("id") or f"call_{n}"
                pending.append(cid)
                args = c["function"].get("arguments") or {}
                calls.append({"id": cid, "type": "function", "function": {
                    "name": c["function"]["name"], "arguments": args if isinstance(args, str) else json.dumps(args)}})
            out.append({"role": "assistant", "content": m.get("content") or "", "tool_calls": calls})
        elif m.get("role") == "tool":
            out.append({"role": "tool", "tool_call_id": pending.pop(0) if pending else f"call_{n}",
                        "content": m.get("content", "")})
        else:
            out.append({"role": m["role"], "content": m.get("content", "")})
    return out


def from_openai(msg: dict) -> dict:
    calls = []
    for c in msg.get("tool_calls") or []:
        args = c["function"].get("arguments") or "{}"
        try:
            args = json.loads(args) if isinstance(args, str) else args
        except ValueError:
            args = {}
        calls.append({"id": c.get("id"), "function": {"name": c["function"]["name"], "arguments": args}})
    out = {"role": "assistant", "content": msg.get("content") or ""}
    if calls:
        out["tool_calls"] = calls
    return out


def _hosted(messages, tools, json_mode, timeout):
    if not (config.LLM_HOSTED_BASE_URL and config.LLM_HOSTED_MODEL and config.LLM_API_KEY):
        raise RuntimeError("LLM_PROVIDER=openai needs LLM_HOSTED_BASE_URL, LLM_HOSTED_MODEL and LLM_API_KEY in .env")
    body = {"model": config.LLM_HOSTED_MODEL, "messages": to_openai(messages), "temperature": 0.1}
    if tools:
        body["tools"] = tools
    if json_mode:
        body["response_format"] = {"type": "json_object"}
    r = requests.post(f"{config.LLM_HOSTED_BASE_URL}/chat/completions", json=body, timeout=timeout,
                      headers={"Authorization": f"Bearer {config.LLM_API_KEY}"})
    if r.status_code >= 400:
        raise RuntimeError(f"hosted LLM {r.status_code}: {r.text[:300]}")
    return from_openai(r.json()["choices"][0]["message"])
