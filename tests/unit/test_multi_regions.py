"""Franchise regions: footprints of every build action, the region check, generation and rotation."""

from __future__ import annotations

import pytest

from railhead.actions import REGISTRY, WHITELIST, validate
from railhead.multi.regions import (
    NOT_BUILDS,
    FranchiseError,
    RegionMap,
    footprint,
    make_region_map,
    region_of,
    rotation,
    split_regions,
)

MX = 64


def idx(x: int, y: int) -> int:
    return y * MX + x


class FakeProbe:
    def __init__(self, tiles: dict[int, dict] | None = None, towns: dict[int, tuple[int, int]] | None = None):
        self.tiles = tiles or {}
        self.towns = towns or {}

    def tile(self, index: int) -> dict:
        return self.tiles.get(index, {"height": 5})

    def town(self, town_id: int):
        return self.towns.get(town_id)


SAMPLE = {
    "tile": "10,10", "from_tile": "10,10", "to_tile": "12,10", "front_tile": "10,11", "start_tile": "10,10",
    "end_tile": "13,10", "toward_tile": "11,10", "track": "ne_sw", "axis": "x", "platforms": 2, "length": 3,
    "corner": "all", "type": "small", "town": 0, "kind": "statue", "vehicle": 1, "railtype": 0, "roadtype": 0,
    "amount": 0, "name": "x", "engine": 0, "depot_tile": "10,10", "train_vehicle": 2, "index": 1,
    "other_vehicle": 2, "cargo": "PASS",
}


def sample_args(m: str) -> dict:
    spec = REGISTRY[m.replace("_", ".", 1)]
    a = {p.name: SAMPLE[p.name] for p in spec.params if p.required}
    if m == "bridge_build":
        a["vehicle"] = "road"
    if m == "bridge_tunnel":
        a["vehicle"] = "road"
    return validate(m, a, map_size_x=MX)


def rm_one(rect=(0, 0, 20, 20)) -> RegionMap:
    return RegionMap(MX, MX, {0: [list(rect)], 1: [[21, 0, 63, 63]]})


@pytest.mark.parametrize("m", sorted(WHITELIST))
def test_every_action_has_a_footprint_rule(m):
    probe = FakeProbe(towns={0: (5, 5)})
    tiles = footprint(m, sample_args(m), MX, probe)
    if m in NOT_BUILDS:
        assert tiles == []
    else:
        assert tiles and all(isinstance(t, tuple) and len(t) == 2 for t in tiles)


def test_station_airport_and_line_footprints():
    st = footprint("rail_station", {"tile": idx(2, 3), "axis": "x", "platforms": 2, "length": 4}, MX)
    assert set(st) == {(x, y) for x in range(2, 6) for y in range(3, 5)}
    st_y = footprint("rail_station", {"tile": idx(2, 3), "axis": "y", "platforms": 2, "length": 4}, MX)
    assert set(st_y) == {(x, y) for x in range(2, 4) for y in range(3, 7)}
    ap = footprint("air_airport", {"tile": idx(1, 1), "type": "commuter"}, MX)
    assert len(ap) == 20 and (5, 4) in ap
    ln = footprint("rail_line", {"from_tile": idx(5, 5), "to_tile": idx(2, 7)}, MX)
    assert (2, 7) in ln and (5, 5) in ln and len(ln) == 12  # diagonal: the whole bounding box
    stop = footprint("road_stop", {"tile": idx(5, 5), "front_tile": idx(5, 6), "drive_through": True}, MX)
    assert set(stop) == {(5, 5), (5, 6), (5, 4)}


