# Railhead procedures

This document gives the procedures to build, install, test, and operate Railhead.
Do all procedures from the root directory of the repository, unless a step tells you a different directory.
The text follows ASD-STE100. Refer to [STE.md](STE.md).

## 1. Requirements

Make sure that you have these items:

| Item | Necessary for |
|---|---|
| Python 3.11 or later | All procedures |
| `uv` | Installation of the package |
| git, CMake, Ninja, a C++ compiler, curl, unzip | To build the engine |
| zlib, liblzma, and liblzo2 development libraries | To build the engine |
| Docker | The image and the Harbor procedures |
| Harbor 0.22 (`harbor` command) | The Harbor procedure |

On Debian or Ubuntu, this command installs the build tools that the CI workflow uses:

```bash
sudo apt-get install -y --no-install-recommends cmake ninja-build zlib1g-dev liblzma-dev liblzo2-dev unzip
```

## 2. Build the engine

The script `engine/build.sh` builds OpenTTD 15.3 with the Railhead patches.
It builds a dedicated server only, without a graphical interface.
It also downloads the OpenGFX base graphics.
A dedicated server cannot start without base graphics.

CAUTION: The script discards all local changes in the engine source tree before it applies the patches. Keep your engine changes as patch files in `engine/patches/`.

1. Run the build script:

   ```bash
   engine/build.sh
   ```

2. Make sure that the last line of the output starts with `engine ready:`.

The engine binary is then at `.cache/openttd-src/build/openttd`.
Railhead finds the binary at this location automatically.

The script uses these environment variables:

| Variable | Default | Description |
|---|---|---|
| `OPENTTD_VERSION` | `15.3` | The git tag of OpenTTD to build |
| `OPENGFX_VERSION` | `8.0` | The version of the OpenGFX base graphics |
| `RAILHEAD_ENGINE_SRC` | `.cache/openttd-src` | The directory of the OpenTTD source tree |
| `JOBS` | Number of processors | The number of parallel build jobs |
| `PREFIX` | Not set | If set, the script also copies the binary and its data to this directory |

To use a different engine binary, set `RAILHEAD_OPENTTD` to its path.
Railhead looks for the binary in this sequence:

1. The path in `RAILHEAD_OPENTTD`.
2. `/opt/railhead/engine/openttd` (the image layout).
3. `.cache/openttd-src/build/openttd` (the development layout).

## 3. Install the package

1. Make a virtual environment:

   ```bash
   uv venv --python 3.12 .venv
   ```

2. Install the package in editable mode with the test and dataset extras:

   ```bash
   uv pip install -e ".[dev,hub]"
   ```

3. Activate the virtual environment:

   ```bash
   source .venv/bin/activate
   ```

The package installs two commands:

| Command | User | Purpose |
|---|---|---|
| `rh` | The agent | Play the game: queries, actions, waits, forks, and meta decisions |
| `railhead` | The operator | Start and stop the daemon, play a recorded log, and export Harbor tasks |

The other procedures in this document use the activated virtual environment.

## 4. Run the tests

The tests are in `tests/unit/` and `tests/integration/`.
The marker `integration` identifies the tests that start real engine processes.

1. Run the unit tests. These tests do not use the engine:

   ```bash
   pytest -q -m "not integration"
   ```

2. Build the engine before you run the integration tests. Refer to section 2.

3. Run the integration tests:

   ```bash
   pytest -q -m integration
   ```

4. Run all tests and stop at the first failure, as the CI workflow does:

   ```bash
   pytest -q -x
   ```

The limit for each test is 300 seconds.
If the engine is not in the default location, set `RAILHEAD_OPENTTD` before step 3.

## 5. Generate tasks

The generator makes tasks from seeds.
Each task has a start state, an objective, an oracle log, and certificates.
The generator keeps a task only if the oracle replay is valid.
The oracle reward must also be more than the reward for no play.

The command has this format:

```bash
python -m railhead.gen.build <family> <split> <seed_from> <seed_to> --out <dir> --jobs <N>
```

