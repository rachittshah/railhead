# Waybill: a shared world for several agents

This document describes Phase 1 of Waybill: several agents play one OpenTTD world, each agent as one company, with one lockstep clock.
It describes the log v3 format, the coordinator, the replay, and the verifier.
The text follows ASD-STE100. Refer to [STE.md](STE.md).
For the single-agent design, refer to [ARCHITECTURE.md](ARCHITECTURE.md) and [VERIFICATION.md](VERIFICATION.md).

## 1. Overview

| Part | Location | Function |
|---|---|---|
| Log v3 | `src/railhead/multi/log.py` | The record schema and the ordering rules of the merged log (pure Python) |
| Start state | `src/railhead/multi/gen.py` | Founds N companies in one task world and saves `start.sav` |
| Shared world | `src/railhead/multi/world.py` | Binds the company of each request and writes the v3 records |
| Barrier | `src/railhead/multi/barrier.py` | Decides when the world steps, by how many ticks, and which agents wake (pure Python) |
| Coordinator | `src/railhead/multi/coordinator.py` | Owns the world. Serves one socket for each agent and one control socket. |
| Replay | `src/railhead/multi/replay.py` | Replays a v3 log and gives the net worth of each company |
| Verifier | `src/railhead/multi/verify.py` | Writes `scores.json` with the net worth of each company |

Single-agent Railhead does not change.
The single-agent replay and verifier accept log versions 1 and 2 only. They reject a v3 log.
The v3 replay accepts v3 logs only.

## 2. Log v3

### 2.1 Records

The log is a JSONL file with one record on each line.
The coordinator writes all records. The agents do not write to the log.
Each record has sorted keys. `seq` increases by one for each record. The header is line 0, thus the first record after the header has `seq` 1.

| Kind | Fields | Description |
|---|---|---|
| `header` | `v` (3), `companies`, `ticks`, `checksum` | `companies` is a list of `{id, name, slot}`. `ticks` and `checksum` are those of the start state. |
| `act` | `seq`, `ticks`, `company`, `m`, `a`, `ok`, `ret` or `code` | One player action of one company and its result |
| `meta` | `seq`, `ticks`, `company`, `m`, `a` | One meta decision of one company (for example `submit`) |
| `wait_req` | `seq`, `ticks`, `company`, `a`, `target`, optional `auto` | One wait request. `a` has `days` or `ticks`, and optional `until`. `target` is the tick at which the wait ends. `auto` is `true` for a wait that the coordinator starts for a straggler. |
| `step` | `seq`, `ticks`, `end_ticks`, `checksum`, `wakers`, `snap` | The world ran from `ticks` to `end_ticks`. `wakers` is a list of `{company, reason}`. `snap` has the money and the loan of each company (the replay ignores it). |

Example:

```json
{"checksum":{"h":113575956137496,"ticks":370},"companies":[{"id":0,"name":"Company A","slot":0},{"id":1,"name":"Company B","slot":1}],"kind":"header","ticks":370,"v":3}
{"a":{"amount":300000},"company":0,"kind":"act","m":"company_loan","ok":true,"ret":{"loan":300000},"seq":1,"ticks":370}
{"a":{"days":5},"company":0,"kind":"wait_req","seq":2,"target":740,"ticks":370}
{"a":{"days":11},"company":1,"kind":"wait_req","seq":3,"target":1184,"ticks":370}
{"checksum":{"h":90210,"ticks":740},"end_ticks":740,"kind":"step","seq":4,"snap":{},"ticks":370,"wakers":[{"company":0,"reason":"elapsed"}]}
```

### 2.2 Time

Only `step` records move the clock.
All times are in ticks. One game day is 74 ticks.
A wait of `days=N` that starts at tick T has the target `T + 74 * N`.
The horizon is the tick `start ticks + 74 * horizon_days`. The coordinator caps each target at the horizon.

Note: a single-agent `wait days=N` stops at a date change. A v3 wait stops at an exact tick count. The two can differ by less than one day.

### 2.3 Ordering rules

The replay and the coordinator recovery use one state machine, `LogState`, for these rules.
A log that breaks a rule is rejected (`LogRejected`, which is a `ReplayRejected`).

1. The first record is a v3 header with one or more companies. Each company id and each slot occurs one time.
2. `seq` is contiguous. Each record has `ticks` equal to the current clock.
3. `act`, `meta`, and `wait_req` name a company of the header. That company must not wait and must not have submitted.
4. `act` and `wait_req` are not permitted at or after the horizon. `meta` is permitted.
5. `wait_req` has valid arguments, and `target` is the current tick plus the wait length, capped at the horizon. The company then waits until a `step` names it in `wakers`.
6. A `step` must move time forward and must have a checksum.
7. A `step` occurs only when every live company waits. A live company is a company that has not submitted. If no company is live, a `step` is not permitted.
8. `end_ticks` is not after the earliest target of the waiting companies and is not after the horizon.
9. Each waker is a waiting company and occurs one time. A waiter whose target is reached must wake with the reason `elapsed`. A waker whose target is not reached must have a reason from its own `until` list. At the horizon, every waiter wakes with the reason `horizon`.
10. Acts and metas of different companies at the same tick keep the log order. The replay sends them again in that order.

