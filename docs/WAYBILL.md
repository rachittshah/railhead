# Waybill: a shared world for several agents

This document describes Waybill: several agents play one OpenTTD world, each agent as one company, with one lockstep clock.
Sections 1 to 7 describe Phase 1: the log v3 format, the coordinator, the replay, and the verifier.
Sections 8 to 11 describe Phases 2 and 3: franchise regions, the contract market, the `rh` market commands, and the task tracks.
For the exchange records and rules, refer to [WAYBILL-EXCHANGE.md](WAYBILL-EXCHANGE.md).
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
| Verifier | `src/railhead/multi/verify.py` | Writes `scores.json` with the net worth (and, with a market, the score) of each company |
| Regions | `src/railhead/multi/regions.py` | Franchise regions: the footprint of each build action and the region check (pure Python) |
| Market | `src/railhead/multi/market.py` | The market driver: the exchange, the cargo monitors, the forced stops, and the market wake reasons (pure Python) |

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
| `ex` | `seq`, `ticks`, `company`, `x` | One exchange record `x` (section 9). `company` is `null` for a system record. |

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
| `tests/unit/test_multi_regions.py` | The footprint of every action in the whitelist, the region check, the regions in force at a tick, the split, and the rotation |
| `tests/unit/test_multi_market.py` | The monitor book, the tick records, the forced stops, the market wake reasons, the `ex` rules, and the command parsing |
| `tests/unit/test_multi_tracks.py` | The track sizes, the paired arms, and the rotation of region types across seeds |
| `tests/integration/test_waybill_market.py` | Regions and the market on the engine, both arms: a refused and a forged out-of-region build; a kept and a broken contract with real deliveries; the replay gives the same exchange state and scores; a forged delivery count is rejected; cheap talk moves no money; a tender opens, is bid, awarded and settled |
| `tests/integration/test_waybill_tracks.py` | The short track from one seed: paired arms and two rotations share one `start.sav`; a changed `regions.json` is tamper |
| `tests/integration/test_waybill_events.py` | A short game: `station_first_vehicle` wakes its company early, `rh` acts through `RAILHEAD_SOCKET`, the world steps on after a submit, the replay is valid, and a moved submit and a changed `start.sav` are found |

## 7. Known limitations

- The GameScript keeps one selected rail type and one selected road type for all companies (`Action.rail_type`, `Action.road_type`). If one company selects a type, the other companies also get it. The replay stays deterministic.
- A wake condition can wake an agent up to 7 days late (refer to 4.3).
- The socket gives the identity of the agent. Each agent must have access to its own socket only. Phase 4 isolates the agents.
- After a restart, the vehicles and stations for `vehicle_crashed` and `station_first_vehicle`, and the month for `new_month`, come from the restart time.
- Start cash in the game is the same for all companies and is not more than the start loan. Unequal starts use the ledger (section 9.1).
- These parts have tests on the fake engine only: the straggler rule and the rebuild after a world fault. The process entry point (`python -m railhead.multi.coordinator`) and the control socket have no automatic test.
- Footprints are conservative where the engine can add pieces next to the target: a depot or a stop includes its front tile (a drive-through stop also the back tile), and terraforming includes the 8 neighbours. A build near the edge of a region can thus be refused.
- A dock, a lock, and a tunnel get their extent from read-only `tile` queries at the same tick in the live world and in the replay. The tunnel end is the first tile uphill with the same base height, as `GSTunnel.GetOtherTunnelEnd` finds it.
- Vehicles, orders, and loans have no region check. Vehicles can use the roads and stations of other companies where the engine permits it. The action whitelist has no industry funding and no land purchase.
- A market wake (`offer_received`, `message_received`) occurs at the next step. When a waiter's condition is already true, that step is one tick long.
- In `cheap_talk`, tender awards and their settlement move no money (the exchange rules). Only `pay` and the start balances move money.
- An amendment that changes the cargo or the industry gets a new monitor from its signing tick. Deliveries before that tick do not count.
- Licence and loan contracts are not tradable yet (`kind_not_enabled`).

## 8. Franchise regions

### 8.1 The region map

Each task can have a `regions.json` file. It gives each company slot a list of rectangles. Both corners of a rectangle are part of it.

```json
{"v": 1, "map": [256, 256],
 "regions": {"0": [[1, 1, 127, 127]], "1": [[128, 1, 254, 127]]},
 "types": {"0": 2, "1": 3},
 "changes": [{"tick": 40000, "slot": 0, "rects": [[1, 1, 140, 127]]}]}
```

| Field | Description |
|---|---|
| `regions` | The rectangles `[x0, y0, x1, y1]` of each slot at the start |
| `types` | The region type of each slot (section 11). Optional. |
| `changes` | Optional. From `tick`, the slot has the rectangles `rects`. The last change at or before a tick is in force at that tick. |

