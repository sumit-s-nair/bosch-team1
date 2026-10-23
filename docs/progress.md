# Progress Log — Team 1 AI On-Call Engineer (Incident Zero)

Day 1, Friday 23 Oct 2026. Bosch one-day AI hackathon at PES University: build an agent that handles a production incident in the OpenTelemetry Astronomy Shop, from alert to a safe, human-approved fix.

## Day 1 milestones and where we are

| Time | Milestone | Expected outcome | Our status |
|---|---|---|---|
| 11:30–12:00 | Kickoff | Problem reveal, starter kit, rules | Done: went through the brief, rules and scoring |
| 12:00–13:00 | Setup | Demo running with load, `faultctl` works, LLM key works, repo forked, members committed | Done: demo runs, local LLM works, repo created with collaborators |
| 13:00 | Checkpoint 0: Setup | Mentor confirms setup | Setup can be verified with `scripts/check_setup.py` |
| 13:00–15:30 | The agent can see | Tools for logs, metrics, traces, deploy history; symptoms summarised; 1-page architecture sketch | Done: 14 tools, `scripts/eyes.py`, architecture doc |
| 15:30 | Checkpoint 1: Eyes | Live run on one practice incident, plus the sketch | Ready: `python -m scripts.eyes` and `architecture.md` |
| 15:30–18:30 | The agent can diagnose | Root cause with evidence and confidence, output in the JSON schema | Done: diagnosis with evidence, confidence and schema check |
| 18:30 | Checkpoint 2: Brain | Correct diagnosis on at least 3 practice incidents | Ran the agent on 3 practice incidents, as asked |
| 18:45 | Day 1 wrap-up | Day 1 code state saved | To do: commit and push the final Day 1 state |

## What we did, step by step
1. **Read the brief.** The agent must detect, investigate, diagnose with evidence, propose an allowlisted fix, get human approval, act, verify and report. Key rules: never read feature-flag settings, never act without approval, escalating when unsure scores better than a wrong fix.
2. **Set up the environment.** OpenTelemetry demo in Docker (with a minimal mode for 8 GB laptops), local LLM (Qwen3 8B via Ollama, no hosted API keys), Python 3.11 environment, repo with collaborators.
3. **Chose the design.** A tool-calling LLM inside a fixed flow: detect, sweep, investigate, diagnose, validate, then escalate or propose a fix. Written up as a diagram with one-line definitions in `architecture.md`, and a detailed version in `docs/detailed architecture/`.
4. **Built the Eyes (read-only tools).** Raw clients for Prometheus (metrics), Jaeger (traces), OpenSearch (logs) and Docker (containers), and LLM-facing tools that return short summaries with evidence IDs `[E#]`. Flag sources are refused at the HTTP layer.
5. **Built the Brain.** Rule-based detector, a fixed first sweep, an LLM loop that picks follow-up tools, a diagnosis in JSON (root cause, confidence, cited evidence), and a validator that drops invented evidence and caps confidence. Low confidence escalates to a human.
6. **Tested and ran.** Scripts to check the setup and run every tool without the LLM, tests for the pipeline, and runs on practice incidents.

## Code built so far

| Area | Files |
|---|---|
| Entry point and config | `agent/run.py`, `agent/config.py`, `.env.example`, `requirements.txt` |
| Local LLM | `agent/llm.py` |
| Detection | `agent/detector.py` (error rate, p95 vs baseline, traffic drop, container down) |
| Brain and output | `agent/brain.py`, `agent/schema.py`, `agent/diagnosis.schema.json`, `agent/evidence.py` |
| Investigation tools | `agent/observe.py` plus priority tool sets in `agent/tools/observe_p1.py` and `observe_p2.py` (14 tools in total) |
| Raw read-only clients | `agent/tools/` (`metrics.py`, `metric_details.py`, `traces.py`, `logs.py`, `containers.py`, `http.py`) |
| Test helpers | `agent/tools/mock_tools.py`, `agent/tools/test_p1_tools.py` |
| Day 2 placeholders | `agent/day2_stubs.py` |
| Scripts | `scripts/start-demo.ps1`, `check_setup.py`, `check_extensions.py`, `discover.py`, `eyes.py` |
| Tests | `tests/conftest.py`, `tests/test_pipeline.py`, `tests/test_metric_extensions.py` |
| Docs | `README.md`, `architecture.md`, `docs/architecture.md`, `docs/day1.md`, `docs/detailed architecture/architecture.md` |

## Verification so far
- **14 investigation tools** built as of now, covering metrics, traces, logs, container health and recent changes.
- **Telemetry and logs scraped from the OpenTelemetry demo** to see what the agent receives.
- **Around 15 fault/incident scenarios are available.** We have run the agent on **3** of them, as asked.

## Next steps (Day 2): the fixing part, up to the human approval
Starting point is `agent/day2_stubs.py`. The goal for tomorrow is that a diagnosis turns into a safe proposed fix and stops at a human decision.

1. **Map the diagnosis to a fix.** Define the allowlisted runbook actions (restart a service, toggle a feature flag, throttle the load generator, scale a service) and choose one from the diagnosed component and category, with a short reason. No suitable action means escalate with the findings.
2. **Attach a rollback plan.** Every proposed action comes with how to undo it and what effect we expect.
3. **Add a dry run.** A read-only preview of what the action would do, the only step allowed without approval.
4. **Build the approval gate.** Show the diagnosis, evidence, confidence, proposed action and rollback together, then wait for an explicit yes or no. A no, or no answer, ends in the report with nothing changed.
5. **Add the kill switch.** A global stop that is checked before proposing, while waiting for approval, and before any action.
6. **Test the flow on the practice incidents.** Check that each one reaches the approval prompt with a sensible proposal, and that escalation happens when confidence is low.

After approval is in place, the remaining Day 2 pieces are: execute through the Action API, verify recovery, roll back or escalate, and write the incident report, evaluation note and final README.
