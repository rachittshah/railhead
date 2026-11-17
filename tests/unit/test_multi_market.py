"""The market driver without an engine: monitor book, tick records, forced stops, wake reasons, and the
v3 rules for `ex` records and market wake reasons."""

from __future__ import annotations

import pytest

from railhead.multi.barrier import Barrier, Observation, Waiter
from railhead.multi.log import LogRejected, LogState, wait_args
from railhead.multi.market import (
    Market,
    MarketConfig,
    MonitorBook,
    exchange_wakes,
    pending_now,
    pick_reason,
)
from railhead.multi.regions import RegionMap

D = 74
START = 1000
COMPANIES = [{"id": 3, "name": "Company A", "slot": 0}, {"id": 5, "name": "Company B", "slot": 1},
             {"id": 7, "name": "Company C", "slot": 2}]
TENDER = {"per_quarter": 1, "bid_ticks": 2 * D, "window_ticks": 20 * D, "quantity_base": 10,
          "quantity_jitter_pct": 0}
INDUSTRIES = [  # a mine in region 0 and a power station that accepts its coal in region 1
    {"id": 1, "type": 0, "x": 5, "y": 5, "produces": [{"cargo": 1}], "accepts": []},
    {"id": 2, "type": 1, "x": 40, "y": 5, "produces": [], "accepts": [1]},
]


def market(arm="enforced", horizon_days=365, start_balances=None) -> Market:
    cfg = MarketConfig(arm=arm, seed=9, start_balances=start_balances or {}, quarter_days=30, tender=TENDER)
    rm = RegionMap(64, 64, {0: [[0, 0, 20, 63]], 1: [[21, 0, 63, 63]]})
    return Market(cfg, COMPANIES, START, START + horizon_days * D, rm)


def supply(seller, buyer, deadline, q=100, price=1000, ppu=5, ws=START):
    return {"seller": seller, "buyer": buyer, "cargo": 1, "industry": 2, "quantity": q, "window_start": ws,
            "deadline": deadline, "price": price, "penalty_per_unit": ppu}


def test_config_from_objective_and_start_balances():
    assert MarketConfig.from_objective({}) is None
    cfg = MarketConfig.from_objective({"market": {"arm": "cheap_talk", "start_balances": {"1": 500}}})
    assert cfg.arm == "cheap_talk" and cfg.start_balances == {1: 500}
    m = market(start_balances={1: 700})
    assert m.balance(5) == 700 and m.balance(3) == 0
    assert m.ex.records[0]["tick"] == START
    with pytest.raises(Exception):
        MarketConfig.from_objective({"market": {"tender": {"nope": 1}}})


def test_monitor_book_counts_from_the_later_of_start_and_creation():
    b = MonitorBook()
    m = market()
    m.ex.offer(START, "3", "supply", supply("3", "5", START + 20 * D), to="5")
    m.ex.accept(START + 2 * D, "5", "C1")
    added = []
    b.ensure(m.ex, START + 2 * D, lambda *k: added.append(k) or 40)
    b.ensure(m.ex, START + 2 * D, lambda *k: added.append(k) or 41)  # idempotent
    assert added == [(3, 1, 2)]
    b.sample(START + 9 * D, [{"id": 40, "total": 30}, {"id": 99, "total": 5}])
    b.sample(START + 20 * D, [{"id": 40, "total": 120}])
    assert b.deliveries("3", 1, 2, START, START + 20 * D) == 120
    assert b.deliveries("3", 1, 2, START + 9 * D, START + 20 * D) == 90
    assert b.deliveries("5", 1, 2, START, START + 20 * D) == 0


def test_tick_records_settle_and_open_tenders_with_real_deliveries():
    m = market()
    recs = m.tick_records(START, lambda: INDUSTRIES)
    assert [r["op"] for r in recs] == ["tender_open"] and len(recs[0]["tenders"]) == 1
    t = recs[0]["tenders"][0]
    assert (t["industry"], t["cargo"], t["region"]) == (2, 1, 1)
    m.ex.offer(START, "3", "supply", supply("3", "5", START + 10 * D), to="5")
    m.ex.accept(START, "5", "C1")
    m.book.ensure(m.ex, START, lambda *k: 0)
    m.book.sample(START + 10 * D, [{"id": 0, "total": 60}])
    out = m.tick_records(START + 10 * D, lambda: INDUSTRIES)
    settle = [r for r in out if r["op"] == "settle"]
    assert settle and settle[0]["delivered"] == 60 and settle[0]["outcome"] == "partial"
    assert m.balance(3) == 1000 * 60 // 100 - 5 * 40
    m.ex.check_invariants()


