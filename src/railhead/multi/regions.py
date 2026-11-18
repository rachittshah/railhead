"""Franchise regions of a Waybill task (docs/WAYBILL.md, section 8). Pure Python, no engine.

``regions.json`` gives each company slot a list of rectangles. A company can build or change
infrastructure only on tiles in its own rectangles. The coordinator refuses a build outside them with
``ERR_FRANCHISE`` (the request is not logged), and the replay refuses a log that contains such a build.

    {"v": 1, "map": [X, Y],
     "regions": {"0": [[x0, y0, x1, y1], ...], "1": [...]},      # slot -> rectangles, both corners inclusive
     "types": {"0": 2, "1": 0, ...},                             # slot -> region type (rotation), optional
     "changes": [{"tick": T, "slot": S, "rects": [[...]]}]}      # optional: new rectangles of a slot from tick T

:func:`footprint` gives the tiles that one action builds on or changes. Most footprints come from the
arguments only. Three need the world, through a ``probe``: a dock (its water tile), a lock (its axis), a
tunnel (its other end), and a town action (the town centre). The live coordinator and the replay use the
same probe queries at the same tick, so they get the same answer.
"""

from __future__ import annotations

import json
from collections.abc import Iterable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol

REGIONS_FILE = "regions.json"
REGIONS_V = 1

# Airport sizes (x, y) of the base set, not rotated (GSAirport.GetAirportWidth / GetAirportHeight).
AIRPORT_SIZE = {"small": (4, 3), "large": (6, 6), "metropolitan": (6, 6), "international": (7, 7),
                "commuter": (5, 4), "intercon": (9, 11), "heliport": (1, 1), "helistation": (4, 2),
                "helidepot": (2, 2)}
# The downhill direction of an inclined tile, as the `tile` query names it (QH.INCLINES).
INCLINE_DOWNHILL = {"se": (0, 1), "sw": (1, 0), "nw": (0, -1), "ne": (-1, 0)}
MAX_TUNNEL_WALK = 256

# Actions that do not build or change infrastructure: no franchise check.
NOT_BUILDS = frozenset({
    "rail_set_type", "road_set_type", "company_loan", "company_rename",
    "vehicle_buy", "vehicle_sell", "vehicle_attach", "vehicle_sell_wagon", "vehicle_start", "vehicle_stop",
    "vehicle_depot", "vehicle_refit", "vehicle_clone", "vehicle_skip_order",
    "order_append", "order_insert", "order_remove", "order_clear", "order_share",
})


class FranchiseError(ValueError):
    """A build outside the acting company's regions (code ``ERR_FRANCHISE``)."""

    code = "ERR_FRANCHISE"


class Probe(Protocol):
    def tile(self, index: int) -> dict: ...

    def town(self, town_id: int) -> tuple[int, int] | None: ...


def xy(index: int, map_x: int) -> tuple[int, int]:
    return index % map_x, index // map_x


def _box(a: tuple[int, int], b: tuple[int, int]) -> list[tuple[int, int]]:
    (x0, x1), (y0, y1) = sorted((a[0], b[0])), sorted((a[1], b[1]))
    return [(x, y) for y in range(y0, y1 + 1) for x in range(x0, x1 + 1)]


def _rect(x: int, y: int, w: int, h: int) -> list[tuple[int, int]]:
    return [(x + i, y + j) for j in range(h) for i in range(w)]


def is_build(m: str) -> bool:
    return m not in NOT_BUILDS


