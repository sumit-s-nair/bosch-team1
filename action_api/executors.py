"""How each allowlisted action is carried out, using the demo's own controls (never by editing demo files).

restart_service          docker restart <container>  (also starts a stopped container)
throttle_load_generator  the load generator's web control (Locust /swarm)
toggle_feature_flag      the flag UI's own read/write API, the same thing a human clicking the flag page does.
                         Flag contents never leave this module: callers only get "flag X set to <state>".
scale_service            Kubernetes only -> refused on Docker Compose
"""
import json
import os
import re
import subprocess

import requests

DEMO_NETWORK = os.getenv("DOCKER_NETWORK", "opentelemetry-demo")
DEMO_BASE = os.getenv("DEMO_BASE_URL", "http://localhost:8080").rstrip("/")
TIMEOUT = 15
# Observability stack and the fault/flag machinery itself are never restarted by the agent.
PROTECTED = {"otel-collector", "prometheus", "jaeger", "opensearch", "grafana", "flagd", "flagd-ui",
             "load-generator", "frontend-proxy", "opamp-server", "telemetry-docs"}
NAME_RE = re.compile(r"^[a-z0-9][a-z0-9_.-]{0,62}$")


def _http_get(url):
    return requests.get(url, timeout=TIMEOUT)


def _http_post(url, **kw):
    return requests.post(url, timeout=TIMEOUT, **kw)


class ActionError(Exception):
    """A refusal or failure the caller should see (message is safe to return)."""


# ------------------------------------------------------------------ docker
def _docker(*args, timeout=60):
    r = subprocess.run(["docker", *args], capture_output=True, text=True, timeout=timeout)
    if r.returncode != 0:
        raise ActionError(f"docker {args[0]} failed: {r.stderr.strip()[:200]}")
    return r.stdout


def demo_containers() -> dict:
    out = _docker("ps", "-a", "--filter", f"network={DEMO_NETWORK}", "--format", "{{json .}}", timeout=20)
    rows = [json.loads(l) for l in out.splitlines() if l.strip()]
    return {r["Names"]: r["State"] for r in rows}


def check_restart(service: str) -> str:
    if not isinstance(service, str) or not NAME_RE.match(service):
        raise ActionError("invalid service name")
    if service in PROTECTED:
        raise ActionError(f"'{service}' is protected and cannot be restarted by the agent")
    state = demo_containers().get(service)
    if state is None:
        raise ActionError(f"no container named '{service}' on the demo network")
    return state


def restart_service(service: str) -> tuple:
    before = check_restart(service)
    _docker("restart", "-t", "10", service, timeout=90)
    after = demo_containers().get(service)
    return {"service": service, "state_before": before, "state_after": after}, None   # no undo for a restart


# ------------------------------------------------------------------ load generator
def _loadgen_users() -> int | None:
    try:
        return int(_http_get(f"{DEMO_BASE}/loadgen/stats/requests").json().get("user_count"))
    except Exception:
        return None


def check_throttle(users) -> int:
    if not isinstance(users, int) or not 0 <= users <= 50:
        raise ActionError("users must be an integer between 0 and 50")
    return users


def throttle_load_generator(users: int, spawn_rate: int = 5) -> tuple:
    users = check_throttle(users)
    before = _loadgen_users()
    r = _http_post(f"{DEMO_BASE}/loadgen/swarm", data={"user_count": users, "spawn_rate": spawn_rate})
    if not r.ok:
        raise ActionError(f"load generator refused: HTTP {r.status_code}")
    undo = {"action": "throttle_load_generator", "params": {"users": before}} if before is not None else None
    return {"users_before": before, "users_after": users}, undo


# ------------------------------------------------------------------ feature flags (contents never returned)
def _flags() -> dict:
    r = _http_get(f"{DEMO_BASE}/feature/api/read")
    if not r.ok:
        raise ActionError(f"flag service unavailable: HTTP {r.status_code}")
    return r.json().get("flags") or {}


def _off_variant(flag: dict) -> str | None:
    variants = flag.get("variants") or {}
    if "off" in variants:
        return "off"
    return next((k for k, v in variants.items() if v in (False, 0, 0.0, "off")), None)


