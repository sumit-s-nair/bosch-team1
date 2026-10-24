"""Investigation loop: fixed first sweep -> LLM picks follow-up tools -> diagnosis JSON -> evidence check."""
import json
import re
import time

from agent import config, llm
from agent.tools import observe
from agent.evidence import Ledger
from agent.schema import validate_output

MAX_TOOL_STEPS = 8          # LLM turns after the sweep
MAX_CALLS_PER_STEP = 2      # an 8B model may ask for one tool per alerting service at once; that bloats the context
MAX_TOOL_CALLS = 8          # total follow-up tool calls per investigation
TOOL_TIME_BUDGET_S = float(config.setting("AGENT_TOOL_BUDGET_S", "180"))   # stop asking for tools after this long
ACT_CONFIDENCE = 0.7
CATEGORIES = ["service_error", "dependency_failure", "container_down", "resource_exhaustion",
              "latency_or_network", "traffic_spike", "cache_unavailable", "database_unavailable",
              "database_slow", "unknown"]

SYSTEM = f"""You are an on-call SRE investigating a production incident in the OpenTelemetry Astronomy Shop
(microservices: frontend, frontend-proxy, cart, checkout, payment, product-catalog, recommendation, ad,
shipping, quote, currency, email, image-provider, valkey-cart cache, postgres astronomy-db, load-generator).

Rules:
- Only use facts returned by tools. Every claim must cite evidence ids like [E3] that appeared in tool output.
- Find the ORIGIN of the failure, not just where it is visible: errors at the frontend usually come from a
  downstream service. Follow CLIENT calls and trace error origins down the call chain.
- Check container health for stopped/restarting/OOM containers and resource pressure.
- product-catalog depends on the Postgres database astronomy-db; if product-catalog errors or is slow, call db_health before blaming product-catalog.
- If CPU, memory or latency looks wrong, check runtime metrics via resource_usage; if request volume changed, use service_overview with group_by.
- Be efficient: at most {MAX_CALLS_PER_STEP} tool calls per step and {MAX_TOOL_CALLS} in total. Start with the service
  whose OWN server operations fail; do not query every alerting service. When you have enough, stop calling tools.
- If evidence is weak or contradictory, say so: low confidence and escalate is a GOOD outcome.
- An incident needs USER-FACING impact (errors, latency, traffic loss) or a broken container. If every service
  looks healthy, say there is no incident (category "unknown", low confidence). Do not invent one.
- Categories: dependency_failure = something this service CALLS (database, cache, another service) is down or
  unreachable. If the service's own operations fail while its dependencies are healthy, it is service_error.
- A container that is NOT RUNNING and "not OOM-killed" was stopped or crashed: category container_down, not
  resource_exhaustion. Only call it resource_exhaustion when the tools show OOM-KILLED or real CPU/memory pressure.
- load-generator is the synthetic traffic source. Its own CPU/memory is normal; it is only a cause if it floods traffic.
- Lines marked ANOMALY / LEAK RISK / issues in container_health are abnormal by code thresholds; everything else is normal.
"""

FINAL_PROMPT = f"""Now write your diagnosis as ONE JSON object and nothing else:
{{
 "summary": "<one sentence: what is broken and why>",
 "symptoms": ["<observed symptom with [E#]>", ...],
 "root_cause": {{"service": "<origin service or container>", "category": one of {CATEGORIES}, "description": "<cause>"}},
 "confidence": <0.0-1.0, how sure you are of the root cause>,
 "evidence": [{{"id": "E#", "why": "<how this supports the root cause>"}}, ...],
 "ruled_out": ["<alternative you checked and why it is not the cause>"],
 "open_questions": ["<what you could not confirm>"]
}}
Only cite evidence ids that appeared in tool outputs."""


def _strip_think(text: str) -> str:
    return re.sub(r"<think>.*?</think>", "", text or "", flags=re.S).strip()


def _tool_specs(services: list[str]) -> list[dict]:
    specs = []
    for name, (_, desc, params) in observe.TOOLS.items():
        props, req = {}, []
        for p, t in params.items():
            props[p] = {"type": "string", "enum": services} if p == "service" else {"type": "string"}
            if p == "group_by":
                props[p] = {"type": "string", "enum": ["route", "client"], "description": "Use route/client breakdown when request volume changes"}
            elif p == "window":
                props[p]['description'] = "Positive Prometheus window such as 5m or 10m"
            if not t.endswith("?"):
                req.append(p)
        specs.append({"type": "function", "function": {
            "name": name, "description": desc,
            "parameters": {"type": "object", "properties": props, "required": req}}})
    return specs


