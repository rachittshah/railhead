# Railhead verification and threat model

This document describes how the verifier calculates the reward and how it stops each known attack.
It also states which attacks the code cannot stop.
The text follows ASD-STE100. Refer to [STE.md](STE.md).

## 1. Principle

The reward is a function of two inputs only:

1. The sealed start state `start.sav`.
2. The action log of the agent.

The verifier does not read the state of the live game.
It replays the action log in a new engine process.
A player action that is not in the log has no effect on the reward.
This rule is proof-of-play.

Proof-of-play proves that the log is a legal play from the start state.
It does not prove that the agent played the log in real time.
An agent that can write the log after the play can choose a legal log in hindsight.
Section 5 gives the limits that follow from this.

## 2. Verifier procedure

Refer to the flowchart in [DIAGRAMS.md, 3](DIAGRAMS.md#3-verifier).

In a Harbor trial, `tests/test.sh` runs after the agent stops.
It does these steps:

1. It examines the hash of each trusted file with `sha256sum -c /tests/sealed.sha256`. This step runs before any Python code. If a hash is different, it writes `tamper = 1` and reward 0, and stops.
2. It stops the live daemon with `railhead down`.
3. It copies the action log to the output directory. If the log is empty or is not there, it uses a log with only a header. This log is equal to no play.
4. It runs `python -I -m railhead.verify` on the task, the start state in the image, and the log.

The Python verifier then does these steps:

1. It compares the hash of `start.sav` with the hash in `sealed.json`. If they are different, it sets `tamper = 1` and reward 0.
2. It copies the log to its private work directory. All later steps read this copy only.
3. It replays the copy with the log rules in section 3. If the replay rejects the log, it sets `rejected = 1` and reward 0. The replay stops at the end of the horizon. It does not simulate a wait that ends after the horizon.
4. If the replay is different from a live checksum or a live action result, it replays the log a second time, up to the first difference only:
   - If the two replays agree, the engine is deterministic and the log is false. It sets `rejected = 1` and reward 0.
   - If the two replays do not agree, the engine is not deterministic. It sets `valid = 0`.
5. If the replayed date is after the end of the horizon, it sets `rejected = 1` and reward 0.
6. It lets the world run to the end of the horizon without the agent.
7. It reads the objective ledger at the end of the horizon.
8. It lets the world run for the run-off days without actions from the agent.
9. It reads the ledger and the net worth after the run-off.
10. It calculates the family reward in the range 0 to 1.
11. It writes `reward.json` and `reward.txt`.

If the verifier has an internal error, it writes `valid = 0` and `error = 1`.

The command line `python -m railhead.verify` accepts only a v2 log (the current version), unless you give `--min-log-version=1`.
The verifier sets the minimum version. The log cannot lower it.
Use `--min-log-version=1` only to verify again an old agent log, for example an Arena 2 game.
The Python function `verify` accepts v1 by default, because the generator and `railhead recheck` verify the v1 oracle logs in `tasks/`.

## 3. Log rules

The replay (`replay.py`) rejects a log (`rejected = 1`) for each of these conditions:

| Condition | Versions |
|---|---|
| The first record is not a header. | All |
| The header version is not an integer from 1 to 2, or is less than the minimum version of the verifier. A log with only a header is "no play" and is accepted at each version. | All |
| A `seq` value is not the previous value plus 1. The first record has `seq` 0 or 1. | All |
| A record, `meta` included, has a `ticks` value that is not the tick of the replayed world at that position. A v1 log can count ticks from its header tick: the replay then adds `start tick - header tick` to each record. | All |
| An `act` record names a method that is not in the whitelist. | All |
| An `act` record is on or after the horizon date. The daemon refuses such an action. | All |
| A `meta` record names a decision that is not `forecast`, `promise`, `abstain`, `flag`, or `submit`. | All |
| A `wait` record has no checksum, does not move time forward, or ends after the horizon. | All |
| The header tick is not the tick of the start state. | v2 |

The verifier uses the replayed ticks of the `meta` records, not the ticks in the file, for the bid deadline.
Thus a v1 log in a shifted tick frame cannot move a late promise before the deadline.

Data for the rules that apply to all versions: the 1,562 tracked oracle logs (all v1) contain no `wait` records, and the 308 `wait` records in the 11 Arena 2 agent logs (all v1) all have a checksum.
All of these logs agree with the tick and `seq` rules.
The 183 generator `abstain` oracles have a header at tick 0 while the start state is later. That is why v1 keeps the header tick frame.
Thus the rules apply to v1 also, and a log cannot avoid a rule if it gives an old version.

In a v2 log, the live world and the replay send each `meta` record to the GameScript as a digest (`note`).
The GameScript adds the digest to its checksum.
Thus each later wait checksum also includes the `meta` records before it.
A v1 replay does not send the digests, so the checksums of v1 logs do not change.

## 4. Fields of `reward.json`

| Field | Description |
|---|---|
| `reward` | The reward in the range 0 to 1 |
| `valid` | 0 if there is an infrastructure problem: an internal error, or two replays that do not agree. Else 1. |
| `tamper` | 1 if a trusted file or the start state has a different hash |
| `rejected` | 1 if the log contains something that a player cannot do, or a live record that a deterministic replay does not give |
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
A changed or deleted checksum does not give `valid = 0`. It gives `rejected = 1` and reward 0.

## 5. Threat model

The agent has a shell in the task container during its play, and it can be root.
The daemon, the engine, and the action log are in the same container.
The verifier runs in the same container after the agent stops (Harbor shared mode).
The agent wants a higher reward than its play earned, or it wants the trainer to drop a bad game.

Status values:

- **Closed**: the code stops the attack, also from a root agent. The test performs the attack.
- **Partly**: the code stops the simple form. A root agent with more work can still do it.
- **Open**: the code cannot stop it in a shared container. Section 6 gives the fix.

| Attack | Example | Protection | Status | Test |
|---|---|---|---|---|
| Write the reward file | The agent writes `/logs/verifier/reward.json`. | `test.sh` writes the reward files after the agent stops. Harbor uploads `tests/` only after the agent stops. | Closed | Harbor behaviour, refer to `docs/research/04-harbor.md` |
| Change a trusted file | The agent replaces `start.sav`, the engine binary, a script, or a Python module. | `sha256sum -c` examines the engine, all `.nut` files, and all `.py` files before Python runs. The verifier examines `start.sav` against `sealed.json`. | Partly: the base operating system, Python, and `sha256sum` are not in the manifest (section 6). Outside Harbor, a task without `sealed.json` is not examined: no tracked task has one (0 of 1,566), only the Harbor export writes it. | `test_tampered_start_state_scores_zero_and_is_flagged` |
| Add a command that a player cannot use | The log contains `monitor_add`. | The replay rejects the log. | Closed | `test_non_player_command_in_log_is_rejected` |
| Move time in the log | A record has a tick that is not the current tick, or a `seq` gap, or the log shifts its tick frame. | The replay examines the tick and the `seq` of each record, `meta` included. The bid deadline uses replayed ticks. | Closed | `test_attack_out_of_sequence_record_is_rejected`, `test_attack_tick_frame_shift_cannot_move_a_late_promise_before_the_deadline`, `test_attack_v2_log_cannot_use_a_shifted_tick_frame` |
| Hindsight promise: add | After the play, the agent adds `{"kind":"meta","m":"promise","ticks":0,...}` with the delivered quantity. | The record is not at the current tick. The replay rejects the log. A promise at the current tick is after the deadline and has no effect. | Closed | `test_attack_hindsight_promise_appended_at_tick_zero_is_rejected`, `test_attack_promise_appended_at_the_current_tick_after_the_deadline_is_ignored`, `test_attack_hindsight_promise_at_tick_zero_is_rejected` (engine) |
| Hindsight promise: insert | The agent puts a promise before the first wait and changes the `seq` values. | In a v2 log, the wait checksums include the meta digests. The changed checksum gives `rejected = 1`. | Partly: a root agent can calculate all checksums again with a private engine (section 6) | `test_attack_promise_inserted_before_the_wait_is_rejected`, `test_attack_hindsight_promise_inserted_before_the_waits_is_rejected` (engine) |
| Dropout: change a checksum | The agent changes one checksum to get `valid = 0`. | A second replay agrees with the first. The verifier sets `rejected = 1`, not `valid = 0`. The second replay stops at the first difference. | Closed | `test_attack_edit_one_checksum_is_rejected_not_dropped` (fake and engine), `test_attack_divergence_costs_at_most_a_partial_second_replay` |
| Dropout: delete the checksums | The agent deletes all checksums to stop the comparison. | Each wait must have a checksum. | Closed | `test_attack_strip_all_checksums_is_rejected`, `test_attack_strip_every_checksum_is_rejected` (engine) |
| Dropout: claim an old log version | The agent writes `"v": 1` to avoid the v2 rules. | The checksum rule applies to v1 also. The command line verifier needs v2. | Closed | `test_attack_old_log_version_does_not_skip_the_checksum_rule`, `test_attack_old_log_version_is_refused_when_the_verifier_needs_v2`, `test_attack_bad_header_version_is_rejected` |
| Dropout: stop the verifier | A root process that the agent left behind stops the verify engine, or fills the disk. | None in a shared container. The result is `valid = 0` and `error = 1`, or no reward file. | Open | None |
| Change the live game through a privileged channel | The agent reads the admin password and sends RCON commands, cheat commands, or GameScript JSON. | The change is not in the log. The next wait checksum shows it, and the verifier sets `rejected = 1`. | Closed | `test_attack_edit_one_checksum_is_rejected_not_dropped` (same detection) |
| Argument name collision | `rh do vehicle_buy timeout=0.001 ...` made the bridge stop waiting, so the action was applied but not logged. | The arguments go as one object from the daemon to the GameScript. The daemon examines the arguments of each action, also for `rh do`. | Closed | `test_attack_timeout_argument_cannot_shorten_the_action_timeout`, `test_attack_rh_do_with_a_timeout_argument_is_refused_before_sending`, `test_attack_timeout_argument_does_not_desync_the_live_world` (engine) |
| Applied action without a record | The engine applies an action but does not reply in time. | The world raises `WorldFault`. The daemon then rebuilds the world from the log and replies `ERR_INFRA`. The live world is again equal to the log. A long wait gets a time-out of 600 s plus 2 s for each game day. | Closed | `test_attack_applied_but_unanswered_action_is_a_world_fault_not_silence`, `test_world_fault_rebuilds_the_world_from_the_log`, `test_wait_that_times_out_is_a_world_fault_and_is_not_logged`, `test_long_wait_gets_a_timeout_that_grows_with_its_length` |
| Play past the horizon | The agent acts on the horizon date, or adds a wait of 100 years. | The daemon refuses. The replay rejects an action on or after the horizon date, and a wait that ends after the horizon, before it simulates the wait. | Closed | `test_attack_act_at_the_horizon_date_is_rejected`, `test_attack_hundred_year_wait_is_rejected_without_simulating_it` |
| Stop the verifier with a long log | The log contains millions of actions. | None. There is no limit on the number of records. A verify that takes more than the Harbor verifier time-out writes no reward file. | Open | None |
| Crash the GameScript | `rh wait until=cash_below:abc` left the script busy, and `until=cash_below` stopped it. | The daemon examines each wake condition. The GameScript reads all arguments before it starts a step. | Closed | `test_attack_bad_wake_condition_is_refused_at_the_daemon`, `test_attack_bad_wake_condition_does_not_break_the_gamescript` (engine) |
| Reset the fork budget | The agent stops the daemon to reset the fork days. | The daemon keeps the budget in `run/forks.json` and reads it when it starts. | Partly: a root agent can edit `run/forks.json` | `test_attack_restart_does_not_reset_the_fork_budget` |
| Use forks to see the true future | The agent forks the world to read future production. | The daemon reseeds each fork. Forks show a possible future only. | Closed | None |
| Run a private engine | The agent starts its own `openttd` from `start.sav` and replays its plans exactly. | None in a shared container with a root agent. | Open | None |
| Make a false action log | The agent writes a different log of legal play, with checksums from a private engine. | The replay makes sure that the log is legal play. It cannot know that the agent did not play it live. | Open | None |
| Keep the best part of the log | The agent cuts the log after the best point. Each start of a legal log is also a legal log. | None. | Open | None |
| Promise a false quantity | The agent promises a small quantity to receive a safe payout. | The payout is a proper scoring rule: the best expected payout comes from the median of the true delivery distribution. | Partly: with a private engine, the agent knows its delivery exactly | `tests/unit/test_rewards.py` |
| Asset stripping or loan games | The agent sells assets or takes a large loan at the end of the horizon. | The `tycoon` reward uses the net worth after the run-off. The `simops` reward uses the run-off deliveries. | Closed | `tests/unit/test_rewards.py` |
| Use profit statistics or book value | The agent moves cargo through transfers to show high vehicle profit. | The reward uses only ledger deliveries and cash minus loan. | Closed | None |
| Abstain on all tasks | The agent abstains to avoid a low reward. | If the agent abstains on a feasible contract, the reward is 0. | Closed | `tests/unit/test_rewards.py` |
| Read the answer | The agent reads `secret.json`, `oracle.jsonl`, or `certificates.json`. | `.dockerignore` keeps these files out of the image. Harbor uploads `tests/` only after the agent stops. | Partly: refer to section 6 | None |
| Stop the daemon or the engine | The agent stops `railheadd` with `kill -9`. | The daemon recovers the world by replay of the log. The verifier does not use the live world. | Closed | `test_daemon_recovers_exact_state_after_being_killed` |
| Results that are not deterministic | An engine or script difference gives a different replay. | Two replays that do not agree give `valid = 0`. | Closed | `test_engine_nondeterminism_is_still_an_infrastructure_fault` |
| Changed settings | A different engine version or different settings change the result. | `engine/build.sh` pins OpenTTD 15.3. The start state contains the settings. The manifest pins the engine binary. | Closed | None |

The GameScript also sends a short error reply, or `ERR_SEND_FAILED`, when an error reply is too large for one packet.
Before, Python waited until its time-out. No known request causes this, so no test performs it.

## 6. What the code cannot defend

When the agent is root in the same container as the daemon, the engine, and the log, the agent can do these things after all changes in this document:

1. Write any legal log. It can run a private engine from `start.sav`, try plans, and then write a log of the best plan, with correct checksums, meta digests, ticks, and `seq` values. The replay accepts the log. For a bid task, this also gives an exact promise.
2. Choose in hindsight. It can play, then cut the log at the best point, or insert a promise before the first wait and calculate all later checksums again.
3. Make the trainer drop a game. A process that it leaves behind can stop the verify engine or fill the disk. The result is `valid = 0` with `error = 1`, or no reward file. SkyRL then retries and masks the episode.
4. Edit `run/forks.json` to get more fork days, and edit the daemon itself during the play.

These fixes remove the attacks above:

| Fix | Effect |
|---|---|
| Run the daemon, the engine, the log, and `/var/lib/railhead` under a separate unix user, and run the agent as an unprivileged user. | The agent can send requests only through the socket. It cannot write the log, read `start.sav` or the engine to run a private engine, or stop processes of the daemon user. |
| Commit a hash chain of the log outside the container while the agent plays (for example, the daemon sends the hash of each record to the host). | The verifier compares the log with the committed head hash. A log that was changed or cut after the play does not agree. This works also when the agent is root. |
| Run the verifier in a separate container (`environment_mode = "separate"`). | Processes of the agent cannot stop the verifier. A verifier image with `/tests` in it is necessary. |

Harbor and a non-root agent: `docs/research/04-harbor.md` §7.2 shows that Harbor 0.22.0 supports `[agent].user`.
With `user = "agent"`, `agent.run()` and each command in it run as that user (verified with the oracle `solve.sh`).
claude-code, codex, and terminus-2 install with a non-root user (verified with `--install-only`).
The ENTRYPOINT and the healthcheck run as the image `USER`, which is root. Thus `railheadd` would run as root and the agent as `agent`.
UNCONFIRMED: that the agent then cannot stop or signal the root engine and cannot write root-owned files. This follows from Unix rules, but 04-harbor.md says that it was not tested.
At this time, the Railhead `task.toml` does not set `[agent].user`, and the image has no `agent` user. Thus the agent runs as root.

Other gaps:

| Gap | Effect |
|---|---|
| The task ID and `scenario.variant` in `task.json` show the `impossible` variant. `task.json` is in the image. | The agent can find an `impossible` task without analysis of the map. |
| `bid_scale` is `1.5 * d`, and `Q` is approximately `0.6 * d`. Both are in `objective`. | The agent can calculate the oracle delivery `d` approximately. |
| The manifest does not contain the base operating system, the Python interpreter, or `sha256sum`. | In shared mode, a root agent can change these programs. The base image is the trust boundary. |
| `seal.check_start` passes when `sealed.json` is not there. | Outside Harbor the start state is not examined. In Harbor, `sealed.json` is in `tests/`, which Harbor uploads after the agent stops. A fail-closed check would stop the generator, `railhead recheck`, and the arena verifier, because no tracked task has `sealed.json`. |
| The verifier does not use forecasts. | `rh forecast` has no effect on the reward. |
| An infrastructure fault that the daemon cannot repair ends the play early. | The log then holds a correct but short play, and the verifier scores it. The daemon does not write a fault record that gives `valid = 0`, because a root agent could add such a record to get any game dropped. |

## 7. Turn credit

With `--credit`, the verifier calculates leave-one-turn-out credit:

1. It divides the `act` records into turns. A turn ends at the next `wait` or `meta` record.
2. For each turn, it writes a copy of the log without that turn.
3. It replays the copy and calculates the reward.
4. It calculates `credit = full reward - reward without the turn`.

The verifier writes the result in `credit.json`.
In Harbor, set `RAILHEAD_CREDIT=1` in the verifier environment to use this option.
