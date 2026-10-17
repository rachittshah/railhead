"""Economics and behaviour of one player, read from its action log alone (no engine needed).

    railhead econ <arena_dir> --task tasks/<task_id> [--out docs/data/<arena>-econ.json]

Per player: capital spent by category and by year, vehicles bought by mode, the route legs in the
vehicles' orders (Manhattan length in tiles), the loan path, when income and positive net worth
arrived, how often and how long the player waited, how many actions failed and why, how often it
used dry runs, and its town actions and sales. Forks are not in the action log, so they are not here.
"""

from __future__ import annotations

import json
import statistics
from collections import Counter, defaultdict
from datetime import date
from pathlib import Path

from railhead.actionlog import read_log
from railhead.arena import engine_mode

TICKS_PER_DAY = 74
CATEGORY = {"air_": "air", "rail_": "rail", "road_": "road", "water_": "water", "town_": "towns", "land_": "terraform",
            "bridge_": "bridges", "tunnel_": "bridges"}
MODE_OF_BUILD = {"air_airport": "air", "rail_station": "rail", "rail_line": "rail", "rail_track": "rail",
                 "road_stop": "road", "road_line": "road", "water_dock": "water"}


def ottd_date(day: int) -> str:
    """OpenTTD date number (days since 1 January of year 0) -> ISO date."""
    return date.fromordinal(day - 365).isoformat()


def _category(m: str) -> str:
    if m in ("vehicle_buy", "vehicle_clone"):
        return "vehicles"
    return next((c for p, c in CATEGORY.items() if m.startswith(p)), "other")


