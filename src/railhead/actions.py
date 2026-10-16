"""The model's action space: player commands for one company (docs/DESIGN.md §3).

One ``REGISTRY`` of :class:`ActionSpec` is the single source of truth for the CLI, the SDK, function
calling (:func:`tool_schemas`) and replay (:data:`WHITELIST`). Each spec maps a canonical name
(``"rail.line"``) to a GameScript handler (``H_rail_line`` in
``gamescript/game/RailheadBridge/lib/action.nut``), which issues the human player's command through the
engine's normal validation for the bound company.

:func:`validate` turns loose input into the canonical argument dict that is sent to the GS *and*
written to the action log: tiles become integer indices (``"x,y"`` → ``y * MapSizeX + x``), defaults
become explicit, unknown arguments are rejected. Replay re-sends exactly those logged arguments.

Every action also accepts ``dry_run`` (GSTestMode): the reply is ``{dry_run, cost, affordable}``
and nothing changes. Engine refusals come back as typed errors (``ERR_AREA_NOT_CLEAR`` …) whose
message names a likely fix.
"""

from __future__ import annotations

import re
from collections.abc import Iterable
from dataclasses import dataclass, field
from typing import Any

TILE = "tile"
INT = "int"
STR = "str"
BOOL = "bool"
ENUM = "enum"
FLAGS = "list[enum]"
CARGO = "cargo"
PARAM_TYPES = frozenset({TILE, INT, STR, BOOL, ENUM, FLAGS, CARGO})

_XY = re.compile(r"^\s*(\d+)\s*,\s*(\d+)\s*$")
_LABEL = re.compile(r"^[A-Za-z0-9_]{1,4}$")


class ActionArgError(ValueError):
    """Arguments rejected before reaching the game (same code the GS uses for bad arguments)."""

    code = "ERR_BAD_ARG"


@dataclass(frozen=True)
class Param:
    name: str
    type: str
    doc: str
    required: bool = True
    default: Any = None
    choices: tuple[str, ...] = ()
    minimum: int | None = None
    maximum: int | None = None

    def __post_init__(self) -> None:
        assert self.type in PARAM_TYPES, self.type
        assert not (self.required and self.default is not None), self.name
        assert bool(self.choices) == (self.type in (ENUM, FLAGS)), self.name


@dataclass(frozen=True)
class ActionSpec:
    name: str
    gs_method: str
    params: tuple[Param, ...]
    doc: str
    mutates: bool = True
    _by_name: dict[str, Param] = field(default_factory=dict, compare=False, repr=False)

    def __post_init__(self) -> None:
        self._by_name.update({p.name: p for p in self.params})

    def param(self, name: str) -> Param | None:
        return self._by_name.get(name)


# ---------------------------------------------------------------------------------------------- params
def _tile(name: str, doc: str, required: bool = True) -> Param:
    return Param(name, TILE, doc + ' Tile as "x,y" (or integer index y*MapSizeX+x).', required=required)


def _int(name: str, doc: str, required: bool = True, default: int | None = None, minimum: int | None = None,
         maximum: int | None = None) -> Param:
    return Param(name, INT, doc, required=required, default=default, minimum=minimum, maximum=maximum)


def _enum(name: str, choices: Iterable[str], doc: str, default: str | None = None) -> Param:
    return Param(name, ENUM, doc, required=default is None, default=default, choices=tuple(choices))


def _bool(name: str, doc: str, default: bool = False) -> Param:
    return Param(name, BOOL, doc, required=False, default=default)


VEHICLE = _int("vehicle", "Vehicle id (one of your primary vehicles).", minimum=0)
JOIN = _int("join_station", "Station to join: omit to join an adjacent own station (GUI default), a station id to "
            "join that station, or -1 to force a new station.", required=False, minimum=-1)

TRACKS = ("ne_sw", "nw_se", "nw_ne", "sw_se", "nw_sw", "ne_se")
SIGNALS = ("normal", "entry", "exit", "combo", "pbs", "pbs_oneway",
           "normal_twoway", "entry_twoway", "exit_twoway", "combo_twoway")
AIRPORTS = ("small", "large", "metropolitan", "international", "commuter", "intercon",
            "heliport", "helistation", "helidepot")
CORNERS = ("n", "e", "s", "w", "nw", "ne", "sw", "se", "ns", "ew", "all")
ORDER_FLAGS = ("full_load", "full_load_any", "unload", "transfer", "no_load", "no_unload",
               "non_stop", "via", "go_depot", "service", "stop_in_depot")
