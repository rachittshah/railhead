"""Read-only world queries served by gamescript/game/RailheadBridge/lib/query.nut.

Every test runs against the shared ``world`` fixture: a 128x128 temperate map (seed 3), the model's
company bound, game paused at 1950-01-01 unless a test steps it.
"""

import pytest

from railhead.bridge import BridgeError

pytestmark = pytest.mark.integration

MAP = 128


def cargo_ids(world) -> dict[str, int]:
    return {c["label"]: c["id"] for c in world.call("cargos")}


# ---------------------------------------------------------------------------------------------- core
def test_towns_have_stable_identity_fields(world):
    towns = world.call("towns")
    assert len(towns) >= 2
    t = towns[0]
    assert {"id", "name", "tile", "x", "y", "population"} <= t.keys()
    assert t["tile"] == t["y"] * 128 + t["x"]


def test_industries_report_produced_and_accepted_cargo(world):
    inds = world.call("industries")
    assert inds, "a normal-density 128x128 map has industries"
    assert any(i["produces"] for i in inds)
    assert any(i["accepts"] for i in inds)


# ---------------------------------------------------------------------------------------------- map_info / cargos
def test_map_info_reports_size_climate_date_and_company(world):
    info = world.call("map_info")
    assert (info["size_x"], info["size_y"]) == (MAP, MAP)
    assert info["climate"] == "temperate"
    assert (info["year"], info["month"], info["day"]) == (1950, 1, 1)
    assert info["date"] == world.state()["date"]
    assert info["company"] == world.company
    assert info["towns"] >= 2 and info["industries"] >= 1


def test_map_info_date_follows_stepping(world):
    world.step(days=40)
    info = world.call("map_info")
    assert (info["year"], info["month"], info["day"]) == (1950, 2, 10)


def test_cargos_list_labels_names_and_town_effects(world):
    cargos = {c["label"]: c for c in world.call("cargos")}
    assert {"PASS", "MAIL", "COAL", "GOOD"} <= cargos.keys()
    assert cargos["PASS"]["freight"] is False and cargos["PASS"]["town_effect"] == "passengers"
    assert cargos["MAIL"]["town_effect"] == "mail"
    assert cargos["COAL"]["freight"] is True and cargos["COAL"]["town_effect"] == "none"
    assert cargos["GOOD"]["town_effect"] == "goods"
    assert all(isinstance(c["id"], int) and c["name"] for c in cargos.values())


# ---------------------------------------------------------------------------------------------- towns
def test_towns_report_houses_rating_and_last_month_service(world):
    world.step(days=35)  # roll one economy-month so last-month stats exist
    towns = world.call("towns")
    for t in towns:
        assert t["houses"] > 0
        assert t["rating"] == "none", "the model's company has no presence anywhere yet"
        assert t["pax_transported"] == 0 and t["mail_transported"] == 0
        assert 0 <= t["pax_pct"] <= 100
    assert sum(t["pax_produced"] for t in towns) > 0
    assert sum(t["mail_produced"] for t in towns) > 0


def test_towns_near_tile_filters_and_sorts_by_distance(world):
    towns = world.call("towns")
    home = towns[-1]
    only = world.call("towns", near_tile=home["tile"], radius=0)
    assert [t["id"] for t in only] == [home["id"]] and only[0]["dist"] == 0
    ranked = world.call("towns", near_tile=home["tile"])
    assert len(ranked) == len(towns)
    assert ranked[0]["id"] == home["id"]
    assert [t["dist"] for t in ranked] == sorted(t["dist"] for t in ranked)


# ---------------------------------------------------------------------------------------------- industries
def test_industries_report_production_transport_and_stations_around(world):
    world.step(days=35)
    inds = world.call("industries")
    for i in inds:
        assert {"id", "name", "tile", "x", "y", "type_name", "produces", "accepts", "stations_around"} <= i.keys()
        for p in i["produces"]:
            assert {"cargo", "last_month", "transported", "pct"} <= p.keys()
            assert p["transported"] == 0 and p["pct"] == 0
        assert i["stations_around"] == 0
    assert any(p["last_month"] > 0 for i in inds for p in i["produces"])


