"""Scripted oracle player: a passenger ferry route between two towns on the same water.

Like ``oracle/road.py`` it plays only whitelisted player actions through ``World.act`` (logged and
replay-verifiable) and reads only player queries. Its planning (dock sites from the ``dock`` map
layer, water connectivity by flood fill, depot placement) lives here, on the player's side; the
environment never offers it to models. Environment-internal: task certification, tests, economics.

    route = WaterOracle(world).build()          # picks the pair itself
    route = WaterOracle(world).build(town_a=99, town_b=135)
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from railhead.bridge import BridgeError
from railhead.oracle.terrain import N4, Tile, TileMap


class OracleFailed(RuntimeError):
    pass


@dataclass
class Site:
    """A dock site: the land tile and the water tile where ships stop."""
    town: int
    tile: Tile
    water: Tile


@dataclass
class Route:
    mode: str
    stations: list[int] = field(default_factory=list)
    depot: Tile | None = None
    vehicles: list[int] = field(default_factory=list)
    cost: int = 0          # money spent on actions (construction + vehicles)
    acts: int = 0          # player actions issued (failed ones included)
    queries: int = 0       # player queries read
    detail: dict[str, Any] = field(default_factory=dict)


# ---------------------------------------------------------------------------------------- planning
def dock_sites_near(tm: TileMap, center: Tile, radius: int) -> list[Tile]:
    """Tiles with the ``dock`` layer set within ``radius`` (Chebyshev) of center, nearest first."""
    out = [t for t, ti in tm.tiles.items()
           if ti.dock and max(abs(t[0] - center[0]), abs(t[1] - center[1])) <= radius]
    return sorted(out, key=lambda t: (abs(t[0] - center[0]) + abs(t[1] - center[1]), t))


def dock_water_tile(tm: TileMap, site: Tile) -> Tile | None:
    """The water tile in front of a dock site: the neighbour with water two tiles deep."""
    for dx, dy in N4:
        a, b = (site[0] + dx, site[1] + dy), (site[0] + 2 * dx, site[1] + 2 * dy)
        if tm.is_water(a) and tm.is_water(b):
            return a
    return None


def depot_sites(tm: TileMap, water: Tile, component: set[Tile], avoid: set[Tile], max_dist: int = 8) -> list[tuple[Tile, Tile]]:
    """(tile, toward_tile) pairs for a ship depot near ``water``: tile and tile+axis both open water in
    the component, with open water before and after along the axis (ships enter from either end),
    clear of ``avoid`` and its neighbours. Nearest first."""
    out = []
    banned = set(avoid) | {(a[0] + dx, a[1] + dy) for a in avoid for dx, dy in N4}
    for (x, y) in component:
        d = abs(x - water[0]) + abs(y - water[1])
        if d < 2 or d > max_dist:
            continue
        for ax in ((1, 0), (0, 1)):
            t, t2 = (x, y), (x + ax[0], y + ax[1])
            before, after = (x - ax[0], y - ax[1]), (x + 2 * ax[0], y + 2 * ax[1])
            if not all(p in component for p in (t2, before, after)):
                continue
            if t in banned or t2 in banned:
                continue
            # neither side of the depot may be land: ships must be able to pass it
            side = (ax[1], ax[0])
            if not all((p[0] + s * side[0], p[1] + s * side[1]) in component for p in (t, t2) for s in (1, -1)):
                continue
            out.append((d, t, t2))
    out.sort()
    return [(t, t2) for _, t, t2 in out]


def pick_pair(cands: dict[int, list[Site]], towns: dict[int, dict], comp_of: dict[Tile, int],
              dmin: int, dmax: int) -> list[tuple[Site, Site]]:
    """Site pairs on the same water body and within [dmin, dmax] tiles, best first: the smaller of
    the two towns' passenger output, a little favouring longer trips (fares grow with distance)."""
    pairs = []
    ids = sorted(cands)
    for i, a in enumerate(ids):
        for b in ids[i + 1:]:
            for sa in cands[a][:2]:
                for sb in cands[b][:2]:
                    if comp_of.get(sa.water) is None or comp_of.get(sa.water) != comp_of.get(sb.water):
                        continue
                    d = abs(sa.water[0] - sb.water[0]) + abs(sa.water[1] - sb.water[1])
                    if not dmin <= d <= dmax:
                        continue
                    pax = min(int(towns[a].get("pax_produced", 0)), int(towns[b].get("pax_produced", 0)))
                    pairs.append((-(pax * (1 + d / 100)), d, a, b, sa, sb))
    pairs.sort(key=lambda p: (p[0], p[1], p[2], p[3]))
    return [(p[4], p[5]) for p in pairs]


