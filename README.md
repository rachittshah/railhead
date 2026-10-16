# Railhead

Railhead is a reinforcement learning (RL) environment.
In Railhead, a language model plays the transport game OpenTTD directly.
The model controls one company with the same commands that a human player uses.
Railhead does not add a program that plays for the model.

The documents of this repository follow ASD-STE100 Simplified Technical English, Issue 9.
Refer to [docs/STE.md](docs/STE.md) for the rules and for the technical names.

## Description

The world is a patched OpenTTD 15.3 dedicated server.
A GameScript in the server receives requests through the admin port.
The GameScript sends each player command for the company of the agent.
The engine uses its usual rules to examine each command.

A daemon (`railheadd`) owns the world of one task.
The agent sends commands to the daemon with the `rh` CLI in a shell.
The daemon records each action, each wait, and each meta decision in the action log.
The verifier calculates the reward from the action log.
The verifier does not use the state of the live game.

```
 agent (bash) --> rh CLI --unix socket--> railheadd (daemon)
                                            |  World: engine process + admin port + GameScript bridge
                                            |  action log (JSONL)
                                            |  forks (copies of the world, reseeded)
                                            v
                                 openttd -D (patched) -- GameScript RailheadBridge
```

## The five mechanisms

Railhead has five mechanisms.
Each mechanism uses a property of a deterministic economic simulation that you can save, load, and fork.

### M1: Proof-of-play

The reward comes only from a replay of the action log of the agent.
The replay starts from the sealed start state in a new engine process.
A change to the live game that is not in the action log has no effect on the reward.
Refer to [docs/VERIFICATION.md](docs/VERIFICATION.md).

### M2: Run-off valuation

After the agent stops, the world continues without the agent.
This period is the run-off.
In a `tycoon` task, the verifier calculates the net worth of the company after the run-off.
In a `simops` task, the verifier counts the deliveries during the run-off.
Asset stripping at the end of the horizon does not increase these values.

### M3: Priced, resampled forks

The agent can make a fork of the world with `rh fork create`.
A fork is a copy of the world in a different engine process.
The daemon reseeds the random generator of each fork.
Because of this, a fork shows a possible future, not the true future.

Each game day in a fork decreases the fork-day budget of the task.
Actions in a fork are not in the action log.
They have no effect on the reward.

### M4: Promise-keeping contracts

In a `deliver_bid` task, the agent promises a delivery quantity `B` with `rh promise`.
For a delivery `D`, the payout is `min(D, B) - max(0, B - D)`.
The expected payout is at its maximum when `B` is the median of the delivery distribution of the agent.
Because of this, the payout is a proper scoring rule for the beliefs of the agent about its own plan.
The verifier changes the payout to the range 0 to 1 with a linear function.
The daemon also records forecasts (`rh forecast`).
At this time, the verifier does not use forecasts to calculate the reward.

### M5: Exact credit and branch points

The action log records a checksum of the world after each wait.
With the `--credit` option, the verifier calculates leave-one-turn-out credit by replay.
For each turn, it replays the log without that turn and compares the two rewards.
A task can start from any savegame.
The `simops` family uses this property: its start state is a transport network with faults.

## Quick start

Before you start, make sure that you have these items:

- Python 3.11 or later, and `uv`.
- git, CMake, Ninja, a C++ compiler, curl, and unzip.
- The development libraries for zlib, liblzma, and liblzo2.
- Docker and Harbor 0.22. These are necessary only for steps 8 to 10.

Do these steps from the root directory of the repository:

1. Build the patched engine:

   ```bash
   engine/build.sh
   ```

2. Make a virtual environment:

   ```bash
   uv venv --python 3.12 .venv
   ```

3. Install the package with the test and dataset extras:

   ```bash
   uv pip install -e ".[dev,hub]"
   ```

4. Activate the virtual environment:

   ```bash
   source .venv/bin/activate
   ```

5. Run the unit tests:

   ```bash
   pytest -q -m "not integration"
   ```

6. Run the integration tests. These tests start real engine processes:

   ```bash
   pytest -q -m integration
   ```

