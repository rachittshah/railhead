"""Licences: the right to build in a rectangle of the grantor's region, between two ticks; the fee is paid
in quarterly instalments in arrears (obligations: collected in enforced, voluntary in cheap talk)."""

import pytest

from railhead.exchange import Exchange, ExchangeError
from railhead.exchange.exchange import GRACE_TICKS, INSTALMENT_TICKS, instalments

CO = ["a", "b", "c"]
Q = INSTALMENT_TICKS


def lic(**kw):
    t = {"grantor": "a", "grantee": "b", "rect": [10, 10, 20, 20], "window_start": 100, "deadline": 100 + 3 * Q + 50,
         "fee": 1000}
    t.update(kw)
    return t


def code(fn, *a, **kw):
    with pytest.raises(ExchangeError) as e:
        fn(*a, **kw)
    return e.value.code


def nothing(*_a):
    return 0


def signed(arm="enforced", **kw):
    ex = Exchange(CO, arm=arm, seed=1, start_balances={"b": 5000})
    cid = ex.offer(0, "a", "licence", lic(**kw), to="b")["contract_id"]
    ex.accept(10, "b", cid)
    return ex, cid


def run_to(ex, tick):
    """settle_due at every forced tick up to ``tick``, as the coordinator does."""
    out, last = [], ex.tick
    while True:
        ticks = sorted({d["tick"] for d in ex.deadlines() if last < d["tick"] <= tick})
        if not ticks:
            return out
        last = ticks[0]
        out += ex.settle_due(last, nothing)


def test_the_instalment_schedule():
    assert instalments(1000, 100, 100 + 3 * Q + 50) == [(100 + Q, 250), (100 + 2 * Q, 250), (100 + 3 * Q, 250),
                                                        (100 + 3 * Q + 50, 250)]
    assert instalments(1001, 0, 2 * Q) == [(Q, 500), (2 * Q, 501)]  # the remainder on the last
    assert instalments(500, 0, Q - 1) == [(Q - 1, 500)]  # shorter than a quarter: one, at the deadline
    assert instalments(0, 0, 9 * Q) == []
    assert instalments(2, 0, 3 * Q) == [(3 * Q, 2)]  # equal parts of 0 are dropped: the remainder is last


def test_signing_creates_obligations_and_moves_no_money():
    ex, cid = signed()
    obs = [o for o in ex.obligations.values() if o["contract_id"] == cid]
    assert [(o["payer"], o["payee"], o["amount"], o["due"]) for o in obs] == \
        [("b", "a", 250, d) for d in (100 + Q, 100 + 2 * Q, 100 + 3 * Q, 100 + 3 * Q + 50)]
    assert ex.accounts.balances()["a"] == 0 and ex.accounts.balances()["b"] == 5000
    assert [x["contract_id"] for x in ex.licences_held("b", 100)] == [cid]
    due = [d for d in ex.deadlines() if d["kind"] == "obligation_due"]
    assert [d["companies"] for d in due] == [["b"]] * 4  # the payer is woken at each due tick


def test_enforced_collects_each_instalment_at_its_due_tick():
    ex, cid = signed()
    recs = ex.settle_due(100 + Q, nothing)
    assert [(r["op"], r["collected"], r["outcome"]) for r in recs if r["op"] == "obligation_settle"] == \
        [("obligation_settle", 250, "kept")]
    assert ex.accounts.balances()["a"] == 250
    run_to(ex, 100 + 3 * Q + 50)
    assert ex.accounts.balances()["a"] == 1000 and ex.accounts.balances()["b"] == 4000
    assert ex.contract_map[cid].outcome == "ended" and not ex.licences_held("b", 100 + 3 * Q + 50)
    assert {o["status"] for o in ex.obligations.values()} == {"kept"}
    ex.check_invariants()
    assert Exchange.replay(ex.records).state() == ex.state()


def test_enforced_collection_creates_a_debt_when_the_balance_cannot_cover_it():
    ex = Exchange(CO, arm="enforced", seed=1)
    cid = ex.offer(0, "a", "licence", lic(fee=400, deadline=100 + Q), to="b")["contract_id"]
    ex.accept(10, "b", cid)
    ex.settle_due(100 + Q, nothing)
    assert ex.accounts.balances()["b"] == -400 and ex.obligations["O1"]["status"] == "kept"