| Argument | Values | Default |
|---|---|---|
| `<family>` | `deliver`, `deliver_bid`, `impossible`, `simops`, `tycoon` | Necessary |
| `<split>` | `train`, `dev`, `ood` | Necessary |
| `<seed_from>` | The first seed | Necessary |
| `<seed_to>` | The seed after the last seed | Necessary |
| `--out <dir>` | The catalog directory | `tasks` |
| `--jobs <N>` | The number of parallel processes | `4` |

CAUTION: Each seed starts one or more engine processes, and a large seed range takes a long time. Start with a small range.

1. Generate the `deliver` tasks for the training seeds 0 to 7:

   ```bash
   python -m railhead.gen.build deliver train 0 8 --out tasks --jobs 4
   ```

2. Read the output. The generator writes one line for each seed:

   ```
   deliver/train/3: ok deliver-train-0003
   deliver/train/5: rejected: oracle delivered too little (12) or invalid replay
   ```

3. Use the task IDs from the lines with `ok`.

A line with `rejected` tells you that the seed does not give a good task.
A line with `error` tells you that the generator stopped because of a problem.
The task ID has the format `<family>-<split>-<seed>`, with a seed of four digits.
For the contents of a task directory, refer to [TASKS.md](TASKS.md).

## 6. Start a task daemon

The daemon `railheadd` owns the world of one task.
The `railhead up` command starts the daemon if it does not run.
If the daemon runs for the same task, the command does nothing.

CAUTION: Use a new, empty state directory for each episode. If `run/actions.jsonl` is in the state directory, the daemon replays that log when it starts.

1. Set the catalog directory:

   ```bash
   export RAILHEAD_TASKS="$PWD/tasks"
   ```

2. Set a new state directory for this episode:

   ```bash
   export RAILHEAD_STATE="$HOME/.railhead/episodes/deliver-train-0003"
   ```

3. Set the task ID. The `rh` command uses this value to start the daemon when no daemon runs:

   ```bash
   export RAILHEAD_TASK=deliver-train-0003
   ```

4. Start the daemon:

   ```bash
   railhead up deliver-train-0003
   ```

5. Make sure that the output is `{"up": true, "pid": <number>}`.

6. Make sure that the daemon replies:

   ```bash
   railhead ping
   ```

If the daemon does not start, read `$RAILHEAD_STATE/daemon.log`.
If a daemon runs for a different task in the same state directory, `railhead up` stops with `ERR_TASK_MISMATCH`.

The daemon writes these files in the state directory:

| File or directory | Contents |
|---|---|
| `run/actions.jsonl` | The action log of the episode |
| `run/meta.json` | The start date and the end date of the horizon |
| `world/` | The instance directory of the live engine |
| `forks/` | The instance directories of the forks |
| `daemon.log` | The output of the daemon |
| `daemon.pid` | The process ID of the daemon |
| `children.json` | The process IDs of the engine processes |
| `rh.sock` | The unix socket of the daemon |

If the socket path has more than 100 characters, the daemon uses a short path in `/tmp` instead.

## 7. Play with `rh`

### 7.1 Command format

```bash
rh <command> [<key>=<value> ...] [--json]
```

- Write a tile as `x,y`.
- The CLI changes the value of `tile` and of each key that ends in `_tile` to a tile index.
- The CLI reads each value as JSON when it can. For example, `true`, `30`, and `'["full_load"]'` become a boolean, an integer, and a list.
- If a value is not JSON, the CLI sends it as a string.
- Add `--json` to receive compact JSON output.

Game time does not pass, except during `rh wait`.
The agent can use as much real time as necessary between commands.

### 7.2 Status and task information

| Command | Result |
|---|---|
| `rh status` | A short dashboard: date, money, objective progress, and alerts |
| `rh brief` | The objective, the horizon, the budgets, and the briefing of the task (JSON) |
| `rh info` | The same data as `rh brief` |
| `rh history n=30` | The last 30 records of the action log |
| `rh help` | The list of commands |

### 7.3 Queries

A query reads the world. It does not change the world.

