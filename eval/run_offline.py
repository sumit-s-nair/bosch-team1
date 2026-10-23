"""Run the agent against recorded incident scenarios (no live demo needed).

  python -m eval.run_offline --list
  python -m eval.run_offline --tools                 # no LLM: do alerts fire and do tools surface the right service?
  python -m eval.run_offline --tools payment_failure # one scenario, prints every tool output
  python -m eval.run_offline                         # full agent with Qwen on every scenario, scored
  python -m eval.run_offline payment_failure cache_unavailable
  python -m eval.run_offline --pipeline payment_failure   # all phases incl. human approval, simulated fix

Results: runs/offline-<time>/<scenario>.json and summary.json
"""
import argparse
import json
import os
import sys
import time

sys.path.insert(0, ".")
from eval import offline, scenarios  # noqa: E402


def all_tools(L):
    from agent import observe
    out = {}
    for name, (fn, _, params) in observe.TOOLS.items():
        if any(not t.endswith("?") for t in params.values()):   # needs an argument (service, trace_id, ...)
            continue
        out[name] = fn(L)
    return out


def tool_check(sc, verbose=False):
    """Without the LLM: alerts fire? expected service present in abnormal evidence after a full sweep?"""
    from agent import detector, observe
    from agent.evidence import Ledger
    offline.install(sc)
    alerts = detector.check()
    L = Ledger()
    outs = all_tools(L)
    for s in {a["service"] for a in alerts if a["type"] != "container"} | set(sc["expected"]["services"]):
        if s in sc["services"]:
            outs[f"service_operations({s})"] = observe.service_operations(L, s)
            outs[f"trace_errors({s})"] = observe.trace_errors(L, s)
            outs[f"error_breakdown({s})"] = observe.TOOLS["error_breakdown"][0](L, s)
    abnormal = [i for i in L.items.values() if i["anomalous"]]
    text = " ".join((i["detail"] + " " + (i["ref"] or "")).lower() for i in abnormal)
    exp = sc["expected"]["services"]
    found = [s for s in exp if s in text]
    ok = (not exp and not alerts and not abnormal) or (bool(exp) and bool(found) and bool(alerts))
    if verbose:
        print("ALERTS:", alerts)
        for k, v in outs.items():
            print(f"\n===== {k}\n{v}")
    return {"scenario": sc["name"], "ok": ok, "alerts": [f"{a['type']}:{a['service']}" for a in alerts],
            "abnormal_facts": len(abnormal), "expected": exp, "found_in_abnormal": found}


def score(sc, d):
    from agent import actions
    exp = sc["expected"]
    p = actions.plan(d)
    rc = d.get("root_cause", {})
    svc_ok = (not exp["services"] and d["decision"] in exp["decision"]) or \
             any(s == (rc.get("service") or "").lower() for s in exp["services"])
    cat_ok = rc.get("category") in exp["categories"]
    return {"scenario": sc["name"], "decision": d["decision"], "service": rc.get("service"), "category": rc.get("category"),
            "confidence": d["confidence"], "service_ok": svc_ok, "category_ok": cat_ok,
            "correct": svc_ok and (cat_ok or not exp["services"]),
            "summary": d.get("summary"),
            "proposed_fix": f"{p['action']}({', '.join(f'{k}={v}' for k, v in p['params'].items())})" if p else "none: escalate to human",
            "fix_reason": p["reason"] if p else None,
            "seconds": d.get("investigation_seconds"), "tools": [t["tool"] for t in d["tool_trail"] if t["by"] == "llm"]}


class SimulatedActionClient:
    """Offline stand-in for the Action API: logs like the real client; the 'fix' heals the scenario only if
    the action targets the real culprit (otherwise validation fails and the pipeline escalates)."""
    def __init__(self, inc, sc):
        from agent import actions
        self.real = actions.ActionClient(inc, dry_run=True)
        self.sc = sc

    def preview(self, p):
        return self.real.preview(p).replace("Action API not configured (dry run only)", "SIMULATED (offline scenario)")

    def execute(self, p, approved_by):
        from agent import actions
        if p["action"] not in actions.ALLOWLIST or actions.kill_switch_on() or not approved_by:
            return self.real.execute(p, approved_by)
        r = {"status": "simulated", "note": "offline scenario; no real system changed"}
        self.real._audit({"plan": p, "approved_by": approved_by, **r})
        return r


def run_pipeline(name):
    from agent import pipeline
    from agent.incident import Incident  # noqa: F401
    sc = scenarios.get(name)
    offline.install(sc)
    print(f"INJECT       offline scenario '{name}': {sc['description']}")

    def heal(p):
        target = p["params"].get("service") or ("load-generator" if p["action"] == "throttle_load_generator" else "")
        culprits = set(sc["expected"]["services"])
        if target in culprits or (p["action"] == "throttle_load_generator" and "traffic_spike" in sc["expected"]["categories"]):
            offline.install(scenarios.get("healthy"))
    rep = pipeline.run(mode="now", client_factory=lambda inc: SimulatedActionClient(inc, sc),
                       validate_kw={"settle_s": 1, "polls": 2, "poll_s": 1}, on_executed=heal)
    print("\nOUTCOME:", rep["outcome"])
    return 0


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("names", nargs="*")
    ap.add_argument("--tools", action="store_true", help="no LLM; check alerts and tool signals")
    ap.add_argument("--list", action="store_true")
    ap.add_argument("--pipeline", nargs="+", metavar="SCENARIO", help="run every phase (detect..report) on these scenarios")
    a = ap.parse_args()
    if a.list:
        for f in scenarios.ALL:
            sc = f(); print(f"{sc['name']:30} {sc['description']}")
        return 0
    names = a.names or scenarios.names()

    if a.pipeline:
        for n in a.pipeline:
            print(f"\n{'#' * 70}")
            run_pipeline(n)
        return 0

    if a.tools:
        rows = [tool_check(scenarios.get(n), verbose=len(names) == 1) for n in names]
        print(f"\n{'scenario':30} {'ok':4} alerts / expected found in abnormal evidence")
        for r in rows:
            print(f"{r['scenario']:30} {'yes' if r['ok'] else 'NO':4} {r['alerts']} / {r['found_in_abnormal']} of {r['expected']}")
        print(f"\n{sum(r['ok'] for r in rows)}/{len(rows)} scenarios surface the right signal")
        return 0

    from agent import brain, detector
    out_dir = os.path.join("runs", "offline-" + time.strftime("%H%M%S"))
    os.makedirs(out_dir, exist_ok=True)
    results = []
    for n in names:
        sc = scenarios.get(n)
        offline.install(sc)
        print(f"\n######## {n}: {sc['description']}")
        alerts = detector.check() or [{"type": "manual", "service": "unknown", "value": "operator page"}]
        d = brain.investigate(alerts, log=lambda *x: None)
        json.dump(d, open(os.path.join(out_dir, f"{n}.json"), "w"), indent=2, default=str)
        r = score(sc, d)
        results.append(r)
        print(f"-> {r['decision']} | {r['service']} / {r['category']} | conf {r['confidence']} | "
              f"{'CORRECT' if r['correct'] else 'wrong'} | {r['seconds']}s | tools {r['tools']}")
        print(f"   summary: {r['summary']}")
        print(f"   fix:     {r['proposed_fix']}")
    json.dump(results, open(os.path.join(out_dir, "summary.json"), "w"), indent=2)
    print(f"\n{sum(r['correct'] for r in results)}/{len(results)} correct. Saved {out_dir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