def test_industries_cargo_filter_keeps_producers_and_acceptors(world):
    coal = cargo_ids(world)["COAL"]
    every = world.call("industries")
    coal_inds = world.call("industries", cargo=coal)
    assert 0 < len(coal_inds) < len(every)
    for i in coal_inds:
        assert coal in i["accepts"] or any(p["cargo"] == coal for p in i["produces"])
    producers = world.call("industries", cargo=coal, role="produces")
    assert producers and all(any(p["cargo"] == coal for p in i["produces"]) for i in producers)


def test_industries_near_tile_sorts_by_distance(world):
    every = world.call("industries")
    pivot = every[0]
    ranked = world.call("industries", near_tile=pivot["tile"], radius=40)
    assert ranked[0]["id"] == pivot["id"] and ranked[0]["dist"] == 0
    assert all(i["dist"] <= 40 for i in ranked)
    assert [i["dist"] for i in ranked] == sorted(i["dist"] for i in ranked)


# ---------------------------------------------------------------------------------------------- engines
ENGINE_FIELDS = {"id", "name", "vtype", "cargo", "capacity", "speed", "price", "running_cost", "reliability",
                 "max_age", "wagon", "articulated"}


def test_engines_lists_buildable_road_vehicles_with_specs(world):
    pas = cargo_ids(world)["PASS"]
    rows = world.call("engines", vtype="road")
    assert rows, "1950 temperate has buses and trucks"
    for e in rows:
        assert ENGINE_FIELDS | {"power", "weight", "road_type"} <= e.keys()
        assert e["vtype"] == "road" and e["price"] > 0 and e["speed"] > 0
        assert 0 <= e["reliability"] <= 100 and e["max_age"] > 0
    assert any(e["cargo"] == pas for e in rows), "a bus is buildable"


def test_engines_rail_has_locomotives_and_wagons(world):
    rows = world.call("engines", vtype="rail")
    locos = [e for e in rows if not e["wagon"]]
    wagons = [e for e in rows if e["wagon"]]
    assert locos and wagons
    assert all(e["power"] > 0 and e["weight"] > 0 for e in locos)
    assert all(e["capacity"] > 0 for e in wagons)
    assert all(isinstance(e["rail_type"], int) and e["rail_type_name"] for e in rows)


def test_engines_cargo_filter_keeps_carriers_and_pullers(world):
    coal = cargo_ids(world)["COAL"]
    rows = world.call("engines", vtype="rail", cargo=coal)
    assert any(e["wagon"] and e["carries"] for e in rows), "a coal wagon is buildable"
    assert any(not e["wagon"] for e in rows), "locomotives that can pull coal are kept"
    assert all(e["carries"] or not e["wagon"] for e in rows)


def test_engines_other_types_and_bad_type(world):
    assert isinstance(world.call("engines", vtype="water"), list)
    assert isinstance(world.call("engines", vtype="air"), list)
    with pytest.raises(BridgeError) as ei:
        world.call("engines", vtype="hovercraft")
    assert ei.value.code == "ERR_BAD_ARG"


# ---------------------------------------------------------------------------------------------- company
def test_company_reports_cash_loan_value_and_quarters(world):
    c = world.call("company")
    assert c["id"] == world.company and c["name"]
    assert c["money"] > 0 and c["loan"] >= 0
    assert c["max_loan"] >= c["loan"] and c["max_loan"] > 0 and c["loan_interval"] > 0
    assert [q["q"] for q in c["quarters"]] == [0, 1, 2, 3, 4]
    assert {"income", "expenses", "delivered", "rating", "value"} <= c["quarters"][0].keys()


def test_company_quarter_history_fills_after_a_quarter(world):
    world.step(days=100)
    q1 = world.call("company")["quarters"][1]
    assert q1["rating"] >= 0 and q1["value"] > 0


# ---------------------------------------------------------------------------------------------- tiles / tile / catchment
KIND_CHARS = set(" .~,TI=#+SWDB^tfLO")
HEIGHT_CHARS = set("0123456789abcdefghijklmnopqrstuvwxyz")


def full_map(world) -> list[dict]:
    rows = []
    for y0 in range(0, MAP, 32):
        rows += world.call("tiles", x=0, y=y0, w=MAP, h=32)
    return rows


def find_kind(rows, ch) -> tuple[int, int]:
    for r in rows:
        i = r["kind"].find(ch)
        if i >= 0:
            return r["x"] + i, r["y"]
    raise AssertionError(f"no {ch!r} tile on the map")


