"""Scripted oracle player: truck service from a cargo source industry to an accepting destination.

It plays exactly like a model must: only whitelisted player actions through ``World.act`` (so its
moves land in the action log and are replay-verifiable). Its "intelligence" (A* over the tile grid,
site selection) lives here, on the player's side — the environment never offers it to models.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field

from railhead.bridge import BridgeError
from railhead.oracle.pathing import Grid, astar, segments
from railhead.world import World

Tile = tuple[int, int]

KIND_COST = {".": 1.0, "t": 1.4, "f": 1.2, "^": 1.6, "=": 0.3}  # clear, trees, fields, rough/rocks, road


class OracleFailed(RuntimeError):
    pass


@dataclass
class TileInfo:
    kind: str
    height: int
    slope: str
    build: bool
    owner: str


@dataclass
class Network:
    pickup: Tile
    drop: Tile
    depot: Tile
    path: list[Tile]
    vehicles: list[int] = field(default_factory=list)
    stations: list[int] = field(default_factory=list)


class RoadOracle:
    def __init__(self, world: World) -> None:
        self.w = world
        info = world.call("map_info")
        self.mx, self.my = int(info["size_x"]), int(info["size_y"])
        self.tiles: dict[Tile, TileInfo] = {}

    # ------------------------------------------------------------------ terrain
    def idx(self, t: Tile) -> int:
        return t[1] * self.mx + t[0]

    def load_area(self, x0: int, y0: int, x1: int, y1: int) -> None:
        x0, y0 = max(1, x0), max(1, y0)
        x1, y1 = min(self.mx - 2, x1), min(self.my - 2, y1)
        step = 64
        for by in range(y0, y1 + 1, step):
            for bx in range(x0, x1 + 1, step):
                w = min(step, x1 - bx + 1)
                h = min(step, y1 - by + 1)
                for row in self.w.call("tiles", x=bx, y=by, w=w, h=h):
                    y = int(row["y"])
                    for i in range(w):
                        slope = row["slope"][i] if "slope" in row else "0"
                        build = row["build"][i] == "1" if "build" in row else row["kind"][i] in ".tf^"
                        self.tiles[(bx + i, y)] = TileInfo(
                            kind=row["kind"][i], height=int(row["height"][i], 36), slope=slope,
                            build=build, owner=row["owner"][i])

    def cost(self, x: int, y: int) -> float | None:
        t = self.tiles.get((x, y))
        if t is None or t.slope == "s":
            return None
        if t.kind == "=":
            return KIND_COST["="]
        if t.kind not in KIND_COST or not t.build:
            return None
        return KIND_COST[t.kind] + (1.0 if t.slope != "0" else 0.0)

    def site_ok(self, t: Tile) -> bool:
        ti = self.tiles.get(t)
        return bool(ti and ti.build and ti.slope == "0" and ti.kind in (".", "t", "f"))

    def industry_tiles(self, loc: Tile, ind_id: int) -> list[Tile]:
        out = []
        for dy in range(-1, 7):
            for dx in range(-1, 7):
                t = (loc[0] + dx, loc[1] + dy)
                if self.tiles.get(t) and self.tiles[t].kind == "I":
                    out.append(t)
        return out or [loc]

    def candidates(self, near: list[Tile], radius: int = 3) -> list[Tile]:
        seen: set[Tile] = set()
        for (ix, iy) in near:
            for dy in range(-radius, radius + 1):
                for dx in range(-radius, radius + 1):
                    t = (ix + dx, iy + dy)
                    if t not in seen and self.site_ok(t):
                        seen.add(t)
        return sorted(seen)

    # ------------------------------------------------------------------ play
    def act(self, method: str, **args):
        return self.w.act(method, **args)

    def try_act(self, method: str, **args):
        try:
            return self.act(method, **args)
        except BridgeError as e:
            return e

    def deliver(self, source: dict, dest: dict, cargo: int, max_trucks: int = 10) -> Network:
        src_loc, dst_loc = (source["x"], source["y"]), (dest["x"], dest["y"])
        pad = 10
        self.load_area(min(src_loc[0], dst_loc[0]) - pad, min(src_loc[1], dst_loc[1]) - pad,
                       max(src_loc[0], dst_loc[0]) + pad, max(src_loc[1], dst_loc[1]) + pad)
        src_tiles = self.industry_tiles(src_loc, source["id"])
        dst_tiles = self.industry_tiles(dst_loc, dest["id"])
        picks = self.candidates(src_tiles)
        drops = [t for t in self.candidates(dst_tiles) if self.accepts(t, cargo)]
        if not picks or not drops:
            raise OracleFailed("no buildable stop sites in catchment")
        pairs = sorted(((p, d) for p in picks[:12] for d in drops[:12]),
                       key=lambda pd: abs(pd[0][0] - pd[1][0]) + abs(pd[0][1] - pd[1][1]))
        self.act("company_loan", amount=self.max_loan())
        grid = Grid(self.mx, self.my, self.cost)
        for pick, drop in pairs[:6]:
            for _attempt in range(6):
                path = astar(grid, pick, drop)
                if path is None or len(path) < 4:
                    break
                bad = self.build_path(path)
                if bad is None:
                    try:
                        return self.finish(path, cargo, source, max_trucks)
                    except (BridgeError, OracleFailed):
                        # stop/depot site refused (e.g. slope): never use these endpoints again
                        for t in (pick, drop):
                            self.tiles[t].build = False
                        break
                for t in bad:  # replan around tiles the engine refused
                    if t in self.tiles:
                        self.tiles[t].build = False
                        if self.tiles[t].kind == "=":
                            self.tiles[t].kind = "#"
        raise OracleFailed("no buildable road route found")

    def accepts(self, t: Tile, cargo: int) -> bool:
        try:
            info = self.w.call("tile", tile=self.idx(t), radius=3)
        except BridgeError:
            return False
        for c in info.get("accepts", []):
            if int(c.get("cargo", -1)) == cargo and (c.get("accepted") or int(c.get("amount", 0)) >= 8):
                return True
        return False

    def max_loan(self) -> int:
        fin = self.w.call("company")
        return int(fin["max_loan"])

    def build_path(self, path: list[Tile]) -> list[Tile] | None:
        """Build the road between the two stop tiles. Returns tiles to avoid if a drag failed."""
        inner = path[1:-1]
        for a, b in segments(inner) if len(inner) > 1 else []:
            r = self.try_act("road_line", from_tile=self.idx(a), to_tile=self.idx(b))
            if isinstance(r, BridgeError) and r.code not in ("ERR_ALREADY_BUILT",):
                line = [a] if a == b else [(x, y) for x in range(min(a[0], b[0]), max(a[0], b[0]) + 1)
                                            for y in range(min(a[1], b[1]), max(a[1], b[1]) + 1)]
                return [t for t in line if self.tiles.get(t) and self.tiles[t].kind != "="] or line
        return None

    def finish(self, path: list[Tile], cargo: int, source: dict, max_trucks: int) -> Network:
        pick, drop = path[0], path[-1]
        st_a = self.act("road_stop", tile=self.idx(pick), front_tile=self.idx(path[1]), kind="truck")
        self.try_act("road_line", from_tile=self.idx(path[1]), to_tile=self.idx(pick))
        st_b = self.act("road_stop", tile=self.idx(drop), front_tile=self.idx(path[-2]), kind="truck")
        self.try_act("road_line", from_tile=self.idx(path[-2]), to_tile=self.idx(drop))
        depot = self.place_depot(path)
        engine, capacity = self.pick_engine(cargo)
        production = sum(int(p.get("last_month", 0)) for p in source.get("produces", []) if p["cargo"] == cargo)
        # enough trucks to carry about one month of output per round trip window, never a loss-making swarm
        n = max(2, min(max_trucks, math.ceil(production / max(capacity, 1) * len(path) / 25)))
        net = Network(pickup=pick, drop=drop, depot=depot, path=path,
                      stations=[st_a.get("station", -1), st_b.get("station", -1)])
        for _ in range(n):
            r = self.try_act("vehicle_buy", depot_tile=self.idx(depot), engine=engine, cargo=cargo)
            if isinstance(r, BridgeError):
                break
            v = int(r["vehicle"])
            self.act("order_append", vehicle=v, tile=self.idx(pick), flags=["full_load_any"])
            self.act("order_append", vehicle=v, tile=self.idx(drop), flags=["unload", "no_load"])
            self.act("vehicle_start", vehicle=v)
            net.vehicles.append(v)
        if not net.vehicles:
            raise OracleFailed("could not buy any truck")
        return net

    def place_depot(self, path: list[Tile]) -> Tile:
        for i in range(1, len(path) - 1):
            px, py = path[i]
            for d in ((0, 1), (0, -1), (1, 0), (-1, 0)):
                t = (px + d[0], py + d[1])
                if t in path or not self.site_ok(t):
                    continue
                r = self.try_act("road_depot", tile=self.idx(t), front_tile=self.idx(path[i]))
                if not isinstance(r, BridgeError):
                    self.try_act("road_line", from_tile=self.idx(path[i]), to_tile=self.idx(t))
                    return t
        raise OracleFailed("no depot site along the route")

    def pick_engine(self, cargo: int) -> tuple[int, int]:
        engines = self.w.call("engines", vtype="road")
        best = None
        for e in engines:
            refits = e.get("refits") or []
            if int(e.get("cargo", -1)) == cargo or cargo in refits:
                score = int(e.get("capacity", 0)) * int(e.get("speed", e.get("max_speed", 1)))
                if best is None or score > best[0]:
                    best = (score, int(e["id"]), int(e.get("capacity", 10)))
        if best is None:
            raise OracleFailed(f"no road vehicle carries cargo {cargo}")
        return best[1], best[2]
