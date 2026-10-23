# Incident Zero — AI On-Call Engineer (Team 1)

Day 1 implementation: give the agent an alert; it chooses read-only telemetry tools, investigates downstream failures, submits a schema-validated diagnosis, verifies evidence, and escalates if support is weak. No remediation runs. Day 2 functions are stubs.

## 1. Install and configure

Python 3.11 or later. From the repository root, on Windows PowerShell:

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
Copy-Item .env.example .env  # first setup only; keep an existing .env
```

On macOS/Linux use `python3 -m venv .venv`, `source .venv/bin/activate` and `cp .env.example .env`. `.env` is git-ignored; never commit credentials.

The default model is local Ollama, `qwen3:8b`, through its OpenAI-compatible endpoint. All commands use the same model settings:

```dotenv
LLM_MODEL=qwen3:8b
LLM_BASE_URL=http://localhost:11434/v1
LLM_API_KEY=ollama
```

For an existing teammate `.env`, legacy `LLM_MODEL=ollama_chat/qwen3:8b` and `LLM_API_BASE=http://localhost:11434` are accepted and normalized. LiteLLM is no longer required. Hosted models can use their own OpenAI-compatible base, model and key. Model access must support function/tool calling.

For the local model, install/start Ollama and pull it:

```powershell
ollama pull qwen3:8b
```

The full tool schema and result history need context space. Set Ollama's context to 16384 if needed (`$env:OLLAMA_CONTEXT_LENGTH="16384"` before starting `ollama serve`; if Ollama is already running, quit and restart it with the setting). CPU-only inference may exceed `LLM_TIMEOUT=120`; adjust it in `.env`. The setup checker tests a real tool call without executing that call.

## 2. Offline checkpoint rehearsal

No model server, Docker, API key or network is needed after dependencies are installed:

```powershell
python -m pytest -q
python -m scripts.mock_demo
python -m scripts.mock_demo --mode empty
python -m scripts.mock_demo --mode fabricated
```

Expected: payment-provider failure with verified trace/log quotes; then empty-data escalation; then escalation for fabricated evidence. This uses a **scripted model client** and synthetic telemetry, so it tests plumbing and safety, not LLM diagnosis quality.

To test the actual model against the same synthetic data, set `USE_MOCK=1` in `.env` and run `python main.py "High error rate on payment"`. Set `MOCK_MODE=empty` for absent evidence. Restore `USE_MOCK=0` and `MOCK_MODE=default` before live checks.

## 3. Start the demo and discover telemetry

Docker Desktop with Compose v2 is required for the demo. Clone it outside the agent source (the directory below is git-ignored). Do not modify demo source or fault settings for diagnosis.

```powershell
git clone https://github.com/open-telemetry/opentelemetry-demo
.\scripts\start-demo.ps1           # full demo + observability
# Or: .\scripts\start-demo.ps1 minimal
```

On macOS/Linux:

```sh
cd opentelemetry-demo
docker compose -f compose.yaml -f compose.full.yaml -f compose.observability.yaml up -d
cd ..
```

The startup script checks that the expected Compose files exist. A different demo revision may need the organizer's startup instructions. The full stack requires several GB of RAM and disk space.

| UI | Default URL |
|---|---|
| Shop | http://localhost:8080 |
| Jaeger | http://localhost:8080/jaeger/ui |
| Grafana | http://localhost:8080/grafana/ |
| Load generator | http://localhost:8080/loadgen/ |

Configure `PROMETHEUS_URL`, `JAEGER_URL`, and `OPENSEARCH_URL` in `.env`. Jaeger URL is the base **before** `/api`. A blank OpenSearch URL discovers its host port with `docker port opensearch 9200`; override `OPENSEARCH_CONTAINER` if the container has a different name. Docker health uses `DOCKER_NETWORK=opentelemetry-demo`. Legacy `PROM_URL` and `LOGS_URL` are fallbacks.

```powershell
python -m scripts.discover
python -m scripts.check_setup --skip-llm
python -m scripts.check_setup
```

