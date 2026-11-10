"""Scripted oracle player: one train route (a cargo source industry to an accepting industry, or two
towns for passengers) on a single track between two terminus stations.

It plays only whitelisted player actions through ``World.act`` and reads only player queries, like
``oracle/road.py``. Its planning (station sites from the ``tiles`` layers and the ``catchment``
query, an A* over (tile, heading) states that knows rail geometry, the track piece for every tile)
lives here, on the player's side; the environment never offers it to models.

Layout built::

    [depot]=[station A]=====line=====[station B]      (one train, no signals needed)

The line leaves each station along the platform axis. Turns are single corner pieces on flat tiles,
at least ``MIN_RUN`` straight tiles apart (OpenTTD forbids 90-degree turns by default).
"""

from __future__ import annotations

import heapq
import math
from dataclasses import dataclass

from railhead.bridge import BridgeError
from railhead.oracle.terrain import Tile, TileMap
from railhead.oracle.water import OracleFailed, Route

Dir = tuple[int, int]
MIN_RUN = 2
KIND_COST = {".": 1.0, "t": 1.3, "f": 1.2, "^": 1.5}
#: tile side names; a track piece connects two sides. -x = NE, +x = SW, -y = NW, +y = SE.
SIDE = {(-1, 0): "ne", (1, 0): "sw", (0, -1): "nw", (0, 1): "se"}
CORNERS = {frozenset(("nw", "ne")): "nw_ne", frozenset(("sw", "se")): "sw_se",
           frozenset(("nw", "sw")): "nw_sw", frozenset(("ne", "se")): "ne_se"}


def track_piece(d_in: Dir, d_out: Dir) -> str:
    """The ``rail.track`` piece for a tile entered moving ``d_in`` and left moving ``d_out``."""
    if d_in == d_out:
        return "ne_sw" if d_in[1] == 0 else "nw_se"
    if d_in == (-d_out[0], -d_out[1]):
        raise ValueError("a track cannot reverse within a tile")
    return CORNERS[frozenset((SIDE[(-d_in[0], -d_in[1])], SIDE[d_out]))]


def rail_astar(cost, start: Tile, d0: Dir, goal: Tile, d_goal: Dir, turn_ok,
               min_run: int = MIN_RUN, turn_penalty: float = 2.0, max_nodes: int = 400_000) -> list[Tile] | None:
    """Cheapest rail path from ``start`` (entered moving ``d0``) to ``goal`` (left moving ``d_goal``).

    ``cost(t)`` is the tile's straight-track cost or None (impassable); ``turn_ok(t)`` says whether a
    corner piece may go on t. A state is (tile, heading, straight tiles since the last turn); a turn is
    allowed only after ``min_run`` straight tiles. Deterministic tie-breaking.
    """
    if cost(start) is None or cost(goal) is None:
        return None

    def h(t: Tile) -> float:
        return abs(t[0] - goal[0]) + abs(t[1] - goal[1])

    s0 = (start, d0, min_run)
    best = {s0: cost(start)}
    parent: dict = {s0: None}
    heap = [(best[s0] + h(start), best[s0], 0, s0)]
    n = 0
    while heap:
        _, g, _, s = heapq.heappop(heap)
        tile, d, run = s
        if g > best.get(s, math.inf):
            continue
        if tile == goal and d == d_goal:
            out = []
            while s is not None:
                out.append(s[0])
                s = parent[s]
            return out[::-1]
        n += 1
        if n > max_nodes:
            return None
        moves = [(d, min(run + 1, min_run))]
        if run >= min_run and turn_ok(tile):
            moves += [((d[1], d[0]), 0), ((-d[1], -d[0]), 0)]
        for nd, nrun in moves:
            nt = (tile[0] + nd[0], tile[1] + nd[1])
            c = cost(nt)
            if c is None:
                continue
            ng = g + c + (turn_penalty if nd != d else 0.0)
            ns = (nt, nd, nrun)
            if ng < best.get(ns, math.inf):
                best[ns] = ng
                parent[ns] = s
                n_push = len(parent)
                heapq.heappush(heap, (ng + h(nt), ng, n_push, ns))
    return None


def path_pieces(path: list[Tile], d0: Dir) -> list[tuple[Tile, str]]:
    """(tile, piece) for every tile of a path whose first tile is entered moving ``d0``; the last tile
    keeps its heading."""
    out = []
    d_in = d0
    for i, t in enumerate(path):
        d_out = (path[i + 1][0] - t[0], path[i + 1][1] - t[1]) if i + 1 < len(path) else d_in
        out.append((t, track_piece(d_in, d_out)))
        d_in = d_out
    return out


