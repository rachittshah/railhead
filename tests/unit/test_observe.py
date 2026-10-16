"""Pure text formatting of query results for the model (src/railhead/observe.py)."""

import pytest

from railhead import observe as ob

LABELS = {0: "PASS", 1: "COAL", 2: "MAIL", 5: "GOOD"}

MAP_INFO = {"date": 712234, "year": 1950, "month": 2, "day": 10, "size_x": 128, "size_y": 128,
            "climate": "temperate", "company": 0, "company_name": "Fedborough Transport", "ticks": 2960,
            "towns": 9, "industries": 21}

COMPANY = {"id": 0, "name": "Fedborough Transport", "money": 95_123, "loan": 100_000, "max_loan": 300_000,
           "loan_interval": 10_000, "value": 4_921, "hq": -1, "quarters": [
               {"q": 0, "income": 2_500, "expenses": -812, "delivered": 40, "rating": None, "value": 4_921},
               {"q": 1, "income": 7_000, "expenses": -3_100, "delivered": 120, "rating": 87, "value": 4_500},
               {"q": 2, "income": 0, "expenses": 0, "delivered": 0, "rating": 0, "value": 0},
               {"q": 3, "income": 0, "expenses": 0, "delivered": 0, "rating": 0, "value": 0},
               {"q": 4, "income": 0, "expenses": 0, "delivered": 0, "rating": 0, "value": 0}]}


def vehicle(id, **kw):
    v = {"id": id, "name": f"Bus #{id}", "unit": id, "vtype": "road", "engine": 116, "engine_name": "MPS Regal Bus",
         "tile": 1972, "x": 52, "y": 15, "state": "running", "speed": 31, "age": 400, "max_age": 4392,
         "reliability": 81, "profit_this_year": 1_200, "profit_last_year": 900, "value": 4_000, "running_cost": 426,
         "cargo": 0, "capacity": 31, "load": 12, "orders": 2, "cur_order": 1, "in_depot": False, "stopped": False}
    v.update(kw)
    return v


def station(id, **kw):
    s = {"id": id, "name": f"Fedborough Stop {id}", "tile": 1328, "x": 48, "y": 10, "types": ["bus"],
         "cargo": [{"cargo": 0, "waiting": 45, "rating": 67}], "vehicles": 1, "town": 0, "radius": 3}
    s.update(kw)
    return s


# ---------------------------------------------------------------------------------------------- money
@pytest.mark.parametrize("v,s", [(12345, "£12,345"), (-1234, "-£1,234"), (0, "£0"), (1_000_000, "£1,000,000"),
                                 (None, "-")])
def test_money_formats_pounds_with_thousands_separators(v, s):
    assert ob.money(v) == s


def test_cargo_labels_maps_ids_to_labels():
    assert ob.cargo_labels([{"id": 0, "label": "PASS"}, {"id": 1, "label": "COAL"}]) == {0: "PASS", 1: "COAL"}


# ---------------------------------------------------------------------------------------------- format_table
def test_format_table_aligns_columns_and_formats_values():
    rows = [{"id": 1, "name": "Alpha", "money": 1500, "ok": True, "types": ["bus", "truck"], "cargo": 1},
            {"id": 22, "name": "B", "money": -20, "ok": False, "types": [], "cargo": None}]
    out = ob.format_table(rows, ["id", "name", "money", "ok", "types", "cargo"], labels=LABELS)
    lines = out.splitlines()
    assert lines[0].split() == ["id", "name", "money", "ok", "types", "cargo"]
    assert lines[1].split() == ["1", "Alpha", "£1,500", "yes", "bus,truck", "COAL"]
    assert lines[2].split() == ["22", "B", "-£20", "no", "-", "-"]
    assert len({lines[0].index("money"), lines[1].index("£1,500"), lines[2].index("-£20")}) == 1


def test_format_table_pages_and_reports_position():
    rows = [{"id": i} for i in range(45)]
    out = ob.format_table(rows, ["id"], page=2, page_size=20)
    body = out.splitlines()[1:-1]
    assert [int(x) for x in body] == list(range(20, 40))
    assert "page 2/3" in out and "rows 21-40 of 45" in out


