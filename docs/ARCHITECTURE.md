# Railhead architecture

This document describes the layers of Railhead, from the engine up to the packages.
The text follows ASD-STE100. Refer to [STE.md](STE.md).

## Overview

```
 agent (bash) --> rh CLI --unix socket--> railheadd (daemon)
                                            |  World: OpenTTDServer + AdminClient + Bridge
                                            |  ActionLog (JSONL)
                                            |  forks (save, load in a new server, reseed)
                                            v
                                 openttd -D (patched) -- GameScript RailheadBridge (company mode)

 verifier: start.sav + actions.jsonl --> replay in a new engine --> horizon --> run-off --> reward.json
```

| Layer | Location | Function |
|---|---|---|
| Engine and patches | `engine/` | A patched OpenTTD 15.3 dedicated server |
| Server manager | `src/railhead/server.py` | Starts one engine process in an isolated directory |
| Admin port protocol | `src/railhead/wire.py`, `admin_protocol.py`, `admin_client.py` | Sends and receives admin port packets |
| GameScript bridge | `gamescript/game/RailheadBridge/`, `src/railhead/bridge.py` | RPC from Python to the game, in lockstep |
| World | `src/railhead/world.py` | One live game with its company, log, savegames, and forks |
| Action registry | `src/railhead/actions.py` | The player actions and their arguments |
| Action log and replay | `src/railhead/actionlog.py`, `replay.py` | The record of play and its deterministic replay |
| Daemon and CLIs | `src/railhead/daemon.py`, `client.py`, `cli.py`, `admin_cli.py` | The interface for the agent and for the operator |
| Verifier and rewards | `src/railhead/verify.py`, `rewards.py`, `seal.py` | The reward calculation |
| Generator | `src/railhead/gen/` | Makes tasks from seeds |
| Oracle | `src/railhead/oracle/` | A scripted player that solves tasks |
| Packages | `src/railhead/harbor_export.py`, `hub.py`, `docker/` | Harbor tasks, the image, and the dataset |

## 1. Engine and patches

Railhead uses OpenTTD 15.3, built as a dedicated server only.
The script `engine/build.sh` applies three patches before it builds the engine.
The integration test `tests/integration/test_engine_patches.py` examines each patch.

| Patch | Change | Reason |
|---|---|---|
| `0001-console-gamespeed-and-reseed` | Adds the console commands `gamespeed <pct>` and `reseed <seed>`. | A stock dedicated server runs at normal speed only. `gamespeed 0` removes the speed limit. `reseed` gives each fork new random values. |
| `0002-fast-paused-frames` | At speed 0, a paused frame takes 1 ms. | Actions arrive while the game is paused. A stock paused frame takes a full tick of approximately 27 ms. |
| `0003-large-gamescript-json` | GameScript JSON to the admin port can have up to `TCP_MTU` bytes (32767). A message that is too large causes a script error. | A stock server limits this JSON to approximately 1460 bytes and does not examine the size. |

The server manager writes an `openttd.cfg` file for each engine process.
These settings are important:

| Setting | Value | Reason |
|---|---|---|
| `construction.command_pause_level` | `3` | Lets the company build while the game is paused. |
| `gui.threaded_saves` | `false` | Makes each save complete before the command returns. |
| `gui.autosave_interval` | `0` | Stops automatic saves. |
| `network.allow_insecure_admin_login` | `true` | Lets the admin client log in without encryption. The admin port is on the loopback address only. |
| `script.script_max_opcode_till_suspend` | `250000` | Gives the GameScript more operations before the engine suspends it. |
| `ai.ai_in_multiplayer` | `true` | Lets the idle AI `RailheadIdle` start the company of the agent. |
| `difficulty.max_no_competitors` | `0` | Removes AI competitors. |

Each engine process has its own instance directory, a random admin password, and two random loopback ports.
Because of this, many engine processes can run at the same time, for example a world and its forks.
The engine starts with `openttd -D 127.0.0.1:<port> -c <cfg> -x -X -G <seed>`, and with `-g <savegame>` to load a game.

## 2. Admin port protocol

Python connects to the admin port of each engine process.
Railhead uses version 3 of the admin port protocol.

| Item | Value |
|---|---|
| Packet format | `[uint16 LE total size][uint8 type][payload]`. The size counts the 3 header bytes. |
| Integers | Little-endian |
| Strings | UTF-8 with a NUL at the end |
| Maximum packet size in the codec | 65535 bytes |
| Maximum RCON command | 499 bytes |
| Maximum JSON from Python to the GameScript | 8999 bytes |
| Messages that Railhead uses | `JOIN`, `RCON`, `GAMESCRIPT`, `UPDATE_FREQUENCY`, `POLL`, `PING`, `QUIT` |

