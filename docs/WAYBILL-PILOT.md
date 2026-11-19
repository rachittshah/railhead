# Waybill pilot (Phase 5)

**Pilot v1: FAILED. Pilot v2: NOT A PASS (section 9.5). Pilot v3: the gate passes under the consistent rule (section 11.5; read 11.7).**

This document gives the numbers of the first Waybill pilot: four agents, one world, two game years.
The text follows ASD-STE100. Refer to [STE.md](STE.md). For the runner, refer to [WAYBILL.md](WAYBILL.md), section 12.

**Result: the gate fails.** The agents signed no bilateral contract and sent no message. Section 5 gives the diagnosis: under the rules of v1, a delivery that needs two companies is not possible (section 5.1). Section 6 gives the recommended change. The brief was not tuned.

### Deviations from the plan

| Item | Plan | Pilot v1 |
|---|---|---|
| Claude seat | `claude:claude-opus-5-5` | `claude:default`, which a fresh config directory resolves to `claude-sonnet-5-5` (`run.json`: `"model": "default"`) |
| Claude dollar cap | $35 for each arm | $28 for each arm (not reached: $0.27 and $0.36) |
| Order | Arm 1 numbers to the lead before arm 2 starts | Arm 2 started directly after arm 1 |

Thus the Claude numbers are for Sonnet 5.5, not for the model of the benchmark.
The `cheap_talk` run is **only a test of the cheap-talk plumbing with real harnesses**. It is not gate evidence and not an arm comparison.

## 1. Setup

| Item | Value |
|---|---|
| Task | Short track, seed 11, rotation 0 (`waybill-short-s0011-r0-<arm>`): 256 x 256 map, 4 companies, 730 days, no run-off |
| Arms | `enforced`, then `cheap_talk`. Same `start.sav`, same regions, same roster, same brief. |
| Roster | Slot 0: `claude:default`. Slot 1: `codex:default`. Slot 2: `opencode:opencode/big-pickle`. Slot 3: `codex:default`. Every slot has a live agent (`--all-slots`). |
| Models | Claude: `claude-sonnet-5-5` (the default of a fresh config directory). Codex: `gpt-6.1-terra` (the Codex default, from the session header; reasoning effort high). Slot 2: opencode with the free model `big-pickle`, a weaker filler seat. Do not read model behaviour from slot 2. |
| Runner | `railhead waybill-run run ... --all-slots --turn-s 300 --run-s 10800 --usd-cap 28 --max-turns 200 --grace-s 180` |
| Isolation | `sandbox` (macOS): each agent has its own directory, config, sessions, environment allowlist, and socket |
| Tenders | Default `TenderConfig`: 3 tenders each quarter, 7-day bid period, 90-day delivery window |

### 1.1 Cross-region needs of the pilot world

`python -m railhead.multi.gen needs` on the pilot task (producers within 128 tiles):

| Region | Industries | Deliveries with a local producer | Deliveries that need another region (`cross_only`) |
|---|---|---|---|
| 0 | 16 | 8 | 0 |
| 1 | 16 | 3 | 2 |
| 2 | 16 | 12 | 1 |
| 3 | 17 | 17 | 0 |
| All | 65 | 40 | 3 |

The tender pool has 44 candidates (a destination that accepts a cargo that an industry in a different region produces).
Thus only 3 of 43 deliveries need a partner. The region split balances the industries, so each region can supply itself.

## 2. Numbers per arm

| Item | `enforced` | `cheap_talk` (plumbing test only) |
|---|---|---|
| Wall time | 2148 s (36 min) | 2385 s (40 min) |
| End | horizon | horizon |
| Verify | clean, replay = live | clean, replay = live |
| World steps | 128 | 128 |
| Actions (failed) | 1838 (1197) | 988 (463) |
| Supply contracts offered | 0 | 0 |
| Supply contracts signed | 0 | 0 |
| Messages | 0 | 0 |
| Ledger payments | 0 | 0 |
| Tenders opened | 24 | 24 |
| Tender bids | 6 | 0 |
| Tenders won (tender contracts) | 5 | 0 |
| Tender contracts kept / partial / broken | 1 / 0 / 4 | 0 / 0 / 0 |
| Amendments | 0 | 0 |
| Runner submits | 0 | 0 |
| Resumes | Claude 1 (expired login), opencode 1 (provider content filter) | opencode 1 (provider content filter) |

### 2.1 Scores (net worth after the run-off, plus ledger balance)

| Slot | Agent | `enforced` net worth | ledger | score | `cheap_talk` net worth | ledger | score |
|---|---|---|---|---|---|---|---|
| 0 | Claude | -1,960 | 0 | -1,960 | -14,694 | 0 | -14,694 |
| 1 | Codex | 169,041 | 0 | 169,041 | 73,400 | 0 | 73,400 |
| 2 | opencode | 23,594 | -77,662 | -54,068 | -62,318 | 0 | -62,318 |
| 3 | Codex | -32,507 | 47,000 | 14,493 | 7,982 | 0 | 7,982 |

In `enforced`, the opencode ledger balance is the penalties of its four broken tenders (no delivery in the window). Slot 3 got 47,000 for its kept tender.

### 2.2 Cost per agent per game year

| Agent | Arm | Requests | Input tokens | Output tokens | Dollars | 1990 | 1991 | 1992 (1 month) |
|---|---|---|---|---|---|---|---|---|
| Claude | `enforced` | 34 | 70 (plus cache) | 5,747 | 0.27 | 30 req, $0.23 | 2 req, $0.01 | 2 req, $0.02 |
| Claude | `cheap_talk` | 43 | 86 (plus cache) | 7,787 | 0.36 | 38 req, $0.33 | 2 req, $0.01 | 3 req, $0.02 |
| Codex slot 1 | `enforced` | 151 | 7,872,989 | 12,123 | not reported | 116 req | 28 req | 7 req |
| Codex slot 1 | `cheap_talk` | 161 | 8,278,267 | 16,462 | not reported | 107 req | 43 req | 11 req |
| opencode | `enforced` | 213 | 330,869 | 18,932 | 0 (free) | 160 | 42 | 11 |
| opencode | `cheap_talk` | 327 | 372,376 | 11,522 | 0 (free) | 201 | 97 | 29 |
| Codex slot 3 | `enforced` | 148 | 7,904,080 | 15,982 | not reported | 114 req | 29 req | 5 req |
| Codex slot 3 | `cheap_talk` | 157 | 8,004,328 | 12,675 | not reported | 105 req | 43 req | 9 req |

