# Model economics and behaviour

This is a behavioural interpretability study of the eleven models that played Arena 2.
The question is what each model's play reveals about how it reasons about an economy, not which model is best.

Every claim below comes from one of three sources:

| Source | What it shows | Status |
|---|---|---|
| The action log of each player | Every action, its cost and its result, and a state snapshot after every wait | Ground truth. The verifier replays it exactly. |
| The visible messages and final report of each player | What the model chose to say while it played and at the end | The model's stated reasoning. It can be wrong, so it is checked against the log. |
| OpenTelemetry traces | Requests, tokens, cost and time | Measured. Token counts are not comparable across vendors. |

Hidden reasoning (thinking blocks) is not used.
"Stated" below always means a visible message or the final report.

To reproduce every number in this document:

```bash
railhead econ .cache/arena2 --task tasks/arena-dev-0100 --out docs/data/arena2-econ.json
railhead arena .cache/arena2 --export docs/data/arena2.json --task tasks/arena-dev-0100
railhead trace bundle .cache/arena2 --otel traces
```

The results are in [`docs/data/arena2-econ.json`](data/arena2-econ.json) and [`docs/data/arena2.json`](data/arena2.json).

## 1. The economy the models faced

Arena 2 is a 512 × 512 temperate map that starts in January 1960.
Each company starts with a £100,000 loan and can borrow up to £500,000.
The score is cash minus loan after a 10-year mandate plus one unattended year.
Vehicles and buildings count only for what they earn, so asset stripping at the end does not help.

Three facts of the OpenTTD economy decided this game:

1. **Pay grows with distance.** OpenTTD pays for cargo by distance and transit time. A long, fast route earns much more per trip than a short one.
2. **Aircraft are fast and need no track.** Opus measured about 8.5 tiles a day for the Bakewell LB-8 and about £28k for one full 340-tile passenger leg. One aircraft cost about £37k, so it paid for itself in less than two full legs. An air route needs two airports (about £17k each) and nothing between them.
3. **In 1960 only the large airport exists.** The small airport was retired at the end of 1959. The metropolitan, commuter and international types arrive in the 1980s and 1990s.

So the dominant strategy was clear in hindsight: long-haul passenger and mail flights between large towns, started early, financed with the loan.
The rest of this document is about which models found it, how, and what the others believed instead.

## 2. The numbers

Net worth is after the run-off year, from the verified replay.
"Return" is final net worth per pound of capital spent.
"Mean leg" is the mean Manhattan length, in tiles, of the legs in the vehicles' orders, weighted by vehicle.

| Model | Net worth | Capital spent | Return | Bought (road / rail / air) | Mean leg (main mode) | Max loan | Loan repaid | Actions | Failed | Dry runs | Waits (median days) |
|---|---|---|---|---|---|---|---|---|---|---|---|
| claude-opus-5-5 | £45,758,664 | £8,076,218 | 5.7 | 0 / 0 / 154 | air 462 | £500k | 1961-10-30 | 554 | 11 | 13 | 30 (60) |
| gpt-6-astra | £27,935,307 | £2,533,931 | 11.0 | 2 / 9 / 48 | air 481 | £500k | 1961-07-30 | 298 | 28 | 0 | 14 (365) |
| gpt-6-sol | £27,232,656 | £2,734,368 | 10.0 | 8 / 10 / 55 | air 452 | £100k | 1965-03-06 | 639 | 0 | 363 | 24 (180) |
| gpt-6.1-sol | £22,371,463 | £2,381,206 | 9.4 | 1 / 6 / 41 | air 472 | £500k | 1962-03-22 | 286 | 27 | 0 | 16 (240) |
| gpt-5.6-sol | £13,130,503 | £2,367,663 | 5.5 | 24 / 0 / 61 | air 460 | £500k | 1963-09-18 | 349 | 6 | 85 | 16 (180) |
| claude-fable-5-1 | £9,837,139 | £1,785,829 | 5.5 | 0 / 56 / 6 | rail 121 | £500k | never | 566 | 15 | 13 | 21 (60) |
| claude-sonnet-5-5 | £3,930,521 | £1,188,264 | 3.3 | 105 / 129 / 0 | road 81, rail 122 | £250k | 1965-12-04 | 1,021 | 61 | 274 | 85 (20) |
| claude-haiku-5-5 | £2,757,221 | £1,415,641 | 1.9 | 275 / 0 / 0 | road 85 | £450k | 1964-11-26 | 832 | 38 | 2 | 33 (120) |
| gpt-5.6-terra | £335,998 | £337,644 | 1.0 | 52 / 6 / 0 | road 108 | £200k | 1969-02-03 | 241 | 7 | 59 | 13 (365) |
| gpt-6-luna | £236,701 | £217,108 | 1.1 | 29 / 5 / 0 | road 71 | £100k | 1968-04-29 | 239 | 7 | 102 | 24 (180) |
| gpt-5.6-luna | −£50,557 | £256,548 | −0.2 | 38 / 2 / 0 | road 52 | £300k | never | 436 | 100 | 81 | 32 (120) |

