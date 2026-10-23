# Architecture — Team 1 AI On-Call Engineer

```mermaid
flowchart LR
    subgraph Demo["OpenTelemetry Astronomy Shop (Docker)"]
        SVC[Microservices] --> COL[OTel Collector]
        COL --> PROM[(Prometheus<br/>metrics)]
        COL --> JAE[(Jaeger<br/>traces)]
        COL --> OS[(OpenSearch<br/>logs)]
        DOCK[(Docker engine<br/>container state)]
        FLAG[flagd and flag UI]
    end

    OFF[(Offline incident bundles<br/>fallback)]

    subgraph Agent["Our agent (Python, local)"]
        GW["Safe-read gateway<br/>flag filter, field scrubbing,<br/>docker and file limits"]
        DET[Detector<br/>rule-based alerts] --> SWEEP[Fixed sweep<br/>overview, containers, restarts]
        SWEEP --> LOOP[Qwen3 8B via Ollama<br/>picks follow-up tools<br/>adaptive step and time cap]
        LOOP <--> TOOLS[Read-only tools<br/>summaries + evidence ids<br/>incl. deploy history]
        LOOP --> DIAG[Diagnosis JSON]
        DIAG --> VAL["Evidence validator<br/>ids exist, values match,<br/>coverage-aware confidence"]
        VAL -->|confidence below 0.7| ESC[Escalate to human<br/>with findings]
        VAL -->|confidence 0.7 or more| PROP[Propose allowlisted fix<br/>plus rollback plan]
        PROP --> DRY[Dry run<br/>read-only preview]
        DRY --> APPROVE{Human approval<br/>kill switch active}
        APPROVE -->|rejected| REP
        APPROVE -->|approved| ACT[Action API<br/>plus audit log]
        ACT --> VERIFY[Verify recovery<br/>re-check alert signals]
        VERIFY -->|still failing or new symptom| LOOP
        VERIFY -->|not recovered| RB[Approved rollback<br/>or escalate]
        VERIFY -->|recovered| REP
        RB --> REP
        ESC --> REP[Incident report<br/>provided JSON schema + markdown]
    end

    PROM --> GW
    JAE --> GW
    OS --> GW
    DOCK --> GW
    OFF --> GW
    GW --> DET
    GW --> TOOLS
    FLAG -. blocked at every layer .-x GW
```

## What each part does
| Part | One-line definition |
|---|---|
| **OpenTelemetry Shop** | The demo microservices app we monitor, with Prometheus (metrics), Jaeger (traces), OpenSearch (logs) and Docker (containers). |
| **flagd and flag UI** | The feature-flag service that holds the fault switches; reading it is forbidden, so it is blocked. |
| **Offline bundles** | Recorded incident data used as a fallback if the live demo can't run. |
| **Safe-read gateway** | The single door for all reads, which filters out anything that could reveal flag settings. |
| **Detector** | Raises an alert when error rate, latency, traffic or container health crosses a threshold. |
| **Sweep** | A fixed first look at services, containers and restarts before digging deeper. |
| **LLM investigation loop** | The local LLM (Qwen3 8B) picks which tool to call next based on what it has found so far. |
| **Read-only tools** | Return short summaries of traces, metrics, logs and container health, each fact tagged with an evidence ID. |
| **Evidence validator** | Checks that cited evidence is real, that quoted values match, and lowers confidence when data is missing. |
| **Escalate to human** | When confidence is low, hands over the findings instead of guessing. |
| **Propose fix + rollback** | Picks an allowlisted runbook action and an undo plan. |
| **Dry run** | A read-only preview of what the fix would do. |
| **Human approval + kill switch** | Nothing changes without an explicit yes, and the kill switch can stop everything at any time. |
| **Action API** | The only allowed way to change the system, with every call logged. |
| **Verify recovery** | Re-checks the same signals that raised the alert to confirm the system recovered. |
| **Rollback or escalate** | Undoes the approved fix or hands over to a human if recovery failed. |
| **Incident report** | Final output: timeline, root cause, evidence, action taken and prevention, in the required JSON schema plus a readable version. |
