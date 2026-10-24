"""Agent server: the backend the dashboard (ui/dashboard) talks to.   python -m agent.server   (127.0.0.1:8000)

  GET  /api/health              agent server up, current run, pending question, Action API reachable + kill state
  GET  /api/incidents           every run in runs/, newest first, with a status
  GET  /api/incidents/<id>      everything that run wrote: alert, diagnosis, plan (incl. dry_run_preview),
                                approval, validation, rollback, report (+ report.md), timeline
  POST /api/run    {mode}       start pipeline.run(mode="now"|"watch") in a background thread (one run at a time)
  GET  /api/pending             the question the running agent is waiting on (or null)
  POST /api/answer {id, answer} answer it; the agent continues exactly as if the answer was typed in its terminal
  GET  /api/kill                kill-switch state, read from the Action API
  POST /api/kill   {on, by}     engage / release the Action API kill switch (token stays server-side)

The approval gate stays in the agent: pipeline.run() asks through ask_fn and blocks until the human answers.
The browser never calls the Action API and never sees ACTION_API_TOKEN; every fix goes
UI -> agent server -> agent (approval record, audit, verify, rollback) -> Action API.
Binds to 127.0.0.1 only; CORS allows the local dashboard (http://localhost:3000).
"""
import json
import os
import re
import secrets
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse

import requests

from agent import config, pipeline
from agent.incident import Incident

RUN_ID_RE = re.compile(r"^\d{8}-\d{6}$")          # runs/<id> folders the pipeline creates
ALLOWED_ORIGINS = {"http://localhost:3000", "http://127.0.0.1:3000"}


def prompt_kind(prompt: str) -> str:
    """What the agent is asking, so the UI can show the right buttons. The full prompt text is always shown."""
    p = prompt.lower()
    for kind, words in (("rollback", "roll back"), ("approve", "approve this action"), ("human_param", "cannot choose"),
                        ("name", "your name"), ("action_api_down", "start it and press enter"),
                        ("acknowledge", "acknowledge")):
        if words in p:
            return kind
    return "text"


class Agent:
    """One pipeline run at a time, in a background thread. ask() is the pipeline's ask_fn."""

    def __init__(self, root="runs", runner=None):
        self.root = root
        self.runner = runner or pipeline.run
        self.lock = threading.Lock()
        self.thread = None
        self.run_id = self.mode = self.error = None
        self.pending = None                 # {"id", "run_id", "prompt", "kind", "asked_at"}
        self._event = threading.Event()
        self._answer = ""

    def running(self) -> bool:
        return bool(self.thread and self.thread.is_alive())

    def start(self, mode: str) -> str | None:
        with self.lock:
            if self.running():
                return None
            inc = Incident(self.root)
            self.run_id, self.mode, self.error, self.pending = inc.id, mode, None, None
            self.thread = threading.Thread(target=self._run, args=(mode, inc), daemon=True)
            self.thread.start()
            return inc.id

    def _run(self, mode, inc):
        try:
            self.runner(mode=mode, ask_fn=self.ask, incident=inc)
        except Exception as e:   # report it on the run, never crash the server
            self.error = f"{type(e).__name__}: {str(e)[:300]}"
            inc.event("error", self.error)

    def ask(self, prompt: str = "") -> str:
        """Called by the pipeline thread: publish the question, block until the browser answers."""
        with self.lock:
            self._event = threading.Event()
            self.pending = {"id": secrets.token_hex(4), "run_id": self.run_id, "prompt": prompt.strip(),
                            "kind": prompt_kind(prompt), "asked_at": time.strftime("%H:%M:%S")}
            event = self._event
        print(f"[waiting for the dashboard] {prompt.strip()}")
        event.wait()
        with self.lock:
            answer, self.pending = self._answer, None
        print(f"[answered from the dashboard] {answer!r}")
        return answer

    def answer(self, pending_id: str, text: str) -> bool:
        with self.lock:
            if not self.pending or self.pending["id"] != pending_id:
                return False      # stale or unknown question: ignore, never apply it to a different one
            self._answer = str(text or "")
            self._event.set()
            return True