For reference, the certified scripted truck route ends at £147,407 and no play ends at −£36,300.
Rail counts include wagons.
A £100k maximum loan means the player never borrowed beyond the starting loan.

## 3. Ten findings

### 3.1 The winners converged on the same answer independently

The five richest companies all built long-haul air networks, and their mean air legs are almost identical: 452 to 481 tiles.
They did not see each other.
Each one reached the same optimum from the same economics.

Most of them also said why.
Opus: "Pay per passenger grows with distance and towns produce limited passengers, so longer routes earned more from the same towns."
gpt-6-astra: long-distance flights "let me earn across the map without first funding hundreds of tiles of track."

### 3.2 The dividing line was applying "distance pays" to the fastest mode

Sonnet stated the same law ("Coal income scales almost linearly with distance") and acted on it, inside freight.
It moved its coal from 59-tile routes to 167 to 200-tile rail routes.
It never tested aircraft: "I did not try shipping, air or passengers."

Arena 1 shows the law working within road transport too.
On its smaller 256 × 256 map, Sonnet won with road legs that averaged 156 tiles.
Haiku's road legs averaged 75 tiles, and it made less than a third as much.

The models that won did not only know the law.
They transferred it to the choice of transport mode.

### 3.3 Scale is not the same as money

Haiku bought 275 road vehicles, the most of any player, and finished 8th.
Opus made 16.6 times more with about half as many vehicles.
Across the arena, the number of vehicles bought does not predict the result.
Route length and transport mode do.

### 3.4 Capital efficiency and absolute scale are different strategies

The gpt-6 family (astra, gpt-6-sol and gpt-6.1-sol) returned about £10 of net worth for every £1 they invested.
Opus returned £5.7 per £1 but invested about three times as much, and finished first.

Opus kept expanding after it saw saturation.
Its own messages say so: "quarterly income has plateaued near £2.0M as existing airports saturate."
It then switched from new aircraft to statues and advertising.
Early on it had rejected statues on price ("Statues cost £140k (about four planes), so I'll skip them").
Later, when more planes stopped paying, it bought eight statues and ten large advertising campaigns.
That is substitution at the margin: the cheaper lever is used first, and the next one only when the first stops paying.

### 3.5 One misleading error message locked two models out of the winning strategy

gpt-6-luna and gpt-5.6-luna both concluded that airports did not exist.
gpt-6-luna: "Standard airports and helicopter depots remain unavailable even in 1969."
gpt-5.6-luna: "1965 only exposes `heliport` without a hangar, so helicopters cannot be purchased yet; the other airport types are unavailable."

Both statements are false.
Large airports were available all game, and Opus built its first in February 1960.

The action logs show how the belief formed:

| Player | Airport attempts |
|---|---|
| gpt-6-luna | `small` 18 times, plus `international`, `commuter`, `metropolitan`, `intercon`, `helidepot` and `helistation`, all rejected as not available. `heliport` 5 times (built, but it has no hangar). **`large` never.** |
| gpt-5.6-luna | `small` 3 times, every other type once, all rejected as not available. **`large` once, rejected as `ERR_AREA_NOT_CLEAR`** (the site, not the type). |
| gpt-5.6-terra | `small` once. Never tried air again. |

At the time, the error for an unavailable type said "try small, or wait for later years".
In 1960, that advice pointed at a type that had just been retired.
gpt-6-luna followed it 18 times and never tried the one type that worked.
gpt-5.6-luna tried `large` once, got a site error, and read it as the same kind of failure.

This is the clearest case in the arena of an environment shaping a model's world model.
The error text was an observation, and the models believed it.
The error now lists the airport types that exist in the current year (fault 7 in the [README](../README.md#10-the-players-filed-bug-reports)).

### 3.6 Self-reports are accurate on numbers the model could check, and weaker on claims it never tested

Each final report was checked against the action log:

| Model | Claim | Log | Verdict |
|---|---|---|---|
| Opus | "repaid the loan in late 1961" | Loan set to 0 on 1961-10-30 | Correct |
| Opus | "The fleet peaked at about 146 planes" | Peak fleet 146, on 1967-05-02 | Correct |
| Opus | Run-off forecast "somewhere around £44-45M" | £45,758,664 | Close (1.7% above the top of its range) |
| Haiku | "The loan peaked at £450k in 1962 and was fully repaid by 1964" | £450k on 1962-01-21, £0 on 1964-11-26 | Correct |
| Haiku | "every route was funded from profit" | Borrowed to £300k, £400k and £450k while it built routes in 1960 to 1962 | Contradicted by its own log |
| Haiku | "Trucks were the only vehicle type that paid back well here" | No airport attempt, no train bought | Unsupported: it never tested an alternative |
| gpt-5.6-luna | The loan went to £200k "then held it" | £200k (1963), £300k (1965), then £200k (1968) | Wrong |
| gpt-6-astra | "repay the £500,000 loan" early | Loan set to 0 on 1961-07-30 | Correct |
| Sonnet | "scaled them with a £250k loan, then repaid it in full" | £250k (1961), £0 on 1965-12-04 | Correct |

gpt-5.6-luna's report is an unusual case.
Its final message contains a visible self-correction: "Reduced the loan from £200,000? Wait starting loan100k, later borrowed to200k; final loan200k."
The correction made the report less accurate, because it forgot the £300k step.

The pattern: numbers a model can query (loan, fleet size) are reported well.
Comparative and causal claims it never tested ("the only type that paid back") can be stated with the same confidence and no evidence.

### 3.7 Exploration styles differ sharply

| Style | Models | Evidence |
|---|---|---|
| Search with dry runs | gpt-6-sol, Sonnet, gpt-6-luna | 363, 274 and 102 dry runs. gpt-6-sol used dry runs to scan for airport sites: it probed 324 sites (251 rejected, 73 accepted), then built 32 airports, every one on the first real attempt. It had zero failed actions all game. |
| Act directly | gpt-6-astra, gpt-6.1-sol | 0 dry runs |
| Controlled experiment | gpt-5.6-sol | Ran trucks and aircraft side by side for 180 days: "aircraft produced about £188,000 profit while the original road fleet added only a few thousand." Then borrowed to the limit and pivoted. |
| Counterfactual in a fork | Opus | Tested "wait until full" orders in two reseeded forks. "The full-load test lost about £384k over 180 days compared with baseline (planes sit idle waiting), so I'm dropping that idea." |
| Debugging in a fork | gpt-5.6-luna | Used a fork to test station and depot geometry after a route failed. |

Only Opus and gpt-5.6-luna used forks at all.
Every other player received the same help text and never created one.

### 3.8 Management rhythm is a personality trait, and it does not predict the result

gpt-6-astra and gpt-5.6-terra waited a year at a time (median 365 days).
Sonnet checked in every 20 days and took 85 management rounds.
Astra finished second with 298 actions; among the top five, only gpt-6.1-sol used fewer (286).
Terra finished ninth with the same rhythm.
Sonnet's high frequency produced the most actions (1,021) and a mid-table result.

Activity over time is also distinctive:
- **Opus front-loaded.** 427 of its 554 actions came in 1960 and 1961, and 72% of its capital was spent by the end of 1962.
- **Sonnet back-loaded.** 701 actions came in 1964 and 1965, during its move to rail.
- **Fable spent 96% of its capital by the end of 1962,** because its session ended in February 1963.

### 3.9 Most models understood the run-off rule, and they prepared for it differently

Several models said explicitly that the score rewards durable profit.
Opus: "vehicle value doesn't count; only sustained profit does."
gpt-6-luna: "I'll prioritize durable routes that can keep earning without frequent intervention."
gpt-5.6-terra repaid its loan before the last year "so the automated run-off begins with no interest burden."

Their end-game behaviour differed:
- **Pruned loss-makers.** Astra, gpt-6-sol and gpt-6.1-sol sold or reassigned unprofitable aircraft before the unattended year.
- **Accepted them on purpose.** Opus kept 42 planes that lost money in 1969, because selling them "would have added well under 1% to the final figure while using up my remaining calls."
- **Missed the deadline.** gpt-6-sol tried to stop two aircraft after the horizon had ended. The environment rejected the actions.

Fable is the extreme case.
It stopped in 1963 at a usage limit, with its loan at the maximum and net worth at −£279,327.
Its rail network ran unattended for the rest of the mandate and the run-off, and finished sixth at £9,837,139.

### 3.10 More visible reasoning did not buy better decisions

Within each vendor, the player that produced the most output tokens did not do best.

| Claude model | Output tokens | Output tokens per action | Cost (API-equivalent) | Wall-clock | Cost per £1M of net worth |
|---|---|---|---|---|---|
| Opus 5.5 | 171,067 | 309 | $15.13 | 37 min | $0.33 |
| Fable 5.1 | 353,547 | 625 | $35.58 | 79 min (stopped at a usage limit) | $3.62 |
| Sonnet 5.5 | 132,487 | 130 | $7.12 | 24 min | $1.81 |
| Haiku 5.5 | 164,116 | 197 | $2.03 | 17 min | $0.74 |

| Codex model | Requests | Output tokens |
|---|---|---|
| gpt-6-astra | 63 | 17,725 |
| gpt-6.1-sol | 70 | 20,327 |
| gpt-5.6-terra | 69 | 23,103 |
| gpt-5.6-sol | 175 | 39,229 |
| gpt-6-sol | 138 | 47,115 |
| gpt-5.6-luna | 94 | 48,154 |
| gpt-6-luna | 111 | 71,655 |

The Codex output counts may exclude reasoning tokens, so do not compare them with the Claude counts.
Within Codex, the second-best player overall (astra) used the fewest requests and output tokens.
The player that used the most output tokens (gpt-6-luna) finished tenth.

Narration is also a behavioural trait.
Opus wrote 91 visible messages while it played.
Haiku wrote none until its final report, and Fable wrote two.

## 4. Model profiles

Each profile gives the revealed economic model (what the player bet on), its stated reasoning, what went wrong and how it adapted, and a one-line behavioural signature.

### claude-opus-5-5: a growth investor with a budget

- **Bet.** Air only, from the first day. 154 aircraft on 26 routes, mean leg 462 tiles. It borrowed £300k in its first week and £500k within two weeks, and repaid it in October 1961.
- **Reasoning.** It measured before it scaled: first plane speed, then revenue per full leg against plane price ("a full 200-pax leg of ~335 tiles earns ~£28k, and a plane costs £37k. Expanding aggressively."). It grew by opening new towns rather than overloading old airports ("Existing airports are near saturation, so growth comes from new towns."). It tracked its action budget and lengthened its waits as the budget ran down.
- **Adaptation.** It noticed the income plateau and the falling station ratings, then switched to statues and advertising. It also counted its own crashes, and corrected itself when it had miscounted ("No new crashes (I'd miscounted).").
- **Calibration.** Every self-report claim checked is correct, and its run-off forecast was within 2%.
- **Signature:** measure, scale, notice saturation, substitute, and keep track of what it has left to spend.

### gpt-6-astra: set-and-forget capital allocator

- **Bet.** Long-haul air (mean leg 481 tiles) with a token coal train and bus. It borrowed the full £500k and repaid it after one operating year.
- **Reasoning.** It explicitly chose air to avoid the capital cost of track ("without first funding hundreds of tiles of track"). It added mail where mail was waiting, and treated the train and bus as small diversifications.
- **Rhythm.** 14 waits, median 365 days, 298 actions. It reported about £850,000 of earnings in its first full operating year.
- **End game.** It sold the marginal bus and duplicate aircraft, and reported "all 46 vehicles were profitable last year" before the run-off.
- **Signature:** the highest return on capital in the arena (11.0) from one of the smallest action counts.

### gpt-6-sol: the careful engineer

- **Bet.** Long-haul air (mean leg 452 tiles), plus coal trucks and two coal rail lines. It never borrowed beyond the starting £100k and still finished third.
- **Reasoning.** It searched before it built. It ran 363 dry runs, including 324 airport-site probes, and had zero failed actions. It diagnosed a loss-making mail aircraft to a root cause: the destination airport accepted only 7/8 of the mail needed. It rerouted the aircraft, then retired it when the fix did not pay.
- **Miss.** It tried to stop two aircraft after the horizon. The actions were rejected and the losses stayed.
- **Signature:** systematic search with no wasted actions, and low leverage.

### gpt-6.1-sol: cost-conscious operator

- **Bet.** The same playbook as astra: long-haul air (mean leg 472 tiles), mail, one coal train, one bus, and the loan repaid early.
- **Reasoning.** It flagged an expensive site and stopped there: "Preparing Dendhattan's waterfront airport cost £291,606 in land work, which makes its payback uncertain; I'll avoid further large construction." It reassigned weak aircraft to new routes instead of selling them.
- **Signature:** the same strategy as its siblings, with closer attention to construction cost.

### gpt-5.6-sol: the empiricist who pivots

- **Bet.** It started with a cheap coal-truck route "to establish reliable income". Then it ran a controlled comparison and moved to long-haul air (mean leg 460 tiles), with feeder buses into the airports.
- **Reasoning.** It is explicit about evidence and sunk cost ("I'm leaving that sunk-cost airport unused because forcing bridges and feeders into it would compound the mistake."). It tuned full-load orders for asymmetric demand so aircraft "fly empty toward the backlog and return full".
- **Signature:** test, compare, then commit. The best-documented strategic pivot in the arena.

### claude-fable-5-1: builds infrastructure that outlives it

- **Bet.** Railways first: 13 rail lines (mean leg 121 tiles), 56 locomotives and wagons, and two air legs added in August 1962. The loan was at the £500k maximum from the first day.
- **Behaviour.** It produced the most output tokens per action in the arena (625) and played slowly. It hit its session usage limit in February 1963 and never returned. Only two visible messages remain, so its reasoning is mostly unreadable from what it showed.
- **Outcome.** The network it left ran unattended for about eight years and finished sixth (£9,837,139). That beat five models that played the full ten years.
- **Signature:** durable infrastructure that did not depend on its owner.

### claude-sonnet-5-5: hands-on operator, local optimiser

- **Bet.** Coal trucks, then coal rail from 1964 (rail legs mean 122 tiles). Its loan peaked at £250k and was repaid in December 1965.
- **Behaviour.** It took the most actions (1,021), the most management rounds (85, median 20 days) and 274 dry runs. Its most common failure was building where vehicles stood (`ERR_VEHICLE_IN_THE_WAY`, 26 times).
- **Reasoning.** It found the distance law and stayed inside freight with it. Its report is candid about its mistakes: unstarted trucks, a missing road piece and a wrong junction.
- **Signature:** strong local optimisation, and no search beyond the first good mode.

### claude-haiku-5-5: a confident specialist that never tested alternatives

- **Bet.** Road only: 11 coal routes, 275 road vehicles, mean leg 85 tiles. Its loan peaked at £450k and was repaid in November 1964.
- **Reasoning.** It stated a payback rule and stopped investing near the end ("With under a year left, a new £4.4k truck would not earn back its price.").
- **Self-report.** Its comparative claim that trucks were "the only vehicle type that paid back well" has no support in its own play, which never tried another mode. Its claim that every route was "funded from profit" is contradicted by its borrowing.
- **Signature:** efficient execution of one idea, stated with more certainty than the evidence allowed.

### gpt-5.6-terra: risk-averse and never found the growth engine

- **Bet.** It tried rail first, and the train never reached its station (a depot without a track connection). Then it switched to three road-freight corridors. It spent £337,644 in total.
- **Reasoning.** It is explicit about cutting losses ("stopping investment in that failed branch rather than compounding the loss") and repaid its loan before the final year. It tried air once, with `small`, and never again.
- **Signature:** sound caution applied to a small opportunity.

### gpt-6-luna: locked out by one wrong belief

- **Bet.** Two coal-truck routes with 25 trucks. A rail attempt and an oil route both failed, and it sold them.
- **Reasoning.** It understood the scoring rule well ("prioritize durable routes"). But in 1964 it concluded that airports did not exist, and it kept that belief to the end of the mandate (see 3.5).
- **Signature:** good reasoning around a false premise. One untested assumption bounded the whole game.

### gpt-5.6-luna: struggled with the interface, then with the economics

- **Bet.** Coal trucks, one failed coal train and heliports without hangars. Its loan was never repaid, and it finished below the no-play baseline.
- **Behaviour.** It had the most failed actions (100 of 436). 85 were malformed arguments (`ERR_BAD_ARG`). It read a site error on its one `large` airport attempt as type unavailability.
- **Reasoning.** It debugged route geometry in a fork, the only player besides Opus to use one. Its final report's visible self-correction introduced a new error.
- **Signature:** effort spent fighting the tools left little room to find the economics.

## 5. Hypotheses

These are interpretations of one run per model, and they need more seeds to confirm.

1. **Transfer, not knowledge, separated the leaders.** Several models stated that pay grows with distance. The winners applied it to the choice of transport mode, and the others applied it only within the mode they already used.
2. **Breadth of early exploration predicted the outcome more than the volume of actions.** The top five all built air routes in their first year. Every player that did not finished below all five.
3. **Error text is part of the observation.** One misleading hint created a false belief that two models kept to the end of the mandate. Environment designers should write error messages as carefully as observations.
4. **Self-reports are reliable for quantities the model can query, and unreliable for untested comparisons.** A verifier for agent reports should check causal and comparative claims against logs, not only the numbers.
5. **Reasoning volume is not decision quality.** The most verbose player in each vendor did not do best. Fable's long turns did build a network durable enough to beat five complete games.

## 6. Limits of this study

- **One world, one run per model.** The differences are large, but there is no variance estimate.
- **Different harnesses.** The Claude models played through Claude Code and the OpenAI models through Codex.
- **The players were not isolated from the operator's environment.** Two Codex players stated that they loaded the operator's personal Codex skills and memory index before playing. The Claude Code sessions also ran with the operator's global settings. A future arena should give each player a clean configuration directory (`CODEX_HOME`, and a separate Claude configuration).
- **Visible narration is chosen by the model.** Silent players (Haiku, Fable) reveal less of their reasoning than narrators (Opus), so their profiles rest more on the action log.
- **Forks are not in the action log.** Fork use comes from the commands the players ran, as recorded in their transcripts.
- **Catchment diagnoses may be wrong.** Two players described a failed station as a catchment-radius problem, with different radii. The `rh catchment` probe uses one radius whatever the station type and does not show the 8/8 acceptance threshold. That probe could be clearer. It is not confirmed as a fault.
