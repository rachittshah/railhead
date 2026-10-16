"""Compact text views of Railhead query results, for a model reading a terminal.

Pure functions: each takes rows exactly as the GameScript queries return them
(gamescript/game/RailheadBridge/lib/query.nut) and returns plain text of at most ``MAX_BYTES``
(4 KB) by default; long lists page (``page``/``page_size``) and say where they are.

    labels = cargo_labels(world.call("cargos"))
    print(format_status(world.call("map_info"), world.call("company"),
                        world.call("vehicles"), world.call("stations"), labels=labels))
    print(render_map(world.call("tiles", x=0, y=0, w=60, h=50)))
"""

from __future__ import annotations

import math
from collections import Counter
from collections.abc import Iterable, Mapping, Sequence
from typing import Any

MAX_BYTES = 4096
STATUS_MAX_BYTES = 1536

#: ``tiles`` row layers: each is a string with one char per tile, starting at column ``x``.
#: Consumers (oracle planner, CLI map) rely on these keys plus ``y`` and ``x``.
MAP_LAYERS = ("kind", "height", "owner", "slope", "build")

#: ``kind`` layer: what is on the tile. Mirrors QH.Kind in gamescript/.../lib/query.nut.
#: ``.``/``t``/``f``/``^``/``,`` and a lone town road piece are buildable (see the ``build`` layer).
KIND_LEGEND: dict[str, str] = {
    " ": "off-map", ".": "clear", "^": "rough/rocks", "f": "farmland", "t": "trees", "~": "water", ",": "coast",
    "L": "lock", "T": "house", "I": "industry", "=": "road", "#": "rail", "+": "level crossing", "S": "station",
    "W": "rail waypoint", "D": "depot", "B": "bridge/tunnel end", "O": "other structure",
}
#: ``owner`` layer.
OWNER_LEGEND: dict[str, str] = {"*": "yours", "o": "other company", ".": "town/none"}
#: ``slope`` layer: ``1`` needs a foundation; avoid ``s`` (most things cannot be built on steep slopes).
SLOPE_LEGEND: dict[str, str] = {"0": "flat", "1": "sloped", "s": "steep"}
#: ``build`` layer: GSTile.IsBuildable (nothing to demolish first).
BUILD_LEGEND: dict[str, str] = {"1": "buildable", "0": "occupied/water/off-map"}
#: ``height`` layer: base-36 height of the tile's lowest corner (0-9, then a=10 ... z=35).
HEIGHT_LEGEND = "height of the lowest corner, base-36 (0-9, then a=10 ... z=35)"
_LAYER_LEGENDS: dict[str, dict[str, str]] = {
    "kind": KIND_LEGEND, "owner": OWNER_LEGEND, "slope": SLOPE_LEGEND, "build": BUILD_LEGEND,
}

#: Integer fields rendered as money by ``format_table``.
MONEY_FIELDS = frozenset({
    "money", "loan", "max_loan", "loan_interval", "value", "price", "running_cost", "profit_this_year",
    "profit_last_year", "income", "expenses", "cost",
})
FLEET_ORDER = ("running", "at_station", "broken", "stopped", "in_depot", "crashed")


# ---------------------------------------------------------------------------------------------- scalars
def money(v: int | None) -> str:
    """£12,345 / -£1,234; ``-`` when unknown."""
    if v is None:
        return "-"
    s = f"£{abs(int(v)):,}"
    return "-" + s if v < 0 else s


def cargo_labels(cargos: Iterable[Mapping[str, Any]]) -> dict[int, str]:
    """Cargo id -> label (``PASS``, ``COAL``...) from the ``cargos`` query."""
    return {c["id"]: c["label"] for c in cargos}


def label(cargo: int | None, labels: Mapping[int, str] | None) -> str:
    if cargo is None or cargo < 0:
        return "-"
    return (labels or {}).get(cargo, f"#{cargo}")


def date_str(info: Mapping[str, Any]) -> str:
    return f"{info['year']:04d}-{info['month']:02d}-{info['day']:02d}"