| Command | Result |
|---|---|
| `rh towns` | The towns |
| `rh industries` | The industries, with the cargo that each industry makes and accepts |
| `rh stations` | The stations of the company |
| `rh vehicles` | The vehicles of the company |
| `rh engines vtype=road` | The vehicle models that the company can buy, for one vehicle type |
| `rh cargos` | The cargo types |
| `rh company` | The finances of the company |
| `rh companies` | All companies, with money, loan, and value |
| `rh depots` | The depots of the company |
| `rh orders` | The orders of the vehicles |
| `rh subsidies` | The subsidies |
| `rh catchment tile=12,40` | The cargo acceptance and the cargo production near a possible station site |
| `rh monitor_read` | The objective ledger |
| `rh map_info` | The date, the map size, the climate, and the number of towns and industries |
| `rh map x=0 y=0 w=40 h=24` | A text map of one area, with a legend |
| `rh tile tile=12,40` | One tile in detail, with the cargo acceptance near it |

### 7.4 Player actions

A player action has the format `rh <group> <verb> <key>=<value> ...`.
The CLI examines the arguments against the action registry before it sends the action.
The daemon records each action in the action log.
The daemon also records an action that the engine refuses.

| Group | Verbs |
|---|---|
| `rail` | `set_type`, `track`, `line`, `remove`, `station`, `depot`, `signal`, `remove_signal`, `waypoint` |
| `road` | `set_type`, `line`, `one_way`, `remove`, `depot`, `stop` |
| `air` | `airport` |
| `water` | `dock`, `canal`, `lock`, `ship_depot` |
| `bridge` | `build`, `tunnel` |
| `land` | `raise`, `lower`, `level`, `demolish` |
| `vehicle` | `buy`, `sell`, `start`, `stop`, `depot`, `refit`, `clone`, `skip_order` |
| `order` | `append`, `insert`, `remove`, `clear`, `share` |
| `company` | `loan`, `rename` |
| `town` | `action` |

The action registry in `src/railhead/actions.py` gives the arguments of each verb.
These examples build a truck route:

```bash
rh road line from_tile=10,4 to_tile=30,4
rh road depot tile=12,5 front_tile=12,4
rh road stop tile=20,4 front_tile=21,4 kind=truck
rh vehicle buy depot_tile=12,5 engine=116 cargo=1
rh order append vehicle=3 tile=20,4 flags='["full_load"]'
rh vehicle start vehicle=3
```

To receive the cost of an action without a change to the world, add `dry_run=true`:

```bash
rh road line from_tile=10,4 to_tile=30,4 dry_run=true
```

### 7.5 Game time

Game time passes only during a wait.

1. Wait for a number of game days:

   ```bash
   rh wait days=30
   ```

2. To stop the wait early on an event, add wake conditions:

   ```bash
   rh wait days=30 until=vehicle_crashed,cash_below:10000
   ```

You can also wait for a number of ticks with `ticks=<N>`. One game day is 74 ticks.

| Wake condition | The wait stops when |
|---|---|
| `vehicle_crashed` | A vehicle crashes. |
| `industry_open` | A new industry opens. |
| `industry_close` | An industry closes. |
| `company_bankrupt` | A company becomes bankrupt. |
| `company_in_trouble` | A company has financial problems. |
| `station_first_vehicle` | The first vehicle stops at a new station. |
| `cash_below:<X>` | The cash of the company is less than `X`. |
| `new_month` | A new game month starts. |

The daemon does not let a wait go past the horizon.
At the end of the horizon, the daemon refuses actions and waits with `ERR_HORIZON`.

### 7.6 Forks

A fork is a copy of the world in a different engine process.
The daemon reseeds each fork.
Because of this, a fork shows a possible future, not the true future.
The daemon does not record fork commands in the action log.
Fork commands have no effect on the reward.

1. Make a fork:

   ```bash
   rh fork create name=try1
   ```

2. Send a query to the fork:

   ```bash
   rh fork q name=try1 m=vehicles
   ```

3. Send an action to the fork. Use the GameScript method name for `m`, for example `road_line`:

   ```bash
   rh fork do name=try1 m=road_line from_tile=10,4 to_tile=30,4
   ```

4. Let game time pass in the fork:

   ```bash
   rh fork wait name=try1 days=90
   ```

5. Show the live forks and the fork-days that are left:

   ```bash
   rh fork list
   ```

