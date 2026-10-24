"""Shared settings for the agent, setup checks and read-only clients (all overridable in .env)."""
import os
import subprocess
from dotenv import load_dotenv

load_dotenv()


def setting(name, default='', alias=None):
    return (os.getenv(name) or (os.getenv(alias) if alias else '') or default).strip()


def prometheus_url():
    return setting('PROMETHEUS_URL', 'http://localhost:9090', 'PROM_URL').rstrip('/')


def jaeger_url():
    """Base before /api (the demo serves Jaeger at /jaeger/ui)."""
    return setting('JAEGER_URL', 'http://localhost:8080/jaeger/ui').rstrip('/')


def http_timeout():
    return max(1, min(float(setting('HTTP_TIMEOUT', '12')), 15))


def opensearch_url():
    url = setting('OPENSEARCH_URL', alias='LOGS_URL')
    if url:
        return url.rstrip('/')
    try:
        result = subprocess.run(['docker', 'port', setting('OPENSEARCH_CONTAINER', 'opensearch'), '9200'],
                                capture_output=True, text=True, timeout=5, check=True)
        port = int(result.stdout.strip().splitlines()[0].rsplit(':', 1)[1])
        return f'http://localhost:{port}'
    except (OSError, ValueError, IndexError, subprocess.SubprocessError):
        return 'http://localhost:9200'


# --- local LLM (Ollama native API). Legacy prefixes / OpenAI-style /v1 bases are normalised.
LLM_MODEL = setting('LLM_MODEL', 'qwen3:8b').removeprefix('ollama_chat/').removeprefix('ollama/')
LLM_API_BASE = (setting('LLM_API_BASE') or setting('LLM_BASE_URL') or 'http://localhost:11434').rstrip('/').removesuffix('/v1')
LLM_NUM_CTX = int(setting('LLM_NUM_CTX', '16384'))
LLM_TIMEOUT = int(float(setting('LLM_TIMEOUT', '300')))
# 'ollama' (local, default) or 'openai' = any OpenAI-compatible hosted API (Gemini, Grok, OpenAI, Groq ...)
LLM_PROVIDER = setting('LLM_PROVIDER', 'ollama').lower()
LLM_HOSTED_BASE_URL = setting('LLM_HOSTED_BASE_URL').rstrip('/')
LLM_HOSTED_MODEL = setting('LLM_HOSTED_MODEL')
LLM_API_KEY = setting('LLM_API_KEY')

# Provider chain: tried in order, the first that answers wins; 'local' = Ollama. Only providers with a key are used.
PROVIDERS = {
    "groq": {"base": "https://api.groq.com/openai/v1", "key": "GROQ_API_KEY", "model": ("GROQ_MODEL", "llama-3.3-70b-versatile")},
    "gemini": {"base": "https://generativelanguage.googleapis.com/v1beta/openai", "key": "GEMINI_API_KEY",
               "model": ("GEMINI_MODEL", "gemini-3.8-flash")},
    "xai": {"base": "https://api.x.ai/v1", "key": "XAI_API_KEY", "model": ("XAI_MODEL", "grok-4-fast")},
}


def llm_chain() -> list[dict]:
    """Ordered providers to try, e.g. LLM_CHAIN=groq,gemini,local (default)."""
    out = []
    for name in [n.strip() for n in setting("LLM_CHAIN", "groq,gemini,xai,local").split(",") if n.strip()]:
        if name == "local":
            out.append({"name": "local", "model": LLM_MODEL})
        elif name == "custom" and LLM_HOSTED_BASE_URL and LLM_API_KEY:
            out.append({"name": "custom", "base": LLM_HOSTED_BASE_URL, "model": LLM_HOSTED_MODEL, "key": LLM_API_KEY})
        elif name in PROVIDERS and setting(PROVIDERS[name]["key"]):
            pr = PROVIDERS[name]
            out.append({"name": name, "base": pr["base"], "key": setting(pr["key"]),
                        "model": setting(pr["model"][0], pr["model"][1])})
    return out or [{"name": "local", "model": LLM_MODEL}]
