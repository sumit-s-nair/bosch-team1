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
python -m scripts.check_setup        # containers, Prometheus, Jaeger, OpenSearch, LLM -> all [ OK ]
python -m scripts.discover           # optional: dumps metric names/log fields to runs/discovery.json
```

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
5. **Validate** — cited ids not produced by a tool are dropped; confidence is capped if evidence is missing or doesn't mention the blamed service. Below 0.7 confidence the agent escalates instead of proposing a fix.

## Layout
```
agent/
  config.py       endpoints + LLM settings (.env)
  tools/          raw read-only clients: Prometheus, Jaeger, OpenSearch, docker
  observe.py      LLM-facing tools with summaries and evidence ids
  evidence.py     evidence ledger
  detector.py     alert rules
  brain.py        investigation loop + validation
  run.py          CLI entry point
scripts/          start-demo.ps1, check_setup.py, discover.py
```

## Safety
- All telemetry tools are read-only (docker access limited to `ps`/`inspect`/`stats`/`logs`).
- Any URL touching feature-flag sources (flagd, flag UI/API) is refused in `agent/tools/http.py`.
- System changes only via the Action API and only after human approval (Day 2, WIP).