def analyse(log: Path, map_x: int) -> dict:
    recs = [r.raw for r in read_log(log)]
    head = recs[0]
    t0, d0 = int(head["checksum"]["ticks"]), int(head["checksum"]["date"])

    def day_of(ticks: int) -> str:
        return ottd_date(d0 + (int(ticks) - t0) // TICKS_PER_DAY)

    acts = failed = dry = 0
    errors: Counter = Counter()
    capex: Counter = Counter()
    capex_year: dict[str, Counter] = defaultdict(Counter)
    acts_year: Counter = Counter()
    bought: Counter = Counter({"road": 0, "rail": 0, "air": 0, "water": 0})
    mode_of: dict[int, str] = {}
    orders: dict[int, list[int]] = {}
    sold, towns, first_build, loan_events = 0, Counter(), {}, []
    sales = 0
    snaps, wait_days = [], []
    for r in recs[1:]:
        if r["kind"] == "wait":
            wait_days.append((int(r.get("end_ticks", r["ticks"])) - int(r["ticks"])) // TICKS_PER_DAY)
            if r.get("snap"):
                snaps.append(r["snap"])
            continue
        if r["kind"] != "act":
            continue
        a, ret, m = r.get("a") or {}, r.get("ret") or {}, r.get("m", "")
        acts += 1
        year = day_of(r["ticks"])[:4]
        acts_year[year] += 1
        if a.get("dry_run"):
            dry += 1
            continue
        if not r.get("ok"):
            failed += 1
            errors[r.get("code", "?")] += 1
            continue
        cost = int(ret.get("cost") or 0)
        if cost > 0:
            capex[_category(m)] += cost
            capex_year[year][_category(m)] += cost
        if m in MODE_OF_BUILD and MODE_OF_BUILD[m] not in first_build:
            first_build[MODE_OF_BUILD[m]] = day_of(r["ticks"])
        if m == "vehicle_buy" and "vehicle" in ret:
            v = ret["vehicle"]
            mode_of[v] = engine_mode(int(ret.get("engine", a.get("engine", 0))))
            orders[v] = []
            bought[mode_of[v]] += 1
        elif m == "vehicle_clone" and "vehicle" in ret:
            src, v = a.get("vehicle"), ret["vehicle"]
            if src in mode_of:
                mode_of[v] = mode_of[src]
                bought[mode_of[v]] += 1
                src_orders = orders.setdefault(src, [])
                orders[v] = src_orders if a.get("share_orders") else list(src_orders)
        elif m == "vehicle_sell":
            sold += 1
            sales += -cost
            mode_of.pop(a.get("vehicle"), None)
        elif m == "order_append" and "tile" in a:
            orders.setdefault(a["vehicle"], []).append(int(a["tile"]))
        elif m == "order_insert" and "tile" in a:
            orders.setdefault(a["vehicle"], []).insert(int(a.get("index", 0)), int(a["tile"]))
        elif m == "order_remove":
            lst = orders.setdefault(a["vehicle"], [])
            if 0 <= int(a.get("index", -1)) < len(lst):
                lst.pop(int(a["index"]))
        elif m == "order_clear":
            orders[a["vehicle"]] = []
        elif m == "order_share" and "other_vehicle" in a:
            orders[a["vehicle"]] = orders.setdefault(a["other_vehicle"], [])
        elif m == "town_action":
            towns[a.get("kind", "?")] += 1
        if m == "company_loan" and "loan" in ret:
            loan_events.append([day_of(r["ticks"]), int(ret["loan"])])

    legs: dict[str, list[int]] = defaultdict(list)
    for v, mode in mode_of.items():
        stops = orders.get(v) or []
        pairs = {tuple(sorted((p, q))) for p, q in zip(stops, stops[1:] + stops[:1]) if p != q}
        for p, q in pairs:
            legs[mode].append(abs(p % map_x - q % map_x) + abs(p // map_x - q // map_x))

    loans = [s.get("loan", 0) for s in snaps]
    max_loan = max([0, *loans, *(int((r.get("ret") or {}).get("loan", 0)) for r in recs[1:] if r.get("m") == "company_loan")])
    first_max = next((s["date"] for s in snaps if s.get("loan") == max_loan), None) if max_loan else None
    repaid = None
    if max_loan:
        seen = False
        for s in snaps:
            seen = seen or s.get("loan", 0) > 0
            if seen and s.get("loan", 0) == 0:
                repaid = s["date"]
                break
    peak = max(snaps, key=lambda s: s.get("income_q", 0), default={})
    top_fleet = max(snaps, key=lambda s: s.get("vehicles", 0), default={})
    last = snaps[-1] if snaps else {}
    return {
        "acts": acts, "failed": failed, "dry_runs": dry, "errors": dict(errors.most_common(6)),
        "capex": dict(capex), "capex_total": sum(capex.values()),
        "capex_by_year": {y: dict(c) for y, c in sorted(capex_year.items())},
        "acts_by_year": dict(sorted(acts_year.items())),
        "vehicles_bought": dict(bought), "vehicles_sold": sold, "sale_revenue": sales,
        "legs": {mode: {"vehicle_legs": len(d), "mean_tiles": round(statistics.mean(d)), "max_tiles": max(d)}
                 for mode, d in sorted(legs.items()) if d},
        "loan": {"max": max_loan, "first_max_date": first_max, "repaid_date": repaid},
        "loan_events": loan_events,
        "peak_vehicles": {"vehicles": top_fleet.get("vehicles"), "date": top_fleet.get("date")},
        "first_build": first_build,
        "first_income_date": next((s["date"] for s in snaps if s.get("income_q", 0) > 0), None),
        "net_worth_positive_date": next((s["date"] for s in snaps if s.get("net_worth", 0) > 0), None),
        "peak_quarter_income": {"date": peak.get("date"), "income_q": peak.get("income_q")},
        "last_snapshot": {k: last.get(k) for k in ("date", "net_worth", "money", "loan", "vehicles", "stations",
                                                   "income_q", "expenses_q")},
        "waits": {"count": len(wait_days), "median_days": float(statistics.median(wait_days)) if wait_days else 0.0,
                  "total_days": sum(wait_days)},
        "town_actions": dict(towns),
    }


def main(argv: list[str]) -> int:
    arena = Path(argv[0])
    task = Path(argv[argv.index("--task") + 1])
    map_x = 1 << int(json.loads((task / "task.json").read_text())["scenario"]["map_log2"])
    out = {p.name: analyse(p / "state/run/actions.jsonl", map_x) for p in sorted(arena.iterdir())
           if (p / "state/run/actions.jsonl").exists()}
    text = json.dumps({"task": task.name, "map_x": map_x, "players": out}, indent=1)
    if "--out" in argv:
        Path(argv[argv.index("--out") + 1]).write_text(text + "\n")
    print(text)
    return 0
