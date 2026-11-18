"""public_view: private messages, sealed bids, and cheap-talk silence on settlement outcomes."""

import json

from railhead.exchange import Exchange


def supply(**kw):
    t = {"seller": "a", "buyer": "b", "cargo": 1, "industry": 9, "quantity": 10, "window_start": 0,
         "deadline": 100, "price": 100, "penalty_per_unit": 2}
    t.update(kw)
    return t


def session(arm):
    ex = Exchange(["a", "b", "c"], arm=arm)
    ex.offer(0, "a", "supply", supply(), to="b")
    ex.accept(1, "b", "C1")
    ex.msg(2, "a", "b", "I will deliver")
    ex.msg(3, "c", "a", "secret to a")
    ex.pay(4, "c", "a", 5)
    ex.settle_due(100, lambda *a: 0)            # broken
    return ex


def test_enforced_view_publishes_outcomes_and_ledger():
    ex = session("enforced")
    v = ex.public_view("c")
    assert v["settlements"] == [{"contract_id": "C1", "seller": "a", "buyer": "b", "outcome": "broken",
                                 "delivered": 0, "quantity": 10, "tick": 100}]
    assert v["ledger"]["balances"]["a"] == -20 + 5
    assert any(h["reason"] == "penalty" for h in v["ledger"]["history"])
    mine = ex.public_view("a")["contracts"][0]
    assert mine["status"] == "settled" and mine["outcome"] == "broken"


def test_cheap_talk_view_hides_every_trace_of_the_outcome():
    ex = session("cheap_talk")
    assert ex.contract_map["C1"].outcome == "broken"        # computed silently for research
    assert ex.records[-1]["outcome"] == "broken"            # ... and in the record log (coordinator only)
    for who in ("a", "b", "c"):
        v = ex.public_view(who)
        blob = json.dumps(v)
        assert "broken" not in blob and "outcome" not in blob and "settle" not in blob
        assert "settlements" not in v
        for c in v["contracts"]:
            assert c["status"] == "closed"
    # cheap-talk ledger: own balance and own transfers only (pay still works)
    va = ex.public_view("a")["ledger"]
    assert va["balances"] == {"a": 5} and [h["reason"] for h in va["history"]] == ["pay"]
    assert ex.public_view("b")["ledger"] == {"balances": {"b": 0}, "history": []}


def test_view_hides_other_companies_messages():
    ex = session("enforced")
    vb = ex.public_view("b")
    assert [m["text"] for m in vb["inbox"]] == ["I will deliver"] and vb["sent"] == []
    va = ex.public_view("a")
    assert [m["text"] for m in va["inbox"]] == ["secret to a"]
    assert [m["text"] for m in va["sent"]] == ["I will deliver"]
    assert "secret to a" not in json.dumps(ex.public_view("b"))
    assert "I will deliver" not in json.dumps(ex.public_view("c"))


def test_view_hides_addressed_offers_between_others():
    ex = Exchange(["a", "b", "c"])
    ex.offer(0, "a", "supply", supply(), to="b")
    assert ex.public_view("c")["contracts"] == [] and ex.public_view("c")["market"]["order_book"] == []
    assert len(ex.public_view("b")["contracts"]) == 1


def test_view_is_json_safe():
    for arm in ("enforced", "cheap_talk"):
        v = session(arm).public_view("a")
        assert json.loads(json.dumps(v)) == v