# ------------------------------------------------------------------ reading runs/
def _read(path, default=None):
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return default


def _latest(d: str, base: str):
    """The last round's file: plan-round2.json beats plan.json."""
    for name in (f"{base}-round{n}.json" for n in range(9, 1, -1)):
        if os.path.exists(os.path.join(d, name)):
            return _read(os.path.join(d, name))
    return _read(os.path.join(d, f"{base}.json"))


def _timeline(d: str) -> list:
    try:
        with open(os.path.join(d, "timeline.jsonl"), encoding="utf-8") as f:
            return [json.loads(line) for line in f if line.strip()]
    except (OSError, ValueError):
        return []


OUTCOME_STATUS = (("no incident", "No incident"), ("fixed and verified", "Resolved"), ("rolled back", "Rolled back"),
                  ("dry run", "Dry run"), ("rejected", "Rejected"))
PHASE_STATUS = {"detect": "Watching", "investigate": "Investigating", "human": "Awaiting approval",
                "fix": "Acting", "validate": "Verifying"}


def run_status(report, timeline, active: bool, pending: bool) -> str:
    if report:
        outcome = report.get("outcome") or ""
        return next((st for key, st in OUTCOME_STATUS if key in outcome), "Escalated")
    if not active:
        return "Interrupted"      # agent server stopped before this run finished
    if pending:
        return "Awaiting approval"
    return PHASE_STATUS.get(timeline[-1]["phase"] if timeline else "detect", "Investigating")


def incident(agent: Agent, run_id: str, full=False) -> dict:
    d = os.path.join(agent.root, run_id)
    alerts = _read(os.path.join(d, "alert.json")) or []
    diag, report, timeline = _latest(d, "diagnosis") or {}, _read(os.path.join(d, "report.json")), _timeline(d)
    active = agent.running() and agent.run_id == run_id
    pending = active and agent.pending is not None
    rc = diag.get("root_cause") or {}
    first = alerts[0] if alerts else {}
    out = {"id": run_id, "status": run_status(report, timeline, active, pending), "active": active,
           "alert_type": first.get("type"), "service": first.get("service"),
           "title": diag.get("summary") or (f"{first.get('type')} alert on {first.get('service')}" if first else "Watching for alerts"),
           "stage": timeline[-1]["msg"] if timeline else "starting", "started": run_id,
           "confidence": diag.get("confidence"), "component": rc.get("service"), "category": rc.get("category"),
           "cause": rc.get("description") or diag.get("summary"), "decision": diag.get("decision"),
           "outcome": (report or {}).get("outcome")}
    if full:
        md = os.path.join(d, "report.md")
        out.update(alerts=alerts, diagnosis=diag or None, plan=_latest(d, "plan"), approval=_latest(d, "approval"),
                   validation=_latest(d, "validation"), rollback=_latest(d, "rollback"), report=report,
                   report_md=open(md, encoding="utf-8").read() if os.path.exists(md) else None, timeline=timeline)
    return out


def incidents(agent: Agent) -> list:
    if not os.path.isdir(agent.root):
        return []
    ids = sorted((n for n in os.listdir(agent.root) if RUN_ID_RE.match(n) and os.path.isdir(os.path.join(agent.root, n))),
                 reverse=True)
    return [incident(agent, i) for i in ids[:50]]


# ------------------------------------------------------------------ Action API (server-side only)
def _action_api(method: str, path: str, body=None):
    url = config.setting("ACTION_API_URL").rstrip("/")
    if not url:
        return 503, {"error": "ACTION_API_URL is not set: there is no Action API, so nothing can execute", "kill_switch": None}
    try:
        r = requests.request(method, f"{url}{path}", json=body, timeout=5,
                             headers={"Authorization": f"Bearer {config.setting('ACTION_API_TOKEN')}"})
        return r.status_code, r.json()
    except (requests.RequestException, ValueError) as e:
        return 502, {"error": f"Action API unreachable at {url} ({type(e).__name__})", "kill_switch": None}


