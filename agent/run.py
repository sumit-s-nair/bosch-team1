"""Entry point.
  python -m agent.run --watch      wait for an alert, then investigate
  python -m agent.run --now        investigate immediately (manual page)
"""
import argparse
import json
import os
import time

from agent import brain, detector


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--watch", action="store_true")
    ap.add_argument("--now", action="store_true")
    a = ap.parse_args()

    if a.watch:
        print("Watching for incidents (Ctrl+C to stop)...")
        alerts = detector.wait_for_alert()
    else:
        alerts = detector.check() or [{"type": "manual", "service": "unknown", "value": "operator page"}]
    print("ALERT:", json.dumps(alerts))

    diag = brain.investigate(alerts)
    run_dir = os.path.join("runs", time.strftime("%Y%m%d-%H%M%S"))
    os.makedirs(run_dir, exist_ok=True)
    with open(os.path.join(run_dir, "diagnosis.json"), "w") as f:
        json.dump(diag, f, indent=2, default=str)

    print("\n================ DIAGNOSIS ================")
    print("Summary:   ", diag.get("summary"))
    rc = diag.get("root_cause", {})
    print("Root cause:", rc.get("service"), "/", rc.get("category"), "-", rc.get("description"))
    print("Confidence:", diag["confidence"], "-> decision:", diag["decision"])
    for e in diag["evidence"]:
        print(f"  [{e['id']}] {e['observed']}  <- {e['why']}")
    for n in diag["validation_notes"]:
        print("  note:", n)
    print("Saved:", run_dir)


if __name__ == "__main__":
    main()
