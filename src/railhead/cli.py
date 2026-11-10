"""``rh`` — the only interface a model needs to play OpenTTD through Railhead (works in plain bash).

    rh status | brief | help
    rh towns | industries | stations | vehicles | engines vtype=road | company | map x=0 y=0 w=40 h=20 | tile tile=10,4
    rh <group> <verb> key=value ...        e.g.  rh road line from_tile=10,4 to_tile=30,4
    rh wait days=30 [until=vehicle_crashed,cash_below:5000]
    rh fork create name=a | fork do name=a m=road_line from_tile=.. | fork wait name=a days=60 | fork drop name=a
    rh fork q name=a m=company                          (read a fork's state)
    rh forecast|promise|abstain|flag|submit key=value ...
    rh q <query> k=v ... | rh do <gs_method> k=v ...     (raw JSON escape hatches)
Tiles are written x,y. Add --json for machine-readable output.
"""

from __future__ import annotations

import json
import os
import sys
from typing import Any

from railhead.client import Client, DaemonError

QUERY_COMMANDS = {
    "towns", "industries", "stations", "vehicles", "engines", "cargos", "company", "depots", "orders",
    "tile", "tiles", "subsidies", "catchment", "companies", "monitor_read", "map_info",
}
META_COMMANDS = {"forecast", "promise", "abstain", "flag", "submit"}


META_HELP = [
    "rh wait days=N [until=vehicle_crashed,industry_close,cash_below:N,new_month,...]   (or ticks=N)",
    "rh promise quantity=N                       commit to a delivery quantity (bid tasks)",
    "rh forecast metric=NAME p50=N [p10=N p90=N by_day=N]",
    "rh abstain reason=\"...\" [max_possible=N]   declare the contract impossible",
    "rh flag note=\"...\"                          report an environment problem",
    "rh submit confirm=yes                       end your mandate (irreversible; the world runs on)",
]
INFO_HELP = [
    "rh status | brief | info | history [n=30]",
    "rh towns | industries | stations | vehicles | depots [vtype=road] | company | cargos | subsidies",
    "rh engines vtype=road|rail|air|water        vehicles you can buy",
    "rh map x=0 y=0 w=48 h=24 [layer=kind|height|owner|slope|build|dock]   (layer=dock: d = a dock fits)",
    "rh map x=0 y=0 w=512 h=512 step=8           overview: every 8th tile (the minimap; finds lakes and sea)",
    "rh tile tile=X,Y [radius=3]   one tile: slope, dock fit, rail pieces, signals, catchment",
    "rh catchment tile=X,Y w=W h=H radius=R     what a station of W x H tiles accepts and gets (train R=4, dock 5)",
    "rh orders vehicle=N",
    "rh fork create|list|drop name=F | fork do name=F m=<gs_method> ... | fork wait name=F days=N",
    "rh fork q name=F m=company|vehicles|stations ...   read the state of a fork (same queries as above)",
    "rh q <query> k=v | rh do <gs_method> k=v    raw JSON escape hatches;  add --json to any command",
]


