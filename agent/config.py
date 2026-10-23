"""Shared settings for the CLI, setup checks and read-only clients."""
import os
import subprocess
from urllib.parse import urlsplit
from dotenv import load_dotenv

load_dotenv()


def setting(name, default='', alias=None):
    return (os.getenv(name) or (os.getenv(alias) if alias else '') or default).strip()


def prometheus_url():
    return setting('PROMETHEUS_URL', 'http://localhost:9090', 'PROM_URL').rstrip('/')


def jaeger_url():
    return setting('JAEGER_URL', 'http://localhost:8080/jaeger/ui').rstrip('/')


def http_timeout():
    return max(1, min(float(setting('HTTP_TIMEOUT', '12')), 15))


def opensearch_url():
    url = setting('OPENSEARCH_URL', alias='LOGS_URL')
    if url:
        return url.rstrip('/')
    try:
        result = subprocess.run(['docker', 'port', setting('OPENSEARCH_CONTAINER', 'opensearch'), '9200'], capture_output=True, text=True, timeout=5, check=True)
        port = int(result.stdout.strip().splitlines()[0].rsplit(':', 1)[1])
        return f'http://localhost:{port}'
    except (OSError, ValueError, IndexError, subprocess.SubprocessError):
        return 'http://localhost:9200'


def llm_settings():
    model = setting('LLM_MODEL', 'qwen3:8b')
    local = model.startswith(('ollama_chat/', 'ollama/'))
    model = model.removeprefix('ollama_chat/').removeprefix('ollama/')
    base = setting('LLM_BASE_URL')
    legacy_base = setting('LLM_API_BASE')
    if not base and legacy_base:
        base = legacy_base.rstrip('/')
        if not base.endswith('/v1'):
            base += '/v1'
    if not base and (local or not os.getenv('LLM_API_KEY')):
        base = 'http://localhost:11434/v1'
    if base:
        base = base.rstrip('/')
    hostname = urlsplit(base).hostname if base else None
    key = setting('LLM_API_KEY') or ('ollama' if hostname in ('localhost', '127.0.0.1', '::1') else '')
    if not key:
        raise ValueError('Set LLM_API_KEY for a hosted model or configure local Ollama')
    return {'model': model, 'base_url': base or None, 'api_key': key}


def llm_client():
    from openai import OpenAI
    settings = llm_settings()
    return OpenAI(api_key=settings['api_key'], base_url=settings['base_url'], timeout=float(setting('LLM_TIMEOUT', '120')), max_retries=0)