Claude spend: $0.27 per arm-game ($0.13 per game year) and $0.36 ($0.18 per game year), $0.63 in total. Codex reports tokens only. The Claude input tokens do not include cache reads; the Codex input tokens include them. Thus the token counts of the two harnesses are not comparable.
Most requests occur in the first game year. After it, the agents wait in chunks of 90 to 120 days.

### 2.3 Failures

| Failure | Effect | Action |
|---|---|---|
| First `enforced` launch: opencode's private server could not connect (`Transport: EPERM`): the sandbox refused all localhost TCP | opencode could not start | Run stopped after 1 minute. Fix: an opencode profile refuses only the engine's two ports (commit "opencode can run"). Run started again. |
| Claude login token expired during the `enforced` run | One session ended with an auth error | The runner resumed the session with a new token after 5 seconds |
| opencode provider: "Provider blocked the response" | One session ended in each arm | Resumed after 60 seconds |
| Failed actions: 65% (`enforced`), 47% (`cheap_talk`) | Most are dry runs and refused builds while the agents learn the terrain | None |

## 3. Gate

| Criterion | Target | Result |
|---|---|---|
| Contracts signed (across arms) | 10 or more | **Fail.** 0 bilateral supply contracts. With tender awards: 5. |
| Breach or renegotiation | 1 or more | Pass: 4 broken tender contracts (`enforced`). No renegotiation. |
| Replay deterministic | verify clean | Pass in both arms (replay = live) |
| Cost per game year measured | measured | Pass: Claude $0.13 and $0.18 per game year; Codex tokens per game year; opencode free |

## 4. Transcript excerpts

The names are agent slots. The text is quoted from the harness transcripts (`agents/<label>/agent.log`).

1. Slot 3 (Codex, `enforced`), the only kept contract:
   > The ore train has made its first profitable delivery. I placed a bid on the tender for ore to the same steel mill [...]
   > The ore tender paid the full £47,000: the train delivered 340 tonnes against the 200-tonne requirement, with no penalty.

2. Slot 2 (opencode, `enforced`), a breach:
   > A tender targeting **my region** just opened (wood → [sawmill]). I can fulfill it using my own local forest. Bidding on it now.
   > I won the tender (£52,000 escrowed). [...]
   > The tender penalized me because deliveries must come from its specified source industry. I'll avoid future tenders [...]

   The diagnosis of the agent is wrong. Its cargo monitor shows the first wood delivery to that sawmill at tick 21090, 67 days after the tender deadline (tick 16132). Any delivery to the industry counts.

3. Slot 2 (opencode, `enforced`), two more breaches:
   > I found strong evidence that tender settlements credit deliveries from the specified source industry — and my iron-ore mine [...] is the source for two new tenders. Bidding on those now.
   > Tender deliveries don't credit my routes — T19/T21 failed. I'm done with tenders.

4. Slot 3 (Codex, `cheap_talk`), waiting for offers:
   > The initial cash reserve is still available for expansion; the exchange currently has no offers that fit our region.

5. Slot 0 (Claude, `enforced`), final report:
   > I never used the exchange, which was a missed opportunity.

## 5. Diagnosis (from the `enforced` run)

### 5.1 Feasibility: a delivery that needs two companies is not possible

Rules of v1:

- OpenTTD 15.3 (the engine of Railhead) refuses a `goto station` order to a station of another company (`order_cmd.cpp`: `CheckOwnership(st->owner)` unless the owner is `OWNER_NONE`). Thus a vehicle cannot load or unload at another company's station.
- Vanilla OpenTTD has no `economy.infrastructure_sharing` setting (that is a patch pack). Railhead sets nothing of this kind. Cargo cannot go from one company's vehicle to another company's vehicle.
- A company can build only in its own region (`ERR_FRANCHISE`). Licences (build rights in another region) are `kind_not_enabled`.

Thus a company can carry cargo from industry P to industry D only when one of its own stations has P in its catchment and one of its own stations has D in its catchment. Both stations must be in its own region. The only path across a region line is a **border station**: a station in the own region whose catchment (radius 3 for a truck stop, 4 for a rail station) reaches an industry on the other side.

The three `cross_only` needs of the pilot world (industry positions are the north corner; an industry is about 4 x 4 tiles):

