# Seed games: multi-map results with intervals

## Summary

Arena 2 ranks 11 models on one map (`arena-dev-0100`) with one game for each model.
This note adds 23 games that exist already, on 10 other maps.
Three models played these games: `claude-opus-5-5`, `gpt-6-astra` and `gpt-6-sol`.
The verifier of this branch replayed all 23 action logs.
All 23 replays are valid, with 0 divergences and 0 rejections.

On the maps that both players played, the order is the same on each map:
`claude-opus-5-5` first, `gpt-6-astra` second, `gpt-6-sol` third.
No pair is tied: each 95 % interval excludes zero.
But the number of maps is small (5 for each pair with `claude-opus-5-5`).
Also 9 of the 23 games did not end with a submit (see "Caveats").
Read the intervals as a description of these maps, not as a significance test.

## Data and method

| Item | Value |
|---|---|
| Games | 23: `gpt-6-astra` 10, `gpt-6-sol` 8, `claude-opus-5-5` 5 |
| Maps | `arena-train-0201`, `0203` to `0208`, `0210`, `0224`, `0225` |
| Task | `tycoon`, 3,650-day horizon, 365-day run-off, 512 x 512, temperate, `log50` score |
| Source logs | The action log of each game (`<map>/<player>/state/run/actions.jsonl`), copied and not changed. The logs are not in this repository. |
| Log identity | The SHA-256 of each copied log was recorded at the time of the replay (not in this repository). |
| Verifier | `python -m railhead.verify` of this branch, one fresh replay per game |
| Data | `docs/data/seedgames.json` (rows in the arena export shape, plus `end`, `days_played`, `submitted`) |
| Statistics | `docs/data/seedgames-stats*.json` from `railhead stats` |

No seed game had a verification result before this work.
Thus, each result in this note comes from a new replay.

The statistics use the map as the unit:

- One cell is the mean of the games of one player on one map. Each cell here has one game.
- A paired comparison uses only the maps that both players played. The table gives `n` for each pair.
- The bootstrap draws maps with replacement, 10,000 times, with the same draw for both players. Seed 0.
- The interval is the 2.5 % to 97.5 % percentile of the mean paired difference.
- A pair is "tied" when the interval contains zero.
- The sign test counts the maps that each player won. It does not count a zero difference.
- `asinh(net worth / 100,000)` is a log-like scale that also accepts a negative net worth. The ranking uses this scale.

Commands:

```bash
railhead stats collect out/seedgames/arena-train-02* --tasks tasks --out docs/data/seedgames.json
railhead stats collect out/arena2-dev0100/arena-dev-0100 --tasks tasks --out docs/data/seedgames-dev0100.json
railhead stats docs/data/seedgames.json --out docs/data/seedgames-stats.json
railhead stats docs/data/seedgames.json --finished-only --out docs/data/seedgames-stats-finished.json
railhead stats docs/data/seedgames.json docs/data/seedgames-dev0100.json --out docs/data/seedgames-stats-dev0100.json
railhead stats docs/data/seedgames.json docs/data/seedgames-dev0100.json --finished-only \
  --out docs/data/seedgames-stats-dev0100-finished.json
```

## Results for each map

Net worth is after the run-off, from the replay.
"End" is from the action log: `submitted`, `horizon` (the game reached the horizon without a submit), or `stopped` (the log ends before the horizon).
`w_idle` and `w_base` are the certified values of the task: do nothing, and the scripted truck route.

| Map | Start year | `w_idle` | `w_base` | `claude-opus-5-5` | `gpt-6-astra` | `gpt-6-sol` |
|---|---|---|---|---|---|---|
| `arena-train-0201` | 2000 | -25,300 | 208,067 | 102,747,512 submitted | 47,697,377 submitted | 2,111,143 submitted |
| `arena-train-0203` | 1950 | -47,300 | 115,158 | 5,323,024 stopped (day 609) | 4,371,317 stopped (day 2,165) | 2,246,493 submitted |
| `arena-train-0204` | 1990 | -47,300 | -44,117 | 115,448,029 submitted | 23,354,225 stopped (day 2,780) | 13,476,963 submitted |
| `arena-train-0205` | 1990 | -36,300 | 260,619 | 69,373,574 stopped (day 772) | 20,479,253 submitted | 4,154,209 stopped (day 1,920) |
| `arena-train-0206` | 2000 | -47,300 | 94,634 | 55,095,462 submitted | 27,044,175 submitted | 5,743,119 horizon |
| `arena-train-0207` | 1960 | -25,300 | 42,294 | not played | 9,572,844 submitted | 2,629,924 stopped (day 1,200) |
| `arena-train-0208` | 2000 | -25,300 | 363,815 | not played | 15,368,350 submitted | 4,137,284 stopped (day 2,640) |
| `arena-train-0210` | 1960 | -47,300 | 121,872 | not played | 19,161,563 stopped (day 2,280) | 4,333,690 submitted |
| `arena-train-0224` | 1990 | -25,300 | 333,208 | not played | 22,583,708 submitted | not played |
| `arena-train-0225` | 1960 | -36,300 | 180,878 | not played | 24,414,094 submitted | not played |
| `arena-dev-0100` (Arena 2) | 1960 | -36,300 | 147,407 | 45,758,664 submitted | 27,935,307 submitted | 27,232,656 submitted |