def test_format_table_clamps_page_and_handles_empty():
    rows = [{"id": i} for i in range(5)]
    assert "page 1/1" in ob.format_table(rows, ["id"], page=9)
    assert ob.format_table([], ["id"]) == "(none)"


def test_format_table_defaults_fields_to_first_row_keys():
    out = ob.format_table([{"a": 1, "b": 2}], None)
    assert out.splitlines()[0].split() == ["a", "b"]


def test_format_table_truncates_to_byte_budget():
    rows = [{"id": i, "name": "x" * 100} for i in range(50)]
    out = ob.format_table(rows, ["id", "name"], page_size=50, max_bytes=1000)
    assert len(out.encode()) <= 1000
    assert "truncated" in out


# ---------------------------------------------------------------------------------------------- format_status
def test_format_status_shows_date_money_fleet_and_stations():
    vehicles = [vehicle(1), vehicle(2, state="at_station"), vehicle(3, state="in_depot", in_depot=True, stopped=True)]
    out = ob.format_status(MAP_INFO, COMPANY, vehicles, [station(7)])
    assert "1950-02-10" in out and "Fedborough Transport" in out and "128x128 temperate" in out
    assert "£95,123" in out and "£100,000" in out and "£300,000" in out
    assert "income £7,000" in out and "rating 87" in out
    assert "Vehicles 3" in out and "1 running" in out and "1 at_station" in out and "1 in_depot" in out
    assert "Fedborough Stop 7" in out and "PASS 45" in out and "67%" in out
    assert len(out.encode()) <= 1536


def test_format_status_raises_alerts_for_problems():
    vehicles = [vehicle(1, state="crashed"), vehicle(2, orders=0, cur_order=-1),
                vehicle(3, profit_last_year=-2_000, age=800), vehicle(4, age=5000, max_age=4392),
                vehicle(5, state="stopped", stopped=True)]
    stations = [station(7, cargo=[{"cargo": 0, "waiting": 600, "rating": 31}])]
    company = dict(COMPANY, money=-5_000)
    out = ob.format_status(MAP_INFO, company, vehicles, stations)
    alerts = out[out.index("Alerts"):]
    assert "cash negative" in alerts
    assert "crashed: 1" in alerts
    assert "no orders: 2" in alerts
    assert "unprofitable last year: 3" in alerts
    assert "past max age: 4" in alerts
    assert "stopped: 5" in alerts
    assert "Fedborough Stop 7" in alerts and "600 PASS" in alerts


def test_format_status_stays_small_for_a_big_company():
    vehicles = [vehicle(i, profit_last_year=-1, age=900) for i in range(500)]
    stations = [station(i, cargo=[{"cargo": 1, "waiting": 900, "rating": 10}]) for i in range(150)]
    out = ob.format_status(MAP_INFO, COMPANY, vehicles, stations, labels=LABELS)
    assert len(out.encode()) <= 1536
    assert "Vehicles 500" in out and "Stations 150" in out


def test_format_status_without_vehicles_or_stations():
    out = ob.format_status(MAP_INFO, COMPANY, [], [])
    assert "Vehicles 0" in out and "Stations 0" in out and "Alerts: none" in out


# ---------------------------------------------------------------------------------------------- render_map
TILES = [
    {"y": 9, "x": 5, "kind": "..~~==", "height": "112000", "owner": "..**.o"},
    {"y": 10, "x": 5, "kind": ".TT=I ", "height": "1234a0", "owner": "......"},
    {"y": 11, "x": 5, "kind": "t^#SDB", "height": "zz1111", "owner": ".**..*"},
]


def test_render_map_has_rulers_labels_and_rows():
    out = ob.render_map(TILES, legend=False)
    lines = out.splitlines()
    # line 0: tens ruler with the label "10" above x=10; line 1: unit digits of every x
    margin = lines[2].index("..~~==")
    assert lines[1][margin:].rstrip() == "567890"
    assert lines[0][margin + 5:margin + 7] == "10"
    assert lines[2].split()[0] == "9" and lines[3].split()[0] == "10"
    assert lines[2][margin:] == "..~~==" and lines[3][margin:] == ".TT=I "
    assert len(lines) == 5