def straight_runs(pieces: list[tuple[Tile, str]]) -> list[tuple[Tile, Tile]]:
    """Maximal runs of identical straight pieces (each one ``rail.line`` drag)."""
    runs: list[tuple[Tile, Tile]] = []
    start = prev = None
    kind = None
    for t, p in pieces:
        straight = p in ("ne_sw", "nw_se")
        if straight and p == kind and prev is not None and abs(t[0] - prev[0]) + abs(t[1] - prev[1]) == 1:
            prev = t
            continue
        if start is not None:
            runs.append((start, prev))
        start, prev, kind = (t, t, p) if straight else (None, None, None)
    if start is not None:
        runs.append((start, prev))
    return runs


@dataclass
class StationSite:
    tile: Tile        # north corner
    axis: str         # "x" or "y"
    length: int

    def tiles(self) -> list[Tile]:
        dx, dy = (1, 0) if self.axis == "x" else (0, 1)
        return [(self.tile[0] + i * dx, self.tile[1] + i * dy) for i in range(self.length)]

    def ends(self) -> list[tuple[Tile, Dir]]:
        """(platform end tile, outward heading) for both ends."""
        ts = self.tiles()
        d = (1, 0) if self.axis == "x" else (0, 1)
        return [(ts[-1], d), (ts[0], (-d[0], -d[1]))]


