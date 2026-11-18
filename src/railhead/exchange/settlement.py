"""Settlement of a supply contract from the delivered quantity D.

With quantity Q, price P, penalty per unit K and bond B (all integer pounds / game units):

- the seller receives ``P * min(D, Q) // Q`` from ``escrow:<id>``; the rest of the escrow returns to the buyer;
- the seller owes ``K * max(0, Q - D)`` to the buyer, paid from the bond first, then from the seller's
  balance (forced: the balance may go negative, which is the recorded debt); unused bond returns to the seller;
- outcome: ``kept`` if D >= Q, ``broken`` if D == 0, else ``partial``.

Arms: ``enforced`` moves the money; ``cheap_talk`` computes the same numbers and moves nothing.
"""

from __future__ import annotations

from typing import Any

from .errors import ExchangeError
from .ledger import Ledger, bond_account, escrow_account, is_int

ARMS = ("enforced", "cheap_talk")


def outcome_for(delivered: int, quantity: int) -> str:
    if delivered >= quantity:
        return "kept"
    return "broken" if delivered == 0 else "partial"


def compute_supply_settlement(terms: dict[str, Any], delivered: int) -> dict[str, int | str]:
    """Pure arithmetic: the transfers a settlement implies. No ledger needed."""
    if not is_int(delivered) or delivered < 0:
        raise ExchangeError("bad_delivered", f"delivered must be a non-negative int: {delivered!r}")
    q, price, ppu, bond = terms["quantity"], terms["price"], terms["penalty_per_unit"], terms.get("bond", 0)
    counted = min(delivered, q)
    seller_payment = price * counted // q
    penalty = ppu * (q - counted)
    from_bond = min(bond, penalty)
    return {
        "delivered": delivered,
        "quantity": q,
        "outcome": outcome_for(delivered, q),
        "seller_payment": seller_payment,
        "buyer_refund": price - seller_payment,
        "penalty": penalty,
        "penalty_from_bond": from_bond,
        "penalty_from_balance": penalty - from_bond,
        "bond_returned": bond - from_bond,
    }


def settle_supply(ledger: Ledger, contract_id: str, terms: dict[str, Any], delivered: int, *, arm: str,
                  tick: int = 0) -> dict[str, Any]:
    """Settle one supply contract. In ``enforced`` the escrow accounts must hold exactly price and bond; they
    end at 0. Returns the computed result plus ``arm``, ``published`` and ``debt_created``."""
    if arm not in ARMS:
        raise ExchangeError("bad_arm", f"{arm!r} (arms: {', '.join(ARMS)})")
    res: dict[str, Any] = dict(compute_supply_settlement(terms, delivered))
    res.update(contract_id=contract_id, arm=arm, published=arm == "enforced", debt_created=0)
    if arm == "cheap_talk":
        return res
    seller, buyer = terms["seller"], terms["buyer"]
    esc, bnd = escrow_account(contract_id), bond_account(contract_id)
    if ledger.balance(esc) != terms["price"] or ledger.balance(bnd) != terms.get("bond", 0):
        raise ExchangeError("escrow_mismatch", f"{contract_id}: escrow {ledger.balance(esc)}/{terms['price']}, "
                                               f"bond {ledger.balance(bnd)}/{terms.get('bond', 0)}")
    debt_before = max(0, -ledger.balance(seller))

    def move(src: str, dst: str, amount: int, reason: str, force: bool = False) -> None:
        if amount:
            ledger.transfer(src, dst, amount, reason=reason, ref=contract_id, tick=tick, force=force)

    move(esc, seller, res["seller_payment"], "settle_payment")
    move(esc, buyer, res["buyer_refund"], "settle_refund")
    move(bnd, buyer, res["penalty_from_bond"], "penalty_bond")
    move(bnd, seller, res["bond_returned"], "bond_return")
    move(seller, buyer, res["penalty_from_balance"], "penalty", force=True)
    res["debt_created"] = max(0, -ledger.balance(seller)) - debt_before if seller != "exchange" else 0
    return res