def _set_variant(flag: dict, variant: str) -> str:
    """Same rule as the flag UI: a flag with targeting [cond, on, off] changes its 'on' branch, else defaultVariant."""
    cond = (flag.get("targeting") or {}).get("if")
    if isinstance(cond, list) and len(cond) == 3:
        prev, cond[1] = cond[1], variant
    else:
        prev, flag["defaultVariant"] = flag.get("defaultVariant"), variant
    return prev


def _words(text: str) -> set:
    """'productCatalogFailure' -> {product, catalog, failure}; 'readiness probe failure for cart service' -> words."""
    spaced = re.sub(r"(?<=[a-z0-9])(?=[A-Z])", " ", str(text or ""))
    return set(re.findall(r"[a-z0-9]+", spaced.lower()))


def _current_variant(flag: dict):
    cond = (flag.get("targeting") or {}).get("if")
    return cond[1] if isinstance(cond, list) and len(cond) == 3 else flag.get("defaultVariant")


def _is_on(flag: dict) -> bool:
    off = _off_variant(flag)
    return off is not None and _current_variant(flag) != off


def _relates(name: str, flag: dict, service: str) -> bool:
    """A flag relates to a service when every word of the service name appears in the flag's name or description
    ('product-catalog' <- productCatalogFailure; 'cart' <- 'readiness probe failure for cart service').
    Flags that describe themselves as not a failure scenario are left alone."""
    desc = str(flag.get("description") or "")
    if "not a failure" in desc.lower():
        return False
    want = set(re.findall(r"[a-z0-9]+", service.lower()))
    return bool(want) and (want <= _words(name) or want <= _words(desc))


def related_enabled_flags(service: str) -> list:
    """Enabled (non-off) flags related to a service. Used only to carry out an approved fix."""
    return sorted(n for n, f in _flags().items() if _is_on(f) and _relates(n, f, service))


def check_toggle(params: dict) -> None:
    flag, service, state = params.get("flag"), params.get("service"), params.get("state")
    if state not in ("off", "restore"):
        raise ActionError("state must be 'off' (restore happens through undo)")
    if flag is not None and (not isinstance(flag, str) or not re.match(r"^[A-Za-z0-9_.-]{1,64}$", flag)):
        raise ActionError("invalid flag name")
    if flag is None and (not isinstance(service, str) or not NAME_RE.match(service)):
        raise ActionError("give the service whose features should be turned off")


def preview_toggle(params: dict) -> str:
    if params.get("flag"):
        return f"turn off flag {params['flag']}"
    names = related_enabled_flags(params["service"])
    if not names:
        raise ActionError(f"no enabled feature flags related to {params['service']}")
    return f"turn off {len(names)} enabled feature flag(s) related to {params['service']}: {', '.join(names)}"


def toggle_feature_flag(params: dict) -> tuple:
    """Turn off one named flag, or every enabled flag related to `service`. Undo restores the previous variants."""
    flags = _flags()
    restore = params.get("_restore") or {}
    if restore:
        targets = list(restore)
    elif params.get("flag"):
        if params["flag"] not in flags:
            raise ActionError("unknown flag name")          # deliberately does not list the flags that exist
        targets = [params["flag"]]
    else:
        targets = sorted(n for n, f in flags.items() if _is_on(f) and _relates(n, f, params["service"]))
        if not targets:
            raise ActionError(f"no enabled feature flags related to {params['service']}")
    prev = {}
    for name in targets:
        variant = restore.get(name) or _off_variant(flags[name])
        if not variant or variant not in (flags[name].get("variants") or {}):
            raise ActionError(f"flag {name} has no 'off' variant")
        prev[name] = _set_variant(flags[name], variant)
    r = _http_post(f"{DEMO_BASE}/feature/api/write", json={"data": {"flags": flags}})
    if not r.ok:
        raise ActionError(f"flag service refused the change: HTTP {r.status_code}")
    undo = {"action": "toggle_feature_flag", "params": {"service": params.get("service"), "state": "restore"},
            "_restore": prev}
    return {"service": params.get("service"), "flags": targets, "state": "restored" if restore else "off"}, undo


def scale_service(service: str, replicas: int) -> dict:
    raise ActionError("scale_service needs Kubernetes; this demo runs on Docker Compose")
