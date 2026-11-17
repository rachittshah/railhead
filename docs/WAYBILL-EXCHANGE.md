# Waybill exchange: records and rules

The package `railhead.exchange` is the market core of Waybill. It holds contracts, the off-world ledger,
world tenders, private messages and the public order book. It does not use the OpenTTD engine. The
coordinator gives it ticks, delivery counts and industry snapshots.

All arithmetic uses integers. The only randomness is a SHA-256 draw over the seed and labels.

## Modules

| Module | Contents |
|---|---|
| `contracts.py` | JSON schemas for `supply`, `licence` and `loan`. Terms validation. The lifecycle state machine. |
| `ledger.py` | Integer ledger in pounds. Balanced transfers only. Conservation check. Transfer history. |
| `settlement.py` | Supply settlement from the delivered quantity, for the `enforced` and `cheap_talk` arms. |
| `tenders.py` | Tender generation from an industry snapshot. The sealed-bid auction rule. |
| `exchange.py` | The `Exchange` facade. Commands, coordinator drivers, queries, `apply` and `replay`. |
| `errors.py` | `ExchangeError` with a stable `code`. |

## Arms

| Arm | Escrow at signing | Settlement | Outcomes visible to agents | Ledger visible to agents |
|---|---|---|---|---|
| `enforced` | Yes | Moves money | Yes | All accounts and all transfers |
| `cheap_talk` | No | Computed, no money moves | No | Own balance and own transfers |

In both arms, `pay` moves money. In `cheap_talk`, tender awards also move no money.

## Accounts

| Account | Rule |
|---|---|
| `<company id>` | Starts at 0. A voluntary debit must stay at or above `-credit_limit`. The default limit is `None` (no limit). |
| `exchange` | Funds tender escrow. Receives tender penalties. It can go negative without a limit. |
| `escrow:<contract id>` | Holds the price of a signed contract. It is never negative. |
| `escrow:<contract id>:bond` | Holds the seller bond. It is never negative. |

Each operation is one transfer from one account to a different account. The sum of all balances is always 0.
`Ledger.check_conservation()` also replays the history and compares it with the balances.

Company ids are non-empty strings. They must not contain `:` and must not be `exchange`.

## Contract types

| Kind | Fields | Status |
|---|---|---|
| `supply` | `seller`, `buyer`, `cargo`, `industry`, `quantity` (Q, at least 1), `window_start`, `deadline`, `price`, `penalty_per_unit`, `bond` (optional, default 0) | Tradable |
| `licence` | `grantor`, `grantee`, `tiles` (unique ints), `term`, `annual_fee` | Schema only |
| `loan` | `lender`, `borrower`, `principal`, `interest_bp`, `due_tick`, `collateral_share_bp` (0 to 10000) | Schema only |

All numbers are non-negative integers. A boolean is not an integer. The two parties must be different.
For `supply`, `deadline` must be after `window_start`. An offer of `licence` or `loan` gives
`kind_not_enabled`.

## Lifecycle

```
offered -> countered (repeatable) -> signed -> active -> settled (kept | partial | broken)
offered | countered -> expired | withdrawn
signed | active: amendment
```

- **Offer.** The offerer is one party. An addressed offer names the other party in the terms and in `to`.
  A public offer has `to = null` and the other party `null`; it shows in the order book.
- **Counter.** Only the party whose turn it is can counter. The parties stay the same. A counter on a public
  offer binds the counterparty and removes the offer from the order book.
- **Accept.** Only the party whose turn it is can accept, before `expires_tick` and before `deadline`.
  In `enforced`, the buyer pays `price` into escrow and the seller pays `bond` into the bond account.
- **Activate.** A signed contract becomes active at `window_start`. If the window is open at signing, it
  becomes active at once.
- **Withdraw.** A negotiator can withdraw an open offer. A signed contract cannot be withdrawn.
- **Expire.** An open offer expires at `expires_tick`. The default life of an offer is 30 game days.
- **Amend.** A party sends new terms. If the other party already sent identical terms, the amendment is
  signed. Otherwise the new terms replace the pending proposal. The parties cannot change. The amendment
  must be signed before the current deadline. It replaces the terms from its signing tick
  (`terms_history`). In `enforced`, escrow and bond go up or down by the difference. Tender awards cannot
  be amended.

## Settlement

The coordinator calls `settle_due(tick, deliveries_fn)` for each contract with `deadline <= tick`. The
function signature is `deliveries_fn(company, cargo, industry, start_tick, end_tick) -> int`. Both ends
are inclusive. For a supply contract, the count is the seller's deliveries in `[window_start, deadline]`
of the current terms.

With D delivered, Q quantity, P price, K penalty per unit and B bond:

| Item | Value |
|---|---|
| Seller payment from escrow | `P * min(D, Q) // Q` |
| Buyer refund from escrow | `P - seller payment` |
| Penalty | `K * max(0, Q - D)` |
| Penalty from bond | `min(B, penalty)` |
| Penalty from seller balance | The remainder. This transfer ignores the credit limit; a negative balance is the debt. |
| Bond returned to seller | `B - penalty from bond` |
| Outcome | `kept` if D >= Q; `broken` if D = 0; else `partial` |

After settlement, both escrow accounts are 0.

## Tenders

`open_tenders(tick, quarter, industries)` makes the tenders for one quarter. Each industry is
`{id, type, produces: [cargo], accepts: [cargo], tile: [x, y], region}`.

