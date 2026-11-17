"""Tender generation (deterministic, cross-region only, formula) and the sealed-bid auction rule."""

import pytest

from railhead.exchange.errors import ExchangeError
from railhead.exchange.tenders import TenderConfig, auction, candidates, draw, generate_tenders

COAL, GOODS, OIL = 1, 5, 3


def ind(i, region, tile, produces=(), accepts=(), typ="x"):
    return {"id": i, "type": typ, "produces": list(produces), "accepts": list(accepts), "tile": list(tile),
            "region": region}


WORLD = [
    ind(1, 0, (10, 10), produces=[COAL], typ="mine"),
    ind(2, 1, (40, 10), accepts=[COAL], typ="power"),        # coal producer in region 0: candidate, d=30
    ind(3, 0, (12, 10), accepts=[COAL], typ="power"),        # only same-region producer: not a candidate
    ind(4, 1, (40, 50), produces=[OIL], typ="well"),
    ind(5, 0, (10, 50), accepts=[OIL, GOODS], typ="refinery"),  # oil from region 1: candidate; goods: none
    ind(6, 2, (90, 10), produces=[COAL], typ="mine"),        # second coal source, farther from 2
]


def test_candidates_need_a_producer_in_a_different_region():
    got = candidates(WORLD)
    assert [(c["industry"], c["cargo"]) for c in got] == [(2, COAL), (3, COAL), (5, OIL)]
    by = {(c["industry"], c["cargo"]): c for c in got}
    assert by[(2, COAL)]["source_industry"] == 1 and by[(2, COAL)]["distance"] == 30
    assert by[(3, COAL)]["source_industry"] == 6 and by[(3, COAL)]["distance"] == 78  # mine 1 is same region
    assert by[(5, OIL)]["distance"] == 30


def test_candidates_ignore_input_order():
    assert candidates(WORLD) == candidates(list(reversed(WORLD)))


def test_bad_snapshot():
    with pytest.raises(ExchangeError) as e:
        candidates([ind(1, 0, (0, 0)), ind(1, 1, (0, 0))])
    assert e.value.code == "bad_snapshot"
    with pytest.raises(ExchangeError):
        candidates([{"id": 1}])
    with pytest.raises(ExchangeError):
        candidates([ind(1, "north", (0, 0))])


def test_generation_is_deterministic_and_seed_sensitive():
    a = generate_tenders(WORLD, seed=7, quarter=0, tick=100)
    assert a == generate_tenders(list(reversed(WORLD)), seed=7, quarter=0, tick=100)
    variants = {tuple(t["industry"] for t in generate_tenders(WORLD, seed=s, quarter=q, tick=0,
                                                               config=TenderConfig(per_quarter=1)))
                for s in range(5) for q in range(4)}
    assert len(variants) > 1


def test_draw_is_stable_known_answer():
    # SHA-256 based; a change here changes every generated tender, so it is pinned.
    assert [draw(1000, 42, 0, "pick", i) for i in range(3)] == [318, 148, 644]
    assert draw(1, "anything") == 0
    with pytest.raises(ValueError):
        draw(0)


def test_formula_without_jitter():
    cfg = TenderConfig(per_quarter=5, quantity_base=100, quantity_per_tile=2, quantity_jitter_pct=0,
                       payout_base_per_unit=10, payout_per_unit_per_tile=1, penalty_bp=2500, bid_ticks=50,
                       window_ticks=1000)
    ts = generate_tenders(WORLD, seed=1, quarter=3, tick=200, config=cfg, first_index=10)
    assert sorted(t["tender_id"] for t in ts) == ["T10", "T11", "T12"]       # only 3 candidates exist
    t = next(t for t in ts if t["industry"] == 2)
    assert t["quantity"] == 100 + 2 * 30
    assert t["max_price"] == 160 * (10 + 30)
    assert t["penalty_per_unit"] == 160 * 40 * 2500 // 10000 // 160 == 10
    assert (t["open_tick"], t["bid_close"], t["window_start"], t["deadline"]) == (200, 250, 250, 1250)
    assert t["quarter"] == 3 and t["cargo"] == COAL


def test_jitter_stays_in_bounds():
    cfg = TenderConfig(per_quarter=3, quantity_base=1000, quantity_jitter_pct=20)
    for s in range(30):
        for t in generate_tenders(WORLD, seed=s, quarter=0, tick=0, config=cfg):
            assert 800 <= t["quantity"] <= 1200


def test_no_candidates_no_tenders():
    assert generate_tenders([ind(1, 0, (0, 0), produces=[COAL])], seed=1, quarter=0, tick=0) == []


def test_bad_config():
    with pytest.raises(ExchangeError) as e:
        TenderConfig(per_quarter=-1)
    assert e.value.code == "bad_config"
    with pytest.raises(ExchangeError):
        TenderConfig(window_ticks=0)


def test_auction_lowest_price_then_earliest_tick_then_company():
    bids = [{"company": "b", "price": 90, "tick": 3}, {"company": "a", "price": 90, "tick": 5},
            {"company": "c", "price": 95, "tick": 1}]
    assert auction(bids)["company"] == "b"
    assert auction([{"company": "z", "price": 5, "tick": 1}, {"company": "y", "price": 5, "tick": 1}])["company"] == "y"
    assert auction([]) is None
