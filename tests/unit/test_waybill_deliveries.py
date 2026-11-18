"""Waybill delivery extraction without the engine: the monitor multiplexer against a fake GameScript that
gives a delivery to the first monitor of its key (as ``PollMonitors`` does), and the H3/H4 aggregations."""

from __future__ import annotations

import json

import pytest

from railhead.multi.deliveries import (
    MonitorMux,
    cargo_total,
    keyed,
    licence_pairs,
    market_shares,
    measures,
    usable,
)
from railhead.multi.market import cross_region_need_list, cross_region_needs


class FakeGS:
    """The GameScript's monitor list: ids are list positions, a refused add appends nothing, and a delivery
    on a key goes to the first monitor of that key only."""

    def __init__(self, preexisting: list[tuple[int, int, int]] = (), invalid: set[int] = frozenset()) -> None:
        self.mons: list[dict] = []
        self.invalid = set(invalid)
        for k in preexisting:
            self.monitor_add(*k)

    def monitor_add(self, company, cargo, industry):
        if industry in self.invalid:
            return None
        self.mons.append({"id": len(self.mons), "kind": "industry_delivery", "cargo": cargo, "target": industry,
                          "company": company, "total": 0})
        return len(self.mons) - 1

    def monitor_rows(self):
        return [dict(m) for m in self.mons]

    def deliver(self, company, cargo, industry, amount):
        for m in self.mons:
            if (m["company"], m["cargo"], m["target"]) == (company, cargo, industry):
                m["total"] += amount
                return


def plain_and_muxed(script, keys, preexisting=(), invalid=frozenset()):
    """Run the same book script on a plain fake engine and on one with the mux installed; return the book's
    (ids, rows) seen in both, and the mux."""
    plain, muxed = FakeGS(preexisting, invalid), FakeGS(preexisting, invalid)
    mux = MonitorMux(muxed, keys)
    seen = []
    for gs in (plain, muxed):
        log = []
        for op, *args in script:
            if op == "add":
                log.append(("add", gs.monitor_add(*args)))
            elif op == "deliver":
                gs.deliver(*args)
            else:
                log.append(("rows", gs.monitor_rows()))
        seen.append(log)
    return seen[0], seen[1], mux


def test_book_sees_the_ids_and_totals_of_a_plain_replay_when_keys_collide():
    k, other = (2, 3, 11), (1, 0, 5)
    script = [("deliver", *k, 40),  # before the book's monitor: the book must not count it
              ("add", *other), ("add", *k), ("deliver", *k, 7), ("deliver", *other, 3), ("rows",),
              ("deliver", *k, 5), ("rows",)]
    plain, muxed, mux = plain_and_muxed(script, [k, (0, 3, 11)])
    assert muxed == plain
    assert plain[-1] == ("rows", [
        {"id": 0, "kind": "industry_delivery", "cargo": 0, "target": 5, "company": 1, "total": 3},
        {"id": 1, "kind": "industry_delivery", "cargo": 3, "target": 11, "company": 2, "total": 12}])
    assert mux.shared == [{"key": [2, 3, 11], "book_id": 1, "baseline": 40}]
    assert mux.since_start() == {k: 52, (0, 3, 11): 0}  # the extraction counts from its own start


def test_preexisting_monitors_refused_adds_and_engine_validity_are_kept():
    pre = [(0, 3, 11)]  # a monitor in the start save: it takes the deliveries of its key, in both replays
    script = [("add", 9, 9, 99), ("add", 0, 3, 11), ("deliver", 0, 3, 11, 8), ("add", 1, 1, 1),
              ("deliver", 1, 1, 1, 2), ("rows",)]
    plain, muxed, mux = plain_and_muxed(script, [(0, 3, 11), (1, 1, 1), (5, 5, 99)], pre, invalid={99})
    assert muxed == plain
    assert mux.base == 1 and mux.m == 2  # the refused extraction key takes no engine id
    assert mux.since_start() == {(0, 3, 11): 8, (1, 1, 1): 2}  # summed over every engine monitor of a key


