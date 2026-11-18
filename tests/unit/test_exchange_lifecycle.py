"""Exchange facade: offer / counter / accept / withdraw / expire / amend / settle, with escrow (enforced)."""

import pytest

from railhead.exchange import Exchange, ExchangeError
from railhead.exchange.ledger import bond_account, escrow_account

CO = ["a", "b", "c"]


def supply(**kw):
    t = {"seller": "a", "buyer": "b", "cargo": 1, "industry": 9, "quantity": 100, "window_start": 100,
         "deadline": 1000, "price": 1000, "penalty_per_unit": 5}
    t.update(kw)
    return t


def code(fn, *a, **kw):
    with pytest.raises(ExchangeError) as e:
        fn(*a, **kw)
    return e.value.code


def delivered(n):
    return lambda company, cargo, industry, start, end: n


@pytest.fixture
def ex():
    return Exchange(CO, arm="enforced", seed=1)


def signed(ex, t=0, **kw):
    cid = ex.offer(t, "a", "supply", supply(**kw), to="b", expires_tick=500)["contract_id"]
    ex.accept(t + 1, "b", cid)
    return cid


# ------------------------------------------------------------------------------------------- offers
def test_addressed_offer_then_accept_escrows_price_and_bond(ex):
    r = ex.offer(0, "a", "supply", supply(bond=200), to="b", expires_tick=50)
    assert r["contract_id"] == "C1" and r["terms"]["bond"] == 200 and r["seq"] == 1
    assert ex.contracts("b")[0]["your_turn"] is True and ex.contracts("a")[0]["your_turn"] is False
    assert ex.contracts("c") == []
    ex.accept(10, "b", "C1")
    c = ex.contract_map["C1"]
    assert c.status == "signed" and c.signed_tick == 10
    assert ex.accounts.balance(escrow_account("C1")) == 1000 and ex.accounts.balance(bond_account("C1")) == 200
    assert ex.accounts.balance("b") == -1000 and ex.accounts.balance("a") == -200
    ex.check_invariants()


def test_seller_or_buyer_may_offer_and_offerer_must_be_a_party(ex):
    assert ex.offer(0, "b", "supply", supply(), to="a")["contract_id"] == "C1"
    assert code(ex.offer, 0, "c", "supply", supply(), to="a") == "not_party"
    assert code(ex.offer, 0, "a", "supply", supply(), to="c") == "bad_terms"     # to != other party
    assert code(ex.offer, 0, "a", "supply", supply(), to="a") == "bad_terms"
    assert code(ex.offer, 0, "a", "supply", supply(), to="zz") == "unknown_company"
    assert code(ex.offer, 0, "zz", "supply", supply(), to="b") == "unknown_company"


def test_offer_validation_codes(ex):
    assert code(ex.offer, 0, "a", "supply", supply(quantity=0), to="b") == "bad_terms"
    assert code(ex.offer, 0, "a", "swap", {}, to="b") == "unknown_kind"
    lic = {"grantor": "a", "grantee": "b", "tiles": [1], "term": 10, "annual_fee": 5}
    assert code(ex.offer, 0, "a", "licence", lic, to="b") == "kind_not_enabled"
    assert code(ex.offer, 0, "a", "supply", supply(), to="b", expires_tick=0) == "bad_terms"
    assert code(ex.offer, 2000, "a", "supply", supply(), to="b") == "deadline_passed"
    assert len(ex.records) == 1                                                  # only init


def test_public_offer_sits_in_the_order_book_until_someone_accepts(ex):
    ex.offer(0, "a", "supply", supply(buyer=None), expires_tick=100)
    assert [o["contract_id"] for o in ex.market("c")["order_book"]] == ["C1"]
    assert ex.market("c")["order_book"][0]["your_turn"] is True
    assert code(ex.accept, 1, "a", "C1") == "not_your_turn"
    ex.accept(2, "c", "C1")
    c = ex.contract_map["C1"]
    assert c.terms["buyer"] == "c" and c.status == "signed"
    assert ex.market("b")["order_book"] == []
    assert ex.accounts.balance("c") == -1000


def test_public_offer_must_leave_other_party_open(ex):
    assert code(ex.offer, 0, "a", "supply", supply()) == "bad_terms"


def test_counter_on_public_offer_binds_the_counterparty(ex):
    ex.offer(0, "a", "supply", supply(buyer=None), expires_tick=100)
    assert code(ex.counter, 1, "c", "C1", supply(buyer="b")) == "bad_terms"   # must name itself
    ex.counter(1, "c", "C1", supply(buyer="c", price=900), expires_tick=200)
    c = ex.contract_map["C1"]
    assert (c.status, c.proposer, c.responder, c.terms["price"]) == ("countered", "c", "a", 900)
    assert ex.market("b")["order_book"] == []
    assert code(ex.accept, 2, "b", "C1") == "not_party"
    assert code(ex.accept, 2, "c", "C1") == "not_your_turn"
    ex.accept(3, "a", "C1")
    assert ex.accounts.balance("c") == -900