Discovery saves `runs/discovery.json` with actual metric names, service labels, trace services, and log field mappings. It does not read feature-flag state. Common span-metric names and log fields are discovered automatically. Unknown names require `METRIC_CALLS`, `METRIC_DURATION`, `METRIC_SERVICE_LABEL`, or `LOGS_*_FIELD` overrides from that output. If histogram units cannot be inferred from its name, set `METRIC_DURATION_UNIT=ms` or `s`. Verify `METRIC_ERROR_SELECTOR` against observed status-code labels; a wrong selector can hide errors. Server spans are preferred where available to avoid counting both client and server spans.

## 4. Live checkpoint run

Set `USE_MOCK=0`. Have the authorized organizer/teammate inject a practice incident, keep demo traffic running, and allow telemetry time to arrive. Then:

```powershell
python main.py "High error rate in checkout" --output runs/checkpoint/diagnosis.json
```

The model chooses its own queries; there are no per-incident diagnosis rules. Each step prints tool arguments. Final JSON includes component, cause, confidence, evidence, escalation status, unverified quotes, and an investigation trace with full bounded tool results for review. Escalation exits with code 0; read `escalate` to determine the outcome.

If results are empty, use discovery and check endpoint paths, exact service names, log mappings, time window and load. The model can request `query_metrics(service="")` to discover names, `query_traces(errors_only=false)` for slow/untagged failures, or `query_logs(level="")` for all severities. Backend errors are returned to the agent as data.

To share results, send your teammate `runs/discovery.json` and the diagnosis artifact after checking them for sensitive telemetry. `runs/` stays git-ignored.

## Safety and limitations

- All model tools are read-only and allowlisted. HTTP rejects feature-flag endpoints, redirects, and POSTs outside OpenSearch `_search`. Docker queries inspect state/restarts, never container configuration or environment.
- Quotes must occur in captured output from the named tool; a supplied trace/log identifier must also exist in that same result. Missing evidence caps confidence at 0.3; no evidence caps it at 0.2. Metrics, traces and logs must each produce useful results for a non-escalated diagnosis. Confidence below 0.6 escalates. Provenance checks cannot prove the causal conclusion is correct.
- Tool results are valid JSON bounded to roughly 3000 characters. Large arrays drop later entries; long strings are shortened. Health results prioritize unhealthy/stopped/restarting containers. Very large traces can omit dependency spans; narrow queries or look up another service.
- Metrics compare the selected window with the preceding hour. Non-finite values become null; absent series are not evidence of healthy traffic. A missing error series is treated as zero only if the request series has positive traffic. Histogram units and status labels still need live verification.
- Deployment history has no source and explicitly returns unavailable. Alert polling, UI, action execution, approval and recovery verification are not implemented.
- No organizer schema was present. `agent/diagnosis.schema.json` remains **TODO-ALIGN**. Replace it before scoring and confirm whether trace/verification metadata belongs in the organizer envelope. Schema validation currently applies to diagnosis fields before metadata is attached.
- Offline tests cover integration with simulated backend responses. This workspace has not validated live telemetry, actual Ollama diagnosis quality or organizer scoring.

## Layout

```text
agent/config.py          shared endpoint and model configuration
agent/tools/             raw read-only telemetry clients
agent/telemetry.py        bounded model-facing tools and summaries
agent/mock_tools.py       synthetic fixtures only
agent/loop.py             model-selected investigation loop
agent/evidence.py         evidence verification and confidence guardrails
agent/schema.py           diagnosis schema validation
agent/day2_stubs.py       future remediation interfaces
main.py                   alert-driven CLI and JSON output
scripts/check_setup.py    live connectivity and tool-calling checks
scripts/discover.py       telemetry name/mapping discovery
scripts/mock_demo.py      fully offline scripted demonstration
tests/                    offline unit and integration tests
docs/architecture.md      checkpoint architecture sketch
```

Declare Codex assistance in the organizer team form. Each member should understand and be able to modify their submission.