def test_next_forced_stops_at_deadlines_and_quarters_inside_the_horizon():
    m = market(horizon_days=60)
    assert m.next_forced(START) == START + 30 * D  # the quarter at day 30 (2 + 20 days fit in 60)
    m.ex.offer(START, "3", "supply", supply("3", "5", START + 12 * D), to="5", expires_tick=START + 4 * D)
    assert m.next_forced(START) == START + 4 * D
    m.ex.accept(START + D, "5", "C1")
    assert m.next_forced(START + D) == START + 12 * D
    assert m.next_forced(START + 12 * D) == START + 30 * D
    assert m.next_forced(START + 30 * D) is None  # day 60 is past the horizon: no tenders open then
    assert not m.tenders_fit(START + 40 * D)


def test_exchange_wakes_forced_and_conditions():
    m = market()
    m.ex.offer(START, "3", "supply", supply("3", "5", START + 12 * D), to="5")
    waiters = {5: (["offer_received"], 1), 7: ([], 1), 3: (["message_received"], 1)}
    w = exchange_wakes(m.ex, waiters, START + D, at_quarter=False)
    assert w == {5: {"offer_received"}}
    assert pending_now(m.ex, waiters)
    m.ex.msg(START, "5", "3", "hello")
    assert exchange_wakes(m.ex, waiters, START + D, False)[3] == {"message_received"}
    m.ex.accept(START, "5", "C1")
    since = len(m.ex.records)
    w = exchange_wakes(m.ex, {3: ([], since), 5: ([], since), 7: ([], since)}, START + 12 * D, at_quarter=True)
    assert w == {3: {"deadline", "tender"}, 5: {"deadline", "tender"}, 7: {"tender"}}
    assert pick_reason(w[3]) == "deadline"
    assert not pending_now(m.ex, {5: (["offer_received"], since)})
    # a counter from the other party wakes the offerer; its own counter does not
    m.ex.offer(START, "3", "supply", supply("3", "5", START + 12 * D), to="5")
    s2 = len(m.ex.records)
    m.ex.counter(START, "5", "C2", supply("3", "5", START + 12 * D, price=1200))
    assert exchange_wakes(m.ex, {3: (["offer_received"], s2), 5: (["offer_received"], s2)}, START, False) == \
        {3: {"offer_received"}}


def test_barrier_uses_the_market_cap_and_reason():
    b = Barrier([3, 5], horizon_tick=START + 100 * D)
    b.add(Waiter(3, target=START + 30 * D))
    b.add(Waiter(5, target=START + 40 * D, until=["deadline"]))
    assert b.next_ticks(START, cap=START + 5 * D) == 5 * D
    assert b.next_ticks(START, cap=None) == 7 * D  # conditions: chunks of 7 days
    woke = b.wake(START + 5 * D, Observation(market={5: "deadline"}))
    assert woke == [(5, "deadline")]


def test_v3_rules_for_ex_records_and_market_reasons():
    hdr = {"kind": "header", "v": 3, "companies": COMPANIES, "ticks": START, "checksum": {"h": 1}}
    st = LogState(hdr, horizon_tick=START + 100 * D)
    st.check({"kind": "ex", "seq": 1, "ticks": START, "company": None, "x": {"op": "init", "company": None}})
    st.check({"kind": "ex", "seq": 2, "ticks": START, "company": 3, "x": {"op": "msg", "company": "3"}})
    with pytest.raises(LogRejected):
        st.check({"kind": "ex", "seq": 3, "ticks": START, "company": 3, "x": {"op": "msg", "company": "5"}})
    with pytest.raises(LogRejected):
        st.check({"kind": "ex", "seq": 3, "ticks": START, "company": None, "x": {"op": "settle", "company": "3"}})
    assert wait_args({"days": 3, "until": ["offer_received", "new_month"]})[1] == ["offer_received", "new_month"]
    with pytest.raises(LogRejected):
        wait_args({"days": 3, "until": ["offers"]})
    for c in (3, 5, 7):
        st.check({"kind": "wait_req", "seq": st.expect_seq, "ticks": START, "company": c, "a": {"days": 30},
                  "target": START + 30 * D})
    # a forced market reason is structurally valid; a GS reason the waiter did not ask for is not
    with pytest.raises(LogRejected):
        LogState.check(_copy(st), {"kind": "step", "seq": st.expect_seq, "ticks": START, "end_ticks": START + D,
                                   "checksum": {"h": 2}, "wakers": [{"company": 3, "reason": "new_month"}]})
    st.check({"kind": "step", "seq": st.expect_seq, "ticks": START, "end_ticks": START + D, "checksum": {"h": 2},
              "wakers": [{"company": 3, "reason": "deadline"}]})
    st.check({"kind": "ex", "seq": st.expect_seq, "ticks": START + D, "company": None,
              "x": {"op": "settle", "company": None}})  # system records while companies wait


def _copy(st: LogState) -> LogState:
    import copy

    return copy.deepcopy(st)