def test_tiles_returns_one_compact_row_per_y(world):
    rows = world.call("tiles", x=0, y=0, w=MAP, h=32)
    assert [r["y"] for r in rows] == list(range(32))
    for r in rows:
        assert r["x"] == 0
        assert len(r["kind"]) == len(r["height"]) == len(r["owner"]) == len(r["slope"]) == len(r["build"]) == MAP
        assert set(r["kind"]) <= KIND_CHARS
        assert set(r["height"]) <= HEIGHT_CHARS
        assert set(r["owner"]) <= set("*o.")
        assert set(r["slope"]) <= set("01s")
        assert set(r["build"]) <= set("01")


def test_tiles_slope_and_build_layers_agree_with_the_tile_query(world):
    rows = full_map(world)
    slope = "".join(r["slope"] for r in rows)
    build = "".join(r["build"] for r in rows)
    assert "0" in slope and "1" in slope, "a generated map has flat and sloped land"
    assert "1" in build and "0" in build
    assert rows[MAP - 1]["slope"] == "0" * MAP and rows[MAP - 1]["build"] == "0" * MAP, "off-map edge"
    picks = {}
    for r in rows[1:-1]:
        for i in range(1, MAP - 1):
            picks.setdefault((r["slope"][i], r["build"][i], r["kind"][i]), (i, r["y"]))
    for (s, b, k), (x, y) in picks.items():
        t = world.call("tile", x=x, y=y, radius=0)
        assert t["kind"] == k
        assert (b == "1") == t["buildable"], (x, y)
        expected = "0" if t["slope"] == 0 else "s" if t["slope"] & 16 else "1"
        assert s == expected, (x, y, t["slope"])


def test_tiles_full_map_shows_towns_roads_industries_and_void_edges(world):
    rows = full_map(world)
    grid = "".join(r["kind"] for r in rows)
    assert grid.count("T") > 0 and grid.count("=") > 0 and grid.count("I") > 0
    assert rows[MAP - 1]["kind"] == " " * MAP, "the south-east border row is off-map (void)"
    assert all(r["kind"][MAP - 1] == " " for r in rows)
    for t in world.call("towns"):
        assert rows[t["y"]]["kind"][t["x"]] in "=T+B"
    assert set("".join(r["owner"] for r in rows)) == {"."}, "a new company owns nothing"


def test_tiles_clips_to_the_map_and_caps_the_area(world):
    rows = world.call("tiles", x=120, y=124, w=20, h=20)
    assert [r["y"] for r in rows] == [124, 125, 126, 127]
    assert all(r["x"] == 120 and len(r["kind"]) == 8 for r in rows)
    with pytest.raises(BridgeError) as ei:
        world.call("tiles", x=0, y=0, w=MAP, h=33)
    assert ei.value.code == "ERR_BAD_ARG"


def test_tile_describes_a_town_centre_with_catchment(world):
    pas = cargo_ids(world)["PASS"]
    town = world.call("towns")[0]
    t = world.call("tile", tile=town["tile"])
    assert (t["tile"], t["x"], t["y"]) == (town["tile"], town["x"], town["y"])
    assert t["town"] == town["id"] and t["closest_town"]["id"] == town["id"]
    assert t["closest_town"]["dist"] == 0
    assert t["owner"] == -1 and t["mine"] is False
    assert t["kind"] in "=T+B"
    assert t["height"] <= t["max_height"]
    assert t["terrain"] == "normal" and t["radius"] == 3
    accepts = {r["cargo"]: r for r in t["accepts"]}
    assert accepts[pas]["amount"] >= 8 and accepts[pas]["accepted"] is True and accepts[pas]["label"] == "PASS"
    assert any(r["cargo"] == pas and r["producers"] > 0 for r in t["produces"])


def test_tile_kind_matches_the_map_for_clear_land_and_industry(world):
    rows = full_map(world)
    x, y = find_kind(rows, ".")
    clear = world.call("tile", x=x, y=y)
    assert clear["kind"] == "." and clear["buildable"] is True and clear["industry"] == -1
    assert clear["station"] == -1 and clear["water"] is False
    x, y = find_kind(rows, "I")
    ind = world.call("tile", tile=y * MAP + x)
    assert ind["kind"] == "I" and ind["buildable"] is False
    assert ind["industry"] in {i["id"] for i in world.call("industries")}
    assert ind["industry"] in {i["id"] for i in ind["industries"]}


