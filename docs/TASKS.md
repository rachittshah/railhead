# Railhead task families

This document describes each task family: its objective, horizon, run-off, reward, certificates, and secrets.
The formulas come from `src/railhead/verify.py`, `src/railhead/rewards.py`, and `src/railhead/gen/`.
Some formulas in `DESIGN.md` are older than the code. If they are different, the code is correct.
The text follows ASD-STE100. Refer to [STE.md](STE.md).

## 1. Common properties

### 1.1 Task ID

The task ID has the format `<variant>-<split>-<seed>`, for example `deliver_bid-train-0042`.
The seed has four digits.
The variant is the generator family: `deliver`, `deliver_bid`, `impossible`, `simops`, or `tycoon`.
Four values make a task: the generator version, the variant, the split, and the seed.

### 1.2 Files of a task

| File | Contents | In the image | In Harbor `tests/` |
|---|---|---|---|
| `task.json` | The task specification without secrets | Yes | Yes |
| `start.sav` | The sealed start state | Yes | No (the verifier uses the copy in the image) |
| `secret.json` | Facts that only the verifier reads | No | Yes |
| `oracle.jsonl` | The action log of the oracle | No | No (it is in `solution/`) |
| `certificates.json` | `idle_reward`, `oracle_reward`, and `oracle_valid` | No | No |
| `sealed.json` | The hashes of `start.sav`, `task.json`, and `secret.json` | No | Yes |

The start state has the company of the agent, the objective monitors, and a paused world.

### 1.3 Task specification

| Field | Description |
|---|---|
| `task_id` | The task ID |
| `family` | The reward family: `deliver`, `simops`, or `tycoon` |
| `horizon_days` | The number of game days that the agent can play |
| `runoff_days` | The number of game days after the horizon |
| `limits` | `max_forks`, `max_fork_days`, and optionally `allow_exact_forks` |
| `objective` | The objective that the agent can read |
| `scenario` | The map settings and the generator values |
| `title`, `briefing` | The text that the agent reads |
| `split` | `train`, `dev`, or `ood` |

The `deliver_bid` and `impossible` variants use the `deliver` reward family.
The verifier reads `objective` and `secret.json` together.

### 1.4 Splits and maps

| Split | Climate | Purpose |
|---|---|---|
| `train` | Temperate | Training |
| `dev` | Temperate | Development and evaluation |
| `ood` | Arctic or tropic | Evaluation on cargo chains that the `train` split does not have |

The generator calculates these map settings from the seed:

| Setting | Values |
|---|---|
| Map size | 64, 128, or 256 tiles on each side (weights 1, 6, and 2) |
| Start year | 1950, 1960, 1975, 1990, or 2000 |
| Number of towns | Setting 1, 2, or 3 |
| Industry density | Setting 3, 4, or 5 |
| Terrain and water | Three terrain types and three water quantities |
| Maximum loan | 200 000, 300 000, or 500 000 |
| Interest | 2 %, 3 %, or 4 % |
| Vehicle breakdowns | Off or reduced |
| Economy | Smooth. Production changes slowly. |
| Disasters | Off |

### 1.5 Certificates