def test_render_map_legend_lists_only_present_kinds():
    out = ob.render_map(TILES)
    legend = out[out.index("Legend"):]
    for word in ("clear", "water", "road", "house", "industry", "trees", "rough", "rail", "station", "depot",
                 "bridge", "off-map"):
        assert word in legend
    assert "farmland" not in legend and "lock" not in legend


def test_render_map_height_and_owner_layers():
    h = ob.render_map(TILES, layer="height")
    assert "1234a0" in h and "base-36" in h
    o = ob.render_map(TILES, layer="owner")
    assert "..**.o" in o and "yours" in o and "other company" in o
    with pytest.raises(ValueError):
        ob.render_map(TILES, layer="weather")


def test_render_map_empty():
    assert ob.render_map([]) == "(empty map area)"


# ---------------------------------------------------------------------------------------------- format_tile
TILE = {"tile": 1969, "x": 49, "y": 15, "kind": "=", "height": 4, "max_height": 5, "slope": 12, "buildable": False,
        "terrain": "normal", "water": False, "coast": False, "river": False, "trees": False, "house": False,
        "road": True, "rail": False, "station": -1, "depot": False, "industry": -1, "town": 0,
        "closest_town": {"id": 0, "name": "Fedborough", "dist": 0}, "owner": -1, "mine": False, "radius": 3,
        "accepts": [{"cargo": 0, "label": "PASS", "amount": 55, "accepted": True},
                    {"cargo": 5, "label": "GOOD", "amount": 5, "accepted": False}],
        "produces": [{"cargo": 0, "label": "PASS", "producers": 12}],
        "industries": [{"id": 3, "name": "Fedborough Sawmill"}]}


def test_format_tile_summarises_terrain_ownership_and_catchment():
    out = ob.format_tile(TILE)
    assert "49,15" in out and "#1969" in out
    assert "road" in out and "height 4-5" in out and "slope NE" in out
    assert "not buildable" in out and "town/none" in out and "Fedborough" in out
    assert "PASS 55/8 accepted" in out and "GOOD 5/8 partial" in out
    assert "PASS x12" in out
    assert "Fedborough Sawmill (3)" in out
    assert len(out.encode()) <= 4096


@pytest.mark.parametrize("slope,name", [(0, "flat"), (8, "N"), (12, "NE"), (5, "EW"), (27, "steep NSW")])
def test_slope_names(slope, name):
    assert ob.slope_name(slope) == name


# ---------------------------------------------------------------------------------------------- vehicles / industries
def test_format_vehicles_resolves_cargo_and_pages():
    vs = [vehicle(i) for i in range(30)]
    out = ob.format_vehicles(vs, LABELS, page=1, page_size=10)
    assert "PASS 12/31" in out and "52,15" in out and "£1,200" in out and "1/2" in out
    assert "page 1/3" in out
    assert len(out.encode()) <= 4096


INDUSTRIES = [
    {"id": 0, "name": "Gutown Coal Mine", "type": 0, "type_name": "Coal Mine", "tile": 2275, "x": 99, "y": 17,
     "produces": [{"cargo": 1, "last_month": 135, "transported": 54, "pct": 40}], "accepts": [],
     "stations_around": 1, "dist": 12},
    {"id": 1, "name": "Senhattan Power Station", "type": 1, "type_name": "Power Station", "tile": 4674, "x": 66,
     "y": 36, "produces": [], "accepts": [1], "stations_around": 0, "dist": 40},
]


def test_format_industries_resolves_cargo_names():
    out = ob.format_industries(INDUSTRIES, LABELS)
    assert "COAL 135 (40%)" in out and "Gutown Coal Mine" in out and "99,17" in out
    lines = out.splitlines()
    assert "COAL" in lines[2] and "Senhattan Power Station" in lines[2]
    assert "dist" in lines[0]
    assert len(out.encode()) <= 4096
