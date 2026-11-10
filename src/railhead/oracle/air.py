"""Scripted oracle player: a passenger air route between two big towns (for the mode economics).

Player actions and queries only, like the other oracles. Site search: flat, buildable, level
footprints near each town centre whose catchment accepts passengers, each confirmed with a
``dry_run`` before the real build (models hit hundreds of ERR_FLAT_LAND_REQUIRED there).
"""

from __future__ import annotations

from railhead.bridge import BridgeError
from railhead.oracle.terrain import Tile, TileMap
from railhead.oracle.water import OracleFailed, Route

#: (width, height, catchment radius) of the airport types the oracle uses.
AIRPORT_DIMS = {"large": (6, 6, 4), "small": (4, 3, 4), "commuter": (5, 4, 4), "metropolitan": (6, 6, 6),
                "international": (7, 7, 8)}
SITE_KINDS = ".tf^"


def flat_blocks(tm: TileMap, center: Tile, w: int, h: int, radius: int) -> list[Tile]:
    """North corners of w x h blocks within ``radius`` of center whose tiles are all buildable, flat
    and at one height. Nearest first."""
    out = []
    cx, cy = center
    for y in range(cy - radius, cy + radius + 1):
        for x in range(cx - radius, cx + radius + 1):
            cells = [tm.get((x + i, y + j)) for j in range(h) for i in range(w)]
            if any(c is None or not c.build or c.slope != "0" or c.kind not in SITE_KINDS for c in cells):
                continue
            if len({c.height for c in cells}) != 1:
                continue
            out.append((abs(x + w // 2 - cx) + abs(y + h // 2 - cy), (x, y)))
    out.sort()
    return [t for _, t in out]


class AirOracle:
    def __init__(self, world, airport: str = "large") -> None:
        self.w = world
        self.tm = TileMap.from_world(world)
        self.airport = airport
        self.route = Route(mode="air")

    def q(self, method: str, **args):
        self.route.queries += 1
        return self.w.call(method, **args)

    def act(self, method: str, **args):
        self.route.acts += 1
        ret = self.w.act(method, **args)
        if isinstance(ret, dict) and not args.get("dry_run"):
            self.route.cost += int(ret.get("cost", 0))
        return ret

    def try_act(self, method: str, **args):
        try:
            return self.act(method, **args)
        except BridgeError as e:
            return e

    def site(self, town: dict, pax: int) -> Tile | None:
        w, h, radius = AIRPORT_DIMS[self.airport]
        c = (town["x"], town["y"])
        self.tm.load_area(c[0] - 16, c[1] - 16, c[0] + 16, c[1] + 16)
        self.route.queries += 1
        for t in flat_blocks(self.tm, c, w, h, 12)[:6]:
            cat = self.q("catchment", tile=self.tm.idx(t), w=w, h=h, radius=radius)
            if not any(int(a["cargo"]) == pax and a.get("accepted") for a in cat.get("accepts", [])):
                continue
            r = self.try_act("air_airport", tile=self.tm.idx(t), type=self.airport, dry_run=True)
            if not isinstance(r, BridgeError) and r.get("affordable", True):
                return t
        return None

    def build(self, dmin: int = 150, dmax: int = 320, planes: int = 2) -> Route:
        pax = next(c["id"] for c in self.q("cargos") if c["label"] == "PASS")
        towns = sorted(self.q("towns"), key=lambda t: (-int(t["population"]), t["id"]))[:12]
        pairs = []
        for i, a in enumerate(towns):
            for b in towns[i + 1:]:
                d = abs(a["x"] - b["x"]) + abs(a["y"] - b["y"])
                if dmin <= d <= dmax:
                    pairs.append((-min(int(a["pax_produced"]), int(b["pax_produced"])), d, a["id"], b["id"], a, b))
        pairs.sort(key=lambda p: p[:4])
        fin = self.q("company")
        self.act("company_loan", amount=int(fin["max_loan"]))
        for *_, a, b in pairs[:6]:
            sa, sb = self.site(a, pax), self.site(b, pax)
            if sa is None or sb is None:
                continue
            ra = self.act("air_airport", tile=self.tm.idx(sa), type=self.airport, join_station=-1)
            rb = self.act("air_airport", tile=self.tm.idx(sb), type=self.airport, join_station=-1)
            hangar = int(ra["hangar_tile"])
            engine = self.pick_engine(pax)
            r = self.route
            r.stations, r.depot = [int(ra["station"]), int(rb["station"])], self.tm.xy(hangar)
            r.detail.update(towns=[a["id"], b["id"]], airports=[sa, sb], engine=engine,
                            distance=abs(sa[0] - sb[0]) + abs(sa[1] - sb[1]))
            for i in range(planes):
                v = int(self.act("vehicle_buy", depot_tile=hangar, engine=engine)["vehicle"])
                self.act("order_append", vehicle=v, tile=self.tm.idx(sa))
                self.act("order_append", vehicle=v, tile=self.tm.idx(sb))
                if i % 2:
                    self.act("vehicle_skip_order", vehicle=v, index=1)
                self.act("vehicle_start", vehicle=v)
                r.vehicles.append(v)
            return r
        raise OracleFailed("no two towns with airport sites in range")

    def pick_engine(self, pax: int) -> int:
        rows = [e for e in self.q("engines", vtype="air", cargo=pax) if e.get("carries")]
        if self.airport in ("small", "commuter"):
            rows = [e for e in rows if e.get("plane_type") != "big"]
        if not rows:
            raise OracleFailed("no passenger aircraft")
        return int(max(rows, key=lambda e: (int(e["capacity"]) * int(e["speed"]), -int(e["id"])))["id"])