TOWN_ACTIONS = ("advertise_small", "advertise_medium", "advertise_large", "road_rebuild", "statue",
                "fund_buildings", "exclusive_rights", "bribe")

_TRACK_DOC = ("Track piece: ne_sw (along x), nw_se (along y), or a corner piece nw_ne (north), "
              "sw_se (south), nw_sw (west), ne_se (east).")
_FLAGS_DOC = ("Order flags. Station: non_stop, via (pass without stopping), one of unload|transfer|no_unload, "
              "one of full_load|full_load_any|no_load. Depot: non_stop, service (only if needed) or stop_in_depot, "
              "go_depot (nearest depot; tile ignored). Waypoint: non_stop, via.")


def _spec(name: str, doc: str, *params: Param) -> ActionSpec:
    return ActionSpec(name=name, gs_method=name.replace(".", "_"), params=tuple(params), doc=doc)


_SPECS: list[ActionSpec] = [
    # ---------------------------------------------------------------- rail
    _spec("rail.set_type", "Choose the rail type used by later rail builds (default: the first available type).",
          _int("railtype", "Rail type id (0 = normal rail in the base set).", minimum=0)),
    _spec("rail.track", "Build one rail track piece on a tile.",
          _tile("tile", "Tile to build on."), _enum("track", TRACKS, _TRACK_DOC)),
    _spec("rail.line", "Drag a straight or diagonal rail line from from_tile to to_tile inclusive, like the GUI.",
          _tile("from_tile", "First tile of the line."), _tile("to_tile", "Last tile of the line."),
          _tile("start_dir_tile", "Neighbour of from_tile the track enters from (sets the first piece). "
                "Default: the tile behind from_tile.", required=False)),
    _spec("rail.remove", "Remove one rail track piece from a tile.",
          _tile("tile", "Tile to remove from."), _enum("track", TRACKS, _TRACK_DOC)),
    _spec("rail.station", "Build a rail station (platforms x length tiles, north corner at tile).",
          _tile("tile", "North corner of the station."),
          _enum("axis", ("x", "y"), "Platform direction: x (north-east to south-west) or y (north-west to south-east)."),
          _int("platforms", "Number of parallel platforms.", minimum=1, maximum=255),
          _int("length", "Platform length in tiles.", minimum=1, maximum=255), JOIN),
    _spec("rail.depot", "Build a train depot whose entrance faces front_tile. Like the GUI, if front_tile already has "
          "track, the pieces joining the depot to it are added (reply: connected).",
          _tile("tile", "Depot tile (flat, clear)."), _tile("front_tile", "Neighbour the entrance faces.")),
    _spec("rail.signal", "Build a signal on a rail tile, facing trains that head toward front_tile.",
          _tile("tile", "Rail tile for the signal."), _tile("front_tile", "Neighbour in front of the signal."),
          _enum("type", SIGNALS, "Signal type (always electric).", default="pbs_oneway")),
    _spec("rail.remove_signal", "Remove the signal on tile that faces front_tile.",
          _tile("tile", "Rail tile with the signal."), _tile("front_tile", "Neighbour in front of the signal.")),
    _spec("rail.waypoint", "Build a rail waypoint on a tile with one straight track (axis follows the track).",
          _tile("tile", "Rail tile with a single straight track.")),
    # ---------------------------------------------------------------- road
    _spec("road.set_type", "Choose the road type used by later road builds (default: normal road).",
          _int("roadtype", "Road type id (0 = road).", minimum=0)),
    _spec("road.line", "Drag a straight road from the centre of from_tile to the centre of to_tile.",
          _tile("from_tile", "Start tile."), _tile("to_tile", "End tile (same x or same y).")),
    _spec("road.one_way", "Drag a one-way road from from_tile to to_tile (repeat to flip/clear the direction).",
          _tile("from_tile", "Start tile."), _tile("to_tile", "End tile (same x or same y).")),
    _spec("road.remove", "Remove road between the centres of from_tile and to_tile.",
          _tile("from_tile", "Start tile."), _tile("to_tile", "End tile (same x or same y).")),
    _spec("road.depot", "Build a road depot facing front_tile. Like the GUI, if front_tile is a road the missing "
          "piece into the depot is added (reply: connected); otherwise build road to front_tile yourself.",
          _tile("tile", "Depot tile (flat, clear)."), _tile("front_tile", "Neighbour the entrance faces.")),
    _spec("road.stop", "Build a bus or truck stop. Bay stops open toward front_tile; drive-through stops sit on a "
          "straight road along the tile->front_tile axis. Like the GUI, adjacent roads at the entrance(s) are joined "
          "to the stop (reply: connected).",
          _tile("tile", "Stop tile."), _tile("front_tile", "Neighbour giving the entrance/axis."),
          _enum("kind", ("bus", "truck"), "Stop for buses (passengers) or trucks (cargo).", default="bus"),
          _bool("drive_through", "Drive-through stop on a road instead of a bay stop."), JOIN),
    # ---------------------------------------------------------------- air / water
    _spec("air.airport", "Build an airport with its north corner at tile.",
          _tile("tile", "North corner of the airport."), _enum("type", AIRPORTS, "Airport type."), JOIN),
    _spec("water.dock", "Build a dock on a sloped coast tile (the land tile next to water).",
          _tile("tile", "Sloped land tile at the water's edge."), JOIN),
    _spec("water.canal", "Build a canal on a tile.", _tile("tile", "Tile to turn into canal.")),
    _spec("water.lock", "Build a lock on a straight sloped tile of a waterway.", _tile("tile", "Middle (sloped) tile of the lock.")),
    _spec("water.ship_depot", "Build a ship depot (2 tiles) on water, oriented toward toward_tile.",
          _tile("tile", "Water tile for the depot."), _tile("toward_tile", "Neighbour on the depot's axis.")),
    # ---------------------------------------------------------------- bridges / tunnels
    _spec("bridge.build", "Build a bridge between two tiles in a straight line (road bridges add road on both ends).",
          _tile("start_tile", "First bridge head."), _tile("end_tile", "Second bridge head (same x or same y)."),
          _enum("vehicle", ("rail", "road", "water"), "What the bridge carries.", default="road"),
          _int("type", "Bridge type id; default: the lowest id that spans this length.", required=False, minimum=0)),
    _spec("bridge.tunnel", "Build a tunnel starting at a straight sloped tile; it runs into the hill to the other side.",
          _tile("start_tile", "Tunnel entrance (sloped tile facing into the hill)."),
          _enum("vehicle", ("rail", "road"), "What the tunnel carries.", default="road")),
    # ---------------------------------------------------------------- land
    _spec("land.raise", "Raise corner(s) of a tile by one height level.",
          _tile("tile", "Tile to raise."), _enum("corner", CORNERS, "Corner(s) to raise; all = the whole tile.")),
    _spec("land.lower", "Lower corner(s) of a tile by one height level.",
          _tile("tile", "Tile to lower."), _enum("corner", CORNERS, "Corner(s) to lower; all = the whole tile.")),
    _spec("land.level", "Level the rectangle from_tile..to_tile to the height of from_tile's north corner.",
          _tile("from_tile", "Corner whose height is kept."), _tile("to_tile", "Opposite corner.")),
    _spec("land.demolish", "Demolish whatever is on a tile (like the dynamite tool).", _tile("tile", "Tile to clear.")),
    # ---------------------------------------------------------------- vehicles
    _spec("vehicle.buy", "Buy a vehicle in one of your depots; with cargo, buy it refitted to that cargo. "
          "It starts stopped: give orders, then vehicle.start.",
          _tile("depot_tile", "Your depot/hangar matching the engine type."),
          _int("engine", "Engine id (see rh engines).", minimum=0),
          Param("cargo", CARGO, "Cargo id or label (PASS, MAIL, COAL, ...) to refit to.", required=False)),
    _spec("vehicle.sell", "Sell a vehicle (it must be stopped inside a depot).", VEHICLE),
    _spec("vehicle.start", "Start a stopped vehicle (no-op if already running).", VEHICLE),
    _spec("vehicle.stop", "Stop a running vehicle (no-op if already stopped).", VEHICLE),
    _spec("vehicle.depot", "Send a vehicle to the nearest depot (repeat to cancel).", VEHICLE,
          _bool("service", "Only service there and continue, instead of stopping in the depot.")),
    _spec("vehicle.refit", "Refit a vehicle stopped in a depot to another cargo.", VEHICLE,
          Param("cargo", CARGO, "Cargo id or label (PASS, MAIL, COAL, ...).")),
    _spec("vehicle.clone", "Buy a copy of a vehicle in a depot, copying or sharing its orders.",
          _tile("depot_tile", "Your depot matching the vehicle type."), VEHICLE,
          _bool("share_orders", "Share the order list instead of copying it.")),
    _spec("vehicle.skip_order", "Make a vehicle head for order `index` next.", VEHICLE,
          _int("index", "Order index (0-based).", minimum=0)),
    # ---------------------------------------------------------------- orders
    _spec("order.append", "Append an order: go to the station/depot/waypoint at tile.", VEHICLE,
          _tile("tile", "Any tile of the destination station, depot or waypoint."),
          Param("flags", FLAGS, _FLAGS_DOC, required=False, default=(), choices=ORDER_FLAGS)),
    _spec("order.insert", "Insert an order at position index.", VEHICLE,
          _int("index", "Position (0 = first; = order count appends).", minimum=0),
          _tile("tile", "Any tile of the destination station, depot or waypoint."),
          Param("flags", FLAGS, _FLAGS_DOC, required=False, default=(), choices=ORDER_FLAGS)),
    _spec("order.remove", "Remove the order at position index.", VEHICLE, _int("index", "Order index (0-based).", minimum=0)),
    _spec("order.clear", "Delete all orders of a vehicle (a vehicle sharing orders just stops sharing).", VEHICLE),
    _spec("order.share", "Make vehicle share the order list of other.", VEHICLE,
          _int("other_vehicle", "Vehicle whose orders to share.", minimum=0)),
    # ---------------------------------------------------------------- company / town
    _spec("company.loan", "Set the loan to amount (a multiple of the loan interval, at most the max loan).",
          _int("amount", "New total loan.", minimum=0)),
    _spec("company.rename", "Rename your company.", Param("name", STR, "New company name.")),
    _spec("town.action", "Perform a town action (advertise, road rebuild, statue, fund buildings, exclusive rights, bribe).",
          _int("town", "Town id.", minimum=0), _enum("kind", TOWN_ACTIONS, "The action.")),
]

