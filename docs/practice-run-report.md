# Practice run report — Team 1 AI On-Call Engineer

**Run:** 23 Oct 2026, 17:43–17:51 · 3 recorded incidents · full loop (detect → investigate → approve → fix → verify → report)
**Model:** Qwen3 8B, local via Ollama · **Mode:** offline scenarios, simulated Action API

## Results

| # | Incident | Agent's root cause | Confidence | Proposed fix | Human | Outcome | Time |
|---|---|---|---|---|---|---|---|
| 1 | Database down | `astronomy-db` — dependency failure | 0.95 | restart `astronomy-db` | approved | fixed and verified | 93 s |
| 2 | Product catalog fails for one product | `product-catalog` — application error | 0.80 | turn off a feature flag for `product-catalog` | approved, named the flag | fixed and verified | 90 s |
| 3 | Cart fails intermittently | `cart` — application error | 0.80 | turn off a feature flag for `cart` | approved, named the flag | fixed and verified | 92 s |

## How a run works

1. **Detect** — rule-based alerts (no AI): error rate > 5%, p95 latency 3× baseline, traffic drop, container down. Must hold for 2 polls.
2. **Investigate** — a fixed first sweep (service overview, container health, recent restarts), then the model picks up to 6 follow-up tools based on what it has found.
3. **Validate (code)** — diagnosis must match the schema; evidence ids not produced by a tool are dropped; confidence is capped if no cited fact is abnormal by code thresholds or the blamed service is missing from the evidence. Below 0.7 → escalate.
4. **Propose a fix (code)** — the diagnosis category maps to one allowlisted runbook action. The model never chooses the action.
5. **Human approval** — diagnosis, evidence, fix and a dry-run preview are shown; only an explicit `yes` approves. A `STOP` file is the kill switch.
6. **Fix** — through the Action API only, audit-logged (simulated in this run).
7. **Verify** — re-checks the signals that raised the alert; not recovered → escalate.
8. **Report** — `report.json` + `report.md` per incident.

Each incident's files are in `runs/<id>/`: `alert.json`, `diagnosis.json`, `plan.json`, `approval.json`, `actions.jsonl`, `validation.json`, `report.json`, `report.md`, `timeline.jsonl`.

---

## Incident 1 — Database down

**Alerts:** error rate on `frontend`, `recommendation`, `product-catalog`; container `astronomy-db` not running.

**What the agent checked (its own choices, in order):** `db_health` → `trace_errors(product-catalog)` → `service_operations(product-catalog)` → `error_logs(product-catalog)`

**Evidence cited**
| Id | Observed |
|---|---|
| E12 | `astronomy-db`: status=exited, exit=0, restarts=0 |
| E16 | Trace error origin: product-catalog `SELECT catalog.products` — `dial tcp 172.18.0.5:5432: connect: connection refused` (4 traces) |
| E23 | product-catalog ERROR log ×12: `Error querying products: … connection refused` |
| E15 | Database health verdict: **database DOWN** |

**Diagnosis:** *The astronomy-db PostgreSQL database is down, causing product-catalog to fail all queries and triggering cascading errors in dependent services.*
**Ruled out:** network latency, resource exhaustion, traffic flood.
**Fix:** `restart_service(service=astronomy-db)` → approved → executed → alerts cleared on both verification polls → **fixed and verified**.

---

## Incident 2 — Product catalog fails for one product

**Alert:** error rate on `product-catalog`.

**What the agent checked:** `db_health` → `service_operations(product-catalog)` → `trace_errors(product-catalog, GetProduct)` → `error_logs(product-catalog)` → `dependency_latency(product-catalog)`

**Evidence cited**
| Id | Observed |
|---|---|
| E1 | product-catalog: 10% errors (baseline 0%), latency unchanged |
| E15 | `GetProduct` server operation: 12% errors |
| E11 | `astronomy-db`: running, no restarts — used to rule the database out |
| E14 | Database health verdict: healthy — used to rule the database out |

**Diagnosis:** *product-catalog has 12% errors in GetProduct despite a healthy database — an internal service failure.*
**Ruled out:** database unavailable, network latency, container down.
**Fix:** application error in a healthy container → a restart would not help, so the runbook proposes **turning off the feature behind it**. The agent never reads feature flags, so it cannot name one: the human typed the flag at approval. → **fixed and verified**.
**Note:** the error text from this service names the flag; the flag guard redacted it before the model saw it.

---

## Incident 3 — Cart fails intermittently

**Alert:** error rate on `checkout`.

**What the agent checked:** `service_operations(checkout)` → `trace_errors(checkout, PlaceOrder)` → `error_logs(checkout)` → `service_operations(cart)` → `trace_errors(cart, EmptyCart)` → `cache_health`

**Evidence cited**
| Id | Observed |
|---|---|
| E1 | checkout: 50% errors (baseline 0%) |
| E17 | checkout ERROR log: `failed to empty user cart … FailedPrecondition … Can't access cart storage` |
| E18 | cart `EmptyCart` server operation: 50% errors |
| E23 | Valkey cache: up, 3 clients, 0 rejected connections — used to rule the cache out |

**Diagnosis:** *Checkout errors originate from the cart service's EmptyCart failure (cart-storage connection errors), while the Valkey cache itself is healthy.*
**Ruled out:** database issues, traffic spike, cache unavailability.
**Fix:** application error in `cart` → turn off a feature flag for `cart`, flag named by the human → **fixed and verified**.

