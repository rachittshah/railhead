"""Known-answer settlement tests: kept, partial, broken, bond, debt, rounding, cheap-talk silence."""

import pytest

from railhead.exchange.errors import ExchangeError
from railhead.exchange.ledger import Ledger, bond_account, escrow_account
from railhead.exchange.settlement import compute_supply_settlement, outcome_for, settle_supply


def terms(**kw):
    t = {"seller": "s", "buyer": "b", "cargo": 1, "industry": 2, "quantity": 100, "window_start": 0,
         "deadline": 1000, "price": 1000, "penalty_per_unit": 5, "bond": 0}
    t.update(kw)
    return t


def funded(t, cid="C1"):
    led = Ledger(["s", "b"])
    led.transfer("b", escrow_account(cid), t["price"], reason="escrow", force=True) if t["price"] else None
    led.transfer("s", bond_account(cid), t["bond"], reason="bond", force=True) if t["bond"] else None
    return led


@pytest.mark.parametrize("d, q, out", [(0, 10, "broken"), (1, 10, "partial"), (9, 10, "partial"),
                                       (10, 10, "kept"), (25, 10, "kept")])
def test_outcome_for(d, q, out):
    assert outcome_for(d, q) == out


def test_kept_pays_full_price_and_no_penalty():
    t = terms()
    led = funded(t)
    r = settle_supply(led, "C1", t, 100, arm="enforced")
    assert r["outcome"] == "kept" and r["seller_payment"] == 1000 and r["penalty"] == 0
    assert led.balance("s") == 1000 and led.balance("b") == -1000
    assert led.balance(escrow_account("C1")) == 0
    led.check_conservation()


def test_over_delivery_is_capped_at_quantity():
    r = compute_supply_settlement(terms(), 250)
    assert r["seller_payment"] == 1000 and r["buyer_refund"] == 0 and r["penalty"] == 0


def test_partial_pro_rata_with_floor_and_penalty_from_balance():
    t = terms(quantity=3, price=100, penalty_per_unit=7)
    led = funded(t)
    r = settle_supply(led, "C1", t, 1, arm="enforced")
    # 100 * 1 // 3 = 33 to seller, 67 back to buyer, penalty 7 * 2 = 14 seller -> buyer
    assert (r["outcome"], r["seller_payment"], r["buyer_refund"], r["penalty"]) == ("partial", 33, 67, 14)
    assert led.balance("s") == 33 - 14 and led.balance("b") == -100 + 67 + 14
    assert r["debt_created"] == 0
    led.check_conservation()


def test_broken_with_bond_covering_part_of_penalty_creates_debt():
    t = terms(bond=200)                      # penalty 5 * 100 = 500: 200 from bond, 300 from balance
    led = funded(t)
    assert led.balance("s") == -200
    r = settle_supply(led, "C1", t, 0, arm="enforced")
    assert r["outcome"] == "broken" and r["seller_payment"] == 0 and r["buyer_refund"] == 1000
    assert (r["penalty_from_bond"], r["penalty_from_balance"], r["bond_returned"]) == (200, 300, 0)
    assert led.balance("s") == -500 and led.balance("b") == 500
    assert r["debt_created"] == 300
    assert led.balance(escrow_account("C1")) == 0 and led.balance(bond_account("C1")) == 0
    led.check_conservation()


def test_unused_bond_returns_to_seller():
    t = terms(bond=1000)
    led = funded(t)
    r = settle_supply(led, "C1", t, 90, arm="enforced")   # penalty 50 from bond, 950 back
    assert (r["penalty_from_bond"], r["bond_returned"], r["penalty_from_balance"]) == (50, 950, 0)
    assert led.balance("s") == 900 - 50 and led.balance(bond_account("C1")) == 0


def test_zero_price_contract_settles_penalty_only():
    t = terms(price=0)
    led = funded(t)
    r = settle_supply(led, "C1", t, 40, arm="enforced")
    assert r["seller_payment"] == 0 and r["penalty"] == 300 and led.balance("b") == 300


def test_cheap_talk_computes_the_same_numbers_and_moves_nothing():
    t = terms(bond=200)
    led = Ledger(["s", "b"])                 # no escrow in cheap talk
    r = settle_supply(led, "C1", t, 0, arm="cheap_talk")
    e = settle_supply(funded(t), "C1", t, 0, arm="enforced")
    for k in ("outcome", "seller_payment", "buyer_refund", "penalty", "penalty_from_bond"):
        assert r[k] == e[k]
    assert r["published"] is False and e["published"] is True
    assert led.history == [] and led.balances() == {"b": 0, "exchange": 0, "s": 0}


def test_escrow_mismatch_and_bad_inputs():
    t = terms()
    with pytest.raises(ExchangeError) as e:
        settle_supply(Ledger(["s", "b"]), "C1", t, 5, arm="enforced")
    assert e.value.code == "escrow_mismatch"
    with pytest.raises(ExchangeError) as e:
        settle_supply(funded(t), "C1", t, -1, arm="enforced")
    assert e.value.code == "bad_delivered"
    with pytest.raises(ExchangeError) as e:
        settle_supply(funded(t), "C1", t, 1, arm="honour")
    assert e.value.code == "bad_arm"


def test_exchange_as_buyer_receives_penalty_and_takes_no_debt():
    t = terms(buyer="exchange", bond=0)
    led = Ledger(["s"])
    led.transfer("exchange", escrow_account("T1"), 1000, reason="tender_escrow")
    r = settle_supply(led, "T1", t, 50, arm="enforced")
    assert led.balance("s") == 500 - 250 and led.balance("exchange") == -1000 + 500 + 250
    assert r["debt_created"] == 0
