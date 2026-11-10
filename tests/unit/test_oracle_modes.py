"""Planning logic of the water and rail oracles, and the mode census, on hand-made tiles (no engine)."""

import itertools

import pytest

from railhead.oracle.census import census_records, summarize
from railhead.oracle.rail import (
    StationSite,
    path_pieces,
    rail_astar,
    straight_runs,
    track_piece,
)
from railhead.oracle.terrain import TileMap
from railhead.oracle.water import Site, depot_sites, dock_sites_near, dock_water_tile, pick_pair


def tilemap(rows: list[str], docks: dict[tuple[int, int], bool] | None = None) -> TileMap:
    """Kind strings, one per y from (0, 0); every tile flat, buildable land unless water."""
    tm = TileMap(lambda *a, **k: [], 64, 64)
    out = []
    for y, kinds in enumerate(rows):
        dock = "".join("1" if (docks or {}).get((x, y)) else "0" for x in range(len(kinds)))
        out.append({"y": y, "x": 0, "kind": kinds, "height": "0" * len(kinds), "owner": "." * len(kinds),
                    "slope": "0" * len(kinds), "build": "".join("0" if k == "~" else "1" for k in kinds),
                    "dock": dock})
    tm.load_rows(out)
    return tm


# ------------------------------------------------------------------------------------------- rail
def test_track_piece_straights_and_corners():
    assert track_piece((1, 0), (1, 0)) == "ne_sw"
    assert track_piece((0, -1), (0, -1)) == "nw_se"
    # arrive moving +x (entered over the ne side), leave to +y (over the se side)
    assert track_piece((1, 0), (0, 1)) == "ne_se"
    assert track_piece((1, 0), (0, -1)) == "nw_ne"
    assert track_piece((-1, 0), (0, 1)) == "sw_se"
    assert track_piece((0, 1), (-1, 0)) == "nw_ne"
    with pytest.raises(ValueError):
        track_piece((1, 0), (-1, 0))


def test_path_pieces_and_straight_runs_compress_to_drags():
    path = [(0, 0), (1, 0), (2, 0), (2, 1), (2, 2), (2, 3)]
    pieces = path_pieces(path, (1, 0))
    assert pieces == [((0, 0), "ne_sw"), ((1, 0), "ne_sw"), ((2, 0), "ne_se"),
                      ((2, 1), "nw_se"), ((2, 2), "nw_se"), ((2, 3), "nw_se")]
    assert straight_runs(pieces) == [((0, 0), (1, 0)), ((2, 1), (2, 3))]


def test_rail_astar_reaches_goal_heading_and_keeps_runs_between_turns():
    free = lambda t: 1.0 if 0 <= t[0] < 20 and 0 <= t[1] < 20 else None
    path = rail_astar(free, (0, 0), (1, 0), (6, 6), (0, 1), lambda t: True, min_run=2)
    assert path[0] == (0, 0) and path[-1] == (6, 6)
    pieces = path_pieces(path, (1, 0))
    assert pieces[-1][1] == "nw_se"  # arrives heading +y
    turns = [i for i, (_, p) in enumerate(pieces) if p not in ("ne_sw", "nw_se")]
    assert turns and all(b - a > 2 for a, b in itertools.pairwise(turns))
    assert all(abs(a[0] - b[0]) + abs(a[1] - b[1]) == 1 for a, b in itertools.pairwise(path))


def test_rail_astar_refuses_back_to_back_turns():
    # a U in two tiles (turn, then turn again at once) is a 90-degree turn: OpenTTD forbids it
    open_ = {(0, 0), (1, 0), (1, 1), (0, 1)}
    cost = lambda t: 1.0 if t in open_ else None
    assert rail_astar(cost, (0, 0), (1, 0), (0, 1), (-1, 0), lambda t: True, min_run=2) is None
    assert rail_astar(cost, (0, 0), (1, 0), (0, 1), (-1, 0), lambda t: True, min_run=0) == [(0, 0), (1, 0), (1, 1), (0, 1)]
    # one turn after a straight approach is fine
    assert rail_astar(cost, (0, 0), (1, 0), (1, 1), (0, 1), lambda t: True, min_run=2) == [(0, 0), (1, 0), (1, 1)]


def test_rail_astar_turns_only_where_allowed():
    free = lambda t: 1.0 if 0 <= t[0] < 10 and 0 <= t[1] < 10 else None
    path = rail_astar(free, (0, 0), (1, 0), (5, 5), (0, 1), lambda t: t == (5, 0), min_run=2)
    assert (5, 0) in path and path_pieces(path, (1, 0))[path.index((5, 0))][1] == "ne_se"