REGISTRY: dict[str, ActionSpec] = {s.name: s for s in _SPECS}
_BY_GS: dict[str, ActionSpec] = {s.gs_method: s for s in _SPECS}
WHITELIST: frozenset[str] = frozenset(s.gs_method for s in _SPECS if s.mutates)


def get(name: str) -> ActionSpec:
    """Look up by canonical name (``rail.line``) or GS method / tool name (``rail_line``)."""
    spec = REGISTRY.get(name) or _BY_GS.get(name)
    if spec is None:
        raise ActionArgError(f"unknown action {name!r}")
    return spec


# ---------------------------------------------------------------------------------------------- validation
def _coerce_tile(p: Param, v: Any, map_size_x: int | None) -> int:
    def xy(x: int, y: int) -> int:
        if map_size_x is None:
            raise ActionArgError(f"{p.name}: an x,y tile needs the map width (map_size_x)")
        if not (0 <= x < map_size_x and y >= 0):
            raise ActionArgError(f"{p.name}: tile {x},{y} is off the map (width {map_size_x})")
        return y * map_size_x + x

    if isinstance(v, bool):
        raise ActionArgError(f"{p.name}: tile must be 'x,y' or an integer index")
    if isinstance(v, int):
        if v < 0:
            raise ActionArgError(f"{p.name}: tile index must be >= 0")
        return v
    if isinstance(v, str):
        if v.strip().isdigit():
            return int(v)
        m = _XY.match(v)
        if m:
            return xy(int(m.group(1)), int(m.group(2)))
    if isinstance(v, (list, tuple)) and len(v) == 2 and all(isinstance(c, int) and not isinstance(c, bool) for c in v):
        return xy(v[0], v[1])
    raise ActionArgError(f"{p.name}: tile must be 'x,y' or an integer index, got {v!r}")