- A candidate is an industry I and a cargo C. I accepts C, and an industry in a different region produces C.
- `distance` is the Manhattan distance from I to the nearest such producer.
- The exchange picks up to `per_quarter` candidates with draws over `(seed, quarter)`.
- `TenderConfig` sets quantity, maximum price, penalty, bid period and delivery window. The class
  docstring gives the formula.

Bids are sealed. Each company can bid one time, from 0 to `max_price`, before `bid_close`. At `bid_close`,
the lowest price wins. A tie goes to the earliest bid, then to the lowest company id. The winner gets a
supply contract with the same id as the tender. Its buyer is `exchange`, its price is the winning bid and
its window is `[bid_close, deadline]`.

Subcontract deliveries count toward a tender. They come from a supply contract with these properties:

- its buyer is the tender holder;
- its cargo and industry are the same as the tender;
- it was signed before the tender deadline.

The seller's deliveries count from the later of the tender window start and the subcontract signing tick.

## Records

Each state change is one JSON-safe record. `Exchange.apply(record)` is the only mutation path.
`Exchange.replay(records)` builds the same state again. A rejected command raises `ExchangeError` and
writes no record.

All records have `op`, `seq` (0, 1, 2 and so on), `tick` (never decreases) and `company`. `company` is
`null` for system records.

| `op` | Written by | Fields |
|---|---|---|
| `init` | constructor (system) | `arm`, `companies`, `seed`, `credit_limit`, `tender_config` |
| `offer` | company | `contract_id`, `kind`, `terms`, `to`, `expires_tick` |
| `counter` | company | `contract_id`, `terms`, `expires_tick` |
| `accept` | company | `contract_id` |
| `withdraw` | company | `contract_id` |
| `amend` | company | `contract_id`, `terms` |
| `tender_bid` | company | `tender_id`, `price` |
| `msg` | company | `to`, `text` |
| `pay` | company | `to`, `amount`, `memo` |
| `expire` | `advance` (system) | `contract_id` |
| `tender_award` | `advance` (system) | `tender_id`, `winner`, `price` (both `null` if no bids) |
| `activate` | `advance` (system) | `contract_id` |
| `tender_open` | `open_tenders` (system) | `quarter`, `tenders` (the generated tenders) |
| `settle` | `settle_due` (system) | `contract_id`, `delivered`, `parts` (`[company, start, end, count]`), `outcome` |

Replay checks each record again. A `settle` or `tender_award` record whose result differs from the
computed result gives `record_mismatch`.

## Commands and queries

| `rh` verb | Method |
|---|---|
| `market` | `market(company)` |
| `tender bid` | `tender_bid(tick, company, tender_id, price)` |
| `offer` | `offer(tick, company, kind, terms, to=None, expires_tick=None)` |
| `counter` | `counter(tick, company, contract_id, terms, expires_tick=None)` |
| `accept` | `accept(tick, company, contract_id)` |
| `amend` | `amend(tick, company, contract_id, terms)` |
| `withdraw` | `withdraw(tick, company, contract_id)` |
| `contracts` | `contracts(company)` |
| `msg` | `msg(tick, company, to, text)` |
| `inbox` | `inbox(company)` |
| `pay` | `pay(tick, company, to, amount, memo="")` |
| `ledger` | `ledger(company)` |

Coordinator drivers:

- `advance(tick)` writes expiries, tender awards and activations that are due.
- `settle_due(tick, deliveries_fn)` calls `advance`, then settles due contracts.
- `open_tenders(tick, quarter, industries)` opens the tenders for one quarter.
- `deadlines()` gives `[{tick, kind, id, companies}]` for forced wake-ups. The kinds are
  `offer_expiry`, `window_start`, `bid_close` and `deadline`.

`public_view(company)` gives everything that one company can see. It never shows the messages of other
companies or sealed bids. In `cheap_talk`, it shows settled contracts as `closed` and has no
`settlements` key.

## Notes for the coordinator

- Do not show raw records to agents. A `settle` record contains the outcome in both arms. Use
  `public_view` and the query methods.
- Write the records to the merged action log in `seq` order. Replay them with `Exchange.replay`. Compare
  `state()` for a determinism check.
- Call `settle_due` and `advance` at each step tick. Use `deadlines()` to choose wake-ups.
- The plan counts deliveries from the signing tick, because monitors start at signing. The exchange asks
  for `[window_start, deadline]`. If a cargo monitor did not exist before signing, `deliveries_fn` returns
  only what the monitor saw.
- `check_invariants()` checks conservation, escrow against contracts, and (in `cheap_talk`) that only
  `pay` moved money.

## Error codes

`already_bid`, `bad_amount`, `bad_arm`, `bad_company`, `bad_config`, `bad_delivered`, `bad_memo`,
`bad_message`, `bad_price`, `bad_record`, `bad_snapshot`, `bad_state`, `bad_terms`, `bad_transition`,
`deadline_passed`, `escrow_mismatch`, `insufficient_funds`, `kind_not_enabled`, `not_amendable`,
`not_due`, `not_party`, `not_your_turn`, `offer_expired`, `record_mismatch`, `self_transfer`,
`tender_closed`, `time_reversed`, `unknown_account`, `unknown_company`, `unknown_contract`,
`unknown_kind`, `unknown_tender`.
