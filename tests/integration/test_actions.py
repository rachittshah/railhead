"""Player actions issued for the bound company through the GS (docs/DESIGN.md §3).

Every family: a success path, a typed failure path and a dry run that prices without changing money.
All calls go through railhead.actions.to_gs (the same validation the CLI/SDK/log use), so tiles are
written "x,y" here. The fixture map is 128x128, seed 3 (deterministic).
"""

import dataclasses
from itertools import pairwise

import pytest

from railhead.actions import to_gs
from railhead.bridge import BridgeError
from railhead.server import ServerConfig
from railhead.world import World

pytestmark = pytest.mark.integration

W = 128  # fixture map width


def at(x: int, y: int) -> str:
    return f"{x},{y}"


def idx(x: int, y: int) -> int:
    return y * W + x


def act(world, action: str, **args):
    m, a = to_gs(action, args, map_size_x=W)
    return world.call(m, **a)


def fails(world, action: str, code: str, **args) -> BridgeError:
    with pytest.raises(BridgeError) as ei:
        act(world, action, **args)
    assert ei.value.code == code, f"{action}: expected {code}, got {ei.value.code}: {ei.value.message}"
    assert ei.value.message, "every error carries a hint"
    return ei.value


def money(world) -> int:
    return next(c["money"] for c in world.call("companies") if c["bound"])


def dry(world, action: str, **args) -> int:
    """dry_run prices the action and changes nothing (money is the cheapest global witness)."""
    before = money(world)
    out = act(world, action, dry_run=True, **args)
    assert out["dry_run"] is True and isinstance(out["cost"], int) and isinstance(out["affordable"], bool)
    assert money(world) == before
    return out["cost"]


BUILDABLE = set(".t^f")  # `tiles` kinds a building can go on (trees/rough/farmland are cleared)


def tile_info(world, x: int, y: int) -> dict:
    return world.call("tile", tile=idx(x, y), radius=0)