def test_station_site_tiles_and_ends():
    s = StationSite((10, 4), "x", 3)
    assert s.tiles() == [(10, 4), (11, 4), (12, 4)]
    assert s.ends() == [((12, 4), (1, 0)), ((10, 4), (-1, 0))]


# ------------------------------------------------------------------------------------------- water
LAKE = [
    "..........",
    "..d.......",
    "..~~~~~~..",
    "..~~~~~~..",
    "..~~~~~~..",
    "..~~~~~~..",
    "......d...",
]


def test_dock_sites_and_their_water_tile():
    tm = tilemap(LAKE, {(2, 1): True, (6, 6): True})
    assert dock_sites_near(tm, (2, 0), 2) == [(2, 1)]
    assert dock_water_tile(tm, (2, 1)) == (2, 2)
    assert dock_water_tile(tm, (6, 6)) == (6, 5)


def test_depot_sites_need_open_water_on_every_side():
    tm = tilemap(LAKE)
    comp = tm.water_component((2, 2))
    assert len(comp) == 24
    sites = depot_sites(tm, (2, 2), comp, {(2, 2)})
    assert sites, "the lake has room for a depot"
    for t, t2 in sites:
        assert t in comp and t2 in comp and abs(t[0] - t2[0]) + abs(t[1] - t2[1]) == 1
        assert t2[0] >= t[0] and t2[1] >= t[1]  # the depot extends in +x or +y
        assert t not in {(2, 2), (3, 2), (2, 3)}  # clear of the dock's water tile and its neighbours


def test_pick_pair_needs_the_same_water_and_the_distance_window():
    a, b, c = Site(1, (2, 1), (2, 2)), Site(2, (6, 6), (6, 5)), Site(3, (40, 40), (40, 41))
    towns = {1: {"pax_produced": 300}, 2: {"pax_produced": 200}, 3: {"pax_produced": 900}}
    comp_of = {(2, 2): 0, (6, 5): 0, (40, 41): 1}
    assert pick_pair({1: [a], 2: [b], 3: [c]}, towns, comp_of, 1, 50) == [(a, b)]
    assert pick_pair({1: [a], 2: [b]}, towns, comp_of, 10, 50) == []


# ------------------------------------------------------------------------------------------- census
def test_census_attributes_vehicles_to_the_mode_of_their_depot():
    recs = [
        {"kind": "act", "m": "rail_station", "a": {}, "ok": True, "ret": {"station": 0}},
        {"kind": "act", "m": "rail_depot", "a": {"tile": 5}, "ok": True, "ret": {"depot_tile": 5}},
        {"kind": "act", "m": "rail_line", "a": {}, "ok": False, "code": "ERR_AREA_NOT_CLEAR"},
        {"kind": "act", "m": "vehicle_buy", "a": {"depot_tile": 5}, "ok": True, "ret": {"vehicle": 1}},
        {"kind": "act", "m": "vehicle_buy", "a": {"depot_tile": 5}, "ok": True, "ret": {"vehicle": 2}},
        {"kind": "act", "m": "vehicle_attach", "a": {"vehicle": 2, "train_vehicle": 1}, "ok": True, "ret": {}},
        {"kind": "act", "m": "air_airport", "a": {}, "ok": True, "ret": {"hangar_tile": 9}},
        {"kind": "act", "m": "vehicle_buy", "a": {"depot_tile": 9}, "ok": True, "ret": {"vehicle": 3}},
        {"kind": "act", "m": "vehicle_start", "a": {"vehicle": 1}, "ok": True, "ret": {}},
        {"kind": "act", "m": "vehicle_start", "a": {"vehicle": 3}, "ok": True, "ret": {}},
        {"kind": "act", "m": "water_dock", "a": {"dry_run": True}, "ok": False, "code": "ERR_SITE_UNSUITABLE"},
        {"kind": "wait", "a": {"days": 1}},
    ]
    pc = census_records("g/p", recs)
    assert pc.bought["rail"] == 1 and pc.wagons == 1 and pc.bought["air"] == 1
    assert pc.started["rail"] == 1 and pc.started["air"] == 1
    assert pc.fail_codes[("rail", "ERR_AREA_NOT_CLEAR")] == 1
    assert pc.calls[("water", False)] == 1
    assert pc.first_start_calls["rail"] == 7  # station, depot, failed line, 2 buys, attach, start
    s = summarize([pc])
    assert s["tried"] == {"rail": 1, "road": 0, "water": 1, "air": 1}
    assert s["ran_vehicles"]["water"] == 0


def test_rapids_split_the_water_for_ships():
    tm = tilemap(["~~~~", "~~~~"])
    tm.tiles[(2, 0)].slope = tm.tiles[(2, 1)].slope = "1"  # a sloped river column: rapids
    assert tm.water_component((0, 0)) == {(0, 0), (1, 0), (0, 1), (1, 1)}
