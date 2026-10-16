# Railhead — design

> Models play OpenTTD **directly**: the action space is the player's own command set, issued for one
> company through the engine's normal validation. No advisor layer, no env-side autopilot.
> What makes it a frontier environment is not "OpenTTD in Gym" (nttd already exists; see
> docs/research/01 §0) but five **training-grade mechanisms** that only a deterministic, forkable
> economic simulator allows:

| # | Mechanism | One line | Closest prior (and the gap) |
|---|---|---|---|
| M1 | **Proof-of-play** | Reward = re-simulation of the agent's action log from a sealed start state in a clean process. The live game is never trusted. | τ²'s gold DB replays *reference* actions; FLE/nttd score the live state. Nobody replays the agent's own log. |
| M2 | **Run-off valuation** | After the agent stops, the verifier fast-forwards N game-years with the agent frozen; value = what the company the agent *built* is worth. | FLE/nttd/FM-Bench/VB2 score the end snapshot (end-of-horizon myopia, asset stripping). |
| M3 | **Priced, resampled forks** | `rh fork` runs the exact engine on a copy of the world, **reseeded** so it shows a plausible future, not the true one; fork-days are charged to a budget. | Free exact clones (2601.03905), learned models (Thinker). Nobody prices ground-truth simulation inside eval or resamples hidden randomness. |
| M4 | **Promise-keeping contracts** | Contracts with a bid quantity: payout `p·min(D,Q) − c·(Q−D)⁺` is a newsvendor = pinball proper score on the agent's own outcome distribution. Forecasts (`rh forecast`) resolve on a **frozen fork** right after commitment. | RLCR/Turtel calibrate on external facts; here the forecast target is the consequence of the agent's own plan, resolved exactly. |
| M5 | **Exact credit & branch points** | Per-turn checkpoints + state hashes; verifier can emit leave-one-turn-out counterfactual credit by replay; tasks can start at any checkpoint ("prefix tasks", SimOps rescue). | VinePPO/GiGPO need resettable states; demonstrated on math. Docker can't snapshot; OpenTTD saves in ~50 ms. |

Plus table stakes taken from prior failures (docs/research/01 §6, 02 §8): seed-addressable tasks,
disjoint train/test seeds + OOD climate split, open-loop baseline, IQM/bootstrap stats, typed errors,
≤4 KB observations, `valid=false` on infra failure, version pinning.

---

## 1. Engine

OpenTTD **15.3**, dedicated-only build, + `engine/patches/` (all verified by `tests/integration/test_engine_patches.py`):

| Patch | Why |
|---|---|
| 0001 `gamespeed <pct>` / `reseed <seed>` console cmds | Stock dedicated servers run at 1× (2 s/game-day). `gamespeed 0` ≈ 3–5k days/s on small maps. `reseed` = M3. |
| 0002 1 ms paused frames at speed 0 | Inputs land on paused frames; each costs 27 ms stock. |
| 0003 GS→admin JSON up to TCP_MTU + size guard | Stock: 1460 B, unchecked (assert/corrupt). |

