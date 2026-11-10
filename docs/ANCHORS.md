# Script-AI anchors

This document gives a ceiling anchor for the `tycoon` arena tasks.
An anchor is the result of a competent player that is not a language model.
The anchors are community script AIs for OpenTTD (NoAI scripts from BaNaNaS).
The text follows ASD-STE100. Refer to [STE.md](STE.md).

## 1. Result on `arena-dev-0100`

The task `arena-dev-0100` has a 512x512 map, a start in 1960, a mandate of 3,650 days, and a run-off of 365 days.
Each AI played the same `start.sav` three times (seeds 0, 1, and 2).
The table shows the median of the three seeds, and the range after the run-off.
Section 3 tells why the two net worth columns are different.

| Player | Net worth at mandate end (median) | Net worth after run-off (median) | Range after run-off | Fleet after run-off, seed 0 (road / rail / air / water) |
|---|---:|---:|---|---|
| AAAHogEx 115 | £180,175,697 | £225,013,820 | £205.8M to £234.6M | 500 / 350 / 199 / 29 |
| NoNoCAB 9 | £75,961,019 | £87,116,557 | £70.9M to £93.4M | 234 / 39 / 95 / 13 |
| NoCAB 2.1.4 | £63,374,327 | £75,799,428 | £71.3M to £83.6M | 348 / 58 / 83 / 5 |
| AIAI kappa97 | £52,524,347 | £63,990,946 | £60.2M to £86.8M | 46 / 14 / 122 / 0 |
| CluelessPlus 41 | £45,815,866 | £53,657,505 | £50.5M to £54.2M | 497 / 0 / 145 / 0 |
| **claude-opus-5-5 (best model)** | £41,323,272 | £45,758,664 | one game | see `arena2.json` |
| AdmiralAI 25 | £19,457,929 | £28,475,193 | £23.5M to £32.3M | 490 / 55 / 165 / 0 |
| **gpt-6-astra (second model)** | £24,196,367 | £27,935,307 | one game | see `arena2.json` |
| ChooChoo 434 | £10,979,881 | £15,224,778 | £12.9M to £15.4M | 201 / 270 / 0 / 0 |
| SimpleAI 14 | £5,808,497 | £7,149,923 | £5.9M to £10.5M | 178 / 50 / 10 / 0 |
| DictatorAI 183 | £7,267,990 | £6,625,840 | £6.6M to £16.4M | 259 / 0 / 117 / 4 |
| LuDiAI AfterFix v27 | £1,922,725 | £1,129,959 | £0.8M to £2.0M | 486 / 165 / 175 / 29 |
| Trans AI 200626 | £662,844 | £61,879 | -£0.38M to £0.24M | 45 / 23 / 106 / 7 |
| baseline (scripted truck route) | | £147,407 | certificate | |
| idle (no action) | | -£36,300 | certificate | |

The best AI (AAAHogEx) has approximately 4.9 times the net worth of the best model after the run-off.
At the mandate end, AAAHogEx has approximately 4.4 times the net worth of the best model.
Five AIs (AAAHogEx, NoNoCAB, NoCAB, AIAI, CluelessPlus) have more net worth than the best model in all three seeds.
This is true at the mandate end and after the run-off.
All rows, all seeds, and the yearly samples are in `docs/data/arena2-anchors.json`.

All 11 AIs started and played to the end.
No AI went bankrupt.
No AI script stopped with an error in the final runs.
Trans AI did not start in the first attempt, because its script name is `Trans` and not `Trans AI`.
In the second attempt, Trans AI stopped at start with "couldn't find library 'AILib.Common'".
The content service did not select the four AILib libraries as dependencies of Trans AI.
After a manual download of these libraries by content ID, Trans AI played normally.

## 2. Multi-map result

Five AIs played five 10-year 512x512 tasks from the seed games.
AAAHogEx played seeds 0, 1, and 2. The other four AIs played seed 0 only.
The table shows the net worth after the run-off. For AAAHogEx, it shows the median of three seeds.
The data, with the mandate-end values, is in `docs/data/seedgames-anchors.json`.

| Task (start year) | idle | baseline | AAAHogEx (median) | NoNoCAB | CluelessPlus | AIAI | AdmiralAI |
|---|---:|---:|---:|---:|---:|---:|---:|
| `arena-train-0201` (2000) | -£25,300 | £208,067 | £201.9M | £31.4M | £57.7M | £12.7M | £6.3M |
| `arena-train-0203` (1950) | -£47,300 | £115,158 | £51.9M | £11.6M | £1.3M | £5.5M | £0.08M |
| `arena-train-0204` (1990) | -£47,300 | -£44,117 | £165.4M | £69.5M | £72.3M | £37.9M | £48.2M |
| `arena-train-0205` (1990) | -£36,300 | £260,619 | £216.8M | £31.9M | £57.1M | £45.0M | £38.9M |
| `arena-train-0206` (2000) | -£47,300 | £94,634 | £192.9M | £81.0M | £65.5M | £51.7M | £54.6M |

AAAHogEx has the most net worth on each map and in each seed.
Its lowest result is on the 1950 map (£46.6M to £58.4M).
On the other four maps, its range is £163.7M to £223.9M.
All 35 runs played to the end. No AI went bankrupt, and no AI script stopped with an error.
On each map, the AI company starts on the same date, with the same loan and the same maximum loan as the company of the model.
The company of the model has £192 to £358 less cash, because it paid one month of loan interest before the task seal.

## 3. Fairness

### 3.1 Conditions that are the same

