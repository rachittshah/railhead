"""Licences: the right to build in a rectangle of the grantor's region, for a fee, between two ticks."""

import pytest

from railhead.exchange import Exchange, ExchangeError

CO = ["a", "b", "c"]


def lic(**kw):
    t = {"grantor": "a", "grantee": "b", "rect": [10, 10, 20, 20], "window_start": 100, "deadline": 1000,
         "fee": 300}
    t.update(kw)
    return t


def code(fn, *a, **kw):
    with pytest.raises(ExchangeError) as e:
        fn(*a, **kw)
    return e.value.code


def nothing(*_a):
    return 0


def signed(arm="enforced", **kw):
    ex = Exchange(CO, arm=arm, seed=1, start_balances={"b": 1000})
    cid = ex.offer(0, "a", "licence", lic(**kw), to="b")["contract_id"]
    ex.accept(10, "b", cid)
    return ex, cid


def test_enforced_licence_lifecycle_fee_window_and_end():
    ex, cid = signed()
    bal = ex.accounts.balances()
    assert (bal["a"], bal["b"]) == (300, 700)  # the fee goes to the grantor at signing, no escrow
    assert ex.licences_held("b", 50) == []  # signed, but before window_start
    assert [x["contract_id"] for x in ex.licences_held("b", 100)] == [cid]
    assert ex.licences_held("a", 100) == [] and ex.licences_held("c", 100) == []
    ex.settle_due(100, nothing)
    assert ex.contract_map[cid].status == "active"
    recs = ex.settle_due(1000, nothing)
    assert [(r["op"], r["outcome"], r["delivered"], r["parts"]) for r in recs] == [("settle", "ended", 0, [])]
    c = ex.contract_map[cid]
    assert c.status == "settled" and c.outcome == "ended"
    assert ex.licences_held("b", 999) == [] and ex.accounts.balances()["a"] == 300  # the end moves no money
    ex.check_invariants()
    assert Exchange.replay(ex.records).state() == ex.state()


def test_the_licence_ends_exactly_at_its_deadline():
    ex, cid = signed()
    assert ex.licences_held("b", 999) and not ex.licences_held("b", 1000)
    assert any(d["kind"] == "deadline" and d["id"] == cid and d["companies"] == ["a", "b"] for d in ex.deadlines())


def test_cheap_talk_grants_the_right_but_moves_no_money():
    ex, cid = signed("cheap_talk")
    assert ex.accounts.balances()["a"] == 0 and ex.accounts.balances()["b"] == 1000
    assert [x["contract_id"] for x in ex.licences_held("b", 500)] == [cid]
    ex.check_invariants()


def test_enforced_fee_needs_funds_and_counter_and_amend_move_the_difference():
    ex = Exchange(CO, arm="enforced", seed=1, credit_limit=0, start_balances={"b": 1000})
    cid = ex.offer(0, "a", "licence", lic(fee=5000), to="b")["contract_id"]
    assert code(ex.accept, 10, "b", cid) == "insufficient_funds"
    ex.counter(10, "b", cid, lic(fee=400))
    ex.accept(20, "a", cid)  # the grantor signs the grantee's counter: the grantee pays at signing
    assert (ex.accounts.balances()["a"], ex.accounts.balances()["b"]) == (400, 600)


def test_amendment_moves_the_fee_difference_and_can_shorten_the_term():
    ex, cid = signed()
    ex.amend(200, "a", cid, lic(fee=500, deadline=600))
    assert ex.accounts.balances()["a"] == 300  # proposed only
    ex.amend(210, "b", cid, lic(fee=500, deadline=600))
    assert ex.accounts.balances()["a"] == 500 and ex.accounts.balances()["b"] == 500
    assert ex.licences_held("b", 599) and not ex.licences_held("b", 600)
    ex.amend(300, "a", cid, lic(fee=100, deadline=600))
    ex.amend(300, "b", cid, lic(fee=100, deadline=600))
    assert ex.accounts.balances()["a"] == 100  # refund to the grantee
    with pytest.raises(ExchangeError, match="parties"):
        ex.amend(310, "a", cid, lic(grantee="c", deadline=600))
    ex.check_invariants()


def test_a_forged_licence_end_is_rejected():
    ex, cid = signed()
    bad = {"op": "settle", "seq": len(ex.records), "tick": 1000, "company": None, "contract_id": cid,
           "delivered": 5, "parts": [], "outcome": "ended"}
    assert code(ex.apply, bad) == "record_mismatch"
    early = dict(bad, delivered=0, tick=900)
    assert code(ex.apply, early) == "not_due"


def test_a_licence_is_not_a_tender_subcontract():
    ex, cid = signed()
    assert ex.contract_map[cid].kind == "licence"
    assert ex.public_view("b")["settlements"] == []
