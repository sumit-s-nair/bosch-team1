"""Team 1 Action API.   python -m action_api.server          (listens on 127.0.0.1:8090 by default)

Endpoints (JSON; every call except /health needs `Authorization: Bearer <ACTION_API_TOKEN>`):
  GET  /health                 liveness
  GET  /actions                the allowlist and each action's parameters
  POST /actions/dry-run        {action, params}                         validate + preview, changes nothing
  POST /actions/execute        {action, params, reason, approval:{approved_by, incident_id}}
  POST /actions/undo           {call_id, approval:{approved_by}}       roll back a reversible action
  GET  /audit?limit=50         the audit log, newest first
  GET  /kill                   kill-switch state
  POST /kill                   {on: true|false, by}                     engage / release the kill switch

Every request, accepted or refused, is appended to the audit log.
"""
import json
import os
import secrets
import threading
import time
from collections import deque
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

from dotenv import load_dotenv

from action_api import executors as ex

load_dotenv()

ALLOWLIST = {
    "restart_service": {"params": {"service": "container name"}, "reversible": False,
                        "check": lambda p: ex.check_restart(p.get("service")),
                        "run": lambda p: ex.restart_service(p["service"])},
    "throttle_load_generator": {"params": {"users": "integer 0-50"}, "reversible": True,
                                "check": lambda p: ex.check_throttle(p.get("users")),
                                "run": lambda p: ex.throttle_load_generator(p["users"])},
    "toggle_feature_flag": {"params": {"service": "service whose enabled features to turn off (or flag: one flag name)",
                                       "state": "'off'"}, "reversible": True,
                            "check": lambda p: ex.check_toggle(p),
                            "preview": lambda p: ex.preview_toggle(p),
                            "run": lambda p: ex.toggle_feature_flag(p)},
    "scale_service": {"params": {"service": "name", "replicas": "integer"}, "reversible": False,
                      "check": lambda p: ex.scale_service(p.get("service"), p.get("replicas")),
                      "run": lambda p: ex.scale_service(p.get("service"), p.get("replicas"))},
}
REPO_STOP = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "STOP")   # <repo>/STOP
MAX_EXECUTES_PER_10_MIN = int(os.getenv("ACTION_API_MAX_PER_10MIN", "5"))
TARGET_COOLDOWN_S = int(os.getenv("ACTION_API_COOLDOWN_S", "60"))


class State:
    def __init__(self, token: str, audit_path: str, kill_file: str | None = None):
        self.token = token
        self.audit_path = audit_path
        self.kill_file = kill_file or REPO_STOP    # the same STOP file the agent checks, wherever either was started
        self.kill = False
        self.lock = threading.Lock()          # one change at a time
        self.recent = deque()                 # execute timestamps (rate limit)
        self.last_target = {}                 # (action, target) -> time
        self.undo = {}                        # call_id -> undo record (kept server-side)
        os.makedirs(os.path.dirname(audit_path) or ".", exist_ok=True)

    def killed(self) -> bool:
        return self.kill or os.path.exists(self.kill_file) or os.path.exists("STOP")

    def audit(self, row: dict) -> None:
        row = {"t": time.strftime("%Y-%m-%d %H:%M:%S"), **row}
        with open(self.audit_path, "a", encoding="utf-8") as f:
            f.write(json.dumps(row, default=str) + "\n")


def _public(params: dict) -> dict:
    return {k: v for k, v in (params or {}).items() if not k.startswith("_")}


def handle(st: State, method: str, path: str, body: dict, query: dict) -> tuple[int, dict]:
    if method == "GET" and path == "/actions":
        return 200, {"actions": {k: {"params": v["params"], "reversible": v["reversible"]} for k, v in ALLOWLIST.items()}}
    if method == "GET" and path == "/audit":
        n = int((query.get("limit") or ["50"])[0])
        rows = open(st.audit_path, encoding="utf-8").read().splitlines() if os.path.exists(st.audit_path) else []
        return 200, {"entries": [json.loads(r) for r in reversed(rows[-n:])]}
    if path == "/kill":
        if method == "POST":
            st.kill = bool(body.get("on"))
            st.audit({"endpoint": "kill", "on": st.kill, "by": body.get("by")})
        return 200, {"kill_switch": st.killed()}

    if method == "POST" and path in ("/actions/dry-run", "/actions/execute"):
        action, params = body.get("action"), body.get("params") or {}
        entry = ALLOWLIST.get(action)
        rec = {"endpoint": path.rsplit("/", 1)[1], "action": action, "params": _public(params),
               "reason": body.get("reason"), "approval": body.get("approval")}
        if not entry:
            st.audit({**rec, "status": "refused", "why": "action not in allowlist"})
            return 403, {"status": "refused", "why": "action not in allowlist", "allowlist": sorted(ALLOWLIST)}
        if any(k.startswith("_") for k in params):
            st.audit({**rec, "status": "refused", "why": "internal parameter"})
            return 400, {"status": "refused", "why": "internal parameters are not accepted"}
        try:
            entry["check"](params)
        except ex.ActionError as e:
            st.audit({**rec, "status": "refused", "why": str(e)})
            return 400, {"status": "refused", "why": str(e)}
        if path.endswith("dry-run"):
            try:
                would = entry["preview"](params) if "preview" in entry else \
                    f"{action}({', '.join(f'{k}={v}' for k, v in params.items())})"
            except ex.ActionError as e:
                st.audit({**rec, "status": "refused", "why": str(e)})
                return 400, {"status": "refused", "why": str(e)}
            st.audit({**rec, "status": "dry_run", "would_do": would})
            return 200, {"status": "dry_run", "would_do": would, "reversible": entry["reversible"], "kill_switch": st.killed()}
        return execute(st, action, params, rec, entry)

    if method == "POST" and path == "/actions/undo":
        call_id = body.get("call_id")
        rec = {"endpoint": "undo", "call_id": call_id, "approval": body.get("approval")}
        u = st.undo.get(call_id)
        if not u:
            st.audit({**rec, "status": "refused", "why": "nothing to undo for this call"})
            return 404, {"status": "refused", "why": "nothing to undo for this call"}
        params = {**u["params"], **({"_restore": u["_restore"]} if "_restore" in u else {})}
        r = execute(st, u["action"], params, {**rec, "action": u["action"], "params": u["params"]},
                    ALLOWLIST[u["action"]], cooldown=False)
        if r[0] == 200:
            st.undo.pop(call_id, None)
        return r
    return 404, {"error": "not found"}


