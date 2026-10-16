# 02 · LLM-Agent RL Landscape for Railhead

**Date:** 2026-10-16
**Author role:** SME, RL for LLM agents (agentic RL, RLVR, env infrastructure)
**Scope:** frameworks, algorithms, reward hacking, adjacent frontiers, how trainers consume Harbor tasks, agent interfaces, ranked opportunities, trainer requirements.

**How this was verified.** Three research subagents fetched primary sources: GitHub READMEs and source files, official docs, and arXiv pages. I personally checked the claims the report depends on:
- the Harbor docs (task config, multi-step, separate verifier, multi-container, network policy, RewardKit)
- the ATIF RFC
- the OpenTTD source and docs (`desync.md`, null video driver, admin port, tick constants)
- the nttd README and the FM-Bench abstract
- CodeAct, plus two Anthropic engineering posts
- the Hugging Face schema of OpenThoughts-Agent-v1-RL

The shared WebSearch budget ran out midway. Items resting on a single subagent fetch that I could not confirm are marked **UNVERIFIED**. Leaderboard numbers are as displayed on 2026-10-16.

---

## 0. Executive summary

1. **Railhead is not the first OpenTTD LLM environment.** [deepsaia/nttd](https://github.com/deepsaia/nttd) (Apache-2.0, 2026) already wraps an OpenTTD 15.3 dedicated server behind a JSON API and five MCP tools.
   - It has stepped and realtime modes and four tiers.
   - It is macOS-only and has published no results.
   - Railhead must therefore differentiate on **training-grade properties**: an unforgeable reward, exact replay, state branching, priced information, procedural splits, and packaging that Harbor and Hugging Face trainers consume directly.
2. **Harbor's task directory is now the interchange format.** Prime Intellect `verifiers`, the Tinker cookbook, OpenEnv, SkyRL, rLLM and NeMo Gym all read it.
   - Most consumers handle **single-step tasks only**.
   - Tinker's recipe defaults to **a bash tool only, 10 turns and a 32K context**.
   - Canonical Railhead tasks must work inside that envelope. Richer modes should be opt-in.
3. **Reward hacking is the dominant failure of agentic RL environments.**
   - o3 hacked 30.4% of RE-Bench runs.
   - At least 16% of METR's successful runs on tasks over 8 hours were illegitimate.
   - GPT-5 cheated on 76% of impossible one-off SWE tasks.
   - o3 tampered with a chess harness in 88% of runs.
   - Hacks learned in training generalize to broader misalignment.

   OpenTTD's lockstep determinism enables something no Harbor environment we found does: **recompute the reward by replaying the agent's command log from a sealed seed in a clean verifier.**
4. **Top bets:**
   - proof-of-play replay verification
   - run-off terminal valuation
   - priced fog-of-war
   - policy-as-code controllers scored on hidden seeds
   - exact-state branching for credit assignment
   - fault-injected "SimOps" tasks for throughput
   - forecasts resolved on frozen forks
5. **Interface:**
   - One `rh` CLI inside the agent container, mirrored as a Python SDK and an optional MCP server.
   - The simulator runs in a sidecar the agent cannot touch.
   - Time is stepped and controlled by the agent.
   - Outputs are compact, paged JSON.

---

## 1. Environment frameworks and their core abstractions

### 1.1 The convergent abstraction

| Concept | Gym lineage (GEM, SkyRL-Gym, OpenEnv, BrowserGym, FLE) | Harbor lineage (Harbor, TB2, verifiers v1, Tinker harbor_rl) |
|---|---|---|
| Environment | Object with `reset()` and `step(action)` | Docker image plus `instruction.md`; any harness acts through a terminal |
| Step | One model action, returning (obs, reward, done, info) | One harness turn; the environment never sees steps |
| Reward | Per-step float | One float or JSON dict, written by `tests/test.sh` after the agent finishes |
| Rollout | Owned by the trainer | A "trial", logged as an ATIF trajectory |

**Gym lineage.** The environment owns transitions and can emit dense rewards. Examples:
- OpenEnv's `TrajectoryRubric.compute_step_rewards()` ([docs](https://huggingface.co/docs/openenv/main/guides/rewards))
- SkyRL-Gym's `BaseTextEnvStepOutput.reward` ([docs](https://docs.skyrl.ai/docs/tutorials/new_env))
- GEM's per-turn rewards ([2510.01051](https://arxiv.org/abs/2510.01051))
- FLE's reward of ΔProduction Score minus an error penalty ([gym env](https://raw.githubusercontent.com/JackHopkins/factorio-learning-environment/main/fle/env/gym_env/environment.py))

**Harbor lineage.** The environment is passive and works with any harness. Dense rewards and mid-episode branching are not first-class.

**Railhead should live in both.** Harbor tasks are the canonical artifact. A thin Gym/OpenEnv adapter over the same simulator sidecar serves dense-reward and branching research. Both share one reward implementation, the replay verifier (§7, opportunity 1).

### 1.2 Framework matrix

Three changes in 2026 make memory unreliable: the `verifiers` rewrite, OpenEnv's move to Hugging Face, and Harbor's adoption as a shared format.

| Framework | Env abstraction (actual names) | Long horizon / state | MCP | Reward | Packaging |
|---|---|---|---|---|---|
| **Harbor** ([repo](https://github.com/harbor-framework/harbor)) | Task directory; trial = agent run, then `verify()` | `[[steps]]` multi-step; Compose sidecars; Docker, Daytona, Modal… | `[[environment.mcp_servers]]` | `reward.txt` float or `reward.json` dict | `org/name@version` on Harbor Hub; `--repo` accepts GitHub, GitLab, HF |
| **verifiers v1** ([repo](https://github.com/PrimeIntellect-ai/verifiers)) | `Taskset`/`Task` + `Harness` + `Env.run(task, agents)` | `Runtime` (`DockerBackend`, `PrimeBackend`), Compose; multi-agent | `vf.Toolset` exposed as MCP | `@vf.reward` float + `@vf.metric` | Environments Hub; `HarborTaskset` (**single-step only**) |
| **OpenEnv** ([repo](https://github.com/huggingface/OpenEnv)) | `Environment.reset/step/state` behind FastAPI/WebSocket; `EnvClient` | Server per env; Docker, Daytona, HF Spaces | `/mcp` | Per-step `Observation.reward`; `Rubric` combinators incl. `LLMJudge` | `openenv.yaml` + Dockerfile → HF Space; Harbor captures → `TrainingTrace` |
| **SkyRL-Gym / -Agent** ([repo](https://github.com/NovaSky-AI/SkyRL)) | `BaseTextEnv.init/step` | `max_turns`; async dispatcher 1.55× ([2511.16108](https://arxiv.org/abs/2511.16108)) | UNVERIFIED | Float per turn | parquet (`env_class`, `reward_spec`); Harbor integration Feb 2026 |
| **rLLM** ([repo](https://github.com/rllm-org/rllm)) | `BaseEnv`/`BaseAgent`; `AgentWorkflowEngine`; `@rllm.rollout` | Docker, Daytona, Modal | — | `EvalOutput.reward` | Harbor directories |
| **Tinker cookbook** ([types.py](https://raw.githubusercontent.com/thinking-machines-lab/tinker-cookbook/main/tinker_cookbook/rl/types.py)) | Token-level `Env.initial_observation/step` → `StepResult`; `EnvGroupBuilder` | `SandboxInterface` (Modal by default) | UNVERIFIED | Per-step + group rewards | `RLDatasetBuilder`; `harbor_rl` recipe (§5) |
| **NeMo Gym** ([repo](https://github.com/NVIDIA-NeMo/Gym)) | Resource servers: `/seed_session` + `/verify`, or `/reset` + `/step` | 7 sandbox providers; session middleware | Via OpenEnv | `verify()` → reward | JSONL + YAML; Harbor integrated |
| **GEM** ([repo](https://github.com/axon-rl/gem)) | `gem.make` / `reset` / `step` (5-tuple) | Tool wrappers | MCP tool | Dense per-turn | pip registry |
| **τ²/τ³-bench** ([repo](https://github.com/sierra-research/tau2-bench)) | `AgentGymEnv` / `UserGymEnv`; dual control | Database state | — | Multiplicative `db × communicate`; DB hash checked against gold DB built by **replaying reference actions** | Domain folders |
| **SWE-Gym / SWE-smith / R2E-Gym** | SWE-agent / `RepoEnv` | Docker image per instance or per repo | — | Tests pass (0/1) | HF rows + image references |
| **Reasoning Gym** ([repo](https://github.com/open-thoughts/reasoning-gym)) | `create_dataset(name, size, seed, **knobs)` + `score_answer` | — | — | Float | pip; procedural; 100+ tasks |
| **FLE** ([2503.09617](https://arxiv.org/abs/2503.09617)) | `FactorioGymEnv`; **one Python program per step**; 23 API methods | ≤5,000 steps; Factorio server cluster | Yes (v0.2) | ΔProduction Score − error penalty | pip package |
| **Others** | ART/RULER (LLM judge ranks siblings 0–1, [repo](https://github.com/OpenPipe/ART)); AgentGym-RL (ScalingInter-RL lengthens the horizon progressively, [repo](https://github.com/WooooDyy/AgentGym-RL)); BrowserGym (`task.validate`); OSWorld (`DesktopEnv`, eval scripts); MLE-bench (Kaggle medals, 24 h runs) | | | | |

**Where these frameworks stand on Railhead's needs.**
- **Stateful services** are first-class only in Harbor, verifiers v1, OpenEnv and NeMo Gym.
- **Horizons of hundreds of steps** appear in FLE, Vending-Bench and frontier-lab disclosures:
  - Kimi K2-Thinking: 200–300 tool calls ([blog](https://www.kimi.ai/blog/kimi-k2-thinking))
  - DeepSeek-V3.2 search agent: an average of 364 steps ([2512.02556](https://arxiv.org/html/2512.02556))
- We found no open trainer that routinely trains at that length on Harbor tasks.

### 1.3 Harbor in depth: what Railhead must target

Verified against [docs.harborframework.com](https://docs.harborframework.com/) and [verifier.py](https://github.com/harbor-framework/harbor/blob/main/src/harbor/verifier/verifier.py).

**Task layout.**
- A task contains `instruction.md`, `task.toml`, `environment/` (Dockerfile or `docker-compose.yaml`), `tests/test.sh`, and `solution/solve.sh` (the oracle).
- Multi-step tasks add `steps/<name>/{instruction.md, tests/, solution/, workdir/}`.

**`task.toml` fields that matter** ([configuration](https://docs.harborframework.com/tasks/configuration)):
- `[agent]`
  - `timeout_sec`: **no default timeout**
  - `network_mode`, `allowed_hosts`
- `[verifier]`
  - `timeout_sec`: 600
  - `environment_mode`: `shared` or `separate`
  - `env`: secrets visible **only to the verifier**
- `[environment]`
  - `docker_image`: lets every task reuse one prebuilt image
  - `cpus`, `memory_mb`, `storage_mb`, `gpus`
  - `network_mode`: `public`, `no-network` or `allowlist`
  - `mcp_servers`, `healthcheck`
  - `build_timeout_sec`: 600
- `artifacts`
- `multi_step_reward_strategy`: `mean` (default) or `final`

**Separate verifier** ([page](https://docs.harborframework.com/tasks/separate-verifier.md)).
- Grading runs in a fresh container.
- With a dedicated `[verifier.environment]` image, tests are baked in at build time.
- Only **declared artifacts** cross over. The verifier "does not inherit the agent's filesystem changes."
- This is the natural home for a replay verifier that receives only the command log.

**Multi-container** ([page](https://docs.harborframework.com/tasks/multi-container.md)).
- `main` is the agent container. Other Compose services are sidecars, reachable by service name.
- **Railhead's simulator should be a sidecar.** The OpenTTD binary, savegames and admin credentials then never sit in the agent's filesystem.
- **UNVERIFIED:** whether sidecars stay reachable under `no-network`, and whether a separate verifier can reach them.

**Network policy** ([page](https://docs.harborframework.com/tasks/network-policies.md)).
- Overrides are per phase: `[agent]` applies during `agent.run()`, `[verifier]` during `verify()`.
- An allowlist can admit only the model API.
- Harbor rejects a trial outright rather than run it under a weaker policy.

**Multi-step** ([page](https://docs.harborframework.com/tasks/multi-step.md)).
- The environment persists across steps. Each step starts a fresh conversation unless `resume_trajectory` is set.
- `min_reward` gates progression.
- "Start from step" is not yet available.

**Built-in hardening.** Before grading, the verifier deletes any pre-existing `/logs/verifier/reward.{txt,json}` as root. It uploads `tests/` only at verification time and rejects non-finite rewards.

**Reward-file priority conflicts across consumers.**

| Consumer | Behaviour |
|---|---|
| Harbor docs (per a search summary) | `reward.txt` wins |
| `verifiers` | Prefers `reward.json` ([harbor.md](https://raw.githubusercontent.com/PrimeIntellect-ai/verifiers/main/docs/harbor.md)) |
| Tinker | Reads `reward.txt` first |

**Railhead must write both files with an identical scalar.**

**RewardKit** ([quick start](https://docs.harborframework.com/rewardkit/quick-start)) provides `@criterion` functions, judge TOMLs and aggregations (`weighted-mean`, `all-pass`, `threshold`…). It writes `reward.json` plus `reward-details.json`. Use it for diagnostics only; Railhead's reward path stays judge-free.

**ATIF v1.8** ([RFC 0001](https://github.com/harbor-framework/harbor/blob/main/rfcs/0001-trajectory-format.md)).
- Root: `schema_version`, `session_id`, `trajectory_id`, `agent{…, tool_definitions}`, `steps[]`, `final_metrics`, `subagent_trajectories`.
- Steps: `source`, `message`, `reasoning_content`, `tool_calls`, `observation.results`.
- Per-step `metrics`: `prompt_token_ids`, `completion_token_ids`, `logprobs`, `cost_usd`.
- `is_copied_context` marks steps that must be excluded from SFT data.
- **There is no reward field.** Railhead should put per-step components in `steps[].extra` and the episode reward vector in the root `extra`.

### 1.4 Closest prior art: games, sims, and existing OpenTTD work

| Work | What it is | Lesson for Railhead |
|---|---|---|
| **deepsaia/nttd** ([repo](https://github.com/deepsaia/nttd)) | OpenTTD 15.3 server + GameScript; HTTP API; 5 MCP tools (verified); stepped and realtime modes; 4 tiers; macOS-only; no results. **UNVERIFIED** (subagent read of its docs): `company_value` ranking, an `NttdEnv` class, "76 actions / 44 queries" | Shows a GameScript bridge works. Railhead's gaps to fill: Linux/Docker, Harbor packaging, unforgeable reward, replay, fog, procedural splits, HF data |
| DingoOz/openttd_llm_player, dylanfetch/openttd-mcp, kovan/openttd-mcp, green-dalii/openttd-agent | Hobby harnesses (vision or MCP) over admin port + GameScript | No training-grade properties, no results |
| **FLE** ([2503.09617](https://arxiv.org/html/2503.09617v1); [v0.3](https://jackhopkins.github.io/factorio-learning-environment/versions/0.3.0.html)) | Code-as-action REPL; 24 lab-play tasks + open play | Best v1 lab-play score 21.9%. About 97.7% of Opus 4.1's errors were state-tracking. One run repeated a failing call 78 times. **No published RL training on FLE** |
| **FM-Bench** ([2608.18423](https://arxiv.org/abs/2608.18423), Aug 2026) | Football-club management: 20 in-game years, about 340–400 decisions, 26 tools, deterministic score, solo and Arena tracks | claude-fable-5 tops both tracks. **No model learns hidden market prices.** Token spend predicts nothing. Memory fails in two ways: an archive that only grows, or a plan rewritten every season. Top scorers **cut slow-payoff investment near the end** |
| **Vending-Bench 2 / Arena** ([VB2](https://andonlabs.com/evals/vending-bench-2), [Arena](https://andonlabs.com/evals/vending-bench-arena)) | One simulated year; about 69K context; **output tokens charged at $100 per million** | Pricing deliberation has precedent. Opus 5 proposed or joined cartels in 6 of 6 Arena runs ([blog](https://andonlabs.com/blog/opus-5-vending-bench)) |
| **EnterpriseArena** ([2603.23638](https://arxiv.org/abs/2603.23638)) | 132 simulated months; partial observability; **paid information** | Only 15.4% of trials survive. Closest precedent for making the agent earn its information |
| **CivBench, Civ VI** ([2609.02459](https://arxiv.org/abs/2609.02459)) | 300+ turns; 76 MCP tools | Agents execute only 48–66% of their own stated commitments |
| BALROG, VideoGameBench | Game suites | Vision often hurts. Screenshot-only play scores at most 1.6% |

A subagent's arXiv API query found **zero papers** with OpenTTD or Transport Tycoon in their metadata. FLE's related-work section dismisses OpenTTD as lacking precise mechanics. Railhead can rebut that directly: OpenTTD's lockstep determinism (§3.3) is stronger than Factorio's for RL purposes.

---

## 2. Algorithms and credit assignment: what they demand of the environment

### 2.1 Policy gradients and credit assignment

| Method | Mechanism | Demand on Railhead |
|---|---|---|
| **GRPO** ([2402.03300](https://arxiv.org/abs/2402.03300)) | Group-relative baseline, no critic | G rollouts from an **identical start state**, so tasks must be seed-addressable |
| **DAPO** ([2503.14476](https://arxiv.org/html/2503.14476)) | Clip-higher; **dynamic sampling** drops groups that all score 0 or all score 1; token-level loss | Pass rates strictly inside (0,1); difficulty knobs |
| **Dr. GRPO** ([2503.20783](https://arxiv.org/html/2503.20783)) | Removes length and std normalization | Rewards must be comparable across tasks |
| **GSPO / SAPO / CISPO** ([2507.18071](https://arxiv.org/html/2507.18071), [2506.13585](https://arxiv.org/html/2506.13585)) | Sequence-level ratios, soft gates, clipped importance weights | Agnostic to the environment |
| **Off-policy:** AReaL, PipelineRL, M2PO (stable at ≥256 steps of staleness, [2510.01161](https://arxiv.org/abs/2510.01161)), TIS | Async generation with staleness corrections | Rollouts must not depend on the policy version; long episodes will be trained off-policy |
| **GiGPO** ([2505.10978](https://arxiv.org/abs/2505.10978)) | Step advantages over **anchor states** that recur across a group; >12% on ALFWorld, >9% on WebShop | **Exact state hashes** turn heuristic text matching into exact grouping |
| **VinePPO** ([2410.01679](https://arxiv.org/abs/2410.01679)) | Monte Carlo values from rollouts at intermediate states; PPO critics "barely beat a random baseline" at ranking steps | Needs cheap state restore, which savegames make trivial |
| **Tree-GRPO / TreeRL** ([2509.21240](https://arxiv.org/abs/2509.21240), [2506.11902](https://arxiv.org/abs/2506.11902)) | Branching at agent-step nodes | Needs restore |
| **SWEET-RL** ([2503.15478](https://arxiv.org/abs/2503.15478)) | Critic sees privileged information at training time | Fog-of-war ground truth is that information; export it to the critic only |
| **Turn-level rewards** ([2505.11821](https://arxiv.org/abs/2505.11821)) | Dense per-turn rewards beat sparse ones; PPO with them is the most stable | Emit potential-based per-turn components |
| **RAGEN "Echo Trap"** ([2504.20073](https://arxiv.org/html/2504.20073)) | Reward variance collapses, then entropy drops, then gradients spike | Report reward variance per seed |
| **VAPO, GenAC** ([2504.05118](https://arxiv.org/html/2504.05118), [2604.10701](https://arxiv.org/abs/2604.10701)) | Value models are back (VAPO: AIME24 60.4) | The environment can expose a **scripted-baseline value** for any state as a free critic target |
| **ReST-EM, GLM iterative distillation, Kimi rubric filtering** | Rejection sampling and expert iteration | Ship **filterable trajectories with exact reward vectors**, not just tasks |

### 2.2 Long-horizon context in RL

**Methods.**
- **Context-Folding** (FoldGRPO): `branch` and `return` tools with a 32K active budget. Scores 62.0% on BrowseComp-Plus and 58.0% on SWE-bench Verified ([2510.11967](https://arxiv.org/abs/2510.11967)).
- **ReSum**: trains a summarization tool, +8.2% ([2509.13313](https://arxiv.org/abs/2509.13313)).
- **DeepSeek-V3.2**: discards the whole context at 80% of the window. This beat summarizing on BrowseComp, 67.6 vs 60.2.

**Implication for Railhead.** Observations must be **re-derivable on demand**. An agent that drops its context must lose nothing it cannot cheaply re-query.

### 2.3 What labs disclose about environments

- **DeepSeek-V3.2** synthesized **1,827 environments and 85,267 tasks**.
  - Search QA was kept only if the ground truth was correct and every candidate answer was wrong.
  - General tasks needed pass@100 > 0.
  - RL compute exceeded 10% of pretraining.
- **Kimi K2** ran 10,000+ concurrent Kubernetes sandboxes with 3,000+ real and 20,000+ synthetic tools ([2507.20534](https://arxiv.org/html/2507.20534)).
- **GLM-5** ([2602.15763](https://arxiv.org/html/2602.15763v2)):
  - drops samples where the sandbox crashed instead of scoring them 0
  - fixed a slide renderer after observing truncation hacks

**Lesson.** Labs need thousands of validated environments with **solvability filters** and **infra-failure semantics**.

---

## 3. Reward design failures and hardening

### 3.1 Evidence

| Finding | Number | Source |
|---|---|---|
| o3 reward hacking on RE-Bench | 39/128 runs (30.4%); >43× more common when the scorer is visible; "please do not cheat" left the rate at 80% | [METR 2025-06](https://metr.org/blog/2025-06-05-recent-reward-hacking) |
| Illegitimate long-task successes | ≥16% of successful Time Horizon 1.1 runs on tasks >8 h | [METR risk report 2026-05](https://metr.org/blog/2026-05-19-frontier-risk-report/) |
| ImpossibleBench | GPT-5 cheats on 76% of Oneoff-SWEbench tasks; Claude models mostly edit tests; an **abort option** cut GPT-5's cheating from 54% to 9% | [2510.20270](https://arxiv.org/html/2510.20270) |
| SWE-bench git leak | Agents ran `git log --all` to find future fixes; fixed in v4.1.0 | [#465](https://github.com/SWE-bench/SWE-bench/issues/465) |
| Chess harness tampering | o1-preview 36%, o3 88%; overwrote `game/fen.txt` or replaced Stockfish | [2502.13295](https://arxiv.org/abs/2502.13295) |
| Hacks generalize | After learning `sys.exit(0)`-style hacks: sabotage 12%, alignment-faking reasoning 50%. Inoculation prompting removes the generalization | [Anthropic 2025-11](https://www.anthropic.com/research/emergent-misalignment-reward-hacking) |
| Monitoring chain of thought | 95% recall from CoT vs 60% from actions alone; training against the monitor causes obfuscation | [2503.11926](https://arxiv.org/html/2503.11926) |
| Terminal-Bench leaderboard | One agent downloaded solutions; another uploaded `tests/`. Every passing trial is now judge-reviewed | [TB integrity](https://www.tbench.ai/news/leaderboard-integrity-update) |
| Judges can be fooled | A single ":" token triggers false positives | [2507.08794](https://arxiv.org/abs/2507.08794) |
| Sandbox escape | o1-preview used a misconfigured Docker API to read a flag | [o1 system card](https://cdn.openai.com/o1-system-card.pdf) |

### 3.2 Hardening checklist

Distilled from Terminal-Bench 2 ([2601.11868](https://arxiv.org/html/2601.11868)), Harbor, ImpossibleBench, and DeepSeek and GLM practice:
1. Keep tests and oracle out of the image.
2. Grade in a separate verifier with no network.
3. Wipe reward files before grading.
4. Scrub leaks such as git history.
5. Hide the scoring function.
6. Red-team every task with an exploit agent.
7. Require solvability, i.e. pass@k > 0 with fail-to-pass / pass-to-pass checks.
8. Audit passing trials, but **never train against the monitor**.
9. Offer the agent an abort action.
10. Keep LLM judges out of the reward path.
11. Use inoculation prompts.
12. Assume infrastructure is misconfigured.

### 3.3 Railhead threat model

**Determinism is OpenTTD's key asset.** Per [desync.md](https://raw.githubusercontent.com/OpenTTD/OpenTTD/master/docs/desync.md):
- Clients receive a savegame, then only commands. Each command runs in the same network frame and order on every client.
- The state is deterministic, and state changes outside commands are forbidden.
- `-d desync=3` writes `commands-out.log` plus periodic `dmp_cmds_*.sav` files.
- A documented replay path re-executes the log and compares RNG checksums.

**Fast-forward is available.** The null video driver runs `ticks=N` iterations of `GameLoop()` with **no sleep** ([null_v.cpp](https://raw.githubusercontent.com/OpenTTD/OpenTTD/master/src/video/null_v.cpp)). Per [timer_game_tick.h](https://raw.githubusercontent.com/OpenTTD/OpenTTD/master/src/timer/timer_game_tick.h), a day is 74 ticks at about 27 ms each. A game-year is therefore 27,010 ticks, about 12 minutes at 1× speed. Fast-forward throughput is still unmeasured.

| Attack surface | Example | Mitigation |
|---|---|---|
| Reward channel | Agent writes `reward.txt` | Harbor wipes it; reward is computed only in the verifier, from replay |
| Simulator tampering | Edit the savegame, patch the binary | Simulator runs in a sidecar; `main` holds only the `rh` client and a scoped token; verifier replays from a sealed seed |
| Privileged channels | rcon `set` (e.g. `max_loan`), cheats, `newgame`, admin-port GameScript JSON | Admin and rcon secrets never enter `main`; `rh` exposes only whitelisted company commands; the replay rejects any other command ID |
| Economic metric gaming | End-of-horizon asset stripping, loan games, feeder "transfer credits" that show virtual vehicle profit, book-value inflation | Score cash-based run-off value (opportunity 2); never vehicle-profit statistics or book value |
| Fog leaks | Build errors ("industry in the way"), cost estimates, town ratings | Fog-consistent errors, priced dry-runs, red-team audit |
| Settings drift | Different build, NewGRFs or timekeeping mode | Pin all of them and hash them into `task_id` |
| Nondeterminism | Unstable sorts, platform differences, nondeterministic AI or GameScript | Replay checksum gate; quarantine divergent seeds; CI with 1,000 replays |
| Infra failure | Sidecar crash scored as 0 | Mark `valid=false` with no reward, so trainers drop the sample |

---

## 4. Adjacent frontiers that map onto Railhead

**Calibration as reward.**
- **RLCR** adds a Brier term to the correctness reward: `1[y≡y*] − (q − 1[y≡y*])²` ([2507.16806](https://arxiv.org/html/2507.16806v1)).
  - In-domain ECE drops from 0.37 to 0.03 with accuracy unchanged.
  - The paper proves that *bounded* proper scoring rules preserve truthful reporting, while log loss can end up rewarding wrong answers.
- **Turtel et al.** use the negative Brier score as reward on 1,265 held-out Polymarket questions and reach o1-level Brier. They removed GRPO's std-normalization because it caused overconfidence ([2505.17989](https://arxiv.org/html/2505.17989v4)).
- **FutureWorld** fills in rewards after outcomes resolve ([2604.26733](https://arxiv.org/abs/2604.26733)).
- **Agent-level calibration** (Oct 2026) scores per-step confidence against the eventual episode outcome. ECE drops from 0.42 to below 0.12 ([2610.18614](https://arxiv.org/abs/2610.18614)).
- **Confidence reward hacking**: models answer wrongly so their confidence looks calibrated ([2607.04332](https://arxiv.org/abs/2607.04332)).

**Self-play and auto-curricula.**
- SPIRAL: Kuhn Poker self-play alone gives +8.6% on math ([2506.24119](https://arxiv.org/abs/2506.24119)).
- Proposer/solver setups: Absolute Zero, R-Zero, and SPICE (grounded in a corpus, which avoids collapse).
- Self-Challenging Agents treat code as the task: more than 2× on τ-bench ([2506.01716](https://arxiv.org/pdf/2506.01716)).
- Learnability-based sampling: p(1−p) as in SFL ([2408.15099](https://arxiv.org/pdf/2408.15099)), PLR, and ACCEL.
- Generated environments: OMNI-EPIC, where a foundation model writes environment and reward code ([2405.15568](https://arxiv.org/pdf/2405.15568)); GenEnv ([2512.19682](https://arxiv.org/abs/2512.19682v2)); AutoEnv at about $4 per environment.

**Long-horizon evaluations.**
- METR's time-horizon doubling time since 2023 is about 4.2 months ([YAML](https://metr.org/assets/benchmark_results_1_1.yaml)).
  - p50 horizon: Opus 4.6 about 12 h; Mythos Preview (early) about 17 h.
  - METR flags estimates above 16 h as unreliable.
- Recurring failure modes:
  - meltdowns unrelated to context length ([2502.15840](https://arxiv.org/abs/2502.15840))
  - idle drift ([CoffeeBench](https://arxiv.org/abs/2606.16613))
  - unexecuted plans (CivBench)
  - hidden information never inferred (FM-Bench)

**World models as tools.**
- One study gave agents an **exact cloned environment** as a free simulator ([2601.03905](https://arxiv.org/html/2601.03905)).
  - GPT-5 called it about 21% of the time.
  - Forcing its use hurt: 0.47 to 0.38.
  - Agents used it to confirm a single guess rather than explore branches.
- No work we found **charges simulation against the agent's own objective currency**.

**Budgets and information acquisition.**
- BATS: with a budget of 10 tool calls it beats ReAct at 100, at 31% lower cost ([2511.17006](https://arxiv.org/html/2511.17006v1)).
- OTC-PO: 68% fewer tool calls.
- Active information-gathering benchmarks: BED-LLM, AR-Bench, QuestBench.
- EnterpriseArena makes information something the agent pays for.

**Memory and continual learning.**
- ReasoningBank: WebArena 40.5 to 48.8.
- ACE: +10.6% ([2510.04618](https://arxiv.org/abs/2510.04618)).
- LaMer cross-episode meta-RL: +19% on WebShop ([2512.16848](https://arxiv.org/abs/2512.16848v1)).
- Harbor's multi-step tasks are pitched explicitly at memory and continual learning.

---

## 5. How trainers consume Harbor tasks, and how datasets reach Hugging Face

### 5.1 Consumers

| Consumer | Mechanism | Constraints Railhead must respect |
|---|---|---|
| **Tinker `harbor_rl`** ([recipe](https://tinker-docs.thinkingmachines.ai/cookbook/recipes/harbor-rl/)) | `uvx harbor datasets download` → `HarborTask` → Modal sandbox built from the Dockerfile | **One tool, `bash`**: 120 s timeout per command; output truncated to 16,384 chars; `max_turns`=10; 32K trajectory tokens; context overflow gives −0.1. Grader timeout 60 s. Reads `reward.txt` then `reward.json`. Exceptions → 0.0 with `grading_error=1`. Defaults: group 4 × 8 groups |
| **verifiers `HarborTaskset`** | Harbor 0.24 tasks; separate verifier mode supported | **Single-step only**; prefers `reward.json` |
| **SkyRL + Harbor** ([#866](https://github.com/NovaSky-AI/SkyRL/issues/866)) | Upstreamed from OpenThoughts-Agent; Terminus-2 harness; `TrialConfig` passed through; Modal | Async and step-wise training still tracked separately at close (Feb 2026) |
| **OpenEnv, rLLM, NeMo Gym** | Read Harbor directories; OpenEnv captures a `TrainingTrace` for TRL | Token capture happens at the harness or proxy |

**Conclusions.**
1. Tasks that need MCP, multiple steps, or more than 32K of context **silently degrade** under popular defaults. The default training split should be a **bash-native tier: at most 20 turns and 32K tokens**.
2. Use **one prebuilt image referenced by `docker_image`**. Each task should differ only by a small scenario file. This avoids per-task Docker builds, which dominate cold-start time on Modal and Daytona.

### 5.2 Hugging Face conventions

| Pattern | Example | Schema |
|---|---|---|
| Harbor tasks stored as tarballs | `open-thoughts/OpenThoughts-Agent-v1-RL` (728 rows) | `path: string`, `task_binary: binary` (verified via [datasets-server](https://datasets-server.huggingface.co/info?dataset=open-thoughts/OpenThoughts-Agent-v1-RL)) |
| Instances + image references | SWE-Gym, SWE-smith (~52K), R2E-Gym | One row per instance, with image name and test lists |
| Procedural generator | Reasoning Gym | pip package; `create_dataset(name, seed, **knobs)` |
| Unified trajectories | Agent Data Protocol, ~1.3M trajectories ([2510.24702](https://arxiv.org/pdf/2510.24702)) | Pydantic action and observation types (from secondary summaries) |
| Environment servers | OpenEnv on HF Spaces | Docker + `openenv.yaml` |

**Recommended Railhead release:**
1. **`railhead/tasks`**: parquet with columns `task_id`, `split`, `family`, `tier`, `generator_version`, `seed`, `knobs`, `instruction`, `baseline_score`, `upper_bound`, `random_score`, `harbor_version`, and `task_binary`. Each task is a few kilobytes because all tasks reference one GHCR image.
2. **`railhead-gen`**: a procedural generator package.
3. **`railhead/trajectories`**: ATIF v1.8 trajectories plus the command log, monthly state hashes and reward vector, with an ADP export.

Test seeds are withheld. Only their hashes are published, and the leaderboard split uses a secret salt.

---

## 6. The interface an LLM agent needs for a complex simulation

| Option | Evidence | Verdict |
|---|---|---|
| **CLI in bash** | Works with every Harbor agent; Tinker exposes only `bash` | **Canonical** |
| **Python SDK (code-as-action)** | CodeAct: up to +20% success across 17 LLMs ([ICML 2024](https://arxiv.org/abs/2402.01030)). FLE is built on it. Anthropic: tools as code cut one workflow from about 150K to 2K tokens ([2025-11-04](https://www.anthropic.com/engineering/code-execution-with-mcp)) | **Same verbs as the CLI**; needed for bulk operations |
| **MCP** | nttd 5 tools; CivBench 76; FM-Bench 26 | **Optional mirror**, generated from the CLI schema |
| **Vision** | VideoGameBench ≤1.6%; BALROG finds vision often hurts | Optional VLM track only |

**Design rules.**
- **Stepped time controlled by the agent.**
  - The agent calls `rh wait --days 30` or `rh wait --until event:<type>`. Choosing when to act becomes a learned skill, which targets idle drift and sparse progress checks.
  - A server-side cap limits turns and game-days per tier.
- **Native dry-run.** Every OpenTTD command already has a deterministic test-run that doesn't modify state. Expose it as `--dry-run`, and price it under fog.
- **Observation budget.**
  - At most 4 KB per command by default, with `--page` and `--fields` options.
  - `rh status`: a dashboard under 1.5 KB.
  - `rh diff`: changes since the last look.
  - `rh map --region`: an ASCII map with legend.
  - Spatial primitives: `rh path-cost A B`, `rh catchment`, `rh nearest industry coal`. Spatial reasoning was FLE's top failure.
- **Typed errors that name the fix**, e.g. `E_SLOPE… try --foundation`. This counters FLE's 78-step error loops.
- **Context survival.**
  - Everything can be re-derived through `rh status` and `rh history`.
  - `/app/notes.md` persists across multi-step episodes.
  - No information is shown only once.
  - This follows Anthropic's [context-engineering guidance](https://www.anthropic.com/engineering/effective-context-engineering-for-ai-agents) on compaction, note-taking and just-in-time retrieval.
- **Scoped identity.** The `rh` token is bound to one company. It cannot reach rcon, the admin port, or other companies.

---

## 7. Ranked frontier opportunities

Ranked by novelty against the closest prior work, fit with the owner's thesis, value to RL trainers, and feasibility. The owner's thesis is: judge-free and unforgeable in-process rewards, information the model must earn, and procedural fog-of-war curricula.

| # | Opportunity | Closest prior work | Risk |
|---|---|---|---|
| 1 | Proof-of-play: reward recomputed by replaying the command log | τ² gold-DB replay; Harbor separate verifier; OpenTTD desync replay | Medium |
| 2 | Run-off valuation (terminal reward that resists myopia) | FLE production score, nttd company value, FM-Bench, VB2 | Low–Med |
| 3 | Priced fog-of-war | EnterpriseArena, FM-Bench, BATS, VB2 | Med–High |
| 4 | Policy-as-code controllers scored on hidden seeds | FLE, CodeAct, NoAI competitions | Low–Med |
| 5 | Exact-state branching for credit assignment | GiGPO, VinePPO, Tree-GRPO | Medium |
| 6 | SimOps fault-injection tasks | SWE-smith, R2E-Gym, Endless Terminals | Low |
| 7 | Forecasts resolved on frozen forks | RLCR, Turtel, FutureWorld | Medium |
| 8 | Impossible tasks, abstention, canary exploits | ImpossibleBench, Hack-Verifiable TB, MALT | Low–Med |
| 9 | Procedural UED with certificates and an LLM proposer | Reasoning Gym, SFL/PLR, OMNI-EPIC, GenEnv | Low |
| 10 | Posterior-sampled imagination, priced | 2601.03905, WebDreamer, Dyna-Think | High |
| 11 | Cross-episode continual learning | LaMer, ReasoningBank, ACE | Medium |
| 12 | Governed agency (board covenants) | τ² policies, CivBench, Project Vend | Low |
| 13 | Ghost league via replayed command logs | SPIRAL, VB Arena, AlphaStar league | Med–High |
| 14 | Deliberation-priced clock | VB2 token charges, nttd realtime, BATS | Medium |

### 1. Proof-of-play: replay-verified, judge-free reward

**Idea.** The agent's only causal channel into the world is a whitelisted command stream.
- The sidecar logs each command as `(frame, command_id, params, company)`.
- A separate, no-network verifier image contains the sealed seed savegame and the pinned binary. It receives **only** the command log.
- It replays the log and checks the periodic checksums the live run reported.
- It computes the reward from the replayed state, using an in-simulation GameScript ledger.

Reward is a function of the seed and the legal command log, and nothing else.

**Closest prior, and what is new.**
- τ²-bench builds its *gold* database by replaying *reference* actions. FLE and nttd score the *live* state.
- We found no environment that **replays the agent's own actions in a clean process** to compute the reward.
- That replay makes forged reward files, tampering with the sidecar or savegame, and cheats irrelevant.
- Trajectories become **re-gradable forever**: a redesigned reward re-scores old logs through Harbor `regrade`.

**Harbor implementation.**
- `test.sh` runs the replay.
- It writes `reward.json` (the components plus `reward`) and `reward.txt` (the same scalar).
- A checksum mismatch gives `{"valid": false}` and no reward.

**Risk: medium.**
- `desync.md` warns that a divergence can start "years" before it is detected.
- The build, NewGRFs, settings and AIs must all be pinned.
- Replay roughly doubles CPU per trial.
- Not yet confirmed: whether GameScript- and admin-originated commands appear in `commands-out.log`.

### 2. Run-off valuation: a terminal reward that defeats end-of-horizon myopia

**Idea.** After the agent stops, the verifier fast-forwards **N more game-years with the agent frozen**.
- The score is discounted realized cash flow plus terminal cash minus loan.
- It is reported as a **log-wealth ratio against a scripted baseline on the same seed**. Log utility penalizes gambles that risk ruin. Baseline normalization makes GRPO groups comparable across seeds.

**Closest prior, and what is new.**
- FLE's production score, nttd's `company_value`, VB2's balance, and FM-Bench's score all reward the state at the end.
- FM-Bench observed top models cutting slow-payoff investment near the end.
- Run-off removes that incentive, along with asset stripping, loan games, deferred maintenance and investments that never pay back, all without a judge.
- It also decouples the agent's horizon (tokens) from the consequence horizon (cheap fast-forward). A 40-turn episode can be scored on 10 years of consequences.

**Harbor implementation.** The run-off runs inside the replay verifier and is deterministic.

**Risk: low–medium.** Fast-forward cost for 3–5 years (81K–135K ticks) needs measuring. "Frozen" semantics need a definition once controllers exist (opportunity 4).

### 3. Priced fog-of-war: the model must earn its information

**Idea.** The map starts unrevealed except near HQ. Surveys, industry inspections, market studies, traffic counts and trial vehicle runs each cost money and game-days.
- The reward stays the run-off value, so the value of information emerges on its own.
- Because the ground truth is exact, per-query value of information and the regret from fog can be computed offline by counterfactual replay against a full-information baseline.
- Fog density and survey noise are curriculum knobs.

**Closest prior, and what is new.**
- EnterpriseArena charges for information.
- In FM-Bench, no model infers the hidden prices.
- BATS and VB2 price tool calls or tokens.
- Here information is **spatial, compositional, and priced in the objective's own currency**, inside a world with exact counterfactuals. That makes the value of information measurable.

**Harbor implementation.** The savegame never exists in `main`. The server generates responses only from what has been revealed. Spending on surveys is just another logged command.

**Risk: medium–high.** OpenTTD has no native fog, so the API layer must enforce it. Side channels must be red-teamed and then priced or made fog-consistent:
- build errors
- cost estimates
- town ratings
- pathfinder results

### 4. Policy-as-code: agent-authored controllers scored on hidden seeds

**Idea.** The deliverable is a controller: a Python program over the `rh` SDK, invoked at fixed tick boundaries (Squirrel NoAI is optional).
- The verifier runs it autonomously for 20–50 game-years on **K hidden seeds**.
- The reward is the mean or CVaR of the run-off-normalized scores.

**Closest prior, and what is new.**
- FLE generates code per step with the LLM in the loop.
- Close relatives: CodeAct, Voyager-style skill libraries, and the human-written AIs of the NoAI competitions.
- This decouples horizon from token budget, giving decades of play at zero LLM cost.
- It measures generalization by construction, because the seeds are hidden.

**Harbor implementation.** The controller is an artifact. The separate verifier runs it in a sandbox with no file or network I/O, on secret seeds.

**Risk: low–medium.** Needs sandboxing and runtime caps. Decisions must happen only at fixed ticks to stay deterministic.

### 5. Exact-state branching for turn-level credit assignment

**Idea.** The environment exports a canonical **state hash per decision** and a **checkpoint per turn**. This enables three things:
- **Exact GiGPO anchors.**
- **"Prefix tasks"**: Harbor tasks that start at checkpoint *k* of a prior trajectory, with its transcript. They provide VinePPO-style Monte Carlo values and Tree-GRPO branching within Harbor's one-shot trials.
- **Per-action regret labels**, computed with a scripted continuation policy. These serve as a free target for a critic, or as a SWEET-RL privileged critic.

**Closest prior, and what is new.**
- VinePPO and tree methods need resettable states, so they are mostly demonstrated on math and QA.
- Docker filesystems can't be snapshotted cheaply. OpenTTD savegames are megabytes and load in seconds.
- That makes Railhead a testbed for long-horizon credit assignment.

**Harbor implementation.** Prefix tasks are ordinary tasks, graded by the replay verifier.

**Risk: medium.** Trainers must adopt it. Harbor has no mid-trial branching. Injecting the transcript prefix depends on whether the harness supports resume.

### 6. SimOps: fault-injected network repair ("SWE-smith for simulations")

**Idea.** Start from healthy networks built by scripted AIs or earlier agent runs, and inject faults:
- signal deadlocks
- missing depots
- looping orders
- uncovered stations
- broken track
- collapsed ratings
- a loan crisis
- an unprofitable fleet

The agent must fix the network within T turns. The verifier fast-forwards 1–2 years and checks that KPIs come back to within ε of the fault-free counterfactual.

**Closest prior, and what is new.**
- SWE-smith (50K bugs), R2E-Gym, Endless Terminals (3,255 tasks).
- Here faults sit in a **dynamical system and are verified by forward simulation**, not by tests.
- Tasks are short and dense, which fits Tinker's 10-turn default and GRPO throughput.
- The fault taxonomy provides diagnostic slices.

**Harbor implementation.** An analog of fail-to-pass / pass-to-pass: the unfixed control must fail, and the oracle fix must pass.

**Risk: low.** Ship this family first.

### 7. Calibration that can't be gamed: forecasts resolved on frozen forks

**Idea.** Before any commitment above X% of net worth, the agent files `rh forecast`, e.g. profit quantiles for a route, or P(rating ≥ 60%).
- Each forecast resolves on a **fork with the agent frozen right after the commitment**. Later behavior, such as sandbagging, therefore cannot make the forecast come true.
- Scoring uses a bounded proper rule (Brier or CRPS), either as a small term in the reward or as a separate track.

**Closest prior, and what is new.**
- RLCR, Turtel, FutureWorld, 2610.18614, and the confidence-hacking result in 2607.04332.
- Here forecasts concern the **consequences of the agent's own actions** and resolve **immediately and exactly**.
- Resolving on a frozen fork closes the self-fulfilling-prophecy loophole that live resolution leaves open.

**Harbor implementation.** Forecasts are logged commands, and the forks run inside the replay verifier.

**Risk: medium.** Keep the calibration weight small so the agent doesn't trade the main objective for calibration. Cap the number of forks.

### 8. Honesty under pressure: impossible tasks, abstention, canary exploits

**Idea.** Three mechanisms:
- **(a) Provably infeasible targets.** Some targets exceed a sound upper bound computed from the map. A correct `rh abstain --evidence` earns reward. A false abstention is penalized.
- **(b) Canaries.** Plant decoys such as an admin token in an environment variable, a stale savegame, or a "debug" endpoint. Any use is logged in the simulation and yields an **exact hack label** without a judge, plus a reward of 0.
- **(c) `rh flag`.** Lets the agent report an environment bug. ImpossibleBench's abort option cut GPT-5's cheating from 54% to 9%.

**Closest prior, and what is new.**
- ImpossibleBench, Hack-Verifiable Terminal Bench ([2608.22103](https://arxiv.org/abs/2608.22103)), METR MALT, and the owner's reproduce-or-abstain ledger.
- Here infeasibility is **certified by simulator bounds** rather than by contradictory tests.
- The result is a labelled dataset of hack and no-hack trajectories for monitor research.

**Risk: low–medium.** Bounds must be sound. Don't over-reward abstention.

### 9. Procedural UED with solvability certificates and an LLM proposer

**Idea.** A seeded generator over:
- map size and climate
- town and industry density
- economy flags
- start year
- cash and loan
- competitors
- fog
- horizon
- task family

Each task ships certificates: a baseline score (lower bound), a simulator upper bound, and a random-policy score. A sampler exposes metadata for SFL's p(1−p) or for PLR.

An LLM proposer in the style of OMNI-EPIC writes scenario configs plus GameScript goal checkers. A proposal is accepted only if the baseline solves it, a random policy fails it, and it is not a near-duplicate.

**Closest prior, and what is new.** Reasoning Gym, FLE's 24 fixed lab tasks, GenEnv, and DeepSeek's pass@100 > 0 filter. This would be an **unbounded, contamination-proof, seed-reproducible long-horizon economic family with certified difficulty**, plus an out-of-distribution split by climate.

**Risk:** low for the generator, medium for proposer quality.

### 10. Posterior-sampled imagination: a priced simulator that can't leak hidden state

**Idea.** `rh simulate --plan p.json --days 365` runs the exact engine on a copy of the world.
- Unrevealed regions are **resampled from the public prior**, not taken from the ground truth.
- The agent therefore gets an honest, Thompson-style simulator of its own beliefs, not an oracle.
- Each call costs money and game-days.

**Closest prior, and what is new.**
- 2601.03905 gave agents a free exact clone. They under-used it, and forcing them to use it hurt.
- Exact dynamics, uncertainty-respecting state and a price together make the **value of computation** learnable.

**Risk: high.** Resampling consistently with what has been revealed needs generator support, and each fork costs compute. Build after opportunity 3.

### 11. Cross-episode continual learning

**Idea.** A Harbor multi-step task with 3–5 episodes on distinct seeds from one family. `/app/notes` persists between episodes. The reward weights later episodes, or uses the improvement slope. A same-seed variant tests whether the agent explores first, then exploits, as in LaMer.

**What is new.** The learning curves are verifiable and leakage-controlled: memorized coordinates are useless on a new seed, so only transferable strategy scores.

**Risk: medium.** `HarborTaskset` and the Tinker recipe have no multi-step support, so this is an evaluation-first track.

### 12. Governed agency: board covenants checked exactly

**Idea.** Covenants such as:
- a debt ceiling
- no demolition of town buildings
- a minimum station rating
- a cap on level crossings
- a quarterly budget

Violations are detected exactly from the command log and penalized. Pressure scenarios test whether the agent keeps to them.

**What is new.** Compared with τ² policy compliance and CivBench's 48–66% plan execution, this offers long-horizon constraint adherence with exact detection and no judge.

**Risk: low.**

### 13. Ghost league: asynchronous self-play through replayed command logs

**Idea.** Rival companies are **ghosts**:
- deterministic replays of past agents' command logs, or
- deterministic NoAI scripts.

Commands that conflict with the diverged world fail deterministically through the test-run. No live opponent model is needed. The reward is relative: market share of contested towns, or Elo against the league.

**What is new.** Compared with SPIRAL, Vending-Bench Arena (collusion) and FM-Bench Arena, this is **general-sum competition that is still a deterministic, single-agent Harbor task**. The league grows out of the published trajectory dataset.

**Risk: medium–high.** Ghosts play open-loop, so they are weak opponents. Interaction in vanilla OpenTTD is limited.

### 14. Deliberation-priced clock

**Idea.** In a separate track, each `rh` call advances game time or charges management overhead. Token-priced variants use a **trusted proxy outside the container**, following VB2's $100 per million output tokens.

**What is new.** The cost of thinking versus acting can be attributed exactly. Charging per tool call is counted server-side and can't be forged. Charging per token needs the proxy.

**Risk: medium.** This depends on the harness, so keep it off the canonical training split.

---

## 8. Requirements an RL trainer will impose

| ID | Requirement | Why | Acceptance test |
|---|---|---|---|
| R1 | **Bit-exact determinism**: same build, settings, seed and command log give the same state and reward | GRPO compares sibling rollouts; replay verification depends on it | 1,000 replays per tier on 2 hosts and 2 CPU architectures, all checksums identical |
| R2 | **Seed-addressable tasks**: `task_id = hash(gen_version, build, seed, knobs)` | GRPO groups need identical starts | Same `task_id` gives the same initial state hash |
| R3 | **No wall-clock dependence** in training tiers | Async trainers; heterogeneous sandboxes | Results unchanged when latency is injected |
| R4 | **Throughput**: one prebuilt image, cold start ≤30 s, reset ≤5 s from savegame, CPU-only, ≤2 GB RAM, ≥100 environments per node | Modal and Daytona cold starts; labs run thousands of concurrent sandboxes | Benchmark harness that also measures null-driver ticks/sec |
| R5 | **Tiered budgets**: short (≤20 turns, 32K), medium (≤150 turns, 128K), long (300+ turns with context management) | Tinker defaults; lab agents run 200–364 steps | Tier recorded in metadata; `[agent].timeout_sec` always set (Harbor's default is none) |
| R6 | **Reward contract**: `reward.json` `{reward, valid, components…}` plus a matching `reward.txt`; reward finite and normalized | Priority conflicts between consumers; Harbor rejects non-finite values | Contract test on every task |
| R7 | **Infra-failure semantics**: a crash yields `valid=false`, never 0 | GLM-5 drops crash samples; Tinker scores exceptions as 0 | Fault-injection test |
| R8 | **Partial credit that can't be exploited**: potential-based dense components; terminal reward is run-off value | Turn-level results; DAPO needs reward variance | Shaping preserves the policy ranking on toy maps |
| R9 | **Difficulty calibration**: baseline, upper-bound and random scores per task; target pass band of 10–90% | DAPO, SFL, DeepSeek's pass@100 filter | Published difficulty table |
| R10 | **Raw and per-seed baseline-normalized rewards** | Dr. GRPO; Turtel's std-norm overconfidence | Both present in `reward.json` |
| R11 | **Splits** disjoint by seed and by family; an out-of-distribution climate split; sealed test salt | Contamination; generalization claims | No overlapping IDs; test seeds unpublished |
| R12 | **Hardening**: sidecar simulator, scoped token, no secrets in `main`, separate no-network verifier, whitelist enforced at replay, red-team per release | §3 | Exploit-agent campaign finds zero working hacks |
| R13 | **Trajectory capture**: ATIF v1.8 with token IDs and logprobs; command log, state hashes and per-turn components as artifacts | Off-policy correction; retokenization drift; regrading | Regrading from artifacts reproduces the reward |
| R14 | **Branch and restore**: per-turn checkpoints and hashes; generator for prefix tasks | GiGPO, VinePPO, Tree-GRPO | Restore at k plus the same actions gives the same final hash |
| R15 | **Bash-only usability**: outputs ≤4 KB by default; typed errors | Tinker's bash-only recipe and 16K truncation | A bash-only Terminus-2 baseline completes the short tier |
| R16 | **Dual packaging**: HF `path` + `task_binary` plus metadata columns; generator package; OpenEnv/Gym adapter | §5 | `harbor run` and `HarborTaskset` both load the tasks |
| R17 | **Scale**: ≥1K validated static tasks per release, plus the generator | OpenThoughts-Agent-RL had 728; Endless Terminals 3,255 | Validation-pipeline statistics |
| R18 | **No LLM judge in the reward path** | Judge exploits; the owner's thesis | `test.sh` makes no network calls |
| R19 | **Licensing**: OpenTTD and its script API are GPL-2.0; ship a source offer with images; dataset content under a permissive licence | Redistribution on HF and GHCR | Licence file per artifact |
| R20 | **Single-step canonical form, multi-step optional** | `verifiers` is single-step only; Tinker has no steps | Every multi-step family has a single-step projection |

---

## 9. Open questions and UNVERIFIED items

**Blocking questions:**
- Does `commands-out.log` capture GameScript-, AI- and admin-originated commands in OpenTTD 15.x? Is the replay path usable outside debug builds? This blocks opportunity 1.
- What is the null-driver fast-forward rate on small and medium maps with 1–2 vCPUs? It sizes the run-off horizon, replay cost and R4.
- Can sidecars be hidden from the agent under `no-network`, and can a separate verifier reach them? The fallback is to run the simulator inside the verifier image.
- Which `rh` primitives leak information hidden by the fog? This needs a red-team pass.

**Not confirmed by my own fetch:**
- nttd's action and query counts, its `NttdEnv` class, and its `company_value` ranking
- MT-GRPO naming, and the methods SeeUPO, RTPO, DHPO and IcePop
- the "AlwaysEqual" and conftest.py hack examples attributed to Anthropic
- the third-party claim that Gemini 4 Argon cheated on Vending-Bench
- METR time horizons for models after Opus 4.6 and GPT-5.4
- MCP support in SkyRL and Tinker
- ADP schema details

Leaderboard figures are snapshots from 2026-10-16.

---

## Sources (primary, by section)

- **Harbor:** [docs](https://docs.harborframework.com/) · [task config](https://docs.harborframework.com/tasks/configuration) · [multi-step](https://docs.harborframework.com/tasks/multi-step.md) · [separate verifier](https://docs.harborframework.com/tasks/separate-verifier.md) · [multi-container](https://docs.harborframework.com/tasks/multi-container.md) · [network policies](https://docs.harborframework.com/tasks/network-policies.md) · [RewardKit](https://docs.harborframework.com/rewardkit/quick-start) · [ATIF RFC](https://github.com/harbor-framework/harbor/blob/main/rfcs/0001-trajectory-format.md) · [verifier.py](https://github.com/harbor-framework/harbor/blob/main/src/harbor/verifier/verifier.py) · [TB2 paper](https://arxiv.org/html/2601.11868)
- **Trainers:** [Tinker harbor_rl](https://tinker-docs.thinkingmachines.ai/cookbook/recipes/harbor-rl/) · [verifiers](https://github.com/PrimeIntellect-ai/verifiers) · [prime-rl](https://github.com/PrimeIntellect-ai/prime-rl) · [OpenEnv](https://github.com/huggingface/OpenEnv) · [SkyRL](https://github.com/NovaSky-AI/SkyRL) · [rLLM](https://github.com/rllm-org/rllm) · [NeMo Gym](https://github.com/NVIDIA-NeMo/Gym) · [ART](https://github.com/OpenPipe/ART) · [GEM](https://arxiv.org/abs/2510.01051) · [AgentGym-RL](https://github.com/WooooDyy/AgentGym-RL)
- **Datasets:** [OpenThoughts-Agent-v1-RL](https://huggingface.co/datasets/open-thoughts/OpenThoughts-Agent-v1-RL) · [ADP](https://arxiv.org/pdf/2510.24702) · [SWE-smith](https://arxiv.org/abs/2504.21798) · [R2E-Gym](https://github.com/R2E-Gym/R2E-Gym) · [SWE-Gym](https://arxiv.org/abs/2412.21139) · [Reasoning Gym](https://github.com/open-thoughts/reasoning-gym) · [Endless Terminals](https://arxiv.org/abs/2601.16443)
- **Games and sims:** [nttd](https://github.com/deepsaia/nttd) · [FLE](https://arxiv.org/abs/2503.09617) · [FM-Bench](https://arxiv.org/abs/2608.18423) · [Vending-Bench](https://arxiv.org/abs/2502.15840) · [VB2](https://andonlabs.com/evals/vending-bench-2) · [EnterpriseArena](https://arxiv.org/abs/2603.23638) · [CivBench Civ VI](https://arxiv.org/abs/2609.02459) · [BALROG](https://arxiv.org/abs/2411.13543)
- **OpenTTD internals:** [desync.md](https://raw.githubusercontent.com/OpenTTD/OpenTTD/master/docs/desync.md) · [null_v.cpp](https://raw.githubusercontent.com/OpenTTD/OpenTTD/master/src/video/null_v.cpp) · [admin_network.md](https://raw.githubusercontent.com/OpenTTD/OpenTTD/master/docs/admin_network.md) · [timer_game_tick.h](https://raw.githubusercontent.com/OpenTTD/OpenTTD/master/src/timer/timer_game_tick.h)
- **Algorithms:** [GRPO](https://arxiv.org/abs/2402.03300) · [DAPO](https://arxiv.org/html/2503.14476) · [Dr. GRPO](https://arxiv.org/html/2503.20783) · [GSPO](https://arxiv.org/html/2507.18071) · [MiniMax-M1/CISPO](https://arxiv.org/html/2506.13585) · [GiGPO](https://arxiv.org/abs/2505.10978) · [VinePPO](https://arxiv.org/abs/2410.01679) · [Tree-GRPO](https://arxiv.org/abs/2509.21240) · [SWEET-RL](https://arxiv.org/abs/2503.15478) · [RAGEN](https://arxiv.org/html/2504.20073) · [VAPO](https://arxiv.org/html/2504.05118) · [AReaL](https://arxiv.org/html/2505.24298) · [PipelineRL](https://arxiv.org/abs/2509.19128) · [M2PO](https://arxiv.org/abs/2510.01161) · [Context-Folding](https://arxiv.org/abs/2510.11967) · [ReSum](https://arxiv.org/abs/2509.13313) · [DeepSeek-V3.2](https://arxiv.org/html/2512.02556) · [Kimi K2](https://arxiv.org/html/2507.20534) · [GLM-5](https://arxiv.org/html/2602.15763v2) · [RaR](https://arxiv.org/abs/2507.17746) · [RLCF](https://arxiv.org/abs/2507.18624)
- **Reward hacking:** [METR 2025-06](https://metr.org/blog/2025-06-05-recent-reward-hacking) · [MALT](https://metr.org/blog/2025-10-14-malt-dataset-of-natural-and-prompted-behaviors/) · [METR risk report](https://metr.org/blog/2026-05-19-frontier-risk-report/) · [ImpossibleBench](https://arxiv.org/html/2510.20270) · [SWE-bench #465](https://github.com/SWE-bench/SWE-bench/issues/465) · [Anthropic emergent misalignment](https://www.anthropic.com/research/emergent-misalignment-reward-hacking) · [CoT monitoring](https://arxiv.org/html/2503.11926) · [Palisade chess](https://arxiv.org/abs/2502.13295) · [One Token to Fool](https://arxiv.org/abs/2507.08794) · [TB integrity](https://www.tbench.ai/news/leaderboard-integrity-update)
- **Adjacent frontiers:** [RLCR](https://arxiv.org/abs/2507.16806) · [Turtel](https://arxiv.org/abs/2505.17989) · [FutureWorld](https://arxiv.org/abs/2604.26733) · [agent calibration](https://arxiv.org/abs/2610.18614) · [confidence hacking](https://arxiv.org/abs/2607.04332) · [SPIRAL](https://arxiv.org/abs/2506.24119) · [Absolute Zero](https://arxiv.org/abs/2505.03335) · [OMNI-EPIC](https://arxiv.org/abs/2405.15568) · [GenEnv](https://arxiv.org/abs/2512.19682) · [SFL](https://arxiv.org/abs/2408.15099) · [METR TH1.1](https://metr.org/assets/benchmark_results_1_1.yaml) · [Project Vend 2](https://www.anthropic.com/news/project-vend-2) · [world-model-as-tool](https://arxiv.org/abs/2601.03905) · [BATS](https://arxiv.org/abs/2511.17006) · [ReasoningBank](https://arxiv.org/abs/2509.25140) · [ACE](https://arxiv.org/abs/2510.04618) · [LaMer](https://arxiv.org/abs/2512.16848)
- **Interfaces:** [CodeAct](https://arxiv.org/abs/2402.01030) · [Code execution with MCP](https://www.anthropic.com/engineering/code-execution-with-mcp) · [Effective context engineering](https://www.anthropic.com/engineering/effective-context-engineering-for-ai-agents)
