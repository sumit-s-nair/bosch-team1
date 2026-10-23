"""VALIDATE phase: after the fix, re-check the same signals that raised the alert."""
import time

from agent import detector


def recovered(original_alerts: list[dict], settle_s: int = 60, polls: int = 3, poll_s: int = 20) -> dict:
    time.sleep(settle_s)
    keys = {(a["type"], a["service"]) for a in original_alerts if a.get("type") != "manual"}
    history = []
    for i in range(polls):
        now = detector.check()
        still = [a for a in now if (a["type"], a["service"]) in keys]
        new = [a for a in now if (a["type"], a["service"]) not in keys]
        history.append({"poll": i + 1, "still_firing": still, "new_alerts": new})
        if i < polls - 1:
            time.sleep(poll_s)
    last = history[-1]
    return {"recovered": not last["still_firing"] and not last["new_alerts"], "polls": history}
