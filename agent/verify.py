"""VALIDATE phase: after the fix, re-check the same signals that raised the alert."""
import time

from agent import detector


def recovered(original_alerts: list[dict], settle_s: int = 60, polls: int = 3, poll_s: int = 20,
              expected: tuple = ()) -> dict:
    """expected: alert types the fix itself causes (throttling the load generator lowers traffic on purpose,
    so a traffic_drop afterwards is the fix working, not a new incident). Recorded per poll, never hidden."""
    if polls < 1 or settle_s < 0 or poll_s < 0:
        raise ValueError("polls must be positive; delays must be nonnegative")
    # The default requires three clean polls. Explicit shorter test/demo runs require all their polls clean.
    required = min(3, polls)
    time.sleep(settle_s)
    keys = {(a["type"], a["service"]) for a in original_alerts if a.get("type") != "manual"}
    history, clean = [], 0
    for i in range(polls):
        try:
            now = detector.check(strict=True, original_alerts=original_alerts)
            still = [a for a in now if (a["type"], a["service"]) in keys]
            new = [a for a in now if (a["type"], a["service"]) not in keys and a["type"] not in expected]
            caused = [a for a in now if (a["type"], a["service"]) not in keys and a["type"] in expected]
            clean = clean + 1 if not still and not new else 0
            history.append({"poll": i + 1, "still_firing": still, "new_alerts": new, "expected_from_fix": caused,
                            "telemetry_valid": True})
        except Exception as exc:
            clean = 0
            history.append({"poll": i + 1, "still_firing": [], "new_alerts": [], "telemetry_valid": False,
                            "error": f"{type(exc).__name__}: {str(exc)[:300]}"})
        if i < polls - 1:
            time.sleep(poll_s)
    return {"recovered": clean >= required, "required_clean_polls": required, "consecutive_clean_polls": clean, "polls": history}