def test_world_dependent_footprints():
    probe = FakeProbe(tiles={idx(5, 5): {"dock": {"ok": True, "water_tile": idx(5, 6)}, "incline": "se", "height": 2},
                             idx(5, 4): {"height": 4}, idx(5, 3): {"height": 2}},
                      towns={7: (30, 30)})
    assert footprint("water_dock", {"tile": idx(5, 5)}, MX, probe) == [(5, 5), (5, 6)]
    assert footprint("water_lock", {"tile": idx(5, 5)}, MX, probe) == [(5, 4), (5, 5), (5, 6)]
    # tunnel: uphill (opposite of the downhill se) until the same base height
    assert footprint("bridge_tunnel", {"start_tile": idx(5, 5), "vehicle": "road"}, MX, probe) == [(5, 5), (5, 4), (5, 3)]
    assert footprint("town_action", {"town": 7, "kind": "statue"}, MX, probe) == [(30, 30)]
    with pytest.raises(FranchiseError):
        footprint("town_action", {"town": 9, "kind": "statue"}, MX, probe)


def test_check_action_in_and_out_of_region():
    rm = rm_one()
    rm.check_action(0, 0, "rail_track", {"tile": idx(20, 20), "track": "ne_sw"})
    rm.check_action(0, 0, "vehicle_start", {"vehicle": 1})  # not a build
    with pytest.raises(FranchiseError) as e:
        rm.check_action(0, 0, "road_line", {"from_tile": idx(19, 5), "to_tile": idx(23, 5)})
    assert e.value.code == "ERR_FRANCHISE"
    assert "21,5" in str(e.value) and "outside your franchise region" in str(e.value)
    rm.check_action(1, 0, "rail_track", {"tile": idx(40, 40), "track": "ne_sw"})
    with pytest.raises(FranchiseError):
        rm.check_action(1, 0, "town_action", {"town": 0, "kind": "statue"}, FakeProbe(towns={0: (5, 5)}))


def test_regions_in_force_at_a_tick_and_json_roundtrip(tmp_path):
    rm = rm_one()
    rm.changes = [{"tick": 1000, "slot": 0, "rects": [[0, 0, 30, 30]]}]
    assert not rm.contains(0, 999, 25, 25)
    assert rm.contains(0, 1000, 25, 25)
    rm.save(tmp_path / "regions.json")
    back = RegionMap.load(tmp_path / "regions.json")
    assert back == rm
    with pytest.raises(ValueError):
        RegionMap.from_json({"v": 1, "map": [10, 10], "regions": {"0": [[0, 0, 10, 3]]}})


def test_split_is_a_balanced_partition():
    pts = [(x, y) for x in range(2, 250, 7) for y in range(2, 120, 5)] + [(200, 200)] * 30
    rects = split_regions(256, 256, 4, pts)
    assert len(rects) == 4
    cover = {}
    for i, r in enumerate(rects):
        for x in range(r[0], r[2] + 1, 3):
            for y in range(r[1], r[3] + 1, 3):
                assert (x, y) not in cover
                cover[(x, y)] = i
    counts = [sum(r[0] <= x <= r[2] and r[1] <= y <= r[3] for x, y in pts) for r in rects]
    assert max(counts) <= 2 * min(counts) + 5
    assert split_regions(256, 256, 4, pts) == rects  # deterministic
    assert len(split_regions(512, 512, 8, pts)) == 8
    assert len(split_regions(64, 64, 3, [])) == 3


def test_rotation_gives_each_slot_each_type():
    n = 4
    seen = {s: set() for s in range(n)}
    for seed in range(100, 100 + n):
        rot = rotation(seed, n)
        assert sorted(rot) == list(range(n))
        for s, t in enumerate(rot):
            seen[s].add(t)
    assert all(v == set(range(n)) for v in seen.values())
    rm = make_region_map(256, 256, 4, [(10, 10), (200, 200)], offset=101)
    assert rm.types == {s: (s + 101) % 4 for s in range(4)}
    assert region_of(rm, 128, 128) in range(4)


def test_terraform_footprints_include_the_shared_corners():
    assert len(footprint("land_raise", {"tile": idx(5, 5), "corner": "n"}, MX)) == 9
    lv = footprint("land_level", {"from_tile": idx(5, 5), "to_tile": idx(6, 7)}, MX)
    assert (4, 4) in lv and (7, 8) in lv and len(lv) == 4 * 5