6. Stop the fork:

   ```bash
   rh fork drop name=try1
   ```

Each `rh fork wait` decreases the fork-day budget by the game days that pass.
If the budget is too small for a wait, the daemon replies with `ERR_FORK_BUDGET`.
The task sets the maximum number of live forks. The default is 2.
`rh fork create name=<name> exact=true` makes a fork without a reseed.
The daemon makes an exact fork only if the task has `allow_exact_forks` in its limits.

### 7.7 Meta decisions

A meta decision does not change the world.
The daemon records each meta decision in the action log.

| Command | Description |
|---|---|
| `rh promise quantity=<N>` | Promise a delivery quantity (`deliver_bid` tasks). |
| `rh forecast <key>=<value> ...` | Record a forecast. At this time, the verifier does not use forecasts. |
| `rh flag note="<text>"` | Record a note. |
| `rh abstain reason="<text>"` | Tell the verifier that no company can complete the contract. |
| `rh submit` | Tell the daemon that you have completed your play. |

After `rh submit`, the daemon refuses player actions with `ERR_SUBMITTED`.
The world then continues without the agent.

### 7.8 Raw requests

Two commands send raw requests to the daemon:

| Command | Description |
|---|---|
| `rh q <query> <key>=<value> ...` | Send a query by its GameScript method name. |
| `rh do <method> <key>=<value> ...` | Send a player action by its GameScript method name, for example `road_line`. |

`rh do` does not examine the arguments against the action registry.
The daemon accepts only methods that are in the whitelist.
Use the `rh <group> <verb>` format when possible.

### 7.9 Error codes

If a command does not succeed, `rh` writes `<error code>: <message>` to standard error.
The exit code is 1 for a daemon error and 2 for an incorrect argument.

| Error code | Cause |
|---|---|
| `ERR_BAD_ARG` | An argument is unknown or incorrect, or a necessary argument is not given. |
| `ERR_NOT_A_QUERY` | The method is not a read-only query. |
| `ERR_NOT_A_PLAYER_ACTION` | The method is not in the whitelist. |
| `ERR_HORIZON` | The game date is at the end of the horizon. |
| `ERR_SUBMITTED` | The agent submitted before this action. |
| `ERR_NO_FORK` | There is no fork with this name. |
| `ERR_FORK_LIMIT` | The number of live forks is at the maximum. |
| `ERR_FORK_BUDGET` | The fork-day budget is too small. |
| `ERR_BUSY` | The world is in a step. |
| `ERR_UNKNOWN_OP`, `ERR_UNKNOWN_METHOD` | The daemon or the GameScript does not know the request. |
| `ERR_INTERNAL`, `ERR_DAEMON` | A problem in the daemon. Read `daemon.log`. |

The engine refuses an incorrect action with a typed error code, for example `ERR_AREA_NOT_CLEAR`.
The message of the error tells a possible correction.

## 8. Stop the daemon

1. Stop the daemon and its engine processes:

   ```bash
   railhead down
   ```

2. Make sure that the output is `{"down": true}`.

The action log stays in `$RAILHEAD_STATE/run/actions.jsonl`.

## 9. Play a recorded log

The `railhead play` command sends a recorded action log through the live daemon.
The daemon records the actions again, as it does for a player.
The Harbor oracle uses this command.

1. Start the daemon for the task. Refer to section 6.

2. Play the oracle log of the task:

   ```bash
   railhead play "$RAILHEAD_TASKS/deliver-train-0003/oracle.jsonl"
   ```

3. Make sure that the output is `{"played": <number>}`.

## 10. Calculate the reward of an action log

The verifier replays an action log from the sealed start state.
Then it continues to the horizon, runs the run-off, and calculates the reward.

CAUTION: Stop the daemon before you start the verifier. If the daemon runs, the action log can change during the replay.

1. Stop the daemon:

   ```bash
   railhead down
   ```

2. Run the verifier:

   ```bash
   python -m railhead.verify tasks/deliver-train-0003 tasks/deliver-train-0003/start.sav \
     "$RAILHEAD_STATE/run/actions.jsonl" out/deliver-train-0003
   ```

3. Read `out/deliver-train-0003/reward.json`.