def test_counter_back_and_forth_then_accept(ex):
    ex.offer(0, "a", "supply", supply(price=2000), to="b", expires_tick=50)
    ex.counter(5, "b", "C1", supply(price=1000), expires_tick=60)
    ex.counter(6, "a", "C1", supply(price=1500), expires_tick=70)
    assert code(ex.counter, 7, "a", "C1", supply(price=1400)) == "not_your_turn"
    assert code(ex.counter, 7, "b", "C1", supply(seller="c")) == "bad_terms"     # parties fixed
    ex.accept(8, "b", "C1")
    assert ex.contract_map["C1"].terms["price"] == 1500
    assert [e["event"] for e in ex.contract_map["C1"].events] == ["offer", "counter", "counter", "accept"]


def test_expired_offer_cannot_be_accepted_and_advance_expires_it(ex):
    ex.offer(0, "a", "supply", supply(), to="b", expires_tick=50)
    assert code(ex.accept, 50, "b", "C1") == "offer_expired"
    assert ex.deadlines()[0] == {"tick": 50, "kind": "offer_expiry", "id": "C1", "companies": ["a", "b"]}
    recs = ex.advance(50)
    assert [r["op"] for r in recs] == ["expire"] and ex.contract_map["C1"].status == "expired"
    assert code(ex.accept, 51, "b", "C1") == "bad_state"
    assert ex.advance(60) == []


def test_withdraw_rules(ex):
    ex.offer(0, "a", "supply", supply(), to="b")
    assert code(ex.withdraw, 1, "c", "C1") == "not_party"
    ex.withdraw(1, "b", "C1")                        # either negotiator may walk away
    assert ex.contract_map["C1"].status == "withdrawn"
    assert code(ex.withdraw, 2, "a", "C1") == "bad_state"
    cid = signed(ex, t=2)
    assert code(ex.withdraw, 4, "a", cid) == "bad_state"
    assert code(ex.withdraw, 4, "a", "C99") == "unknown_contract"


def test_credit_limit_blocks_escrow():
    ex = Exchange(CO, credit_limit=500)
    ex.offer(0, "a", "supply", supply(), to="b")
    assert code(ex.accept, 1, "b", "C1") == "insufficient_funds"
    assert ex.contract_map["C1"].status == "offered" and ex.accounts.history == []
    ex.pay(2, "c", "b", 500)
    ex.accept(3, "b", "C1")
    assert ex.accounts.balance("b") == -500


def test_activation_at_window_start(ex):
    cid = signed(ex)
    assert ex.contract_map[cid].status == "signed"
    assert [r["op"] for r in ex.advance(100)] == ["activate"]
    assert ex.contract_map[cid].status == "active"
    cid2 = ex.offer(150, "a", "supply", supply(), to="b")["contract_id"]
    ex.accept(150, "b", cid2)                           # window already open: active at signing
    assert ex.contract_map[cid2].status == "active"


# ------------------------------------------------------------------------------------------- settlement
@pytest.mark.parametrize("d, outcome, a_bal, b_bal", [
    (100, "kept", 1000, -1000),
    (130, "kept", 1000, -1000),
    (40, "partial", 400 - 300, -400 + 300),
    (0, "broken", -500, 500),
])
def test_settle_due_known_answers(ex, d, outcome, a_bal, b_bal):
    cid = signed(ex)
    assert ex.settle_due(999, delivered(d)) != [] and ex.contract_map[cid].status == "active"
    recs = ex.settle_due(1000, delivered(d))
    s = recs[-1]
    assert (s["op"], s["contract_id"], s["delivered"], s["outcome"]) == ("settle", cid, d, outcome)
    assert s["parts"] == [["a", 100, 1000, d]]
    assert ex.contract_map[cid].outcome == outcome
    assert (ex.accounts.balance("a"), ex.accounts.balance("b")) == (a_bal, b_bal)
    assert ex.accounts.balance(escrow_account(cid)) == 0
    ex.check_invariants()
    assert ex.settle_due(2000, delivered(d)) == []


def test_settle_due_passes_window_to_deliveries_fn(ex):
    signed(ex)
    calls = []
    ex.settle_due(1000, lambda *a: calls.append(a) or 7)
    assert calls == [("a", 1, 9, 100, 1000)]


def test_bad_delivery_counts_are_rejected(ex):
    signed(ex)
    assert code(ex.settle_due, 1000, delivered(-1)) == "bad_delivered"
    assert code(ex.settle_due, 1000, lambda *a: 1.5) == "bad_delivered"


def test_settle_record_must_match_and_be_due(ex):
    cid = signed(ex)
    base = {"seq": len(ex.records), "company": None, "contract_id": cid, "parts": []}
    assert code(ex.apply, {"op": "settle", "tick": 999, "delivered": 5, "outcome": "partial", **base}) == "not_due"
    assert code(ex.apply, {"op": "settle", "tick": 1000, "delivered": 5, "outcome": "kept", **base}) == \
        "record_mismatch"