class RailOracle:
    def __init__(self, world, length: int = 3) -> None:
        self.w = world
        self.tm = TileMap.from_world(world)
        self.length = length
        self.route = Route(mode="rail")

    # ------------------------------------------------------------------ io
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

    # ------------------------------------------------------------------ terrain
    def flat_free(self, t: Tile) -> bool:
        ti = self.tm.get(t)
        return bool(ti and ti.build and ti.slope == "0" and ti.kind in KIND_COST and t not in self.reserved)

    def cost(self, t: Tile) -> float | None:
        if t in self.blocked:
            return None
        ti = self.tm.get(t)
        if ti is None or ti.slope == "s" or not ti.build or ti.kind not in KIND_COST:
            return None
        return KIND_COST[ti.kind] + (2.0 if ti.slope != "0" else 0.0)

    def turn_ok(self, t: Tile) -> bool:
        ti = self.tm.get(t)
        return bool(ti and ti.slope == "0")

    def station_sites(self, near: list[Tile], cargo: int, role: str, radius: int = 4) -> list[StationSite]:
        """Flat free station footprints whose catchment touches ``near`` (industry/town tiles), checked
        with the ``catchment`` query (role: produces | accepts). Nearest first, at most 6 checked."""
        cands = []
        for (ix, iy) in near:
            for dy in range(-radius - self.length, radius + 2):
                for dx in range(-radius - self.length, radius + 2):
                    for axis in ("x", "y"):
                        s = StationSite((ix + dx, iy + dy), axis, self.length)
                        ts = s.tiles()
                        # the footprint plus one tile beyond each end (line exit / depot) must be free
                        (e1, d1), (e2, d2) = s.ends()
                        if all(self.flat_free(t) for t in ts + [(e1[0] + d1[0], e1[1] + d1[1]), (e2[0] + d2[0], e2[1] + d2[1])]):
                            d = min(abs(t[0] - x) + abs(t[1] - y) for t in ts for (x, y) in near)
                            cands.append((d, s.tile, axis, s))
        cands.sort(key=lambda c: (c[0], c[1], c[2]))
        out, seen = [], set()
        for _, _, _, s in cands:
            if (s.tile, s.axis) in seen:
                continue
            seen.add((s.tile, s.axis))
            w, h = (self.length, 1) if s.axis == "x" else (1, self.length)
            c = self.q("catchment", tile=self.tm.idx(s.tile), w=w, h=h, radius=radius)
            ok = any(int(p["cargo"]) == cargo for p in c.get("produces", [])) if role == "produces" else \
                any(int(a["cargo"]) == cargo and a.get("accepted") for a in c.get("accepts", []))
            if ok:
                out.append(s)
            if len(out) >= 3 or len(seen) >= 12:
                break
        return out

    # ------------------------------------------------------------------ choose
    def industry_tiles(self, ind: dict) -> list[Tile]:
        x, y = ind["x"], ind["y"]
        ts = [(x + dx, y + dy) for dy in range(-1, 6) for dx in range(-1, 6)
              if (ti := self.tm.get((x + dx, y + dy))) and ti.kind == "I"]
        return ts or [(x, y)]

    def pick_cargo_pair(self, label: str, dmin: int, dmax: int) -> list[tuple[dict, dict, int]]:
        cargo = next(c["id"] for c in self.q("cargos") if c["label"] == label)
        srcs = self.q("industries", cargo=cargo, role="produces")
        dsts = self.q("industries", cargo=cargo, role="accepts")
        pairs = []
        for s in srcs:
            prod = sum(int(p["last_month"]) for p in s["produces"] if int(p["cargo"]) == cargo)
            for d in dsts:
                dist = abs(s["x"] - d["x"]) + abs(s["y"] - d["y"])
                if dmin <= dist <= dmax and prod > 0:
                    pairs.append((-prod * min(dist, 80), dist, s["id"], d["id"], s, d))
        pairs.sort(key=lambda p: p[:4])
        return [(p[4], p[5], cargo) for p in pairs]

    # ------------------------------------------------------------------ build
    def build(self, cargo_label: str = "COAL", dmin: int = 20, dmax: int = 60, wagons: int = 4) -> Route:
        self.blocked: set[Tile] = set()
        self.reserved: set[Tile] = set()
        tries = []
        for src, dst, cargo in self.pick_cargo_pair(cargo_label, dmin, dmax)[:5]:
            pad = 12
            self.tm.load_area(min(src["x"], dst["x"]) - pad, min(src["y"], dst["y"]) - pad,
                              max(src["x"], dst["x"]) + pad, max(src["y"], dst["y"]) + pad)
            a_sites = self.station_sites(self.industry_tiles(src), cargo, "produces")
            b_sites = self.station_sites(self.industry_tiles(dst), cargo, "accepts")
            for a in a_sites[:2]:
                for b in b_sites[:2]:
                    try:
                        return self._build(a, b, cargo, wagons, src, dst)
                    except (BridgeError, OracleFailed) as e:
                        tries.append(f"{src['id']}->{dst['id']}: {e}")
        raise OracleFailed(f"no buildable train route; tried: {tries[:6]}")

    def _plan(self, a: StationSite, b: StationSite):
        best = None
        self.reserved = set(a.tiles()) | set(b.tiles())
        for ea, da in a.ends():
            # the tile beyond A's other end is kept for the depot
            far = next((e[0] + d[0], e[1] + d[1]) for e, d in a.ends() if e != ea)
            for eb, db in b.ends():
                start = (ea[0] + da[0], ea[1] + da[1])
                goal = (eb[0] + db[0], eb[1] + db[1])
                saved = self.reserved
                self.reserved = set()
                path = rail_astar(self.cost_reserved(saved | {far}), start, da, goal, (-db[0], -db[1]), self.turn_ok)
                self.reserved = saved
                if path and (best is None or len(path) < len(best[0])):
                    best = (path, ea, da, eb, db)
        return best

    def cost_reserved(self, reserved: set[Tile]):
        def c(t: Tile):
            return None if t in reserved else self.cost(t)
        return c

    def _build(self, a: StationSite, b: StationSite, cargo: int, wagons: int, src: dict, dst: dict) -> Route:
        for _attempt in range(6):
            plan = self._plan(a, b)
            if plan is None:
                raise OracleFailed("no rail path between the station sites")
            path, ea, da, _eb, _db = plan
            bad = self._lay_track(path, da)
            if bad is None:
                break
            self.blocked |= bad
        else:
            raise OracleFailed("track kept failing")
        fin = self.q("company")
        if int(fin["loan"]) < int(fin["max_loan"]):
            self.act("company_loan", amount=int(fin["max_loan"]))
        st_a = self.act("rail_station", tile=self.tm.idx(a.tile), axis=a.axis, platforms=1, length=a.length, join_station=-1)
        st_b = self.act("rail_station", tile=self.tm.idx(b.tile), axis=b.axis, platforms=1, length=b.length, join_station=-1)
        # depot behind station A's far end, facing into the platform
        far = next((e, d) for e, d in a.ends() if e != ea)
        depot = (far[0][0] + far[1][0], far[0][1] + far[1][1])
        self.act("rail_depot", tile=self.tm.idx(depot), front_tile=self.tm.idx(far[0]))
        r = self.route
        r.stations, r.depot = [int(st_a["station"]), int(st_b["station"])], depot
        r.detail.update(source=src["id"], dest=dst["id"], cargo=cargo, track_tiles=len(path),
                        stations_at=[a.tile, b.tile])
        self._buy_train(depot, cargo, wagons, a, b)
        return r

    def _lay_track(self, path: list[Tile], d0: Dir) -> set[Tile] | None:
        """Build the line; on a refusal return the tiles to avoid next time."""
        pieces = path_pieces(path, d0)
        for t, p in pieces:
            if p not in ("ne_sw", "nw_se"):
                r = self.try_act("rail_track", tile=self.tm.idx(t), track=p)
                if isinstance(r, BridgeError) and r.code != "ERR_ALREADY_BUILT":
                    return {t}
        for s, e in straight_runs(pieces):
            if s == e:
                p = next(pp for tt, pp in pieces if tt == s)
                r = self.try_act("rail_track", tile=self.tm.idx(s), track=p)
            else:
                r = self.try_act("rail_line", from_tile=self.tm.idx(s), to_tile=self.tm.idx(e))
            if isinstance(r, BridgeError) and r.code != "ERR_ALREADY_BUILT":
                return self._probe_run(s, e, pieces)
        return None

    def _probe_run(self, s: Tile, e: Tile, pieces) -> set[Tile]:
        """Find which tiles of a failed drag refuse a single piece (dry runs)."""
        bad = set()
        piece = dict(pieces)
        dx, dy = (e[0] > s[0]) - (e[0] < s[0]), (e[1] > s[1]) - (e[1] < s[1])
        t = s
        while True:
            r = self.try_act("rail_track", tile=self.tm.idx(t), track=piece[t], dry_run=True)
            if isinstance(r, BridgeError) and r.code != "ERR_ALREADY_BUILT":
                bad.add(t)
            if t == e:
                break
            t = (t[0] + dx, t[1] + dy)
        return bad or {s, e}

    def pick_engines(self, cargo: int) -> tuple[int, int | None, int]:
        """(locomotive, wagon or None if the engine itself carries the cargo, wagon capacity)."""
        rows = self.q("engines", vtype="rail", cargo=cargo)
        locos = [e for e in rows if not e.get("wagon") and int(e.get("power", 0)) > 0 and int(e.get("rail_type", 0)) == 0]
        wagons = [e for e in rows if e.get("wagon") and e.get("carries") and int(e.get("rail_type", 0)) == 0]
        if not locos:
            raise OracleFailed("no locomotive available")
        if not wagons:
            self_carry = [e for e in locos if e.get("carries")]
            if not self_carry:
                raise OracleFailed(f"no wagon carries cargo {cargo}")
            e = max(self_carry, key=lambda e: (int(e["capacity"]) * int(e["speed"]), -int(e["id"])))
            return int(e["id"]), None, int(e["capacity"])
        loco = max(locos, key=lambda e: (min(int(e["power"]), 1200) * int(e["speed"]) / max(int(e["price"]), 1),
                                         -int(e["id"])))
        wagon = max(wagons, key=lambda e: (int(e["capacity"]), -int(e["id"])))
        return int(loco["id"]), int(wagon["id"]), int(wagon["capacity"])

    def _buy_train(self, depot: Tile, cargo: int, n_wagons: int, a: StationSite, b: StationSite) -> None:
        loco, wagon, _cap = self.pick_engines(cargo)
        dt = self.tm.idx(depot)
        v = int(self.act("vehicle_buy", depot_tile=dt, engine=loco)["vehicle"])
        if wagon is not None:
            for _ in range(n_wagons):
                wv = int(self.act("vehicle_buy", depot_tile=dt, engine=wagon, cargo=cargo)["vehicle"])
                self.act("vehicle_attach", vehicle=wv, train_vehicle=v)
        self.act("order_append", vehicle=v, tile=self.tm.idx(a.tile), flags=["full_load_any"])
        self.act("order_append", vehicle=v, tile=self.tm.idx(b.tile), flags=["unload", "no_load"])
        self.act("vehicle_start", vehicle=v)
        self.route.vehicles.append(v)
        self.route.detail.update(engine=loco, wagon=wagon, wagons=n_wagons if wagon is not None else 0)