The arguments are, in sequence:

1. The task directory. It must contain `task.json`. It can contain `secret.json` and `sealed.json`.
2. The start state.
3. The action log.
4. The output directory.

Add `--credit` to calculate the turn credit.
The verifier then writes `credit.json` in the output directory.
This option replays the log one time for each turn.
Because of this, the verifier takes much more time.

The verifier writes `reward.json` and `reward.txt`.
For the fields of `reward.json`, refer to [VERIFICATION.md](VERIFICATION.md).
If the task directory has no `sealed.json`, the verifier does not examine the hash of the start state.

## 11. Build the image

The image contains the engine, the scripts, the Python package, and the start states of all tasks.
One image serves all Harbor tasks.

CAUTION: The image copies the `tasks/` directory. Generate the tasks before you build the image.

1. Build the image:

   ```bash
   docker build -f docker/Dockerfile -t railhead:dev .
   ```

2. Make sure that the build completes without errors.

The file `.dockerignore` keeps these files out of the image:

- `oracle.jsonl`
- `certificates.json`
- `secret.json`
- `sealed.json`

The Dockerfile has two build arguments:

| Build argument | Default | Description |
|---|---|---|
| `DEBIAN` | `trixie` | The Debian release of the base image |
| `RAILHEAD_VERSION` | `0.1.0` | The version label in the image |

The workflow `.github/workflows/image.yml` builds the image for `linux/amd64` and `linux/arm64`.
It pushes the image to `ghcr.io/<owner>/railhead` when you push a tag that starts with `v`.

## 12. Export Harbor tasks

The `railhead export` command writes one Harbor task directory for each task in the catalog.
It also seals each task.

CAUTION: Run `railhead export` inside the image. The manifest contains the absolute paths and the hashes of the engine, the scripts, and the package. A manifest from a different installation is not correct for the image. The verifier then sets `tamper` to 1.

CAUTION: Mount the host `tasks/` directory on `/opt/railhead/tasks`. The image does not contain `secret.json` and `oracle.jsonl`. Without these files, the exported tests and solutions are not complete.

1. Make the output directory:

   ```bash
   mkdir -p harbor
   ```

2. Export all tasks:

   ```bash
   docker run --rm -e RAILHEAD_TASKS=/work/tasks -v "$PWD/tasks:/work/tasks:ro" \
     -v "$PWD/harbor:/out" railhead:dev railhead export /out --image railhead:dev
   ```

3. Make sure that the output is `{"exported": <number>}`.

Use the same `tasks/` directory that you used to build the image.

| Option | Default | Description |
|---|---|---|
| `<task_id> ...` | All tasks | Export only these tasks. |
| `--image <ref>` | `ghcr.io/rachittshah/railhead:0.1.0` | The image that `task.toml` names |
| `--version <v>` | `0.1.0` | The version in `task.toml` |

For the layout of a Harbor task, refer to [ARCHITECTURE.md](ARCHITECTURE.md).

## 13. Run a Harbor task on your computer

CAUTION: Use a job directory below `$HOME`. If Docker runs in colima, colima shares only `$HOME` with its virtual machine. A job directory in a different location causes `RewardFileNotFoundError`.

1. Run one task with the oracle agent:

   ```bash
   harbor run -p harbor/deliver-train-0003 -a oracle -o ~/.cache/harbor/jobs
   ```

2. Open the trial directory in `~/.cache/harbor/jobs/<job>/`.

3. Read `verifier/reward.json` in the trial directory.

4. Compare `reward` with `oracle_reward` in `tasks/deliver-train-0003/certificates.json`. The two values are usually equal.

To run all exported tasks, use `-p harbor`.
To run a model, replace `-a oracle` with an agent and add `-m <model>`.
To calculate the turn credit, add `--ve RAILHEAD_CREDIT=1`.

Harbor uses the image that `task.toml` names.
It does not build the image.
The healthcheck runs `railhead-up <task_id>` until the world answers.

## 14. Build the Hugging Face dataset

The module `railhead.hub` makes the dataset.
There is no `railhead` command for this procedure at this time.
The `tasks` configuration has one row for each task.
The `trajectories` configuration has one row for each oracle log.