The admin client has a reader thread.
The thread sends RCON output to the RCON call, GameScript JSON to a queue, and all other messages to an event queue.
The decoder keeps unknown messages as `Unknown`.
It does not delete data.

## 3. GameScript bridge and the lockstep invariant

The GameScript `RailheadBridge` runs inside the engine.
It receives JSON requests through the admin port and sends JSON replies.
It sends each player command in company mode for the company of the agent.
The engine then uses its usual rules to examine the command.

| Message | Direction | Format |
|---|---|---|
| Request | Python to GameScript | `{"id": N, "m": method, "a": {...}}` |
| Reply | GameScript to Python | `{"t": "r", "id": N, "ok": true, "v": value}` |
| Error reply | GameScript to Python | `{"t": "r", "id": N, "ok": false, "code": "ERR_...", "msg": "..."}` |
| Rows | GameScript to Python | `{"t": "p", "id": N, "rows": [...]}`, before the final reply of a list |
| Event | GameScript to Python | `{"t": "ev", "k": kind, ...}` |

The GameScript sends list replies in batches of 16 rows.
If a batch is too large for one packet, the GameScript divides the batch into two halves.
The GameScript calls each handler directly, never through `.call`.
A command suspends the script, and the script cannot suspend through a native call frame.

### 3.1 The lockstep invariant

The lockstep invariant makes the tick of each action a function of the action log only:

1. The GameScript pauses the game when it starts.
2. The `step` request unpauses the game for a number of ticks or game days.
3. At the end of the step, the GameScript pauses the game again and replies.
4. The GameScript applies actions only while the game is paused.
5. During a step, the GameScript refuses actions with `ERR_BUSY`. It accepts queries.

The GameScript counts the unpaused ticks in the value `ticks`.
It keeps `ticks` in the savegame.
`ticks` is the clock of the replay, because frame numbers change on load and also during a pause.
One game day is 74 ticks.

### 3.2 Wake conditions

A step can stop early on a wake condition.
The GameScript examines events at each tick.
It examines `cash_below` and `new_month` one time each game day.

| Wake condition | Source |
|---|---|
| `vehicle_crashed` | Engine event |
| `industry_open`, `industry_close` | Engine event |
| `company_bankrupt`, `company_in_trouble` | Engine event |
| `station_first_vehicle` | Engine event |
| `cash_below:<X>` | Daily examination of the bank balance |
| `new_month` | Daily examination of the date |

### 3.3 Objective ledger

The GameScript keeps a list of cargo monitors.
Each monitor counts the cargo that one company delivers to, or picks up from, one industry or one town.
The GameScript reads the engine cargo monitors one time each game day and adds the amounts to totals.
The totals are in the savegame.
Because of this, the replay reads the same ledger as the live game.

| Monitor kind | Counts |
|---|---|
| `industry_delivery` | Cargo that the company delivers to an industry |
| `industry_pickup` | Cargo that the company picks up from an industry |
| `town_delivery` | Cargo that the company delivers to a town |
| `town_pickup` | Cargo that the company picks up from a town |

### 3.4 Checksum

The `checksum` request returns a deterministic checksum of the economic state.
The checksum uses these values:

- The date and `ticks`.
- The cash and the loan of each company.
- The position, the state, and the profit of each vehicle.
- The population of each town.
- The location of each industry.

The replay compares this checksum with the checksum in the action log.

### 3.5 The idle AI

`RailheadIdle` is an AI script that does nothing.
The World starts it with `start_ai RailheadIdle` to make the company of the agent.
Then the GameScript binds to that company with the `bind_company` request.

## 4. World

A `World` object is one live game.
It contains an engine process, an admin client, a GameScript bridge, and the bound company.

| Operation | Function |
|---|---|
| `World.create(workdir, config)` | Makes a new map from a seed and starts the company of the agent. |
| `World.load(workdir, savegame)` | Loads a savegame. Episodes, replays, and forks always start this way. |
| `call(method, **args)` | Sends one request through the GameScript bridge. |
| `state()` | Returns the date, `ticks`, the company, and the pause state. |
| `step(days=, ticks=, until=)` | Lets game time pass. It does not write to the action log. |
| `attach_log(log)` | Starts the action log. It writes a header with the start `ticks` and checksum. |
| `act(method, **args)` | Sends one player action and writes an `act` record. |
| `wait(days=, ticks=, until=)` | Lets game time pass and writes a `wait` record with a checksum. |
| `note(method, **args)` | Writes a `meta` record. |
| `save(dest)` | Writes a savegame and moves it to `dest`. |
| `fork(workdir, reseed=)` | Saves the world, loads the savegame in a new engine process, and reseeds it. |
| `close()` | Stops the engine process. |

After each load, the World sends `gamespeed 0` again, because the engine resets the speed on load.
If the engine does not have the patch, the World stops with an error.

## 5. Action registry

