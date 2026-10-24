"""The whole incident flow, phase by phase. Each phase writes its output into runs/<incident-id>/.

  INJECT      (testing only) load a recorded scenario offline; live faults are injected by a human with faultctl
  DETECT      rule-based alert on error rate / latency / traffic / container state
  INVESTIGATE agent: fixed sweep + Qwen picks tools -> diagnosis with evidence ids -> code validation
  HUMAN       show diagnosis, evidence, proposed fix, dry run -> explicit yes/no (kill switch respected)
  FIX         execute the approved allowlisted action via the Action API (audit-logged)
  VALIDATE    re-check the alerting signals; not recovered -> offer rollback, then investigate once more
              knowing what was tried (never repeating the same fix); still not recovered -> escalate
  REPORT      report.json + report.md
"""
from agent import actions, approval, brain, detector, report, verify
from agent.incident import Incident


MAX_ROUNDS = 2      # one re-investigation after a fix that ran but did not recover


def run(mode="now", ask_fn=None, client_factory=None, validate_kw=None, on_executed=None, incident=None):
    """ask_fn answers every human question (terminal input by default; the agent server passes the browser's).
    incident: an already-created Incident, so a caller (agent/server.py) knows the run id before the run starts."""
    ask_fn = ask_fn or (lambda prompt="": input(prompt))
    inc = incident or Incident()

    # DETECT
    if mode == "watch":
        inc.event("detect", "watching for alerts")
        alerts = detector.wait_for_alert()
    else:
        alerts = detector.check() or [{"type": "manual", "service": "unknown", "value": "operator page"}]
    inc.save("alert.json", alerts)
    inc.event("detect", f"{len(alerts)} alert(s): " + ", ".join(f"{a['type']}:{a['service']}" for a in alerts))

    client = (client_factory or actions.ActionClient)(inc)
    rounds, tried, context = [], [], ""
    final = None
    for rnd in range(1, MAX_ROUNDS + 1):
        sfx = "" if rnd == 1 else f"-round{rnd}"

        # INVESTIGATE
        inc.event("investigate", "sweep + LLM tool loop started" + (f" (round {rnd}, after a fix that did not recover)" if rnd > 1 else ""))
        diag = brain.investigate(alerts, log=lambda *a: None, context=context)
        inc.save(f"diagnosis{sfx}.json", diag)
        rc = diag.get("root_cause", {})
        inc.event("investigate", f"{rc.get('service')} / {rc.get('category')} conf={diag['confidence']} -> {diag['decision']}",
                  tools=[t["tool"] for t in diag["tool_trail"]])

        p = actions.plan(diag)
        if p and any(p["action"] == t["action"] and p["params"] == t["params"] for t in tried):
            inc.event("investigate", f"same fix as already tried ({p['action']} {p['params']}): not repeating it, escalating")
            diag["decision"], p = "escalate", None
        preview = client.preview(p) if p else None
        if p:
            inc.save(f"plan{sfx}.json", {**p, "dry_run_preview": preview})
            inc.event("investigate", f"proposed {p['action']} {p['params']}")
            ok, why = client.health()
            for _ in range(3):
                if ok:
                    break
                inc.event("fix", f"cannot act: {why}")
                ans = ask_fn(f"{why}. Start it and press Enter to check again, or type 'skip' to escalate without acting: ")
                if ans.strip().lower() == "skip":
                    break
                ok, why = client.health()
            if not ok:
                diag["decision"], p, preview = "escalate", None, None
                diag.setdefault("validation_notes", []).append(f"no fix possible right now: {why}")
                inc.save(f"diagnosis{sfx}.json", diag)

        # HUMAN
        if diag["decision"] == "no_incident":
            appr = {"approved": False, "by": None, "reason": "no incident"}
        elif rnd > 1 and not p:
            appr = {"approved": False, "by": None, "reason": "escalated after a fix that did not recover"}
        else:
            inc.event("human", "waiting for approval" if p else "escalating to human")
            appr = approval.ask(diag, p, preview, ask_fn)
        inc.save(f"approval{sfx}.json", appr)
        inc.event("human", appr["reason"])

        # FIX + VALIDATE
        result = validation = rollback = None
        if p and appr["approved"]:
            tried.append({"action": p["action"], "params": dict(p["params"])})
            result = client.execute(p, appr["by"])
            inc.event("fix", f"{p['action']} -> {result['status']}", **{k: v for k, v in result.items() if k != 'status'})
            if result["status"] in ("executed", "simulated"):
                if on_executed:
                    on_executed(p)
                inc.event("validate", "re-checking alert signals")
                validation = verify.recovered(alerts, **(validate_kw or {}))
                inc.save(f"validation{sfx}.json", validation)
                inc.event("validate", "recovered" if validation["recovered"] else "NOT recovered")
                if not validation["recovered"] and result.get("undo_available") and result.get("call_id"):
                    # a rollback is a change too: it needs its own explicit approval
                    ans = ask_fn(f"Fix did not recover the system. Roll back {p['action']}? type 'yes' to roll back: ").strip().lower()
                    if ans == "yes":
                        rollback = client.undo(result["call_id"], appr["by"])
                        inc.event("fix", f"rollback of {result['call_id']} -> {rollback['status']}")
                    else:
                        rollback = {"status": "declined", "why": "human chose not to roll back"}
                        inc.event("human", "rollback declined")
                    inc.save(f"rollback{sfx}.json", rollback)

        rounds.append({"round": rnd, "diagnosis": f"{rc.get('service')} / {rc.get('category')} ({diag['confidence']})",
                       "action": f"{p['action']} {p['params']}" if p else None,
                       "result": (result or {}).get("status"), "recovered": (validation or {}).get("recovered")})
        if final is None or p:
            final = (diag, p, appr, result, validation, rollback)
        acted = result is not None and result["status"] in ("executed", "simulated")
        if not acted or validation["recovered"]:
            break
        # the fix ran but the system did not recover: look again, knowing what was tried
        alerts = detector.check() or alerts
        context = (f"Round {rnd} diagnosed {rc.get('service')} / {rc.get('category')} and applied "
                   f"{p['action']} {p['params']} (approved by a human). The system did NOT recover. "
                   f"Do not assume that diagnosis was right; find what is still failing.")

    # REPORT
    diag, p, appr, result, validation, rollback = final
    rep = report.build(inc, alerts, diag, p, appr, result, validation, rollback, rounds)
    inc.event("report", f"{rep['outcome']} -> {inc.dir}/report.md")
    return rep
