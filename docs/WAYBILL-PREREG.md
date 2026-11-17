# Waybill pre-registration notes (draft)

This document collects the decisions that the pre-registration of the Waybill runs will fix. It is a draft: the pre-registration commit (plan, Phase 7) makes it final.
The text follows ASD-STE100. Refer to [STE.md](STE.md).

## Arms

| Item | `enforced` | `cheap_talk` |
|---|---|---|
| Supply contract | Escrow at signing; settlement moves money | No escrow; settlement is computed but moves no money |
| Tender award | Paid by the exchange through the ledger | No money moves |
| Licence: build right | Granted from `window_start` until `deadline` | **The same** |
| Licence: fee | Paid by the grantee to the grantor at signing | No money moves |
| `pay` | Moves money | Moves money |
| Outcomes and ledger visible to agents | Yes, all accounts | No outcomes; own balance and transfers only |

The build right of a licence is the same in both arms. Thus the arms differ only in money and in what the agents can see, not in what is physically possible. A difference between the arms is then a difference in behaviour, not in the physical options.

## Gate (provisional)

- Bilateral contracts signed (supply and licence): 10 or more across the arms. Tender awards are reported separately and do not count (provisional decision of the lead).
- Breach or renegotiation: 1 or more. Partial or broken contracts, also tender contracts, count; amendments count as renegotiations.
- Replay deterministic: the verifier is clean and the replay is equal to the live world.
- Cost per game year measured.

## World and tasks

- Short track: 4 companies, 256 x 256, 730 days, run-off 365 days.
- Seeds: a seed with fewer than 5 cross-region needs (within 128 tiles) is rejected; `gen track` takes the next seed.
- Tenders: only where the destination's region has no producer of the cargo within 128 tiles.
- Score: net worth (cash minus loan) after the run-off, plus the ledger balance.

## Roster

- The Claude seat is pinned to a model id (`claude:claude-opus-5-5`), not the harness default.
- The model id of each seat is recorded in `result.json` and in the report.