def _llm(messages, tools=None, json_mode=False, think=False):
    return llm.chat(messages, tools=tools, json_mode=json_mode, think=think)


def _run_tool(L: Ledger, name: str, args: dict) -> str:
    fn = observe.TOOLS[name][0]
    try:
        out = fn(L, **args)
    except Exception as e:
        out = f"Tool error: {type(e).__name__}: {e}"
    if isinstance(out, dict):
        return json.dumps(out, ensure_ascii=False)
    return out[:2500]


def _parse_json(text: str):
    text = _strip_think(text)
    m = re.search(r"\{.*\}", text, flags=re.S)
    if not m:
        raise ValueError("no JSON object in model output")
    return json.loads(m.group(0))


def other_problems(diag: dict, L: Ledger, limit: int = 6) -> list[str]:
    """Abnormal facts about OTHER services than the root cause: with several faults at once, the human must see
    what this diagnosis does not explain (the agent fixes one thing at a time)."""
    root = (diag.get("root_cause") or {}).get("service") or ""
    cited = {e["id"] for e in diag.get("evidence", [])}
    out, seen = [], set()
    for i in L.items.values():
        if not i["anomalous"] or i["id"] in cited:
            continue
        ref = str(i.get("ref") or "")
        svc = ref.split(":", 1)[1].split(":")[0] if ref.split(":")[0] in ("container", "span_metrics") else i["detail"].split(":")[0].split()[0]
        if not svc or svc == root or svc in ("frontend", "frontend-proxy") or root and root in i["detail"]:
            continue
        if svc not in seen:
            seen.add(svc)
            out.append(f"{i['detail'][:140]} [{i['id']}]")
    return out[:limit]


def validate(diag: dict, L: Ledger) -> dict:
    """Drop cited evidence that isn't in the ledger; enforce decision rules."""
    notes = []
    good = []
    for ev in diag.get("evidence", []) or []:
        eid = str(ev.get("id", "")).strip("[] ")
        item = L.get(eid)
        if item:
            good.append({"id": eid, "why": ev.get("why", ""), "kind": item["kind"],
                         "ref": item["ref"], "observed": item["detail"], "anomalous": item["anomalous"]})
        else:
            notes.append(f"dropped invented evidence id {eid!r}")
    diag["evidence"] = good
    try:
        conf = float(diag.get("confidence", 0))
    except Exception:
        conf = 0.0
    rc = diag.get("root_cause") or {}
    svc = (rc.get("service") or "").lower()
    if rc.get("category") not in CATEGORIES:
        rc["category"] = "unknown"
    if not good:
        conf = min(conf, 0.2); notes.append("no valid evidence -> confidence capped")
    elif not any(L.get(e["id"]).get("anomalous") for e in good):
        conf = min(conf, 0.3); notes.append("none of the cited evidence is abnormal by code thresholds -> confidence capped")
    elif svc and not any(svc in (e["observed"] or "").lower() or svc in (e["ref"] or "").lower() for e in good):
        conf = min(conf, 0.4); notes.append(f"root-cause service '{svc}' not present in cited evidence -> confidence capped")
    diag["root_cause"] = rc
    diag["confidence"] = round(max(0.0, min(1.0, conf)), 2)
    diag["decision"] = "propose_fix" if diag["confidence"] >= ACT_CONFIDENCE and rc.get("category") != "unknown" else "escalate"
    diag["validation_notes"] = notes
    return diag