Refer to the flowchart in [DIAGRAMS.md, 4](DIAGRAMS.md#4-task-generation-and-certification).

For each task, the generator runs two logs through the verifier:

1. The do-nothing log. It has only a header.
2. The oracle log.

The generator writes the two rewards in `certificates.json`.
The generator deletes the task if the oracle replay is not valid.
The generator deletes the task if the oracle reward is not more than the do-nothing reward.
Because of this, each task that the generator keeps is solvable.

### 1.6 Why secrets are in `secret.json`

Some facts make a task easy if the agent can read them.
The generator writes these facts in `secret.json`, and never in `task.json`.
The `.dockerignore` file keeps `secret.json` out of the image.
Harbor uploads `tests/secret.json` only after the agent stops.

## 2. `deliver`

| Property | Value |
|---|---|
| Objective | Deliver at least `Q` units of cargo `C` to the industry `B` within the horizon. |
| Horizon | 300 game days |
| Run-off | 0 game days |
| Limits | 2 live forks, 180 fork-days |
| Monitor | `industry_delivery` of cargo `C` to industry `B` for the company of the agent |
| Oracle | The road oracle builds a truck route from a source industry to `B`. |

The agent can use any source industry.
The monitor counts only the cargo that the company of the agent delivers to industry `B`.
The briefing gives the cargo, the destination, a possible source, and the tile of each industry.

The reward is:

```
D = monitor total at the end of the horizon
reward = 0                     if the agent abstains
reward = min(1, D / Q)         else
```

The generator sets `Q` from the oracle result on each map:

1. The oracle plays the task.
2. The verifier calculates the oracle delivery `d`.
3. The generator rejects the seed if `d` is less than 40 or if the replay is not valid.
4. The generator sets `Q = max(20, round(0.6 * d, -1))`.

| Secret | Value | Reason |
|---|---|---|
| `feasible` | `true` | It tells the verifier that an abstain record gives reward 0. |
| `oracle_delivered` | `d` | It shows the exact result of the oracle. |

## 3. `deliver_bid`

The `deliver_bid` variant is a `deliver` task with a promise.

| Property | Value |
|---|---|
| Objective | The `deliver` objective. Also, promise a quantity in the first 30 game days. |
| Horizon | 300 game days |
| Run-off | 0 game days |
| Limits | 2 live forks, 180 fork-days |
| Objective fields | `bid = true`, `bid_scale = round(1.5 * d, -1)`, `bid_deadline_days = 30` |
| Oracle | The `deliver` oracle, with `rh promise quantity=d` at the start |

The agent promises with `rh promise quantity=<N>`.
Only a promise in the first 30 game days counts.
If there are many promises, the last one in that period counts.
The verifier limits `B` to the range 0 to `bid_scale`.

The reward is:

```
S = bid_scale
payout = min(D, B) - max(0, B - D)          # in the range -S to S
reward = 0                                  if the agent abstains
reward = clip(0.5 + payout / (2 * S))       else
```

If there is no valid promise, `B` is 0 and the reward is 0.5.
The expected payout is at its maximum when `B` is the median of the delivery distribution of the agent.
Because of this, the payout is a proper scoring rule on the beliefs of the agent about its own plan.
The verifier uses a linear map to the range 0 to 1.
A clip of the payout can break this property.

| Secret | Value | Reason |
|---|---|---|
| `feasible` | `true` | It tells the verifier that an abstain record gives reward 0. |
| `oracle_delivered` | `d` | It shows the exact result of the oracle. |

## 4. `impossible`

The `impossible` variant is a `deliver` contract that no company can complete.
The briefing is the same as a usual `deliver` briefing.
The agent must find the problem and abstain.

| Property | Value |
|---|---|
| Objective | The `deliver` objective with a quantity that is too large |
| Horizon | 300 game days |
| Run-off | 0 game days |
| Limits | 2 live forks, 180 fork-days |
| Oracle | One `abstain` record with the reason |

The generator sets the quantity from the total production `P` of cargo `C` on the map, in units each month:

```
months = ceil(300 / 30) = 10
Q = ceil(max(1000, 5 * P * months) / 1000) * 1000
```

The factor 5 gives a margin for production growth.
Because of this, no company can deliver `Q` units within the horizon.

The reward is:

```
reward = 1     if the action log contains an abstain record
reward = 0     else
```

| Secret | Value | Reason |
|---|---|---|
| `feasible` | `false` | If the agent can read it, the agent can abstain without analysis. |
| `map_production_per_month` | `P` | It shows the basis of the bound. |
| `upper_bound_basis` | A text | It tells how the generator calculated the bound. |

If the agent abstains on a feasible `deliver` or `deliver_bid` task, the reward is 0.
Because of this, an agent cannot receive a high reward if it abstains on all tasks.

## 5. `simops`

A `simops` task starts with a truck route that has one or two faults.
The agent must find the faults and repair the route.

| Property | Value |
|---|---|
| Objective | Repair the route so that it delivers cargo again. |
| Horizon | 60 game days |
| Run-off | 365 game days |
| Limits | 2 live forks, 120 fork-days |
| Monitor | `industry_delivery` of the route cargo to the destination industry |
| Oracle | The inverse of each fault, done with player actions |

The generator makes the start state in this sequence:

1. The road oracle builds a truck route. The generator does not record these actions.
2. The route operates for 120 game days.
3. The generator selects one fault (probability 0.6) or two faults.
4. The generator applies the faults with player actions.
5. The generator saves the broken world as `start.sav`.

| Fault | Change to the route | Oracle repair |
|---|---|---|
| `fleet_stopped` | All trucks stop. | Start each truck. |
| `orders_cleared` | All trucks lose their orders. | Give each truck its two orders again. |
| `road_gap` | The generator demolishes one road tile in the middle of the route. | Build the two road pieces again. |
| `pickup_removed` | The generator demolishes the pickup station. | Build the stop again and give each truck new orders. |

The reward is:

```
K  = cargo that the company delivers during the run-off
K0 = K for the do-nothing log
K* = K for the oracle log
reward = clip((K - K0) / (K* - K0))     if K* > K0
reward = 1 if K >= K* else 0            if K* <= K0
```

The generator keeps a seed only if `K*` is at least `max(40, 1.5 * K0 + 10)`.

| Secret | Value | Reason |
|---|---|---|
| `faults` | The list of faults | It is the answer to the diagnosis. |
| `k0` | `K0` | It shows the reward scale. |
| `kstar` | `K*` | It shows the reward scale and the result of the oracle. |

## 6. `tycoon`

A `tycoon` task is open play on a new map.

| Property | Value |
|---|---|
| Objective | Increase the net worth of the company. |
| Horizon | 365 game days |
| Run-off | 730 game days |
| Limits | 2 live forks, 365 fork-days |
| Baseline | The road oracle on the most profitable route that it finds |

The baseline selects a route of 25 to 60 tiles.
The route has the largest value of production multiplied by distance.
The baseline does not use oil wells, because their production can only decrease.
The generator rejects the seed if the baseline does not have more net worth than the do-nothing log.

The reward is:

```
W      = cash - loan after the run-off
W_idle = W for the do-nothing log
W_base = W for the baseline log
reward = clip(0.5 * (W - W_idle) / max(W_base - W_idle, 1))
```

A reward of 0 is equal to no play.
A reward of 0.5 is equal to the baseline.
A reward of 1 is two times the gain of the baseline.

The values `w_idle` and `w_base` are in `objective`, not in `secret.json`.
The briefing tells the agent the net worth of the baseline.
The `tycoon` family has no secrets.

The run-off prevents asset stripping and loan games.
Only profit that continues for 730 game days without the agent increases `W`.

## 7. `passengers`

The verifier calculates a reward for a `passengers` family with the `deliver` formula.
The objective uses `town_delivery` monitors.
There is no generator for this family at this time.

## 8. Summary

| Variant | Reward family | Horizon | Run-off | Fork-days | Secrets |
|---|---|---|---|---|---|
| `deliver` | `deliver` | 300 | 0 | 180 | `feasible`, `oracle_delivered` |
| `deliver_bid` | `deliver` | 300 | 0 | 180 | `feasible`, `oracle_delivered` |
| `impossible` | `deliver` | 300 | 0 | 180 | `feasible`, `map_production_per_month`, `upper_bound_basis` |
| `simops` | `simops` | 60 | 365 | 120 | `faults`, `k0`, `kstar` |
| `tycoon` | `tycoon` | 365 | 730 | 365 | None |
