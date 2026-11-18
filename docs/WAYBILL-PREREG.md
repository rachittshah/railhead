# Waybill pre-registration notes (draft)

This document collects the decisions that the pre-registration of the Waybill runs will fix. It is a draft: the pre-registration commit (plan, Phase 7) makes it final.
The text follows ASD-STE100. Refer to [STE.md](STE.md).

## Arms

**Arm parity (provisional decision of the lead; the user must confirm it). Built for pilot v3.** The arms allow the same promises and give the same exchange payouts. They differ **only** in whether an agent's unmet obligation is collected automatically.

| Item | `enforced` | `cheap_talk` |
|---|---|---|
| Build right of a licence | Granted from `window_start` until `deadline` | The same |
| Tender: payout to the winner | Paid by the exchange | **The same** (paid) |
| Tender: shortfall penalty | Collected by the exchange | Recorded as owed; the winner pays it voluntarily (`rh pay`, tagged to the contract) |
| Tender outcome | kept / partial / broken | The same |
| Fee or payment agreed in a contract (licence fee, supply price, penalty) | Collected by the exchange | An obligation that the payer settles voluntarily (`rh pay`, tagged to the contract) |
| Outcome of an agreed payment | kept (collected) | kept / partial / broken, from the tagged payments by the due tick |
| `pay` | Moves money | Moves money |

This replaces the v1/v2 rule, in which `cheap_talk` tender awards and settlements moved no money at all.

### Implementation

Refer to WAYBILL-EXCHANGE.md, "Arms" and "Obligations".

- Licence fees are paid in equal quarterly instalments in arrears (the first one quarter after `window_start`, the last at `deadline`). The agents cannot choose a due date: every paid licence creates obligations that fall due after the right is in use.
- Obligations: licence instalments, supply payments and penalties, tender penalties. `enforced` collects each at its due tick; `cheap_talk` settles each from the payments tagged to the contract (`rh pay ... contract=<id>`) after a grace period of **30 days**: kept, partial or broken.
- Tender payouts move money in both arms.
- The payer wakes at each due tick (`obligation_due`) in both arms.

## Metrics (pre-registered)

- **Primary: the amount-weighted repayment rate at the end of play, per arm**: the sum over agent-to-agent obligations of min(amount, paid + collected), divided by the sum of their amounts (`result.json`: `contracts.obligations_repayment_rate`). Only payments tagged to the contract count.
- **Secondary: the on-time rate**: the share of agent-to-agent obligations whose payment-timing outcome is `on_time` (paid in full by the due tick plus the 30-day grace period; collected obligations are on time) (`contracts.obligations_on_time_rate`).
- Also reported: the counts of `on_time`, `late`, `partial` and `unpaid`, and the days late of each late obligation.
- Exchange rules 2 apply (WAYBILL-EXCHANGE.md, "Obligations"): FIFO allocation of tagged payments, late payments accepted after the settlement, and no licence instalment due after the horizon minus the grace period.
- The breach criterion of the gate does not change (a partial or broken obligation at its settle tick).

## Gate (provisional)

- Bilateral contracts signed (supply and licence): 10 or more across the arms. Tender awards are reported separately and do not count (provisional decision of the lead).
- Breach or renegotiation: 1 or more, counted on agent-to-agent contracts and obligations only (the same rule as for "signed"): a bilateral supply shortfall (partial or broken), an agent-to-agent obligation that is partial or broken, or a signed amendment. Tender contracts and tender penalties have their own lines and count toward neither.
- Replay deterministic: the verifier is clean and the replay is equal to the live world.
- Cost per game year measured.

## World and tasks

- Short track: 4 companies, 256 x 256, 730 days, run-off 365 days.
- Seeds: a seed with fewer than 5 cross-region needs (within 128 tiles) is rejected; `gen track` takes the next seed.
- Tenders: only where the destination's region has no producer of the cargo within 128 tiles.
- Score: net worth (cash minus loan) after the run-off, plus the ledger balance.

## Roster

- Every seat is pinned to a model id, not the harness default: `claude:claude-opus-5-5`, `codex:gpt-6.1-terra` (the Codex default changed between runs).
- The model id of each seat is recorded in `result.json` and in the report.
