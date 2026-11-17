# Waybill pre-registration notes (draft)

This document collects the decisions that the pre-registration of the Waybill runs will fix. It is a draft: the pre-registration commit (plan, Phase 7) makes it final.
The text follows ASD-STE100. Refer to [STE.md](STE.md).

## Arms

**Arm parity (provisional decision of the lead; the user must confirm it).** The arms allow the same promises and give the same exchange payouts. They differ **only** in whether an agent's unmet obligation is collected automatically.

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

### Proposed implementation (not built)

1. `pay` gets an optional `contract_id`. The ledger records the reference; the exchange keeps, for each obligation, the sum paid towards it.
2. Obligations become explicit: when a contract creates a payment (licence fee at `fee_due`, supply price and penalty at the deadline, tender penalty at the deadline), the driver writes an `obligation` record {contract, payer, payee, amount, due}.
3. `enforced`: the driver collects the obligation at `due` (a ledger transfer; the ledger can go negative) and settles it kept. `cheap_talk`: no transfer; at `due` plus a grace period the driver settles it kept (paid at least the amount), partial (0 < paid < amount) or broken (paid = 0).
4. Tender payouts move money in both arms (the exchange pays the winner for the delivered share).
5. The replay rebuilds the obligations and their settlements with the same driver, as for supply settlements today.
6. Views: `rh contracts` shows the open obligations (amount owed, due tick) in both arms.

## Gate (provisional)

- Bilateral contracts signed (supply and licence): 10 or more across the arms. Tender awards are reported separately and do not count (provisional decision of the lead).
- Breach or renegotiation: 1 or more, counted on agent-to-agent contracts and obligations only (the same rule as for "signed"): a partial or broken obligation, or a signed amendment.
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
