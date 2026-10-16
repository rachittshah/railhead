# 04 — Harbor 0.22.0: task format, runtime, publishing, RL hooks

Research date: 2026-10-16. Ground truth is the installed Harbor 0.22.0 at
`~/.local/share/uv/tools/harbor/lib/python3.12/site-packages/harbor/` (abbreviated `H/` below).
Web sources were used only where the installed source does not reach: Hub docs, cookbook, and trainer repos.

## TL;DR for Railhead

| Question | Answer (verified) |
|---|---|
| Is CMD overridden? | **Yes.** `main` runs `command: ["sh","-c","sleep infinity"]`. **ENTRYPOINT is kept.** (`H/environments/docker/docker-compose-build.yaml:1-6`) |
| Can a service started at container boot stay alive through agent + verifier? | **Yes, verified.** An ENTRYPOINT-launched server kept the same PID from boot through the verifier phase. |
| Is the verifier in the same container, after the agent? | **Yes, by default** (`[verifier].environment_mode` resolves to `shared`). `test.sh` runs via `exec` in the live `main` container before teardown (`H/trial/single_step.py:38-55`). |
| Sidecar containers? | **Yes.** `environment/docker-compose.yaml` is merged on top of Harbor's base compose. Sidecar CMDs are *not* overridden. Verified: sidecar in-memory state survived from agent to verifier. |
| Reward file | `/logs/verifier/reward.json` (flat `{str: number}`, multiple metrics OK) takes precedence over `/logs/verifier/reward.txt` (single float). The test.sh exit code is ignored. |
| Publish | `harbor publish` uploads a `dist.tar.gz` of task files to Supabase storage, plus registry metadata. **No image is built or pushed.** Runners build from `environment/Dockerfile` or pull `[environment].docker_image`. |
| Auth | GitHub OAuth via browser (localhost:19284 callback) or `--no-browser` + `--callback-url`. The result is a long-lived personal API key `sk-harbor-…` stored in `~/.harbor/credentials.json`. **`HARBOR_API_KEY` env var overrides it** (CI path). |
| RL | Programmatic `Trial.create(TrialConfig)` -> `TrialResult.verifier_result.rewards` plus `agent_result.rollout_details` (token ids and logprobs from terminus-2). SkyRL uses exactly this and reads `rewards["reward"]`. |

**Local gotcha (colima):** colima's default `mounts: []` shares only `$HOME` with the VM.
Harbor bind-mounts `<jobs_dir>/<trial>/{agent,verifier,artifacts}` into the container as `/logs/*`.
A jobs dir under `/private/tmp` (or anywhere outside `$HOME`) mounts as an empty VM directory, so the trial fails with `RewardFileNotFoundError`.
**Always use `-o` under `$HOME`.** This was reproduced on the first toy run and fixed by `-o ~/.cache/harbor/...`.

---

## 1. Task format

### 1.1 Directory layout (`H/models/task/paths.py:11-29`, packager `H/publisher/packager.py:26-71`)

```
my-task/
├── task.toml              # required
├── instruction.md         # required for single-step tasks (piped to the agent verbatim)
├── README.md              # optional, published
├── trajectory.json        # optional ATIF prior context, seeded before the agent runs
├── .gitignore             # optional, used as the publish ignore list
├── environment/           # required dir: Dockerfile and/or docker-compose.yaml (or docker_image)
├── tests/                 # test.sh (or test.bat), uploaded to /tests AFTER the agent runs
├── solution/              # solve.sh, uploaded to /solution ONLY for the oracle agent
└── steps/<name>/...       # multi-step tasks only: instruction.md, tests/, solution/
```

Published files are task.toml, instruction.md, README.md, trajectory.json, and everything under environment/, tests/, solution/, steps/, minus `.gitignore` matches.
Container paths come from `H/models/trial/paths.py:37-46`.
They are `/logs/agent`, `/logs/verifier`, `/logs/artifacts` (bind mounts to the host trial dir), `/tests`, `/solution`, and `/harbor/skills`.

### 1.2 Full `task.toml` schema (`H/models/task/config.py`)

Top level (`TaskConfig`, `config.py:795-820`):

| Key | Type | Default | Notes |
|---|---|---|---|
| `schema_version` | str | `"1.4"` | legacy alias `version` |
| `source` | str? | none | |
| `multi_step_reward_strategy` | `"mean"`\|`"final"` | none (mean for multi-step) | |
| `artifacts` | list[str \| {source, destination?, exclude[], service?}] | `[]` | files collected after agent into `<trial>/artifacts/` |
| `[[steps]]` | list[StepConfig] | none | multi-step; each has `name`, `[steps.agent]`, `[steps.verifier]`, `min_reward` (float or dict), `healthcheck`, `artifacts` |