All 23 seed games have `valid` = 1, `rejected` = 0 and 0 divergences.
The `arena-dev-0100` rows are the Arena 2 results. They are equal to `docs/data/arena2.json` in each shared field.
The verifier of this branch replayed these three logs again.
The result is equal to the Arena 2 result: `valid` = 1, 0 divergences, the same net worth.

Per-game action counts, failed actions, the arena scores and the horizon net worth are in `docs/data/seedgames.json`.

## Aggregate results

### The 10 seed maps, all 23 games

| Player | Maps | Mean net worth | Median net worth | Mean `asinh` | Mean `score_k50` | Mean `score_k1000` |
|---|---|---|---|---|---|---|
| `claude-opus-5-5` | 5 | 69.60M | 69.37M | 6.856 | 0.979 | 0.809 |
| `gpt-6-astra` | 10 | 21.40M | 21.53M | 5.902 | 0.979 | 0.686 |
| `gpt-6-sol` | 8 | 4.85M | 4.15M | 4.394 | 0.790 | 0.504 |

The per-player means use different sets of maps.
Do not compare them directly. Use the paired table below.

The arena score `score_k50` stops at 1 (it is clipped).
Most games of `claude-opus-5-5` and `gpt-6-astra` have `score_k50` = 1.
On `arena-train-0204`, `w_base` is only 3,183 more than `w_idle`.
Thus, a net worth of approximately 112,000 or more gives `score_k50` = 1 on that map.
Thus, `score_k50` does not separate the two strongest players. `score_k1000` and `asinh` separate them.

### Paired comparisons

The difference is "first player minus second player" on the shared maps.

| Maps in the set | Pair | `n` | Net worth difference [95 % interval] | `asinh` difference [95 % interval] | Maps won | Sign test p | Tied |
|---|---|---|---|---|---|---|---|
| 10 seed maps | `claude-opus-5-5` - `gpt-6-astra` | 5 | 45.01M [20.13M, 71.88M] | 0.90 [0.50, 1.32] | 5-0 | 0.062 | no |
| 10 seed maps | `claude-opus-5-5` - `gpt-6-sol` | 5 | 64.05M [31.84M, 94.09M] | 2.39 [1.53, 3.24] | 5-0 | 0.062 | no |
| 10 seed maps | `gpt-6-astra` - `gpt-6-sol` | 8 | 16.03M [8.82M, 25.65M] | 1.45 [0.99, 1.99] | 8-0 | 0.008 | no |
| plus `arena-dev-0100` | `claude-opus-5-5` - `gpt-6-astra` | 6 | 40.48M [17.98M, 65.32M] | 0.83 [0.48, 1.21] | 6-0 | 0.031 | no |
| plus `arena-dev-0100` | `claude-opus-5-5` - `gpt-6-sol` | 6 | 56.46M [26.30M, 86.41M] | 2.08 [1.20, 2.99] | 6-0 | 0.031 | no |
| plus `arena-dev-0100` | `gpt-6-astra` - `gpt-6-sol` | 9 | 14.32M [7.21M, 23.52M] | 1.29 [0.78, 1.84] | 9-0 | 0.004 | no |

With only the games that ended with a submit or at the horizon (`--finished-only`):

