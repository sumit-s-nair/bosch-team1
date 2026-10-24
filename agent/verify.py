"""VALIDATE phase: after the fix, check (1) the service the fix targeted, on a short 1-minute window, and
(2) the signals that raised the alert. Separates three outcomes:
  recovered          target healthy and the original alerts cleared
  target_recovered   the fixed service is healthy, but other alerts remain (likely another fault): no rollback
  not recovered      the fixed service itself is still failing
"""
import time

from agent import detector
from agent.tools import containers, metrics, observe

SHORT = "1m"


def target_healthy(service: str | None) -> tuple[bool, str]:
    """Fast check of one service: no error spans in the last minute, container (if any) running and not restarting."""
    if not service:
        return False, "no target"
    try:
        c = {x["name"]: x for x in containers.list_containers()}.get(service)
    except Exception:
        c = None
    if c is not None and (c["state"] != "running" or "Restarting" in c["status"] or "unhealthy" in c["status"]):
        return False, f"container {c['status']}"
    try:
        rows = metrics.promql(f'sum(increase(traces_span_metrics_calls_total{{service_name="{service}",'
                              f'{observe.ERR}}}{metrics.rng(SHORT)}))')
        errs = rows[0]["value"] if rows and rows[0]["value"] is not None else 0.0
    except Exception:
        return False, "metrics unavailable"
    if errs >= 1:
        return False, f"{errs:.0f} error spans in the last minute"
    return True, "no errors in the last minute" + (", container running" if c is not None else "")


def recovered(original_alerts: list[dict], target: str | None = None, settle_s: int = 60, polls: int = 3,
              poll_s: int = 20, expected: tuple = ()) -> dict:
    """target: the service the fix acted on. expected: alert types the fix itself causes (throttling the load
    generator lowers traffic on purpose, so a traffic_drop afterwards is the fix working). A poll whose telemetry is
    missing never counts as clean. Full recovery needs min(3, polls) clean polls in a row."""
    if polls < 1 or settle_s < 0 or poll_s < 0:
        raise ValueError("polls must be positive; delays must be nonnegative")
    required = min(3, polls)
    time.sleep(settle_s)
    keys = {(a["type"], a["service"]) for a in original_alerts if a.get("type") != "manual"}
    history, clean = [], 0
    for i in range(polls):
        ok, why = target_healthy(target) if target else (None, "")
        try:
            now = detector.check(strict=True, original_alerts=original_alerts)
            still = [a for a in now if (a["type"], a["service"]) in keys]
            new = [a for a in now if (a["type"], a["service"]) not in keys and a["type"] not in expected]
            caused = [a for a in now if (a["type"], a["service"]) not in keys and a["type"] in expected]
            clean = clean + 1 if not still and not new else 0
            history.append({"poll": i + 1, "still_firing": still, "new_alerts": new, "expected_from_fix": caused,
                            "telemetry_valid": True, "target": target, "target_healthy": ok, "target_detail": why})
        except Exception as exc:
            clean = 0
            history.append({"poll": i + 1, "still_firing": [], "new_alerts": [], "telemetry_valid": False,
                            "error": f"{type(exc).__name__}: {str(exc)[:300]}", "target": target,
                            "target_healthy": ok, "target_detail": why})
        if i < polls - 1:
            time.sleep(poll_s)
    last2 = history[-2:]
    target_ok = bool(target) and all(h["target_healthy"] for h in last2)
    valid = [h for h in history if h["telemetry_valid"]]
    last = valid[-1] if valid else {"still_firing": [], "new_alerts": []}
    # the fix's own service must be healthy; alerts on OTHER services are reported, not counted against the fix
    others = [a for a in last["still_firing"] + last["new_alerts"] if a["service"] != target]
    return {"recovered": clean >= required and (target_ok or not target), "target_recovered": target_ok,
            "other_alerts": others, "required_clean_polls": required, "consecutive_clean_polls": clean, "polls": history}