def investigate(alerts: list[dict], log=print, context: str = "", feed=None) -> dict:
    """feed(kind, **data), if given, receives the whole conversation as it happens (the dashboard shows it live)."""
    feed = feed or (lambda kind, **d: None)
    L = Ledger()
    t0 = time.time()
    trail = []
    try:
        names = {c["name"] for c in observe.containers.list_containers()}
    except Exception:
        names = set()          # docker briefly unavailable: fall back to the services Prometheus knows
    services = sorted(set(observe.service_health_raw()) | names)

    # 1) fixed first sweep (cheap, always useful)
    sweep = []
    for name in ("service_overview", "container_health", "recent_changes"):
        out = _run_tool(L, name, {})
        trail.append({"tool": name, "args": {}, "by": "sweep"})
        sweep.append(f"### {name}\n{out}")
        log(f"[sweep] {name}\n{out}\n")
        feed("sweep", tool=name, args={}, output=out)
    real_alerts = [a for a in alerts if a.get("type") != "manual"]
    if not real_alerts and not any(i["anomalous"] for i in L.items.values()):
        # manual page, and the fixed sweep found nothing abnormal: no need to spend model time
        return {"summary": "No alert fired and the sweep found nothing abnormal.",
                "root_cause": {"service": "none", "category": "unknown", "description": "no incident"},
                "confidence": 0.0, "evidence": [], "decision": "no_incident",
                "validation_notes": ["model not called: no alert and nothing abnormal in the sweep"],
                "alerts": alerts, "tool_trail": trail, "investigation_seconds": round(time.time() - t0, 1),
                "ledger": list(L.items.values())}
    messages = [
        {"role": "system", "content": SYSTEM},
        {"role": "user", "content": f"ALERT: {json.dumps(alerts)}\n\nInitial sweep:\n\n" + "\n\n".join(sweep) +
         ("\n\nEARLIER IN THIS INCIDENT:\n" + context if context else "") +
         "\n\nInvestigate further with tools to find the root cause."},
    ]
    tools = _tool_specs(services)
    feed("prompt", text=messages[1]["content"])

    # 2) model-driven follow-ups, capped by steps and by wall-clock time (checked before each LLM turn)
    limit_note = None
    for step in range(MAX_TOOL_STEPS):
        if time.time() - t0 > TOOL_TIME_BUDGET_S:
            limit_note = f"stopped calling tools after {time.time() - t0:.0f}s (time budget {TOOL_TIME_BUDGET_S:.0f}s)"
            log(f"[limit] {limit_note}\n")
            feed("limit", text=limit_note)
            break
        msg = _llm(messages, tools)
        calls = (msg.get("tool_calls") or [])[:MAX_CALLS_PER_STEP]
        used = sum(1 for t in trail if t["by"] == "llm")
        calls = calls[:max(0, MAX_TOOL_CALLS - used)]
        if msg.get("thinking"):
            log(f"[llm thinking] {msg['thinking'][:600]}\n")
            feed("thinking", text=msg["thinking"][:2000])
        said = _strip_think(msg.get("content", "")).strip()
        if said:
            feed("model", text=said[:2000])
        if not calls:
            break
        messages.append({"role": "assistant", "content": _strip_think(msg.get("content", "")),
                         "tool_calls": calls})
        for c in calls:
            name = c["function"]["name"]
            args = c["function"].get("arguments") or {}
            if isinstance(args, str):
                try:
                    args = json.loads(args)
                except Exception:
                    args = {}
            if name not in observe.TOOLS:
                out = f"Unknown tool {name}"
            else:
                args = {k: v for k, v in args.items() if k in observe.TOOLS[name][2]}
                out = _run_tool(L, name, args)
            trail.append({"tool": name, "args": args, "by": "llm"})
            log(f"[llm step {step+1}] {name}({args})\n{out}\n")
            feed("tool", tool=name, args=args, output=out, step=step + 1)
            messages.append({"role": "tool", "tool_name": name, "content": out})

    # 3) diagnosis
    messages.append({"role": "user", "content": FINAL_PROMPT})
    diag = None
    for attempt in range(2):
        final_msg = _llm(messages, json_mode=True, think=True)
        raw = final_msg.get("content", "")
        if final_msg.get("thinking"):
            feed("thinking", text=final_msg["thinking"][:3000])
        feed("diagnosis", text=_strip_think(raw)[:4000])
        try:
            diag = _parse_json(raw)
            ok, errors = validate_output(diag)
            if not ok:
                raise ValueError("schema errors: " + "; ".join(errors[:5]))
            break
        except Exception as e:
            diag = None
            messages.append({"role": "assistant", "content": _strip_think(raw)[:500]})
            messages.append({"role": "user", "content": f"Invalid diagnosis ({e}). Reply with only the corrected JSON object."})
            feed("retry", text=f"invalid diagnosis ({e}); asked the model to correct it")
    if diag is None:
        diag = {"summary": "Model failed to produce a valid diagnosis twice; escalating",
                "root_cause": {"service": "unknown", "category": "unknown", "description": "no valid diagnosis"},
                "confidence": 0, "evidence": []}
    diag = validate(diag, L)
    feed("validation", text="; ".join(diag["validation_notes"]) or "evidence ids and confidence checked: ok",
         decision=diag["decision"], confidence=diag["confidence"])
    if limit_note:
        diag["validation_notes"].append(limit_note)
    real_alerts = [a for a in alerts if a.get("type") != "manual"]
    if not real_alerts and not any(i["anomalous"] for i in L.items.values()):
        diag["decision"] = "no_incident"
        diag["validation_notes"].append("no alert fired and no tool found anything abnormal")
    diag["other_problems"] = other_problems(diag, L)
    diag["model"] = config.LLM_MODEL
    diag["alerts"] = alerts
    diag["tool_trail"] = trail
    diag["investigation_seconds"] = round(time.time() - t0, 1)
    diag["ledger"] = list(L.items.values())
    return diag