def execute(st, action, params, rec, entry, cooldown=True):
    appr = rec.get("approval") or {}
    if not str(appr.get("approved_by") or "").strip():
        st.audit({**rec, "status": "refused", "why": "no human approval"})
        return 403, {"status": "refused", "why": "no human approval: approval.approved_by is required"}
    if st.killed():
        st.audit({**rec, "status": "refused", "why": "kill switch on"})
        return 423, {"status": "refused", "why": "kill switch is on"}
    with st.lock:
        # A request may have waited behind another action while STOP/the kill switch changed.
        if st.killed():
            st.audit({**rec, "status": "refused", "why": "kill switch on"})
            return 423, {"status": "refused", "why": "kill switch is on"}
        now = time.time()
        while st.recent and now - st.recent[0] > 600:
            st.recent.popleft()
        if len(st.recent) >= MAX_EXECUTES_PER_10_MIN:
            st.audit({**rec, "status": "refused", "why": "rate limit"})
            return 429, {"status": "refused", "why": f"more than {MAX_EXECUTES_PER_10_MIN} actions in 10 minutes"}
        key = (action, json.dumps(_public(params), sort_keys=True))
        if cooldown and now - st.last_target.get(key, 0) < TARGET_COOLDOWN_S:
            st.audit({**rec, "status": "refused", "why": "cooldown"})
            return 429, {"status": "refused", "why": f"same action on the same target within {TARGET_COOLDOWN_S}s"}
        call_id = "act-" + secrets.token_hex(6)
        try:
            result, undo = entry["run"](params)
        except ex.ActionError as e:
            st.audit({**rec, "call_id": call_id, "status": "failed", "why": str(e)})
            return 502, {"status": "failed", "call_id": call_id, "why": str(e)}
        except Exception as e:   # unexpected: report, never crash the API
            st.audit({**rec, "call_id": call_id, "status": "failed", "why": f"{type(e).__name__}"})
            return 500, {"status": "failed", "call_id": call_id, "why": f"{type(e).__name__}: {str(e)[:200]}"}
        st.recent.append(now)
        st.last_target[key] = now
        if undo:
            st.undo[call_id] = undo
        st.audit({**rec, "call_id": call_id, "status": "executed", "result": result})
        return 200, {"status": "executed", "call_id": call_id, "result": result, "undo_available": bool(undo)}


def make_handler(st: State):
    class H(BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def _send(self, code, obj):
            data = json.dumps(obj, default=str).encode()
            self.send_response(code)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def _do(self, method):
            u = urlparse(self.path)
            if u.path == "/health":
                return self._send(200, {"ok": True, "kill_switch": st.killed()})
            if not secrets.compare_digest(self.headers.get("Authorization", ""), f"Bearer {st.token}"):
                return self._send(401, {"error": "missing or wrong token"})
            body = {}
            if method == "POST":
                try:
                    body = json.loads(self.rfile.read(int(self.headers.get("Content-Length", 0) or 0)) or b"{}")
                    if not isinstance(body, dict):
                        raise ValueError
                except Exception:
                    return self._send(400, {"error": "body must be a JSON object"})
            code, obj = handle(st, method, u.path, body, parse_qs(u.query))
            self._send(code, obj)

        def do_GET(self):
            self._do("GET")

        def do_POST(self):
            self._do("POST")
    return H


def serve(host="127.0.0.1", port=8090, token=None, audit_path="runs/action_api_audit.jsonl"):
    token = token or os.getenv("ACTION_API_TOKEN")
    if not token:
        raise SystemExit("Set ACTION_API_TOKEN in .env (any long random string) before starting the Action API.")
    st = State(token, audit_path)
    srv = ThreadingHTTPServer((host, port), make_handler(st))
    return srv, st


if __name__ == "__main__":
    port = int(os.getenv("ACTION_API_PORT", "8090"))
    srv, _ = serve(port=port)
    print(f"Action API on http://127.0.0.1:{port}  (allowlist: {', '.join(ALLOWLIST)}; kill switch: STOP file or POST /kill)")
    srv.serve_forever()