The action registry in `src/railhead/actions.py` is the single source of the action space.
The CLI, the function schemas, and the replay whitelist all come from it.

| Name | Function |
|---|---|
| `REGISTRY` | Gives the specification of each canonical name, for example `rail.line`. |
| `WHITELIST` | The set of GameScript methods that change the world, for example `rail_line`. |
| `validate(name, args, map_size_x)` | Returns the canonical arguments, or raises `ActionArgError`. |
| `to_gs(name, args, map_size_x)` | Returns the GameScript method and the canonical arguments. |
| `tool_schemas(style)` | Returns function-call schemas in the `anthropic` or `openai` style. |

The canonical arguments are the arguments that the daemon sends and that the action log records.
Tiles become tile indices, defaults become explicit, and unknown arguments cause an error.
Each action has the argument `dry_run`. A dry run returns the cost and does not change the world.

## 6. Action log

The action log is a JSONL file with one record on each line.
The log and the sealed start state are the proof of play.

| Kind | Fields | Description |
|---|---|---|
| `header` | `v`, `company`, `ticks`, `checksum` | The start of the log |
| `act` | `seq`, `ticks`, `m`, `a`, `ok`, `ret` or `code` | One player action and its result |
| `wait` | `seq`, `ticks`, `end_ticks`, `a`, `reason`, `checksum` | One wait, from `ticks` to `end_ticks` |
| `meta` | `seq`, `ticks`, `m`, `a` | One meta decision |

The log writes each record with sorted keys and flushes the file after each record.
The value `seq` increases by one for each record.

## 7. Replay

The replay loads the start state in a new engine process and sends the log again:

1. The replay sends each `act` record at its recorded tick, with its recorded arguments.
2. For each `wait` record, the replay runs exactly `end_ticks - ticks` ticks. Because of this, a wait that stopped early on an event stops at the same tick in the replay.
3. The replay ignores `header` and `meta` records.
4. After each wait, the replay compares the checksum with the recorded checksum.

| Condition | Result |
|---|---|
| A record has a tick that is not the current tick | `ReplayRejected` |
| An `act` record names a method that is not in the whitelist | `ReplayRejected` |
| A `wait` record does not move time forward | `ReplayRejected` |
| A record has an unknown kind | `ReplayRejected` |
| An action has a different result (`ok`) | A divergence |
| A checksum is different | A divergence |

A divergence is an infrastructure problem. The verifier sets `valid` to 0. It does not set the reward to 0 silently.

## 8. Daemon

The daemon `railheadd` owns the world of one task.
The agent can send requests to the world only through the unix socket of the daemon.
Each request is one JSON line, and each reply is one JSON line.

| Operation | Function | Recorded in the log |
|---|---|---|
| `ping` | Returns the task ID and the process ID. | No |
| `info` | Returns the task, the date, the horizon, the budgets, the objective, and the briefing. | No |
| `query` | Sends a read-only GameScript method. | No |
| `act` | Sends a player action that is in the whitelist. | Yes |
| `wait` | Lets game time pass, not past the horizon. | Yes |
| `meta` | Records a forecast, promise, abstain, flag, or submit. | Yes |
| `history` | Returns the last records of the log. | No |
| `fork_*` | Makes, uses, and stops forks. | No |
| `shutdown` | Stops the daemon and its engines. | No |

When the daemon starts, it loads the sealed `start.sav`.
If `run/actions.jsonl` is not empty, the daemon replays it to recover the world.
Then it continues to add records to the same log.
Because of this, the daemon recovers the exact world after a crash or a `kill -9`.

The function `ensure_up` starts the daemon if it does not run.
It uses a file lock.
Because of this, the image entry point, the Harbor healthcheck, and `rh` can all call it at the same time.

## 9. Forks

A fork is a copy of the world in a different engine process.

| Property | Value |
|---|---|
| Copy method | The daemon saves the live world, then loads the savegame in a new engine process. |
| Random values | The daemon reseeds the fork with a seed from the task ID and the fork number. |
| Exact fork | Only with `exact=true` and `allow_exact_forks` in the task limits |
| Maximum live forks | `max_forks` in the task limits. The default is 2. |
| Budget | `max_fork_days` in the task limits. Each fork wait decreases it by the days that pass. |
| Effect on the reward | None. The daemon does not record fork requests. |

The reseed changes the random values of industry production, breakdowns, and town growth.
Because of this, a fork shows a possible future and does not show the true future.

## 10. Verifier

The verifier `railhead.verify` calculates the reward from the start state and the action log only.
For the procedure and the threat model, refer to [VERIFICATION.md](VERIFICATION.md).

| Module | Function |
|---|---|
| `verify.py` | Replays the log, runs to the horizon and through the run-off, and writes `reward.json`. |
| `rewards.py` | The reward formulas. All rewards are in the range 0 to 1. |
| `seal.py` | Records the hash of each trusted file and examines the start state. |

