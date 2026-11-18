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

**Arm parity.** The arms allow the same promises and give the same exchange payouts. They differ only in
whether an amount that a company owes under a contract (an obligation, below) is collected automatically.

| Item | `enforced` | `cheap_talk` |
|---|---|---|
| Supply contract: escrow at signing | Yes (price and bond) | No |
| Tender award: the exchange pays the winner for the delivered share | Yes | **Yes** |
| Licence build right | Yes | Yes |
| Obligation (licence instalment, supply payment or penalty, tender penalty) | Collected at its due tick | Recorded; the payer pays it voluntarily with `pay` tagged to the contract, until due + 30 days (`GRACE_TICKS`) |
| Obligation outcome | `kept` (collected; a negative balance is the debt) | `kept` / `partial` / `broken` from the tagged payments |
| Contract outcomes visible to agents | Yes | No (settled contracts read `closed`); each party sees its own obligations |
| Ledger visible to agents | All accounts and transfers | Own balance and own transfers |

In both arms, `pay` moves money. Before arm parity (pilots v1 and v2), `cheap_talk` tender awards and
settlements moved no money at all.

## Accounts

| Account | Rule |
|---|---|
| `<company id>` | Starts at its `start_balances` value (default 0). A voluntary debit must stay at or above `-credit_limit`. The default limit is `None` (no limit). |
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
| `licence` | `grantor`, `grantee`, `rect` (`[x0, y0, x1, y1]`, both corners included), `window_start`, `deadline`, `fee` | Tradable |
| `loan` | `lender`, `borrower`, `principal`, `interest_bp`, `due_tick`, `collateral_share_bp` (0 to 10000) | Schema only |

All numbers are non-negative integers. A boolean is not an integer. The two parties must be different.
For `supply` and `licence`, `deadline` must be after `window_start`. For `licence`, `x0 <= x1` and
`y0 <= y1`. An offer of `loan` gives `kind_not_enabled`.

### Licences

A licence gives the grantee the right to build inside `rect` of the grantor's franchise region, from
`window_start` until `deadline` (the right ends at the deadline tick).

| Item | Rule |
|---|---|
| Fee | Paid in equal quarterly instalments, in arrears: one obligation each quarter (90 days) after `window_start` before `deadline`, and the last at `deadline`. The remainder of the integer division goes on the last instalment; an instalment of 0 is dropped. A fee of 0 gives no obligations. Nothing is paid at signing. |
| Amendment | When both parties sign new terms, the open instalments that are not yet due are replaced (status `replaced`): the new fee minus the instalments already due is spread over the instalment ticks of the new terms after the amendment tick. |
| Build right | The same in both arms. Thus the arms differ in money only, not in what is physically possible. |
| End | At `deadline`, the exchange writes a `settle` record with `delivered` 0, `parts` `[]` and the outcome `ended`. No money moves. |
| Built infrastructure | It stays after the end, with the grantee as owner. New builds in the rectangle need a live licence. |
| Revocation | Not possible. An amendment (both parties sign the same terms) can shorten the term or change the rectangle. |
| Region | The exchange does not know the regions. The coordinator refuses a licence offer whose rectangle is not inside the grantor's region. At each build, the coordinator and the replay clip the rectangle to the grantor's region at that tick. |

The schema changed from `tiles`, `term`, `annual_fee` (never tradable) to `rect`, `window_start`, `deadline`,
`fee`: a tile list cannot describe a route, absolute ticks reuse the forced stops and wakes of supply
contracts. The fee was one-off at signing (pilot v2); it is now paid in instalments, so that every paid
licence creates obligations that fall due after the right is in use.

## Obligations

An obligation is an amount that one party owes another under a contract: `{id (O1, O2, ...), contract_id,
kind, payer, payee, amount, due, settle_tick, paid, paid_by_due, payments, collected, status}`.

| Kind | Created | Payer → payee | Due |
|---|---|---|---|
| `licence_instalment` | At signing (and at a signed amendment) | grantee → grantor | Each instalment tick |
| `supply_penalty` | At settlement, when D < Q | seller → buyer (after the bond, in `enforced`) | The settlement tick |
| `supply_payment` | At settlement, `cheap_talk` only (nothing was escrowed) | buyer → seller | The settlement tick |
| `tender_penalty` | At settlement of a tender award, when D < Q | winner → `exchange` | The settlement tick |