def test_cheap_talk_settles_from_tagged_payments_kept_partial_broken():
    ex, cid = signed("cheap_talk")
    d1, d2, _d3, d4 = (o["due"] for o in ex.obligations.values())
    ex.settle_due(d1, nothing)  # nothing is collected at the due tick
    assert ex.accounts.balances()["a"] == 0 and ex.obligations["O1"]["status"] == "open"
    ex.pay(d1 + 5, "b", "a", 250, contract_id=cid)  # in the grace period: on time? no (after due); kept
    ex.pay(d2 - 5, "b", "a", 100, contract_id=cid)  # before due: on time, but short
    recs = run_to(ex, d4 + GRACE_TICKS)
    out = {r["obligation_id"]: (r["outcome"], r["paid"], r["collected"]) for r in recs if r["op"] == "obligation_settle"}
    assert out == {"O1": ("kept", 250, 0), "O2": ("partial", 100, 0), "O3": ("broken", 0, 0), "O4": ("broken", 0, 0)}
    o1, o2 = ex.obligations["O1"], ex.obligations["O2"]
    assert (o1["paid_by_due"], o2["paid_by_due"]) == (0, 100)
    assert ex.accounts.balances()["a"] == 350  # only the voluntary payments moved
    ex.check_invariants()
    assert Exchange.replay(ex.records).state() == ex.state()


def test_a_payment_tag_that_names_no_open_obligation_is_refused():
    ex, cid = signed("cheap_talk")
    assert code(ex.pay, 20, "c", "a", 10, contract_id=cid) == "bad_tag"  # c owes nothing under it
    assert code(ex.pay, 20, "a", "b", 10, contract_id=cid) == "bad_tag"  # wrong direction
    assert code(ex.pay, 20, "b", "a", 10, contract_id="C99") == "bad_tag"
    forged = {"op": "pay", "seq": len(ex.records), "tick": 20, "company": "c", "to": "a", "amount": 10, "memo": "",
              "contract_id": cid}
    assert code(ex.apply, forged) == "bad_tag"
    ex.pay(20, "b", "a", 10, contract_id=cid)
    old = {"op": "pay", "seq": len(ex.records), "tick": 21, "company": "b", "to": "a", "amount": 1, "memo": "x"}
    ex.apply(old)  # a record without the field (written before it existed) is still valid


def test_a_forged_obligation_settlement_is_rejected():
    ex, _cid = signed("cheap_talk")
    t = ex.obligations["O1"]["settle_tick"]
    base = {"op": "obligation_settle", "seq": len(ex.records), "company": None, "obligation_id": "O1"}
    assert code(ex.apply, {**base, "tick": t - 1, "collected": 0, "paid": 0, "outcome": "broken"}) == "bad_state"
    assert code(ex.apply, {**base, "tick": t, "collected": 0, "paid": 0, "outcome": "kept"}) == "record_mismatch"


def test_an_amendment_replaces_the_instalments_not_yet_due():
    ex, cid = signed()
    ex.settle_due(100 + Q, nothing)  # first 250 collected
    new = lic(fee=1600, deadline=100 + 2 * Q + 10)
    ex.amend(100 + Q + 1, "a", cid, new)
    assert ex.obligations["O2"]["status"] == "open"  # proposed only
    ex.amend(100 + Q + 2, "b", cid, new)
    st = {o["id"]: (o["status"], o["amount"], o["due"]) for o in ex.obligations.values()}
    assert [st[k][0] for k in ("O1", "O2", "O3", "O4")] == ["kept", "replaced", "replaced", "replaced"]
    assert [v for k, v in st.items() if k not in ("O1", "O2", "O3", "O4")] == \
        [("open", 675, 100 + 2 * Q), ("open", 675, 100 + 2 * Q + 10)]  # 1600 - 250 over the new dues
    run_to(ex, 100 + 2 * Q + 10)
    assert ex.accounts.balances()["a"] == 1600
    assert all(o["contract_id"] == cid for o in ex.obligations_of("b"))
    assert "replaced" not in {o["status"] for o in ex.obligations_of("b")}
    ex.check_invariants()


def test_a_free_licence_has_no_obligations():
    ex, _cid = signed(fee=0)
    assert ex.obligations == {} and ex.licences_held("b", 100)
