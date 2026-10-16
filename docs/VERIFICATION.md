# Railhead verification and threat model

This document describes how the verifier calculates the reward and how it stops each known attack.
The text follows ASD-STE100. Refer to [STE.md](STE.md).

## 1. Principle

The reward is a function of two inputs only:

1. The sealed start state `start.sav`.
2. The action log of the agent.

The verifier does not read the state of the live game.
It replays the action log in a new engine process.
A player action that is not in the log has no effect on the reward.
This rule is proof-of-play.

## 2. Verifier procedure

In a Harbor trial, `tests/test.sh` runs after the agent stops.
It does these steps:

1. It examines the hash of each trusted file with `sha256sum -c /tests/sealed.sha256`. This step runs before any Python code. If a hash is different, it writes `tamper = 1` and reward 0, and stops.
2. It stops the live daemon with `railhead down`.
3. It copies the action log to the output directory. If the log is empty or is not there, it uses a log with only a header. This log is equal to no play.
4. It runs `python -I -m railhead.verify` on the task, the start state in the image, and the log.

The Python verifier then does these steps:

1. It compares the hash of `start.sav` with the hash in `sealed.json`. If they are different, it sets `tamper = 1` and reward 0.
2. It replays the log with the whitelist. If the replay rejects the log, it sets `rejected = 1` and reward 0.
3. If the replayed date is after the end of the horizon, it sets `rejected = 1` and reward 0.
4. It lets the world run to the end of the horizon without the agent.
5. It reads the objective ledger at the end of the horizon.
6. It lets the world run for the run-off days without actions from the agent.
7. It reads the ledger and the net worth after the run-off.
8. It calculates the family reward in the range 0 to 1.
9. It writes `reward.json` and `reward.txt`.

If the verifier has an internal error, it writes `valid = 0` and `error = 1`.

## 3. Fields of `reward.json`

| Field | Description |
|---|---|
| `reward` | The reward in the range 0 to 1 |
| `valid` | 1 if the replay agrees with the live game. 0 if there is an infrastructure problem. |
| `tamper` | 1 if a trusted file or the start state has a different hash |
| `rejected` | 1 if the log contains something that a player cannot do |
| `delivered` | The ledger total at the end of the horizon |
| `runoff_delivered` | The ledger increase during the run-off |
| `net_worth_horizon` | Cash minus loan at the end of the horizon |
| `net_worth` | Cash minus loan after the run-off |
| `abstained`, `submitted` | 1 if the log contains that meta decision |
| `quantity`, `bid` | The contract quantity and the promise (`deliver` family) |
| `acts`, `failed_acts`, `waits` | The number of actions, refused actions, and waits in the replay |
| `divergences` | The number of differences between the live record and the replay |
| `credit_turns` | The number of turns with credit (only with `--credit`) |
| `error` | 1 if the verifier had an internal error |

A trainer must drop a sample with `valid = 0`.
Such a sample shows an infrastructure problem, not a bad play.
The verifier marks each infrastructure problem that it finds with `valid = 0`.

## 4. Threat model

The agent has root access in the task container during its play.
The verifier runs in the same container after the agent stops (Harbor shared mode).
Because of this, the verifier examines each trusted file before it uses the file.