Settings that matter (src/railhead/server.py `BASE_SETTINGS`): `command_pause_level=3`,
`threaded_saves=false`, `allow_insecure_admin_login=true` (loopback only), opcodes 250k,
`ai_in_multiplayer=true` (the model's company is created with `start_ai RailheadIdle`).

## 2. Runtime architecture

```
 agent (bash) ──► rh CLI / Python SDK ──unix socket──► railheadd (daemon, owns everything below)
                                                         │  World: OpenTTDServer + AdminClient + Bridge
                                                         │  ActionLog (JSONL, the trajectory = the proof)
                                                         │  ForkManager (save → boot fork server → reseed)
                                                         ▼
                                              openttd -D (patched) ── GS RailheadBridge (company mode)
```

* **Lockstep invariant:** the GS pauses the game at start and after every step; actions are applied
  only while paused; `ticks` (unpaused ticks, persisted) is the replay clock. Verified: 74·k ticks = k days.
* **Start state:** every episode (live, replay, fork) starts by *loading* a savegame (`start.sav`,
  company already created, paused). "All branches load" is the fork-fidelity rule (research/03 §4.5).
* **Bridge RPC** (`src/railhead/bridge.py` ⇄ `gamescript/game/RailheadBridge/`): `{id,m,a}` →
  `{t:r|p|ev}`; list replies stream rows; handlers are invoked directly (never `.call`, which breaks
  command suspension).

## 3. Action space (model plays directly)

Every action is a **player command for the bound company**, executed by the GS in `GSCompanyMode`.
Tiles are `x,y` (index = y·MapSizeX + x). All actions accept `dry_run` (GSTestMode → cost, no effect).
Canonical names (CLI `rh <group> <verb>`, SDK `w.<group>_<verb>()`, log `{"a":"<group>.<verb>"}`):

| Group | Verbs (args) |
|---|---|
| `rail` | `track(tile, track)`, `line(from, to, [start_dir_tile])` (drag, like the GUI), `remove(tile, track)`, `station(tile, axis, platforms, length, [join])`, `depot(tile, front)`, `signal(tile, front, type)`, `remove_signal(tile, front)`, `waypoint(tile, axis)`, `set_type(railtype)` |
| `road` | `line(from, to)` (drag), `one_way(from, to)`, `remove(from, to)`, `depot(tile, front)`, `stop(tile, front, kind=bus|truck, drive_through)`, `set_type(roadtype)` |
| `air` | `airport(tile, type)` |
| `water` | `dock(tile)`, `canal(tile)`, `lock(tile)`, `ship_depot(tile, toward)` |
| `bridge` | `build(type, start, end, vehicle=rail|road)` · `tunnel(start, vehicle)` |
| `land` | `raise(tile, corner)`, `lower(tile, corner)`, `level(from, to)`, `demolish(tile)` |
| `vehicle` | `buy(depot, engine, [cargo])`, `sell(v)`, `start(v)`, `stop(v)`, `depot(v)` (send), `refit(v, cargo)`, `clone(depot, v, share_orders)`, `skip_order(v, i)` |
| `order` | `append(v, tile, flags)`, `insert(v, i, tile, flags)`, `remove(v, i)`, `clear(v)`, `share(v, other)`; flags ∈ {`full_load`, `full_load_any`, `unload`, `transfer`, `no_load`, `no_unload`, `non_stop`, `via`, `go_depot`, `service`} |
| `company` | `loan(amount)`, `rename(name)` |
| `town` | `action(town, kind)` kind ∈ {advertise_small/medium/large, road_rebuild, statue, fund_buildings, exclusive_rights, bribe} |
| meta (not world-changing) | `wait(days|ticks, until=[events])`, `fork.*`, `forecast`, `promise`, `abstain(reason)`, `flag(note)`, `submit` |

Known gaps vs a human (GS API, research/03 §7): timetables, service intervals, semaphores/signal
conversion, force-proceed, road waypoints, give-money. Documented; a raw-command patch is future work.

## 4. Observation (pull-based, ≤4 KB per call)

`rh status` (≤1.5 KB dashboard: date, cash, loan, value, vehicles by state, alerts, contract progress) ·
`rh map [x y w h] [--layer terrain|infra|owner|height]` (ASCII + legend) · `rh tile x,y` ·
`rh towns|industries|stations|vehicles|engines|cargo [--near x,y] [--page n] [--fields …]` ·
`rh finance` · `rh events [--since t]` · `rh history` (own actions) · `rh sql "…"` (read-only SQLite
snapshot of the same tables). Errors are typed and name a fix (`ERR_LAND_SLOPED_WRONG: try land.level or a foundation-compatible tile`).

## 5. Time model

The world is frozen except inside `wait`. `rh wait --days N [--until vehicle_crashed,industry_close,cash_below:X,vehicle_stuck,...]`
returns early on a matching event (management by exception). Per-task caps: max game-days, max turns.
After the agent submits (or its time runs out) **the world continues without it** (M2 run-off).

## 6. Task families (each with generator, oracle, verifier)

| Family | Goal | Reward (∈[0,1], `valid`) | Oracle |
|---|---|---|---|
| `deliver` | Contract: ≥Q units of cargo C from industry A to B by date D, budget-limited; optional bid (M4) | `min(1, D/Q)` (+ promise term if bid) from GSCargoMonitor ledger in replay | scripted road/rail builder |
| `passengers` | Move ≥N passengers between towns T1→T2 by D | same, town delivery monitor | scripted bus/air service |
| `simops` | A healthy network with injected faults (broken track, missing depot, bad orders, stopped fleet, wrong refit, uncovered station) — restore it | `clip((K−K₀)/(K*−K₀))`, K = run-off deliveries; K₀ = do-nothing, K* = fault-free counterfactual | reverse the injected fault |
| `tycoon` | Open play for H years | `W/(W+W_base)`, W = run-off net worth (cash − loan after N frozen years), W_base = scripted baseline same seed | scripted baseline |
| `impossible` (mixed into deliver) | Certified-infeasible contract (Q > production upper bound) | `1` iff `rh abstain` with evidence, else 0 | abstain |

Splits: train / dev seeds disjoint; `ood` = arctic/tropic climates (cargo chains unseen in train).
Each task ships certificates: oracle score, do-nothing score, upper bound.

## 7. Verification & threat model

Verifier (`tests/test.sh` → `railhead.verify`): load sealed `start.sav` → replay `actions.jsonl` at
recorded ticks with the **whitelist** of §3 → continue to horizon → run-off → read GS ledger →
`reward.json {reward, valid, …components}` + `reward.txt`. Live checksums vs replay checksums are
compared; a mismatch is `valid=false` (infra) not 0.

| Attack | Why it fails |
|---|---|
| write reward file | verifier recomputes; Harbor uploads tests after the agent |
| rcon/admin cheats on the live server | not in the log ⇒ not in the replay; only self-deceiving |
| forge the log | any log that replays legally *is* play; whitelist rejects other commands |
| swap start.sav / binary / GS / python pkg | verifier checks sha256 against values sealed in `tests/` (shared mode) or runs in a fresh container (`environment_mode="separate"`) |
| run a private engine to plan for free (bypass M3 pricing) | eval track runs the agent as non-root; engine + saves are root-only. Training track: forks are free by declaration. |
| end-of-horizon asset stripping / loan games | M2 run-off scores the company after N frozen years |

## 8. Packaging

One image `ghcr.io/rachittshah/railhead:<ver>` (engine + scripts + `railhead` + all task start states).
Harbor task = `task.toml` (docker_image + healthcheck `railhead-up <task_id>`), `instruction.md`,
`environment/Dockerfile` (`FROM` the image + `ENV RAILHEAD_TASK`), `tests/` (sealed hashes + verifier),
`solution/solve.sh` (oracle). Canonical tier fits Tinker's envelope (bash only, ≤20 turns, 32K).
Hugging Face: `railhead-tasks` (metadata + `task_binary`) and `railhead-trajectories` (oracle runs:
action logs, per-turn hashes, counterfactual credits, rewards).
