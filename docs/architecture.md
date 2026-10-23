# Day 1 architecture

```text
Alert -> bounded agent loop -> model chooses read-only tool
                ^                        |
                |---- summarized JSON <--+
                |
        submit_diagnosis
                |
      JSON schema validation (one retry)
                |
      evidence provenance and confidence checks
                |
      diagnosis + escalation + investigation trace
```

Five adapters query Prometheus rates and latency, Jaeger spans, OpenSearch logs, Docker health, and deployment history (explicitly unavailable). All queries have timeouts and return bounded JSON or errors. Only allowlisted tool names can execute. The model cannot invoke arbitrary commands. Docker inspection requests state and restart count only, never environment or configuration. No adapter reads flag state or mutates the demo.

The loop receives each result separately and lets the model choose its next query. A maximum step count bounds the investigation. Unknown tools, invalid arguments and backend errors become tool messages; model failures or exhausted budgets yield an escalation. Each call records its tool, arguments, result preview, and full bounded result.

Submission parameters derive from the diagnosis JSON schema. Evidence must be a normalized substring of the result from its named tool. Missing quotes cap confidence at 0.3; absent evidence caps at 0.2. Missing useful results from any of metrics, traces or logs caps confidence at 0.5 and forces escalation; any confidence below 0.6 escalates. These safeguards cannot establish that a causal conclusion is true.

Mock adapters and an injectable model client allow offline verification. Live backend mappings and the organizer schema remain integration inputs. Day 2 functions raise NotImplementedError until safe proposals, human approval, Action API execution and recovery verification are implemented.

All clients share `agent/config.py` and `agent/tools/http.py`. `agent/telemetry.py` adapts them to the five model-facing functions. Prometheus series and OpenSearch field capabilities support discovery; no demo configuration is read. The CLI and setup checker use the same OpenAI-compatible model client and normalize legacy Ollama settings.