def _coerce(p: Param, v: Any, map_size_x: int | None) -> Any:
    if p.type == TILE:
        return _coerce_tile(p, v, map_size_x)
    if p.type == INT:
        if isinstance(v, bool) or not isinstance(v, int):
            raise ActionArgError(f"{p.name} must be an integer, got {v!r}")
        if p.minimum is not None and v < p.minimum:
            raise ActionArgError(f"{p.name} must be >= {p.minimum}, got {v}")
        if p.maximum is not None and v > p.maximum:
            raise ActionArgError(f"{p.name} must be <= {p.maximum}, got {v}")
        return v
    if p.type == STR:
        if not isinstance(v, str):
            raise ActionArgError(f"{p.name} must be a string")
        return v
    if p.type == BOOL:
        if not isinstance(v, bool):
            raise ActionArgError(f"{p.name} must be a bool (true/false), got {v!r}")
        return v
    if p.type == ENUM:
        if v not in p.choices:
            raise ActionArgError(f"{p.name} must be one of {'|'.join(p.choices)}, got {v!r}")
        return v
    if p.type == FLAGS:
        if not isinstance(v, (list, tuple)):
            raise ActionArgError(f"{p.name} must be a list of: {', '.join(p.choices)}")
        bad = [f for f in v if f not in p.choices]
        if bad:
            raise ActionArgError(f"{p.name}: unknown flag(s) {bad}; known: {', '.join(p.choices)}")
        return list(dict.fromkeys(v))
    if p.type == CARGO:
        if isinstance(v, int) and not isinstance(v, bool) and v >= 0:
            return v
        if isinstance(v, str) and _LABEL.match(v):
            return v.upper()
        raise ActionArgError(f"{p.name}: cargo must be a cargo id or a 4-letter label like PASS, got {v!r}")
    raise AssertionError(p.type)