## 11. Generator

The generator makes a task from four values: the generator version, the family, the split, and the seed.

| Module | Function |
|---|---|
| `gen/scenario.py` | Calculates the map settings from the seed. |
| `gen/families.py` | Selects a source industry, a destination industry, and a cargo. |
| `gen/build.py` | Makes `deliver`, `deliver_bid`, `impossible`, and `tycoon` tasks, and their certificates. |
| `gen/simops.py` | Makes `simops` tasks with faults. |

The scenario sets the map size, the climate, the start year, and the difficulty settings.
The `train` and `dev` splits use the temperate climate.
The `ood` split uses the arctic or the tropic climate.
Each split adds a different number to the seed.
Because of this, the same seed gives different maps in different splits.

For each task, the generator runs the oracle and the do-nothing log through the verifier.
It keeps the task only if the oracle replay is valid and the oracle reward is more than the do-nothing reward.
Refer to [TASKS.md](TASKS.md).

## 12. Oracle

The oracle `RoadOracle` is a scripted player.
It builds a truck route from a source industry to a destination industry.
It uses only player actions through `World.act`.
Because of this, its actions go into the action log.
The verifier can replay the oracle log like the log of an agent.

The oracle does these steps:

1. It takes the maximum loan.
2. It reads the tiles near the two industries.
3. It finds free, flat stop sites near each industry. The destination site must accept the cargo.
4. It finds a road path with the A* search in `oracle/pathing.py`. A turn costs more than a straight tile.
5. It builds the road as straight drags. If the engine refuses a drag, it marks those tiles and finds a new path.
6. It builds two truck stops and a depot.
7. It selects the road vehicle with the largest capacity multiplied by speed.
8. It buys 2 to 10 trucks, gives each truck its orders, and starts each truck.

The environment does not give this logic to the agent.
An agent must write its own logic.

## 13. Packages

### 13.1 Image

One image serves all Harbor tasks.

| Path | Contents |
|---|---|
| `/opt/railhead/engine/` | The engine binary and its data |
| `/opt/railhead/scripts/` | The GameScript and the idle AI |
| `/opt/railhead/venv/` | The Python package and the `rh` and `railhead` commands |
| `/opt/railhead/tasks/` | The `task.json` and `start.sav` of each task |
| `/var/lib/railhead/` | The state directory of the daemon |
| `/usr/local/bin/railhead-up` | Starts the daemon. The entry point and the healthcheck use it. |

The entry point starts `railhead-up` in the background if `RAILHEAD_TASK` is set.
The image stops with `SIGKILL`, because a trial does not keep data after it stops.

### 13.2 Harbor task

`railhead export` writes one directory for each task:

| Path | Contents |
|---|---|
| `task.toml` | The task name, metadata, timeouts, resources, image, `RAILHEAD_TASK`, and healthcheck |
| `instruction.md` | The briefing, the horizon, the budgets, and the rules of play |
| `environment/Dockerfile` | `FROM` the image, with `ENV RAILHEAD_TASK` |
| `tests/test.sh` | The verifier procedure |
| `tests/task.json` | The task specification |
| `tests/secret.json` | The secret facts of the task |
| `tests/sealed.json` | The hashes of `start.sav`, `task.json`, and `secret.json` |
| `tests/sealed.sha256` | The manifest of the engine, the scripts, and the package |
| `solution/solve.sh` | Starts the daemon and plays the oracle log with `railhead play` |
| `solution/oracle.jsonl` | The oracle log |

| `task.toml` value | Setting |
|---|---|
| Agent timeout | 2400 seconds, or `agent_timeout_sec` from the scenario |
| Verifier timeout | 900 seconds |
| CPUs and memory | 1 CPU and 2048 MB |
| Healthcheck | `/usr/local/bin/railhead-up <task_id>`, timeout 180 seconds, interval 5 seconds, 3 retries |

Harbor uploads `tests/` only after the agent stops.
The verifier runs in the same container as the agent (shared mode).

### 13.3 Dataset

The module `railhead.hub` writes Parquet files for the Hugging Face dataset.

| Configuration | One row for each | Main columns |
|---|---|---|
| `tasks` | Task | Metadata, certificates, `start_sav`, and `task_binary` (the Harbor task as a tar archive) |
| `trajectories` | Oracle log | `actions_jsonl`, `reward`, `components`, and `turn_credit` |

### 13.4 Continuous integration

| Workflow | Trigger | Steps |
|---|---|---|
| `ci.yml` | A push to `main`, a pull request, or a manual start | Builds the engine, installs the package, and runs all tests. |
| `image.yml` | A tag that starts with `v`, or a manual start | Builds the image for `linux/amd64` and `linux/arm64` and pushes it to `ghcr.io/<owner>/railhead`. |
