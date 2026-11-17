# Waybill pilot (Phase 5)

This document gives the numbers of the first Waybill pilot: four agents, one world, two game years, both arms.
The text follows ASD-STE100. Refer to [STE.md](STE.md). For the runner, refer to [WAYBILL.md](WAYBILL.md), section 12.

**Result: the gate fails.** The agents signed no bilateral contract and sent no message in either arm. Section 5 gives the diagnosis and section 6 the smallest design changes. The brief was not tuned.

## 1. Setup

| Item | Value |
|---|---|
| Task | Short track, seed 11, rotation 0 (`waybill-short-s0011-r0-<arm>`): 256 x 256 map, 4 companies, 730 days, no run-off |
| Arms | `enforced`, then `cheap_talk`. Same `start.sav`, same regions, same roster, same brief. |
| Roster | Slot 0: `claude:default`. Slot 1: `codex:default`. Slot 2: `opencode:opencode/big-pickle`. Slot 3: `codex:default`. Every slot has a live agent (`--all-slots`). |
| Models | Claude Code with its own config directory selects `claude-sonnet-5-5` as its default model. Codex selects `gpt-6.1-sol` (reasoning effort high). opencode uses the free model `big-pickle`. |
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

| Item | `enforced` | `cheap_talk` |
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

## 5. Diagnosis

1. **The world needs few partners.** Only 3 of 43 deliveries within 128 tiles need another region (section 1.1). Each region has its own mines, farms, and processing industries, so the most profitable routes are local.
2. **Tenders do not need a partner.** A tender candidate is a destination that accepts a cargo that some other region produces. The destination's own region can also produce that cargo. Slot 3 delivered its tender from its own mine. Thus a tender is a subsidy for a local route, not a cross-region need.
3. **Nobody makes the first offer.** Both Codex agents waited 90 to 120 days at a time with `until=offer_received,message_received`, and slot 3 wrote that "the exchange currently has no offers". Each agent waited for an offer, and no agent made one.
4. **Claude plays very little.** Claude made 34 and 43 requests in two game years. After the first months it waited 120 days at a time and did not look at the market again. The model is `claude-sonnet-5-5` (the default of a fresh config directory), not Opus.
5. **The score punishes assets.** The score is cash minus loan. Company value (vehicles, stations) does not count. Claude's report gives a company value of £87,163 and a score of -14,694. This makes long-term investment, also in a partner's route, less attractive.

## 6. Smallest design changes

The brief stays as it is. These changes are in the world and the market:

| Change | Where | Expected effect |
|---|---|---|
| A tender candidate needs a destination whose own region has **no** producer of the cargo (within a distance) | `exchange/tenders.py`, `candidates()` | Every tender then needs a station in another region: a subcontract or a supply contract |
| Region generation that separates producers from consumers (for example, the primary industries of a cargo in one region and its accepting industries in another), and a generator check that `cross_only` is at least a minimum | `multi/regions.py`, `multi/gen.py` | Many profitable routes need a partner |
| Seed selection: reject seeds whose `region_needs.cross_only` is below a threshold | `multi/gen.py` | Paired worlds all have partner needs |
| Optional: count company value in the score, as in single-agent Railhead (or give a run-off) | `multi/verify.py`, track config | Investments, also in shared routes, are not a loss at the end |

A second pilot with the first two changes, the same roster and the same brief, tests whether the gate then passes. Opus as the Claude model is a separate decision.

## 7. Reproduction

```
python -m railhead.multi.gen track short 11 1 <tasks>
railhead waybill-run run <tasks>/waybill-short-s0011-r0-enforced <run> claude:default codex:default \
    opencode:opencode/big-pickle codex:default --all-slots --turn-s 300 --run-s 10800 --usd-cap 28 \
    --max-turns 200 --grace-s 180
```

The run directories (transcripts, merged logs, `result.json`) are not in the repository.