- The AI loads the same `start.sav` as the model. The engine binary is the same.
- The AI does not change a game setting. The settings come from the savegame. The rows record them in `settings`.
- The AI uses the default value of each of its own settings. The `ai_settings` field is empty in all rows.
- The vehicle limits are the same for each company: 500 road vehicles, 500 trains, 200 aircraft, and 300 ships. Some AIs reach the road and air limits.
- The company of the model also stays in the world, with no assets. Its net worth after the run-off is -£36,300 in each run. This is equal to the idle certificate, so the AI does not change the company of the model.

### 3.2 Opening state of the two companies

The tool reads both companies at the same instant, after `start_ai`.

| Value | Company of the model | Company of the AI |
|---|---:|---:|
| Date | 1960-02-01 | 1960-02-01 |
| Cash | £99,725 | £100,000 |
| Loan | £100,000 | £100,000 |
| Maximum loan | £500,000 | £500,000 |
| Company value | £1 | £1 |

The AI has £275 more cash at the start.
The company of the model paid one month of loan interest before the task seal.
This difference is approximately 0.0005% of the AAAHogEx result.

### 3.3 Conditions that are not the same

- **The run-off is generous to the AI.** The company of the model stops at the mandate end. The company of the AI continues to play for 365 days, because an AI cannot stop without the deletion of its company. Use the net worth at the mandate end for the strict comparison. Each row has `runoff_generous_to_ai: true`.
- **Game time.** The AI acts during game time, on each tick. A model acts only while the clock stops, and it then sets a wait. The AI script does not run while the game is paused (OpenTTD `openttd.cpp` runs `AI::GameLoop` only when the game is not paused).
- **Instructions for each tick.** The savegame sets `script.script_max_opcode_till_suspend` to 250,000. The OpenTTD default is 10,000. This setting gives a script more instructions in each tick. It is a task setting, and the tool does not change it. It helps a script AI that uses much computation.
- **Seeds.** Seed 0 keeps the world random generator of the savegame. This gives the same world as the model games. Seeds 1 and 2 use the engine command `reseed` after the AI company exists. The `reseed` command changes the world random generator (industry production, town growth). It does not change the random generator of the AI script, because the savegame keeps that generator. Thus the AI decisions change only because the world changes.
- **Number of games.** Each model has one game. Each AI has three seeds.

### 3.4 Repeatability

Two runs of AAAHogEx with seed 0 gave the same net worth (£225,013,820).
The two runs had different machine loads, so the result does not depend on wall-clock time. This agrees with the source code: an AI does not act while the game is paused.

## 4. Duration of a run

One run of 4,015 game days takes 28 to 360 seconds on a shared, loaded machine.
SimpleAI is the fastest (approximately 30 seconds).
NoNoCAB and NoCAB are the slowest (approximately 200 to 360 seconds).
AAAHogEx takes 88 to 165 seconds.

## 5. Procedure to reproduce

The AIs are not part of Railhead.
The tool downloads them from the OpenTTD content service (BaNaNaS) into `.cache/anchors/`.
Git ignores this directory.

1. Set the engine path:

   ```bash
   export RAILHEAD_OPENTTD=/path/to/patched/openttd
   ```

2. Download the AIs and their libraries:

   ```bash
   railhead anchor fetch AAAHogEx NoNoCAB NoCAB AIAI CluelessPlus AdmiralAI ChooChoo SimpleAI DictatorAI "LuDiAI AfterFix" "Trans AI"
   railhead anchor fetch 12740649 12046408 11826239 14293344
   ```

   The second command downloads the AILib libraries that Trans AI needs.
   The tool writes the name, version, licence, and content ID of each package to `.cache/anchors/manifest.json`.

3. Run the anchors:

   ```bash
   railhead anchor tasks/arena-dev-0100 --ai AAAHogEx --ai NoNoCAB --seeds 3 --out docs/data/arena2-anchors.json
   ```

   Use the script name of the AI with `--ai`. For Trans AI, the script name is `Trans`.
   Do not run more than three engines at the same time on a shared machine.

The tool writes each run to `out/anchors/<task>-<ai>-s<seed>.json` and keeps the engine log in the run directory.
The options `--horizon-days` and `--runoff-days` are for short tests only. The tool does not let you use them with `--out`.

## 6. Packages

| AI | Version | Content ID | Licence (from the package) |
|---|---|---:|---|
| AAAHogEx | 115 | 15290992 | GNU GPL version 3 |
| NoNoCAB | 9 | 15788994 | GNU GPL version 2 |
| NoCAB | 2.1.4 | 10849002 | GNU GPL version 2 |
| AIAI | kappa97 | 12289466 | GNU GPL version 2 |
| CluelessPlus | 41 | 13916129 | GNU GPL version 2 |
| AdmiralAI | 25 | 10884238 | GNU GPL version 2 |
| ChooChoo | 434 | 200993 | Creative Commons Attribution-ShareAlike 3.0 Unported |
| SimpleAI | 14 | 11983414 | GNU GPL version 2 |
| DictatorAI | 183 | 9798921 | GNU GPL version 2 |
| LuDiAI AfterFix | v27 | 12236102 | GNU GPL version 2 |
| Trans AI | 200626 | 5306716 | GNU GPL version 2 |

The licence files of the libraries are GNU GPL (version not checked), except MinchinWeb's MetaLibrary, which has its own licence ("MinchinWeb's MetaLibrary License").
Railhead does not contain code from these AIs or libraries.

## 7. Notes on the data files

- The keys of `curves` and `fleet_mix` are `anchor:<AI>#<seed>`. The key of a row is `player` plus `seed`. Thus `railhead figures` does not match them to rows.
- For an anchor row, `reached` is the end of the run-off. For a model row, `reached` is the date of the last action.
- On the seed-game maps, AAAHogEx has three seeds. The other four AIs have one seed (seed 0).