def slope_name(slope: int) -> str:
    """Raised corners of a tile slope (GSTile.Slope bits N=8 E=4 S=2 W=1, steep=16)."""
    if slope == 0:
        return "flat"
    if slope < 0 or slope > 31:
        return "invalid"
    corners = "".join(n for bit, n in ((8, "N"), (4, "E"), (2, "S"), (1, "W")) if slope & bit)
    return ("steep " if slope & 16 else "") + corners


def _ids(ids: Sequence[Any], k: int = 8) -> str:
    head = ", ".join(str(i) for i in ids[:k])
    return head + (f" (+{len(ids) - k} more)" if len(ids) > k else "")


def _fit(text: str, max_bytes: int) -> str:
    """Hard byte cap (UTF-8 safe) as a last resort."""
    data = text.encode()
    if len(data) <= max_bytes:
        return text
    return data[: max_bytes - 4].decode(errors="ignore").rstrip() + "\n..."


# ---------------------------------------------------------------------------------------------- tables
def _cell(field: str, v: Any, labels: Mapping[int, str] | None) -> str:
    if v is None:
        return "-"
    if isinstance(v, bool):
        return "yes" if v else "no"
    if isinstance(v, int) and field in MONEY_FIELDS:
        return money(v)
    if isinstance(v, int) and field == "cargo":
        return label(v, labels)
    if isinstance(v, (list, tuple)):
        return ",".join(_cell(field, x, labels) for x in v) or "-"
    if isinstance(v, dict):
        return " ".join(f"{k}={_cell(k, x, labels)}" for k, x in v.items())
    return str(v)


def format_table(
    rows: Sequence[Mapping[str, Any]],
    fields: Sequence[str] | None = None,
    page: int = 1,
    page_size: int = 20,
    *,
    labels: Mapping[int, str] | None = None,
    title: str | None = None,
    max_bytes: int = MAX_BYTES,
) -> str:
    """Left-aligned text table of ``fields`` (default: the first row's keys), one page at a time.

    Money fields print as £, ``cargo`` ids as labels, bools as yes/no, lists comma-joined, None as ``-``.
    The last line always says ``page p/P, rows a-b of N``; rows that do not fit ``max_bytes`` are
    dropped from the end of the page and the footer says so.
    """
    head = [title] if title else []
    if not rows:
        return "\n".join(head + ["(none)"])
    fields = list(fields or rows[0].keys())
    total = len(rows)
    pages = max(1, math.ceil(total / page_size))
    page = min(max(1, page), pages)
    start = (page - 1) * page_size
    chunk = rows[start : start + page_size]
    cells = [[_cell(f, r.get(f), labels) for f in fields] for r in chunk]
    widths = [max([len(f)] + [len(c[i]) for c in cells]) for i, f in enumerate(fields)]

    def line(cs: Sequence[str]) -> str:
        return "  ".join(c.ljust(w) for c, w in zip(cs, widths)).rstrip()

    body = [line(c) for c in cells]
    footer = f"page {page}/{pages}, rows {start + 1}-{start + len(body)} of {total}"
    text = "\n".join(head + [line(fields)] + body + [footer])
    while len(text.encode()) > max_bytes and len(body) > 1:
        body.pop()
        footer = (f"page {page}/{pages}, rows {start + 1}-{start + len(body)} of {total} "
                  f"(truncated to fit {max_bytes} bytes: use a smaller page_size or fewer fields)")
        text = "\n".join(head + [line(fields)] + body + [footer])
    return _fit(text, max_bytes)


# ---------------------------------------------------------------------------------------------- status
def _station_cargo(st: Mapping[str, Any], labels: Mapping[int, str] | None) -> str:
    parts = []
    for c in sorted(st.get("cargo", []), key=lambda c: -c["waiting"])[:3]:
        rating = "-" if c.get("rating") is None else f"{c['rating']}%"
        parts.append(f"{label(c['cargo'], labels)} {c['waiting']} {rating}")
    return ", ".join(parts) or "no cargo yet"