7. Generate the `deliver` tasks for the training seeds 0 to 7:

   ```bash
   python -m railhead.gen.build deliver train 0 8 --out tasks --jobs 4
   ```

8. Build the environment image:

   ```bash
   docker build -f docker/Dockerfile -t railhead:dev .
   ```

9. Export the Harbor tasks inside the image:

   ```bash
   mkdir -p harbor
   docker run --rm -e RAILHEAD_TASKS=/work/tasks -v "$PWD/tasks:/work/tasks:ro" \
     -v "$PWD/harbor:/out" railhead:dev railhead export /out --image railhead:dev
   ```

10. Run one task with the oracle agent:

    ```bash
    harbor run -p harbor/deliver-train-0000 -a oracle -o ~/.cache/harbor/jobs
    ```

In step 7, the generator rejects some seeds.
In step 10, use a task ID that the generator showed with `ok`.
The reward is in the file `verifier/reward.json` of the trial directory.
For the full procedures and the cautions, refer to [docs/USAGE.md](docs/USAGE.md).

## Task families

| Family | Objective | Horizon | Run-off | Reward |
|---|---|---|---|---|
| `deliver` | Deliver `Q` units of a cargo to one industry. | 300 days | 0 days | `min(1, D / Q)` |
| `deliver_bid` | The `deliver` objective, plus a promise in the first 30 days. | 300 days | 0 days | Linear map of `min(D, B) - max(0, B - D)` to 0 to 1 |
| `impossible` | A `deliver` contract that no company can complete. | 300 days | 0 days | 1 if the agent abstains, else 0 |
| `simops` | Repair a truck route that has one or two faults. | 60 days | 365 days | `clip((K - K0) / (K* - K0))` |
| `tycoon` | Open play. Increase the net worth of the company. | 365 days | 730 days | `clip(0.5 * (W - W_idle) / max(W_base - W_idle, 1))` |

The symbols in the table are:

- `D` is the cargo that the company delivers by the end of the horizon.
- `B` is the last promise in the first 30 game days.
- `K` is the cargo that the company delivers during the run-off.
- `K0` is `K` when the agent does nothing. `K*` is `K` after the oracle repair.
- `W` is the net worth after the run-off. `W_idle` and `W_base` are the values for no play and for the baseline.

The verifier also has a reward for a `passengers` family.
There is no generator for this family at this time.
Refer to [docs/TASKS.md](docs/TASKS.md) for the details of each family.

Each family has three splits:

| Split | Climate | Purpose |
|---|---|---|
| `train` | Temperate | Training seeds |
| `dev` | Temperate | Development seeds, different from the training seeds |
| `ood` | Arctic or tropic | Seeds with cargo chains that are not in the `train` split |

## Repository contents

| Directory | Contents |
|---|---|
| `engine/` | The script `build.sh` and three patches for OpenTTD 15.3 |
| `gamescript/` | The GameScript `RailheadBridge` and the idle AI `RailheadIdle` |
| `src/railhead/` | The Python package: daemon, CLIs, replay, verifier, rewards, and Harbor export |
| `src/railhead/gen/` | The task generators |
| `src/railhead/oracle/` | The scripted oracle player |
| `docker/` | The Dockerfile of the environment image and its start scripts |
| `tests/unit/` | Unit tests |
| `tests/integration/` | Integration tests |
| `.github/workflows/` | The CI workflow and the image workflow |
| `docs/` | The documents |

## Documents

| Document | Contents |
|---|---|
| [docs/USAGE.md](docs/USAGE.md) | Procedures: build, install, test, generate, play, calculate rewards, export, and run |
| [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) | Description of each layer of the system |
| [docs/TASKS.md](docs/TASKS.md) | The task families, their rewards, and their secrets |
| [docs/VERIFICATION.md](docs/VERIFICATION.md) | The threat model and the protection against each attack |
| [docs/STE.md](docs/STE.md) | The STE rules, technical names, and technical verbs of these documents |
| [docs/DESIGN.md](docs/DESIGN.md) | The design notes (not in STE) |
| `docs/research/` | The research notes (not in STE) |

## License

The Python package has the MIT license.
OpenTTD has the GPL-2.0 license.
The environment image contains both.
