"""Transport-mode census of real action logs (docs/MODES.md, step 1).

Reads ``actions.jsonl`` files and attributes every logged act to a transport mode:

* infrastructure by method name (``rail_*``, ``water_*``, ``road_*``, ``air_*``; ``bridge_build`` by its
  ``vehicle`` argument);
* vehicles by the depot they were bought or cloned in (rail depot, road depot, ship depot, hangar), so
  ``vehicle_*`` / ``order_*`` calls on a vehicle id count for that vehicle's mode.

Read-only analysis for environment authors; it is not a player tool. Usage::

    python -m railhead.oracle.census <actions.jsonl>...   # JSON summary on stdout
"""

from __future__ import annotations

import json
import sys
from collections import Counter, defaultdict
from collections.abc import Iterable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

MODES = ("rail", "road", "water", "air")
_DEPOT_BUILDERS = {"rail_depot": "rail", "road_depot": "road", "water_ship_depot": "water"}


@dataclass
class PlayerCensus:
    player: str
    calls: Counter = field(default_factory=Counter)        # (mode, ok) -> n, dry runs included
    methods: Counter = field(default_factory=Counter)      # (mode, method, ok) -> n
    fail_codes: Counter = field(default_factory=Counter)   # (mode, code) -> n
    bought: Counter = field(default_factory=Counter)       # mode -> vehicles bought or cloned (not wagons)
    wagons: int = 0                                        # rail vehicles later attached to a train
    started: Counter = field(default_factory=Counter)      # mode -> distinct vehicles started
    first_start_calls: dict[str, int] = field(default_factory=dict)  # mode -> mode calls before 1st start

    def to_json(self) -> dict[str, Any]:
        return {
            "player": self.player,
            "calls": {m: {"ok": self.calls[(m, True)], "fail": self.calls[(m, False)]} for m in MODES},
            "bought": dict(self.bought), "wagons": self.wagons, "started": dict(self.started),
            "first_start_calls": self.first_start_calls,
            "fail_codes": {f"{m}:{c}": n for (m, c), n in self.fail_codes.most_common()},
            "methods": {f"{m}:{meth}:{'ok' if ok else 'fail'}": n for (m, meth, ok), n in sorted(self.methods.items())},
        }


def _mode_of_infra(method: str, args: dict[str, Any]) -> str | None:
    head = method.split("_", 1)[0]
    if head in MODES:
        return head
    if method == "bridge_build":
        return {"rail": "rail", "road": "road", "water": "water"}.get(args.get("vehicle", "road"))
    if method == "bridge_tunnel":
        return {"rail": "rail", "road": "road"}.get(args.get("vehicle", "road"))
    return None


def census_records(player: str, records: Iterable[dict[str, Any]]) -> PlayerCensus:
    """Census of one player's log records (dicts as written by ``World.act``)."""
    pc = PlayerCensus(player)
    depot_mode: dict[int, str] = {}
    vehicle_mode: dict[int, str] = {}
    started: dict[str, set[int]] = defaultdict(set)
    for r in records:
        if r.get("kind") != "act":
            continue
        m, a, ok = r.get("m", ""), r.get("a") or {}, bool(r.get("ok"))
        ret = r.get("ret") or {}
        dry = bool(a.get("dry_run"))
        mode = _mode_of_infra(m, a)
        if ok and not dry:
            if m in _DEPOT_BUILDERS:
                depot_mode[int(ret.get("depot_tile", a.get("tile", -1)))] = _DEPOT_BUILDERS[m]
            elif m == "air_airport" and int(ret.get("hangar_tile", -1)) >= 0:
                depot_mode[int(ret["hangar_tile"])] = "air"
        if m in ("vehicle_buy", "vehicle_clone"):
            mode = depot_mode.get(int(a.get("depot_tile", -1)))
            if ok and not dry and mode and "vehicle" in ret:
                vehicle_mode[int(ret["vehicle"])] = mode
                pc.bought[mode] += 1
        elif m == "vehicle_attach":
            mode = "rail"
            if ok and not dry:
                w = int(a.get("vehicle", -1))
                if vehicle_mode.get(w) == "rail" and pc.bought["rail"] > 0:
                    pc.bought["rail"] -= 1  # a wagon, not a train
                    pc.wagons += 1
                vehicle_mode[w] = "wagon"
        elif m.startswith(("vehicle_", "order_")):
            mode = vehicle_mode.get(int(a.get("vehicle", -1)))
        if mode not in MODES:
            continue
        pc.calls[(mode, ok)] += 1
        pc.methods[(mode, m, ok)] += 1
        if not ok:
            pc.fail_codes[(mode, r.get("code", "?"))] += 1
        if m == "vehicle_start" and ok and not dry:
            v = int(a.get("vehicle", -1))
            if not started[mode]:
                pc.first_start_calls[mode] = pc.calls[(mode, True)] + pc.calls[(mode, False)]
            started[mode].add(v)
    pc.started = Counter({k: len(v) for k, v in started.items()})
    return pc


def load(path: Path) -> list[dict[str, Any]]:
    out = []
    for line in Path(path).read_text().splitlines():
        try:
            out.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return out


def player_name(path: Path) -> str:
    """``.../<game>/<player>/state/run/actions.jsonl`` -> ``<game>/<player>``."""
    parts = Path(path).parts
    try:
        i = parts.index("state")
        return "/".join(parts[max(0, i - 2):i])
    except ValueError:
        return str(path)


def summarize(players: list[PlayerCensus]) -> dict[str, Any]:
    tried = {m: sorted(p.player for p in players if p.calls[(m, True)] + p.calls[(m, False)]) for m in MODES}
    ran = {m: sorted(p.player for p in players if p.started[m]) for m in MODES}
    codes: Counter = Counter()
    calls: Counter = Counter()
    for p in players:
        codes.update(p.fail_codes)
        calls.update(p.calls)
    return {
        "players": len(players),
        "tried": {m: len(v) for m, v in tried.items()},
        "ran_vehicles": {m: len(v) for m, v in ran.items()},
        "calls": {m: {"ok": calls[(m, True)], "fail": calls[(m, False)]} for m in MODES},
        "vehicles_bought": {m: sum(p.bought[m] for p in players) for m in MODES},
        "wagons": sum(p.wagons for p in players),
        "top_fail_codes": {f"{m}:{c}": n for (m, c), n in codes.most_common(25)},
        "first_start_calls": {m: sorted(p.first_start_calls[m] for p in players if m in p.first_start_calls)
                              for m in MODES},
        "who_tried": tried, "who_ran": ran,
    }


def main(argv: list[str] | None = None) -> int:
    paths = [Path(p) for p in (argv if argv is not None else sys.argv[1:])]
    if not paths:
        print(__doc__, file=sys.stderr)
        return 2
    players = [census_records(player_name(p), load(p)) for p in paths]
    print(json.dumps({"summary": summarize(players), "players": [p.to_json() for p in players]}, indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