def _alerts(company: Mapping[str, Any], vehicles: Sequence[Mapping[str, Any]],
            stations: Sequence[Mapping[str, Any]], labels: Mapping[int, str] | None) -> list[str]:
    out: list[str] = []
    if company.get("money", 0) < 0:
        out.append(f"cash negative ({money(company['money'])}): raise the loan or cut costs")

    def ids(pred) -> list[int]:
        return [v["id"] for v in vehicles if pred(v)]

    checks = [
        ("crashed", lambda v: v["state"] == "crashed", " (sell them)"),
        ("no orders", lambda v: v.get("orders", 0) == 0 and v["state"] != "crashed", ""),
        ("stopped", lambda v: v["state"] == "stopped", " (start them)"),
        ("idle in depot", lambda v: v["state"] == "in_depot", " (start or sell them)"),
        ("unprofitable last year", lambda v: v.get("profit_last_year", 0) < 0 and v.get("age", 0) >= 365, ""),
        ("past max age", lambda v: 0 < v.get("max_age", 0) < v.get("age", 0), " (replace them)"),
    ]
    for name, pred, hint in checks:
        hit = ids(pred)
        if hit:
            out.append(f"{name}: {_ids(hit)}{hint}")
    piles = []
    for st in stations:
        for c in st.get("cargo", []):
            r = c.get("rating")
            if c["waiting"] >= 300 and r is not None and r < 50:
                piles.append((c["waiting"], st["name"], label(c["cargo"], labels), r))
    piles.sort(reverse=True)
    for waiting, name, lab, r in piles[:3]:
        out.append(f"{name}: {waiting} {lab} waiting, rating {r}% (add capacity)")
    if len(piles) > 3:
        out.append(f"(+{len(piles) - 3} more stations with piled-up cargo)")
    return out


def format_status(
    map_info: Mapping[str, Any],
    company: Mapping[str, Any],
    vehicles: Sequence[Mapping[str, Any]],
    stations: Sequence[Mapping[str, Any]],
    *,
    labels: Mapping[int, str] | None = None,
    max_bytes: int = STATUS_MAX_BYTES,
) -> str:
    """Dashboard (<= 1.5 KB): date, cash, loan, value, last quarter, fleet by state, busiest stations, alerts."""
    lines = [
        (f"{date_str(map_info)} | {company.get('name')} (#{company.get('id')}) | "
         f"{map_info['size_x']}x{map_info['size_y']} {map_info['climate']}"),
        (f"Cash {money(company.get('money'))} | Loan {money(company.get('loan'))} of "
         f"{money(company.get('max_loan'))} | Value {money(company.get('value'))}"),
    ]
    quarters = {q["q"]: q for q in company.get("quarters", [])}
    if 1 in quarters:
        q = quarters[1]
        lines.append(f"Last quarter: income {money(q['income'])}, expenses {money(q['expenses'])}, "
                     f"delivered {q['delivered']}, rating {q['rating']}")
    if 0 in quarters:
        q = quarters[0]
        lines.append(f"This quarter: income {money(q['income'])}, expenses {money(q['expenses'])}, "
                     f"delivered {q['delivered']}")
    states = Counter(v["state"] for v in vehicles)
    fleet = ", ".join(f"{states[s]} {s}" for s in (*FLEET_ORDER, *sorted(set(states) - set(FLEET_ORDER)))
                      if states[s])
    profit = sum(v.get("profit_this_year", 0) for v in vehicles)
    profit_ly = sum(v.get("profit_last_year", 0) for v in vehicles)
    lines.append(f"Vehicles {len(vehicles)}" + (f": {fleet} | profit this year {money(profit)}, "
                                                f"last year {money(profit_ly)}" if vehicles else ""))
    lines.append(f"Stations {len(stations)}" + (" (busiest first):" if stations else ""))
    busiest = sorted(stations, key=lambda s: -sum(c["waiting"] for c in s.get("cargo", [])))
    for st in busiest[:4]:
        lines.append(f"  {st['name']} ({st['x']},{st['y']}) {'/'.join(st.get('types', []))}: "
                     f"{_station_cargo(st, labels)}")
    if len(stations) > 4:
        lines.append(f"  (+{len(stations) - 4} more: see stations)")
    alerts = _alerts(company, vehicles, stations, labels)
    lines.append("Alerts:" + ("" if alerts else " none"))
    lines += [f"- {a}" for a in alerts]
    text = "\n".join(lines)
    while len(text.encode()) > max_bytes and len(lines) > 6:
        lines.pop()
        text = "\n".join(lines + ["- (more alerts truncated)"])
    return _fit(text, max_bytes)


