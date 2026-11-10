"""Mode economics: build one simple route per transport mode with the oracles, run the world for some
years, and measure build cost, actions, yearly income, payback and income per pound (docs/MODES.md).

    python -m railhead.oracle.modes tasks/arena-dev-0100/start.sav --seeds 1,2,3 --years 3 \\
        --modes water,rail,air,road --out docs/data/modes-econ.json

Each (mode, seed) loads the start save, reseeds the hidden randomness (``reseed``, as forks do), builds
with player actions only, and steps to 1 January of each following year. Income is the route vehicles'
``profit_last_year`` (fares minus running costs; infrastructure upkeep and loan interest excluded).
The first calendar year is warm-up; yearly income is the mean of the later full years.
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import statistics
import tempfile
from pathlib import Path
from typing import Any

from railhead.world import World

MODES = ("water", "rail", "air", "road")


class Meter:
    """World proxy counting player actions and queries (what a model would pay in calls)."""

    def __init__(self, world: World) -> None:
        self.w = world
        self.acts = 0
        self.queries = 0

    def call(self, method: str, **args: Any) -> Any:
        self.queries += 1
        return self.w.call(method, **args)

    def act(self, method: str, **args: Any) -> Any:
        self.acts += 1
        return self.w.act(method, **args)


def net_worth(w: World) -> int:
    c = w.call("company")
    return int(c["money"]) - int(c["loan"])


def _ymd(w: World) -> dt.date:
    y, m, d = (int(x) for x in w.state()["ymd"].split("-"))
    return dt.date(y, m, d)


def step_to(w: World, day: dt.date) -> None:
    now = _ymd(w)
    while now < day:
        w.step(days=min((day - now).days, 366))
        now = _ymd(w)


def build_route(mode: str, m: Meter) -> dict[str, Any]:
    if mode == "water":
        from railhead.oracle.water import WaterOracle
        r = WaterOracle(m).build()
        return {"vehicles": r.vehicles, "detail": {**r.detail, "cargo": "PASS"}}
    if mode == "rail":
        from railhead.oracle.rail import RailOracle
        r = RailOracle(m).build()
        return {"vehicles": r.vehicles, "detail": {**r.detail, "cargo": "COAL", "distance": r.detail["track_tiles"]}}
    if mode == "air":
        from railhead.oracle.air import AirOracle
        r = AirOracle(m).build()
        return {"vehicles": r.vehicles, "detail": {**r.detail, "cargo": "PASS"}}
    if mode == "road":
        from railhead.gen.families import pick_deliver_pair
        from railhead.oracle.road import RoadOracle
        pair = pick_deliver_pair(m.w, revenue=True)
        if pair is None:
            raise RuntimeError("no road delivery pair")
        src, dst, cargo = pair
        net = RoadOracle(m).deliver(src, dst, cargo)
        labels = {c["id"]: c["label"] for c in m.w.call("cargos")}
        return {"vehicles": net.vehicles, "detail": {"source": src["id"], "dest": dst["id"], "cargo": labels.get(cargo),
                                                       "distance": len(net.path)}}
    raise ValueError(f"unknown mode {mode!r}")


def measure(save: Path, mode: str, seed: int, workdir: Path, years: int = 3, log: Path | None = None) -> dict[str, Any]:
    """One (mode, seed) run. ``log``: also write the build's action log there (replayable with
    ``railhead play`` on a daemon of the same task, after the same ``reseed``)."""
    with World.load(workdir, save) as w:
        out = w.admin.rcon(f"reseed {seed & 0xFFFFFFFF}")
        if not out or not out[0].startswith("Reseeded"):
            raise RuntimeError(f"engine lacks the reseed patch: {out!r}")
        if log is not None:
            from railhead.actionlog import ActionLog
            log.parent.mkdir(parents=True, exist_ok=True)
            w.attach_log(ActionLog(log))
        start = _ymd(w)
        nw0 = net_worth(w)
        m = Meter(w)
        route = build_route(mode, m)
        build_cost = nw0 - net_worth(w)
        yearly: list[dict[str, Any]] = []
        vs = set(route["vehicles"])
        for k in range(1, years + 1):
            step_to(w, dt.date(start.year + k, 1, 1))
            rows = [v for v in w.call("vehicles") if v["id"] in vs]
            yearly.append({"year": start.year + k - 1, "profit": sum(int(v["profit_last_year"]) for v in rows),
                           "vehicles": len(rows), "net_worth": net_worth(w)})
    full = [y["profit"] for y in yearly[1:]] or [yearly[0]["profit"]]
    income = statistics.mean(full)
    return {
        "mode": mode, "seed": seed, "save": str(save), "start": start.isoformat(),
        "build_cost": build_cost, "acts": m.acts, "queries": m.queries, "vehicles": len(vs),
        "yearly": yearly, "income": round(income), "payback_years": round(build_cost / income, 2) if income > 0 else None,
        "income_per_pound": round(income / build_cost, 3) if build_cost > 0 else None,
        "income_per_act": round(income / m.acts) if m.acts else None, **route["detail"],
    }


def table(rows: list[dict[str, Any]]) -> str:
    """Markdown table: one line per mode, mean over seeds (min-max of income)."""
    lines = ["| Mode | Route | Build cost | Actions | Yearly income (min-max) | Payback | Income per £ | Income per action |",
             "|---|---|---:|---:|---:|---:|---:|---:|"]
    for mode in MODES:
        rs = [r for r in rows if r["mode"] == mode and "error" not in r]
        if not rs:
            continue
        inc = [r["income"] for r in rs]
        cost = statistics.mean(r["build_cost"] for r in rs)
        acts = statistics.mean(r["acts"] for r in rs)
        mi = statistics.mean(inc)
        route = f"{rs[0].get('cargo')}, {rs[0].get('distance')} tiles, {rs[0]['vehicles']} vehicles"
        lines.append(f"| {mode} | {route} | £{cost:,.0f} | {acts:.0f} | £{mi:,.0f} (£{min(inc):,}-£{max(inc):,}) | "
                     f"{cost / mi:.1f} y | {mi / cost:.2f} | £{mi / acts:,.0f} |" if mi > 0 else
                     f"| {mode} | {route} | £{cost:,.0f} | {acts:.0f} | £{mi:,.0f} | never | - | - |")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="python -m railhead.oracle.modes")
    ap.add_argument("save", type=Path)
    ap.add_argument("--seeds", default="1,2,3")
    ap.add_argument("--years", type=int, default=3)
    ap.add_argument("--modes", default=",".join(MODES))
    ap.add_argument("--out", type=Path)
    ap.add_argument("--logs", type=Path, help="directory for one action log per (mode, seed)")
    a = ap.parse_args(argv)
    rows = []
    for mode in a.modes.split(","):
        for seed in (int(s) for s in a.seeds.split(",")):
            with tempfile.TemporaryDirectory(prefix="rh-modes-") as tmp:
                try:
                    log = a.logs / f"{mode}-{seed}.jsonl" if a.logs else None
                    row = measure(a.save, mode, seed, Path(tmp) / "w", a.years, log)
                except Exception as e:  # noqa: BLE001 - one failed mode must not hide the others
                    row = {"mode": mode, "seed": seed, "error": f"{type(e).__name__}: {e}"}
            print(json.dumps(row, default=str), flush=True)
            rows.append(row)
    if a.out:
        a.out.parent.mkdir(parents=True, exist_ok=True)
        a.out.write_text(json.dumps(rows, indent=1, default=str) + "\n")
    print(table(rows))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
