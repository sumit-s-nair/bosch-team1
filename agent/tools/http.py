"""Single read-only HTTP helper used by every telemetry tool."""
import requests
from agent.config import FORBIDDEN_URL_PARTS, HTTP_TIMEOUT


class ForbiddenSource(Exception):
    pass


def _check(url: str):
    low = url.lower()
    if any(p in low for p in FORBIDDEN_URL_PARTS):
        raise ForbiddenSource(f"Refusing to read {url}: feature-flag sources are off-limits")


def get(url: str, params=None):
    _check(url)
    r = requests.get(url, params=params, timeout=HTTP_TIMEOUT)
    r.raise_for_status()
    return r.json()


def post(url: str, json=None):
    """POST used only for read-only search queries (OpenSearch _search)."""
    _check(url)
    r = requests.post(url, json=json, timeout=HTTP_TIMEOUT)
    r.raise_for_status()
    return r.json()