#: Minimal working recipes, shown by `rh help rail` / `rh help water` (docs/USAGE.md has the long form).
RECIPES: dict[str, list[str]] = {
    "rail": [
        "RECIPE: one coal train (about 20 actions; measured payback 0.7 years from 1960, 1.2 years from 1990)",
        " 1 rh industries cargo=1 role=produces  and  ... role=accepts   (rh cargos: COAL is 1 in temperate)",
        "   choose a mine and a power station 20-60 tiles apart, with flat clear land between them",
        " 2 rh catchment tile=X,Y w=3 h=1 radius=4   (w=1 h=3 for axis=y): the station footprint must",
        "   show COAL in 'produces' at the mine and COAL accepted at the power station",
        " 3 rh rail station tile=X,Y axis=x platforms=1 length=3 join_station=-1   (at each end; flat land)",
        " 4 track: rh rail line from_tile=A to_tile=B for each straight run (same x or same y), starting on",
        "   the tile next to a platform end. At a turn put ONE corner piece: rh rail track tile=T track=P.",
        "   Tile sides: ne = x-1, sw = x+1, nw = y-1, se = y+1. P names the two sides the train uses:",
        "   nw_ne, sw_se, nw_sw or ne_se (e.g. arrive from x-1, leave to y+1: ne_se).",
        "   Keep 2 or more straight tiles between turns: OpenTTD forbids 90-degree turns.",
        " 5 rh rail depot tile=D front_tile=E   D = the tile behind a platform end E (it faces into the",
        "   platform). If the reply has a hint, the depot is not joined to track: fix the front tile.",
        " 6 rh engines vtype=rail cargo=1   a locomotive (wagon=no) and a wagon with carries=yes",
        " 7 rh vehicle buy depot_tile=D engine=LOCO   -> V;   4 times: rh vehicle buy depot_tile=D engine=WAGON",
        "   cargo=COAL -> W, then rh vehicle attach vehicle=W train_vehicle=V",
        " 8 rh order append vehicle=V tile=<mine station> flags='[\"full_load_any\"]'",
        "   rh order append vehicle=V tile=<power station> flags='[\"unload\",\"no_load\"]'",
        "   rh vehicle start vehicle=V",
        " 9 check: rh tile tile=X,Y shows the rail pieces on a tile; rh vehicles shows profit and state.",
        " One train per single track. A second train needs a passing loop and signals (rh rail signal).",
    ],
    "water": [
        "RECIPE: a passenger ferry between two towns on one lake or sea (about 13 actions)",
        " 1 rh map x=0 y=0 w=512 h=512 step=8   overview: find water (~) with towns (rh towns) on its shore",
        " 2 rh map x=X y=Y w=40 h=30 layer=dock   'd' marks tiles where a dock fits",
        "   rh tile tile=X,Y   'Dock: yes' and PASS accepted (a dock's catchment radius is 5)",
        " 3 rh water dock tile=A join_station=-1   and the same at B. Both docks must touch the same water:",
        "   ships cannot cross land or sloped river tiles (rapids: '1' in rh map layer=slope); locks pass them.",
        " 4 rh water ship_depot tile=T toward_tile=T2   T and the next tile in +x or +y must be flat open",
        "   water near dock A; T2 is any neighbour of T in x or y and only sets the direction",
        " 5 rh engines vtype=water cargo=0   a ferry (carries=yes)",
        " 6 rh vehicle buy depot_tile=T engine=E cargo=PASS -> V",
        "   rh order append vehicle=V tile=A;  rh order append vehicle=V tile=B;  rh vehicle start vehicle=V",
        " 7 more ferries: rh vehicle clone depot_tile=T vehicle=V share_orders=true, then rh vehicle start",
        " 1960 ferries are slow (32 km/h, 100 passengers): short crossings pay better than long voyages.",
    ],
}


def _param_usage(p) -> str:
    from railhead.actions import ENUM, FLAGS, TILE

    if p.type == TILE:
        v = "X,Y"
    elif p.type == ENUM:
        v = "|".join(p.choices)
    elif p.type == FLAGS:
        v = "'[" + ",".join(f'"{c}"' for c in p.choices[:3]) + ",...]'"
    else:
        v = p.type.upper()
    return f"{p.name}={v}" if p.required else f"[{p.name}={v}]"


def help_text(group: str | None = None) -> str:
    """Every player verb with its arguments, generated from the action registry (never out of date)."""
    from railhead.actions import REGISTRY

    groups: dict[str, list] = {}
    for name, spec in REGISTRY.items():
        g, v = name.split(".")
        groups.setdefault(g, []).append((v, spec))
    if group is not None and group not in groups:
        raise ValueError(f"unknown group {group!r}; groups: {', '.join(groups)}")
    lines = []
    if group is None:
        lines += ["Railhead: you play OpenTTD directly. Tiles are X,Y. The world is frozen except in `rh wait`.", "",
                  "LOOK"] + [f"  {x}" for x in INFO_HELP] + ["", "ACT (player commands; add dry_run=true to price one)"]
    for g, verbs in groups.items():
        if group is not None and g != group:
            continue
        for v, spec in verbs:
            usage = " ".join(_param_usage(p) for p in spec.params if p.name != "dry_run")
            lines.append(f"  rh {g} {v} {usage}".rstrip())
            if group is not None:
                lines.append(f"      {spec.doc}")
    if group is None:
        lines += ["", "TIME AND DECISIONS"] + [f"  {x}" for x in META_HELP]
        lines += ["", "Details for one group: rh help <group>   (e.g. rh help vehicle)",
                  "Route recipes (the minimal calls that work): rh help rail | rh help water"]
    elif group in RECIPES:
        lines += [""] + RECIPES[group]
    return "\n".join(lines)


