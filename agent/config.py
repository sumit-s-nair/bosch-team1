"""Central config. Every endpoint the agent may read is listed here and nowhere else."""
import os
import subprocess
from dotenv import load_dotenv

load_dotenv()

PROMETHEUS_URL = os.getenv("PROMETHEUS_URL", "http://localhost:9090").rstrip("/")
JAEGER_URL = os.getenv("JAEGER_URL", "http://localhost:8080/jaeger/ui").rstrip("/")
LLM_MODEL = os.getenv("LLM_MODEL", "gpt-4o-mini")
HTTP_TIMEOUT = float(os.getenv("HTTP_TIMEOUT", "10"))

# Hackathon rule: diagnosis must come from telemetry, never from feature-flag settings.
# Any URL containing one of these is refused by agent.tools.http.get().
FORBIDDEN_URL_PARTS = ("flagd", "/feature", "8013", "8016", "flag")


def opensearch_url() -> str:
    url = os.getenv("OPENSEARCH_URL", "").strip()
    if url:
        return url.rstrip("/")
    try:
        out = subprocess.run(["docker", "port", "opensearch", "9200"],
                             capture_output=True, text=True, timeout=5).stdout
        # e.g. "0.0.0.0:32768\n[::]:32768"
        port = out.strip().splitlines()[0].rsplit(":", 1)[1]
        return f"http://localhost:{port}"
    except Exception:
        return "http://localhost:9200"