`[task]` package info (`PackageInfo`, `config.py:285-332`). It is required for publish, not for local runs.

| Key | Type | Notes |
|---|---|---|
| `name` | str | `org/name`, regex `^[a-zA-Z0-9][a-zA-Z0-9._-]*/[a-zA-Z0-9][a-zA-Z0-9._-]*$` |
| `version` | str? | free-form, e.g. `"1.0.0"` |
| `description` | str | `""` |
| `[[task.authors]]` | `{name, email?}` | |
| `keywords` | list[str] | |

`[metadata]` is a free-form dict.
`harbor init --include-standard-metadata` writes `author_name`, `author_email`, `difficulty`, `category`, `tags`, `expert_time_estimate_min`, `junior_time_estimate_min`.

`[agent]` (`AgentConfig`, `config.py:339-344`, plus network fields `config.py:218-243`):

| Key | Type | Default |
|---|---|---|
| `timeout_sec` | float? | **none = no timeout**. The scaffold writes 600. |
| `user` | str \| int? | image default USER |
| `network_mode` | `"public"`\|`"no-network"`\|`"allowlist"` | inherits environment |
| `allowed_hosts` | list[str] | only with allowlist |

`[verifier]` (`VerifierConfig`, `config.py:561-610`):

| Key | Type | Default |
|---|---|---|
| `timeout_sec` | float | 600.0 |
| `env` | dict[str,str] | `{}`. Supports `${VAR}` and `${VAR:-default}` resolved from the host. |
| `user` | str \| int? | image default |
| `environment_mode` | `"shared"`\|`"separate"` | `shared`, or `separate` if `[verifier.environment]` is set |
| `[verifier.environment]` | EnvironmentConfig | dedicated verifier container |
| `[[verifier.collect]]` | `{command, service="main", timeout_sec=60, user?}` | runs after the agent, before artifact collection. Use it to snapshot runtime state (e.g. dump a savegame). |
| `network_mode` / `allowed_hosts` | | phase override |

`[environment]` (`EnvironmentConfig`, `config.py:421-551`):

| Key | Type | Default | Notes |
|---|---|---|---|
| `build_timeout_sec` | float | 600.0 | covers the **whole** `environment.start()`: build + `compose up --wait` (`H/trial/trial.py:1427-1438`) |
| `docker_image` | str? | none | prebuilt image. If set, it is used **instead of** building, even when a Dockerfile exists, unless `--force-build` (`H/environments/definition.py:26-36`). Dockerfile optional then. |
| `os` | `"linux"`\|`"windows"` | linux | |
| `cpus` | int? | none | docker: `cpus` limit |
| `memory_mb` | int? | none | docker: `mem_limit` (deprecated `memory = "2G"`) |
| `storage_mb` | int? | none | deprecated `storage = "10G"` |
| `gpus` | int? | none | |
| `gpu_types` | list[str]? | none | |
| `tpu` | `{type, topology}` | none | GKE |
| `[[environment.mcp_servers]]` | `{name, transport="sse"\|"streamable-http"\|"stdio", url?, command?, args[]}` | `[]` | handed to the agent (see section 3.4) |
| `env` | dict[str,str] | `{}` | container env at startup, `${VAR}` templating from the host |
| `skills_dir` | str? | none | copied to the agent's skills dir |
| `[environment.healthcheck]` | `{command, interval_sec=5, timeout_sec=30, start_period_sec=0, start_interval_sec=5, retries=3}` | none | runs via `exec` in `main` after start, **before agent setup**. It must pass. |
| `workdir` | str? | image WORKDIR | default cwd for every exec |
| `network_mode` | enum | `"public"` | non-public modes add an egress-control sidecar on docker |
| `allowed_hosts` | list[str] | none | hostnames, `*.suffix`, IPs, CIDRs |
| `allow_internet` | bool | deprecated | maps to network_mode |

`[solution]` has `env` (dict). That env is applied when the oracle runs solve.sh.

### 1.3 `instruction.md`

Read verbatim and passed as the agent's prompt.
Installed agents receive it on stdin, for example claude-code via `printf "%s" "$VAR" | claude --verbose --output-format=stream-json ...` (`H/agents/installed/claude_code.py:1897`).
`--extra-instruction[-path]` appends text at run time.

### 1.4 `environment/`: Dockerfile vs compose

