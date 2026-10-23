"""Container health via the docker CLI (read-only commands only)."""
import json
import subprocess

READ_ONLY = ("ps", "inspect", "stats", "logs")


def _docker(*args) -> str:
    assert args[0] in READ_ONLY, "containers tool is read-only"
    return subprocess.run(["docker", *args], capture_output=True, text=True, timeout=20).stdout


def list_containers() -> list:
    out = _docker("ps", "-a", "--filter", "network=opentelemetry-demo", "--format", "{{json .}}")
    rows = [json.loads(l) for l in out.splitlines() if l.strip()]
    return [{"name": r["Names"], "state": r["State"], "status": r["Status"]} for r in rows]


def inspect(name: str) -> dict:
    d = json.loads(_docker("inspect", name) or "[{}]")[0]
    st = d.get("State", {})
    return {"name": name, "status": st.get("Status"), "running": st.get("Running"),
            "restart_count": d.get("RestartCount"), "exit_code": st.get("ExitCode"),
            "oom_killed": st.get("OOMKilled"), "started_at": st.get("StartedAt"),
            "finished_at": st.get("FinishedAt"), "error": st.get("Error")}


def stats() -> list:
    out = _docker("stats", "--no-stream", "--format", "{{json .}}")
    return [json.loads(l) for l in out.splitlines() if l.strip()]
