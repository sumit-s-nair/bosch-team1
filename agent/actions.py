"""FIX phase: allowlisted runbook actions, executed ONLY through the organisers' Action API, ONLY after approval.

- ALLOWLIST: the only actions that exist (from the brief: restart a service, toggle a feature flag,
  throttle the load generator, scale a service on Kubernetes).
- plan(): maps the diagnosis category to an action. No match -> no action, escalate.
- Kill switch: create a file named STOP in the repo root (or set AGENT_KILL=1) and nothing executes.
- Every call (and every refusal) is appended to the incident's actions.jsonl audit log.
- Until ACTION_API_URL is set, execution is dry-run only.
"""
import json
import os
import time

import requests

from agent import config

ALLOWLIST = {
    "restart_service": {"params": ["service"], "desc": "Restart one service container"},
    "throttle_load_generator": {"params": ["users"], "desc": "Reduce load-generator users"},
    "toggle_feature_flag": {"params": ["flag", "state"], "desc": "Turn a feature flag on/off (human must name the flag)"},
    "scale_service": {"params": ["service", "replicas"], "desc": "Scale a service (Kubernetes only)"},
}

HUMAN_PICKS = "<named by the human at approval>"   # the agent never reads flags, so it cannot choose one
CACHE_WORDS = ("valkey", "redis", "cache", "memcached")


def _evidence_container(diag, words):
    """A container from the cited evidence whose name matches `words` (e.g. the cache that is down)."""
    for e in diag.get("evidence", []):
        ref = str(e.get("ref") or "")
        if ref.startswith("container:") and any(w in ref.lower() for w in words):
            return ref.split(":", 1)[1]
    return None


def _restart_blamed(rc, diag):
    return {"service": rc["service"]}


def _restart_cache(rc, diag):
    svc = rc.get("service") or ""
    target = svc if any(w in svc.lower() for w in CACHE_WORDS) else _evidence_container(diag, CACHE_WORDS)
    return {"service": target} if target else None


def _disable_feature(rc, diag):
    # An application-level fault in a healthy container: restarting will not change the failing code path.
    # The usual safe mitigation is turning off the recently enabled feature behind it; a human names the flag.
    return {"flag": HUMAN_PICKS, "state": "off", "service": rc["service"]}


# category -> (action, params builder). Generic: keyed on category + diagnosed component, never on incident names.
RUNBOOK = {
    "container_down": ("restart_service", _restart_blamed),
    "database_unavailable": ("restart_service", _restart_blamed),
    "dependency_failure": ("restart_service", _restart_blamed),
    "resource_exhaustion": ("restart_service", _restart_blamed),     # frees leaked memory / stuck threads (mitigation)
    "cache_unavailable": ("restart_service", _restart_cache),
    "service_error": ("toggle_feature_flag", _disable_feature),
    "traffic_spike": ("throttle_load_generator", lambda rc, diag: {"users": 5}),
    # database_slow / latency_or_network / unknown: no safe allowlisted fix -> escalate with findings
}
NOT_RESTARTABLE = {"load-generator", "otel-collector", "prometheus", "jaeger", "opensearch", "grafana", "flagd", "flagd-ui"}


def kill_switch_on() -> bool:
    return os.path.exists("STOP") or os.getenv("AGENT_KILL") == "1"


def plan(diag: dict) -> dict | None:
    """Pick one allowlisted action for a propose_fix diagnosis, with a reason and a rollback note."""
    if diag.get("decision") != "propose_fix":
        return None
    rc = diag.get("root_cause", {})
    entry = RUNBOOK.get(rc.get("category"))
    if not entry:
        return None
    action, params_fn = entry
    params = params_fn(rc, diag)
    if not params or (action == "restart_service" and (not params.get("service") or params["service"] in NOT_RESTARTABLE)):
        return None
    return {"action": action, "params": params,
            "reason": f"{rc.get('category')} in {rc.get('service')}: {rc.get('description', '')}"[:300],
            "expected_effect": "alerting signals return to baseline within ~2 minutes",
            "rollback": ("turn the flag back on" if action == "toggle_feature_flag"
                         else "none needed for a restart/throttle") + "; if not recovered, escalate to a human"}


class ActionClient:
    def __init__(self, incident, dry_run: bool | None = None):
        self.inc = incident
        self.url = config.setting("ACTION_API_URL").rstrip("/")
        self.dry_run = (not self.url) if dry_run is None else dry_run

    def _audit(self, row):
        with open(os.path.join(self.inc.dir, "actions.jsonl"), "a", encoding="utf-8") as f:
            f.write(json.dumps({"t": time.strftime("%H:%M:%S"), **row}) + "\n")

    def preview(self, p: dict) -> str:
        """Read-only dry run: what would be sent. Allowed without approval."""
        return f"{p['action']}({', '.join(f'{k}={v}' for k, v in p['params'].items())}) -> " + \
               (f"POST {self.url}/actions" if self.url else "Action API not configured (dry run only)")

    def execute(self, p: dict, approved_by: str) -> dict:
        if p["action"] not in ALLOWLIST:
            r = {"status": "refused", "why": "action not in allowlist"}
        elif kill_switch_on():
            r = {"status": "refused", "why": "kill switch is on"}
        elif not approved_by:
            r = {"status": "refused", "why": "no human approval"}
        elif HUMAN_PICKS in p["params"].values():
            r = {"status": "refused", "why": "a parameter still needs to be named by the human"}
        elif self.dry_run:
            r = {"status": "dry_run", "would_send": self.preview(p)}
        else:
            # TODO-ALIGN: request shape of the organisers' Action API
            resp = requests.post(f"{self.url}/actions", json={"action": p["action"], **p["params"], "reason": p["reason"]},
                                 timeout=30)
            r = {"status": "executed" if resp.ok else "failed", "http": resp.status_code, "body": resp.text[:300]}
        self._audit({"plan": p, "approved_by": approved_by, **r})
        return r