- **Dockerfile only:** Harbor uses its base compose with `build: ${CONTEXT_DIR}` and `command: sleep infinity`.
- **`docker-compose.yaml`:** merged after the base. Order: resources, build or prebuilt, task compose, extra `--extra-docker-compose`, env, mounts, egress (`H/environments/docker/docker.py:316-379`). You only add sidecars and overrides such as `depends_on`, `init`, and `healthcheck`. Service `main` is reserved for the agent. Sidecars are reachable by service name.
- Compose support by backend (Harbor docs `tasks/multi-container.mdx`):
  - Native compose: docker, podman, ec2, islo, vercel.
  - DinD compose: modal, daytona, gke, beam, blaxel, langsmith, novita, tensorlake, and others.
  - **No compose:** e2b, runloop, apple-container, singularity, and others.

### 1.5 `tests/` and verifier conventions (`H/verifier/verifier.py:96-238`)

1. After the agent phase, `tests/` is uploaded to `/tests`. It is never visible to the agent beforehand.
2. Harbor runs `chmod +x /tests/test.sh` as root, then `exec`s `/tests/test.sh > /logs/verifier/test-stdout.txt 2>&1`. The script runs as `[verifier].user`, or the image default user (root).
3. The **exit code is ignored.** Harbor then reads the reward file (lines 227-236):
   - `reward.json` if present: `json.loads` into `dict[str, float|int]` (`H/models/verifier/result.py`).
   - else `reward.txt`: `{"reward": float(text)}`.
   - else `RewardFileNotFoundError`. An empty file raises `RewardFileEmptyError`.
4. Multiple named metrics are fine. The job-level `mean` is computed **per key**, and missing keys count as 0 (`H/metrics/base.py`, `aggregate_reward_dicts`).
5. **Always include a `reward` key.** SkyRL and Prime read `rewards["reward"]` as the training scalar.
6. The scaffold comment says `rewards.json`, but that is wrong. The code reads `reward.json` (`H/models/trial/paths.py:46`).

### 1.6 `solution/solve.sh` and the oracle (`H/agents/oracle.py:81-151`)

The oracle uploads `solution/` to `/solution` and runs `/solution/solve.sh > /logs/agent/oracle.txt 2>&1` with `[solution].env`.
A non-zero exit is written to `agent/exit-code.txt`, but the trial does not error.
The verifier then runs normally. `harbor run -a oracle` is the task self-test.

---

## 2. Runtime: container start, services, verifier (CRITICAL)

### 2.1 Exact code path (docker backend)

1. `Trial._prepare()` (`H/trial/trial.py:441-453`) runs `environment.start()`, then `run_healthcheck()`, then skills upload, then `agent.setup()`.
2. `DockerEnvironment.start()` (`docker.py:868-923`):
   - writes mounts, resources, and env override files;
   - chooses prebuilt vs build;
   - runs `docker compose build`, then `down --remove-orphans`, then **`docker compose up --detach --wait`**;
   - makes the writable mount dirs;
   - uploads `environment/` into the workdir, but only for prebuilt-image tasks without a Dockerfile or compose.
3. Base compose: `main` gets `command: ["sh","-c","sleep infinity"]`. This replaces **CMD only**. Docker keeps the image ENTRYPOINT and passes the command as its args. In the toy run, PID 1 was `/sbin/docker-init -- /usr/local/bin/entrypoint.sh sh -c sleep infinity`.
4. Agent phase: `asyncio.wait_for(agent.run(...), timeout=[agent].timeout_sec × multipliers)` (`trial.py:487-561`). Every command is `docker compose exec [-u user] [-w workdir] [-e K=V] main bash -c "<cmd>"` (`docker.py:1141-1183`, `docker_unix.py:203-205`). Sidecar execs use `sh -c`.
5. `SingleStepTrial._run()` (`single_step.py:38-55`) runs `_run_agent`, `_upload_agent_logs`, `_collect_artifacts` (runs `[[verifier.collect]]` hooks), then **`_run_verifier()` in the same live env (shared mode)**. Only after that does it run `_stop_agent_environment()`.
6. Teardown runs `docker compose down --rmi local --volumes --remove-orphans`, which is the default `--delete`, or `down` with `--no-delete` (`docker.py:941-973`). `--rmi local` deletes the built image every trial. Rebuilds then hit the buildx layer cache.

### 2.2 Ways to keep an OpenTTD server running during agent and verifier

| Pattern | Verified here | Portability |
|---|---|---|
| **A. ENTRYPOINT script** starts the server in the background, then `exec "$@"` | Yes. Same PID at verifier time. | docker (verified). DinD compose backends (modal/daytona when a compose file exists) reuse the same base compose files, so this should hold there too; that is inferred from source, not run. **Not** Modal direct: it overrides ENTRYPOINT with its own keepalive unless the env kwarg `keepalive=None` (`H/environments/modal.py:285-300`). E2B and Daytona direct depend on the platform: UNCONFIRMED. |
| **B. `[environment.healthcheck]` command = idempotent `ensure-up` script** (start if not running, wait until ready, exit 0) | Yes. Single-container variant, same PID at verifier time. | **Every Harbor backend.** The healthcheck runs through `env.exec` (`H/environments/base.py:1329-1381`) before agent setup. Background processes started via exec survive. |
| **C. Compose sidecar** (`game:` service) | Yes. Same `boot_id` and tick count at verifier time. | Native and DinD compose backends only. Ignored by runners that only build `environment/Dockerfile` (Tinker cookbook). |
| D. Agent or tool lazily starts the server on first use | Yes. An agent-spawned `nohup` server was alive at verifier time. | Any runner. Belt and braces for third-party trainers. |