def footprint(m: str, a: dict[str, Any], map_x: int, probe: Probe | None = None) -> list[tuple[int, int]]:
    """The tiles (x, y) that action ``m`` with canonical arguments ``a`` builds on or changes.
    Empty for an action that is not a build. Conservative where the engine adds pieces next to the
    target (depots and stops join the road or track on the front tile; a drive-through stop also on the
    back tile)."""
    if not is_build(m):
        return []
    t = lambda k: xy(int(a[k]), map_x)
    if m in ("rail_track", "rail_remove", "rail_signal", "rail_remove_signal", "rail_waypoint", "water_canal",
             "land_demolish"):
        return [t("tile")]
    if m in ("land_raise", "land_lower"):  # corners are shared: the 8 neighbours change too
        x, y = t("tile")
        return _box((x - 1, y - 1), (x + 1, y + 1))
    if m == "land_level":
        (x0, y0), (x1, y1) = t("from_tile"), t("to_tile")
        return _box((min(x0, x1) - 1, min(y0, y1) - 1), (max(x0, x1) + 1, max(y0, y1) + 1))
    if m in ("rail_line", "road_line", "road_one_way", "road_remove"):
        return _box(t("from_tile"), t("to_tile"))
    if m == "bridge_build":
        return _box(t("start_tile"), t("end_tile"))
    if m == "rail_station":
        x, y = t("tile")
        length, platforms = int(a["length"]), int(a["platforms"])
        return _rect(x, y, length, platforms) if a["axis"] == "x" else _rect(x, y, platforms, length)
    if m == "air_airport":
        x, y = t("tile")
        w, h = AIRPORT_SIZE[a["type"]]
        return _rect(x, y, w, h)
    if m in ("rail_depot", "road_depot"):
        return [t("tile"), t("front_tile")]
    if m == "road_stop":
        (x, y), (fx, fy) = t("tile"), t("front_tile")
        out = [(x, y), (fx, fy)]
        if a.get("drive_through"):
            out.append((2 * x - fx, 2 * y - fy))
        return out
    if m == "water_ship_depot":
        (x, y), (tx, _ty) = t("tile"), t("toward_tile")
        return [(x, y), (x + 1, y) if tx != x else (x, y + 1)]
    if m == "water_dock":
        x, y = t("tile")
        info = probe.tile(int(a["tile"])) if probe else {}
        dock = info.get("dock") or {}
        if dock.get("ok") and isinstance(dock.get("water_tile"), int) and dock["water_tile"] >= 0:
            return [(x, y), xy(dock["water_tile"], map_x)]
        return [(x, y)]
    if m == "water_lock":
        x, y = t("tile")
        d = INCLINE_DOWNHILL.get((probe.tile(int(a["tile"])) if probe else {}).get("incline"))
        return [(x, y)] if d is None else [(x - d[0], y - d[1]), (x, y), (x + d[0], y + d[1])]
    if m == "bridge_tunnel":
        return tunnel_tiles(int(a["start_tile"]), map_x, probe)
    if m == "town_action":
        loc = probe.town(int(a["town"])) if probe else None
        if loc is None:
            raise FranchiseError(f"town {a['town']} does not exist")
        return [loc]
    raise FranchiseError(f"{m}: no franchise footprint is defined for this action")


def tunnel_tiles(start: int, map_x: int, probe: Probe | None) -> list[tuple[int, int]]:
    """The tiles of a tunnel that starts at ``start``: it goes uphill, into the hill, to the first tile
    with the same base height (as GSTunnel.GetOtherTunnelEnd). Only the start if the tile is not inclined."""
    x, y = xy(start, map_x)
    if probe is None:
        return [(x, y)]
    info = probe.tile(start)
    d = INCLINE_DOWNHILL.get(info.get("incline"))
    if d is None:
        return [(x, y)]
    dx, dy, h = -d[0], -d[1], info.get("height")
    out = [(x, y)]
    cx, cy = x, y
    for _ in range(MAX_TUNNEL_WALK):
        cx, cy = cx + dx, cy + dy
        if cx < 0 or cy < 0 or cx >= map_x:
            break
        out.append((cx, cy))
        if probe.tile(cy * map_x + cx).get("height") == h:
            break
    return out


@dataclass
class RegionMap:
    map_x: int
    map_y: int
    regions: dict[int, list[list[int]]]
    types: dict[int, int] = field(default_factory=dict)
    changes: list[dict] = field(default_factory=list)

    # ------------------------------------------------------------------ io
    @classmethod
    def from_json(cls, d: dict) -> RegionMap:
        if not isinstance(d, dict) or d.get("v") != REGIONS_V:
            raise ValueError(f"regions: not a v{REGIONS_V} region map")
        mx, my = d["map"]
        regions = {int(k): [list(map(int, r)) for r in v] for k, v in d["regions"].items()}
        changes = sorted((dict(c) for c in d.get("changes", [])), key=lambda c: int(c["tick"]))
        rm = cls(int(mx), int(my), regions, {int(k): int(v) for k, v in d.get("types", {}).items()}, changes)
        rm.check()
        return rm

    def to_json(self) -> dict:
        d: dict[str, Any] = {"v": REGIONS_V, "map": [self.map_x, self.map_y],
                             "regions": {str(k): self.regions[k] for k in sorted(self.regions)}}
        if self.types:
            d["types"] = {str(k): self.types[k] for k in sorted(self.types)}
        if self.changes:
            d["changes"] = self.changes
        return d

    @classmethod
    def load(cls, path: Path) -> RegionMap:
        return cls.from_json(json.loads(Path(path).read_text()))

    @classmethod
    def for_task(cls, task_dir: Path) -> RegionMap | None:
        p = Path(task_dir) / REGIONS_FILE
        return cls.load(p) if p.exists() else None

    def save(self, path: Path) -> None:
        Path(path).write_text(json.dumps(self.to_json(), indent=1, sort_keys=True))

    def check(self) -> None:
        for rects in [*self.regions.values(), *(c["rects"] for c in self.changes)]:
            for r in rects:
                if len(r) != 4 or not (0 <= r[0] <= r[2] < self.map_x and 0 <= r[1] <= r[3] < self.map_y):
                    raise ValueError(f"regions: rectangle {r} is not [x0, y0, x1, y1] inside the map")

    # ------------------------------------------------------------------ queries
    def rects_at(self, slot: int, tick: int) -> list[list[int]]:
        """The rectangles of ``slot`` in force at ``tick`` (the last change at or before ``tick``)."""
        rects = self.regions.get(slot, [])
        for c in self.changes:
            if int(c["tick"]) <= tick and int(c["slot"]) == slot:
                rects = [list(map(int, r)) for r in c["rects"]]
        return rects

    def contains(self, slot: int, tick: int, x: int, y: int) -> bool:
        return any(r[0] <= x <= r[2] and r[1] <= y <= r[3] for r in self.rects_at(slot, tick))

    def outside(self, slot: int, tick: int, tiles: Iterable[tuple[int, int]]) -> list[tuple[int, int]]:
        return [t for t in dict.fromkeys(tiles) if not self.contains(slot, tick, *t)]

    def check_action(self, slot: int, tick: int, m: str, a: dict[str, Any], probe: Probe | None = None) -> None:
        """Raise :class:`FranchiseError` when action ``m`` of the company in ``slot`` touches a tile outside
        its regions at ``tick``."""
        if not is_build(m):
            return
        bad = self.outside(slot, tick, footprint(m, a, self.map_x, probe))
        if bad:
            rects = self.rects_at(slot, tick)
            shown = ", ".join(f"{x},{y}" for x, y in bad[:4]) + (" ..." if len(bad) > 4 else "")
            own = "; ".join(f"{r[0]},{r[1]} to {r[2]},{r[3]}" for r in rects) or "none"
            raise FranchiseError(f"{m}: tile(s) {shown} are outside your franchise region (your region: {own}). "
                                 "Build only inside your region; `rh market` and `rh info` show it.")