def route(cmd: str, rest: list[str]) -> tuple[str, str]:
    """Decide whether `rh <cmd> <rest...>` is a player action or a query. A `<group> <verb>` that names
    a registry action always wins (e.g. `company` is both the finance query and an action group)."""
    from railhead.actions import REGISTRY

    if rest and not rest[0].startswith("-") and "=" not in rest[0] and f"{cmd}.{rest[0]}" in REGISTRY:
        return "act", f"{cmd}.{rest[0]}"
    if cmd == "map" or cmd in QUERY_COMMANDS:
        return "query", cmd
    return "other", cmd


def _is_tile_key(k: str) -> bool:
    return k == "tile" or k.endswith("_tile")


def parse_kv(pairs: list[str], map_x: int) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for p in pairs:
        if "=" not in p:
            raise ValueError(f"expected key=value, got {p!r}")
        k, v = p.split("=", 1)
        if _is_tile_key(k) and "," in v:
            xs, ys = v.split(",", 1)
            out[k] = int(ys) * map_x + int(xs)
            continue
        try:
            out[k] = json.loads(v)
        except json.JSONDecodeError:
            out[k] = v
    return out


def _client() -> Client:
    from railhead.daemon import ensure_up

    c = Client()
    try:
        c.request("ping")
    except (OSError, DaemonError):
        ensure_up(os.environ.get("RAILHEAD_TASK"))
    return c


def _print(result: Any, as_json: bool, formatter=None) -> None:
    if as_json or formatter is None:
        print(json.dumps(result, indent=None if as_json else 1, default=str))
    else:
        print(formatter(result))


def _labels(c: Client) -> dict[int, str]:
    from railhead.observe import cargo_labels

    return cargo_labels(c.request("query", m="cargos"))


def _status(c: Client, info: dict) -> str:
    from railhead.observe import format_status, money

    q = lambda m: c.request("query", m=m)  # noqa: E731
    text = format_status(q("map_info"), q("company"), q("vehicles"), q("stations"), labels=_labels(c))
    obj = info.get("objective") or {}
    lines = [text, "",
             f"task {info['task_id']} ({info['family']}): {info.get('title', '')}",
             f"days left {info['days_left']} of {info['horizon_days']} | run-off after: {info['runoff_days']} days | "
             f"fork-days left {info['fork_days_left']} | forks {info['forks_alive'] or '-'}"]
    if "monitor" in obj:
        done = next((m["total"] for m in c.request("query", m="monitor_read") if m["id"] == obj["monitor"]), 0)
        target = obj.get("quantity")
        lines.append(f"objective: {done:,} delivered to {obj.get('dest_name', obj.get('dest'))}"
                     + (f" / contract {target:,}" if target else ""))
    if "w_base" in obj:
        lines.append(f"baseline run-off net worth to beat: {money(obj['w_base'])}")
    if info.get("submitted"):
        lines.append("submitted: the world now runs without you")
    return "\n".join(lines)


def _render(cmd: str, result: Any, c: Client, args: dict) -> str | None:
    try:
        from railhead import observe
    except ImportError:
        return None
    if cmd == "map":
        return observe.render_map(result, layer=args.get("layer", "kind"))
    if cmd == "vehicles":
        return observe.format_vehicles(result, _labels(c), page=int(args.get("page", 1)))
    if cmd == "industries":
        return observe.format_industries(result, _labels(c), page=int(args.get("page", 1)))
    if cmd == "tile":
        return observe.format_tile(result, _labels(c))
    if cmd == "engines" and isinstance(result, list):
        fields = ["id", "name", "cargo", "capacity", "speed", "price", "running_cost", "reliability", "wagon", "power",
                  "rail_type", "carries", "refit_cargos"]
        return observe.format_table(result, [f for f in fields if result and f in result[0]],
                                    page=int(args.get("page", 1)), labels=_labels(c))
    if isinstance(result, list) and result and isinstance(result[0], dict):
        return observe.format_table(result, list(result[0].keys())[:10], page=int(args.get("page", 1)))
    return None


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    as_json = "--json" in argv
    argv = [a for a in argv if a != "--json"]
    if not argv or argv[0] in ("help", "-h", "--help"):
        try:
            print(help_text(argv[1] if len(argv) > 1 else None))
        except ValueError as e:
            print(f"ERR_BAD_ARG: {e}", file=sys.stderr)
            return 2
        return 0
    try:
        return _run(argv, as_json)
    except DaemonError as e:
        print(f"{e.code}: {e.message}", file=sys.stderr)
        return 1
    except ValueError as e:
        print(f"ERR_BAD_ARG: {e}", file=sys.stderr)
        return 2