def map_rows(world) -> tuple[list[str], list[str]]:
    """Per-row kind and height strings of the whole map (query `tiles`, in strips of <= 4096 tiles)."""
    sy = world.call("map_info")["size_y"]
    kind, height = [""] * sy, [""] * sy
    for y0 in range(0, sy, 4096 // W):
        for r in world.call("tiles", x=0, y=y0, w=W, h=min(4096 // W, sy - y0)):
            kind[r["y"]], height[r["y"]] = r["kind"], r["height"]
    return kind, height


def site(world, w: int, h: int) -> tuple[int, int]:
    """First (row-major) w x h rectangle of buildable (clear/trees/rough/farm), flat, level land — queries only."""
    kind, height = map_rows(world)
    flat: dict[tuple[int, int], bool] = {}

    def is_flat(x: int, y: int) -> bool:
        if (x, y) not in flat:
            flat[(x, y)] = tile_info(world, x, y)["slope"] == 0
        return flat[(x, y)]

    for y in range(2, len(kind) - h - 2):
        for x in range(2, W - w - 2):
            z = height[y][x]
            if all(set(kind[y + dy][x:x + w]) <= BUILDABLE and height[y + dy][x:x + w] == z * w for dy in range(h)) and \
                    all(is_flat(x + dx, y + dy) for dy in range(h) for dx in range(w)):
                return x, y
    raise AssertionError(f"no flat {w}x{h} site on the fixture map")


def probe(world, action: str, key: str, kinds: str, max_cost: int | None = None, **args) -> tuple[int, int]:
    """First tile of the given map kinds where a dry run of `action` succeeds (what a model would do)."""
    kind, _ = map_rows(world)
    for y in range(2, len(kind) - 2):
        for x in range(2, W - 2):
            if kind[y][x] not in kinds:
                continue
            try:
                out = act(world, action, dry_run=True, **{key: at(x, y)}, **args)
            except BridgeError:
                continue
            if max_cost is None or out["cost"] <= max_cost:
                return x, y
    raise AssertionError(f"no site for {action} on the fixture map")


def cargo_id(world, label: str) -> int:
    return next(c["id"] for c in world.call("cargos") if c["label"] == label)


def vehicle_row(world, v: int) -> dict:
    return next(r for r in world.call("vehicles") if r["id"] == v)


def quarters(world, n: int = 2) -> dict:
    qs = world.call("company")["quarters"][:n]
    return {"income": sum(q["income"] for q in qs), "delivered": sum(q["delivered"] for q in qs)}


# ------------------------------------------------------------------------------------------- basics
def test_company_rename_runs_a_real_command(world):
    out = act(world, "company.rename", name="Railhead Transport")
    assert out == {"name": "Railhead Transport", "cost": 0}
    assert next(c["name"] for c in world.call("companies") if c["bound"]) == "Railhead Transport"


def test_gs_rejects_bad_args_even_without_python_validation(world):
    """Replay sends logged args straight to the GS: it must validate on its own."""
    for kwargs, frag in [({}, "missing required argument 'name'"), ({"name": ""}, "empty"),
                         ({"name": "x", "colour": 1}, "unknown argument 'colour'"), ({"name": 5}, "string")]:
        with pytest.raises(BridgeError) as ei:
            world.call("company_rename", **kwargs)
        assert ei.value.code == "ERR_BAD_ARG" and frag in ei.value.message
    with pytest.raises(BridgeError) as ei:
        world.call("road_line", from_tile=-5, to_tile=3)
    assert ei.value.code == "ERR_BAD_ARG" and "from_tile" in ei.value.message


def test_actions_need_a_bound_company(tmp_path):
    with World.create(tmp_path / "w", ServerConfig(seed=3), found_company=False) as w:
        with pytest.raises(BridgeError) as ei:
            w.call("land_demolish", tile=idx(10, 10))
        assert ei.value.code == "ERR_NO_COMPANY"


def test_dry_run_cost_matches_the_real_build(world):
    x, y = site(world, 6, 2)
    cost = dry(world, "road.line", from_tile=at(x, y), to_tile=at(x + 5, y))
    assert cost > 0
    before = money(world)
    out = act(world, "road.line", from_tile=at(x, y), to_tile=at(x + 5, y))
    assert out["cost"] == cost
    assert money(world) == before - cost


def test_chosen_rail_type_survives_save_and_load(tmp_path):
    """rail.set_type is session state in the GS; Save()/Load() must carry it so forks build the same track."""
    cfg = ServerConfig(seed=3, map_x_log2=7, map_y_log2=7, starting_year=2010)
    with World.create(tmp_path / "a", cfg) as w:
        assert act(w, "rail.set_type", railtype=1)["railtype"] == 1  # electrified rail
        save = w.save(tmp_path / "s.sav")
    with World.load(tmp_path / "b", save) as w2:
        x, y = site(w2, 1, 1)
        act(w2, "rail.track", tile=at(x, y), track="ne_sw")
        assert tile_info(w2, x, y)["rail_type"] == 1


# ------------------------------------------------------------------------------------------- rail
def test_rail_family(world):
    x, y = site(world, 12, 8)
    out = act(world, "rail.set_type", railtype=0)
    assert out["railtype"] == 0 and out["name"]
    fails(world, "rail.set_type", "ERR_BAD_ARG", railtype=60)

    # line (straight), dry run first, then the engine's own refusal to build it twice
    assert dry(world, "rail.line", from_tile=at(x, y + 1), to_tile=at(x + 5, y + 1)) > 0
    assert act(world, "rail.line", from_tile=at(x, y + 1), to_tile=at(x + 5, y + 1))["cost"] > 0
    for i in range(6):  # a dry-run removal only passes if that exact piece is there
        dry(world, "rail.remove", tile=at(x + i, y + 1), track="ne_sw")
    assert tile_info(world, x + 6, y + 1)["rail"] is False
    fails(world, "rail.line", "ERR_ALREADY_BUILT", from_tile=at(x, y + 1), to_tile=at(x + 5, y + 1))
    fails(world, "rail.line", "ERR_BAD_ARG", from_tile=at(x, y + 1), to_tile=at(x + 5, y + 3))  # neither straight nor diagonal

    # diagonal drag x+8..x+11, y..y+3
    assert act(world, "rail.line", from_tile=at(x + 8, y), to_tile=at(x + 11, y + 3))["cost"] > 0
    for i in range(4):
        assert tile_info(world, x + 8 + i, y + i)["rail"] is True

    # single pieces
    act(world, "rail.track", tile=at(x, y + 7), track="nw_se")
    act(world, "rail.remove", tile=at(x, y + 7), track="nw_se")
    fails(world, "rail.remove", "ERR_UNSUITABLE_TRACK", tile=at(x, y + 7), track="nw_se")

    # signals
    assert dry(world, "rail.signal", tile=at(x + 2, y + 1), front_tile=at(x + 3, y + 1), type="normal") > 0
    act(world, "rail.signal", tile=at(x + 2, y + 1), front_tile=at(x + 3, y + 1), type="normal")
    act(world, "rail.remove_signal", tile=at(x + 2, y + 1), front_tile=at(x + 3, y + 1))
    fails(world, "rail.remove_signal", "ERR_UNSUITABLE_TRACK", tile=at(x + 2, y + 1), front_tile=at(x + 3, y + 1))
    fails(world, "rail.signal", "ERR_BAD_ARG", tile=at(x + 2, y + 1), front_tile=at(x + 4, y + 1))

    # waypoint on the straight line
    wp = act(world, "rail.waypoint", tile=at(x + 4, y + 1))
    assert wp["waypoint"] >= 0

    # station + depot
    st = act(world, "rail.station", tile=at(x, y + 3), axis="x", platforms=2, length=4)
    assert st["station"] >= 0 and st["cost"] > 0
    # the engine has no script error code for "remove the station first": ERR_UNKNOWN + what is there
    e = fails(world, "rail.station", "ERR_UNKNOWN", tile=at(x + 1, y + 3), axis="x", platforms=1, length=2, join_station=-1)
    assert f"station {st['station']}" in e.message and "yours" in e.message
    fails(world, "rail.signal", "ERR_UNSUITABLE_TRACK", tile=at(x + 1, y + 3), front_tile=at(x + 2, y + 3))
    act(world, "rail.line", from_tile=at(x + 4, y + 3), to_tile=at(x + 6, y + 3))
    dep = act(world, "rail.depot", tile=at(x + 7, y + 3), front_tile=at(x + 6, y + 3))
    assert dep["depot_tile"] == idx(x + 7, y + 3)


# ------------------------------------------------------------------------------------------- road
def test_road_family(world):
    x, y = site(world, 8, 5)
    assert act(world, "road.set_type", roadtype=0)["roadtype"] == 0
    act(world, "road.line", from_tile=at(x, y + 1), to_tile=at(x + 6, y + 1))
    fails(world, "road.line", "ERR_ALREADY_BUILT", from_tile=at(x, y + 1), to_tile=at(x + 6, y + 1))
    fails(world, "road.line", "ERR_BAD_ARG", from_tile=at(x, y + 1), to_tile=at(x + 2, y + 2))

    assert dry(world, "road.depot", tile=at(x + 1, y), front_tile=at(x + 1, y + 1)) > 0
    assert act(world, "road.depot", tile=at(x + 1, y), front_tile=at(x + 1, y + 1))["depot_tile"] == idx(x + 1, y)

    bay = act(world, "road.stop", tile=at(x + 3, y + 2), front_tile=at(x + 3, y + 1), kind="bus")
    dt = act(world, "road.stop", tile=at(x + 5, y + 1), front_tile=at(x + 6, y + 1), kind="truck", drive_through=True,
             join_station=-1)
    assert bay["station"] >= 0 and dt["station"] >= 0 and bay["station"] != dt["station"]
    fails(world, "road.stop", "ERR_AREA_NOT_CLEAR", tile=at(x + 1, y), front_tile=at(x + 1, y + 1))
    fails(world, "road.stop", "ERR_ROAD_DRIVE_THROUGH_WRONG_DIRECTION",
          tile=at(x + 4, y + 1), front_tile=at(x + 4, y + 2), drive_through=True)

    act(world, "road.one_way", from_tile=at(x, y + 4), to_tile=at(x + 4, y + 4))
    act(world, "road.remove", from_tile=at(x, y + 4), to_tile=at(x + 4, y + 4))
    assert tile_info(world, x + 2, y + 4)["road"] is False


# ------------------------------------------------------------------------------------------- GUI auto-connect parity
def test_road_depot_and_stops_connect_to_adjacent_road_like_the_gui(world):
    """CcRoadDepot / CcRoadStop -> ConnectRoadToStructure: the entrance is joined to a plain road outside it."""
    x, y = site(world, 9, 4)
    act(world, "road.line", from_tile=at(x, y + 1), to_tile=at(x + 8, y + 1))

    # depot beside the road: the missing piece is added; the dry run prices it exactly
    cost = dry(world, "road.depot", tile=at(x + 1, y), front_tile=at(x + 1, y + 1))
    out = act(world, "road.depot", tile=at(x + 1, y), front_tile=at(x + 1, y + 1))
    assert out["connected"] is True and out["cost"] == cost
    fails(world, "road.line", "ERR_ALREADY_BUILT", from_tile=at(x + 1, y + 1), to_tile=at(x + 1, y))  # proof

    # depot facing bare land: nothing to connect to (same as the GUI)
    assert act(world, "road.depot", tile=at(x + 3, y + 3), front_tile=at(x + 4, y + 3))["connected"] is False

    # bay stop beside the road
    assert act(world, "road.stop", tile=at(x + 3, y), front_tile=at(x + 3, y + 1), join_station=-1)["connected"] is True
    fails(world, "road.line", "ERR_ALREADY_BUILT", from_tile=at(x + 3, y + 1), to_tile=at(x + 3, y))

    # drive-through stop on the existing road: already connected, nothing added, dry run exact
    cost = dry(world, "road.stop", tile=at(x + 6, y + 1), front_tile=at(x + 7, y + 1), drive_through=True, join_station=-1)
    out = act(world, "road.stop", tile=at(x + 6, y + 1), front_tile=at(x + 7, y + 1), drive_through=True, join_station=-1)
    assert out["connected"] is False and out["cost"] == cost


def test_drive_through_stop_in_a_road_gap_connects_both_ends(world):
    x, y = site(world, 7, 1)
    act(world, "road.line", from_tile=at(x, y), to_tile=at(x + 2, y))      # ends at the centre of x+2
    act(world, "road.line", from_tile=at(x + 4, y), to_tile=at(x + 6, y))  # starts at the centre of x+4
    out = act(world, "road.stop", tile=at(x + 3, y), front_tile=at(x + 4, y), kind="truck", drive_through=True)
    assert out["connected"] is True
    for end in (x + 2, x + 4):  # both neighbours now have their piece into the stop
        fails(world, "road.line", "ERR_ALREADY_BUILT", from_tile=at(end, y), to_tile=at(x + 3, y))


def test_rail_depot_beside_a_line_gets_the_gui_curves(world):
    """CcRailDepot: a depot facing a straight line gets both curves onto it (not a straight stub)."""
    x, y = site(world, 6, 3)
    act(world, "rail.line", from_tile=at(x, y + 1), to_tile=at(x + 5, y + 1))
    cost = dry(world, "rail.depot", tile=at(x + 2, y), front_tile=at(x + 2, y + 1))
    out = act(world, "rail.depot", tile=at(x + 2, y), front_tile=at(x + 2, y + 1))
    assert out["connected"] is True and out["cost"] == cost
    dry(world, "rail.remove", tile=at(x + 2, y + 1), track="nw_ne")  # curve toward north-east
    dry(world, "rail.remove", tile=at(x + 2, y + 1), track="nw_sw")  # curve toward south-west
    fails(world, "rail.remove", "ERR_UNSUITABLE_TRACK", tile=at(x + 2, y + 1), track="nw_se", dry_run=True)
    # facing bare land: nothing to connect
    assert act(world, "rail.depot", tile=at(x + 4, y + 2), front_tile=at(x + 5, y + 2))["connected"] is False


# ------------------------------------------------------------------------------------------- air / water
def test_air_family(world):
    x, y = site(world, 4, 3)
    fails(world, "air.airport", "ERR_TYPE_NOT_AVAILABLE", tile=at(x, y), type="metropolitan")  # 1950: not yet
    assert dry(world, "air.airport", tile=at(x, y), type="small") > 0
    out = act(world, "air.airport", tile=at(x, y), type="small")
    assert out["station"] >= 0 and out["hangar_tile"] >= 0
    fails(world, "air.airport", "ERR_AREA_NOT_CLEAR", tile=at(x + 1, y), type="small", join_station=-1)


def test_water_family(world):
    x, y = site(world, 3, 3)
    assert dry(world, "water.canal", tile=at(x, y + 1)) > 0
    act(world, "water.canal", tile=at(x, y + 1))
    act(world, "water.canal", tile=at(x + 1, y + 1))
    fails(world, "water.canal", "ERR_ALREADY_BUILT", tile=at(x, y + 1))
    dep = act(world, "water.ship_depot", tile=at(x, y + 1), toward_tile=at(x + 1, y + 1))
    assert dep["depot_tile"] == idx(x, y + 1)
    fails(world, "water.dock", "ERR_SITE_UNSUITABLE", tile=at(x + 2, y))  # flat land, no water

    dock = probe(world, "water.dock", "tile", kinds=",")
    assert act(world, "water.dock", tile=at(*dock))["station"] >= 0
    lock = probe(world, "water.lock", "tile", kinds=".,")
    act(world, "water.lock", tile=at(*lock))


# ------------------------------------------------------------------------------------------- bridges / tunnels / land
def test_bridge_and_tunnel(world):
    act(world, "company.loan", amount=300_000)
    tx, ty = probe(world, "bridge.tunnel", "start_tile", kinds=".", max_cost=50_000, vehicle="road")
    assert dry(world, "bridge.tunnel", start_tile=at(tx, ty), vehicle="road") > 0
    out = act(world, "bridge.tunnel", start_tile=at(tx, ty), vehicle="road")
    assert out["other_end_tile"] not in (-1, idx(tx, ty))
    fails(world, "bridge.tunnel", "ERR_AREA_NOT_CLEAR", start_tile=at(tx, ty), vehicle="road")

    x, y = site(world, 6, 2)
    fails(world, "bridge.tunnel", "ERR_TUNNEL_START_SITE_UNSUITABLE", start_tile=at(x, y), vehicle="rail")
    fails(world, "bridge.build", "ERR_BAD_ARG", start_tile=at(x, y), end_tile=at(x, y))
    act(world, "road.line", from_tile=at(x + 2, y), to_tile=at(x + 2, y + 1))  # something to span
    assert dry(world, "bridge.build", start_tile=at(x, y), end_tile=at(x + 4, y), vehicle="road") > 0
    br = act(world, "bridge.build", start_tile=at(x, y), end_tile=at(x + 4, y), vehicle="road")
    assert br["length"] == 5 and br["bridge_type"] >= 0
    fails(world, "bridge.build", "ERR_ALREADY_BUILT", start_tile=at(x, y), end_tile=at(x + 4, y), vehicle="road")


def test_land_family(world):
    x, y = site(world, 4, 4)
    fails(world, "land.level", "ERR_AREA_ALREADY_FLAT", from_tile=at(x, y), to_tile=at(x + 3, y + 3))
    h0 = tile_info(world, x + 1, y + 1)["max_height"]
    assert dry(world, "land.raise", tile=at(x + 1, y + 1), corner="all") > 0
    act(world, "land.raise", tile=at(x + 1, y + 1), corner="all")
    assert tile_info(world, x + 1, y + 1)["max_height"] == h0 + 1
    act(world, "land.lower", tile=at(x + 1, y + 1), corner="n")
    act(world, "land.level", from_tile=at(x, y), to_tile=at(x + 3, y + 3))
    assert tile_info(world, x + 1, y + 1)["slope"] == 0
    act(world, "road.line", from_tile=at(x, y), to_tile=at(x + 2, y))
    act(world, "land.demolish", tile=at(x + 1, y))
    assert tile_info(world, x + 1, y)["road"] is False


# ------------------------------------------------------------------------------------------- vehicles / orders
def _bus_yard(world):
    """Road with two bay bus stops and a depot on a flat site; returns (depot, stop_a, stop_b) tiles as "x,y"."""
    x, y = site(world, 10, 3)
    act(world, "road.line", from_tile=at(x, y + 1), to_tile=at(x + 9, y + 1))
    assert act(world, "road.depot", tile=at(x + 4, y), front_tile=at(x + 4, y + 1))["connected"] is True
    act(world, "road.stop", tile=at(x + 1, y + 2), front_tile=at(x + 1, y + 1))
    act(world, "road.stop", tile=at(x + 8, y + 2), front_tile=at(x + 8, y + 1), join_station=-1)
    return at(x + 4, y), at(x + 1, y + 2), at(x + 8, y + 2)


def bus_engine(world) -> int:
    return next(e["id"] for e in world.call("engines", vtype="road", cargo=cargo_id(world, "PASS")) if e["carries"])


def test_vehicle_and_order_family(world):
    depot, a, b = _bus_yard(world)
    eng = bus_engine(world)

    assert dry(world, "vehicle.buy", depot_tile=depot, engine=eng) > 0
    v = act(world, "vehicle.buy", depot_tile=depot, engine=eng)["vehicle"]
    fails(world, "vehicle.buy", "ERR_BAD_ARG", depot_tile=a, engine=eng)  # not a depot
    fails(world, "vehicle.refit", "ERR_VEHICLE_CANNOT_REFIT", vehicle=v, cargo="COAL")
    fails(world, "vehicle.buy", "ERR_VEHICLE_CANNOT_REFIT", depot_tile=depot, engine=eng, cargo="COAL")

    # orders
    assert act(world, "order.append", vehicle=v, tile=a, flags=["non_stop"])["index"] == 0
    assert act(world, "order.append", vehicle=v, tile=b)["orders"] == 2
    assert act(world, "order.insert", vehicle=v, index=1, tile=depot, flags=["service"])["orders"] == 3
    fails(world, "order.append", "ERR_BAD_ARG", vehicle=v, tile=a, flags=["full_load", "no_load"])
    fails(world, "order.append", "ERR_BAD_ARG", vehicle=v, tile=at(0, 0))  # not a destination
    assert act(world, "order.remove", vehicle=v, index=1)["orders"] == 2
    orders = world.call("orders", vehicle=v)
    assert [o["tile"] for o in orders] == [to_gs("land.demolish", {"tile": a}, W)[1]["tile"],
                                           to_gs("land.demolish", {"tile": b}, W)[1]["tile"]]
    assert orders[0]["flags"] == ["non_stop"]

    # clone sharing orders (proof of sharing: an order appended to one shows up on the other), then clear/share
    c = act(world, "vehicle.clone", depot_tile=depot, vehicle=v, share_orders=True)["vehicle"]
    act(world, "order.append", vehicle=v, tile=depot)
    assert vehicle_row(world, c)["orders"] == 3
    act(world, "order.remove", vehicle=c, index=2)
    assert vehicle_row(world, v)["orders"] == 2
    assert dry(world, "order.clear", vehicle=c) == 0
    assert act(world, "order.clear", vehicle=c)["orders"] == 0
    assert vehicle_row(world, v)["orders"] == 2  # the original keeps its list
    assert act(world, "order.share", vehicle=c, other_vehicle=v)["orders"] == 2
    fails(world, "order.share", "ERR_BAD_ARG", vehicle=c, other_vehicle=c)

    # start/stop are idempotent, sell needs a stopped-in-depot vehicle
    assert act(world, "vehicle.start", vehicle=v)["changed"] is True
    assert act(world, "vehicle.start", vehicle=v)["changed"] is False
    act(world, "vehicle.skip_order", vehicle=v, index=1)
    fails(world, "vehicle.skip_order", "ERR_BAD_ARG", vehicle=v, index=5)
    world.step(days=3)
    row = vehicle_row(world, v)
    assert row["tile"] != to_gs("land.demolish", {"tile": depot}, W)[1]["tile"], "bus left the auto-connected depot"
    fails(world, "vehicle.sell", "ERR_VEHICLE_NOT_IN_DEPOT", vehicle=v)
    act(world, "vehicle.depot", vehicle=v)
    assert act(world, "vehicle.stop", vehicle=c)["changed"] is False  # bought vehicles start stopped
    sold = act(world, "vehicle.sell", vehicle=c)
    assert sold["cost"] < 0  # selling earns money
    fails(world, "vehicle.start", "ERR_BAD_ARG", vehicle=c)  # gone

    # buy refitted
    w2 = act(world, "vehicle.buy", depot_tile=depot, engine=eng, cargo="PASS")
    assert w2["vehicle"] >= 0


# ------------------------------------------------------------------------------------------- company / town
def test_company_and_town_family(world):
    town = world.call("towns")[0]["id"]
    out = act(world, "company.loan", amount=0)
    assert out["loan"] == 0 and out["money"] == 0
    fails(world, "town.action", "ERR_TOWN_ACTION_UNAVAILABLE", town=town, kind="advertise_small")
    x, y = site(world, 4, 1)
    fails(world, "road.line", "ERR_NOT_ENOUGH_CASH", from_tile=at(x, y), to_tile=at(x + 3, y))
    fails(world, "company.loan", "ERR_BAD_ARG", amount=12345)       # not a multiple of the interval
    fails(world, "company.loan", "ERR_BAD_ARG", amount=10_000_000)  # above max loan

    assert act(world, "company.loan", amount=300_000)["loan"] == 300_000
    assert dry(world, "town.action", town=town, kind="statue") > 0
    assert act(world, "town.action", town=town, kind="advertise_small")["cost"] > 0
    fails(world, "company.loan", "ERR_NOT_ENOUGH_CASH", amount=0)  # cannot repay more than the cash


# ------------------------------------------------------------------------------------------- end to end
def _seg(world, p: tuple[int, int], q: tuple[int, int]) -> bool:
    if p == q:
        return True
    try:
        act(world, "road.line", from_tile=at(*p), to_tile=at(*q))
        return True
    except BridgeError as e:
        return e.code == "ERR_ALREADY_BUILT"


def _connect(world, a: tuple[int, int], b: tuple[int, int]) -> list[tuple[int, int]] | None:
    """Straight segments only (L and Z shapes), first that builds wins — deterministic for a seed."""
    (ax, ay), (bx, by) = a, b
    mx, my = (ax + bx) // 2, (ay + by) // 2
    for pts in ([a, (bx, ay), b], [a, (ax, by), b], [a, (mx, ay), (mx, by), b], [a, (ax, my), (bx, my), b]):
        if all(_seg(world, p, q) for p, q in pairwise(pts)):
            return pts
    return None


def _walk(p: tuple[int, int], q: tuple[int, int], n: int) -> list[tuple[int, int]]:
    """Tiles 1..n steps from p toward q (on a straight segment)."""
    sx, sy = (q[0] > p[0]) - (q[0] < p[0]), (q[1] > p[1]) - (q[1] < p[1])
    span = abs(q[0] - p[0]) + abs(q[1] - p[1])
    return [(p[0] + sx * i, p[1] + sy * i) for i in range(1, min(n, span - 1) + 1)]


def _stop_near(world, end: tuple[int, int], toward: tuple[int, int]) -> tuple[int, int] | None:
    sx, sy = (toward[0] > end[0]) - (toward[0] < end[0]), (toward[1] > end[1]) - (toward[1] < end[1])
    for t in _walk(end, toward, 5):
        try:
            act(world, "road.stop", tile=at(*t), front_tile=at(t[0] + sx, t[1] + sy), kind="bus", drive_through=True,
                join_station=-1)
            return t
        except BridgeError:
            continue
    return None


def _depot_beside(world, p: tuple[int, int], q: tuple[int, int], avoid: tuple[int, int]) -> str | None:
    sx, sy = (q[0] > p[0]) - (q[0] < p[0]), (q[1] > p[1]) - (q[1] < p[1])
    for t in _walk(p, q, 12):
        if abs(t[0] - avoid[0]) + abs(t[1] - avoid[1]) <= 1:
            continue
        for side in ((t[0] + sy, t[1] + sx), (t[0] - sy, t[1] - sx)):  # perpendicular neighbours
            try:
                out = act(world, "road.depot", tile=at(*side), front_tile=at(*t))
            except BridgeError:
                continue
            assert out["connected"] is True  # the GUI follow-up joined it to the route
            return at(*side)
    return None


def _bus_line(world) -> dict:
    """Player actions + read-only queries only: road, stops, depot, two buses, orders, start, 90 days."""
    towns = world.call("towns")
    pairs = sorted((abs(s["x"] - t["x"]) + abs(s["y"] - t["y"]), s["id"], t["id"])
                   for s in towns for t in towns if s["id"] < t["id"])
    by_id = {t["id"]: t for t in towns}
    built = None
    for dist, i, j in pairs:
        if not 15 <= dist <= 45:
            continue
        a, b = (by_id[i]["x"], by_id[i]["y"]), (by_id[j]["x"], by_id[j]["y"])
        route = _connect(world, a, b)
        if route is None:
            continue
        stop_a = _stop_near(world, route[0], route[1])
        stop_b = _stop_near(world, route[-1], route[-2])
        depot = _depot_beside(world, route[0], route[1], avoid=stop_a) if stop_a and stop_b else None
        if depot:
            built = {"towns": (i, j), "route": route, "stops": (stop_a, stop_b), "depot": depot}
            break
    assert built, "no buildable town pair on the fixture map"

    eng = bus_engine(world)
    buses = [act(world, "vehicle.buy", depot_tile=built["depot"], engine=eng)["vehicle"]]
    for stop in built["stops"]:
        act(world, "order.append", vehicle=buses[0], tile=at(*stop))
    buses.append(act(world, "vehicle.clone", depot_tile=built["depot"], vehicle=buses[0], share_orders=True)["vehicle"])
    for v in buses:
        assert act(world, "vehicle.start", vehicle=v)["changed"] is True

    world.step(days=90)

    built["finance"] = quarters(world)
    built["profits"] = [vehicle_row(world, v)["profit_this_year"] for v in buses]
    built["checksum"] = world.call("checksum")
    return built


def test_e2e_model_plays_directly_bus_line_between_two_towns_earns_money(world):
    out = _bus_line(world)
    assert out["finance"]["delivered"] > 0, out
    assert out["finance"]["income"] > 0, out
    assert sum(out["profits"]) > 0, out


def test_e2e_bus_line_is_deterministic_for_a_fixed_seed(world, tmp_path):
    first = _bus_line(world)
    with World.create(tmp_path / "again", dataclasses.replace(world.server.config)) as w2:
        second = _bus_line(w2)
    assert first == second


def test_rail_wagons_couple_to_a_locomotive_like_the_depot_gui(world):
    x, y = site(world, 4, 3)
    act(world, "rail.set_type", railtype=0)
    act(world, "rail.depot", tile=at(x + 1, y + 1), front_tile=at(x + 2, y + 1))
    engines = world.call("engines", vtype="rail")
    loco = next(e for e in engines if not e.get("wagon"))
    wagon = next(e for e in engines if e.get("wagon") and e.get("capacity", 0) > 0)
    train = act(world, "vehicle.buy", depot_tile=at(x + 1, y + 1), engine=loco["id"])["vehicle"]
    free = act(world, "vehicle.buy", depot_tile=at(x + 1, y + 1), engine=wagon["id"])["vehicle"]
    out = act(world, "vehicle.attach", vehicle=free, train_vehicle=train)
    assert out["wagons"] == 2
    assert vehicle_row(world, train)["capacity"] > 0
    # a second wagon goes to the end of the same train
    free2 = act(world, "vehicle.buy", depot_tile=at(x + 1, y + 1), engine=wagon["id"])["vehicle"]
    assert act(world, "vehicle.attach", vehicle=free2, train_vehicle=train)["wagons"] == 3
    # selling a wagon back out of the train
    assert act(world, "vehicle.sell_wagon", vehicle=train, index=2)["wagons"] == 2
