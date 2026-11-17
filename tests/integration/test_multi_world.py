"""Waybill start state: N companies in one world, an act lands on the bound company, and binding is
invisible to the checksum (so bind-per-request needs no GameScript change)."""

from __future__ import annotations

import pytest

from railhead.multi.gen import build_start, company_name
from railhead.world import World
from tests.integration.conftest import make_config

pytestmark = pytest.mark.integration

START_CASH = 150_000


@pytest.fixture(scope="module")
def start3(tmp_path_factory):
    tmp = tmp_path_factory.mktemp("multi")
    with World.create(tmp / "gen", make_config(seed=5), found_company=False) as w:
        companies = build_start(w, tmp / "start.sav", 3, start_cash=START_CASH, history_days=5)
    return tmp / "start.sav", companies


def _rows(w):
    return {c["id"]: c for c in w.call("companies")}


def test_three_named_companies_with_the_start_cash(start3, tmp_path):
    start, companies = start3
    assert [c["slot"] for c in companies] == [0, 1, 2]
    assert len({c["id"] for c in companies}) == 3
    with World.load(tmp_path / "w", start) as w:
        rows = _rows(w)
        assert set(rows) == {c["id"] for c in companies}
        for c in companies:
            assert rows[c["id"]]["name"] == company_name(c["slot"]) == c["name"]
            assert rows[c["id"]]["money"] == START_CASH and rows[c["id"]]["loan"] == START_CASH


def test_an_act_changes_only_the_bound_company(start3, tmp_path):
    start, companies = start3
    with World.load(tmp_path / "w", start) as w:
        for i, c in enumerate(companies):
            before = _rows(w)
            w.call("bind_company", company=c["id"])
            w.call_args("company_loan", {"amount": START_CASH + 10_000 * (i + 1)})
            after = _rows(w)
            for o in companies:
                delta = after[o["id"]]["money"] - before[o["id"]]["money"]
                assert delta == (10_000 * (i + 1) if o["id"] == c["id"] else 0)


def _play(start, wdir, binds):
    """Bind in the given order, act, step: the checksum must not depend on the bind order."""
    with World.load(wdir, start) as w:
        out = []
        for order in binds:
            for cid, amount in order:
                w.call("bind_company", company=cid)
                w.call_args("company_loan", {"amount": amount})
            w.step(ticks=200)
            out.append(w.call("checksum")["h"])
        return out


def test_bind_order_is_invisible_and_replays_are_deterministic(start3, tmp_path):
    start, companies = start3
    a, b, c = (x["id"] for x in companies)
    p1 = [[(a, 200_000), (b, 160_000)], [(c, 170_000)], [(a, 150_000)]]
    p2 = [[(b, 160_000), (a, 200_000)], [(c, 170_000)], [(a, 150_000)]]  # same acts, other bind order
    h1 = _play(start, tmp_path / "w1", p1)
    h2 = _play(start, tmp_path / "w2", p1)
    h3 = _play(start, tmp_path / "w3", p2)
    assert h1 == h2 == h3
    assert len(set(h1)) == 3