`wait_req` records and `wakers` have no effect on the world.
The replay examines them for structure only.
They show who waited for what, and they make the lockstep rule examinable.

### 2.4 Meta digest

The live world and the replay send the digest of each `meta` record to the GameScript (`note`), as in a v2 log.
The v3 digest includes the company: `sha256({"c": company, "m": m, "a": a})`, first 31 bits.
Thus a `submit` that moves to a different company changes all later step checksums.

## 3. Start state

`railhead.multi.gen` makes the start state of a Waybill task:

1. It makes the map from the scenario seed and runs 31 game days, so the production values are real.
2. It founds N companies. For each company it starts the idle AI `RailheadIdle`, binds the new company, and gives it a neutral name (`Company A`, `Company B`, and so on).
3. Optional `start_cash`: it sets the loan of each company to this value. A new company has cash equal to its loan, thus each company starts with this cash and a net worth of 0. The value must be a multiple of the loan interval and not more than the maximum loan. All companies get the same value.
4. It saves `start.sav` and writes `task.json` with the family `waybill`. `objective.companies` is the list `[{id, name, slot}]`. `limits.agent_turn_s` is the turn budget of each agent.
5. It seals the task (`sealed.json`), as `railhead export` does for a single-agent task.

```
python -m railhead.multi.gen <seed> <out_dir> [--agents 3] [--horizon 730] [--runoff 0] [--map-log2 K] [--start-cash MONEY]
```

### 3.1 No GameScript change

The GameScript acts and reads for one bound company.
The shared world sends `bind_company` before a request for a different company.
The checksum does not include the bound company. Thus the bind order has no effect on the replay, and existing single-company logs verify as before.
The integration test `tests/integration/test_multi_world.py` examines this.

## 4. Coordinator

### 4.1 Sockets and locks

One coordinator process owns one shared world.

| Socket | Path | Operations |
|---|---|---|
| Agent socket | `run/agent-<slot>.sock` | `ping`, `info`, `query`, `act`, `meta`, `wait`, `history` |
| Control socket | `run/control.sock` | `ping`, `status`, `shutdown` |

If a path is longer than the AF_UNIX limit, the coordinator uses a short hashed path in `/tmp`.
The file `run/sockets.json` gives the paths.
Each request on an agent socket is for the company of that slot.
An agent cannot send a request for a different company on its socket.
Forks are not available (`ERR_NOT_SUPPORTED`).
`history` gives the records of the company of the socket and the `step` records.

One world lock serialises all engine calls and all log writes, because the bridge has one reply queue.
The barrier state has its own lock and condition variable.
The lock order is: the world lock, then the barrier lock.

To use the socket of one agent, set one of these variables in the shell of that agent:

| Variable | Value |
|---|---|
| `RAILHEAD_SOCKET` | The path of the agent socket |
| `RAILHEAD_SLOT` | The company slot. `rh` uses `run/agent-<slot>.sock` under `RAILHEAD_STATE`. |

When one of these variables is set, `rh` does not start a single-agent daemon, and the client time-out is 24 hours.
When the two variables are not set, `rh` operates as before.

Start the coordinator:

```
python -m railhead.multi.coordinator <task_dir> <state_dir>
```

### 4.2 The wait barrier

A `wait` request does these steps:

1. The coordinator takes the world lock. It examines again that the company does not wait, has not submitted, and is before the horizon.
2. It writes a `wait_req` record and adds a waiter to the barrier. These two steps are one operation for the clock thread, because the clock thread needs the world lock to step.
3. It releases the world lock and blocks on the condition variable. A blocked wait does not hold the world lock. Thus the other agents can act and read.

The clock thread does these steps:

1. It waits until every live company waits.
2. It calculates the step length: to the earliest target, not more than the horizon, and not more than 7 days when a waiter has wake conditions.
3. It steps the world by that exact number of ticks. The GameScript gets no wake conditions.
4. It decides the wakers, writes one `step` record, and wakes those agents. The reply has `ymd`, `date`, `ticks`, `reason`, and `days`.

The other waiters continue to wait.

### 4.3 Wake conditions

The GameScript examines `cash_below` for the bound company only, and a GameScript step that stops on an event does not tell which company the event is for.
Thus Waybill uses the fallback of the plan: the world steps in chunks of not more than 7 days, and the coordinator examines the conditions of each company in Python after each chunk.

