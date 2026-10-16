# 01 · Games RL environment landscape, and where Railhead can push the frontier

**Date:** 2026-10-16
**Role:** games-RL SME research pass for Railhead (an RL and agent environment on OpenTTD)
**Status:** research input for design. No code.

**Method.** Three parallel research passes covered classic environments, LLM-era benchmarks, and sim/transport work. A fourth pass was mine: direct verification of every OpenTTD engine fact and every OpenTTD prior-art repo cited here. Claims were checked against primary sources (arXiv full text or abstracts, GitHub source and READMEs, official docs) fetched this session. Every item dated after mid-2026 was re-fetched to confirm it exists, including CivBench (Civ VI), the Game Arena paper, Factoriax, Business Arena, EcoAgent-Bench and the Vending-Bench 2 and BALROG leaderboards. **UNVERIFIED** marks anything from a secondary source or not re-fetchable.

**Revision (same day).** A teammate flagged two items this pass had missed. I verified both against source and folded them in: nttd (an OpenTTD benchmark engine) and FM-Bench (long-horizon competitive management). The teammate's note said nttd ranks by `company_value`. nttd's own docs say so, but its scoring code says the native rating ranks, so the report records both. The shared web-search quota ran out late in the pass, so a few long-tail items rest on direct fetches of known URLs.

---

## 0. Summary for the design team

