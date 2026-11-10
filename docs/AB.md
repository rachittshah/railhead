# A/B experiments

This document tells you how to compare two versions of the Railhead environment with the same models on the same maps.
The tool is `railhead ab`. The code is in `src/railhead/ab.py` and `src/railhead/behaviour.py`.

## What the tool does

An arm is one Railhead checkout.
The file `<checkout>/.venv/bin/railhead` of the arm starts the players and runs the verifier.
Thus the `rh` command that a player uses and the verifier that scores the game are the code of that arm.

A cell is one game: one arm, one task, one model, and one replicate.
The tool runs each cell, verifies each game with the verifier of its arm, and compares the arms.

| Command | What it does |
|---|---|
| `railhead ab plan <plan.json>` | Checks the plan. Shows the cells, the run order, and the code that each arm runs. Starts no game. |
| `railhead ab run <plan.json>` | Runs the cells. Resumes an interrupted cell. Never runs a finished cell again. |
| `railhead ab status <plan.json>` | Shows the state of each cell. |
| `railhead ab collect <plan.json>` | Verifies each finished game. Writes one export for each arm. |
| `railhead ab report <plan.json>` | Writes `report.md` and `report.json` with the paired differences. |
| `railhead ab behaviour <player_dir> --task <task_dir>` | Shows the behaviour metrics of one game. |

All commands take `--out DIR`. The default is `<plan directory>/out`.
Set `RAILHEAD_OPENTTD` to the engine binary before you run a command.

## Write a plan

Write the plan before the run. The hypothesis and the primary metric are a pre-registration.
The report shows them first. If the plan changes after the first run, `railhead ab run` stops.

```json
{
 "name": "pilot-water-rail",
 "hypothesis": "With water and rail fixed, the models spend more on non-air vehicles.",
 "primary_metric": "non_air_vehicle_spend_share",
 "arms": [
  {"name": "control", "checkout": "../railhead"},
  {"name": "treatment", "checkout": "../railhead-modes",
   "env": {"EXAMPLE_FLAG": "1"}, "task_map": {"arena-dev-0005": "arena-dev-0005-v2"}}
 ],
 "models": ["codex:gpt-6-astra", "codex:gpt-6-sol"],
 "tasks": ["arena-dev-0005", "arena-dev-0008", "arena-dev-0014"],
 "replicates": 1,
 "parallel": 3,
 "billing": "subscription",
 "seed": 20261110,
 "notes": "What this run can show and what it cannot show."
}
```

| Key | Meaning |
|---|---|
| `arms` | Two or more arms. The first arm is the baseline. The report shows each other arm minus the baseline. |
| `arms[].checkout` | The Railhead checkout of the arm. It must contain `.venv/bin/railhead`. |
| `arms[].env` | Optional. More environment variables for each process of the arm. |
| `arms[].tasks_dir` | Optional. The task catalog of the arm. The default is `<checkout>/tasks`. |
| `arms[].task_map` | Optional. Use it when the arm has a variant of a task under a different task ID. |
| `arms[].supervise_args` | Optional. More options for `arena-run --supervise`, for example `["--poll", "5"]`. Only checkouts that have this change accept them. |
| `models` | `vendor:model` strings, as `railhead arena-run` accepts them. The vendor is `claude`, `codex`, `opencode`, or `script`. |
| `tasks` | Task IDs. Each arm must have each task. |
| `replicates` | The number of games for each arm, task, and model. |
| `parallel` | The maximum number of games that run at the same time. |
| `billing` | A label that you set, for example `subscription` or `api`. Each export row contains it. |
| `seed` | Optional. The seed of the random run order. If you do not set it, the tool selects one and records it. |
| `primary_metric` | One metric name. `railhead ab plan x --metrics` shows the names. |
| `max_resumes` | Optional. The maximum number of resumes after provider errors. |
| `otel_dir` | Optional. The directory of an OTLP sink that the tool did not start. |

Run `railhead ab plan <plan.json>` before the run.
Make sure that `own_code` is `true` for each arm.
If `own_code` is `false`, the `.venv` of that arm imports Railhead from a different checkout.

## Do the A/A check first

Do an A/A check before each real experiment.
An A/A check has two arms that point to the same checkout. Its differences must be exactly zero.
The check finds problems in the harness, the verifier, and the statistics before you use model time.

1. Write a script for the scripted player. Write one `rh` command on each line. A line `@sleep N` stops for N seconds.

   ```text
   rh status
   rh company loan amount=100000
   rh wait days=60
   rh submit confirm=yes
   ```

2. Write a plan with two arms that have the same `checkout`, and the model `script:<path of the script>`.
3. Run `railhead ab run`, `railhead ab collect`, and `railhead ab report`.
4. Make sure that the report shows "TIED on every game metric".

The integration test `tests/integration/test_ab_e2e.py` does this check with two small tasks.
It also stops the harness during the games and makes sure that the next run resumes them.

## How the run works