| Attack | Example | Protection | Status |
|---|---|---|---|
| Write the reward file | The agent writes `/logs/verifier/reward.json`. | `test.sh` writes the reward files after the agent stops. Harbor uploads `tests/` only after the agent stops. | Done |
| Change the live game through a privileged channel | The agent reads the admin password and sends RCON commands, cheat commands, or GameScript JSON. | These changes are not in the action log. The replay does not contain them. | Done |
| Make a false action log | The agent changes `actions.jsonl` on the disk. | The replay sends each action through the whitelist at its recorded tick. A log that replays correctly is legal play. | Done |
| Add a command that a player cannot use | The log contains a method that is not a player action. | The replay rejects the log. The verifier sets `rejected = 1` and reward 0. | Done |
| Move time backward or forward in the log | A record has a tick that is not the current tick. A wait does not move time forward. | The replay rejects the log. | Done |
| Play past the horizon | The agent waits or acts after the horizon. | The daemon refuses actions and waits after the horizon. The verifier rejects a log that ends after the horizon. | Done |
| Change a trusted file | The agent replaces `start.sav`, the engine binary, a script, or a Python module. | `sha256sum -c` examines the engine, all `.nut` files, and all `.py` files before Python runs. The verifier examines `start.sav` against `sealed.json`. | Done |
| Read the answer | The agent reads `secret.json`, `oracle.jsonl`, or `certificates.json`. | `.dockerignore` keeps these files out of the image. Harbor uploads `tests/` only after the agent stops. | Done, with gaps in section 5 |
| Use forks to see the true future | The agent forks the world to read future production. | The daemon reseeds each fork. Forks show a possible future only. | Done |
| Use forks without cost | The agent runs many long forks. | Each task has a fork-day budget and a maximum number of live forks. | Done |
| Run a private engine to plan without cost | The agent starts its own `openttd` process from a savegame. | The agent runs as a non-root user. Only root can read the engine and the savegames. | Planned |
| Asset stripping or loan games | The agent sells assets or takes a large loan at the end of the horizon. | The `tycoon` reward uses the net worth after 730 run-off days. The `simops` reward uses the run-off deliveries. | Done |
| Use profit statistics or book value | The agent moves cargo through transfers to show high vehicle profit. | The reward uses only ledger deliveries and cash minus loan. | Done |
| Abstain on all tasks | The agent abstains to avoid a low reward. | If the agent abstains on a feasible contract, the reward is 0. | Done |
| Promise a false quantity | The agent promises a small quantity to receive a safe payout. | The payout is a proper scoring rule. The best expected payout comes from the median of the true delivery distribution. | Done |
| Stop the daemon or the engine | The agent stops `railheadd` with `kill -9`. | The daemon recovers the world by replay of the log. The verifier does not use the live world. | Done |
| Results that are not deterministic | An engine or script difference gives a different replay. | The replay compares the checksums. A difference sets `valid = 0`. | Done |
| Infrastructure failure | The engine stops during the verifier. | The verifier writes `valid = 0` and `error = 1`. A trainer drops the sample. | Done |
| Changed settings | A different engine version or different settings change the result. | `engine/build.sh` pins OpenTTD 15.3. The start state contains the settings. The manifest pins the engine binary. | Done |

## 5. Known gaps

These gaps are in the code at this time:

| Gap | Effect |
|---|---|
| The agent runs as root, and `task.toml` does not set a non-root agent user. | The agent can start a private engine and plan without a fork-day cost. |
| The task ID and `scenario.variant` in `task.json` show the `impossible` variant. `task.json` is in the image. | The agent can find an `impossible` task without analysis of the map. |
| `bid_scale` is `1.5 * d`, and `Q` is approximately `0.6 * d`. Both are in `objective`. | The agent can calculate the oracle delivery `d` approximately. |
| The manifest does not contain the base operating system, the Python interpreter, or `sha256sum`. | In shared mode, a root agent can change these programs. The base image is the trust boundary. |
| The verifier does not use forecasts. | `rh forecast` has no effect on the reward. |

A verifier in a separate container (`environment_mode = "separate"`) removes the root-agent gaps.
For this mode, a verifier image with `/tests` in it is necessary.
Refer to `docs/research/04-harbor.md` for the details.

## 6. Turn credit

With `--credit`, the verifier calculates leave-one-turn-out credit:

1. It divides the `act` records into turns. A turn ends at the next `wait` or `meta` record.
2. For each turn, it writes a copy of the log without that turn.
3. It replays the copy and calculates the reward.
4. It calculates `credit = full reward - reward without the turn`.

The verifier writes the result in `credit.json`.
In Harbor, set `RAILHEAD_CREDIT=1` in the verifier environment to use this option.
