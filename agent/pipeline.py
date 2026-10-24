"""The whole incident flow, phase by phase. Each phase writes its output into runs/<incident-id>/.

  INJECT      (testing only) load a recorded scenario offline; live faults are injected by a human with faultctl
  DETECT      rule-based alert on error rate / latency / traffic / container state
  INVESTIGATE agent: fixed sweep + Qwen picks tools -> diagnosis with evidence ids -> code validation
  HUMAN       show diagnosis, evidence, proposed fix, dry run -> explicit yes/no (kill switch respected)
  FIX         execute the approved allowlisted action via the Action API (audit-logged)
  VALIDATE    re-check the alerting signals; not recovered -> escalate
  REPORT      report.json + report.md
"""
from agent import actions, approval, brain, detector, report, verify
from agent.incident import Incident


def run(mode="now", ask_fn=None, client_factory=None, validate_kw=None, on_executed=None):
    ask_fn = ask_fn or (lambda prompt="": input(prompt))
    inc = Incident()

    # DETECT
    if mode == "watch":
        inc.event("detect", "watching for alerts")
        alerts = detector.wait_for_alert()
    else:
        alerts = detector.check() or [{"type": "manual", "service": "unknown", "value": "operator page"}]
    inc.save("alert.json", alerts)
    inc.event("detect", f"{len(alerts)} alert(s): " + ", ".join(f"{a['type']}:{a['service']}" for a in alerts))

    # INVESTIGATE
    inc.event("investigate", "sweep + LLM tool loop started")
    diag = brain.investigate(alerts, log=lambda *a: None)
    inc.save("diagnosis.json", diag)
    rc = diag.get("root_cause", {})
    inc.event("investigate", f"{rc.get('service')} / {rc.get('category')} conf={diag['confidence']} -> {diag['decision']}",
              tools=[t["tool"] for t in diag["tool_trail"]])

    p = actions.plan(diag)
    client = (client_factory or actions.ActionClient)(inc)
    preview = client.preview(p) if p else None
    if p:
        inc.save("plan.json", {**p, "dry_run_preview": preview})
        inc.event("investigate", f"proposed {p['action']} {p['params']}")

    # HUMAN
    if diag["decision"] == "no_incident":
        appr = {"approved": False, "by": None, "reason": "no incident"}
    else:
        inc.event("human", "waiting for approval" if p else "escalating to human")
        appr = approval.ask(diag, p, preview, ask_fn)
    inc.save("approval.json", appr)
    inc.event("human", appr["reason"])

    # FIX + VALIDATE
    result = validation = rollback = None
    if p and appr["approved"]:
        result = client.execute(p, appr["by"])
        inc.event("fix", f"{p['action']} -> {result['status']}", **{k: v for k, v in result.items() if k != 'status'})
        if result["status"] in ("executed", "simulated"):
            if on_executed:
                on_executed(p)
            inc.event("validate", "re-checking alert signals")
            validation = verify.recovered(alerts, **(validate_kw or {}))
            inc.save("validation.json", validation)
            inc.event("validate", "recovered" if validation["recovered"] else "NOT recovered -> escalate to human")
            if not validation["recovered"] and result.get("undo_available") and result.get("call_id"):
                # a rollback is a change too: it needs its own explicit approval
                ans = ask_fn(f"Fix did not recover the system. Roll back {p['action']}? type 'yes' to roll back: ").strip().lower()
                if ans == "yes":
                    rollback = client.undo(result["call_id"], appr["by"])
                    inc.event("fix", f"rollback of {result['call_id']} -> {rollback['status']}")
                else:
                    rollback = {"status": "declined", "why": "human chose not to roll back"}
                    inc.event("human", "rollback declined")
                inc.save("rollback.json", rollback)

    # REPORT
    rep = report.build(inc, alerts, diag, p, appr, result, validation, rollback)
    inc.event("report", f"{rep['outcome']} -> {inc.dir}/report.md")
    return rep