- `settle_tick` is `due` in `enforced` and `due + GRACE_TICKS` (30 days) in `cheap_talk`.
- `pay` with `contract_id` counts toward the payer's open obligations of that contract to `to`, the oldest due first (an overpayment counts toward the last). A payment at or before `due` also counts in `paid_by_due`. A tag that names no open obligation of the payer to `to` is refused (`bad_tag`). A payment to `exchange` needs a tag (a tender penalty).
- At `settle_tick` the driver writes `obligation_settle` {`obligation_id`, `collected`, `paid`, `outcome`}. `enforced`: `collected` = amount − paid, moved from payer to payee (forced: the balance can go negative), outcome `kept`. `cheap_talk`: `collected` 0; outcome `kept` if paid ≥ amount, `partial` if 0 < paid < amount, `broken` if paid = 0.
- `deadlines()` gives `obligation_due` at `due` (the payer must wake: a forced wake reason in the coordinator) and, in `cheap_talk`, `obligation_settle` at `settle_tick` (a forced stop).
- **Rules 2** (`init` fields `rules: 2` and `horizon_tick`; new track tasks set `objective.market.rules = 2`; a log without the fields keeps rules 1 and verifies as before):
  - FIFO: a tagged payment goes to the oldest obligation of the contract (payer to `to`) that is not paid in full, also after its settlement (a late payment). The settle-time outcome (kept / partial / broken) does not change; the payment counts in `paid`, and `completed_tick` is the tick at which the obligation was paid in full.
  - End of play: no licence instalment falls due after `horizon_tick - GRACE_TICKS`. The amounts of later instalments fold into the last instalment that falls due in time (one instalment at that tick if none does).
  - `payment_timing(o)` gives the timing outcome at the end of play: `on_time` (paid in full before the settle tick: in `cheap_talk` by the due tick plus the 30-day grace period; in `enforced`, collected at the due tick), `late` (paid in full later, with the days late from the due tick), `partial`, or `unpaid`.
  - `Exchange.rescore(records, horizon_tick)` applies an existing (rules-1) log again under rules 2 and makes the `obligation_settle` records again: an analysis, not a verification.
- `obligations_of(company)` gives the obligations that a company owes or is owed (not the replaced ones); `rh contracts` (per contract, `your_obligations`) and `rh market` (the open ones) show them.

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
| Penalty from seller balance | The remainder, as a `supply_penalty` obligation due at the settlement tick (`enforced`: collected at once, ignoring the credit limit; a negative balance is the debt). |
| Bond returned to seller | `B - penalty from bond` |
| Outcome | `kept` if D >= Q; `broken` if D = 0; else `partial` |

After settlement, both escrow accounts are 0. In `cheap_talk`, a bilateral contract has no escrow: the
seller payment and the penalty become obligations. A tender award has escrow in both arms (the exchange
pays the winner); its penalty is a `tender_penalty` obligation.

## Tenders

`open_tenders(tick, quarter, industries)` makes the tenders for one quarter. Each industry is
`{id, type, produces: [cargo], accepts: [cargo], tile: [x, y], region}`.

- A candidate is an industry I and a cargo C. I accepts C, and an industry in a different region produces C.
- With `reach_tiles` > 0 (the task tracks use 128), also: no industry in I's own region produces C within
  `reach_tiles` (Manhattan), and the nearest producer in another region is within it. Every such tender
  needs a station in another company's region: a licence or a subcontract.
- `distance` is the Manhattan distance from I to the nearest such producer.
- The exchange picks up to `per_quarter` candidates with draws over `(seed, quarter)`.
- `TenderConfig` sets quantity, maximum price, penalty, bid period and delivery window. The class
  docstring gives the formula.

Bids are sealed. Each company can bid one time, from 0 to `max_price`, before `bid_close`. At `bid_close`,
the lowest price wins. A tie goes to the earliest bid tick, then to the lowest company id. Log order in one
tick does not count. The winner gets a supply contract with the same id as the tender. Its buyer is
`exchange`, its price is the winning bid and its window is `[bid_close, deadline]`.

If the first `advance` after `bid_close` is at or after the tender deadline, the award lapses with no winner.

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
| `init` | constructor (system) | `arm`, `companies`, `seed`, `credit_limit`, `tender_config`, `start_balances` (money from `exchange` to each company: unequal starts) |
| `offer` | company | `contract_id`, `kind`, `terms`, `to`, `expires_tick` |
| `counter` | company | `contract_id`, `terms`, `expires_tick` |
| `accept` | company | `contract_id` |
| `withdraw` | company | `contract_id` |
| `amend` | company | `contract_id`, `terms` |
| `tender_bid` | company | `tender_id`, `price` |
| `msg` | company | `to`, `text` |
| `pay` | company | `to`, `amount`, `memo`, `contract_id` (optional; `null` or absent: an untagged payment) |
| `expire` | `advance` (system) | `contract_id` |
| `tender_award` | `advance` (system) | `tender_id`, `winner`, `price` (both `null` if no bids) |
| `activate` | `advance` (system) | `contract_id` |
| `tender_open` | `open_tenders` (system) | `quarter`, `tenders` (the generated tenders) |
| `settle` | `settle_due` (system) | `contract_id`, `delivered`, `parts` (`[company, start, end, count]`), `outcome` |
| `obligation_settle` | `settle_due` (system) | `obligation_id`, `collected`, `paid`, `outcome` |

Replay checks each record again. A `settle` or `tender_award` record whose result differs from the
computed result gives `record_mismatch`. So does an `obligation_settle` record whose `collected`, `paid`
or `outcome` differs from the computed one.

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

- Do not show raw records or `state()` to agents. A `settle` record contains the outcome in both arms.
  Use `public_view` and the query methods.
- Write the records to the merged action log in `seq` order. `seq` is the exchange counter, not the index
  in the merged log. Replay the records with `Exchange.replay`. Compare `state()` for a determinism check.
- A `msg` record contains the full text. If the log must keep only a digest, keep the text in a side file.
- In `enforced`, all companies see the `pay` memo in the public ledger.
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