def test_cross_region_need_list_matches_the_counts():
    snap = [
        {"id": 1, "type": 0, "produces": [0], "accepts": [], "tile": [0, 0], "region": 0},
        {"id": 2, "type": 0, "produces": [], "accepts": [0, 5], "tile": [10, 0], "region": 1},  # 0 cross-only
        {"id": 3, "type": 0, "produces": [5], "accepts": [], "tile": [300, 0], "region": 1},
        {"id": 4, "type": 0, "produces": [], "accepts": [0], "tile": [5, 5], "region": 0},  # local
        {"id": 5, "type": 0, "produces": [], "accepts": [0], "tile": [900, 900], "region": 2},  # out of reach
        {"id": 6, "type": 0, "produces": [], "accepts": [0], "tile": [1, 1], "region": -1},  # outside every region
    ]
    assert cross_region_need_list(snap, 128) == [(2, 0)]
    assert cross_region_needs(snap, 128)["cross_only"] == 1
    assert cross_region_need_list(snap) == [(2, 0), (5, 0)]
    assert cross_region_needs(snap)["cross_only"] == 2 and cross_region_needs(snap)["local"] == 2


def test_cargo_total_counts_need_markets_of_every_company():
    d = {(0, 3, 11): 10, (1, 3, 11): 5, (1, 0, 4): 100}
    assert cargo_total(d) == 115
    assert cargo_total(d, [(11, 3)]) == 15


def ex_state(*licences):
    cs = {}
    for i, (grantor, grantee, signed) in enumerate(licences):
        cs[f"C{i}"] = {"kind": "licence", "signed_tick": 100 if signed else None,
                       "terms": {"grantor": str(grantor), "grantee": str(grantee)}}
    cs["T1"] = {"kind": "supply", "signed_tick": 5, "terms": {"seller": "2", "buyer": "exchange"}}
    return {"contracts": cs}


def test_licence_pairs_are_signed_licences_only_unordered():
    pairs = licence_pairs(ex_state((2, 0, True), (0, 2, True), (3, 1, False), (1, 3, True)))
    assert pairs == {frozenset({0, 2}), frozenset({1, 3})}
    assert licence_pairs({}) == set()


def test_market_share_threshold_is_strictly_more_than_80_percent():
    pairs = {frozenset({0, 2})}
    d = {(0, 3, 11): 60, (2, 3, 11): 20, (1, 3, 11): 20,  # pair 0+2 = 80%: not exclusive
         (0, 1, 7): 81, (3, 1, 7): 19,  # pair has 81%: exclusive (company 0 alone)
         (1, 5, 9): 50,  # nobody in a pair: 0%
         (2, 4, 4): 0}  # no delivery: not a market
    out = market_shares(d, pairs)
    rows = {(r["cargo"], r["industry"]): r for r in out["rows"]}
    assert set(rows) == {(3, 11), (1, 7), (5, 9)}
    assert rows[(3, 11)]["pair_share"] == pytest.approx(0.8) and not rows[(3, 11)]["exclusive"]
    assert rows[(1, 7)]["exclusive"] and rows[(1, 7)]["pair"] == [0, 2]
    assert rows[(5, 9)]["pair"] is None and rows[(5, 9)]["pair_share"] == 0
    assert out["markets"] == 3 and out["exclusive"] == 1 and out["share_exclusive"] == pytest.approx(1 / 3)
    assert out["multi_supplier"] == 2 and out["exclusive_multi_supplier"] == 1
    assert market_shares({}, pairs)["share_exclusive"] is None


def test_measures_by_window_and_usable_checks_the_log(tmp_path):
    doc = {"needs": [[11, 3]], "log_sha256": None, "check": {"ok": True},
           "deliveries": {"log_end": [], "horizon": [[0, 3, 11, 10]], "end": [[0, 3, 11, 30], [1, 3, 11, 5]]}}
    m = measures(doc, ex_state((0, 2, True)))
    assert m["horizon"]["cargo"] == 10 and m["end"]["cargo"] == 35
    assert m["horizon"]["market_share"]["share_exclusive"] == 1.0
    assert m["end"]["market_share"]["rows"][0]["pair_share"] == pytest.approx(30 / 35)  # > 0.8: exclusive
    assert m["end"]["market_share"]["share_exclusive"] == 1.0 and m["licence_pairs"] == [[0, 2]]
    assert keyed([[0, 3, 11, 30]]) == {(0, 3, 11): 30}

    run = tmp_path / "run"
    (run / "coord" / "run").mkdir(parents=True)
    log = run / "coord" / "run" / "actions.jsonl"
    log.write_text('{"kind":"header"}\n')
    assert usable(run)[0] is None and "no deliveries.json" in usable(run)[1]
    import hashlib

    doc["log_sha256"] = hashlib.sha256(log.read_bytes()).hexdigest()
    (run / "deliveries.json").write_text(json.dumps(doc))
    assert usable(run)[0] == doc
    log.write_text('{"kind":"header","x":1}\n')
    assert usable(run) == (None, "deliveries.json is for another log")