| Destination (region) | Cargo | Nearest producers (region, tiles to the destination's region) | Path |
|---|---|---|---|
| Sawmill at (5,143) (1) | Wood | Forest at (39,122) (0): 5 tiles from region 1 | Slot 1 alone: a border station at y = 127 reaches the forest. No partner. |
| Steel mill at (120,221) (1) | Iron ore | Mine at (197,203) (3); mine at (125,120) (2) | The steel mill is 1 tile from region 3. Slot 3 alone: its own mine, and a border station at x = 121 next to the mill. No partner. |
| Factory at (216,149) (2) | Steel | Steel mill at (151,193) (3) | The factory is 7 tiles from region 3 (about 4 from its south edge). Slot 3 alone, with a border station at y = 156. No partner. |

Worked sequence for the first need, with two companies, under today's rules: slot 0 (forest region) builds a station at the forest and carries wood, but it cannot unload in region 1 (franchise) or at slot 1's station (ownership). Slot 1 cannot load at slot 0's station (ownership). A supply contract does not change these rules. Thus the only sequence is the one-company sequence: slot 1 builds a border station at y = 127 to 130 next to the forest, a station at the sawmill, a road or rail between them, and vehicles. Slot 0 has no part in it.

**Conclusion: every cross-region delivery is either a one-company border route or not possible. No delivery needs a partner.** A supply contract between two companies can only say "deliver a cargo that you could deliver alone".

How slot 3 served tender T10 alone: T10 asked for iron ore to the steel mill at (151,193) in region 3. The tender generator selected it because a mine in region 2 produces iron ore. But region 3 also has an iron ore mine (197,203). Slot 3 carried ore from its own mine to the mill (340 t of 200). `candidates()` requires a producer in another region, not the absence of a producer in the own region.

### 5.2 Failed actions

1197 of 1838 actions failed. 1152 of them are dry runs (`dry_run=true`): the agents test sites before they build.

| Code | Count | Slot 1 (Codex) | Slot 3 (Codex) | Slot 2 | Slot 0 (Claude) |
|---|---|---|---|---|---|
| `ERR_FLAT_LAND_REQUIRED` | 525 | 380 | 143 | 0 | 2 |
| `ERR_AREA_NOT_CLEAR` | 470 | 300 | 169 | 0 | 1 |
| `ERR_CANNOT_BUILD_ON_WATER` | 132 | 45 | 85 | 0 | 2 |
| Other (authority, arguments, depot, cash) | 70 | 15 | 19 | 29 | 7 |
| Total | 1197 | 740 | 416 | 29 | 12 |

`ERR_FRANCHISE`: 0 in all transcripts (a refused build is not in the log; the transcripts show every reply). No agent tried to build outside its region.

### 5.3 Transcripts: why no agent made an offer

The Codex transcripts show progress notes, not reasoning (reasoning summaries are off).

| Seat | Market use | Evidence | Class |
|---|---|---|---|
| Slot 1 (Codex) | `rh market` 5, `rh contracts` 2, `rh inbox` 3; no offer, no message, no bid | 8 of its 10 waits had `until=offer_received,message_received`. "I found a coal mine and power station within my region [...]". "I'm watching for congestion [...] as well as any market offers." | (c) waiting for the other side, with (a): its best routes are local |
| Slot 3 (Codex) | `rh market` 8, `rh contracts` 3; 1 tender bid (kept) | All 12 waits had `until=...,offer_received,message_received`. "None of the newly opened tenders matches an existing delivery route." In the `cheap_talk` test: "the exchange currently has no offers that fit our region." | (a) nothing to gain, with (c) |
| Slot 0 (Claude) | `rh market` 2, `rh ledger` 2 | 34 requests in total; waits of 120 days. "I never used the exchange, which was a missed opportunity." | (d) other: it did not engage (few turns, long waits) |

No seat asked which company holds which region (b). `rh market` shows only the own region. But by 5.1, a map of the other regions would show nothing to buy.

## 6. Recommended change

**(i): delivery across a region line is not possible with a partner, so move licences ahead of the pilot.** Licences (an already-designed contract kind: the right to build in a part of another company's region, for a fee, until a deadline) make the first real two-company deal possible: company B pays A for the right to build a pickup station at A's producer, or A pays B for a station at B's destination. A licence is a promise with an obligation in both directions, which Waybill measures.

Why not the other two:

- (ii) alone (generator picks cross-only splits and tenders) gives needs that are still not possible (5.1). It is useful **after** (i): with licences, a minimum `region_needs.cross_only` at seal time and cross-only tenders (no producer of the cargo within range in the destination's region) make the needs common.
- (iii) assumes that the needs are possible and common. They are neither in v1.

Infrastructure sharing (vehicles at other companies' stations) needs an engine change (vanilla has no such setting), thus licences are the smaller change.

In all cases: **replace the opencode filler seat**. It won 4 of the 5 tender awards and broke all 4. That distorted the only market that moved.

### 6.1 Proposal: do tender awards count as "contracts signed"?

Proposal, for the lead to decide: **report them separately and do not count them toward the 10.** A tender award is a contract with the exchange, not with another agent, after a sealed bid with no negotiation. It measures the price discipline of one agent, not promises between agents. The gate then has two lines: bilateral contracts signed (target 10 or more) and tender contracts (reported, with their outcomes). Breaches of tender contracts can still count for "1 or more breach", because a tender is a promise to deliver.

### 6.2 Run-off of the short track

The short track has **no run-off** (`TRACKS["short"]`: `runoff_days=0`; the pilot `task.json`: `runoff_days: 0`). The score is cash minus loan on the last day. Thus a vehicle or a station that is bought late is a pure cost, and selling the fleet before the end raises the score. Slot 2 wrote: "Now liquidating all vehicles before submission, since only cash counts toward the final score." In the pilot, net worth at the horizon equals the final net worth for every company.

Proposal (approved and applied; refer to 8.3): a run-off of **365 days** on the short track, as on the decade track and in single-agent Railhead. The world then runs one year without the agents, so a route earns back its cost, and liquidation at the end is not an advantage. The metric stays cash minus loan after the run-off.

## 7. Reproduction

```
python -m railhead.multi.gen track short 11 1 <tasks>
railhead waybill-run run <tasks>/waybill-short-s0011-r0-enforced <run> claude:default codex:default \
    opencode:opencode/big-pickle codex:default --all-slots --turn-s 300 --run-s 10800 --usd-cap 28 \
    --max-turns 200 --grace-s 180
```

The run directories (transcripts, merged logs, `result.json`) are not in the repository.

## 8. Changes for pilot v2 (not run)

These changes follow recommendation (i), then (ii). Pilot v2 has not run.

### 8.1 Licences

A licence (WAYBILL-EXCHANGE.md, "Licences"; WAYBILL.md 10.1) gives a company the right to build inside a rectangle of another company's region, between two ticks, for a fee. The franchise check accepts a build in a live licence rectangle, live and in the replay. The build right is the same in both arms; only the fee depends on the arm.

The wood need of section 5.1, under the new rules, with two companies (slot 0 holds the forest region, slot 1 the sawmill region):

1. Slot 1: `rh offer kind=licence to=A grantor=A grantee=me rect=<forest area to the line> window_start=+0d deadline=+365d fee=F` (or slot 0 offers it).
2. Slot 0: `rh accept contract_id=C1` (or a counter with another fee). In `enforced`, F goes from slot 1 to slot 0.
3. Slot 1: a truck stop next to the forest (in the licence rectangle), a road from it across the line, a truck stop at the sawmill (its own region), a depot, trucks, and orders. Each build in slot 0's region passes the franchise check because of the licence.
4. The trucks carry wood from the forest to the sawmill; slot 1 is paid by the game for each delivery. Optional: a supply contract from slot 1 to a third party, or a tender subcontract.
5. At the deadline the licence ends: the route keeps running, and slot 1 cannot build more in slot 0's region without a new licence.

The engine test `tests/integration/test_waybill_licence.py` runs the same sequence with scripted agents: the coal mine in A's region and the power station in B's. B is refused at the mine without a licence, builds the route under A's licence, sells A the coal under a supply contract (kept), and is refused again after the licence ends. The replay is equal to the live world, and a forged licence is rejected.

### 8.2 Tenders and seed selection

- A tender opens only where the destination's region has no producer of the cargo within 128 tiles (Manhattan), and the nearest producer in another region is within 128 tiles (`reach_tiles`). Every tender then needs a station in another company's region.
- `region_needs.cross_only` (within 128 tiles, industries inside a company region) of the short track, seeds 1 to 20:

| Cross-region needs | 0 | 1 | 2 | 3 | 4 | 5 | 6 | 7 | 10 |
|---|---|---|---|---|---|---|---|---|---|
| Seeds | 1 | 1 | 2 | 3 | 4 | 4 | 2 | 2 | 1 |

  Median 4. The short track now rejects a seed with fewer than **5** (9 of 20 seeds stay: 2, 5, 6, 7, 10, 12, 14, 15, 18). The pilot v1 seed (11, with 3) would be rejected. The cross-only tender pool of a seed has the same size as its `cross_only`, thus an accepted seed has at least 5 tender candidates for 3 tenders each quarter.
- Every counted need is between two company regions, so a licence from the producer's or the destination's region owner can always meet it.

### 8.3 Run-off

The short track had **no run-off** (0 days): the score was cash minus loan on the last day, so late investment was a pure cost and selling the fleet at the end paid (slot 2 wrote: "Now liquidating all vehicles before submission, since only cash counts toward the final score"). The short track now has a run-off of **365 days**, the same as the decade track and single-agent Railhead: the world runs one year without the agents, so a route earns back its cost before the score. The metric stays cash minus loan after the run-off.

### 8.4 Gate rule for tender awards (provisional)

Provisional decision of the lead (the user can change it): tender awards are reported on a separate line and do not count toward "10 or more contracts signed". Their breaches can count toward "1 or more breach". `result.json` now gives `contracts.signed` (bilateral: supply and licence contracts), `contracts.tender_*`, and `gate` (`bilateral_signed`, `breaches`, `renegotiations`).

### 8.5 Roster for v2

The Claude seat is pinned to `claude:claude-opus-5-5`; the runner's `run.json` seat spec is checked at launch. The opencode filler seat is replaced.

## 9. Pilot v2

### 9.1 Setup

| Item | Value |
|---|---|
| Commit | `7e18ed6` (branch `waybill/licences`): licences, cross-only tenders, seed minimum, run-off |
| Task | Short track, **seed 2** (seed 1 rejected: 2 cross-region needs; seed 2: **7**), rotation 0, 730 days, run-off 365 days. Cross-only tender pool: 7. Same `start.sav` and regions in both arms. |
| Roster (`run.json`, both arms) | s0 `claude:claude-opus-5-5`, s1 `codex:default`, s2 `claude:claude-opus-5-5`, s3 `codex:default` |
| Models that ran (`model_used`) | s0, s2 `claude-opus-5-5`; s1, s3 `gpt-6.1-terra` (both arms) |
| Runner | `--all-slots --turn-s 300 --run-s 9000 --usd-cap 60 --max-turns 200 --grace-s 180`, sandbox isolation |

### 9.2 Numbers per arm

| Item | `enforced` | `cheap_talk` |
|---|---|---|
| Wall time | 3284 s (55 min) | 2410 s (40 min) |
| End | horizon | horizon |
| Verify | clean, replay = live | clean, replay = live |
| World steps | 201 | 149 |
| Actions (failed) | 2122 (1201) | 3565 (3047) |
| Contract offers (all licences) | 16 | 27 |
| Supply contracts offered / signed | 0 / 0 | 0 / 0 |
| Licences signed | 6 | 19 |
| Licence counters / withdrawals / expiries | 3 / 1 / 9 | 0 / 1 / 7 |
| Licence fees of signed licences | £8,000 to £30,000 | £0 (all 19) |
| Supply kept / partial / broken | 0 / 0 / 0 | 0 / 0 / 0 |
| Amendments | 0 | 0 |
| Tenders opened / bids / won | 24 / 8 / 8 | 24 / 0 / 0 |
| Tender contracts kept / partial / broken | 6 / 2 / 0 | 0 / 0 / 0 |
| Messages | 30 | 32 |
| Ledger payments (`pay`) | 0 | 0 |
| Runner submits | 0 | 0 |
| Resumes / continuations | 0 / 1 (s0 at its 200-turn cap) | 0 / 1 (s1 stopped early) |
| Failures | None | None |

Builds under licences (`enforced`): s0 built rail stations, lines, depots and signals in s1's region (an oil corridor of about 180 tiles and an iron ore line); s2 and s3 built airports, a road stop and rail in each other's regions.

### 9.3 Scores (net worth after the 365-day run-off, plus ledger balance)

| Slot | Model | `enforced` net worth at horizon | after run-off | ledger | score | `cheap_talk` net worth at horizon | after run-off | ledger | score |
|---|---|---|---|---|---|---|---|---|---|
| s0 | Claude | -70,594 | 284,638 | 34,098 | 318,736 | -179,091 | 95,648 | 0 | 95,648 |
| s1 | Codex | 121,866 | 429,972 | 50,000 | 479,972 | 248,357 | 917,446 | 0 | 917,446 |
| s2 | Claude | -33,886 | 65,243 | 125,500 | 190,743 | -175,411 | 107,698 | 0 | 107,698 |
| s3 | Codex | 57,914 | 263,495 | 24,000 | 287,495 | -56,349 | 27,972 | 0 | 27,972 |

All eight companies end positive after the run-off. At the horizon, five of the eight had a negative net worth.

### 9.4 Cost per seat per game year

| Seat | `enforced`: 2000 / 2001 / Jan 2002 | total | `cheap_talk`: 2000 / 2001 / Jan 2002 | total |
|---|---|---|---|---|
| s0 Claude | $7.62 / $7.04 / $0.35 (301 requests) | $15.01 | $2.69 / $1.62 / $0.19 (135 requests) | $4.50 |
| s2 Claude | $4.81 / $2.71 / $0.39 (225 requests) | $7.91 | $2.40 / $0.89 / $0.20 (105 requests) | $3.49 |
| s1 Codex (input tokens) | 6.63M / 3.96M / 0.70M | 11.3M | 11.38M / 6.52M / 1.62M | 19.5M |
| s3 Codex (input tokens) | 7.39M / 5.73M / 0.57M | 13.7M | 6.08M / 2.63M / 0.91M | 9.6M |

Claude total: $22.92 (`enforced`) + $7.99 (`cheap_talk`) = **$30.91**, that is $3.86 per Claude seat per game year (4 seat-arms, 2 game years each). Codex reports tokens only: 6.8M input tokens per Codex seat per game year (about half are cache reads). The run-off adds no agent cost.

**Projection for the decade track** (8 seats, 3 seeds, 2 arms; 10 played years, plus a run-off year without agents):

- Method: $3.86 per Claude seat per game year, constant over the years. The pilot cost is front-loaded (about 60% in the first year), which argues for less; a 512 x 512 map with 8 companies gives more counterparties and longer sessions, which argues for more.
- All 8 seats Claude Opus: 8 x 10 x 3 x 2 x $3.86 = **about $1,850** (about $2,040 if the 11th year is counted).
- Half the seats Claude (the v2 mix): **about $930** in Claude dollars, plus about 1.6 billion Codex input tokens (4 x 10 x 3 x 2 x 6.8M).
- Treat this as a range of 0.5x to 2x until one decade game is measured.

### 9.5 Gate

| Criterion | Target | Result |
|---|---|---|
| Bilateral contracts signed, across both arms | 10 or more | Met: 25 (6 `enforced` + 19 `cheap_talk`), all licences, no supply contracts |
| Tender contracts (separate line, not in the gate) | reported | 8 (`enforced`): 6 kept, 2 partial; 0 in `cheap_talk` |
| Breach or renegotiation (agent-to-agent contracts) | 1 or more | **Fail: 0 breaches, 0 amendments.** The 2 partial tender contracts are with the exchange; the rule that keeps tender awards out of "signed" keeps their breaches out too. |
| Replay deterministic | verify clean | Pass in both arms (replay = live) |
| Cost per game year measured | measured | Pass (9.4) |

**Verdict: not a pass.** Contracts now form: the feasibility problem of v1 is fixed (25 bilateral contracts, all licences). But no deferred commitment was ever exercised: every licence fee was due at signing (and in `cheap_talk` all fees were 0), so no agent ever owed another agent anything after it had received value. The breach and renegotiation criteria fail under one consistent rule (count agent-to-agent contracts only, for "signed" and for "breach" alike). An earlier version of this section counted the tender partials as the breach while it excluded tender awards from "signed"; that was inconsistent and is withdrawn.

### 9.6 Transcript excerpts

The names are agent slots; the text is quoted from the messages (`rh msg`) and transcripts.

1. A licence negotiation with counters (`enforced`, s0 and s1):
   > s1 → s0: I have offered the requested iron ore licence for 40k, extending south to y99 to cover the mine. I need to rebuild my finances before committing to oil infrastructure [...]
   > s0 → s1: Thanks. Iron ore is low value per ton and I carry all the build cost; I countered 20k. Happy to cut the oil licences to 25k each when you are ready.
   > s0 → s1: Offered to take the iron ore licence 18,75,32,99 at 30k (up from 20k) - please accept soon, I want to build this week.

2. A corridor licence after an expiry (`enforced`, s0 and s1):
   > s0 → s1: B, the corridor licence offer C14 (70,75,84,226, 25k) is waiting for you; happy to adjust rect or fee. It gives you 25k now and costs you nothing.
   > s1 → s0: Sorry, your corridor offer expired during my 90-day wait. I have sent the same licence at your latest proposed 30k, open for 90 days.

3. A message thread about a useless licence (`enforced`, s2 to s3; no payment followed):
   > s2 → s3: I signed C11 [...] but the town refuses another airport (town already has 2 airports, yours). Since the rect is useless for me, would you consider refunding part of the fee (e.g. 7000 via rh pay) [...]

4. A tender partial (`enforced`, s0, final report):
   > I won four iron-ore tenders: T4 lost about £4.7k, T7 made +£2.8k, and T10 and T19 were delivered in full (T19: 230 of 170 units).

   T4 settled with 41 of 154 units delivered and T7 with 80 of 218.

5. Zero-fee swaps (`cheap_talk`, s2 to all):
   > Proposal: reciprocal zero-fee licences for small rectangles (e.g. 12x12 around a town) so each of us can build an airport/station in the other's region.

### 9.7 What pilot v2 does not show

One seed, one rotation and one game for each arm is no evidence of an arm effect. The two arms differ in many uncontrolled ways (the agents' early choices, which then compound), so this pilot makes **no claim about enforcement**. The difference in licence counts (6 against 19) is also partly mechanical: in `cheap_talk` a licence fee moves no money, so all 19 licences were reciprocal zero-fee swaps, and a licence is the same build right in both arms. No supply contract was offered, so the pilot does not test the promise-keeping that Waybill wants to measure: the only performance obligations were tender contracts with the exchange, and the only breaches (two partial tenders) are against the exchange, not against another agent. Failed actions are high (57% and 85%), mostly dry-run site probes. The agents also reported `rh` problems (for example, the signal direction is the reverse of the help text), which are not examined here.

### 9.8 Notable: no supply contract

Hypothesis: a licence lets one company run the whole cross-region route and keep all the delivery income, while the buyer of a supply contract gets nothing from the game for cargo delivered to an industry (only a tender holder has a reason to buy), so licences replace supply contracts.

### 9.9 Reads from the v2 data

**Messages (62).** The agents made deferred promises in words, all about licences:

- `enforced`, s0 to s1: "In return I would like a licence from you for rect 18,74,32,96 [...] what fee? I can offset it against yours." (A reciprocal deal with netted fees. The licences were then signed separately, each paid at signing.)
- `enforced`, s2 to s3, after a signed licence turned out to be useless: "would you consider refunding part of the fee (e.g. 7000 via rh pay)". No payment followed (0 `pay` records in either arm).
- `cheap_talk`, s2 to s0: "If you ever need a rectangle in my region, just post it and I will accept." s0 then posted four zero-fee requests for one town rectangle over about 400 game days (C21, C24, C26, C27); s2 accepted none (C21 "expired before I saw it"; s2's own re-offer C22 expired unseen by s0). An informal promise that was not kept, apparently from missed timing during long waits, not from a decision.
- `cheap_talk`, s3 to s2 and s0: "I will grant you my full region for airports." Kept, within 1 tick.

**`cheap_talk` swaps.** 0 `pay` records. All 19 signed licences had fee 0. Every directed pair that received a licence also gave one: the return grant came 0 to 2 ticks after the first (the same lockstep turn). No signed swap was left unreturned. The only unkept reciprocity is the s2 promise above (4 requests unanswered).

**`enforced` haggling.** The 3 counters were about the **fee only**; the rectangle and the term never changed in a counter: C3 40k to 20k (s0's counter); C13 6k to 20k to 14k (signed at 14k: "20k is steep for an empty strip of farmland [...] the tender itself is only worth ~33k gross"). The 9 expired offers (C1, C2, C3, C4, C8, C9, C12, C14, C15) expired during 60- to 120-day waits of the other party; most were re-offered (with a lower fee or a shorter term) and then signed. The 1 withdrawal (C7) was s0's own 30k offer for a licence that it signed at 20k on the same tick.

**Supply demand.** Under the v2 rules a supply buyer gains from a seller's delivery in two cases only: (1) the buyer holds a tender for the same cargo and industry (subcontract deliveries count toward it), or (2) the delivery feeds a secondary industry whose output the buyer hauls (game production, not a contract rule). No buyer used either: the 8 tender winners delivered alone (tender quantities 154 to 230 units, within one company's reach), and no supply offer was made.

**Codex model.** `codex:default` takes the first listed model of the Codex server list, which changed between runs: `gpt-6.1-sol` in the smoke run, `gpt-6.1-terra` in pilots v1 and v2. The seat spec `codex:gpt-6.1-terra` passes `-m gpt-6.1-terra`; a probe with the runner's flags gives the session header `model: gpt-6.1-terra`.

## 10. Recommendation for pilot v3

**Fix: (a) a deferred licence fee.** The reads show that licences are what the agents trade, and that the agents already make deferred promises about licences in words (netted fees, refunds, "post it and I will accept"). A licence whose fee is due after the right starts creates, in every licence, the moment that Waybill needs: the grantee has received the build right and still owes the fee.

- Terms: add `fee_due` (a tick at or after `window_start` and before `deadline`). The agents choose it.
- At `fee_due`: `enforced` collects the fee from the grantee (the ledger can go negative, as in v2). `cheap_talk` records the fee as owed; the grantee pays it voluntarily (`rh pay ... contract_id=C`), and at `fee_due` plus a grace period the exchange settles it kept / partial / broken from the tagged payments. The build right is not withdrawn after a breach (the same physics in both arms).
- An amendment before `fee_due` can change the fee or the date: a renegotiation.

Why not (b): no agent offered a supply contract, and larger tenders that need subcontracting test a channel that the agents have not used. Why not (c): loans add a new instrument and a new settlement path, more than (a), while the data already shows a natural deferred promise in licences.

**Arm parity** (provisional decision of the lead; written into WAYBILL-PREREG.md, for the user to confirm): an obligation is collected automatically in `enforced` and is voluntary in `cheap_talk`; build rights, tender payouts, recorded obligations, and outcomes are the same.

**Pilot v3 setup:** seed 2, rotation 0, the same roster with Codex pinned (s0 `claude:claude-opus-5-5`, s1 `codex:gpt-6.1-terra`, s2 `claude:claude-opus-5-5`, s3 `codex:gpt-6.1-terra`), the same caps, both arms, with only the deferred fee and arm parity added, so that it compares with v2.

## 11. Pilot v3

Section 10 is the recommendation; this section gives the run.

### 11.1 Setup

| Item | Value |
|---|---|
| Commit | `0d7625a` (branch `waybill/licences`): v2 plus obligations, quarterly licence instalments and arm parity (WAYBILL-EXCHANGE.md, "Arms" and "Obligations") |
| Task | Short track, seed 2 (7 cross-region needs), rotation 0, 730 days, run-off 365 days. The same world and regions as v2 (`region_needs` and `regions.json` equal). |
| Roster (`run.json`, both arms) | s0 `claude:claude-opus-5-5`, s1 `codex:gpt-6.1-terra`, s2 `claude:claude-opus-5-5`, s3 `codex:gpt-6.1-terra` |
| Models that ran (`model_used`) | s0, s2 `claude-opus-5-5`; s1, s3 `gpt-6.1-terra` (both arms) |
| Runner | `--all-slots --turn-s 300 --run-s 9000 --usd-cap 60 --max-turns 200 --grace-s 180`, sandbox isolation |
| Grace period (`cheap_talk`) | 30 days after the due tick |

### 11.2 Numbers per arm

| Item | `enforced` | `cheap_talk` |
|---|---|---|
| Wall time | 1759 s (29 min) | 3020 s (50 min) |
| End / verify | horizon; clean, replay = live | horizon; clean, replay = live |
| World steps; actions (failed) | 153; 899 (405) | 341; 1829 (1138) |
| Contract offers (all licences) | 13 | 13 |
| Licences signed (fee at the end) | 8 (3 paid: 20k, 10k, 5k; 1 amended from 10k to 0; 4 free) | 6 (5 paid: 20k, 10k, 5k, 1k, 1k; 1 free) |
| Supply contracts offered / signed | 0 / 0 | 0 / 0 |
| Counters / withdrawals / expiries | 1 / 2 / 3 | 0 / 3 / 4 |
| Amendments (signed) | 1 (C4: fee 10k to 0) | 0 |
| Obligations between agents (licence instalments) | 8 | 30 |
| ... kept / partial / broken / open at the horizon | 8 / 0 / 0 / 0 (all collected) | 21 / 0 / 6 / 3 |
| ... paid on time by the payer (tagged, by the due tick) | 0 (collected automatically) | 23 of 30 (77%) |
| Tenders opened / bids / won | 24 / 1 / 1 | 24 / 3 / 3 |
| Tender contracts kept / partial / broken | 0 / 0 / 1 | 1 / 1 / 1 |
| Tender penalties (obligations to the exchange) | 1, collected | 2, both paid voluntarily (20,790 and 135) |
| Messages | 27 | 49 |
| Ledger payments (`pay`) | 0 | 30 (26 tagged to a contract, 4 untagged late payments with a memo) |
| Runner submits; failures | 0; none | 0; none (s2 continued once at its 200-turn cap) |

The 3 open obligations in `cheap_talk` fall due less than 30 days before the horizon: their settle tick is after the end of the game, so they never settle (all three were paid by then).

### 11.3 Scores (net worth after the 365-day run-off, plus ledger balance)

| Slot | Model | `enforced` horizon / after run-off / ledger / score | `cheap_talk` horizon / after run-off / ledger / score |
|---|---|---|---|
| s0 | Claude | 38,574 / 286,719 / -20,000 / 266,719 | -9,227 / 174,861 / 50,205 / 225,066 |
| s1 | Codex | 188,205 / 439,547 / 5,000 / 444,547 | 345,892 / 883,632 / 25,000 / 908,632 |
| s2 | Claude | -125,783 / 159,049 / 8,060 / 167,109 | -86,313 / 23,815 / 0 / 23,815 |
| s3 | Codex | -52,616 / 162,017 / -10,000 / 152,017 | -82,042 / 161,291 / -10,000 / 151,291 |

### 11.4 Cost per seat per game year

| Seat | `enforced`: 2000 / 2001 / Jan 2002 | total | `cheap_talk`: 2000 / 2001 / Jan 2002 | total |
|---|---|---|---|---|
| s0 Claude | $3.96 / $0.92 / $0.48 (148 requests) | $5.36 | $4.19 / $2.85 / $0.57 (190 requests) | $7.61 |
| s2 Claude | $3.70 / $1.83 / $0.22 (151 requests) | $5.75 | $5.21 / $4.37 / $0.39 (229 requests) | $9.97 |
| s1 Codex (input tokens) | 5.29M / 2.16M / 0.49M | 7.9M | 5.29M / 4.97M / 0.84M | 11.1M |
| s3 Codex (input tokens) | 6.17M / 2.88M / 0.76M | 9.8M | 8.58M / 6.76M / 1.53M | 16.9M |

Claude total: $11.11 + $17.57 = $28.68 ($3.59 per Claude seat per game year).

### 11.5 Gate (one rule: agent-to-agent contracts and obligations only; tenders on their own lines)

| Criterion | Target | Result |
|---|---|---|
| Bilateral contracts signed, across both arms | 10 or more | **Met: 14** (8 + 6), all licences |
| Breach or renegotiation | 1 or more | **Met: 6 broken obligations** (`cheap_talk`: licence instalments not paid within the grace period) and **1 renegotiation** (`enforced`: a signed amendment of C4) |
| Replay deterministic | verify clean | Met in both arms |
| Cost per game year measured | measured | Met (11.4) |
| Tender lines (not in the gate) | reported | `enforced` 1 contract (broken; penalty collected); `cheap_talk` 3 (1 kept, 1 partial, 1 broken; both penalties paid voluntarily) |

**Verdict: the gate passes under the consistent rule.** Deferred commitments were exercised: 38 instalment obligations fell due after the build right was in use, and both breach and renegotiation occurred between agents. Read 11.7 before using these numbers.

### 11.6 Transcript excerpts

1. A deferred promise kept by a renegotiation (`enforced`, s3 and s2):
   > s2 → s3: Accepted your Prarfingburg licence (C4). [...] Prarfingburg now refuses any new station of mine (town station limit after your airport), so I may not be able to use C4 [...]
   > s3 → s2: Thank you; Sindbury airport built. If Prarfingburg proves unusable, please propose a zero-fee amendment to C4 and I will accept.

   Both parties then signed the amendment of C4 to fee 0 (ticks 11177 and 11178).

2. Instalment terms agreed in words (`cheap_talk`, s1 and s0):
   > s1 → s0: Accepted C7. Please pay the licence instalments through the exchange ledger as they fall due.
   > s0 → s1: Thanks B for accepting C7! I'll pay each instalment through the ledger when due.

3. A missed instalment, a late payment, and the payee asking for the rest (`cheap_talk`, s0 and s1):
   > s0 → s1: sorry, I missed the instalment due at tick 26199 - just paid it (4000) late. Next ones will be on time.
   > s1 → s0: The late C7 payment was applied to the next instalment; the earlier £4,000 is marked broken, so please send that amount as a separate ledger payment when convenient.
   > s0 → s1: paid 4000 for C7 instalment O25 which I missed (as a plain ledger payment).

4. A grantor that raises the fee when its own licence turns out useless (`cheap_talk`, s2 to s3):
   > Netborough already has two airports so it is likely capped too. I can license you a northern Sindbury rect [...]; since your Prarfingburg/Netborough licences aren't buildable for me, I'd ask a fee of 10000.

5. A tender penalty paid voluntarily, with no collection (`cheap_talk`, s0): a payment of £20,790 to the exchange tagged to tender T4, at the tick of the T4 settlement.

### 11.7 What pilot v3 does not show

- **The 6 breaches are late payment by the payers, not a scheduling artefact.** `obligation_due` is a forced wake in both arms; it fired 12 times in the `cheap_talk` run, and **each of the 6 broken instalments (O18 to O20 of s2; O23, O25 and O29 of s0) woke its payer with the logged reason `obligation_due` at its due tick**. The payers had a turn at each due tick and did not pay within the 30-day grace period. All 6 were paid later, outside the grace period, as untagged payments with a memo that names them (days late from the due tick: O23 127, O25 37, O29 106, O18 240, O19 150, O20 60). On the other due ticks, the logged reason of s3 was `deadline` (its `until=deadline` matches the due tick); s3 paid all its instalments on time. The `rh wait` reply now lists every reason of the tick, so a coinciding reason cannot hide `obligation_due` (WAYBILL.md 9.4).
- **The allocation rule hid one late payment.** s0's first late payment was tagged to C7 after O23 had closed, so (rules 1) it counted toward O24 (marked on time) and O23 stayed broken. Rules 2 allocate FIFO (below).
- **One seed, one game per arm.** No claim about enforcement follows. The arms differ in many compounding early choices (13 offers in each arm, but 8 against 6 signed, and different fees).
- **No supply contract was offered** in either arm, again. All agent-to-agent commitments are licence fees.
- **Obligations near the horizon never settle** (3 in `cheap_talk`): an obligation whose settle tick is after the horizon stays open.

### 11.8 Corrected v3 numbers (measurement fixes)

The fixes (exchange rules 2: FIFO allocation of tagged payments, late payments, no instalment due after the horizon minus the grace period; the payment-timing outcome; the pre-registered metrics) change how obligations are scored. The v3 logs were written under rules 1 and still verify unchanged (replay clean in both arms). Two readings:

**(a) The v3 obligations as they were, with the new metrics** (`contract_stats` on the verified exchange state; only tagged payments count):

| Metric | `enforced` | `cheap_talk` |
|---|---|---|
| Obligations (agent to agent) | 8 | 30 |
| on_time / late / partial / unpaid | 8 / 0 / 0 / 0 (collected) | 24 / 0 / 0 / 6 |
| On-time rate (secondary) | 100% | 80% |
| Amount-weighted repayment rate (primary) | 100% | 73.9% (27,324 of 37,000) |
| Including the 4 untagged payments that name the 6 missed instalments (9,676) | 100% | 100%, of which 6 obligations late by 37 to 240 days |

**(b) A counterfactual rescore under rules 2** (`railhead waybill-run rescore <run_dir>`, `Exchange.rescore`): the same company records, applied again under rules 2. `enforced`: 7 obligations (the two instalments of C13 fold into one), all on time, repayment 100%. `cheap_talk`: 27 obligations: 16 on time, 6 late (days late 44 to 240), 2 partial, 3 unpaid; on-time rate 59%; repayment 73.9%; 10 broken and 1 partial at the settle tick. This reading is **not** what the agents would have done under rules 2: under rules 1, s1 told s0 that a late tagged payment had gone to the next instalment and asked for a separate untagged payment, and s0 did that. Under FIFO, each later payment of s0 moves to an older instalment and the lateness cascades. A rerun is needed for v3 numbers under rules 2.

The gate under the consistent rule does not change: (a) 14 signed, 6 breaches, 1 renegotiation.

## 12. Preflight before the lock (2026-11-19)

A check of the full pipeline on the locked code, before the pre-registration hash. Seed 2 was played before (pilots v2 and v3), so these games are not held out and are not in any result.

- Setup: short track, seed 2, rotation 0, both arms, exchange rules 2; two Claude Opus 5.5 and two Codex gpt-6.1-terra seats; the runner flags of prereg section 9 (`--all-slots --turn-s 300 --max-turns 200 --grace-s 180 --traces off --usd-cap 60 --run-s 9000`). The `cheap_talk` arm used a second Claude login (`--claude-profile`) and `--sandbox-deny` for the paired arm.

| Arm | Verifier | Replay equal to live | Ended | Steps | Cost (Claude) | Wall time |
|---|---|---|---|---|---|---|
| `enforced` | clean | yes | horizon | 202 | $12.78 | 42 min |
| `cheap_talk` | clean | yes | horizon | 217 | $14.41 | 29 min |

- `railhead waybill-deliveries` and `railhead waybill-analyze` ran on the pair: 0 exclusions, 1 paired seed (underpowered, as expected).
- Failed actions: 4,757 of 5,397 (`enforced`) and 900 of 1,705 (`cheap_talk`). Of these, 98% and 89% are dry runs (`dry_run: true`) that probe tiles for a station site (`ERR_FLAT_LAND_REQUIRED`, `ERR_AREA_NOT_CLEAR`). This is agent behaviour, not a defect.
- A Linux-only defect of the campaign (a restarted campaign resumed live games) was found by CI and fixed before the lock (CHANGELOG, Fixed).