def health(agent: Agent) -> dict:
    url = config.setting("ACTION_API_URL").rstrip("/")
    api = {"configured": bool(url), "reachable": False, "kill_switch": None}
    if url:
        try:
            h = requests.get(f"{url}/health", timeout=2).json()
            api.update(reachable=True, kill_switch=bool(h.get("kill_switch")))
        except (requests.RequestException, ValueError):
            pass
    return {"ok": True, "agent_running": agent.running(), "run_id": agent.run_id, "mode": agent.mode,
            "last_error": agent.error, "pending": agent.pending is not None, "model": config.LLM_MODEL,
            "action_api": api, "server_time": time.strftime("%Y-%m-%dT%H:%M:%S%z")}


# ------------------------------------------------------------------ routes
def handle(agent: Agent, method: str, path: str, body: dict) -> tuple[int, dict]:
    if method == "GET" and path == "/api/health":
        return 200, health(agent)
    if method == "GET" and path == "/api/incidents":
        return 200, {"incidents": incidents(agent)}
    if method == "GET" and path.startswith("/api/incidents/"):
        run_id = path.rsplit("/", 1)[1]
        if not RUN_ID_RE.match(run_id) or not os.path.isdir(os.path.join(agent.root, run_id)):
            return 404, {"error": "no such incident"}
        return 200, incident(agent, run_id, full=True)
    if method == "POST" and path == "/api/run":
        mode = body.get("mode", "now")
        if mode not in ("now", "watch"):
            return 400, {"error": "mode must be 'now' or 'watch'"}
        run_id = agent.start(mode)
        if not run_id:
            return 409, {"error": f"a run is already in progress ({agent.run_id})"}
        return 202, {"run_id": run_id, "mode": mode}
    if method == "GET" and path == "/api/pending":
        return 200, {"pending": agent.pending}
    if method == "POST" and path == "/api/answer":
        if not agent.answer(str(body.get("id") or ""), body.get("answer", "")):
            return 409, {"error": "that question is no longer waiting (already answered or the run moved on)"}
        return 200, {"ok": True}
    if path == "/api/kill":
        if method == "POST":
            return _action_api("POST", "/kill", {"on": bool(body.get("on")), "by": str(body.get("by") or "dashboard")})
        return _action_api("GET", "/kill")
    return 404, {"error": "not found"}


def make_handler(agent: Agent):
    class H(BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def _cors(self):
            origin = self.headers.get("Origin", "")
            if origin in ALLOWED_ORIGINS:
                self.send_header("Access-Control-Allow-Origin", origin)
                self.send_header("Vary", "Origin")

        def _send(self, code, obj):
            data = json.dumps(obj, default=str).encode()
            self.send_response(code)
            self._cors()
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def do_OPTIONS(self):          # CORS preflight for the dashboard's JSON POSTs
            self.send_response(204)
            self._cors()
            self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
            self.send_header("Access-Control-Allow-Headers", "Content-Type")
            self.end_headers()

        def _do(self, method):
            body = {}
            if method == "POST":
                try:
                    body = json.loads(self.rfile.read(int(self.headers.get("Content-Length", 0) or 0)) or b"{}")
                    if not isinstance(body, dict):
                        raise ValueError
                except Exception:
                    return self._send(400, {"error": "body must be a JSON object"})
            self._send(*handle(agent, method, urlparse(self.path).path, body))

        def do_GET(self):
            self._do("GET")

        def do_POST(self):
            self._do("POST")
    return H


def serve(host="127.0.0.1", port=8000, agent: Agent | None = None):
    agent = agent or Agent()
    return ThreadingHTTPServer((host, port), make_handler(agent)), agent


if __name__ == "__main__":
    port = int(config.setting("AGENT_SERVER_PORT", "8000"))
    srv, _ = serve(port=port)
    print(f"Agent server on http://127.0.0.1:{port}  (dashboard: cd ui/dashboard; pnpm dev)")
    srv.serve_forever()