# ---------------------------------------------------------------------------------------------- map
def render_map(
    tiles_rows: Sequence[Mapping[str, Any]],
    legend: bool = True,
    *,
    layer: str = "kind",
    ruler: int = 10,
    max_bytes: int = MAX_BYTES,
) -> str:
    """ASCII map from ``tiles`` rows: an x ruler (labels every ``ruler`` tiles + a units-digit line),
    each row prefixed with its y, then a legend of the symbols actually shown.

    ``layer`` is one of MAP_LAYERS: ``kind`` (terrain + infrastructure, KIND_LEGEND), ``height``
    (base-36 min corner height), ``owner`` (``*`` yours, ``o`` other company, ``.`` town/none),
    ``slope`` (``0`` flat, ``1`` sloped, ``s`` steep) or ``build`` (``1`` buildable).
    """
    if layer not in MAP_LAYERS:
        raise ValueError(f"layer must be one of {', '.join(MAP_LAYERS)}")
    if not tiles_rows:
        return "(empty map area)"
    rows = sorted(tiles_rows, key=lambda r: r["y"])
    x0 = rows[0]["x"]
    width = max(len(r[layer]) for r in rows)
    margin = len(str(rows[-1]["y"]))
    pad = " " * (margin + 1)
    tens = [" "] * (width + 8)
    for c in range(width):
        x = x0 + c
        lab = str(x)
        if x % ruler == 0 and (c == 0 or tens[c - 1] == " ") and all(t == " " for t in tens[c : c + len(lab)]):
            tens[c : c + len(lab)] = lab
    out = [pad + "".join(tens).rstrip(), pad + "".join(str((x0 + c) % 10) for c in range(width))]
    body = [f"{r['y']:>{margin}} {r[layer]}" for r in rows]
    tail: list[str] = []
    if legend:
        if layer == "height":
            tail.append(f"Legend: {HEIGHT_LEGEND}")
        else:
            shown = set().union(*(set(r[layer]) for r in rows))
            items = [f"{'(blank)' if ch == ' ' else ch} {name}"
                     for ch, name in _LAYER_LEGENDS[layer].items() if ch in shown]
            tail.append("Legend: " + "  ".join(items))
        tail.append("x grows east (right), y grows south (down); tile index = y * map_size_x + x")
    text = "\n".join(out + body + tail)
    while len(text.encode()) > max_bytes and len(body) > 1:
        body.pop()
        cut = f"(truncated after y={rows[len(body) - 1]['y']} to fit {max_bytes} bytes: request a smaller area)"
        text = "\n".join(out + body + [cut] + tail)
    return _fit(text, max_bytes)


