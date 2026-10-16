"""Action registry: names, validation/coercion, function-calling schemas — no server needed."""

import json
import re
from pathlib import Path

import pytest

from railhead import actions
from railhead.actions import REGISTRY, WHITELIST, ActionArgError, to_gs, tool_schemas, validate

ACTION_NUT = Path(__file__).resolve().parents[2] / "gamescript/game/RailheadBridge/lib/action.nut"

# docs/DESIGN.md §3 vocabulary (world-changing verbs).
DESIGN_VERBS = {
    "rail": {"track", "line", "remove", "station", "depot", "signal", "remove_signal", "waypoint", "set_type"},
    "road": {"line", "one_way", "remove", "depot", "stop", "set_type"},
    "air": {"airport"},
    "water": {"dock", "canal", "lock", "ship_depot"},
    "bridge": {"build", "tunnel"},
    "land": {"raise", "lower", "level", "demolish"},
    "vehicle": {"buy", "sell", "start", "stop", "depot", "refit", "clone", "skip_order"},
    "order": {"append", "insert", "remove", "clear", "share"},
    "company": {"loan", "rename"},
    "town": {"action"},
}


def test_registry_covers_the_design_vocabulary_exactly():
    expected = {f"{g}.{v}" for g, verbs in DESIGN_VERBS.items() for v in verbs}
    assert set(REGISTRY) == expected


def test_gs_method_is_group_underscore_verb():
    for name, spec in REGISTRY.items():
        assert spec.name == name
        assert spec.gs_method == name.replace(".", "_")
        assert spec.doc


def test_whitelist_is_exactly_the_player_actions():
    assert WHITELIST == frozenset(s.gs_method for s in REGISTRY.values())
    for m in ("step", "companies", "towns", "checksum", "bind_company", "rh_tile", "rh_engines"):
        assert m not in WHITELIST


def _nut_handlers() -> dict[str, str]:
    """GS handler name -> body text (functions inside the Action table)."""
    src = ACTION_NUT.read_text()
    starts = [(m.group(1), m.start()) for m in re.finditer(r"\n\tfunction (\w+)\(", src)]
    out = {}
    for i, (name, pos) in enumerate(starts):
        end = starts[i + 1][1] if i + 1 < len(starts) else len(src)
        out[name] = src[pos:end]
    return out


def _nut_params(handlers: dict[str, str], name: str) -> set[str]:
    body = handlers[name]
    if "Action.Begin(" not in body:  # delegating handler, e.g. H_road_line -> RoadSegment
        helper = re.search(r"Action\.(\w+)\(br, id, a", body).group(1)
        body = handlers[helper]
    spec = body[body.index("Action.Begin("):]
    spec = spec[: spec.index("]]);") + 2]
    return set(re.findall(r'\["(\w+)", ', spec))


def test_every_action_has_a_gs_handler_with_the_same_params():
    handlers = _nut_handlers()
    gs_actions = {n[2:] for n in handlers if n.startswith("H_") and not n.startswith("H_rh_")}
    assert gs_actions == set(WHITELIST)
    for spec in REGISTRY.values():
        assert _nut_params(handlers, "H_" + spec.gs_method) == {p.name for p in spec.params}, spec.name


def test_tiles_coerce_from_xy_strings_lists_and_ints():
    assert validate("rail.depot", {"tile": "5,7", "front_tile": [6, 7]}, map_size_x=128) == {
        "tile": 7 * 128 + 5, "front_tile": 7 * 128 + 6}
    assert validate("rail.depot", {"tile": 901, "front_tile": "902"}) == {"tile": 901, "front_tile": 902}


def test_xy_tile_needs_map_width_and_must_be_on_map():
    with pytest.raises(ActionArgError, match="map width"):
        validate("land.demolish", {"tile": "5,7"})
    with pytest.raises(ActionArgError, match="tile"):
        validate("land.demolish", {"tile": "200,7"}, map_size_x=128)
    with pytest.raises(ActionArgError, match="tile"):
        validate("land.demolish", {"tile": -1})


def test_unknown_and_missing_params_are_rejected_by_name():
    with pytest.raises(ActionArgError, match="unknown argument 'colour'"):
        validate("company.rename", {"name": "X", "colour": 3})
    with pytest.raises(ActionArgError, match="missing required argument 'name'"):
        validate("company.rename", {})
    with pytest.raises(ActionArgError, match="unknown action"):
        validate("rail.teleport", {})


