# Railhead diagrams

This document contains the flowcharts and the sequence diagrams of Railhead.
The text follows ASD-STE100. Refer to [STE.md](STE.md).
GitHub shows the diagrams from their Mermaid source.

| Number | Diagram | Related text |
|---|---|---|
| 1 | [System](#1-system) | [ARCHITECTURE.md, Overview](ARCHITECTURE.md#overview) |
| 2 | [One action and one wait (lockstep)](#2-one-action-and-one-wait-lockstep) | [ARCHITECTURE.md, 3.1](ARCHITECTURE.md#31-the-lockstep-invariant) |
| 3 | [Verifier](#3-verifier) | [VERIFICATION.md, 2](VERIFICATION.md#2-verifier-procedure) |
| 4 | [Task generation and certification](#4-task-generation-and-certification) | [TASKS.md, 1.5](TASKS.md#15-certificates) |
| 5 | [Harbor trial](#5-harbor-trial) | [ARCHITECTURE.md, 13.2](ARCHITECTURE.md#132-harbor-task) |
| 6 | [Forks](#6-forks) | [ARCHITECTURE.md, 9](ARCHITECTURE.md#9-forks) |
| 7 | [Arena, traces, and charts](#7-arena-traces-and-charts) | [USAGE.md, Run an arena](USAGE.md#run-an-arena) |

## 1. System

The model sends commands only to `rh`.
The daemon sends each command to the engine and records it in the action log.
The verifier reads only the sealed start state and the action log.

```mermaid
flowchart LR
    M["Model<br/>claude -p, codex exec,<br/>or a Harbor agent"] -->|"bash: rh ..."| RH["rh CLI"]
    RH -->|"unix socket"| D["railheadd daemon<br/>task rules and budgets"]
    D --> W["World"]
    W -->|"admin port"| E["openttd -D<br/>patched 15.3"]
    E --- GS["GameScript RailheadBridge<br/>company mode, lockstep"]
    D -->|"append"| LOG[("actions.jsonl")]
    D -->|"save, load, reseed"| F["Forks<br/>other engines"]
    SAV[("start.sav<br/>sealed")] --> V["Verifier<br/>replay in a new engine"]
    LOG --> V
    V --> R[("reward.json")]
```

## 2. One action and one wait (lockstep)

The game is paused when the model acts.
Game time passes only during a step.
At the end of the step, the GameScript pauses the game again.
The daemon records the tick count, the checksum, and a snapshot after each wait.

```mermaid
sequenceDiagram
    autonumber
    participant M as Model
    participant D as rh and daemon
    participant G as GameScript
    participant E as Engine
    Note over G,E: The game is paused
    M->>D: rh road line from_tile=10,4 to_tile=30,4
    D->>G: request road_line (admin port, JSON)
    G->>E: build the road in company mode
    E-->>G: result and cost
    G-->>D: reply
    D->>D: append "act" to actions.jsonl
    D-->>M: ok, cost
    M->>D: rh wait days=60 until=vehicle_crashed
    D->>G: step 4440 ticks (60 days x 74 ticks)
    G->>E: unpause
    loop each tick
        E-->>G: tick count + 1, events
    end
    G->>E: pause at the last tick or at a wake condition
    G-->>D: ticks, checksum, wake reason
    D->>D: append "wait" with the snapshot
    D-->>M: date, money, events
```

## 3. Verifier

The verifier runs after the agent stops.
It examines the hashes before it runs Python code.
Then it replays the action log in a new engine and lets the world continue without the agent.

```mermaid
flowchart TD
    A["The agent stops"] --> B{"sha256sum -c<br/>sealed manifest<br/>for this CPU"}
    B -->|"a hash is different"| T["tamper = 1<br/>reward 0"]
    B -->|"all hashes agree"| C["railhead down<br/>copy actions.jsonl"]
    C --> P["python -I -m railhead.verify"]
    P --> S{"hash of start.sav<br/>agrees with sealed.json?"}
    S -->|"no"| T
    S -->|"yes"| RP["Replay the log in a new engine<br/>whitelist, ticks from the log"]
    RP -->|"rejected, or after the horizon"| X["rejected = 1<br/>reward 0"]
    RP -->|"a checksum is different"| DV["divergence<br/>valid = 0"]
    RP --> H["Run to the end of the horizon<br/>without the agent"]
    H --> L1["Read the objective ledger"]
    L1 --> RO["Run-off: the world continues<br/>without the agent"]
    RO --> L2["Read the ledger and the net worth"]
    L2 --> RW["Family reward, 0 to 1"]
    RW --> OUT[("reward.json<br/>reward.txt")]
```

## 4. Task generation and certification

The generator makes a task from four values: the generator version, the family, the split, and the seed.
It keeps the task only if the oracle replay is valid and the oracle reward is more than the do-nothing reward.

```mermaid
flowchart TD
    SEED["generator version,<br/>family, split, seed"] --> SC["Scenario<br/>map size, climate, year"]
    SC --> MAP["New world<br/>company, warm-up"]
    MAP --> FAM["Family set-up<br/>cargo pair, target, faults"]
    FAM --> SAV[("start.sav<br/>task.json<br/>secret.json")]
    SAV --> OR["The oracle plays<br/>scripted, A* paths"]
    OR --> CERT{"Verifier: oracle valid<br/>and oracle reward more than<br/>do-nothing reward?"}
    CERT -->|"no"| DEL["Delete the task"]
    CERT -->|"yes"| KEEP["Keep the task<br/>certificates.json, sealed.json,<br/>oracle.jsonl"]
    KEEP --> HARD["Harden<br/>opaque IDs, private seeds,<br/>noisy targets"]
    HARD --> PUB["Harbor tasks, image,<br/>Hugging Face dataset"]
```

## 5. Harbor trial

The agent and the verifier use the same container (shared mode).
Harbor uploads `tests/` only after the agent stops.

```mermaid
sequenceDiagram
    participant H as Harbor
    participant C as Container
    participant A as Agent
    participant T as tests/test.sh
    H->>C: start the image (ENTRYPOINT railhead-up)
    H->>C: healthcheck railhead-up task_id
    C-->>H: healthy: daemon up, world paused
    H->>A: instruction.md
    loop until submit or the agent timeout
        A->>C: rh ...
        C-->>A: text reply
    end
    H->>C: upload tests/
    H->>T: run in the same container
    T->>T: sha256sum -c sealed manifest
    T->>T: railhead down, then railhead.verify
    T-->>H: /logs/verifier/reward.json and reward.txt
```

## 6. Forks

A fork is a copy of the world in a different engine process.
The daemon reseeds each fork. Because of this, a fork shows a possible future and does not show the true future.
Actions in a fork are not in the action log. They have no effect on the reward.

```mermaid
flowchart LR
    W["Live world<br/>the only world that the verifier replays"] -->|"save"| S[("fork savegame")]
    S -->|"load in a new engine,<br/>reseed"| F["Fork F"]
    F -->|"rh fork do / fork wait<br/>(each day uses the fork-day budget)"| O["A possible future"]
    O -->|"rh fork q name=F m=company"| M["Model"]
    M -->|"rh ... (logged)"| W
```

## 7. Arena, traces, and charts

An arena puts several players on the same task.
Each player has its own daemon, its own engine, and its own state directory.
The telemetry, the action log, and the verdict of each player go into one trace bundle.
The verified results go into `docs/data/` and then into the charts.

```mermaid
flowchart LR
    AR["railhead arena-run"] --> P["Player directory<br/>daemon and engine"]
    AR --> AG["claude -p or codex exec<br/>first person, rh only"]
    AG -->|"rh"| P
    AG -->|"OTLP/HTTP JSON"| SINK["railhead otel serve"]
    SINK --> TR[("traces/<br/>logs, traces, metrics")]
    AG --> TX[("agent.log")]
    P --> LOG[("actions.jsonl")]
    LOG --> VER["railhead.verify"]
    VER --> RW[("reward.json")]
    TR --> BUN["railhead trace bundle"]
    TX --> BUN
    LOG --> BUN
    RW --> BUN
    BUN --> B[("one bundle for each player<br/>scrubbed, local only")]
    RW --> EXP["railhead arena --export"]
    LOG --> EXP
    EXP --> DATA[("docs/data/arena.json")]
    DATA --> FIG["railhead figures"]
    P --> REN["railhead render"]
    FIG --> IMG[("docs/img/*.svg")]
    REN --> IMG2[("docs/img/*.png")]
```