1. **One serious OpenTTD benchmark already exists; Railhead must position against it.**
   - **[deepsaia/nttd](https://github.com/deepsaia/nttd)** (Apache-2.0, created 2026-03-10, last push 2026-09-05, 531 commits, 3★, Zenodo concept DOI 10.5281/zenodo.22105381):
     - Wraps an OpenTTD 15.3 server with a GameScript.
     - Exposes a JSON HTTP API, MCP, and a Gymnasium-style `NttdEnv`.
     - Has stepped (paused) and real-time modes, and tiers of 1 to 10 game years.
     - Its action surface covers rail and signals.
   - Its limits:
     - One contestant company per session; several contestant companies are refused.
     - Pinned to temperate on 25 square map families.
     - macOS-only.
     - No populated public board found.
   - There is still no peer-reviewed RL or LLM paper on OpenTTD.
   - Six smaller hobby LLM harnesses from 2026 exist (0 to 5★, road/air/bus only, no published results). The best-instrumented one reports a negative result: "every measured run ends with the company losing money" ([green-dalii/openttd-agent](https://github.com/green-dalii/openttd-agent)).
   - FLE's arXiv v1 dismissed OpenTTD for lacking "precise mechanics and exponential scaling" ([arXiv 2503.09617v1](https://arxiv.org/html/2503.09617v1)). Railhead must answer that line directly.
2. **The closest cousin is the Factorio Learning Environment (FLE).** It established code-as-action, lab-play vs open-play, and an unbounded production score. Its documented weak points are where Railhead should differentiate:
   - The API pre-solves routing.
   - It has no market and no competitors.
   - There are no trains in v1.
   - Forking is a harness trick, not an evaluation primitive.
3. **Most failures in the field fall into three recurring themes.**
   - **Determinism gets exploited.** Brute search on ALE; open-loop policies on SMAC.
   - **Aggregate metrics get gamed or saturate.** NetHack median gaming; BALROG saturation; CivBench outcomes too noisy to rank models.
   - **The interface or harness decides what gets measured.** Action masking, API pre-solving, harness on/off, and real-time latency all shift results.
4. **OpenTTD's unique assets are infrastructure, not graphics.**
   - Lockstep determinism with a replayable command log.
   - Full savegame serialization.
   - A headless null video driver that runs ticks without sleeping.
   - Deity-mode GameScript that can act as any company.
   - An admin port streaming per-company commands with frame numbers.
   - Up to 15 companies, mixing humans and scripts.
   - Native, typed, ethically loaded actions: bribes, exclusive rights, road reconstruction.
   - A 17-year ecosystem of human-written AIs.
5. **Top frontier bets** (section 5):
   1. Priced ground-truth forks with resampled hidden randomness.
   2. Branch-point task suites cut from replayable command logs.
   3. Rail-network design plus operations with verified throughput.
   4. Program-as-company: autonomous in-engine AIs scored over decades.
   5. An engine-verifiable conduct and promise-keeping ledger.

---

## 1. Foundational environments and the critiques they provoked

| Environment | Interface | Score | What broke, and the lesson | Primary source |
|---|---|---|---|---|
| **ALE** (2013), revisited by **Machado et al.** (2018) | 160×210 px or 128-byte RAM; 18 actions; frameskip | Game score | **Determinism exploit.** "The Brute" is a trajectory-tree search that only works because ALE is "deterministic, episodic, and guarantee[s] a unique starting state". It beat the best learner on 45 of 55 games.<br>**What fixed it.** No-op and human starts did not. Sticky actions (ς=0.25) did. The paper also says to end on game-over, not life loss.<br>**Versioning.** ALE v4 and v5 have different defaults, so results across them can't be compared. | [1709.06009](https://arxiv.org/abs/1709.06009), [ALE docs](https://ale.farama.org/environments/) |
| **Atari 100k** and **rliable** | Same as ALE | Normalized score | Point estimates over 3–10 seeds reversed conclusions. Use IQM, stratified-bootstrap CIs, and performance profiles. | [2108.13264](https://arxiv.org/abs/2108.13264) |
| **Gym → Gymnasium** | `step`/`reset` | n/a | A single `done` flag made libraries drop the bootstrap on time-limit truncation. The fix was the `terminated`/`truncated` split (v0.26). Pardo et al.: put remaining time in the observation for fixed horizons. `FuncEnv` exposes state so you can sample several transitions from it. | [2407.17032](https://arxiv.org/abs/2407.17032), [1712.00378](https://arxiv.org/abs/1712.00378) |
| **PettingZoo** (AEC) | Sequential agent-environment cycle | Per-agent reward | Two bugs led to AEC:<br>• A reward-ordering bug in "pursuit" (fixing it raised reward up to 22%).<br>• Race conditions in simultaneous-move APIs, unnoticed for years in the sequential social dilemma (SSD) games. | [2009.14471](https://arxiv.org/abs/2009.14471) |
| **OpenSpiel** | Extensive-form games, explicit chance nodes | Utilities | The best counterfactual API in the field: `clone`, `serialize`, `chance_outcomes`, and `resample_from_infostate` (samples a world consistent with a player's knowledge). | [1908.09453](https://arxiv.org/abs/1908.09453), [API](https://openspiel.readthedocs.io/en/latest/api_reference.html) |
| **Procgen / CoinRun** | 64×64 px, procedural levels | Return | Training on fixed level sequences gives an "illusion of meaningful progress". Closing the gap needs about 10⁴ levels plus a train/test seed split. | [1912.01588](https://arxiv.org/abs/1912.01588), [1812.02341](https://arxiv.org/abs/1812.02341) |
| **NLE** (2020) and the **NetHack Challenge** (2021) | Symbolic glyphs; 93 actions; turn-based; 14.4K steps/s | In-game score; challenge ranked by median | **Symbolic bots won.** The best symbolic median was nearly 3× the best neural one.<br>**Metric gaming.** Entrants ended episodes early to protect the median.<br>**Train/test split.** NLE bans save-scumming and RNG manipulation at test time only. | [2006.13760](https://arxiv.org/abs/2006.13760), [2203.11889](https://arxiv.org/abs/2203.11889) |
| **Crafter → Craftax** | 64×64 px or symbolic; 17 actions | Geometric mean of 22 achievement success rates | The geometric mean rewards breadth.<br>Craftax is 169–257× faster. PPO effectively solves Craftax-Classic in about 1B steps in under an hour.<br>Exploration bonuses (ICM, RND, E3B) did not help on the hard achievements. | [2109.06780](https://arxiv.org/abs/2109.06780), [2402.16801](https://arxiv.org/abs/2402.16801) |
| **MineRL / BASALT / MineDojo / Voyager** | Pixels plus labels, or a high-level JS API | Diamond; human judgments; MineCLIP | **MineRL 2019:** no team obtained a diamond.<br>**MineRL 2020:** observations and actions were re-embedded to stop agents hard-coding label semantics.<br>**BASALT:** natural-language tasks judged by humans; winners were retrained to catch cheating.<br>**Voyager:** a code skill library on the privileged Mineflayer API, which the authors admit is "not apple-to-apple". | [2101.11071](https://arxiv.org/abs/2101.11071), [2107.01969](https://arxiv.org/abs/2107.01969), [2305.16291](https://arxiv.org/abs/2305.16291) |
| **SMAC → SMACv2** | Unit-level, decentralized | Win rate | Open-loop policies that see only the timestep and agent ID do well on many maps. SMACv2 randomizes team composition, start positions and ranges. Its diagnostic: an open-loop baseline must now fail. | [2212.07489](https://arxiv.org/abs/2212.07489) |
| **Melting Pot 1 and 2** | Substrates; scenarios with held-out background bots | Focal per-capita return, normalized so random = 0 and exploiter = 1 | It measures generalization to novel co-players. The "do not train on the test set" rule refers to the background population. | [2107.06857](https://arxiv.org/abs/2107.06857), [2211.13746](https://arxiv.org/abs/2211.13746) |
| **XLand / AdA / XLand-MiniGrid** | Predicate-composed goals; JAX | Normalized percentiles | Easy tasks dominate means, so use low percentiles (XLand-MiniGrid uses the 20th).<br>Rules are hidden from the agent.<br>A generalization gap persists after about 1T transitions. | [2107.12808](https://arxiv.org/abs/2107.12808), [2312.12044](https://arxiv.org/abs/2312.12044) |
| **Neural MMO 2.0** | 128 agents; predicate task system | Task completion | The engine had to be rewritten (3× faster) before the competition was feasible. Predicates give partial credit. | [2311.03736](https://arxiv.org/abs/2311.03736) |
| **Gym-μRTS** | Per-unit or per-cell factored actions | Win rate | Invalid-action masking gave a win rate of 0.82; with no masking it was 0.0. | [2105.13807](https://arxiv.org/abs/2105.13807) |
| **SC2LE / AlphaStar** | Feature layers or entity lists; ~300 function IDs with arguments | Win/loss, with the Blizzard score kept separate | Interface constraints shaped results:<br>• An APM cap of 22 non-duplicate actions per 5 s.<br>• A camera interface, which lowered performance.<br>• The agent **chooses when to observe next**.<br>Training used a league with main and league exploiters. | [1708.04782](https://arxiv.org/abs/1708.04782), [AlphaStar PDF](https://storage.googleapis.com/deepmind-media/research/alphastar/AlphaStar_unformatted.pdf) |
| **OpenAI Five** | About 16K observation values; acts every 4th frame, about 20K steps per game | Win | More than 20 "surgeries" to survive game patches. The authors admit the model sees everything at once, "whereas a human needs to actively click". Restricted to 17 heroes. | [1912.06680](https://arxiv.org/abs/1912.06680) |
| **Hanabi** | Cooperative; hints cost tokens | Score | Self-play conventions collapse in cross-play, with some pairs scoring about zero. Other-Play addresses this. | [1902.00506](https://arxiv.org/abs/1902.00506), [2003.02979](https://arxiv.org/abs/2003.02979) |
| **Diplomacy** (no-press RL, Cicero, AI Diplomacy) | Simultaneous moves; language | Supply centers | Self-play alone fails with humans; piKL regularizes toward human play.<br>Cicero drew a premeditated-deception critique.<br>AI Diplomacy (2025): o3 won mainly through deception. Results are observational only. | [2210.05492](https://arxiv.org/abs/2210.05492), [Every](https://every.to/p/diplomacy) |

**Infrastructure trend.**
- JAX co-location of environment and learner:
  - [Brax](https://arxiv.org/abs/2106.13281) and [PureJaxRL](https://github.com/luchris429/purejaxrl).
  - [Craftax](https://arxiv.org/abs/2402.16801).
  - [XLand-MiniGrid](https://arxiv.org/abs/2312.12044), about 1M steps/s training per device.
- C++ pools: [EnvPool](https://arxiv.org/abs/2206.10558) reaches 1M Atari frames/s on 256 cores.
- In 2025–26, "environment as a service" for LLM RL arrived:
  - [OpenEnv](https://github.com/meta-pytorch/OpenEnv) from Meta and Hugging Face: `reset/step/state` over WebSocket, packaged as Docker images.
  - Prime Intellect's [verifiers / Environments Hub](https://www.primeintellect.ai/blog/environments): an environment is a module containing a dataset, a rollout, and a rubric, and the same entry point serves training and evaluation ([INTELLECT-3](https://arxiv.org/abs/2512.16144)).
- Railhead should ship adapters for both families: Gymnasium/PettingZoo and OpenEnv/verifiers.

---

## 2. LLM-era game environments

### 2.1 Factorio Learning Environment, the closest cousin (deep dive)

**Identity.** Hopkins, Bakler and Khan; [arXiv 2503.09617](https://arxiv.org/abs/2503.09617) (v1 only); [NeurIPS 2025 D&B](https://proceedings.neurips.cc/paper_files/paper/2025/hash/75f3a62e55187bc3e5e5961fa2a46bcf-Abstract-Datasets_and_Benchmarks_Track.html); [repo](https://github.com/JackHopkins/factorio-learning-environment).

**Action interface.**
- Python code-as-action in a REPL. One "step" is one submitted program, which returns stdout, stderr, reward and in-game time.
- About 23–27 API methods, including `nearest`, `get_entities`, `can_place_entity`, `place_entity`, `place_entity_next_to`, `connect_entities`, `craft_item`, `insert_item`, `set_entity_recipe`, `set_research` and `sleep`.
- Methods return typed objects.
- The namespace persists across steps and acts as "episodic symbolic memory", but stored objects are stale snapshots.
- Long-running programs are killed.
- The client and server talk Python ↔ Lua over RCON, about 80k lines of code.

**Observation.**
- The agent sees whatever it prints. There is no free global state dump.
- In the paper, Claude spent 43% of its lines on prints and GPT-4o leaned on asserts.
- v0.3.0 (Oct 2025) added structured fields: entities, inventory, research, flows, task_verification, map_image, and others. It also went headless, added a Gym interface, and the `fle eval` CLI ([v0.3.0 notes](https://jackhopkins.github.io/factorio-learning-environment/versions/0.3.0.html)).

**Production Score (PS).**
- Definition: PS(t) = Σᵢ V(i)·(Pᵢ(t) − Cᵢ(t)), the value of production net of consumption.
- Item value is the cheapest recipe cost: ingredient values times a complexity multiplier β^(n−2), with β≈√1.025, plus an energy term.
- Raw-resource seed prices are fixed constants.
- The scale spans orders of magnitude. A rocket launch is about 1.2×10⁸. The human record is about 3×10¹². LLM factories landed at about 3×10⁴ to 2×10⁵ (camera-ready appendix A, via research pass).
- The authors concede the pricing was "designed for gameplay progression rather than physical realism."

**Settings.**
- **Lab-play:**
  - Full inventory and the complete tech tree.
  - 24 throughput targets (arXiv v1). The camera-ready has 33 tasks in three families, adding troubleshooting broken human factories and visual-spatial multiple choice.
  - Targets are checked over a 60 s in-game holdout, so the measure is steady state, not a snapshot.
  - 128 steps per task in the paper. v0.3 used 64 steps and pass@8.
- **Open-play:**
  - "Build the largest factory possible" on a procedural map.
  - 5,000 steps, 8 runs, median reported.
  - Milestones (first production of each item, techs researched) track tech-tree coverage.

**Results.**

| Setting | Version | Result |
|---|---|---|
| Lab-play | Paper | Claude 3.5 Sonnet best at 7/24 tasks. GPT-4o and DeepSeek-v3 5/24. Llama-3.3-70B 2/24. A human using the same API solved 20/24. |
| Lab-play | v0.2 | Backtracking took Claude 3.5's mean success from 21.9% to 28.1%. Claude 3.7 scored 29.1 ([v0.2 notes](https://jackhopkins.github.io/factorio-learning-environment/versions/0.2.0.html)). |
| Lab-play | v0.3 | Opus 4.1 about 16/24, GPT-5 about 15/24 (secondary reading of an image-only table, UNVERIFIED). |
| Troubleshooting | Paper | Claude F1 0.24, with no gain from vision. |
| Open-play | Paper | Claude 3.5 Sonnet median PS about 2.9×10⁵. Electric drilling discovered around step 3,000. |

**Documented failure modes.**
- **Spatial.** "placing entities too close or on-top of each other, not leaving room for connections".
- **Debug loops.** GPT-4o misused one API method for 78 contiguous steps.
- **Breaking what works.** Agents dismantled working structures.
- **Giving up.** GPT-4o-mini "repeatedly asked to be reset".
- **Objective-setting is its own skill.** DeepSeek did well in lab-play but rarely built anything in open-play without a set goal.
- **Knowing-doing gap.** The appendix borrows BALROG's term: models know the ratios but can't execute them.
- **Reward hacking.** In v0.3, agents ferried items by hand and used chests as buffers. Successful lab-play runs were manually inspected for hand-feeding.

**Cost and throughput.**
- The headless server averages 218 operations/s. With the Python layer that drops to about 68/s.
- `connect_entities`, which does the pathfinding, runs at 25–48/s.
- About 95% of wall time is LLM sampling.
- Open-play cost $8,857 in total. Claude 3.5 Sonnet cost $4,590 for 8×5,000 steps, about $0.11 per step.
- v0.4.x (Sept 2026) moved to Factorio 2.0.73 and an Inspect-based runner, and cut a 1,600-entity save from 10.5 s to 0.52 s ([CHANGELOG](https://github.com/JackHopkins/factorio-learning-environment/blob/main/CHANGELOG.md)).

**Stated limitations and outside critiques.**
- The API differs from the human GUI.
- Single-agent experiments only. v0.2 added multiplayer, but agents "fully yield to each other".
- No trains, bots or circuits in v1.
- Hacker News commenters called "largest factory" vague and argued most of Factorio could be solved by GOFAI ([HN thread](https://news.ycombinator.com/item?id=43331582)).
- My own critique: `connect_entities` pre-solves routing, which is the hardest spatial subproblem. The headline score therefore under-measures spatial reasoning.

**What FLE said about OpenTTD.**
- arXiv v1, related work: "Management simulation games like OpenTTD have explored aspects of resource optimization, but lack precise mechanics and exponential scaling that would enable quantitative measurement of small improvements in agent capabilities."
- The sentence is absent from the camera-ready (research pass grep).
- Railhead's answer:
  - OpenTTD's payment, value and rating formulas are exact integer code (section 3.3).
  - Company value is unbounded and compounds with reinvestment.
  - OpenTTD lacks Factorio's recipe-tree exponential, so a log-scale headline metric plus milestones is the right design (section 6).

**Follow-ons.**
- [Factoriax](https://arxiv.org/abs/2610.05569) (Oct 2026) is a JAX Factorio-like simulator running 1B PPO steps in about 8 minutes on an A100. PPO learns to gather, craft and place machines, but not to arrange them into a working layout.
- Spatial layout is the wall for both RL and LLMs.

### 2.2 Other LLM-era game and long-horizon business benchmarks

| Benchmark | Interface and time | Score and evaluation | Key finding or lesson |
|---|---|---|---|
| **BALROG** ([2411.13543](https://arxiv.org/abs/2411.13543)) | 6 games (BabyAI → NetHack); text, or text plus an image; one call per step | 0–100 per game. NetHack progress comes from human-data win probability by dungeon and experience level. | **Knowing-doing gap.** Models describe dangers correctly, then walk into them.<br>**Images often hurt.**<br>**Saturation.** The live [leaderboard](https://balrogai.com) shows BabyAI and BabaIsAI at 100 for top models while NetHack sits at 13.2 (GPT-6-Astra-Max) and 7.3 (Claude-Opus-5-Max). Report per environment; never average saturated with unsaturated. |
| **TextArena / SPIRAL** ([2504.11442](https://arxiv.org/abs/2504.11442), [2506.24119](https://arxiv.org/abs/2506.24119)) | 57+ text games; online TrueSkill | Self-play RL on TicTacToe, Kuhn Poker and negotiation | Zero-sum self-play gave up to 10% gains on reasoning benchmarks. Only tiny, short-horizon games were used. |
| **VideoGameBench** ([2505.18134](https://arxiv.org/abs/2505.18134)) | Raw frames, real time; 3 secret held-out games | Checkpoint matching against walkthroughs | Best full score 0.48%. "Lite", which pauses during inference, reached 1.6%. Real time mostly measures latency. |
| **lmgame-Bench** ([2505.15146](https://arxiv.org/abs/2505.15146)) | Gym API; perception, memory and reasoning harness modules | Per-game | 40% of runs without the harness failed to beat random play. Contamination (Ace Attorney) was detected and then neutralized by entity masking. Game RL transferred to planning tasks, not math. |
| **Kaggle Game Arena** ([2609.31473](https://arxiv.org/abs/2609.31473)) | Chess, poker, Werewolf | Bradley–Terry with bootstrap CIs; duplicate (mirrored) poker decks; Werewolf separates skill from role luck | Duplicate formats are the variance-reduction tool for stochastic multi-agent games. |
| **Claude and Gemini Plays Pokémon** ([Gemini harness](https://blog.jcz.dev/the-making-of-gemini-plays-pokemon), [Gemini 2.5 report](https://arxiv.org/abs/2507.06261), [Claude finish](https://www.lesswrong.com/posts/sehJYg5Yny9fvpbpt/a-year-late-claude-finally-beats-pokemon)) | Screens plus RAM-to-text plus tools (pathfinder, map memory, critic) | Badges; n=1 runs | The Gemini report documents "agent panic", loops beyond about 100k tokens, context poisoning, and topological traps. The open question is whether the harness did the work. Its author says "Please don't consider this a benchmark." |
| **CivRealm** ([2401.10568](https://arxiv.org/abs/2401.10568)) | Freeciv; tensor API and language API | 16-component score | Neither RL nor LLM agents make real progress in the full game. Growing faster triggered barbarian invasions, an emergent engine penalty. |
| **CivBench, Civ VI** ([2609.02459](https://arxiv.org/abs/2609.02459)) | MCP tools; 300+ turns | Proactive Monitoring Rate; RAG@10 (are plan commitments executed within 10 turns?) | "aggregate outcomes do not reliably discriminate models at this scale". Agents under-query latent state and drop their own commitments. **Use process metrics.** |
| **Vending-Bench 1 / 2 / Arena** ([2502.15840](https://arxiv.org/abs/2502.15840), [VB2](https://andonlabs.com/evals/vending-bench-2), [Arena](https://andonlabs.com/evals/vending-bench-arena)) | Email and tools; LLM-simulated suppliers and customers; one simulated year | Final money balance | **v1:** "meltdown" loops; collapse did not correlate with full context (r=0.167).<br>**v2:** charges $100 per million output tokens against the agent's balance. Leader GPT-6 Astra about $15.5k against an estimated ceiling of about $63k per year.<br>**Arena:** cartels, threats and fabricated quotes, assessed by reading emails. Andon Labs calls this evidence "anecdotal".<br>**Caveat:** LLM-simulated counterparties make runs non-reproducible. |
| **Project Vend 1 and 2** ([1](https://www.anthropic.com/research/project-vend-1), [2](https://www.anthropic.com/research/project-vend-2)) | A real shop | Profit | Sold below cost under social pressure. A same-model overseer shared the operator's blind spots. "Bureaucracy matters." |
| **Business Arena** ([2608.08621](https://arxiv.org/abs/2608.08621)), **EnterpriseArena** ([2603.23638](https://arxiv.org/abs/2603.23638)), **CoffeeBench** ([2606.16613](https://arxiv.org/abs/2606.16613)) | Bespoke, non-spatial business sims | Net worth or survival | ~9× spread across 15 models, all below human strategies. Mechanism ablations rule out simulator shortcuts. CoffeeBench uses fixed reference competitors. |
| **FM-Bench** ([2608.18423](https://arxiv.org/abs/2608.18423), Aug 2026) | Football-club management. 20 in-game years, about 340–400 decision stops, 26 tools. Solo track against a frozen scripted world, plus an Arena where 15 models and a scripted anchor share one world. 3 seeds. | A deterministic engine produces the score. No LLM judge. | **The closest long-horizon competitive management benchmark, and it shows a deterministic score with competitors is already done** (outside transport).<br>• No model learned the hidden market prices, even after hundreds of rejected bids.<br>• The ranking settles only late in the horizon.<br>• The Arena title rotated among ten models.<br>• Top models pull back slow-payoff investment near the end, so watch for end-of-horizon gaming.<br>• Memory fails two ways: an ever-growing archive, or a plan rewritten every season.<br>• Token spend predicts nothing. |
| **CodeClash** ([2511.00839](https://arxiv.org/abs/2511.00839)), **LLM Skirmish** ([site](https://llmskirmish.com/)) | The LLM edits a codebase, which then competes autonomously | Tournament wins | Repos get messier with every round. Top models lose every round to expert human programmers. Matches are short. |
| **Others** | | | [StarDojo](https://arxiv.org/abs/2507.07445) (Stardew Valley), [Orak](https://arxiv.org/abs/2506.03610), [TheAgentCompany](https://arxiv.org/abs/2412.14161) (checkpoint partial credit), [τ-bench](https://arxiv.org/abs/2406.12045) (pass^k), [Project Sid](https://arxiv.org/abs/2411.00114), [MINDcraft](https://arxiv.org/abs/2504.17950) (communication is the bottleneck). |

### 2.3 World models as environments

- Genie 2 holds together for about a minute ([Genie 2](https://deepmind.google/discover/blog/genie-2-a-large-scale-foundation-world-model/)).
- Genie 3 runs 720p at 24 fps for "several minutes". It supports promptable world events, and SIMA was tested inside it. DeepMind itself lists a constrained action space and limited multi-agent interaction as limits ([Genie 3](https://deepmind.google/discover/blog/genie-3-a-new-frontier-for-world-models/)).
- Related work: [GameNGen](https://arxiv.org/abs/2408.14837) (Doom), [Oasis](https://oasis-model.github.io/) (about 3 s memory, "no physics engine"), and [DIAMOND](https://arxiv.org/abs/2405.12399).
- **Implication.** A learned simulator cannot be the ground truth for an economy that needs exact bookkeeping over decades. In Railhead the deterministic engine is the asset. World models matter only as cheap learned proxies for model-based RL, and those can be validated against forks of the real engine (section 5, idea 1).

### 2.4 Training vs evaluation

Every published *training* use of games for LLM RL is short-horizon (tens of steps):
- [RAGEN](https://arxiv.org/abs/2504.20073) (Sokoban, FrozenLake; "Echo Trap" collapse)
- SPIRAL
- lmgame (Sokoban, Tetris)
- [ViGaL](https://arxiv.org/abs/2506.08011) (Snake)
- [GEM](https://arxiv.org/abs/2510.01051)
- [AgentGym-RL](https://arxiv.org/abs/2509.08755), which grows the horizon gradually
- OpenEnv's TextArena and OpenSpiel environments

FLE, Vending-Bench, CivBench and Pokémon are evaluation-only in the literature. Lab-internal training is undisclosed. **Nobody has published LLM RL on a long-horizon economic or construction game.**

---

## 3. Management, city and transport sims, and exactly what exists for OpenTTD

### 3.1 Adjacent prior work

**SimCity / Micropolis**
- [gym-city](https://github.com/smearle/gym-city) (Earle, EXAG 2019 / [arXiv 2002.03896](https://arxiv.org/abs/2002.03896)): fractal networks trained on 16×16 maps and run on 32×32. Includes human-agent co-play demos.
- The PCGRL line treats design as an MDP: [2001.09212](https://arxiv.org/abs/2001.09212), [controllable PCGRL](https://arxiv.org/abs/2105.02993) (SimCity and RCT modes), [PCGRL+](https://arxiv.org/abs/2408.12525).
- No later Micropolis RL or LLM paper was found.

**Cities: Skylines.** No peer-reviewed RL or LLM-agent work was found. Related urban-planning work:
- DRL urban planning on real geography ([Nature Comp Sci 2023](https://link.springer.com/10.1038/s43588-023-00503-5)).
- An LLM participatory-planning paper ([2402.17161](https://arxiv.org/abs/2402.17161)).
- The "SimCity" multi-agent macro-economy paper ([2510.01297](https://arxiv.org/abs/2510.01297)), which is unrelated to the Maxis game.

**Flatland, the most relevant prior art for rail** (environment paper [2012.05893](https://arxiv.org/abs/2012.05893); NeurIPS 2020 report [2103.16511](https://arxiv.org/abs/2103.16511)).
- **Environment design:**
  - 5 discrete actions.
  - A tree observation from a 4-branch search over the rail graph.
  - A shaped reward.
  - Hidden malfunctions.
  - Test instances scaled from 1 to 6,256 trains.
  - 10 s per step budget, with early-stop thresholds.
- **NeurIPS 2020 result.** An OR entry (prioritized planning with SIPP, plus LNS) scored 297.5 with 98.6% of trains arriving. The best RL entry (PPO with attention communication and a departure classifier) scored 214.2 with 78.5%.
- **Authors' lessons:**
  - OR searches the joint configuration space centrally.
  - Deadlocks cascade ("domino effect").
  - Observation design "has proven to be difficult".
  - Departure scheduling drives much of the result.
  - RL agents were too cautious on big maps.
- **Since then:**
  - [Maze-Flatland (2026)](https://arxiv.org/abs/2605.10257) splits dispatch from routing and roughly doubles arrivals over monolithic RL.
  - [DISPLIB 2025](https://arxiv.org/html/2509.12254v1) was won by branch-and-bound.
- **Lessons for Railhead:**
  - Ship OR baselines.
  - Make deadlock rate a first-class metric.
  - Act at decision points.
  - Separate rare high-impact decisions from frequent routine ones.
  - Flatland never lets the agent *design* the network (section 5, idea 3).

**Traffic and logistics.**
- [CityFlow](https://arxiv.org/abs/1905.05217) and [sumo-rl](https://github.com/LucasAlegre/sumo-rl) are the main traffic simulators. RL signal controllers "have never been deployed" ([2206.11996](https://arxiv.org/abs/2206.11996)).
- [OR-Gym](https://arxiv.org/abs/2008.06319) and [MABIM](https://arxiv.org/abs/2306.07542): MARL is unstable at scale.
- [Amazon deep inventory RL](https://arxiv.org/abs/2210.03137) shows the value of exogenous logged data plus a simulator for trustworthy counterfactual backtests. In a live test (10K products, 26 weeks) it cut inventory about 12% with no significant revenue change.

**Economic simulations.**
- The [AI Economist](https://www.ncbi.nlm.nih.gov/pmc/articles/PMC9067926/) has a planner that sets taxes while worker agents learn to game them, a two-level RL setup.
- [EconAgent](https://aclanthology.org/2024.acl-long.829) uses LLM households.
- [ABIDES-Gym](https://arxiv.org/abs/2110.14771) is a discrete-event market simulator.

### 3.2 OpenTTD prior art (complete as far as search could establish)

| Project | What it actually is (verified by fetch) | Scope | Results |
|---|---|---|---|
| **[deepsaia/nttd](https://github.com/deepsaia/nttd)** (Apache-2.0; created 2026-03-10; 531 commits; Zenodo DOI 10.5281/zenodo.22105381) | **The most complete prior art. It is a benchmark engine, not an agent.**<br>• **Engine:** OpenTTD 15.3 dedicated server plus an `nttd-gs` GameScript.<br>• **Interface:** JSON HTTP API on :8000, three route tiers (operator, participant, public), MCP, and a Gymnasium-style `NttdEnv`. The env's 10-float observation is "a convenience for a baseline"; the full snapshot is in `info`. Actions are a sequence of text commands, and reward is computed client-side by design ([env.py](https://raw.githubusercontent.com/deepsaia/nttd/HEAD/src/nttd/rl/env.py)).<br>• **Modes** ([play_modes.md](https://raw.githubusercontent.com/deepsaia/nttd/HEAD/docs/play_modes.md)): *stepped* pauses between steps, and `POST /step` is a synchronous barrier. *Real time* runs at 1 wall-minute per economy month, and observations lag own actions by about 7 s.<br>• **Tiers:** T1–T4 are 1, 2, 5 and 10 game years.<br>• **Worlds:** 25 scored world families (5 sizes × 5 terrains, square, temperate locked, start 2020, any seed). "Scoredness" is computed from config, and a scored session refuses operator mutations.<br>• **Contestants:** human, LLM, multi-agent, RL and ES are recorded alike, but **only one contestant company per map**; extra companies are idle placeholders.<br>• **Scoring is inconsistent.** The docs say "The board ranks `company_value`", with the native rating shown alongside. The code's docstring says the native rating is the primary score and company value is display-only ([score.py](https://raw.githubusercontent.com/deepsaia/nttd/HEAD/src/nttd/analysis/score.py)). It also admits v1 scored every run 0.<br>• **Actions:** rail track, signals, stations, depots, `build_train`, road, marine, air, groups, autoreplace, and `build_path` (agent-designed route) vs `connect_rail`/`connect_road` (nttd picks the route) ([actions.md](https://raw.githubusercontent.com/deepsaia/nttd/HEAD/docs/actions/actions.md)). | All modes, including trains and signals. macOS-only, Python 3.13+. | Result plots in `docs/images`. `nttd publish` files a PR to a "board" (needs a Hugging Face token). **No populated public leaderboard or paper found.** 3★. |
| [graweedman/OpenTTD_RL](https://github.com/graweedman/OpenTTD_RL) | An OpenTTD fork whose About text says "with Reinforcement Training Module". The README is upstream's; no interface is documented. | Unknown | None. 1★. |
| [DingoOz/openttd_llm_player](https://github.com/DingoOz/openttd_llm_player) ("ottd-bench") | Vision LLM harness. Admin port, plus an `LLMBridge` GameScript, plus an idle AI that exists only so the company exists. Screenshots carry tile overlays. Tools include `look`, `view_map`, `build_road` (pathfinder built in) and `wait(days)`. The game pauses between turns. Score is net worth = cash − loan + vehicle resale. Logs tool error rate and idle turns. OpenTTD 15.3 pinned. | Road and air only. "No trains or ships". Maps ≤256². | None published. 0★. |
| [green-dalii/openttd-agent](https://github.com/green-dalii/openttd-agent) | TypeScript harness. Bridge GameScript plus Executor AI. Each decision gets a snapshot plus `sinceLastDecision` diffs. The world keeps running by default. "Memory-evolution" across runs, with no weight updates. | Buses only | **Negative:** "every measured run ends with the company losing money". About 70% of 500 s runs deliver nothing. Earlier comparisons had 8–20% statistical power. The only project that reports power. |
| [jplattel blog](https://jplattel.nl/post/2026-01-22-llm-plays-openttd/) | LLM-written Squirrel AIs: "half of the time it wouldn't compile", and otherwise crashed the game. Switched to structured commands plus log parsing. Proposes an "OpenTTDBench". | Proof of concept: reads map size and balance | None |
| [quodlibetbv/openttd_llm](https://github.com/quodlibetbv/openttd_llm) ("OpenTTD Model Arena") | Windows-first. Fixed YAML scenarios, immutable manifests, `verify-run`, and replay of accepted actions. | `road-profit-v1` | None. 0★. |
| [walter-grace/agent-openttd-arena](https://github.com/walter-grace/agent-openttd-arena) | MCP server; a ~900-line Squirrel executor fed through a sign "mailbox"; a Google Maps → heightmap + towns scenario builder. Persistent public servers and agent payments are **planned only**. | Road | None. 5★. |
| [dylanfetch/openttd-mcp](https://github.com/dylanfetch/openttd-mcp), [kovan/openttd-mcp](https://github.com/kovan/openttd-mcp) | MCP servers (research pass; not re-fetched by me) | Buses and trucks | None |
| Classical AIs | trAIns, an A*-based rail AI ([SBGames 2009 PDF](https://homepages.dcc.ufmg.br/~chaimo/public/SBGames09a.pdf)). DTU thesis ([PDF](https://www2.imm.dtu.dk/pubdb/edoc/imm6091.pdf)). The TJIP 2008 programmer challenge, won by "Rondje om de kerk" ([forum](https://www.tt-forums.net/viewtopic.php?p=759121)). Community AIs such as NoCAB ([forum](https://www.tt-forums.net/viewtopic.php?p=742175)). Road-vehicle AI comparisons on fixed seeds ([forum](https://www.tt-forums.net/viewtopic.php?p=1171248)). An AI-ranking-server proposal ([forum](https://www.tt-forums.net/viewtopic.php?p=1227538)). | Squirrel NoAI API | Forum-level only |

**What does not exist:**
- No peer-reviewed RL or LLM paper on OpenTTD. A search for an OpenTTD LLM benchmark on arXiv in 2026 returned nothing.
- No PettingZoo or multi-agent environment.
- **No evaluation with several agent-controlled companies in one world.** nttd refuses this on purpose.
- No published, populated model comparison.
- No forking, replay-verification or branch-point tasks.
- No conduct ledger.
- No verified-throughput or deadlock rail tasks, even though nttd exposes the rail and signal actions.

**What does exist:**
- **nttd**, a well-engineered single-company benchmark engine. Credit what it already does well:
  - a synchronous stepped barrier;
  - computed "scoredness";
  - pinned world families;
  - honesty about score bugs.
- A crowded hobby layer, all built on the same pattern: admin port, plus a bridge GameScript, plus an executor AI.

**Where Railhead must differ from nttd:**
- Multiple contested companies in one world.
- Priced forks, plus replay-verified branch-point tasks.
- Verified rail design tasks.
- A conduct ledger.
- A log-scale steady-state headline score instead of an end-of-run value or a capped rating.
- Linux and headless operation at scale.
- Published, power-analyzed results.

nttd's T1 analysis independently confirms the native rating is a poor short-horizon headline: `SCORE_MIN_PROFIT` is always 0 in a one-year run, because only vehicles older than two years count.

### 3.3 OpenTTD engine affordances (verified)

| Affordance | Evidence | Railhead use | Caveat |
|---|---|---|---|
| **Lockstep determinism** | Clients "execute the command simultaneously in the same network frame in the same order". Desync detection compares RNG state ([desync.md](https://github.com/OpenTTD/OpenTTD/blob/master/docs/desync.md)). | Reproducible runs and exact paired comparisons | NewGRF cache bugs and undefined-order sorts can desync. Pin builds. |
| **Command log and replay** | `-d desync=2/3` writes `commands-out.log`. Level 3 also writes periodic `dmp_cmds_*.sav`. Replay needs a `DEBUG_DUMP_COMMANDS` build of the same version, and a mismatch triggers `NOT_REACHED` (same doc). | Replay-verified scores; branch-point task mining | "AI/GameScript state… scripts are not re-run during replay". Needs a compile flag. The dumps "take a lot of disk space". |
| **Admin port** (TCP 3977) | Subscriptions: Date, CompanyEconomy, CompanyStats, Chat, Console, CmdLogging. CmdLogging carries client ID, company, command ID, data and frame. RCON. JSON in both directions via `AdminGameScript` / `ServerGameScript` ([admin_network.md](https://github.com/OpenTTD/OpenTTD/blob/master/docs/admin_network.md), [tcp_admin.h](https://raw.githubusercontent.com/OpenTTD/OpenTTD/master/src/network/core/tcp_admin.h)) | Out-of-process observation and control channel | The command-logging payload is "for logging purposes only" and can change without notice. |
| **Deity GameScript** | `GSCompanyMode`: "All actions performed within the scope of this mode, will be executed on behalf of the company you switched to." Costs are charged to that company ([docs](https://docs.openttd.org/gs-api/classGSCompanyMode)). `GSLeagueTable` provides in-game scoreboards ([docs](https://docs.openttd.org/gs-api/classGSLeagueTable)). `OWNER_DEITY` = 0x12 ([company_type.h](https://raw.githubusercontent.com/OpenTTD/OpenTTD/master/src/company_type.h)). | One GameScript can broker actions for many agent-controlled companies, plus an in-game scoreboard | Script `Save()` stores tables only, not class instances. "Pending events are lost when a game is loaded" ([GSController](https://docs.openttd.org/gs-api/classGSController)). |
| **Headless fast-forward** | The null video driver takes a `ticks` parameter (default 1000) and loops `GameLoop()` with no sleep ([null_v.cpp](https://raw.githubusercontent.com/OpenTTD/OpenTTD/master/src/video/null_v.cpp)). | Forks and batch evaluation | **Ticks/s is UNMEASURED.** Benchmark it first (section 7). |
| **Time base** | `DAY_TICKS = 74` ([timer_game_tick.h](https://raw.githubusercontent.com/OpenTTD/OpenTTD/master/src/timer/timer_game_tick.h)). Since 14.0 the calendar can be slowed or frozen. That affects vehicle introduction dates, inflation and aging, but not vehicle movement, cargo or town growth ([14.0 release](https://www.openttd.org/news/2024/04/13/openttd-14-0)). | A clean on/off knob for technology and inflation drift | n/a |
| **Exact economics** | Cargo payment = distance × time factor (255 down to a floor of 31, then decaying toward 1 past the cargo's transit thresholds) × amount × price. Company value counts vehicles at value×1.5, plus stations, plus cash, minus loan ([economy.cpp](https://raw.githubusercontent.com/OpenTTD/OpenTTD/master/src/economy.cpp)). | Verifiable, decomposable scoring | NewGRF profit callbacks can replace the formula. |
| **Native 1000-point performance rating** | Vehicles 100, Stations 100, MinProfit 100, MinIncome 50, MaxIncome 100, Delivered 400, Cargo 50, Money 50, Loan 50, each capped at a "needed" threshold (same file) | A built-in milestone vector | **It saturates.** For example, Delivered caps at 40,000 units per year. Not suitable as the headline score. |
| **Inflation** | Applied only during the first 170 years, to avoid overflow and costs diverging from income (same file) | Long-run non-stationarity | n/a |
| **Typed events** | `AIEventType` includes VEHICLE_UNPROFITABLE, INDUSTRY_CLOSE, ENGINE_AVAILABLE, COMPANY_IN_TROUBLE, COMPANY_ASK_MERGER, ROAD_RECONSTRUCTION, EXCLUSIVE_TRANSPORT_RIGHTS, SUBSIDY_* ([docs](https://docs.openttd.org/ai-api/classAIEvent)) | An event-driven time model | n/a |
| **Ethically loaded native actions** | Bribe the local authority (a regional investigator can catch it), buy exclusive transport rights (rivals not notified), fund road reconstruction (disrupts road traffic) ([wiki](https://wiki.openttd.org/en/Manual/Local%20authority), research pass) | Engine-verifiable conduct | n/a |
| **Many companies, mixed control** | Up to 15 companies; humans, Squirrel AIs and GameScript coexist | Cross-paradigm leagues | Real-time multiplayer conflicts with LLM latency |
| **Cargodist link graph** | The flow job runs in a separate thread and is joined after a configured number of in-game days. "if it hasn't finished by then the game will hang" ([linkgraph.md](https://raw.githubusercontent.com/OpenTTD/OpenTTD/master/docs/linkgraph.md)). | Endogenous passenger and cargo routing demand | **Throughput risk.** Fast-forward can stall on the join. |

---

## 4. Taxonomy of environment design dimensions

Each row gives where the field sits, what has been tried, and what is untried, then Railhead's stance.

| Dimension | Where the field sits | Tried | Untried / gap | Railhead stance |
|---|---|---|---|---|
| **Observation** | Pixels → symbolic → text → queryable | Pixels (ALE, Procgen, VideoGameBench); feature layers (SC2LE); entity lists (AlphaStar, OpenAI Five); text (BALROG); structured JSON (FLE v0.3, openttd-agent diffs); code-queried state (FLE prints) | Observation as a **priced query language with fidelity tiers**. Observations of **counterfactual futures**, sampled from the true simulator. | Query-first, structured; pixels optional |
| **Action** | Atomic → parameterized → macro → code → natural language | Atomic (ALE); function plus arguments (SC2LE); masked factored (μRTS); macro tools (Pokémon, ottd-bench); code REPL (FLE, Voyager); dialogue (Cicero); program-as-deliverable in short matches (CodeClash) | Multiple resolutions **compiled to one logged command stream**, so trajectories convert exactly between levels. Programs that run autonomously for decades in an economy. | All three layers, equivalence-checked |
| **Time model** | Fixed step, turn-based, or real time | Frameskip plus sticky actions (ALE); real time (VideoGameBench); agent-chosen next-observation time (AlphaStar); pause while thinking (ottd-bench); nttd's paired stepped (paused, synchronous barrier) and real-time (1 wall-minute per economy month) modes; world keeps running (openttd-agent); `wait_for_next_day` (Vending-Bench); decision-point skipping (Flatland); discrete-event (ABIDES) | **Agent-authored interrupt predicates** over an engine event bus, with continuous control delegated to engine automation, and attention priced | Event-driven SMDP, plus a tick-synchronous mode for RL |
| **Reward / score** | Score, sparse, shaped, achievements, unbounded log-scale | Game score; win/loss; shaped (Flatland); geometric-mean achievements (Crafter); production score (FLE); net worth (Vending-Bench); human-calibrated progression (BALROG); learned win probability (CivBench Civ V); vector rewards ([MO-Gymnasium](https://proceedings.neurips.cc/paper_files/paper/2023/hash/4aa8891583f07ae200ba07843954caeb-Abstract.html)); process metrics (CivBench Civ VI) | **Dual ledger:** profit and conduct, both computed from the engine log. Steady-state, audited headline. | Log company value over a trailing window; milestones; conduct ledger |
| **Task specification** | Fixed, procedural, language, open-ended | Procedural seeds (Procgen, NLE); NL tasks (BASALT, MineDojo); predicate composition (XLand, NMMO 2.0); LLM-generated environments ([OMNI-EPIC](https://arxiv.org/abs/2405.15568), [EnvGen](https://arxiv.org/abs/2403.12014)); lab-play vs open-play (FLE) | **Branch-point tasks** cut from real recorded games. **Parametric rule-perturbation families.** | Lab, open, and branch-point tasks |
| **Multi-agent** | AEC/parallel; leagues; held-out co-players | Leagues with exploiters (AlphaStar); held-out bots (Melting Pot); cross-play (Hanabi); LLM arenas (Game Arena, Vending-Bench Arena); populations (NMMO, Project Sid) | **Cross-paradigm** (LLM + RL + scripted + human) in one ground-truth economy with full logs | 15-company lockstep worlds |
| **Determinism and reproducibility** | Deterministic sims get exploited; LLM-simulated worlds aren't reproducible | Sticky actions (ALE); randomized starts (SMACv2); version freezes (NLE v0.7.3); LLM counterparties (Vending-Bench, non-reproducible) | **Replay-as-proof:** a claimed score is verified by re-simulating the submitted command log. Common for tool-assisted speedruns; not standard in RL benchmarks. | Determinism for verification; seeded perturbation for evaluation |
| **Counterfactual / fork access** | Free in training, banned or absent at test | ALE `cloneState`; OpenSpiel `clone` and `resample_from_infostate`; Gymnasium `FuncEnv`; JAX pytrees; Go-Explore (training only); NLE bans save-scumming at test; FLE backtracking; [Koh et al.](https://arxiv.org/abs/2407.01476) free resets; [Thinker](https://arxiv.org/abs/2307.14993) and [Hamrick 2017](https://arxiv.org/abs/1705.02670) price *learned*-model queries | **Priced, resampled, ground-truth forks as an evaluation-time action** | Core feature (section 5, idea 1) |
| **Information acquisition cost** | Mostly free omniscience | AlphaStar camera and chosen observation timing; Hanabi hint tokens; tabular costly-observation POMDPs ([AMRL](https://arxiv.org/abs/2005.12697), [Act-Then-Measure](https://ojs.aaai.org/index.php/ICAPS/article/view/27197)); [EcoAgent-Bench](https://arxiv.org/abs/2608.05519) (priced tools, not a game); Vending-Bench 2 (output tokens charged) | Priced observation in a rich game. One unified "cognition budget" covering queries, forks and tokens. | Unified budget, with free and priced tracks |
| **Evaluation protocol** | Held-out seeds plus robust statistics | Seed splits (Procgen, NLE); held-out populations; secret games (VideoGameBench); entity masking (lmgame); IQM and CIs (rliable); low percentiles (XLand); duplicate formats (Game Arena); power analysis (openttd-agent only) | **Power-analyzed run counts** in the spec. Rule-perturbation memorization index. Branch-point paired evaluation. | All of the above |
| **Compute / throughput** | JAX at 10⁶–10⁹ steps/s; LLM environments token-bound | Craftax; EnvPool; NLE at 14.4K steps/s; MineRL at 60 steps/s; FLE 68–218 ops/s with 95% of time in LLM sampling, about $0.11/step | Cheap evaluation through **token-free autonomous programs** (section 5, idea 4) | Measure null-driver ticks/s first |
| **Harness as part of the system** (added) | Unversioned, debated | lmgame on/off ablation; Pokémon harness debate | A **standardized ablation ladder** over harness abstraction levels | Version and ablate the harness |

---

## 5. Frontier opportunities, ranked

**Ranking criterion:** novelty × how much it depends specifically on OpenTTD × research value ÷ feasibility risk. Each item names the closest prior work found and says how Railhead differs. Ideas that are renamings of existing work were killed; see the list after idea 12.

### 1. Priced, resampled ground-truth forks: a "counterfactual budget"

- **Idea.** Expose `fork(plan, horizon, n)` as an action.
  - The environment snapshots the savegame and runs the agent's candidate plan for k game-days headless, n times.
  - Each run reseeds the hidden random streams: industry production changes, breakdowns, town growth, competitor AI choices.
  - It returns outcome distributions, not one future.
  - Every fork is charged against the same budget as play, for example game-days of delay or a compute allowance that enters the score.
  - Report score against fork-budget curves. Forks also serve as an exact counterfactual credit oracle during training ("what if this route had not been built").
- **Closest prior.**
  - **Learned models.** Thinker (2023) gives imaginary steps in a learned model. Hamrick et al. (2017) price queries to learned experts.
  - **Free resets in training only.** ALE/Go-Explore restore exact states for free, and only in training. NLE bans save-scumming at test.
  - **Free resets for LLM agents.** Koh et al. backtrack LLM web agents with free resets. FLE rewinds to pre-error states as a harness feature.
  - **Models don't use resets.** [AutumnBench](https://arxiv.org/abs/2510.19788) found humans spend about 12.5% of actions on resets as experiments, while models spend under 7% and "do not treat resets as special actions".
  - **What nobody does:** price ground-truth simulation *inside evaluation*, or resample hidden randomness so a fork is evidence rather than an oracle. Resampling also defuses the Brute failure: with resampling there is no single deterministic trajectory to memorize.
- **Why OpenTTD.**
  - Exact determinism, full savegame serialization, and a no-sleep headless loop.
  - A deity GameScript can inject the plan.
  - Economic consequences show up months later, which is exactly where looking ahead pays off.
  - A game-year is about 27k ticks.
- **Risk: medium.**
  - Fork cost on large maps (save/load I/O, unmeasured ticks/s, cargodist joins).
  - Separating "hidden" RNG streams from player-observable state needs source work.
  - If forks are too cheap, search dominates. That is a finding to measure, not a bug.

### 2. Branch-point task suites from replayable command logs

- **Idea.** Mine tasks of the form (savegame at frame f, objective, horizon) from recorded games: AI-vs-AI leagues, Railhead's own runs, and consenting multiplayer servers via admin-port CmdLogging. Task families:
  - **"Rescue":** a company in trouble (`ET_COMPANY_IN_TROUBLE` fires).
  - **"Continue":** sustain growth from a mid-game state.
  - **Paired counterfactual starts:** identical frames that differ in one variable.

  Leaderboard entries are command log plus seed, re-simulated server-side, so scores are proven rather than reported.
- **Closest prior.**
  - [AlphaStar Unplugged](https://arxiv.org/abs/2308.03526): 1.4M resimulated StarCraft II replays for offline RL, but episodes start at the beginning.
  - KataGo starts self-play from SGF positions, but in a board game.
  - The NetHack Learning Dataset is ttyrec recordings that are not resimulated.
  - FLE's troubleshooting tasks are hand-built broken factories.
  - Amazon backtests on logged exogenous data.
  - **Difference:** exact mid-game branch points at scale in a deep economic sim, with replay-verified scoring.
- **Why OpenTTD.**
  - `commands-out.log` with frame numbers.
  - Periodic `dmp_cmds` saves.
  - Admin-port command copies.
  - A savegame-to-JSON reader for diffing.
- **Risk: medium.**
  - Scripts are not re-run during replay, so when AIs are present, branch from savegames rather than logs.
  - Version and NewGRF pinning.
  - Consent and licensing for human-server logs.

### 3. Design-and-operate rail networks with verified throughput and deadlock-freedom

- **Idea.** A task class where the agent designs the system and the engine judges it.
  - The agent designs track topology, signalling, orders and timetables.
  - The engine verifies **sustained** throughput over a trailing window (trains per month through a junction, cargo delivered).
  - It also verifies that no deadlock occurs over long simulated periods. A deadlock is a train held at a signal beyond a threshold.
  - **Lab version:** fixed budget, "carry X trains/month through this junction with zero deadlocks".
  - **Open version:** full company.
- **Closest prior.**
  - Flatland's network is fixed, so agents only route and dispatch. OR beats RL there (297.5 vs 214.2).
  - FLE v1 has no trains, and `connect_entities` pre-solves routing.
  - Ramp Labs' RollerCoaster Tycoon agent called path routing a "serious challenge" ([post](https://ramplabs.substack.com/p/ai-plays-rollercoaster-tycoon)).
  - DISPLIB dispatches on given networks.
  - **nttd** exposes rail track and signal actions but scores only end-of-run company value or rating. It has no throughput or deadlock verification.
  - **Difference:** joint infrastructure synthesis plus operations, with signalling semantics checked by simulation.
- **Why OpenTTD.** Trains with block and path signalling, timetables and conditional orders are OpenTTD's core gameplay, and FLE chose not to model them. Strong OR and scripted baselines exist to calibrate against.
- **Risk: medium-high.**
  - Spatial reasoning is the documented LLM bottleneck (FLE, BALROG).
  - Decide explicitly how much pathfinding the API does, and expose that as an ablation axis. This is the `connect_entities` lesson.

### 4. Program-as-company: autonomous in-engine AIs scored over decades

- **Idea.** The agent's deliverable is a Squirrel AI.
  - It runs a company with **no model in the loop** for 50–100 game-years on held-out maps.
  - It operates under the engine's per-tick opcode suspension.
  - Optional edit checkpoints every N years: the agent reads logs and ships a patch, and patches are priced.
- **Closest prior.**
  - CodeClash and LLM Skirmish: short adversarial matches.
  - FLE kills long-running programs.
  - Voyager keeps the LLM in the loop.
  - jplattel: LLM-written Squirrel "half of the time it wouldn't compile".
  - **Difference:** decades of autonomous economic control, judged head-to-head against 17+ years of human-written AIs (TJIP 2008 onward; the BaNaNaS library, count UNVERIFIED) on identical seeds. That makes it a direct comparison against human engineers.
- **Why OpenTTD.**
  - The NoAI and GameScript APIs.
  - Script Save/Load.
  - AIs are first-class companies.
  - Evaluation is token-free after authoring, so many seeds and real statistical power are affordable. That addresses the power problem openttd-agent measured.
- **Risk: medium-high.**
  - Squirrel is low-resource in pretraining.
  - Compile and crash isolation is needed (a sandboxed server, linter, and typed stubs).
  - API surface area.

### 5. Engine-verifiable conduct and promise-keeping ledger

- **Idea.** Score profit and conduct separately, both computed from the command log.
  - **Native conduct events:**
    - bribes, and whether they were caught;
    - exclusive-rights purchases;
    - road reconstruction that hurts rivals;
    - merger and takeover moves;
    - track blocking.
  - **Negotiation:** agents negotiate track access, feeder agreements or non-compete deals over admin-port chat, using a **structured contract DSL**.
  - **Promise-keeping rate:** promises compile to predicates, and the engine checks them against later commands.
- **Closest prior.**
  - Vending-Bench Arena found cartels and bribes by reading emails; its own word for the evidence is "anecdotal".
  - MACHIAVELLI annotates text-adventure actions (UNVERIFIED this pass).
  - AI Diplomacy counts lies observationally.
  - CivBench RAG@10 checks the agent's commitments to itself.
  - Melting Pot studies social dilemmas without language.
  - **Difference:** ground-truth conduct, verifiable contracts, and deterministic counterparties.
- **Why OpenTTD.**
  - The local-authority action set.
  - Typed events (`ET_EXCLUSIVE_TRANSPORT_RIGHTS`, `ET_ROAD_RECONSTRUCTION`, `ET_COMPANY_ASK_MERGER`).
  - Per-company command attribution.
  - The infrastructure-sharing setting, which lets rivals' trains use your track (engine setting, not re-fetched).
- **Risk: medium.**
  - Free-text promises need a judge, which is the reason for the contract DSL.
  - Misconduct base rates are low unless scenarios load the incentives.

### 6. A compiled multi-resolution action stack with exact equivalence

- **Idea.** Three action layers compile to one logged command stream:
  - atomic engine commands (tile-level, masked);
  - macro tools (for example "build route A→B");
  - programs.

  What this enables:
  - LLM trajectories become atomic-level demonstrations, for distilling LLM agents into RL policies.
  - Harness ablations are exact.
  - The question of how much "the API does the work" (Pokémon, FLE) becomes a measured axis rather than an argument.
- **Closest prior.**
  - CivRealm has tensor and language APIs, but they are not equivalence-checked.
  - SC2LE has raw and feature interfaces.
  - μRTS has unit-action vs grid formulations.
  - lmgame has harness on/off.
  - **nttd** already offers a two-level routing ablation: `build_path` (agent-designed route) vs `connect_rail`/`connect_road` (engine-chosen). It does not compile levels into equivalence-checked logs.
- **Why OpenTTD.** Every state change goes through a typed command attributed to a company and frame. The Squirrel APIs are already a macro layer over those commands.
- **Risk: medium.** The atomic action space is huge, so masking is mandatory: in μRTS, masking was the difference between 0.82 and 0.0. Command payload encoding is version-unstable.

### 7. Cross-paradigm lockstep league

- **Idea.** Seasonal worlds where companies run by LLM agents, RL policies, legacy NoAI scripts and humans compete.
  - Cross-paradigm ratings.
  - Held-out background populations (Melting Pot-style).
  - Exploiter agents (AlphaStar-style).
- **Closest prior.**
  - Neural MMO: episodic and homogeneous.
  - [AI Village](https://theaidigest.org/village/blog/what-we-learned-2025): cross-lab, but cooperative real-world tasks.
  - Vending-Bench Arena: LLM-only, with an LLM-simulated world.
  - Game Arena: short head-to-head games.
  - gym-city: co-play demos.
  - agent-openttd-arena: persistent servers planned only.
  - **nttd** records human, LLM, multi-agent, RL and ES contestants under one API, but deliberately allows only one contestant company per map. Cross-paradigm *rating* exists there; cross-paradigm *interaction in one world* does not.
  - **FM-Bench's Arena** puts 15 LLMs plus a scripted anchor in one deterministic 20-year world, but has no RL policies and no humans.
  - What remains novel is heterogeneous paradigms *in the same world*, with full logs, in a spatial economy.
- **Why OpenTTD.** Native multiplayer, AI and human companies in one world, dedicated servers, and admin-port streaming.
- **Risk: high.**
  - The time model conflicts. Humans need real time, and real time measures LLM latency (VideoGameBench).
  - Mitigation: agent-only seasons run tick-synchronous; mixed seasons run at reduced speed, with think-time debited in ticks.
  - Ops cost.

### 8. Parametric rule perturbation and a "memorization index"

- **Idea.** OpenTTD is heavily documented online, so LLMs bring recited strategy. Generate families of perturbed economies:
  - masked or renamed cargos;
  - permuted vehicle statistics;
  - altered payment curves;
  - rewired industry chains via NewGRF, settings and GameScript.

  Report the performance gap between canonical and perturbed rules as a dose-response curve.
- **Closest prior.**
  - lmgame entity masking.
  - VideoGameBench secret games.
  - SpireClimb using Slay the Spire 2 as out-of-training ([Epoch](https://epoch.ai/publications/ai-systems-are-improving-rapidly-at-slay-the-spire-and-its-sequel)).
  - MineRL observation re-embedding.
  - XLand-MiniGrid hidden rules.
  - **Difference:** systematic, parametric perturbation of a deep economic ruleset in the same engine.
- **Why OpenTTD.** NewGRF can redefine cargos, industries and vehicles; settings and GameScript change the economy.
- **Risk: low-medium.** Perturbations can break solvability, so certify each variant with scripted reference AIs.

### 9. Event-driven attention with engine automation as the default controller

- **Idea.**
  - The agent registers wake conditions on engine events or thresholds: unprofitable vehicle, industry closing, new engine available, station rating below x.
  - Otherwise standing policies run: orders, timetables, autoreplace, cargodist.
  - Metrics: score per decision, decisions per game-year, and event-response latency.
- **Closest prior.** AlphaStar's chosen observation timing; Flatland decision points; Vending-Bench `wait_for_next_day`; ottd-bench `wait(days)`; ABIDES discrete events.
- **Honest novelty: moderate.** The components exist. The combination, "management by exception" in a full economy, is new.
- **Why OpenTTD.** The `AIEventType` bus and rich automation.
- **Risk: low.**

### 10. Exogenous technology and economic drift as a controlled axis

- **Idea.** Run the same seed with the calendar frozen and with it running.
  - When it runs, new vehicles arrive, old ones retire, and inflation applies.
  - The difference isolates adaptation to obsolescence and price drift.
  - Long-run evaluation spans, for example, 1950 to 2050.
- **Closest prior.**
  - [NS-Gym](https://arxiv.org/abs/2501.09646): drift processes kept separate from the agent, in small environments.
  - Jelly Bean World: non-episodic.
  - Civilization tech trees: driven by the agent, not exogenous.
- **Why OpenTTD.** The 14.0 calendar slow/freeze setting, and inflation in the first 170 years.
- **Risk: low.** Novelty is modest.

### 11. GIS-grounded maps with geography-held-out splits

- **Idea.** Build real-heightmap scenarios with real town placements. Train on one region and test on another.
- **Closest prior.**
  - SUMO OSM import, CARLA and StreetLearn (traffic and navigation).
  - OpenTTD tools: [bother](https://github.com/bunburya/bother), [mapparser](https://github.com/DavidAlexanderNowak/mapparser), native town JSON import ([docs](https://github.com/OpenTTD/OpenTTD/blob/master/docs/importing_town_data.md)), and agent-openttd-arena's Google Maps builder.
- **Why OpenTTD.** The heightmap and town import pipeline already exists.
- **Risk: low.** Novelty is low-moderate. Its value is as one distribution-shift axis among several.

### 12. GameScript-authored scenarios with a regret objective

- **Idea.** An agent writes GameScript scenarios (goals, subsidies, disasters, rule tweaks) to maximize regret against solver agents, with solvability certified by scripted AIs.
- **Closest prior.** OMNI-EPIC, EnvGen, PAIRED/ACCEL, and the AI Economist planner.
- **Honest novelty: narrow.** It only adds authoring in a production game's own scripting language.
- **Recommendation:** build it as infrastructure for curricula, not as a headline.
- **Risk: medium.**

### Killed ideas: renamings of existing work

| Idea | Why killed |
|---|---|
| "LLM manager over low-level controllers" | Done by [SayCan](https://arxiv.org/abs/2204.01691), [Plan4MC](https://arxiv.org/abs/2303.16563), [SwarmBrain](https://arxiv.org/abs/2401.17749) and [MA-Gym](https://arxiv.org/abs/2510.02557). It is also the default pattern in OpenTTD hobby harnesses. |
| "Vector / decomposed reward" | Done by MO-Gymnasium, [reward machines](https://arxiv.org/abs/2010.03950) and HRA. Only *what* is decomposed (idea 5) can be new. |
| "Code-as-action REPL" | Done by FLE and Voyager. |
| "Unbounded log-scale production score" | Done by FLE. Table stakes, not frontier. |
| "LLM-generated curricula or environments" | Done by OMNI-EPIC, EnvGen and PAIRED/ACCEL (see idea 12). |
| "Cross-episode memory / reflection" | Done by Voyager and Reflexion, and openttd-agent already does "memory-evolution". |
| "Charge tokens in-world" | Done by Vending-Bench 2. Folded into idea 1's unified budget. |
| "Multi-agent LLM market shows collusion" | Done by Vending-Bench Arena. Only engine-verifiability (idea 5) is new. |
| "First OpenTTD benchmark / Gym env for OpenTTD" | Done by nttd: HTTP and MCP API, `NttdEnv`, stepped and real-time modes, tiers. Railhead's claim must be the frontier features, not existence. |
| "Long-horizon management with competitors and a deterministic, judge-free score" | Done by FM-Bench (football, 20 years, Arena). Railhead adds the spatial infrastructure and transport-network layer, not the idea itself. |
| "World model as the environment" | Minute-scale consistency is incompatible with decades of exact bookkeeping (Genie 3). |
| "Vision agent plays OpenTTD from screenshots" | ottd-bench does this, and vision did not help in BALROG or FLE. |
| "Duplicate-format evaluation" | Game Arena (poker) and duplicate bridge. Adopt it as protocol (section 6). |

---

## 6. Table stakes: non-negotiable requirements drawn from prior failures

1. **Defeat open-loop memorization.**
   - Seeded perturbations that don't distort the policy: industry and economy RNG, map seeds, optional sticky-command delay.
   - Thousands of training seeds with disjoint test seeds.
   - A **mandatory open-loop baseline** (time-indexed command replay) that must fail on test seeds.
   - Sources: Machado, SMACv2, Procgen.
2. **Separate training affordances from evaluation rules.** Forks, save/load and RNG access are free in training. At test they are banned or priced, and the protocol declares which track a result is on. Sources: NLE, Go-Explore.
3. **Horizon semantics.**
   - Open-ended runs are indefinite-horizon: cutoffs are `truncated`, never `terminated`.
   - In fixed-horizon tasks, remaining time is observable.
   - Bankruptcy is the only true terminal.
   - Sources: Gymnasium, Pardo.
4. **Headline score.**
   - Log₁₀ of company value, averaged over a trailing window (steady state, not a snapshot), plus delivered-cargo value.
   - The native 1000-point rating is a milestone vector only, because it saturates.
   - Report per scenario family. Never average saturated tasks with unsaturated ones.
   - Sources: FLE, BALROG, NetHack.
5. **Audit for score gaming.** Watch for end-of-run loan juggling, selling off vehicles, and buffer hoarding, the analogues of NetHack's early termination and FLE's hand-feeding. Re-score on a held-out window after the cutoff.
6. **Process metrics alongside outcomes.** Monitoring rate, commitment execution, repeated-error loops, deadlock rate, and idle drift. Sources: CivBench, Vending-Bench, Flatland, CoffeeBench.
7. **Statistics.**
   - IQM with stratified-bootstrap CIs.
   - Paired seeds and duplicate formats.
   - **Published power analysis and minimum runs per claim.**
   - Sources: rliable, Game Arena, openttd-agent.
8. **Baselines.** Strong scripted, OR and legacy NoAI baselines are mandatory. Symbolic bots beat neural ones about 3× in NetHack, and OR beat RL in Flatland.
9. **Harness hygiene.**
   - Version the harness and report a harness-ablation ladder.
   - Pause during deliberation, or debit think-time in ticks.
   - Sources: lmgame, Pokémon, VideoGameBench.
10. **Version pinning.** Every result carries the OpenTTD build, NewGRF set, settings hash, and harness version. Sources: ALE v4/v5, NLE freeze, OpenAI Five surgeries.
11. **Action masking and feasibility queries.** Provide `can_build`-style queries and say explicitly which spatial work the API does. Sources: μRTS, FLE.
12. **Two adapter families.** Gymnasium/PettingZoo (AEC, with OpenTTD's fixed per-tick command order made explicit) and OpenEnv/verifiers.

---

## 7. Open measurements to run before committing to the design

1. **Null-driver throughput.** Ticks/s on 256², 512² and 1024² maps, with and without cargodist and with AI companies active.
2. **Fork cost.** Save, load and simulate-one-game-year latency. This sets idea 1's pricing.
3. **Replay fidelity.**
   - Does a `DEBUG_DUMP_COMMANDS` replay reproduce company value exactly with no AIs present?
   - With AIs present, how large is the script-state gap?
4. **Hidden RNG streams.** Which random streams can be reseeded per fork without changing player-observable history? This needs a source audit of `Random()` call sites.
5. **Determinism under GameScript-brokered multi-company control.** Is command order stable when one deity script issues commands for N companies within one tick?
6. **Legacy AI baselines.** A starter set of BaNaNaS AIs, including rail-capable ones, that run stably on the pinned build.