**Recommendation for Railhead:** use a single `main` image containing the OpenTTD dedicated server.
Add an idempotent `/usr/local/bin/railhead-up` and invoke it from all three places:
- the ENTRYPOINT;
- `[environment.healthcheck].command`;
- lazily from the agent-facing CLI or MCP tool.

The verifier (shared mode) then queries the live server, for example over the admin port, and writes `reward.json`.
Use `[[verifier.collect]]` to dump a savegame as an artifact for audit and regrade.
Avoid relying on a sidecar unless the game server must be isolated from the agent.
Isolation is an anti-cheat lever, since the agent cannot kill or patch a sidecar process.

`ensure-up` pattern that was verified (healthcheck variant):

```bash
#!/bin/bash
mkdir -p /run/svc
if ! { [ -f /run/svc/pid ] && kill -0 "$(cat /run/svc/pid)" 2>/dev/null; }; then
  nohup <server cmd> >/var/log/svc.log 2>&1 </dev/null &
  echo $! > /run/svc/pid
fi
for _ in $(seq 1 50); do <probe> && exit 0; sleep 0.2; done
exit 1
```

### 2.3 Fast teardown (measured)

PID 1 `sh -c "sleep infinity"` ignores SIGTERM, so `compose down` waits the 10s grace period per container.
The waits run sequentially when `depends_on` is set.

| Variant | env setup | teardown | trial total |
|---|---|---|---|
| ENTRYPOINT + sidecar, no init | 2.9s | **20.6s** | 24.4s |
| same + `init: true` on main and sidecar (compose), 2 concurrent | 2.9–3.2s | **0.5s** | 4.3–4.6s |
| healthcheck-start, Dockerfile only | 1.4s | **10.4s** | 12.8s |
| same + `STOPSIGNAL SIGKILL` in Dockerfile | 1.3s | **0.4s** | 2.7s |

Agent (oracle) and verifier phases took about 0.3s and 0.2s.
The final asset run took 4s of job time and 6.2s wall-clock, with images cached.
Fix: add `init: true` in compose, or `STOPSIGNAL SIGKILL` in the Dockerfile. OpenTTD needs no graceful shutdown inside an ephemeral trial.

### 2.4 Separate verifier mode

`[verifier].environment_mode = "separate"`, or setting `[verifier.environment]`, stops `main`.
Only collected artifacts reach a fresh verifier container (`single_step.py:43-50`).
That container **cannot** query the live game, so dump state with `[[verifier.collect]]` first.
Use it only if the anti-tamper story needs it.

---

## 3. CLI workflow

### 3.1 Scaffolding

```bash
harbor init railhead/<task> --task [--description ..] [--author "Name <e>"] \
   [--no-pytest] [--no-solution] [--include-standard-metadata] [--include-canary-strings] [--steps N] [-o DIR]
harbor init railhead/<dataset> --dataset [--with-metric]   # auto-adds sibling task dirs with digests
```

Task scaffold files: task.toml (`[task]`, `[metadata]`, `[verifier]`, `[agent]`, `[environment]`, `[solution.env]`), instruction.md, README.md, .gitignore, environment/Dockerfile (`FROM ubuntu:24.04`, `WORKDIR /app`), solution/solve.sh, tests/test.sh, and tests/test_outputs.py.
The pytest template installs uv in test.sh, runs `pytest --ctrf /logs/verifier/ctrf.json`, and writes 1 or 0 to reward.txt.

`dataset.toml` (`H/models/dataset/manifest.py`), verified output:

```toml
[dataset]
name = "railhead/toy-dataset"     # org/name
version = "1.0.0"
description = "Toy dataset"
authors = []                      # [{name, email?}]
keywords = []

[[tasks]]
name = "railhead/toy-service"
digest = "sha256:f1e9cd69...ae22b00baf"   # Packager content hash of the task dir

[[files]]                         # dataset-level files, e.g. metric.py (uv script: -i rewards.jsonl -o metric.json)
path = "metric.py"
digest = "sha256:5290...ead9"
```

