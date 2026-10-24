"""FIX phase: allowlisted runbook actions, executed ONLY through our Action API (action_api/), ONLY after approval.

- ALLOWLIST: the only actions that exist (from the brief: restart a service, toggle a feature flag,
  throttle the load generator, scale a service on Kubernetes).
- plan(): maps the diagnosis category to an action. No match -> no action, escalate.
- Kill switch: create a file named STOP in the repo root (or set AGENT_KILL=1) and nothing executes. The repo root is
  found from this file, so it works whatever folder the agent was started from; STOP in that folder also counts.
- Every call (and every refusal) is appended to the incident's actions.jsonl audit log.
- The agent never changes the system itself: it sends the approved plan to the Action API, which re-checks
  everything (allowlist, approval, kill switch, rate limits) and keeps its own audit log.
- Until ACTION_API_URL is set, execution is dry-run only.
"""
import json
import os
import time
from pathlib import Path

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
    "traffic_spike": ("throttle_load_generator", lambda rc, diag: {"users": 1}),   # demo default is 5 users
    # database_slow / latency_or_network / unknown: no safe allowlisted fix -> escalate with findings
}
NOT_RESTARTABLE = {"load-generator", "otel-collector", "prometheus", "jaeger", "opensearch", "grafana", "flagd", "flagd-ui"}


KILL_FILE = Path(__file__).resolve().parents[1] / "STOP"     # <repo>/STOP, independent of the working directory


def kill_switch_on() -> bool:
    return os.path.exists(KILL_FILE) or os.path.exists("STOP") or os.getenv("AGENT_KILL") == "1"


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
    """Talks to the Action API. Local checks here are a second line of defence; the API enforces them again."""

    def __init__(self, incident, dry_run: bool | None = None):
        self.inc = incident
        self.url = config.setting("ACTION_API_URL").rstrip("/")
        self.token = config.setting("ACTION_API_TOKEN")
        self.dry_run = (not self.url) if dry_run is None else dry_run

    def _audit(self, row):
        with open(os.path.join(self.inc.dir, "actions.jsonl"), "a", encoding="utf-8") as f:
            f.write(json.dumps({"t": time.strftime("%H:%M:%S"), **row}) + "\n")

    def _post(self, path: str, body: dict) -> dict:
        try:
            r = requests.post(f"{self.url}{path}", json=body, timeout=120,
                              headers={"Authorization": f"Bearer {self.token}"})
            out = r.json() if r.headers.get("content-type", "").startswith("application/json") else {"body": r.text[:300]}
            return {"http": r.status_code, **out}
        except requests.RequestException as e:
            return {"status": "failed", "why": f"Action API unreachable: {type(e).__name__}"}

    def health(self) -> tuple[bool, str]:
        """Is the Action API reachable? (Always 'ok' in dry-run-only mode.)"""
        if not self.url:
            return True, "dry-run only (no ACTION_API_URL)"
        try:
            r = requests.get(f"{self.url}/health", timeout=5)
            if r.ok and r.json().get("kill_switch"):
                return False, "Action API kill switch is on"
            return r.ok, f"HTTP {r.status_code}"
        except requests.RequestException as e:
            return False, f"Action API unreachable at {self.url} ({type(e).__name__})"

    def preview(self, p: dict) -> str:
        """Read-only dry run, allowed without approval. With the API up, it also validates the target."""
        call = f"{p['action']}({', '.join(f'{k}={v}' for k, v in p['params'].items())})"
        if not self.url:
            return f"{call} -> Action API not configured (dry run only)"
        if HUMAN_PICKS in p["params"].values():
            return f"{call} -> Action API (a parameter will be named by the human at approval)"
        r = self._post("/actions/dry-run", {"action": p["action"], "params": p["params"]})
        return f"{call} -> Action API dry run: {r.get('status')}" + (f" ({r.get('why')})" if r.get("why") else "")

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
            r = self._post("/actions/execute", {"action": p["action"], "params": p["params"], "reason": p["reason"],
                                                "approval": {"approved_by": approved_by, "incident_id": self.inc.id}})
            r.setdefault("status", "failed")
        self._audit({"plan": p, "approved_by": approved_by, **r})
        return r

    def undo(self, call_id: str, approved_by: str) -> dict:
        """Roll back a reversible action (throttle, flag). Needs its own human approval."""
        if kill_switch_on():
            r = {"status": "refused", "why": "kill switch is on"}
        elif not approved_by:
            r = {"status": "refused", "why": "no human approval"}
        else:
            r = self._post("/actions/undo", {"call_id": call_id, "approval": {"approved_by": approved_by,
                                                                                "incident_id": self.inc.id}})
            r.setdefault("status", "failed")
        self._audit({"undo_of": call_id, "approved_by": approved_by, **r})
        return r
