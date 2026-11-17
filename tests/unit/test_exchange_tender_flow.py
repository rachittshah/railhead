"""Tenders through the facade: open, sealed bids, award, holder settlement, subcontract deliveries."""

import pytest

from railhead.exchange import Exchange, ExchangeError, TenderConfig
from railhead.exchange.ledger import escrow_account

COAL = 1
WORLD = [
    {"id": 1, "type": "mine", "produces": [COAL], "accepts": [], "tile": [0, 0], "region": 0},
    {"id": 9, "type": "plant", "produces": [], "accepts": [COAL], "tile": [20, 0], "region": 1},
]
CFG = TenderConfig(per_quarter=1, quantity_base=100, quantity_jitter_pct=0, payout_base_per_unit=10,
                   payout_per_unit_per_tile=0, penalty_bp=5000, bid_ticks=10, window_ticks=1000)


def code(fn, *a, **kw):
    with pytest.raises(ExchangeError) as e:
        fn(*a, **kw)
    return e.value.code


def opened(arm="enforced"):
    ex = Exchange(["a", "b", "c"], arm=arm, seed=3, tender_config=CFG)
    r = ex.open_tenders(0, 0, WORLD)
    (t,) = r["tenders"]
    assert (t["tender_id"], t["industry"], t["cargo"], t["quantity"], t["max_price"]) == ("T1", 9, COAL, 100, 1000)
    assert t["penalty_per_unit"] == 5 and (t["bid_close"], t["deadline"]) == (10, 1010)
    return ex


def test_sealed_bids_hidden_from_others_until_award():
    ex = opened()
    ex.tender_bid(1, "a", "T1", 800)
    ex.tender_bid(2, "b", "T1", 700)
    assert ex.market("a")["tenders"][0]["your_bid"] == 800
    assert ex.market("c")["tenders"][0]["your_bid"] is None
    assert "bids" not in ex.market("c")["tenders"][0]
    assert ex.public_view("c")["tender_awards"] == []
    recs = ex.advance(10)
    assert [(r["op"], r["winner"], r["price"]) for r in recs if r["op"] == "tender_award"] == \
        [("tender_award", "b", 700)]
    assert ex.public_view("c")["tender_awards"] == [{"tender_id": "T1", "winner": "b", "price": 700}]
    c = ex.contract_map["T1"]
    assert (c.status, c.terms["seller"], c.terms["buyer"], c.terms["price"]) == ("active", "b", "exchange", 700)
    assert ex.accounts.balance(escrow_account("T1")) == 700 and ex.accounts.balance("exchange") == -700
    assert ex.contracts("b")[0]["tender_id"] == "T1"


def test_tie_goes_to_earliest_bid():
    ex = opened()
    ex.tender_bid(1, "c", "T1", 500)
    ex.tender_bid(1, "a", "T1", 500)
    ex.advance(10)
    assert ex.tenders["T1"]["winner"] == "c"


def test_bid_rules():
    ex = opened()
    assert code(ex.tender_bid, 1, "a", "T1", 1001) == "bad_price"
    assert code(ex.tender_bid, 1, "a", "T1", -1) == "bad_price"
    assert code(ex.tender_bid, 1, "a", "T9", 5) == "unknown_tender"
    ex.tender_bid(1, "a", "T1", 900)
    assert code(ex.tender_bid, 2, "a", "T1", 800) == "already_bid"
    assert code(ex.tender_bid, 10, "b", "T1", 800) == "tender_closed"


def test_no_bids_means_unawarded():
    ex = opened()
    ex.advance(10)
    assert ex.tenders["T1"]["status"] == "unawarded" and "T1" not in ex.contract_map


def test_tender_award_record_must_match_auction():
    ex = opened()
    ex.tender_bid(1, "a", "T1", 900)
    bad = {"op": "tender_award", "seq": len(ex.records), "tick": 10, "company": None, "tender_id": "T1",
           "winner": "b", "price": 1}
    assert code(ex.apply, bad) == "record_mismatch"
    assert code(ex.apply, {**bad, "tick": 9, "winner": "a", "price": 900}) == "bad_state"


