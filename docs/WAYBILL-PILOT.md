# Waybill pilot (Phase 5)

**Pilot v1: FAILED.**

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

## 7. Reproduction

```
python -m railhead.multi.gen track short 11 1 <tasks>
railhead waybill-run run <tasks>/waybill-short-s0011-r0-enforced <run> claude:default codex:default \
    opencode:opencode/big-pickle codex:default --all-slots --turn-s 300 --run-s 10800 --usd-cap 28 \
    --max-turns 200 --grace-s 180
```

The run directories (transcripts, merged logs, `result.json`) are not in the repository.