def _run(argv: list[str], as_json: bool) -> int:
    c = _client()
    info = c.request("info")
    mx = int(info.get("map_x", 256))
    cmd, rest = argv[0], argv[1:]

    if cmd == "status":
        print(json.dumps(info) if as_json else _status(c, info))
        return 0
    if cmd in ("info", "brief"):
        if as_json or cmd == "info":
            _print(info, as_json)
        else:
            print(f"# {info.get('title') or info['task_id']}\n\n{info.get('briefing', '')}\n\n"
                  f"horizon {info['horizon_days']} days, run-off {info['runoff_days']} days, "
                  f"fork budget {info['fork_days_left']} days\n\n"
                  "Route recipes (the minimal calls that work): rh help rail | rh help water")
        return 0
    if cmd == "history":
        _print(c.request("history", **parse_kv(rest, mx)), as_json)
        return 0
    if cmd == "wait":
        a = parse_kv(rest, mx)
        if isinstance(a.get("until"), str):
            a["until"] = [u for u in a["until"].split(",") if u]
        out = c.request("wait", **a)
        if as_json:
            print(json.dumps(out))
        else:
            left = c.request("info")["days_left"]
            print(f"waited {out.get('days')} days -> {out.get('ymd')} (stopped because: {out.get('reason')}); "
                  f"{left} days left")
        return 0
    if cmd in META_COMMANDS:
        _print(c.request("meta", m=cmd, a=parse_kv(rest, mx)), as_json)
        return 0
    if cmd in ("q", "do") and not rest:
        raise ValueError(f"usage: rh {cmd} <{'query' if cmd == 'q' else 'gs_method'}> key=value ...")
    if cmd == "q":
        _print(c.request("query", m=rest[0], a=parse_kv(rest[1:], mx)), as_json)
        return 0
    if cmd == "do":
        _print(c.request("act", m=rest[0], a=parse_kv(rest[1:], mx)), as_json)
        return 0
    if cmd == "fork":
        sub, kv = rest[0], parse_kv(rest[1:], mx)
        name = kv.pop("name", "f")
        if sub in ("create", "list", "drop"):
            _print(c.request(f"fork_{sub}", name=name, **kv), as_json)
        elif sub == "wait":
            _print(c.request("fork_wait", name=name, **kv), as_json)
        elif sub in ("q", "do"):
            m = kv.pop("m")
            _print(c.request("fork_query" if sub == "q" else "fork_act", name=name, m=m, a=kv), as_json)
        else:
            raise ValueError(f"unknown fork subcommand {sub!r}")
        return 0
    if route(cmd, rest)[0] == "query":
        a = parse_kv(rest, mx)
        view = {k: a.pop(k) for k in ("layer", "page") if k in a}
        if cmd == "map":
            a.setdefault("x", 0)
            a.setdefault("y", 0)
            a.setdefault("w", 48)
            a.setdefault("h", 24)
        result = c.request("query", m="tiles" if cmd == "map" else cmd, a=a)
        text = None if as_json else _render(cmd, result, c, view)
        print(text if text is not None else json.dumps(result, indent=None if as_json else 1, default=str))
        return 0
    # rh <group> <verb> k=v ...  ->  registry action
    from railhead.actions import REGISTRY

    if not rest and any(n.startswith(cmd + ".") for n in REGISTRY):
        print(help_text(cmd))
        return 0
    if rest:
        from railhead.actions import to_gs

        method, args = to_gs(f"{cmd}.{rest[0]}", parse_kv(rest[1:], mx), map_size_x=mx)
        _print(c.request("act", m=method, a=args), as_json)
        return 0
    raise ValueError(f"unknown command {cmd!r}; try `rh help`")


if __name__ == "__main__":
    raise SystemExit(main())
