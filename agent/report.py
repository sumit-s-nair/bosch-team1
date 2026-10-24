"""REPORT phase: report.json (machine-readable) + report.md (human-readable) in the incident folder.
TODO-ALIGN: map report.json to the organisers' JSON schema when it is published."""


def build(inc, alerts, diag, p, approval, result, validation, rollback=None) -> dict:
    rc = diag.get("root_cause", {})
    rep = {
        "incident_id": inc.id,
        "alerts": alerts,
        "summary": diag.get("summary"),
        "root_cause": rc,
        "confidence": diag.get("confidence"),
        "decision": diag.get("decision"),
        "evidence": [{"id": e["id"], "kind": e["kind"], "ref": e["ref"], "observed": e["observed"], "why": e["why"]}
                     for e in diag.get("evidence", [])],
        "action": p, "approval": approval, "action_result": result, "validation": validation, "rollback": rollback,
        "outcome": _outcome(diag, approval, result, validation, rollback),
        "prevention": _prevention(rc),
        "timeline": inc.timeline(),
    }
    inc.save("report.json", rep)
    md = [f"# Incident {inc.id}", "", f"**Outcome:** {rep['outcome']}", "",
          f"**Summary:** {rep['summary']}", "",
          f"**Root cause:** `{rc.get('service')}` / {rc.get('category')} (confidence {rep['confidence']})", "",
          rc.get("description") or "", "", "## Evidence"]
    md += [f"- [{e['id']}] {e['observed']} — {e['why']}" for e in rep["evidence"]] or ["- none"]
    md += ["", "## Action", f"- Plan: {p}" if p else "- None (escalated)", f"- Approval: {approval}",
           f"- Result: {result}", f"- Recovered: {validation.get('recovered') if validation else 'n/a'}",
           f"- Rollback: {rollback if rollback else 'n/a'}",
           "", "## Prevention", f"- {rep['prevention']}", "", "## Timeline"]
    md += [f"- {t['t']} {t['phase']}: {t['msg']}" for t in rep["timeline"]]
    with open(f"{inc.dir}/report.md", "w", encoding="utf-8") as f:
        f.write("\n".join(md) + "\n")
    return rep


def _outcome(diag, approval, result, validation, rollback=None):
    if diag.get("decision") == "no_incident":
        return "no incident found"
    if not approval or not approval.get("approved"):
        return "escalated to human" if diag.get("decision") == "escalate" else f"not fixed: {approval.get('reason') if approval else ''}"
    if result and result.get("status") == "dry_run":
        return "approved; dry run only (Action API not configured)"
    if validation and validation.get("recovered"):
        return "fixed and verified"
    if rollback and rollback.get("status") in ("executed", "simulated"):
        return "fix applied but not recovered: rolled back and escalated"
    return "fix applied but not recovered: escalated"


def _prevention(rc):
    return {
        "container_down": "Add a restart policy/liveness probe and alert on container exits for this service.",
        "resource_exhaustion": "Set memory/CPU limits with headroom, alert on growth rate (time-to-limit), profile the service.",
        "cache_unavailable": "Health-check the cache, add client retry/backoff and a fallback when the cache is down.",
        "dependency_failure": "Add health checks and circuit breaking on the failing dependency.",
        "service_error": "Alert on per-operation error rate and add a regression test for the failing path.",
        "traffic_spike": "Add rate limiting at the gateway and autoscaling for the frontend.",
        "latency_or_network": "Alert on client-vs-server latency gaps between services; add timeouts.",
        "database_unavailable": "Health-check the database, add connection retry/backoff in product-catalog and alert on DB container exits.",
        "database_slow": "Alert on DB lock waits and query p95; avoid long ACCESS EXCLUSIVE locks; add statement timeouts.",
    }.get(rc.get("category"), "Review the findings and add an alert for the observed symptom.")