@pytest.mark.parametrize("own, sub, outcome, holder_bal", [
    (100, 0, "kept", 700),
    (40, 60, "kept", 700 - 300),        # subcontractor c delivers 60 for holder b, paid 300 by b
    (20, 30, "partial", (700 * 50 // 100 - 5 * 50) - 300 * 30 // 60),   # sub refunds b the unearned half
    (0, 0, "broken", -5 * 100),                                         # sub escrow fully refunded to b
])
def test_holder_settlement_counts_subcontract_deliveries(own, sub, outcome, holder_bal):
    ex = opened()
    ex.tender_bid(1, "b", "T1", 700)
    ex.advance(10)
    # b subcontracts 60 units of coal to industry 9 from c
    cid = ex.offer(20, "b", "supply", {"seller": "c", "buyer": "b", "cargo": COAL, "industry": 9, "quantity": 60,
                                       "window_start": 20, "deadline": 1000, "price": 300, "penalty_per_unit": 0},
                   to="c")["contract_id"]
    ex.accept(30, "c", cid)
    counts = {"b": own, "c": sub}
    calls = []

    def fn(company, cargo, industry, start, end):
        calls.append((company, start, end))
        return counts[company]

    ex.settle_due(1000, fn)                    # subcontract settles first (deadline 1000)
    recs = ex.settle_due(1010, fn)
    s = next(r for r in recs if r["op"] == "settle")
    assert s["contract_id"] == "T1" and s["outcome"] == outcome and s["delivered"] == own + sub
    assert s["parts"] == [["b", 10, 1010, own], ["c", 30, 1010, sub]]
    assert ex.accounts.balance("b") == holder_bal
    assert ex.tenders["T1"]["status"] == "settled"
    ex.check_invariants()


def test_unrelated_contracts_do_not_count_toward_tender():
    ex = opened()
    ex.tender_bid(1, "b", "T1", 700)
    ex.advance(10)
    other = {"seller": "c", "buyer": "a", "cargo": COAL, "industry": 9, "quantity": 10, "window_start": 20,
             "deadline": 1000, "price": 0, "penalty_per_unit": 0}
    ex.accept(21, "a", ex.offer(20, "c", "supply", other, to="a")["contract_id"])
    assert ex.delivery_parts(ex.contract_map["T1"]) == [["b", 10, 1010]]


def test_tender_contracts_cannot_be_amended():
    ex = opened()
    ex.tender_bid(1, "b", "T1", 700)
    ex.advance(10)
    t = dict(ex.contract_map["T1"].terms)
    assert code(ex.amend, 11, "b", "T1", t) == "not_amendable"


def test_cheap_talk_tenders_move_no_money():
    ex = opened("cheap_talk")
    ex.tender_bid(1, "b", "T1", 700)
    ex.advance(10)
    ex.settle_due(1010, lambda *a: 0)
    assert ex.contract_map["T1"].outcome == "broken"
    assert ex.accounts.history == [] and ex.accounts.balance("b") == 0
    ex.check_invariants()


def test_bad_tender_open_records():
    ex = opened()
    base = {"op": "tender_open", "seq": len(ex.records), "tick": 0, "company": None, "quarter": 1}
    assert code(ex.apply, {**base, "tenders": [{"tender_id": "T1"}]}) == "bad_record"
    t = dict(ex.tenders["T1"])
    for k in ("status", "bids", "winner", "price"):
        t.pop(k)
    assert code(ex.apply, {**base, "tenders": [t]}) == "bad_record"           # T1 already used
    assert code(ex.apply, {**base, "tenders": [{**t, "tender_id": "T2", "quantity": 0}]}) == "bad_record"
    ex.apply({**base, "tenders": [{**t, "tender_id": "T2"}]})
    assert ex.next_tender == 3