# ---------------------------------------------------------------------------------------------- tile
def format_tile(tile_info: Mapping[str, Any], labels: Mapping[int, str] | None = None) -> str:
    """One tile: what is on it, height/slope, ownership, nearby town, catchment (accepts/produces)."""
    t = tile_info
    h = f"{t['height']}" if t["height"] == t["max_height"] else f"{t['height']}-{t['max_height']}"
    kind = KIND_LEGEND.get(t["kind"], t["kind"])
    owner = ("yours" if t.get("mine") else "town/none" if t["owner"] < 0 else f"company #{t['owner']}")
    closest = t.get("closest_town")
    town = "-"
    if t.get("town", -1) >= 0:
        town = (f"{closest['name']} ({t['town']})" if closest and closest["id"] == t["town"]
                else f"town #{t['town']}")
    near = f"{closest['name']} ({closest['id']}), {closest['dist']} tiles" if closest else "-"
    here = [f"station {t['station']}" if t.get("station", -1) >= 0 else "",
            f"industry {t['industry']}" if t.get("industry", -1) >= 0 else "",
            "depot" if t.get("depot") else "", "water" if t.get("water") else "", "coast" if t.get("coast") else "",
            "river" if t.get("river") else "", "trees" if t.get("trees") else "", "house" if t.get("house") else "",
            "road" if t.get("road") else "", "rail" if t.get("rail") else ""]
    accepts = ", ".join(f"{a['label']} {a['amount']}/8 {'accepted' if a['accepted'] else 'partial'}"
                        for a in t.get("accepts", [])) or "nothing"
    produces = ", ".join(f"{p['label']} x{p['producers']}" for p in t.get("produces", [])) or "nothing"
    inds = ", ".join(f"{i['name']} ({i['id']})" for i in t.get("industries", [])) or "none"
    lines = [
        (f"Tile {t['x']},{t['y']} (#{t['tile']}): {kind}, height {h}, slope {slope_name(t['slope'])}, "
         f"{'buildable' if t['buildable'] else 'not buildable'}, terrain {t['terrain']}"),
        f"Owner {owner} | local authority {town} | closest town {near}",
        "Has: " + (", ".join(x for x in here if x) or "nothing"),
        f"Catchment radius {t['radius']}: accepts {accepts} | produces {produces}",
        "(acceptance in 1/8: a station accepts a cargo at >= 8)",
        f"Industries in catchment: {inds}",
    ]
    return _fit("\n".join(lines), MAX_BYTES)


# ---------------------------------------------------------------------------------------------- lists
def format_vehicles(
    vehicles: Sequence[Mapping[str, Any]],
    labels: Mapping[int, str] | None = None,
    page: int = 1,
    page_size: int = 20,
    *,
    max_bytes: int = MAX_BYTES,
) -> str:
    """Fleet table: where each vehicle is, what it carries (load/capacity), profit, age, orders (cur/n)."""
    rows = [{
        "id": v["id"], "name": v.get("name"), "type": v.get("vtype"), "state": v["state"],
        "at": f"{v['x']},{v['y']}", "km/h": v.get("speed"),
        "cargo": f"{label(v.get('cargo'), labels)} {v.get('load', 0)}/{v.get('capacity', 0)}"
        if v.get("cargo", -1) >= 0 else "-",
        "profit_ty": money(v.get("profit_this_year")), "profit_ly": money(v.get("profit_last_year")),
        "age": f"{v.get('age', 0) / 365:.1f}y", "rel": f"{v.get('reliability')}%",
        "orders": f"{v.get('cur_order', -1)}/{v.get('orders', 0)}",
    } for v in vehicles]
    fields = ["id", "name", "type", "state", "at", "km/h", "cargo", "profit_ty", "profit_ly", "age", "rel", "orders"]
    return format_table(rows, fields, page, page_size, max_bytes=max_bytes)


def format_industries(
    industries: Sequence[Mapping[str, Any]],
    labels: Mapping[int, str] | None = None,
    page: int = 1,
    page_size: int = 20,
    *,
    max_bytes: int = MAX_BYTES,
) -> str:
    """Industries with last-month production per cargo (transported %), accepted cargo, stations around."""
    rows = []
    for i in industries:
        rows.append({
            "id": i["id"], "name": i["name"], "at": f"{i['x']},{i['y']}",
            "produces": "; ".join(f"{label(p['cargo'], labels)} {p['last_month']} ({p.get('pct', 0)}%)"
                                  for p in i.get("produces", [])) or "-",
            "accepts": ",".join(label(c, labels) for c in i.get("accepts", [])) or "-",
            "stations": i.get("stations_around", 0),
            "dist": i.get("dist"),
        })
    fields = ["id", "name", "at", "produces", "accepts", "stations"]
    if any(i.get("dist") is not None for i in industries):
        fields.append("dist")
    return format_table(rows, fields, page, page_size, max_bytes=max_bytes)