- `harbor add <path|org/name>... --to <dataset>` adds or updates entries. `--scan` adds all subdir tasks. Verified.
- `harbor remove <org/name|path> --from <dataset>` removes entries. Verified.
- `harbor sync [dataset] [-u]` recomputes local digests. `-u` also bumps registry tasks to latest. Verified, and `publish` auto-syncs.
- **Gotcha (verified):** `harbor run -p <dataset-dir>` runs **every immediate subdir with a valid task.toml**. It ignores dataset.toml membership and metric.py (`H/models/job/config.py:174-191`, `H/job.py:791-797`). Dataset metrics apply only to registry or package datasets.

### 3.2 `harbor check`

It assembles a "check task" around your task: prebuilt `python:3.13-slim`, task files in `/app` (`H/analyze/check-task-template/task.toml`).
It then runs **claude-code with `claude-sonnet-4-6`** (default `-a claude-code -m claude-sonnet-4-6 -e docker`) to grade `H/cli/quality_checker/default-rubric.toml`.
The rubric's 11 criteria are:
- behavior_in_task_description
- behavior_in_tests
- informative_test_structure
- anti_cheating_measures
- structured_data_schema
- pinned_dependencies
- typos
- tests_or_solution_in_image
- test_deps_in_image
- hardcoded_solution
- file_reference_mentioned

**It requires `ANTHROPIC_API_KEY` or `ANTHROPIC_AUTH_TOKEN`** (`H/agents/installed/claude_code.py:56`). None was set in this shell, so it was not run.
To run it: `ANTHROPIC_API_KEY=... harbor check <task> [-r rubric.toml]`.
Note: `hardcoded_solution` would likely FAIL the toy task, whose solve.sh just `printf`s the answer.
`harbor analyze <job|trial>` is the same mechanism with rubric criteria reward_hacking and task_specification, default model claude-haiku-4-5.

### 3.3 `harbor run` (alias `harbor job start`)

Key flags, taken from `harbor run --help`:
- **Task selection:** `-p/--path` (task dir or dir of tasks), `-t org/name` (registry task), `-d org/dataset@ref`, `-i/-x` include and exclude globs, `-l` n-tasks.
- **Agent:** `-a agent`, `-m model` (repeatable), `--ak k=v` agent kwargs, `--ae K=V` agent env.
- **Environment:** `-e env` (default docker), `--force-build`, `--[no-]delete`, `--override-cpus`, `--override-memory-mb`, `--extra-docker-compose`, `--ek k=v` env kwargs.
- **Concurrency and timeouts:** `-n` concurrency (default 4), `-k` attempts per task, `-r` retries, `--timeout-multiplier` and per-phase multipliers.
- **Verifier:** `--ve K=V`, `--disable-verification`, `--verifier module:Class`.
- **Other:** `-o` jobs dir (default `./jobs`), `--job-name`, `-c config.yaml` (JobConfig), `--upload`, `--launch` (hosted), `--artifact PATH`.

Built-in agents (`-a`): oracle, nop, claude-code, codex, terminus / terminus-1 / terminus-2, aider, openhands, openhands-sdk, swe-agent, mini-swe-agent, gemini-cli, goose, opencode, cursor-cli, copilot-cli, cline-cli, qwen-coder, kimi-cli, kimi-code, trae-agent, hermes, devin, dspy-rlm, computer-1, langgraph, deerflow, junie, grok-build, antigravity-cli/sdk, cortex-code, eve, fx, mcode, mimo, nemo-agent, openclaw, pi, rovodev-cli, vibe, and `acp:<agent>`.
Custom agents load via `module.path:ClassName`.

Installed agents run `install()` **inside the task container** during agent setup, with a 360s default timeout (`trial.py:93`).
claude-code, for example, needs curl, bash, nodejs, npm, and procps.
So the task image must allow package install (network) or pre-bake these.

### 3.4 How agents see tools

`[[environment.mcp_servers]]` plus `--mcp-config` are merged and passed to the agent (`trial.py:952-960`).
claude-code writes them into `.claude.json` (`claude_code.py:1658-1684`).
**terminus-2 has no MCP client.** It only appends the server list to the instruction text (`H/agents/terminus_2/terminus_2.py:1578-1586`).
Since terminus-2 is the RL workhorse (SkyRL, Tinker), **Railhead must expose a shell CLI in `main`**.
An MCP server can be an extra layer for claude-code-style agents.

### 3.5 Job output layout (verified)