def test_types_are_strict():
    with pytest.raises(ActionArgError, match="integer"):
        validate("company.loan", {"amount": "100000"})
    with pytest.raises(ActionArgError, match="integer"):
        validate("company.loan", {"amount": True})  # bool is not an int here
    with pytest.raises(ActionArgError, match="bool"):
        validate("vehicle.clone", {"depot_tile": 1, "vehicle": 2, "share_orders": 1})


def test_enums_and_flag_lists():
    assert validate("rail.station", {"tile": 1, "axis": "x", "platforms": 2, "length": 5})["axis"] == "x"
    with pytest.raises(ActionArgError, match="axis must be one of"):
        validate("rail.station", {"tile": 1, "axis": "z", "platforms": 2, "length": 5})
    out = validate("order.append", {"vehicle": 3, "tile": 10, "flags": ["full_load", "non_stop"]})
    assert out["flags"] == ["full_load", "non_stop"]
    with pytest.raises(ActionArgError, match="flags"):
        validate("order.append", {"vehicle": 3, "tile": 10, "flags": ["teleport"]})
    with pytest.raises(ActionArgError, match="flags"):
        validate("order.append", {"vehicle": 3, "tile": 10, "flags": "full_load"})


def test_bounds():
    with pytest.raises(ActionArgError, match="platforms"):
        validate("rail.station", {"tile": 1, "axis": "x", "platforms": 0, "length": 5})
    with pytest.raises(ActionArgError, match="amount"):
        validate("company.loan", {"amount": -5})


def test_defaults_are_made_explicit_for_the_log():
    out = validate("road.stop", {"tile": 1, "front_tile": 2})
    assert out == {"tile": 1, "front_tile": 2, "kind": "bus", "drive_through": False}
    assert "join" not in out  # optional without default stays absent


def test_cargo_accepts_id_or_label():
    assert validate("vehicle.refit", {"vehicle": 1, "cargo": 0})["cargo"] == 0
    assert validate("vehicle.refit", {"vehicle": 1, "cargo": "pass"})["cargo"] == "PASS"
    with pytest.raises(ActionArgError, match="cargo"):
        validate("vehicle.refit", {"vehicle": 1, "cargo": "PASSENGERS"})


def test_dry_run_is_kept_only_when_true():
    assert validate("land.demolish", {"tile": 5, "dry_run": True}) == {"tile": 5, "dry_run": True}
    assert validate("land.demolish", {"tile": 5, "dry_run": False}) == {"tile": 5}
    with pytest.raises(ActionArgError, match="dry_run"):
        validate("land.demolish", {"tile": 5, "dry_run": "yes"})


def test_to_gs_accepts_canonical_or_gs_names():
    assert to_gs("road.line", {"from_tile": "1,1", "to_tile": "4,1"}, map_size_x=64) == (
        "road_line", {"from_tile": 65, "to_tile": 68})
    assert to_gs("road_line", {"from_tile": 65, "to_tile": 68})[0] == "road_line"


def test_validation_does_not_mutate_input():
    args = {"tile": "1,2", "front_tile": "1,3"}
    validate("road.depot", args, map_size_x=16)
    assert args == {"tile": "1,2", "front_tile": "1,3"}


@pytest.mark.parametrize("style", ["anthropic", "openai"])
def test_tool_schemas_are_valid_function_calling_specs(style):
    tools = tool_schemas(style)
    assert len(tools) == len(REGISTRY)
    json.dumps(tools)  # serialisable
    for t in tools:
        if style == "anthropic":
            name, desc, schema = t["name"], t["description"], t["input_schema"]
        else:
            assert t["type"] == "function"
            name, desc, schema = t["function"]["name"], t["function"]["description"], t["function"]["parameters"]
        assert re.fullmatch(r"[a-zA-Z0-9_-]{1,64}", name)
        assert desc
        assert schema["type"] == "object"
        assert schema["additionalProperties"] is False
        spec = actions.get(name)
        assert set(schema["required"]) == {p.name for p in spec.params if p.required}
        assert set(schema["properties"]) == {p.name for p in spec.params} | {"dry_run"}
        for prop in schema["properties"].values():
            assert prop.get("description")


def test_tool_schema_types():
    tools = {t["name"]: t["input_schema"]["properties"] for t in tool_schemas("anthropic")}
    st = tools["rail_station"]
    assert st["axis"]["enum"] == ["x", "y"]
    assert st["platforms"]["type"] == "integer" and st["platforms"]["minimum"] == 1
    assert {"type": "string", "pattern": r"^\d+,\d+$"} in [
        {k: v for k, v in s.items() if k in ("type", "pattern")} for s in st["tile"]["anyOf"]]
    fl = tools["order_append"]["flags"]
    assert fl["type"] == "array" and "full_load" in fl["items"]["enum"]
    assert tools["vehicle_clone"]["share_orders"]["type"] == "boolean"