| Maps in the set | Pair | `n` | Net worth difference [95 % interval] | `asinh` difference [95 % interval] | Maps won | Sign test p | Tied |
|---|---|---|---|---|---|---|---|
| 10 seed maps | `claude-opus-5-5` - `gpt-6-astra` | 2 | 41.55M [28.05M, 55.05M] | 0.74 [0.71, 0.77] | 2-0 | 0.500 | no |
| 10 seed maps | `claude-opus-5-5` - `gpt-6-sol` | 3 | 83.99M [49.35M, 101.97M] | 2.76 [2.15, 3.88] | 3-0 | 0.250 | no |
| 10 seed maps | `gpt-6-astra` - `gpt-6-sol` | 2 | 33.44M [21.30M, 45.59M] | 2.33 [1.55, 3.12] | 2-0 | 0.500 | no |
| plus `arena-dev-0100` | `claude-opus-5-5` - `gpt-6-astra` | 3 | 33.64M [17.82M, 55.05M] | 0.66 [0.49, 0.77] | 3-0 | 0.250 | no |
| plus `arena-dev-0100` | `claude-opus-5-5` - `gpt-6-sol` | 4 | 67.62M [33.94M, 101.30M] | 2.20 [0.95, 3.45] | 4-0 | 0.125 | no |
| plus `arena-dev-0100` | `gpt-6-astra` - `gpt-6-sol` | 3 | 22.53M [0.70M, 45.59M] | 1.56 [0.03, 3.12] | 3-0 | 0.250 | no |

How to read these intervals:

- A percentile bootstrap of a mean cannot go outside the smallest and the largest map difference. When one player wins on all maps, the interval excludes zero for each `n`.
- With 5 maps, the smallest possible two-sided sign test p is 0.0625. With 6 maps, it is 0.031.
- Thus, "not tied" here means "the first player won on each shared map". It does not mean "statistically significant".
- With `--finished-only`, two or three maps remain for most pairs. With so few maps, the bootstrap has very few different resamples. Do not use these intervals as evidence.

### Ranking

The ranking counts separated pairwise wins minus losses on `asinh`, then uses mean `asinh`.
Each of the four sets (seed maps, plus `arena-dev-0100`, each with and without unfinished games) gives the same order with no ties:

1. `claude-opus-5-5`
2. `gpt-6-astra`
3. `gpt-6-sol`

On `arena-dev-0100` alone, `gpt-6-astra` (27.94M) and `gpt-6-sol` (27.23M) are close.
On the 8 seed maps that both played, `gpt-6-astra` has more net worth on each map.

### Is `arena-dev-0100` like for like?

Partly. These items are the same: the `tycoon` family, the 3,650-day horizon, the 365-day run-off, the 512 x 512 map, the `log50` score, and the definition of `w_idle` and `w_base`.
These items are different:

- The briefing. The `arena-dev-0100` briefing gives the correct log scale. The seed-map briefings give an incorrect scale (caveat a).
- The settings. `arena-dev-0100` starts in 1960 with a maximum loan of 500,000 and no breakdowns. The seed maps also vary among themselves: start years 1950 to 2000, maximum loan 200,000 to 500,000, breakdowns on some maps.

Thus, this note gives the results with and without `arena-dev-0100`. The order of the players does not change.

## Do the winners go to air transport?

The data counts the vehicles that each game bought or cloned, by transport mode (`fleet_mix`, from the action log).
Rail counts include wagons. Thus, the rail count is higher than the number of trains.
The count includes vehicles that the player sold later. It is not the fleet at the end.

| Map | Winner | Winner air share | `claude-opus-5-5` | `gpt-6-astra` | `gpt-6-sol` |
|---|---|---|---|---|---|
| `arena-train-0201` | `claude-opus-5-5` | 100 % | 100 % (163 air) | 96 % | 62 % |
| `arena-train-0203` | `claude-opus-5-5` (stopped) | 0 % | 0 % (30 rail, 0 air) | 35 % (stopped) | 60 % |
| `arena-train-0204` | `claude-opus-5-5` | 100 % | 100 % (118 air) | 76 % (stopped) | 85 % |
| `arena-train-0205` | `claude-opus-5-5` (stopped) | 100 % | 100 % (109 air) | 73 % | 87 % (stopped) |
| `arena-train-0206` | `claude-opus-5-5` | 100 % | 100 % (96 air) | 78 % | 64 % |
| `arena-train-0207` | `gpt-6-astra` | 78 % | not played | 78 % | 48 % (stopped) |
| `arena-train-0208` | `gpt-6-astra` | 63 % | not played | 63 % | 91 % (stopped) |
| `arena-train-0210` | `gpt-6-astra` (stopped) | 79 % | not played | 79 % (stopped) | 83 % |
| `arena-train-0224` | `gpt-6-astra` | 72 % | not played | 72 % | not played |
| `arena-train-0225` | `gpt-6-astra` | 90 % | not played | 90 % | not played |
| `arena-dev-0100` | `claude-opus-5-5` | 100 % | 100 % (154 air) | 81 % | 75 % |

What the data shows:

- On 10 of 11 maps, the winner bought mostly aircraft (63 % to 100 %).
- The exception is `arena-train-0203`. There, `claude-opus-5-5` bought only rail vehicles and stopped after 609 game days, in 1951. The map starts in 1950.
- `claude-opus-5-5` bought only aircraft on 5 of its 6 maps.
- Air is not specific to the winners. 23 of the 26 games bought more than 50 % aircraft. This includes most games of `gpt-6-sol`, which is last on each shared map.
- Thus, the data supports "all three models use mostly air on these maps". It does not show that air alone separates a winner from a loser. The quantity of aircraft and the routes are not in this analysis.

## Caveats

### a. The briefing gave an incorrect score scale

25 tasks in `tasks/` have this problem: the 14 `arena-train-02xx` tasks and the 11 `arena-dev-02xx` tasks.
All 23 seed games use these tasks.
The briefing says: "a simple scripted truck route scores 0.5 ... and doubling its gain scores 1".
But each task has `"score": "log50"`. On this scale, the scripted route scores approximately 0.18, and 1 needs 50 times the route gain.
The net worth does not depend on the briefing. But the briefing can change how a player plays.
For example, a player can think that it is near the maximum score at two times the route gain.
The `arena-dev-0100` briefing is correct.

### b. Nine games did not end with a submit

| Map | Player | End | Days played | Last date in the log |
|---|---|---|---|---|
| `arena-train-0203` | `claude-opus-5-5` | stopped | 609 | 1951-10-03 |
| `arena-train-0203` | `gpt-6-astra` | stopped | 2,165 | 1956-01-06 |
| `arena-train-0204` | `gpt-6-astra` | stopped | 2,780 | 1997-09-12 |
| `arena-train-0205` | `claude-opus-5-5` | stopped | 772 | 1992-03-14 |
| `arena-train-0205` | `gpt-6-sol` | stopped | 1,920 | 1995-05-06 |
| `arena-train-0206` | `gpt-6-sol` | horizon | 3,650 | 2010-01-29 |
| `arena-train-0207` | `gpt-6-sol` | stopped | 1,200 | 1963-05-16 |
| `arena-train-0208` | `gpt-6-sol` | stopped | 2,640 | 2007-04-25 |
| `arena-train-0210` | `gpt-6-astra` | stopped | 2,280 | 1966-04-30 |

How the verifier scores a log without a submit:
the verifier replays the log, then runs the world to the horizon without the player, then runs the run-off.
It reads the net worth after the run-off. `submitted` is only a diagnostic value. It does not change the reward.
Thus, a stopped game gets a valid score. The company runs without the player from the last action to the end.
These scores do not show the result of a full game. A game that stopped after 609 days is not a full sample.

Why the games stopped: the logs do not record a reason.
The evidence is as follows:

- No agent log of a stopped game has a final message, an error, or a usage-limit message.
- All 208 `rate_limit_info` events in the `claude-opus-5-5` logs have `"status":"allowed"`. The five-hour use was at 2 % to 47 %.
- The OpenTelemetry log file of the Codex games (`runs/seed-games/traces/logs.jsonl`) has 0 matches for "usage limit", "usage_limit", "rate limit", "rate_limit", "quota" or "hit your" (case-insensitive search of the full text, keys and values).
- All nine agent logs stop between 14:34:48 and 14:36:21 IST (UTC+05:30) on 2026-10-20 (file modification times). No agent log of the 23 games has a later write. The trace files also stop at 14:36.
- Codex and Claude Code games stopped at the same time.

Thus, an external stop of all agents at approximately 14:36 is probable. The cause is unknown.
Do not describe these games as stopped by a usage limit.
The `gpt-6-sol` game on `arena-train-0206` reached the horizon. Its last commands came after the horizon and it did not submit.

### c. Model and harness are not separate

`claude-opus-5-5` played in Claude Code. `gpt-6-astra` and `gpt-6-sol` played in Codex.
A difference between Claude and GPT players can come from the model or from the harness.
The difference between `gpt-6-astra` and `gpt-6-sol` has the same harness.

### d. The maps are training maps

- The seed games were played as imitation data for a different project, which trains small decision models on these games.
- No seed map is also a dev map. Each `arena-train-02xx` task has a different seed and a different `start.sav` hash from each `arena-dev-02xx` task and from `arena-dev-0100`.
- Thus, these results are not held-out results for a model that trains on these games.
- Only `gpt-6-astra` played `arena-train-0224` and `arena-train-0225`. These two maps were possibly calibration seeds. This is not confirmed.

### Other limits

- One game for each player on each map. The tool can average more games for each map, but there are none.
- Coverage is not equal. `claude-opus-5-5` played 5 maps, `gpt-6-astra` 10, `gpt-6-sol` 8.
- The games ran on the Claude Max and ChatGPT subscriptions, as in Arena 2 (System 1 spec, decisions 11 and 18).
