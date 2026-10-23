"""Rule-based alerting on span metrics + container state (no LLM). Mirrors an error-rate / latency alert."""
import time
from agent import observe
from agent.tools import containers

ERR_THRESHOLD = 0.05        # 5% server errors
LAT_FACTOR = 3.0            # p95 > 3x baseline ...
LAT_MIN_MS = 300            # ... and above 300ms
LAT_ABS_MS = 1500           # fallback when no baseline exists yet
MIN_RPS = 0.05


def check() -> list[dict]:
    alerts = []
    for s, v in observe.service_health_raw().items():
        if v["rps"] >= MIN_RPS and v["err_rate"] is not None and v["err_rate"] >= ERR_THRESHOLD \
                and v["err_rate"] > 2 * (v["base_err_rate"] or 0):
            alerts.append({"type": "error_rate", "service": s, "value": round(v["err_rate"], 3),
                           "baseline": v["base_err_rate"]})
        b = v["base_p95_ms"]
        p = v["p95_ms"]
        if p and p == p:
            if b and b == b and p > max(LAT_FACTOR * b, LAT_MIN_MS):
                alerts.append({"type": "latency", "service": s, "value": round(p), "baseline": round(b)})
            elif not (b and b == b) and p > LAT_ABS_MS:   # no baseline yet: absolute fallback
                alerts.append({"type": "latency", "service": s, "value": round(p), "baseline": None})
        if v["base_rps"] and v["base_rps"] > 0.2 and v["rps"] < 0.2 * v["base_rps"]:
            alerts.append({"type": "traffic_drop", "service": s, "value": round(v["rps"], 2),
                           "baseline": round(v["base_rps"], 2)})
    for c in containers.list_containers():
        if c["state"] != "running" or "unhealthy" in c["status"] or "Restarting" in c["status"]:
            alerts.append({"type": "container", "service": c["name"], "value": c["status"]})
    return alerts


def wait_for_alert(poll_s: int = 15, confirm: int = 2) -> list[dict]:
    """Block until the same alert key fires on `confirm` consecutive polls."""
    streak: dict = {}
    while True:
        alerts = check()
        keys = {(a["type"], a["service"]) for a in alerts}
        streak = {k: streak.get(k, 0) + 1 for k in keys}
        firing = [a for a in alerts if streak[(a["type"], a["service"])] >= confirm]
        print(time.strftime("%H:%M:%S"), "alerts:", [f"{a['type']}:{a['service']}" for a in alerts] or "none")
        if firing:
            return firing
        time.sleep(poll_s)