def test_unknown_style_rejected():
    with pytest.raises(ValueError):
        tool_schemas("xml")


# Every error a 15.3 GameScript command can report (ErrorMessages enums in src/script/api/script_*.hpp),
# except ERR_NONE (success).
GS_ERRORS_15_3 = {"ERR_UNKNOWN", "ERR_PRECONDITION_FAILED", "ERR_PRECONDITION_STRING_TOO_LONG", "ERR_PRECONDITION_INVALID_COMPANY", "ERR_NEWGRF_SUPPLIED_ERROR", "ERR_NOT_ENOUGH_CASH", "ERR_LOCAL_AUTHORITY_REFUSES", "ERR_ALREADY_BUILT", "ERR_AREA_NOT_CLEAR", "ERR_OWNED_BY_ANOTHER_COMPANY", "ERR_NAME_IS_NOT_UNIQUE", "ERR_FLAT_LAND_REQUIRED", "ERR_LAND_SLOPED_WRONG", "ERR_VEHICLE_IN_THE_WAY", "ERR_SITE_UNSUITABLE", "ERR_TOO_CLOSE_TO_EDGE", "ERR_STATION_TOO_SPREAD_OUT", "ERR_BRIDGE_TOO_LOW", "ERR_BRIDGE_CANNOT_END_IN_WATER", "ERR_BRIDGE_HEADS_NOT_ON_SAME_HEIGHT", "ERR_BRIDGE_TYPE_UNAVAILABLE", "ERR_MARINE_MUST_BE_BUILT_ON_WATER", "ERR_ORDER_AIRCRAFT_NOT_ENOUGH_RANGE", "ERR_ORDER_TOO_FAR_AWAY_FROM_PREVIOUS_DESTINATION", "ERR_ORDER_TOO_MANY", "ERR_CROSSING_ON_ONEWAY_ROAD", "ERR_RAILTYPE_DISALLOWS_CROSSING", "ERR_UNSUITABLE_TRACK", "ERR_ROAD_CANNOT_BUILD_ON_TOWN_ROAD", "ERR_ROAD_DRIVE_THROUGH_WRONG_DIRECTION", "ERR_ROAD_ONE_WAY_ROADS_CANNOT_HAVE_JUNCTIONS", "ERR_ROAD_WORKS_IN_PROGRESS", "ERR_ROADTYPE_DISALLOWS_CROSSING", "ERR_UNSUITABLE_ROAD", "ERR_SIGN_TOO_MANY_SIGNS", "ERR_STATION_TOO_CLOSE_TO_ANOTHER_STATION", "ERR_STATION_TOO_MANY_STATIONS", "ERR_STATION_TOO_MANY_STATIONS_IN_TOWN", "ERR_AREA_ALREADY_FLAT", "ERR_EXCAVATION_WOULD_DAMAGE", "ERR_LIMIT_REACHED", "ERR_TILE_TOO_HIGH", "ERR_TILE_TOO_LOW", "ERR_TUNNEL_ANOTHER_TUNNEL_IN_THE_WAY", "ERR_TUNNEL_CANNOT_BUILD_ON_WATER", "ERR_TUNNEL_END_SITE_UNSUITABLE", "ERR_TUNNEL_START_SITE_UNSUITABLE", "ERR_VEHICLE_BUILD_DISABLED", "ERR_VEHICLE_CANNOT_REFIT", "ERR_VEHICLE_CANNOT_SEND_TO_DEPOT", "ERR_VEHICLE_CANNOT_START_STOP", "ERR_VEHICLE_CANNOT_TURN", "ERR_VEHICLE_IN_FLIGHT", "ERR_VEHICLE_IS_DESTROYED", "ERR_VEHICLE_NO_POWER", "ERR_VEHICLE_NOT_AVAILABLE", "ERR_VEHICLE_NOT_IN_DEPOT", "ERR_VEHICLE_TOO_LONG", "ERR_VEHICLE_TOO_MANY", "ERR_VEHICLE_WRONG_DEPOT", "ERR_WAYPOINT_ADJOINS_MULTIPLE_WAYPOINTS", "ERR_WAYPOINT_TOO_CLOSE_TO_ANOTHER_WAYPOINT"}


def test_every_gs_error_has_a_fix_hint():
    src = ACTION_NUT.read_text()
    hints = src[src.index("HINTS = {"):src.index("},", src.index("HINTS = {"))]
    keys = set(re.findall(r"\b(ERR_[A-Z_]+) = \"", hints))
    assert GS_ERRORS_15_3 <= keys, sorted(GS_ERRORS_15_3 - keys)