The tool runs the cells in a random order from the recorded seed. Thus the arm and the time of day are not confounded.
Two cells with the same task and the same model never run at the same time.
This rule is necessary because the OTLP records identify a player only by its model name and its task.

For each cell, the tool does these steps:

1. It starts the game with `<arm>/.venv/bin/railhead arena-run <task> <cell> <model>`.
2. It watches the game with `arena-run --supervise`. The supervisor resumes the agent after provider errors.
3. When the game ends, it stops the daemon of the cell with `railhead down`.

If you stop `railhead ab run`, run it again. It does these steps for each cell:

- A finished cell: no action.
- A cell whose agent still runs: the tool watches it again.
- A cell whose agent stopped before the game ended: the tool resumes it with `arena-run --resume`.
- A cell that failed because of the infrastructure: no action. Add `--retry-failed` to run it again.

The tool cannot see if a Codex or opencode agent stopped by itself or because of the interruption.
In these two cases, it resumes the agent. For Claude and for the scripted player, it reads the transcript and does not resume a finished agent.

The tool starts `railhead otel serve` on port 4318 into `<out>/otel` if no program listens on that port.
It stops only a sink that it started.
If a different sink uses the port, set `otel_dir` to its directory.

The tool never deletes a file.

## What the logs record

| File | Content |
|---|---|
| `<out>/run.json` | The plan hash, the seed, the run order, and the time of the first run. |
| `<out>/plan.<hash>.json` | A copy of the plan at the first run. |
| `<out>/events.jsonl` | An append-only log of events: `started`, `resumed`, `attached`, `finished`, `failed`, `retry`, `verified`, and sink events. |
| `<out>/harness.jsonl` | The structured log of the tool (JSON lines). `RAILHEAD_LOG_LEVEL` sets the level. |
| `<cell>/manifest.json` | The manifest of the cell. See below. |
| `<cell>/launch.log`, `<cell>/supervise.log` | The output of `arena-run`. |
| `<cell>/<label>/` | The player: `state/`, `agent.log`, `verify/reward.json`. |
| `<cell>/behaviour.json` | All behaviour metrics of the game. |
| `<out>/arm-code/<arm>-<hash>.patch` | The uncommitted changes of a dirty arm checkout, once for each different hash. The file `.untracked.json` lists the untracked files and their hashes. |
| `<out>/exports/<arm>.json` | The rows of each arm in the arena row shape, with the arm, model, replicate, billing, plan hash, end state, and behaviour metrics. |

The manifest of each cell records these items:

- The plan hash, the arm, the task, the task ID in the arm, the model, the vendor, and the replicate.
- The harness CLI and its version, from `claude --version`, `codex --version`, or `opencode --version`. The value is empty if the CLI is not there.
- The git commit of the arm checkout, if its tree has changes, and a hash of the changes. The hash includes the files that git does not track. The tool reads these items again for each cell, because a checkout can change during a run. The git commands only read, and they do not lock the index.
- The Railhead package path and the Python version of the arm, and if the arm runs its own code.
- The path and the SHA-256 hash of the engine binary.
- The SHA-256 hashes of `task.json`, `start.sav`, `secret.json`, the task briefing, and the agent prompt of the arm.
- The start time, the end time, the time windows of the harness, the exit state, and the number of resumes.
- The `billing` value from the plan, and the OTLP sink that the cell used.

## How to read the report

The report shows the pre-registration first: the hypothesis, the primary metric, and the plan hash.
Then it shows a table for each arm against the baseline.

| Column | Meaning |
|---|---|
| n tasks | The number of tasks that both arms have a verified game for. |
| mean a, mean b | The mean of the metric over the paired games of each arm. |
| diff | The mean difference b - a. The tool first takes the mean over the models of a task. Then it takes the mean over the tasks. |
| 95% CI | A bootstrap interval. The bootstrap samples tasks again with replacement. |
| sign p | A two-sided sign test over the tasks. Zero differences are not counted. |
| tied | "yes" when the interval contains zero. |

The tool uses only verified games with `valid = 1`. The report lists the other games and the reasons.
If the verifier stopped with `error = 1`, `railhead ab collect` verifies the game again. It keeps the old verdict as `reward.error-<time>.json`.

Small samples cannot show an effect.
When there are fewer than six tasks, the report says that n is too small.
With n tasks, the smallest two-sided sign-test p is 2 / 2^n. With three tasks it is 0.25.
Use a pilot only to find problems and to calculate the size of the next experiment.

The report shows a warning when the task files of the arms are different.
It also shows a warning when the code of an arm changed during the run.
It shows the commit, the engine hash, and the prompt hash of each arm.
A table shows the categorical metrics for each arm, for example the mode of the first vehicle.

The behaviour metrics come from the action log. `src/railhead/behaviour.py` gives a definition of each metric.
Two metrics are approximations:

- `first_profitable_route_mode`: the action log has no profit for each route. The tool uses the first quarter with a positive operating result.
- `forks_created` and `fork_days_used`: forks are not in the action log. The tool reads them from the agent transcript.