# ------------------------------------------------------------------------------------------- amendment
def test_amendment_needs_both_signatures_and_adjusts_escrow(ex):
    cid = signed(ex, bond=100)
    new = supply(price=1500, bond=50, deadline=2000, quantity=120)
    ex.amend(200, "a", cid, new)
    c = ex.contract_map[cid]
    assert c.pending_amendment["by"] == "a" and c.terms["price"] == 1000
    assert code(ex.amend, 201, "c", cid, new) == "not_party"
    ex.amend(210, "b", cid, new)                        # identical terms from the other party: signed
    assert c.terms["price"] == 1500 and c.pending_amendment is None
    assert [h["from_tick"] for h in c.terms_history] == [1, 210]
    assert ex.accounts.balance(escrow_account(cid)) == 1500 and ex.accounts.balance(bond_account(cid)) == 50
    assert ex.accounts.balance("b") == -1500 and ex.accounts.balance("a") == -50
    assert ex.settle_due(1000, delivered(0)) == []         # old deadline: nothing settles
    assert {d["tick"] for d in ex.deadlines() if d["id"] == cid} == {2000}
    recs = ex.settle_due(2000, delivered(60))
    assert recs[-1]["outcome"] == "partial"
    assert ex.accounts.balance("a") == 1500 * 60 // 120 - 5 * 60
    ex.check_invariants()


def test_amendment_counter_proposal_replaces_pending(ex):
    cid = signed(ex)
    ex.amend(200, "a", cid, supply(price=1200))
    ex.amend(201, "b", cid, supply(price=1100))         # different terms: b's proposal replaces a's
    c = ex.contract_map[cid]
    assert c.pending_amendment["by"] == "b" and c.terms["price"] == 1000
    ex.amend(202, "b", cid, supply(price=1050))         # proposer may revise
    assert c.pending_amendment["terms"]["price"] == 1050
    ex.amend(203, "a", cid, supply(price=1050))
    assert c.terms["price"] == 1050 and ex.accounts.balance(escrow_account(cid)) == 1050


def test_amendment_rules(ex):
    cid = signed(ex)
    assert code(ex.amend, 200, "a", cid, supply(buyer="c")) == "bad_terms"
    assert code(ex.amend, 200, "a", cid, supply(deadline=150, window_start=100)) == "deadline_passed"
    ex.offer(200, "a", "supply", supply(), to="b")
    assert code(ex.amend, 201, "a", "C2", supply()) == "bad_state"
    assert code(ex.amend, 1000, "a", cid, supply()) == "deadline_passed"


def test_amendment_cannot_sign_after_deadline(ex):
    cid = signed(ex)
    ex.amend(500, "a", cid, supply(deadline=3000))
    assert code(ex.amend, 1000, "b", cid, supply(deadline=3000)) == "deadline_passed"


def test_amendment_with_credit_limit_checks_topup():
    ex = Exchange(CO, credit_limit=1000)
    cid = signed(ex)
    ex.amend(5, "a", cid, supply(price=1001))
    assert code(ex.amend, 6, "b", cid, supply(price=1001)) == "insufficient_funds"
    assert ex.contract_map[cid].terms["price"] == 1000


# ------------------------------------------------------------------------------------------- messages, pay
def test_messages_are_private(ex):
    ex.msg(1, "a", "b", "hello b")
    ex.msg(2, "c", "a", "hi a")
    assert [m["text"] for m in ex.inbox("b")] == ["hello b"]
    assert ex.inbox("c") == []
    assert code(ex.msg, 3, "a", "a", "me") == "bad_message"
    assert code(ex.msg, 3, "a", "b", "") == "bad_message"
    assert code(ex.msg, 3, "a", "zz", "x") == "unknown_company"


def test_pay(ex):
    r = ex.pay(5, "a", "b", 300, memo="thanks")
    assert r["op"] == "pay" and ex.accounts.balance("b") == 300
    assert ex.accounts.history[-1].ref == "thanks"
    assert code(ex.pay, 6, "a", "b", 0) == "bad_amount"
    assert code(ex.pay, 6, "a", "exchange", 5) == "unknown_company"
    assert code(ex.pay, 6, "a", "b", 5, memo="x" * 201) == "bad_memo"


def test_time_never_goes_backwards(ex):
    ex.pay(10, "a", "b", 1)
    assert code(ex.pay, 9, "a", "b", 1) == "time_reversed"


def test_deadlines_list_every_forced_wakeup(ex):
    ex.offer(0, "a", "supply", supply(), to="b", expires_tick=40)
    signed(ex)
    ex.open_tenders(5, 0, [
        {"id": 1, "type": "mine", "produces": [1], "accepts": [], "tile": [0, 0], "region": 0},
        {"id": 9, "type": "plant", "produces": [], "accepts": [1], "tile": [9, 0], "region": 1},
    ])
    kinds = [(d["tick"], d["kind"], d["id"]) for d in ex.deadlines()]
    assert kinds == [(40, "offer_expiry", "C1"), (100, "window_start", "C2"), (5 + 518, "bid_close", "T1"),
                     (1000, "deadline", "C2")]
