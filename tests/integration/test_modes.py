"""Water and rail are playable: a ship route and a train route built from player primitives deliver
and earn on a real arena start save (docs/MODES.md).

Both oracles act only through ``World.act`` with logged, whitelisted player actions, so each route
below is also a proof that a model could build it with ``rh``.
"""

from pathlib import Path

import pytest

from railhead.actionlog import ActionLog
from railhead.oracle.rail import RailOracle
from railhead.oracle.water import WaterOracle
from railhead.replay import replay
from railhead.world import World

pytestmark = pytest.mark.integration

TASKS = Path(__file__).resolve().parents[2] / "tasks"
SAVE = TASKS / "arena-dev-0100" / "start.sav"   # 1960, temperate, a large inland sea
DAYS = 365


def _route_income(w: World, vehicles: list[int]) -> tuple[int, int]:
    """(profit over this and last year, cargo units carried right now) of the route's vehicles."""
    rows = {v["id"]: v for v in w.call("vehicles")}
    profit = sum(int(rows[v]["profit_this_year"]) + int(rows[v]["profit_last_year"]) for v in vehicles)
    return profit, sum(int(rows[v]["load"]) for v in vehicles)


def _delivered(w: World) -> int:
    return sum(int(q["delivered"]) for q in w.call("company")["quarters"])


def test_ship_route_delivers_passengers_and_earns_within_a_year(tmp_path):
    log = tmp_path / "water.jsonl"
    with World.load(tmp_path / "play", SAVE) as w:
        w.attach_log(ActionLog(log))
        route = WaterOracle(w).build()
        assert route.vehicles and len(route.stations) == 2 and route.depot
        w.wait(days=DAYS)
        profit, _ = _route_income(w, route.vehicles)
        delivered = _delivered(w)
    print(f"water route {route.detail} cost={route.cost} acts={route.acts} profit={profit} delivered={delivered}")
    assert delivered > 0, "no passengers delivered in a year"
    assert profit > 0, f"ferries lost money: {profit}"
    out = replay(SAVE, log, tmp_path / "replay")
    assert out.valid, out.divergences


def test_train_route_delivers_and_earns_within_a_year(tmp_path):
    log = tmp_path / "rail.jsonl"
    with World.load(tmp_path / "play", SAVE) as w:
        w.attach_log(ActionLog(log))
        route = RailOracle(w).build()
        assert route.vehicles and len(route.stations) == 2 and route.depot
        w.wait(days=DAYS)
        profit, _ = _route_income(w, route.vehicles)
        delivered = _delivered(w)
    print(f"rail route {route.detail} cost={route.cost} acts={route.acts} profit={profit} delivered={delivered}")
    assert delivered > 0, "the train delivered nothing in a year"
    assert profit > 0, f"the train lost money: {profit}"
    out = replay(SAVE, log, tmp_path / "replay")
    assert out.valid, out.divergences