1. Build the dataset. This step replays each oracle log:

   ```bash
   python -c "from pathlib import Path; from railhead.hub import build; print(build(Path('tasks'), Path('harbor'), Path('hf')))"
   ```

2. Set `HF_TOKEN` to a Hugging Face token that has write access:

   ```bash
   export HF_TOKEN=<your token>
   ```

3. Push the dataset:

   ```bash
   python -c "from pathlib import Path; from railhead.hub import push; print(push(Path('hf')))"
   ```

The default repository is `rachittshah/railhead`.

## 15. Environment variables

| Variable | Default | Description |
|---|---|---|
| `RAILHEAD_OPENTTD` | Search sequence in section 2 | The path of the engine binary |
| `RAILHEAD_SCRIPTS` | `src/railhead/scripts`, then `gamescript/` | The directory that contains `game/` and `ai/` |
| `RAILHEAD_TASKS` | `/opt/railhead/tasks`, then `tasks/` | The task catalog |
| `RAILHEAD_STATE` | `~/.railhead/state` | The state directory of the daemon |
| `RAILHEAD_TASK` | Not set | The task that `railhead up` and `rh` start |
| `RAILHEAD_CREDIT` | Not set | If set, the Harbor verifier calculates the turn credit |

The image sets `RAILHEAD_OPENTTD`, `RAILHEAD_SCRIPTS`, `RAILHEAD_TASKS`, and `RAILHEAD_STATE`.
Each Harbor task sets `RAILHEAD_TASK`.

## Record the LLM traces (OpenTelemetry)

Railhead has an OTLP/HTTP receiver that uses only the Python standard library.
It writes each export to a JSONL file.

1. Start the receiver:

   ```bash
   railhead otel serve --port 4318 --dir traces
   ```

2. Start a Claude Code player with telemetry. Set these variables in the environment of the process:

   ```bash
   export CLAUDE_CODE_ENABLE_TELEMETRY=1 OTEL_LOGS_EXPORTER=otlp OTEL_METRICS_EXPORTER=otlp
   export OTEL_TRACES_EXPORTER=otlp CLAUDE_CODE_ENHANCED_TELEMETRY_BETA=1
   export OTEL_EXPORTER_OTLP_PROTOCOL=http/json OTEL_EXPORTER_OTLP_ENDPOINT=http://127.0.0.1:4318
   export OTEL_LOG_USER_PROMPTS=1 OTEL_LOG_TOOL_DETAILS=1
   ```

3. Start a Codex player with telemetry. Use configuration overrides on the command line:

   ```bash
   codex exec -c 'otel.exporter={otlp-http={endpoint="http://127.0.0.1:4318/v1/logs",protocol="json"}}' \
     -c otel.log_user_prompt=true "<prompt>"
   ```

4. Show the requests, the tokens, and the cost for each model:

   ```bash
   railhead otel summary --dir traces
   ```

CAUTION: Inside a Docker container, `127.0.0.1` is the container. Use `http://host.docker.internal:4318`.

## Run an arena

An arena puts several players on the same task.
Each player has its own daemon and its own state directory.

1. Start one daemon for each player:

   ```bash
   for p in a b c; do
     RAILHEAD_TASKS="$PWD/tasks" RAILHEAD_STATE="$PWD/arena/$p/state" railhead up arena-dev-0005
   done
   ```

2. Give each player the same instruction. Set `RAILHEAD_STATE` to the directory of that player.
3. Show the scoreboard:

   ```bash
   railhead arena arena
   ```

4. Calculate the verified result of each player:

   ```bash
   python -m railhead.verify tasks/arena-dev-0005 tasks/arena-dev-0005/start.sav \
     arena/a/state/run/actions.jsonl arena/a/verify
   ```

## Watch a game

The viewer is a GUI client of OpenTTD 15.3 that joins as a spectator.
It does not change the world.

- To watch a live player, attach to its daemon:

  ```bash
  railhead view --attach arena/a/state
  ```

- To watch a recorded game, replay its action log:

  ```bash
  railhead view arena-dev-0005 --log arena/a/state/run/actions.jsonl --speed 1000
  ```