# ---------------------------------------------------------------------- generation
def split_regions(map_x: int, map_y: int, n: int, points: list[tuple[int, int]],
                  margin: int = 1) -> list[list[int]]:
    """Split the map into ``n`` rectangles, balanced by the number of ``points`` (industry tiles) in each.
    Recursive bisection: cut the longer side so that each part gets points in proportion to the number
    of regions it will hold. Deterministic. The void edge (``margin`` tiles) is left out."""
    if n < 1:
        raise ValueError("at least one region")

    def rec(r: list[int], k: int, pts: list[tuple[int, int]]) -> list[list[int]]:
        if k == 1:
            return [r]
        x0, y0, x1, y1 = r
        k1 = k // 2
        axis = 0 if (x1 - x0) >= (y1 - y0) else 1
        lo, hi = (x0, x1) if axis == 0 else (y0, y1)
        coords = sorted(p[axis] for p in pts)
        if coords:
            want = len(coords) * k1 // k
            cut = coords[want - 1] if want > 0 else lo
        else:
            cut = lo + (hi - lo + 1) * k1 // k - 1
        span = hi - lo + 1
        min_side = max(1, span * k1 // (4 * k))  # no sliver: each part keeps a quarter of its fair share
        cut = max(lo + min_side - 1, min(cut, hi - max(1, span * (k - k1) // (4 * k))))
        if axis == 0:
            a, b = [x0, y0, cut, y1], [cut + 1, y0, x1, y1]
        else:
            a, b = [x0, y0, x1, cut], [x0, cut + 1, x1, y1]
        pa = [p for p in pts if p[axis] <= cut]
        pb = [p for p in pts if p[axis] > cut]
        return rec(a, k1, pa) + rec(b, k - k1, pb)

    full = [margin, margin, map_x - 1 - margin, map_y - 1 - margin]
    inside = [p for p in points if full[0] <= p[0] <= full[2] and full[1] <= p[1] <= full[3]]
    return rec(full, n, sorted(inside))


def rotation(offset: int, n: int) -> list[int]:
    """Region type for each slot: slot s plays region type (s + offset) mod n. Over the n offsets
    0..n-1, each slot (and so each model of a fixed roster) plays each region type once."""
    return [(s + offset) % n for s in range(n)]


def make_region_map(map_x: int, map_y: int, n: int, industry_tiles: list[tuple[int, int]],
                    offset: int) -> RegionMap:
    """One region per company. The bisection order gives the region types 0..n-1; :func:`rotation`
    with ``offset`` assigns them to slots."""
    rects = split_regions(map_x, map_y, n, industry_tiles)
    rot = rotation(offset, n)
    return RegionMap(map_x, map_y, {s: [rects[rot[s]]] for s in range(n)}, {s: rot[s] for s in range(n)})


def region_of(rm: RegionMap, x: int, y: int, tick: int = 0) -> int:
    """The slot whose region holds tile (x, y) at ``tick``, or -1."""
    for s in sorted(rm.regions):
        if rm.contains(s, tick, x, y):
            return s
    return -1