`seal_task` records the hash of `regions.json` in `sealed.json`. The verifier gives `tamper = 1` if the file changed or is missing.

### 8.2 The check

A company can build or change infrastructure only on tiles in its own region.
The coordinator calculates the footprint of each action at the current tick, before the engine gets the action.
If a tile of the footprint is outside the region of the company, the coordinator refuses the action with `ERR_FRANCHISE`. The log does not get the action.
The replay calculates the footprint of each logged action again, with the regions in force at the tick of the action. A logged action outside the region rejects the log (`LogRejected`). The verifier then gives `rejected = 1`.

| Actions | Footprint |
|---|---|
| `rail_track`, `rail_remove`, `rail_signal`, `rail_remove_signal`, `rail_waypoint`, `water_canal`, `land_demolish` | The tile |
| `rail_line`, `road_line`, `road_one_way`, `road_remove`, `bridge_build` | The rectangle from the first tile to the last tile (a diagonal rail line: the full rectangle) |
| `rail_station`, `air_airport` | The station area (`axis=x`: `length` along x, `platforms` along y) or the airport area |
| `rail_depot`, `road_depot` | The tile and the front tile |
| `road_stop` | The tile and the front tile. A drive-through stop: also the back tile. |
| `water_ship_depot` | The tile and the next tile in +x or +y |
| `water_dock` | The tile and its water tile (from the `tile` query) |
| `water_lock` | The middle tile and its two neighbours along the slope |
| `bridge_tunnel` | The tiles from the start to the other end of the tunnel |
| `land_raise`, `land_lower` | The tile and its 8 neighbours (the corners are shared) |
| `land_level` | The rectangle and a border of one tile |
| `town_action` | The centre tile of the town (all town actions: advertising, road rebuild, statue, fund buildings, exclusive rights, bribe) |
| `rail_set_type`, `road_set_type`, `vehicle_*`, `order_*`, `company_loan`, `company_rename` | No check |

`rh info` gives the rectangles of the company in `region`. `rh market` also gives them.

### 8.3 Generation

`region_map_for` splits the map into one rectangle for each company.
It cuts the longer side of a rectangle so that each part gets industries in proportion to the number of regions in it. Then it does the same for each part.
The order of the cut gives the region types 0 to N-1. Slot s gets region type `(s + rotation) mod N`.

## 9. The market

### 9.1 Configuration

The market is in `task.json`, in `objective.market`. A task without it has no market.

| Field | Default | Description |
|---|---|---|
| `arm` | `enforced` | `enforced` or `cheap_talk` (refer to WAYBILL-EXCHANGE.md) |
| `seed` | 0 | The seed of the tender draws |
| `start_balances` | `{}` | The ledger money of each slot at the start (unequal starts). The `exchange` account pays it. |
| `credit_limit` | `null` | The lowest ledger balance for a voluntary debit (`null`: no limit) |
| `quarter_days` | 90 | The length of a tender quarter in game days |
| `tender` | `{}` | `TenderConfig` fields (quantity, price, penalty, bid period, delivery window) |

### 9.2 Records

The coordinator owns one exchange. Exchange company ids are the engine company ids as strings (`"3"`).
Each exchange record goes into the log as one `ex` record:

```json
{"company":3,"kind":"ex","seq":41,"ticks":2590,"x":{"company":"3","contract_id":"C1","expires_tick":4810,"kind":"supply","op":"offer","seq":5,"terms":{...},"tick":2590,"to":"5"}}
```

1. When the log starts, the coordinator writes the `init` record and the records of the start tick (the tenders of quarter 0).
2. A company command writes one `ex` record with that company. Its digest goes to the GameScript (`note`), as for a `meta` record. Thus a company record that is added, moved or changed changes all later step checksums.
3. After each `step` record, the coordinator writes the system records of the new tick: expiries, tender awards, activations, settlements, and then the tenders of a quarter that starts at this tick. These records have `company` `null`. No other record comes between the `step` record and them.

The replay makes its own exchange from the sealed configuration. It applies the company records, and it runs the same driver after each step. Each logged system record must be equal to the record that the driver makes, else the log is rejected. Thus a forged delivery count or a forged tender is rejected.
The recovery of the coordinator takes the exchange from the replay.

### 9.3 Deliveries

When a contract is signed (an accept, a tender award, or an amendment), the coordinator adds one engine cargo monitor for each (seller, cargo, industry) that the contract needs, if it does not have one. A subcontract seller of a tender is included.
The replay adds the monitors at the same records. The monitors are not in the checksum.
After each step, the coordinator reads the monitor totals. The deliveries in `[start, end]` are `total(end) - total(max(start, creation tick))`. A monitor counts nothing before it exists.