```
<jobs_dir>/<job-name>/
├── config.json  job.log  lock.json
├── result.json                 # JobResult: stats.evals["<agent>__<dataset>"].metrics[{key: mean}], reward_stats, exception_stats
└── <task>__<rand>/
    ├── config.json  lock.json  trial.log  [exception.txt]
    ├── result.json             # TrialResult: verifier_result.rewards, agent_result{tokens,cost,rollout_details}, timings per phase, exception_info
    ├── agent/                  # bind-mounted /logs/agent: oracle.txt | trajectory.json (ATIF) | sessions/ ...
    ├── verifier/               # bind-mounted /logs/verifier: reward.json|reward.txt, test-stdout.txt
    └── artifacts/manifest.json
```

The trajectory format is **ATIF** (Agent Trajectory Interchange Format), schema `ATIF-v1.7` (`H/models/trajectories/trajectory.py`).
A trajectory holds `session_id`, `agent`, `steps[]`, `final_metrics`, and `subagent_trajectories`.
Each step holds `{step_id, timestamp, source: system|user|agent, model_name, message, reasoning_content, tool_calls, observation, metrics{prompt_tokens, completion_tokens, cached_tokens, cost_usd, prompt_token_ids, completion_token_ids, logprobs}}`.
Only the oracle ran here, which writes no ATIF, so the ATIF shape is taken from source.

`harbor view <jobs_dir> [--port]` serves a web UI plus JSON API at `/api/jobs`. Verified HTTP 200 on 127.0.0.1.
`harbor upload <job>` and `harbor run --upload` share runs to Harbor Hub.

---

## 4. Publishing and auth

### 4.1 What `harbor publish` does (`H/publisher/publisher.py:172-327`, `H/cli/publish.py`)

- Requires a `[task]` section with `name = "org/name"`, an `environment/` dir, and a valid Task.
- Calls `ensure_org(org)`, which **creates the org if missing**. Storage permission requires membership.
- Computes a content hash over (relative path, sha256) of the packaged files. If that version already exists, it skips.
- Uploads `packages/<org>/<name>/<hash>/dist.tar.gz` to Supabase storage. It then registers metadata through the `publish_task_version` RPC: instruction, readme, the verifier, agent, and environment configs, the files list, tags, and visibility.
- **It never builds or pushes Docker images.** Consumers build from `environment/Dockerfile` or pull `[environment].docker_image`.
- **Hosted launches** (`harbor run --launch`) pull private images only from Google Artifact Registry, using a stored credential (`H/hosted/registry_credentials.py`). Anything else must be public.
- Datasets: `harbor publish <dataset-dir>` auto-syncs digests, publishes the sibling task dirs first, uploads `[[files]]`, then registers the dataset version.

Naming and versions:
- The package is `org/name`.
- `--tag` is repeatable, and `latest` is always added.
- Refs use the form `org/name@<tag|revision|sha256:digest>` (`H/models/package/reference.py`).
- Each publish of new content gets a new revision.
- `harbor version list|show|tag` manages tags.

Visibility:
- `--public/--private` applies to **new packages only**. The default is private.
- Making a dataset public cascades to its tasks (Harbor docs `harbor-hub/publish.mdx`).
- Later changes go through `harbor task visibility` / `harbor dataset visibility`, and `share` / `unshare` with orgs.

Hub web UI: **https://hub.harborframework.com**. It has task pages `/tasks/<org>/<name>`, dataset pages `/datasets/...`, `/jobs`, and `/profile` (org management).
Docs live at https://docs.harborframework.com.

### 4.2 Auth (`H/auth/*`, `H/cli/auth.py`)

- `harbor auth login` runs GitHub OAuth with PKCE through Supabase. It opens a browser to an authorize URL and waits for the callback on `http://localhost:19284/auth/callback`.
- `harbor auth login --no-browser` prints a hosted-callback URL (`https://hub.harborframework.com/auth/cli-callback`). On a TTY it prompts for the code.
- Non-TTY two-step flow: first run `harbor auth login --no-browser`, which errors with a URL and parks the PKCE verifier in `~/.harbor/oauth-pending.json`. Then run `harbor auth login --callback-url "<pasted URL or code>"`. **A human must click through GitHub once.** There is no device-code flow.
- After login, a long-lived personal API key `sk-harbor-<id>_<secret>` is minted and stored at `~/.harbor/credentials.json` (mode 0600). Sessions and refresh tokens are not stored.
- **Non-interactive or CI use:** set `HARBOR_API_KEY=sk-harbor-...`. It takes precedence over the file (`H/auth/credentials.py:1-12,26`).
- `harbor auth key list|revoke` manages keys. **There is no `key create` in the CLI.** Mint keys via login, or on the Hub web UI under Profile, then Settings (Harbor docs `hosted-harbor/api-key.mdx`). A web-minted key plus `HARBOR_API_KEY` is the fully non-interactive path for publishing from CI.
- `harbor auth status`, `harbor auth org list`, and `harbor auth logout` (revokes the key) round out the commands.
- Current state on this machine: "Not authenticated."

