# Architecture — Team 1 AI On-Call Engineer


```mermaid
flowchart TD
    subgraph DEMO["OpenTelemetry Astronomy Shop (Docker)"]
        SVC[Microservices] --> COL[OTel Collector]
        COL --> PROM[(Prometheus<br/>metrics)]
        COL --> JAE[(Jaeger<br/>traces)]
        COL --> OS[(OpenSearch<br/>logs)]
        DOCK[(Docker engine<br/>container state)]
        FLAG[flagd and flag UI<br/>fault switches]
    end

    OFF[(Offline incident bundles<br/>fallback)]

    subgraph EYES["Layer 1: Eyes - read only"]
        GW["Safe-read gateway<br/>flag filter, field scrubbing,<br/>docker and file limits"]
        TOOLS["14 investigation tools<br/>summaries with evidence ids"]
        LEDGER[Evidence ledger<br/>E1, E2, E3 ...]
    end

    subgraph BRAIN["Layer 2: Brain"]
        DET[Detector<br/>rule-based alerts] --> SWEEP[Fixed sweep<br/>overview, containers, changes]
        SWEEP --> LOOP[LLM investigation loop<br/>Qwen3 8B via Ollama<br/>picks next tool]
        LOOP --> DIAG[Diagnosis JSON<br/>root cause, confidence, evidence]
        DIAG --> VAL["Validator in code<br/>drop invented ids,<br/>cap confidence"]
    end

    subgraph HANDS["Layer 3: Hands - gated"]
        ALLOW[Runbook allowlist<br/>component and category to action]
        PROP[Proposal<br/>action, reason, rollback plan]
        DRY[Dry run<br/>read-only preview]
        APPROVE{Human approval<br/>explicit yes needed}
        KILL[Kill switch]
        ACT[Action API client<br/>plus audit log]
        VERIFY[Verify recovery<br/>re-check alert signals]
        RB[Approved rollback<br/>or escalate]
    end

    ESC[Escalate to human<br/>with findings]

    subgraph OUT["Layer 4: Reporting and evaluation"]
        REP[Incident report<br/>JSON schema plus readable]
        TL[Timeline logger]
        HAR[Practice harness<br/>and evaluation note]
    end

    PROM --> GW
    JAE --> GW
    OS --> GW
    DOCK --> GW
    OFF --> GW
    FLAG -. blocked .-x GW

    GW --> TOOLS
    GW --> DET
    TOOLS --> LEDGER
    LOOP <--> TOOLS
    LEDGER --> VAL

    VAL -->|confidence below 0.7| ESC
    VAL -->|0.7 or more and known category| ALLOW
    ALLOW -->|no matching action| ESC
    ALLOW --> PROP --> DRY --> APPROVE
    APPROVE -->|rejected or no answer| REP
    APPROVE -->|approved| ACT
    ACT --> VERIFY
    VERIFY -->|recovered| REP
    VERIFY -->|still failing or new symptom| LOOP
    VERIFY -->|not recovered| RB
    RB --> REP
    ESC --> REP

    KILL -.-> APPROVE
    KILL -.-> ACT
    TL -.-> REP
    HAR -.-> REP

    classDef built fill:#d4edda,stroke:#2e7d32,color:#000
    classDef todo fill:#fff3cd,stroke:#b8860b,color:#000
    class TOOLS,LEDGER,DET,SWEEP,LOOP,DIAG,VAL built
    class GW,OFF,ALLOW,PROP,DRY,APPROVE,KILL,ACT,VERIFY,RB,ESC,REP,TL,HAR todo
```

## Legend
| Layer | Job |
|---|---|
| **Eyes** | Reads telemetry safely and turns it into short summaries with evidence IDs |
| **Brain** | Detects, investigates with the LLM, diagnoses, and validates the evidence in code |
| **Hands** | Picks an allowlisted fix, asks a human, acts through the Action API, verifies |
| **Reporting and evaluation** | Timeline, incident report, practice runs and the evaluation note |

## Rules the design enforces
1. Never read feature-flag settings: everything is read through one gateway.
2. Never act without human approval: the Action API is reachable only after the approval gate.
3. When unsure (below 0.7 confidence), escalate with findings instead of guessing.

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
| **Read-only tools** | Return short summaries of metrics, traces, logs, container health, the Valkey cache, the Postgres database (`db_health`: down vs slow/lock contention behind product-catalog), network latency between services, error breakdown by attribute, single-trace and trace-log lookup, and the Kafka queue, each fact tagged with an evidence ID. |
| **Evidence validator** | Checks that cited evidence is real, that quoted values match, and lowers confidence when data is missing. |
| **Escalate to human** | When confidence is low, hands over the findings instead of guessing. |
| **Propose fix + rollback** | Picks an allowlisted runbook action and an undo plan. |
| **Dry run** | A read-only preview of what the fix would do. |
| **Human approval + kill switch** | Nothing changes without an explicit yes, and the kill switch can stop everything at any time. |
| **Action API** | The only allowed way to change the system, with every call logged. |
| **Verify recovery** | Re-checks the same signals that raised the alert to confirm the system recovered. |
| **Rollback or escalate** | Undoes the approved fix or hands over to a human if recovery failed. |
| **Incident report** | Final output: timeline, root cause, evidence, action taken and prevention, in the required JSON schema plus a readable version. |