def test_tile_rejects_missing_or_off_map_tile(world):
    for args in ({}, {"tile": MAP * MAP + 5}, {"x": MAP, "y": 0}):
        with pytest.raises(BridgeError) as ei:
            world.call("tile", **args)
        assert ei.value.code == "ERR_BAD_ARG"


def test_catchment_covers_a_station_footprint(world):
    pas = cargo_ids(world)["PASS"]
    town = world.call("towns")[0]
    c = world.call("catchment", tile=town["tile"], w=2, h=2, radius=4)
    assert (c["w"], c["h"], c["radius"]) == (2, 2, 4)
    assert any(r["cargo"] == pas and r["accepted"] for r in c["accepts"])
    assert any(r["cargo"] == pas for r in c["produces"])
    small = world.call("catchment", tile=town["tile"], radius=0)
    big = {r["cargo"]: r["amount"] for r in c["accepts"]}
    assert all(r["amount"] <= big.get(r["cargo"], 0) for r in small["accepts"])


# ---------------------------------------------------------------------------------------------- company assets
def test_asset_queries_are_empty_for_a_new_company(world):
    assert world.call("stations") == []
    assert world.call("vehicles") == []
    assert world.call("depots") == []
    for vt in ("rail", "road", "water", "air"):
        assert world.call("depots", vtype=vt) == []


def test_asset_queries_validate_arguments(world):
    with pytest.raises(BridgeError) as ei:
        world.call("orders", vehicle=12345)
    assert ei.value.code == "ERR_NO_VEHICLE"
    for method, args in (("orders", {}), ("depots", {"vtype": "zeppelin"}), ("vehicles", {"vtype": "zeppelin"})):
        with pytest.raises(BridgeError) as ei:
            world.call(method, **args)
        assert ei.value.code == "ERR_BAD_ARG", method


def test_subsidies_name_both_endpoints(world):
    world.step(days=730)  # offers are monthly dice rolls; seed 3 has some by year two
    subs = world.call("subsidies")
    assert subs, "two years on a map with towns and industries offer subsidies"
    for s in subs:
        assert {"id", "cargo", "label", "awarded", "awarded_to", "mine", "expires", "from", "to"} <= s.keys()
        assert s["awarded"] is False and s["awarded_to"] == -1 and s["mine"] is False
        for end in (s["from"], s["to"]):
            assert end["type"] in ("town", "industry")
            assert {"id", "name", "tile", "x", "y"} <= end.keys()
        assert s["expires"]["date"] > world.state()["date"]


def _act(world, method, **args):
    """Call a player action; skip the test while the actions module does not provide it yet."""
    try:
        return world.call(method, **args)
    except BridgeError as e:
        if e.code == "ERR_UNKNOWN_METHOD":
            pytest.skip(f"action {method} is not implemented yet")
        raise


def _sites_next_to_road(world, town, span=10):
    """Clear tiles 4-adjacent to a road near ``town`` as (tile, road_tile) pairs, nearest first."""
    x0, y0 = max(1, town["x"] - span), max(1, town["y"] - span)
    rows = world.call("tiles", x=x0, y=y0, w=2 * span + 1, h=2 * span + 1)
    kind = {(r["x"] + i, r["y"]): ch for r in rows for i, ch in enumerate(r["kind"])}
    out = []
    for (x, y), ch in kind.items():
        if ch != ".":
            continue
        for nx, ny in ((x + 1, y), (x - 1, y), (x, y + 1), (x, y - 1)):
            if kind.get((nx, ny)) == "=":
                out.append((abs(x - town["x"]) + abs(y - town["y"]), y * MAP + x, ny * MAP + nx))
    return [(t, f) for _, t, f in sorted(out)]