---

## 5. RL training integration

### 5.1 What Harbor 0.22.0 exposes

- **Programmatic trial API** (exported from `harbor`):

  ```python
  from harbor.models.trial.config import TrialConfig
  from harbor.trial.trial import Trial
  trial = await Trial.create(TrialConfig.model_validate({
      "task": {"path": "/abs/path/to/task"},          # or registry/git task ids
      "trials_dir": "~/trials",
      "agent": {"name": "terminus-2", "model_name": "hosted_vllm/policy",
                "override_timeout_sec": 1200,
                "kwargs": {"api_base": "http://trainer:8000/v1", "collect_rollout_details": True,
                           "max_turns": 32, "enable_summarize": False, "temperature": 1.0}},
      "environment": {"type": "docker", "override_cpus": 2, "override_memory_mb": 4096, "delete": True},
      "verifier": {"disable": False},
  }))
  result = await trial.run()
  reward  = result.verifier_result.rewards["reward"]
  rollout = result.agent_result.rollout_details      # list[RolloutDetail]
  ```

  `Job` / `JobConfig` give the same thing in batch form, with concurrency, retries, and metrics.
- **`RolloutDetail`** (`H/models/agent/rollout_detail.py`) holds per-turn `prompt_token_ids`, `completion_token_ids`, `logprobs`, and `extra`. terminus-2 fills it when `collect_rollout_details=True`. Summarization makes it incomplete, so trainers set `enable_summarize=false`.
- **LLM backends for terminus-2:** `llm_backend="litellm"` (any OpenAI-compatible `api_base`, e.g. a trainer's vLLM) or `"tinker"` (`H/llms/tinker.py`, `uv pip install harbor[tinker]`).
- **Not in 0.22.0:** `harbor.rl` (`RLEnvironment.step()/grade()`, `BashTool`) is PR #1291 and is **still OPEN/unmerged**. The cookbook's `harbor_rl/train.py` pins the git branch `feature/harbor-rl-4d0`.

### 5.2 Trainer integrations and what they require of our tasks

| Trainer | How it consumes Harbor tasks | Requirements on Railhead tasks |
|---|---|---|
| **SkyRL** (official; `NovaSky-AI/SkyRL/examples/train_integrations/harbor`) | Dataset = list of local task dirs (valid if `instruction.md` exists). Per rollout it runs `Trial.create(TrialConfig)` then `trial.run()`, and reads `verifier_result.rewards["reward"]` and `agent_result.rollout_details`. The default agent is terminus-2 on **Daytona**. Its default config overrides **`override_cpus: 1`, `override_memory_mb: 1024`, `override_storage_mb: 1024`**. | A `reward` key. A shell CLI, since terminus-2 has no MCP. Must fit or document resources, because trainer overrides beat task.toml. Daytona compose works through DinD. |
| **Tinker cookbook `harbor_rl` recipe** (Thinking Machines) | Loads tasks from `~/.cache/harbor/tasks/` (`harbor download`). It builds its **own** Modal sandbox from `environment/Dockerfile` (`modal.Image.from_dockerfile`), with its own bash tool and reward from tests. | **A Dockerfile is required. Compose sidecars and `[environment.healthcheck]` are not honored.** Server start must work from the Dockerfile alone or lazily (pattern D). |
| **Prime-RL / verifiers** (`verifiers/v1/tasksets/harbor`) | Pulls Harbor Hub packages through the Harbor CLI cache. It reads `/logs/verifier/reward.json` or `reward.txt`, with `reward_key="reward"` (other keys become metrics). It supports compose and mcp_servers. | **A pullable `[environment].docker_image` is required.** Dockerfile-only tasks are rejected unless `ignore_dockerfile`. |
| **harbor.rl + Tinker** (cookbook `harbor_rl`) | Unmerged API. | Watch PR #1291. |

### 5.3 Railhead publishing checklist derived from the above

1. Ship **both** `environment/Dockerfile` and `[environment].docker_image = "ghcr.io/<org>/railhead-openttd:<ver>"` (public). Push the image yourself; Harbor will not. Locally, Harbor uses the prebuilt image unless `--force-build`.
2. Use a single container with OpenTTD in `main`, started by the ENTRYPOINT, by `[environment.healthcheck]`, and lazily by the agent CLI. Add `STOPSIGNAL SIGKILL` (or `init: true`).
3. Make the verifier shared-mode and query the live server. `reward.json` must contain `reward` in [0,1] plus diagnostic metrics. Optionally use `[[verifier.collect]]` to dump a savegame artifact.
4. Expose a shell CLI for terminus-2. MCP (`[[environment.mcp_servers]]`, stdio inside `main`) is optional for claude-code and Prime.
5. Keep the OpenTTD footprint under 1 CPU and 1 GB, or document the needed overrides. SkyRL's defaults clamp resources.
6. Pin the Python deps in the image (the `harbor check` rubric's `pinned_dependencies`). Keep tests and solution out of the image. Install test-only deps in test.sh.

---

## 6. Hands-on: verified minimal example

Location: `docs/research/assets/harbor-toy-task/` (the copy of the scratch task, re-verified from that path).

```
harbor-toy-task/
├── task.toml                      # [task] railhead/toy-service, healthcheck, cpus=1, memory_mb=1024
├── instruction.md                 # write /srv/www/hello.txt; POST http://game:9000/tick x3
├── environment/
│   ├── Dockerfile                 # python:3.12-slim; ENTRYPOINT entrypoint.sh; CMD http.server :9090 (never runs)
│   ├── entrypoint.sh              # http.server :8080 & ; record pid ; exec "$@"
│   ├── docker-compose.yaml        # main: init + depends_on game healthy; game: build ./game, init, healthcheck
│   └── game/{Dockerfile,server.py}# stateful sidecar: GET /state, POST /tick
├── solution/solve.sh              # oracle: write file, 3 ticks, record pids, spawn nohup :8081
└── tests/{test.sh,check.py}       # writes multi-key /logs/verifier/reward.json
```

Command and result:

```bash
harbor run -p /Users/rachitt/railhead/docs/research/assets/harbor-toy-task \
  -a oracle -e docker -o ~/.cache/harbor/railhead-probe-jobs --job-name toy-final -q
# Total runtime: 4s   (wall 6.24s, images cached)
```

```json
{"reward": 1.0, "hello_served": 1, "game_ticks": 3,
 "entrypoint_service_alive": 1, "entrypoint_service_same_pid": 1,
 "cmd_service_alive": 0, "agent_spawned_service_alive": 1,
 "sidecar_same_boot": 1, "service_uptime_sec": 1}
```

What each probe proves:
- `entrypoint_service_same_pid=1`: the boot-time service survives from agent into verifier.
- `cmd_service_alive=0`: the image CMD is overridden.
- `agent_spawned_service_alive=1`: exec-launched daemons survive.
- `sidecar_same_boot=1` and `game_ticks=3`: sidecar state persists across phases.
- `test-stdout.txt` records `pid1 = /sbin/docker-init -- /usr/local/bin/entrypoint.sh sh -c sleep infinity`.

Run log (scratch jobs moved to the session scratchpad, `.../scratchpad/harbor-jobs/`):

| Job | Result | Note |
|---|---|---|
| toy-oracle-1 | RewardFileNotFoundError | `-o` under /private/tmp. Colima did not share it, so /logs mounts were empty. |
| toy-oracle-2 | all probes pass | teardown 20.6s (SIGTERM ignored) |
| toy-oracle-3 (`-k 2 -n 2`) | 2/2 pass | `init: true` brought teardown to 0.5s |
| toy-hc-1/2 | service probes pass | healthcheck-started service variant. `STOPSIGNAL SIGKILL` brought teardown from 10.4s to 0.4s. Reward 0 by design, since there is no sidecar. |
| toy-dataset-1 | 2 trials | `-p <dir>` ran all subdir tasks, ignoring dataset.toml |
| toy-final | pass | from the assets path |

Not run: `harbor check` and `harbor analyze` (they need an Anthropic key), `harbor publish`, and `harbor auth login` (excluded by brief).

## Sources

- Installed source: `~/.local/share/uv/tools/harbor/lib/python3.12/site-packages/harbor/` (0.22.0); file:line cited inline.
- [Harbor docs](https://docs.harborframework.com) and repo docs: `docs-mintlify/tasks/multi-container.mdx` and `docs-mintlify/harbor-hub/publish.mdx` in [harbor-framework/harbor](https://github.com/harbor-framework/harbor).
- [Harbor cookbook](https://github.com/harbor-framework/harbor-cookbook): `harbor_rl/`, `sky_rl/`, `prime_rl/`, `tinker_rl/` READMEs. Also [harbor.rl PR #1291](https://github.com/harbor-framework/harbor/pull/1291), which is open.
- [SkyRL Harbor integration](https://github.com/NovaSky-AI/SkyRL/tree/main/examples/train_integrations/harbor): `harbor_generator.py`, `harbor_trial_config/default.yaml`, `dataset.py`.
- [Tinker cookbook Harbor RL recipe](https://tinker-docs.thinkingmachines.ai/cookbook/recipes/harbor-rl/)
- [PrimeIntellect verifiers Harbor taskset](https://github.com/PrimeIntellect-ai/verifiers/tree/main/verifiers/v1/tasksets/harbor)
