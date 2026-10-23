# Incident Zero — AI On-Call Engineer (Team 1)

An agent that detects an incident in the OpenTelemetry Astronomy Shop, investigates it with
read-only telemetry tools (metrics, traces, logs, container health), names the root cause with
evidence, and (WIP) applies an approved runbook fix via the Action API.

Runs fully local: the demo in Docker, the LLM (Qwen3 8B) in Ollama. No hosted API keys.

## Prerequisites
- Docker Desktop with Compose v2 (better to give it ~6 GB RAM; ~14 GB free disk)
- Python 3.11
- Ollama (https://ollama.com) and a GPU with ~8 GB VRAM
- Git

## 1. Get the code
```powershell
git clone https://github.com/sumit-s-nair/bosch-team1
cd bosch-team1
git clone https://github.com/open-telemetry/opentelemetry-demo   # git-ignored, we never edit it
```

## 2. Start the OpenTelemetry demo
```powershell
Set-ExecutionPolicy -Scope Process Bypass   # only if PowerShell blocks the script
.\scripts\start-demo.ps1                    # full demo (~6 GB RAM)
.\scripts\start-demo.ps1 minimal            # 8 GB laptops: no Kafka (~3 GB RAM)
```

```powershell
docker ps --format "table {{.Names}}\t{{.Status}}"
```
| UI | URL |
|---|---|
| Shop | http://localhost:8080 |
| Jaeger | http://localhost:8080/jaeger/ui |
| Grafana | http://localhost:8080/grafana/ |
| Load generator | http://localhost:8080/loadgen/ |

Stop it with `cd opentelemetry-demo; docker compose down`.

## 3. Set up the local LLM
```powershell
setx OLLAMA_CONTEXT_LENGTH 16384     # default 4k context is too small
setx OLLAMA_FLASH_ATTENTION 1
setx OLLAMA_KV_CACHE_TYPE q8_0       # makes 16k context fit in 8 GB VRAM
# quit Ollama from the system tray and reopen it, then:
ollama pull qwen3:8b
```

## 4. Python environment
```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
copy .env.example .env               # defaults work for the local setup
```

## 5. Verify
```powershell
python -m pytest -q                  # offline tests: no Docker, model or network needed
python -m scripts.check_setup        # containers, Prometheus, Jaeger, OpenSearch, LLM tool-calling -> all [ OK ]
python -m scripts.check_setup --skip-llm   # telemetry only
python -m scripts.discover           # optional: metric names, Jaeger API flavour, log field mapping -> runs/discovery.json
python -m scripts.eyes [service]     # run every investigation tool without the LLM (what the agent sees)
python -m scripts.logs_check 10      # which services send logs, with what severity, and with trace ids
```
Log field names are auto-discovered from OpenSearch; override with `LOGS_*_FIELD` in `.env` if discovery fails.

## 6. Run the agent
```powershell
python -m agent.run --now            # investigate the current state immediately
python -m agent.run --watch          # wait for an alert, then investigate
```
Each run writes `runs/<timestamp>/diagnosis.json` (summary, root cause, confidence, cited evidence, tool trail).

## How it works
1. **Detect** (`agent/detector.py`) — rule-based: server error rate > 5%, p95 > 3x baseline, traffic drop, container stopped/unhealthy; must hold for 2 polls.
2. **Sweep** — always runs service overview, container health and recent restarts first.
3. **Investigate** (`agent/brain.py`) — Qwen picks up to 6 follow-up tools from `agent/observe.py`. Tools return short summaries computed in code, each fact tagged with an evidence id `[E#]`.
4. **Diagnose** — the model returns JSON: root cause, category, confidence, cited evidence ids.
5. **Validate** — the JSON is schema-checked (one retry). Cited ids not produced by a tool are dropped. Confidence is capped if evidence is missing, if none of it is abnormal by code thresholds, or if it doesn't mention the blamed service. Below 0.7 the agent escalates; with no alert and nothing abnormal it reports `no_incident`.

## Tools (`agent/observe.py`)
| Tool | What it answers |
|---|---|
| `service_overview` | Request rate, error % and p95 per service vs baseline; services that went silent; optional route/client breakdown |
| `service_operations` | Per-operation errors and latency for one service, incl. outgoing calls |
| `trace_errors` | Which downstream span errors originate from |
| `error_logs` | Recent WARN/ERROR logs grouped by message |
| `container_health` | Stopped / restarting / OOM / recently restarted containers, CPU starved or throttled, memory pressure |
| `resource_usage` | Top CPU/memory/memory-growth containers; runtime GC/heap/CPU for one service |
| `recent_changes` | Containers (re)started recently (deployment-history proxy) |
| `cache_health` | Valkey/Redis cache behind cart |
| `db_health` (`agent/tools/observe_db.py`) | Postgres `astronomy-db` behind product-catalog: container, metric freshness, connections, deadlocks/locks, DB query latency, code-computed verdict (down / slow-lock contention / errors / healthy) |

## Layout
```
agent/
  config.py              endpoints + LLM settings (.env)
  tools/                 raw read-only clients: Prometheus, Jaeger (v3/legacy), OpenSearch, docker
  tools/observe_db.py    db_health tool (Postgres astronomy-db), merged into observe.TOOLS
  observe.py             LLM-facing tools with summaries and evidence ids
  evidence.py            evidence ledger
  flagguard.py           keeps feature-flag data (flagd spans/logs, flag UI, flag mentions) away from the model
  detector.py            alert rules
  llm.py                 Ollama /api/chat client
  brain.py               investigation loop + validation
  schema.py              JSON-schema check of the diagnosis (diagnosis.schema.json, TODO-ALIGN with organiser schema)
  day2_stubs.py          Day 2 interfaces: propose fix, approval, act + verify
  run.py                 CLI entry point
scripts/                 start-demo.ps1, check_setup.py, discover.py, eyes.py
tests/                   offline tests (network blocked)
docs/architecture.md     one-page architecture
```

## Safety
- All telemetry tools are read-only: docker access is limited to `ps`/`inspect`/`stats`; HTTP POST is only allowed to OpenSearch `_search`; redirects are refused.
- Any URL touching feature-flag sources (flagd, flag UI/API, ports 8013/8016) is refused in `agent/tools/http.py`.
- `agent/flagguard.py` drops flagd's own spans and logs, client calls to flagd, flag-UI gateway access logs, and redacts any log or error text mentioning flags. `tests/test_flag_leaks.py` proves each route is blocked.
- System changes only via the Action API and only after human approval (Day 2, WIP).


## Known limitations

Runtime health in `resource_usage(service=..., window="10m")` depends on the metrics each service emits. Runtime kind is inferred from discovered metric families, not service names. Unsupported/missing GC, heap or process-CPU metrics are reported in notes. The global metric-name list is cached for 60 seconds; series are checked for the requested service. Container summaries remain available without a service argument.

Traffic breakdowns in `service_overview(window="5m", service=None, group_by="route" or "client")` depend on the span labels available in this demo version. Missing route/client labels produce notes. The baseline is the preceding hour excluding the current window; missing/zero baselines leave ratios unknown. Gateway counters are included only when discovered and compatible with the requested service. Global gateway counters cannot reliably be attributed to one service.

`memory_growing` (>20% growth) and per-route `spike` (>3x baseline) are plain numerical hints, not incident conclusions. Values are returned with evidence IDs; list output is capped and can be shortened further to stay below roughly 3000 characters. The tool registry has nine tools (`db_health` added).

To verify these extensions against the live demo, run `python -m scripts.check_extensions --service <exact-service-name>` (omit `--service` to discover a service emitting runtime metrics). It prints the actual metric-name list and raw tool outputs, and saves them under `runs/runtime-validation/`. This is a diagnostic script, not an additional agent tool.