| Condition | Examination after each chunk |
|---|---|
| `cash_below:X` | The cash of the company is less than X. |
| `new_month` | The month is different from the month when the wait started. |
| `company_bankrupt`, `company_in_trouble` | An event of the chunk names the company. |
| `vehicle_crashed` | An event of the chunk names a vehicle of the company (the vehicles when the wait started). |
| `station_first_vehicle` | An event of the chunk names a station of the company (the stations when the wait started). |
| `industry_open`, `industry_close` | An event of this kind occurs in the chunk. |

A condition can thus wake an agent up to 7 days after the event.

### 4.4 Stragglers

Each agent has a wall-clock budget for each turn: `limits.agent_turn_s` in `task.json` (default 600 seconds).
A turn starts when the coordinator starts and each time the agent wakes.
If a turn is longer than the budget, the coordinator writes a `wait_req` with `a = {"days": 7}` and `auto = true` for that company.
Then the world can step.
The next `act`, `meta`, or `wait` request of that company blocks until that wait ends.
A `wait` request from a company that already waits joins that wait and gets its result.
This also applies to an agent whose connection stopped during a wait and who sends the wait again.

### 4.5 Submit and horizon

`submit` removes the company from the lockstep. The world continues to step for the other companies.
After `submit`, `act` and `wait` from that company get `ERR_SUBMITTED`.
At the horizon, all waiters wake with the reason `horizon`. Then `act` and `wait` get `ERR_HORIZON`, and the world does not step again.
If all companies submit, the world stops. The verifier runs it to the horizon.

### 4.6 Boot and faults

When the coordinator starts, it loads `start.sav`.
If `run/actions.jsonl` is not empty, it replays the log, and it makes the barrier again from the log: the waiting companies, their targets, and the submitted companies.
If the engine does not reply to a request that changes the world, the coordinator replays the log in the same way and replies `ERR_INFRA`.
The log does not contain the failed request.

## 5. Replay and verifier

`replay_multi(start_save, log, workdir, horizon_days=...)` does these steps for each record, after `LogState` examines it:

| Record | Replay |
|---|---|
| `act` | Binds the company and sends the action again with the recorded arguments. A different `ok` is a divergence. |
| `meta` | Sends the v3 digest to the GameScript. |
| `wait_req` | No effect on the world. |
| `step` | Runs exactly `end_ticks - ticks` ticks and compares the checksum. A different checksum is a divergence. |

The header tick must be the start tick, else the log is rejected. A different header checksum is a divergence.
The result has the net worth (cash minus loan) of each company at the end of the log.

`verify_multi(task_dir, start_sav, log, workdir)` does these steps:

1. It examines the hash of `start.sav` (`tamper`).
2. It examines that the companies of the log header (id, slot, and name) are the companies of `task.json`. If they are different, the log is rejected. Thus a header cannot change which net worth goes to which slot.
3. It replays the log. A rejected log gives `rejected = 1`. It attributes a divergence with a second replay, as the single-agent verifier does.
4. It runs the world to the horizon and through the run-off.
5. It gives the net worth of each company at the end of the log, at the horizon, and after the run-off.

```
python -m railhead.multi.verify <task_dir> <start.sav> <actions.jsonl> <out_dir>
```

The command writes `<out_dir>/scores.json`.

## 6. Tests

| Test | Contents |
|---|---|
| `tests/unit/test_multi_log.py` | The record schema and the ordering rules |
| `tests/unit/test_multi_barrier.py` | The step length, the wakers, the wake conditions, and the straggler rule |
| `tests/unit/test_multi_coordinator.py` | The coordinator on a fake engine: lockstep, stragglers, the examination under the world lock, submit, horizon, and the `rh` socket selection |
| `tests/integration/test_multi_world.py` | Three companies in one start state, binding, and determinism |
| `tests/integration/test_waybill.py` | The gate: three scripted agents play one game year through their sockets. The replay gives every step checksum and the net worth of each company. A forged act and a relabelled header are rejected. A restarted coordinator recovers the world and the barrier from the log. |
| `tests/integration/test_waybill_events.py` | A short game: `station_first_vehicle` wakes its company early, `rh` acts through `RAILHEAD_SOCKET`, the world steps on after a submit, the replay is valid, and a moved submit and a changed `start.sav` are found |

## 7. Known limitations

- The GameScript keeps one selected rail type and one selected road type for all companies (`Action.rail_type`, `Action.road_type`). If one company selects a type, the other companies also get it. The replay stays deterministic.
- A wake condition can wake an agent up to 7 days late (refer to 4.3).
- The socket gives the identity of the agent. Each agent must have access to its own socket only. Phase 4 isolates the agents.
- After a restart, the vehicles and stations for `vehicle_crashed` and `station_first_vehicle`, and the month for `new_month`, come from the restart time.
- Start cash is the same for all companies and is not more than the start loan.
- These parts have tests on the fake engine only: the straggler rule and the rebuild after a world fault. The process entry point (`python -m railhead.multi.coordinator`) and the control socket have no automatic test.