### 9.4 Forced stops and wakes

The world stops at each exchange deadline (offer expiry, window start, bid close, contract deadline) and at the start of each quarter whose tenders end by the horizon. The replay rejects a step that passes such a tick.

| Reason | When |
|---|---|
| `deadline` (forced) | A contract of the company reaches its deadline at this tick |
| `tender` (forced) | The tenders of a quarter open at this tick (all waiting companies) |
| `deadline` (`until=deadline`) | Any exchange deadline at this tick names the company (offer expiry, window start, bid close) |
| `offer_received` (`until=offer_received`) | Since the wait started: an offer to the company, or a counter or amendment from the other party of its contract |
| `message_received` (`until=message_received`) | Since the wait started: a message to the company |

The GameScript never gets the market wake conditions. The replay calculates the market reasons again and compares them with the `step` record.

### 9.5 Horizon and score

A deadline after the horizon is refused (`ERR_BAD_ARG`). Tenders that end after the horizon do not open.
The verifier runs the driver until the horizon, thus contracts settle also after all companies submit.
The score of a company is its net worth after the run-off plus its ledger balance. `scores.json` gives `ledger_balance` and `score` for each company, and `market` (the exchange state).

### 9.6 What agents see

Agents get only views and query results: `market`, `contracts`, `inbox`, `ledger`, and `view` (`public_view`). They never get raw exchange records.
`history` gives the records of the company of the socket and the `step` records. A `step` record shows only the wake of that company and no `snap`. `history` never gives system records.
`monitor_read` gives only the monitors of the company of the socket.

## 10. Commands

The agent socket has the operation `ex` with `m` (the operation) and `a` (the arguments).

| `rh` command | `m` | Turn needed | Description |
|---|---|---|---|
| `rh market` | `market` | No | The public order book, the open tenders (your bid only), and your region |
| `rh contracts` | `contracts` | No | Your contracts and the offers you can answer (`your_turn`) |
| `rh inbox` | `inbox` | No | The messages to you |
| `rh ledger` | `ledger` | No | `enforced`: all balances and transfers. `cheap_talk`: your own. |
| `rh offer ...` | `offer` | Yes | A supply offer. `to` is the other party, or no `to` for a public offer. |
| `rh counter contract_id=C ...` | `counter` | Yes | New terms. Fields that you do not give keep their value. |
| `rh accept contract_id=C` | `accept` | Yes | Sign the contract |
| `rh amend contract_id=C ...` | `amend` | Yes | Propose new terms for a signed contract |
| `rh withdraw contract_id=C` | `withdraw` | Yes | Withdraw an open offer |
| `rh tender bid tender_id=T price=N` | `tender_bid` | Yes | A sealed bid |
| `rh msg to=B text="..."` | `msg` | Yes | A private message |
| `rh pay to=B amount=N [memo="..."]` | `pay` | Yes | A ledger transfer |

A company is an id (`3`), a name (`Company B`), a letter (`B`), or `me`.
A tick field (`window_start`, `deadline`, `expires_tick`) is an absolute tick, or relative to now: `+30d` (days) or `+500t` (ticks).
The coordinator also examines that the cargo and the industry exist and that the deadline is not after the horizon.
An exchange error comes back as `ERR_<CODE>`, for example `ERR_NOT_YOUR_TURN` or `ERR_INSUFFICIENT_FUNDS`. Refer to WAYBILL-EXCHANGE.md for the codes.
`rh help market` gives the commands and the settlement rule.

## 11. Task tracks

| Track | Companies | Map | Horizon | Run-off |
|---|---|---|---|---|
| `short` | 4 | 256 x 256 | 730 days (2 game years) | 0 |
| `decade` | 8 | 512 x 512 | 3650 days (10 game years) | 365 days |

```
python -m railhead.multi.gen track <short|decade> <first_seed> <count> <out_dir> [--rotations cycle|all] [--arms enforced,cheap_talk] [--start-balances SLOT=POUNDS,...]
```

- The generator makes one world for each seed. Each (rotation, arm) of that seed gets one task directory with the same `start.sav`.
- The rotation changes only `regions.json`: slot s plays region type `(s + rotation) mod N`. The arm changes only `objective.market.arm`.
- `--rotations cycle` (the default) gives the seed number i of the list the rotation `i mod N`. Thus N consecutive seeds give each slot, and each model of a fixed roster, each region type one time. `--rotations all` gives each seed all N rotations.
- The arms are paired: for each seed and rotation there is one `enforced` task and one `cheap_talk` task.
- The task id is `waybill-<track>-s<seed>-r<rotation>-<arm>`. `objective` also has `track`, `seed`, `rotation`, `arm`, and `region_types`.
