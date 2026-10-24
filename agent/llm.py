"""Model client with a provider chain (the rest of the agent only sees Ollama-shaped messages).
LLM_CHAIN=groq,gemini,xai,local (default): each hosted provider is used only if its key is in .env
(GROQ_API_KEY, GEMINI_API_KEY, XAI_API_KEY); 'local' = Ollama/Qwen on this laptop. The first provider that
answers wins, so a busy or rate-limited free tier falls through to the next. Declare hosted models in the team form.
"""
import json
import time

import requests

from agent import config


last_provider = None      # which provider answered the last call (shown in logs/reports)


def chat(messages: list[dict], tools: list[dict] | None = None, think: bool = True,
         json_mode: bool = False, timeout: int | None = None) -> dict:
    """Returns the assistant message: {"role", "content", "thinking"?, "tool_calls"?: [{"function": {"name", "arguments": {...}}}]}
    Tries each provider in config.llm_chain() in order; the first that answers wins."""
    global last_provider
    timeout = timeout or config.LLM_TIMEOUT
    errors = []
    for pr in config.llm_chain():
        try:
            if pr["name"] == "local":
                out = _ollama(messages, tools, think, json_mode, timeout)
            else:
                out = _hosted(messages, tools, json_mode, min(timeout, 90), pr)
            last_provider = f"{pr['name']}:{pr['model']}"
            return out
        except Exception as e:
            errors.append(f"{pr['name']}: {str(e)[:160]}")
    raise RuntimeError("every model provider failed: " + " | ".join(errors))


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


def _hosted(messages, tools, json_mode, timeout, pr):
    body = {"model": pr["model"], "messages": to_openai(messages), "temperature": 0.1}
    if tools:
        body["tools"] = tools
    if json_mode:
        body["response_format"] = {"type": "json_object"}
    for attempt in range(3):     # free tier: 503 "high demand" / 429 rate limit are usually brief
        r = requests.post(f"{pr['base']}/chat/completions", json=body, timeout=timeout,
                          headers={"Authorization": f"Bearer {pr['key']}"})
        if r.status_code not in (429, 503) or attempt == 2:
            break
        time.sleep(2 * (attempt + 1))
    if r.status_code >= 400:
        raise RuntimeError(f"{pr['name']} {r.status_code}: {r.text[:300]}")
    return from_openai(r.json()["choices"][0]["message"])