def validate(name: str, args: dict[str, Any] | None, map_size_x: int | None = None) -> dict[str, Any]:
    """Canonical args for ``name``: tiles as indices, explicit defaults, ``dry_run`` only when true.

    Raises :class:`ActionArgError` naming the offending argument.
    """
    spec = get(name)
    args = dict(args or {})
    dry = args.pop("dry_run", False)
    if not isinstance(dry, bool):
        raise ActionArgError("dry_run must be a bool")
    for k in args:
        if spec.param(k) is None:
            known = ", ".join(p.name for p in spec.params) or "none"
            raise ActionArgError(f"{spec.name}: unknown argument '{k}' (arguments: {known})")
    out: dict[str, Any] = {}
    for p in spec.params:
        v = args.get(p.name)
        if v is None:
            if p.required:
                raise ActionArgError(f"{spec.name}: missing required argument '{p.name}'")
            if p.default is not None:
                out[p.name] = list(p.default) if isinstance(p.default, tuple) else p.default
            continue
        out[p.name] = _coerce(p, v, map_size_x)
    if dry:
        out["dry_run"] = True
    return out


def to_gs(name: str, args: dict[str, Any] | None, map_size_x: int | None = None) -> tuple[str, dict[str, Any]]:
    """(GS method, canonical args) ready for ``world.call(method, **args)`` and the action log."""
    return get(name).gs_method, validate(name, args, map_size_x)


# ---------------------------------------------------------------------------------------------- schemas
def _prop(p: Param) -> dict[str, Any]:
    d: dict[str, Any]
    if p.type == TILE:
        d = {"anyOf": [{"type": "string", "pattern": r"^\d+,\d+$"}, {"type": "integer", "minimum": 0}]}
    elif p.type == INT:
        d = {"type": "integer"}
        if p.minimum is not None:
            d["minimum"] = p.minimum
        if p.maximum is not None:
            d["maximum"] = p.maximum
    elif p.type == STR:
        d = {"type": "string"}
    elif p.type == BOOL:
        d = {"type": "boolean"}
    elif p.type == ENUM:
        d = {"type": "string", "enum": list(p.choices)}
    elif p.type == FLAGS:
        d = {"type": "array", "items": {"type": "string", "enum": list(p.choices)}, "uniqueItems": True}
    else:  # CARGO
        d = {"anyOf": [{"type": "string", "pattern": r"^[A-Za-z0-9_]{1,4}$"}, {"type": "integer", "minimum": 0}]}
    d["description"] = p.doc
    if p.default is not None:
        d["default"] = list(p.default) if isinstance(p.default, tuple) else p.default
    return d


def _input_schema(spec: ActionSpec) -> dict[str, Any]:
    props = {p.name: _prop(p) for p in spec.params}
    props["dry_run"] = {"type": "boolean", "default": False,
                        "description": "Only price the action (GSTestMode): returns cost and whether you can afford it; nothing changes."}
    return {"type": "object", "properties": props,
            "required": [p.name for p in spec.params if p.required], "additionalProperties": False}


def tool_schemas(style: str = "anthropic") -> list[dict[str, Any]]:
    """Function-calling tool definitions for every action (tool name = GS method, e.g. ``rail_line``)."""
    out = []
    for spec in _SPECS:
        desc = f"{spec.name}: {spec.doc}"
        schema = _input_schema(spec)
        if style == "anthropic":
            out.append({"name": spec.gs_method, "description": desc, "input_schema": schema})
        elif style == "openai":
            out.append({"type": "function", "function": {"name": spec.gs_method, "description": desc, "parameters": schema}})
        else:
            raise ValueError(f"unknown tool schema style {style!r} (anthropic|openai)")
    return out
