"""HUMAN LOOP phase: show diagnosis + evidence + plan + dry run, wait for an explicit yes. Nothing changes without it."""
from agent import actions


def summary(diag: dict, p: dict | None, preview: str | None) -> str:
    rc = diag.get("root_cause", {})
    lines = ["", "=" * 70, f"DIAGNOSIS: {diag.get('summary')}",
             f"Root cause: {rc.get('service')} / {rc.get('category')}   confidence {diag.get('confidence')}   decision {diag.get('decision')}",
             "Evidence:"]
    lines += [f"  [{e['id']}] {e['observed']}" for e in diag.get("evidence", [])]
    for n in diag.get("validation_notes", []):
        lines.append(f"  note: {n}")
    if p:
        lines += [f"PROPOSED FIX: {p['action']} {p['params']}", f"  reason:   {p['reason']}",
                  f"  rollback: {p['rollback']}", f"  dry run:  {preview}"]
    else:
        lines.append("NO FIX PROPOSED: escalating to on-call human with the findings above.")
    lines.append("=" * 70)
    return "\n".join(lines)


def ask(diag, p, preview, ask_fn=input) -> dict:
    print(summary(diag, p, preview))
    if not p:
        ask_fn("Press Enter to acknowledge the escalation ")
        return {"approved": False, "by": None, "reason": "escalated, no action"}
    if actions.kill_switch_on():
        return {"approved": False, "by": None, "reason": "kill switch on"}
    ans = ask_fn("Approve this action? type 'yes' to approve, anything else rejects: ").strip().lower()
    if ans != "yes":
        return {"approved": False, "by": None, "reason": f"rejected by human ({ans or 'empty'})"}
    who = ask_fn("Your name (recorded in the audit log): ").strip() or "on-call"
    return {"approved": True, "by": who, "reason": "approved"}