def build_bus_service(world):
    """Road depot + bus stop in the first town and one bus with a station order and a depot order."""
    pas = cargo_ids(world)["PASS"]
    town = world.call("towns")[0]
    built: dict[str, int] = {}
    fronts: dict[str, int] = {}
    for tile, front in _sites_next_to_road(world, town):
        for what, method, extra in (("depot", "road_depot", {}), ("stop", "road_stop", {"kind": "bus"})):
            if what in built:
                continue
            try:
                _act(world, method, tile=tile, front_tile=front, **extra)
            except BridgeError:
                continue
            built[what], fronts[what] = tile, front
            break
        if len(built) == 2:
            break
    assert len(built) == 2, f"could not place a depot and a bus stop near {town['name']}: {built}"
    # Join each entrance to its road like the GUI does (the GS build commands do not connect them).
    for what in ("depot", "stop"):
        try:
            _act(world, "road_line", from_tile=built[what], to_tile=fronts[what])
        except BridgeError as e:
            if e.code != "ERR_ALREADY_BUILT":
                raise
    bus = next(e for e in world.call("engines", vtype="road", cargo=pas) if e["cargo"] == pas)
    v = _act(world, "vehicle_buy", depot_tile=built["depot"], engine=bus["id"])["vehicle"]
    _act(world, "order_append", vehicle=v, tile=built["stop"], flags=["non_stop", "full_load_any"])
    _act(world, "order_append", vehicle=v, tile=built["depot"], flags=["service"])
    return {"town": town, "bus": bus, "vehicle": v, "pas": pas, **built}


def test_asset_queries_report_a_bus_service(world):
    svc = build_bus_service(world)
    v, depot, stop = svc["vehicle"], svc["depot"], svc["stop"]

    (st,) = world.call("stations")
    assert st["tile"] == stop and st["types"] == ["bus"] and st["vehicles"] == 1
    assert st["town"] == svc["town"]["id"]

    (dp,) = world.call("depots", vtype="road")
    assert dp["tile"] == depot and dp["vtype"] == "road" and dp["vehicles"] == 1
    assert world.call("depots") == [dp]

    (veh,) = world.call("vehicles")
    assert veh["id"] == v and veh["vtype"] == "road" and veh["engine"] == svc["bus"]["id"]
    assert veh["engine_name"] == svc["bus"]["name"]
    assert veh["state"] == "in_depot" and veh["in_depot"] is True and veh["stopped"] is True
    assert veh["tile"] == depot and veh["orders"] == 2 and veh["cur_order"] == 0
    assert veh["cargo"] == svc["pas"] and veh["capacity"] == svc["bus"]["capacity"] and veh["load"] == 0
    assert {"name", "unit", "speed", "age", "max_age", "reliability", "profit_this_year", "profit_last_year",
            "value"} <= veh.keys()
    assert world.call("vehicles", vtype="rail") == []

    o0, o1 = world.call("orders", vehicle=v)
    assert (o0["i"], o0["kind"], o0["tile"], o0["station"]) == (0, "station", stop, st["id"])
    assert set(o0["flags"]) == {"non_stop", "full_load_any"} and o0["current"] is True
    assert (o1["i"], o1["kind"], o1["tile"]) == (1, "depot", depot)
    assert o1["flags"] == ["service"] and o1["current"] is False


def test_vehicles_and_stations_update_as_the_service_runs(world):
    svc = build_bus_service(world)
    _act(world, "vehicle_start", vehicle=svc["vehicle"])
    world.step(days=30)
    (veh,) = world.call("vehicles")
    assert veh["state"] in ("running", "at_station") and veh["in_depot"] is False
    assert veh["age"] >= 30
    (st,) = world.call("stations")
    pas = [c for c in st["cargo"] if c["cargo"] == svc["pas"]]
    assert pas and 0 <= pas[0]["rating"] <= 100 and pas[0]["waiting"] >= 0


def test_company_queries_need_a_bound_company(tmp_path):
    from railhead.bridge import Bridge
    from railhead.server import OpenTTDServer, ServerConfig

    with OpenTTDServer(tmp_path / "inst", ServerConfig(seed=7)) as srv, srv.admin() as admin:
        bridge = Bridge(admin)
        bridge.wait_ready(timeout=30)
        assert bridge.call("map_info")["company"] == -1
        assert all(t["rating"] == "none" for t in bridge.call("towns"))
        assert bridge.call("tiles", x=0, y=0, w=16, h=16)
        for method, args in (("company", {}), ("stations", {}), ("vehicles", {}), ("depots", {}),
                             ("engines", {"vtype": "road"}), ("orders", {"vehicle": 0})):
            with pytest.raises(BridgeError) as ei:
                bridge.call(method, **args)
            assert ei.value.code == "ERR_NO_COMPANY", method