# ---------------------------------------------------------------------------------------- player
class WaterOracle:
    def __init__(self, world, min_pop: int = 600, site_radius: int = 7) -> None:
        self.w = world
        self.tm = TileMap.from_world(world)
        self.min_pop, self.site_radius = min_pop, site_radius
        self.route = Route(mode="water")

    def q(self, method: str, **args):
        self.route.queries += 1
        return self.w.call(method, **args)

    def act(self, method: str, **args):
        self.route.acts += 1
        ret = self.w.act(method, **args)
        if isinstance(ret, dict):
            self.route.cost += int(ret.get("cost", 0))
        return ret

    def try_act(self, method: str, **args):
        try:
            return self.act(method, **args)
        except BridgeError as e:
            return e

    def _verify_site(self, town: int, t: Tile) -> Site | None:
        info = self.q("tile", tile=self.tm.idx(t), radius=5)
        dock = info.get("dock") or {}
        if not dock.get("ok"):
            return None
        pax = any(int(c["cargo"]) == self.pax and c.get("accepted") for c in info.get("accepts", []))
        if not pax:
            return None
        return Site(town=town, tile=t, water=self.tm.xy(int(dock["water_tile"])))

    def build(self, town_a: int | None = None, town_b: int | None = None, ships: int = 2,
              dmin: int = 25, dmax: int = 140) -> Route:
        cargos = self.q("cargos")
        self.pax = next(c["id"] for c in cargos if c["label"] == "PASS")
        towns = {t["id"]: t for t in self.q("towns")}
        want = [t for t in (town_a, town_b) if t is not None]
        pool = [towns[i] for i in want] if len(want) == 2 else \
            [t for t in towns.values() if int(t["population"]) >= self.min_pop]
        if len(pool) < 2:
            raise OracleFailed("fewer than two candidate towns")
        xs, ys = [t["x"] for t in pool], [t["y"] for t in pool]
        pad = 48
        self.tm.load_area(min(xs) - pad, min(ys) - pad, max(xs) + pad, max(ys) + pad)
        self.route.queries += 1 + ((max(xs) - min(xs) + 2 * pad) // 64 + 1) * ((max(ys) - min(ys) + 2 * pad) // 64 + 1)

        cands: dict[int, list[Site]] = {}
        for t in pool:
            sites = []
            for s in dock_sites_near(self.tm, (t["x"], t["y"]), self.site_radius)[:4]:
                v = self._verify_site(t["id"], s)
                if v:
                    sites.append(v)
            if sites:
                cands[t["id"]] = sites
        comp_of: dict[Tile, int] = {}
        components: list[set[Tile]] = []
        for sites in cands.values():
            for s in sites:
                if s.water not in comp_of:
                    comp = self.tm.water_component(s.water)
                    components.append(comp)
                    for c in comp:
                        comp_of[c] = len(components) - 1
        pairs = pick_pair(cands, towns, comp_of, dmin, dmax)
        if not pairs:
            raise OracleFailed("no two towns with dock sites on the same water within range")
        for sa, sb in pairs[:6]:
            try:
                return self._build_pair(sa, sb, components[comp_of[sa.water]], ships)
            except (BridgeError, OracleFailed) as e:
                self.route.detail.setdefault("rejected", []).append([sa.town, sb.town, str(e)])
        raise OracleFailed(f"no buildable ferry route: {self.route.detail.get('rejected')}")

    def _dock(self, site: Site) -> dict:
        """Build a dock once (a retried pair reuses the docks already built)."""
        built = self.route.detail.setdefault("_built", {})
        if site.tile not in built:
            built[site.tile] = self.act("water_dock", tile=self.tm.idx(site.tile), join_station=-1)
        return built[site.tile]

    def _build_pair(self, sa: Site, sb: Site, comp: set[Tile], ships: int) -> Route:
        fin = self.q("company")
        if int(fin["loan"]) < int(fin["max_loan"]):
            self.act("company_loan", amount=int(fin["max_loan"]))
        # the depot site decides the pair, so find it before building anything
        cands = [s for s in (depot_sites(self.tm, sa.water, comp, {sa.water, sb.water}, 10)[:4] +
                             depot_sites(self.tm, sb.water, comp, {sa.water, sb.water}, 10)[:4])]
        if not cands:
            raise OracleFailed("no ship depot site near either dock")
        st_a, st_b = self._dock(sa), self._dock(sb)
        depot = None
        for t, t2 in cands:
            r = self.try_act("water_ship_depot", tile=self.tm.idx(t), toward_tile=self.tm.idx(t2))
            if not isinstance(r, BridgeError):
                depot = t
                break
        if depot is None:
            raise OracleFailed("no ship depot site was accepted")
        engine = self.pick_engine()
        r = self.route
        r.stations, r.depot = [int(st_a["station"]), int(st_b["station"])], depot
        r.detail.pop("_built", None)
        r.detail.update(towns=[sa.town, sb.town], docks=[sa.tile, sb.tile], water=[sa.water, sb.water],
                        engine=engine, distance=abs(sa.water[0] - sb.water[0]) + abs(sa.water[1] - sb.water[1]))
        for i in range(ships):
            v = int(self.act("vehicle_buy", depot_tile=self.tm.idx(depot), engine=engine, cargo=self.pax)["vehicle"])
            self.act("order_append", vehicle=v, tile=self.tm.idx(sa.tile))
            self.act("order_append", vehicle=v, tile=self.tm.idx(sb.tile))
            if i % 2:
                self.act("vehicle_skip_order", vehicle=v, index=1)
            self.act("vehicle_start", vehicle=v)
            r.vehicles.append(v)
        return r

    def pick_engine(self) -> int:
        best = None
        for e in self.q("engines", vtype="water", cargo=self.pax):
            if not e.get("carries"):
                continue
            score = (int(e.get("capacity", 0)) * int(e.get("speed", 0)), -int(e["id"]))
            if best is None or score > best[0]:
                best = (score, int(e["id"]))
        if best is None:
            raise OracleFailed("no ship carries passengers")
        return best[1]
