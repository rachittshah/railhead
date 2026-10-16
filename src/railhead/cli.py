"""``rh`` — the only interface a model needs to play OpenTTD through Railhead (works in plain bash).

    rh status | brief | help
    rh towns | industries | stations | vehicles | engines vtype=road | company | map x=0 y=0 w=40 h=20 | tile tile=10,4
    rh <group> <verb> key=value ...        e.g.  rh road line from_tile=10,4 to_tile=30,4
    rh wait days=30 [until=vehicle_crashed,cash_below:5000]
    rh fork create name=a | fork do name=a m=road_line from_tile=.. | fork wait name=a days=60 | fork drop name=a
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
    if isinstance(result, list) and result and isinstance(result[0], dict):
        return observe.format_table(result, list(result[0].keys())[:10], page=int(args.get("page", 1)))
    return None


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    as_json = "--json" in argv
    argv = [a for a in argv if a != "--json"]
    if not argv or argv[0] in ("help", "-h", "--help"):
        print(__doc__)
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
                  f"fork budget {info['fork_days_left']} days")
        return 0
    if cmd == "history":
        _print(c.request("history", **parse_kv(rest, mx)), as_json)
        return 0
    if cmd == "wait":
        a = parse_kv(rest, mx)
        if isinstance(a.get("until"), str):
            a["until"] = [u for u in a["until"].split(",") if u]
        _print(c.request("wait", **a), as_json)
        return 0
    if cmd in META_COMMANDS:
        _print(c.request("meta", m=cmd, a=parse_kv(rest, mx)), as_json)
        return 0
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
    if cmd == "map" or cmd in QUERY_COMMANDS:
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
    if rest:
        from railhead.actions import to_gs

        method, args = to_gs(f"{cmd}.{rest[0]}", parse_kv(rest[1:], mx), map_size_x=mx)
        _print(c.request("act", m=method, a=args), as_json)
        return 0
    raise ValueError(f"unknown command {cmd!r}; try `rh help`")


if __name__ == "__main__":
    raise SystemExit(main())
