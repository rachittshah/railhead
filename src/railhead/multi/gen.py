"""Multi-company start states for Waybill (docs/WAYBILL.md, section 3).

One task world gets N companies, each founded by the idle AI ``RailheadIdle`` (as ``World`` founds the
single company), with a neutral name and the same start cash. The world is saved as ``start.sav``
with all of them; the task directory also gets ``task.json`` (family ``waybill``) whose objective
lists the companies as ``[{id, name, slot}]``.

    python -m railhead.multi.gen <seed> <out_dir> [--agents N] [--split train] [--horizon DAYS]
        [--runoff DAYS] [--map-log2 K] [--start-cash MONEY]
    python -m railhead.multi.gen track <short|decade> <first_seed> <count> <out_dir>
        [--rotations cycle|all] [--arms enforced,cheap_talk] [--start-balances SLOT=POUNDS,...]
"""

from __future__ import annotations

import shutil
import sys
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path

from railhead.multi.market import MarketConfig
from railhead.multi.regions import REGIONS_FILE, RegionMap, make_region_map, region_of
from railhead.seal import seal_task
from railhead.task import TaskSpec
from railhead.world import AGENT_AI, World

FAMILY = "waybill"


def company_name(slot: int) -> str:
    """A neutral name: it does not tell the agents which model plays which company."""
    return f"Company {chr(ord('A') + slot)}" if slot < 26 else f"Company {slot + 1}"


def found_company(world: World, timeout: float = 30.0) -> int:
    """Start one idle AI and return the id of the company that it founds."""
    existing = {c["id"] for c in world.call("companies")}
    world.admin.rcon(f"start_ai {AGENT_AI}")
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        new = [c["id"] for c in world.call("companies") if c["id"] not in existing]
        if new:
            return int(new[0])
        time.sleep(0.02)
    raise TimeoutError("company was not founded (start_ai RailheadIdle)")


def found_companies(world: World, n: int, start_cash: int | None = None) -> list[dict]:
    """Found ``n`` companies in ``world`` and return ``[{id, name, slot}]`` in slot order.

    ``start_cash``: the loan (and so the cash) of every company at the start. A new company starts with
    its cash equal to its loan; the generator sets the loan to ``start_cash``, so that cash equals
    ``start_cash`` and net worth stays 0. It must be a multiple of the loan interval and at most the
    maximum loan."""
    if n < 1:
        raise ValueError("at least one company")
    out = []
    for slot in range(n):
        cid = found_company(world)
        world.call("bind_company", company=cid)
        name = company_name(slot)
        world.call_args("company_rename", {"name": name})
        if start_cash is not None:
            world.call_args("company_loan", {"amount": int(start_cash)})
        out.append({"id": cid, "name": name, "slot": slot})
    world.call("bind_company", company=out[0]["id"])
    return out


def build_start(world: World, dest: Path, n: int, start_cash: int | None = None,
                history_days: int = 31) -> list[dict]:
    """Run ``history_days`` (so production figures are real), found the companies, save ``dest``."""
    if history_days > 0:
        world.step(days=history_days)
    companies = found_companies(world, n, start_cash=start_cash)
    world.save(Path(dest))
    return companies


def region_map_for(world: World, n: int, rotation: int) -> RegionMap:
    """One franchise region per company, balanced by the industries of the world; slot s gets region
    type (s + rotation) mod n."""
    welcome = world.admin.welcome
    tiles = [(int(i["x"]), int(i["y"])) for i in world.call("industries")]
    return make_region_map(int(welcome.map_x), int(welcome.map_y), n, tiles, rotation)


def write_task(tdir: Path, task_id: str, companies: list[dict], horizon_days: int, runoff_days: int = 0,
               scenario: dict | None = None, split: str = "train", agent_turn_s: float = 600.0,
               regions: RegionMap | None = None, market: MarketConfig | None = None,
               extra_objective: dict | None = None, title: str = "Waybill: a shared world") -> TaskSpec:
    spec = TaskSpec(
        task_id=task_id, family=FAMILY, horizon_days=horizon_days, runoff_days=runoff_days,
        limits={"max_forks": 0, "max_fork_days": 0, "agent_turn_s": agent_turn_s},
        objective={"companies": companies, **({"market": market.to_json()} if market else {}),
                   **(extra_objective or {})},
        scenario=scenario or {}, split=split, title=title,
        briefing=("You run one of several transport companies in one shared world. Each company is played by "
                  "a different agent. Game time passes only when every company waits (`rh wait`). "
                  "Your score is your net worth (cash minus loan) at the end."),
    )
    spec.save(Path(tdir))
    if regions is not None:
        regions.save(Path(tdir) / REGIONS_FILE)
    if (Path(tdir) / "start.sav").exists():
        seal_task(Path(tdir))  # the verifier then refuses a changed start.sav (tamper=1)
    return spec


def gen_waybill(seed: int, out: Path, n: int = 3, split: str = "train", horizon_days: int = 730,
                runoff_days: int = 0, map_log2: int | None = None, start_cash: int | None = None) -> TaskSpec:
    from railhead.gen.scenario import sample_scenario, server_config, task_id

    sc = sample_scenario(seed, split, FAMILY)
    if map_log2:
        sc["map_log2"] = map_log2
    tid = task_id(FAMILY, split, seed)
    tdir = Path(out) / tid
    tdir.mkdir(parents=True, exist_ok=True)
    work = Path(tempfile.mkdtemp(prefix=f"rh-gen-{tid}-"))
    try:
        with World.create(work / "gen", server_config(sc), found_company=False) as w:
            companies = build_start(w, tdir / "start.sav", n, start_cash=start_cash)
            rm = region_map_for(w, n, seed % n)
        return write_task(tdir, tid, companies, horizon_days, runoff_days, scenario=sc, split=split, regions=rm)
    except Exception:
        shutil.rmtree(tdir, ignore_errors=True)
        raise
    finally:
        shutil.rmtree(work, ignore_errors=True)


# ---------------------------------------------------------------------- task tracks
@dataclass(frozen=True)
class Track:
    """A statistics-friendly task variant (docs/WAYBILL.md, section 11)."""

    name: str
    agents: int
    map_log2: int
    horizon_days: int
    runoff_days: int
    quarter_days: int = 90


TRACKS = {
    "short": Track("short", agents=4, map_log2=8, horizon_days=730, runoff_days=0),
    "decade": Track("decade", agents=8, map_log2=9, horizon_days=3650, runoff_days=365),
}
ARMS = ("enforced", "cheap_talk")


@dataclass(frozen=True)
class TaskPlan:
    track: str
    seed: int
    rotation: int
    arm: str
    horizon_days: int | None = None  # None: the horizon of the track

    @property
    def task_id(self) -> str:
        h = "" if self.horizon_days is None else f"-h{self.horizon_days}"
        return f"{FAMILY}-{self.track}-s{self.seed:04d}-r{self.rotation}-{self.arm}{h}"


def plan_track(track: str, seeds: list[int], rotations: str | list[int] = "cycle",
               arms: tuple[str, ...] = ARMS) -> list[TaskPlan]:
    """The tasks of one track. ``rotations``: ``"cycle"`` gives seed number i (in the list) the rotation
    i mod N, so N consecutive seeds give each slot (each model of a fixed roster) each region type once;
    ``"all"`` gives every seed all N rotations; or an explicit list (one rotation per seed). Every
    (seed, rotation) has one task per arm: the arms are paired (same start.sav, same regions)."""
    t = TRACKS[track]
    if rotations == "cycle":
        rots = [[i % t.agents] for i in range(len(seeds))]
    elif rotations == "all":
        rots = [list(range(t.agents)) for _ in seeds]
    else:
        if len(rotations) != len(seeds):
            raise ValueError("one rotation per seed")
        rots = [[int(r) % t.agents] for r in rotations]
    for a in arms:
        if a not in ARMS:
            raise ValueError(f"unknown arm {a!r} (arms: {', '.join(ARMS)})")
    return [TaskPlan(track, seed, r, arm) for seed, rs in zip(seeds, rots, strict=True) for r in rs for arm in arms]


def gen_track(track: str, seed: int, out: Path, rotations: list[int] | None = None,
              arms: tuple[str, ...] = ARMS, start_balances: dict[int, int] | None = None,
              tender: dict | None = None, split: str = "train", horizon_days: int | None = None) -> list[TaskSpec]:
    """Make the world of one seed once, then one task directory for each (rotation, arm). All of them
    have the same start.sav; the rotation changes only regions.json, the arm only the market config.
    ``horizon_days`` shortens (or lengthens) the track horizon for smoke runs; the task id then ends in
    ``-h<days>`` so a short game is never mistaken for a track game."""
    from railhead.gen.scenario import sample_scenario, server_config

    t = TRACKS[track]
    rotations = [seed % t.agents] if rotations is None else rotations
    sc = sample_scenario(seed, split, FAMILY)
    sc["map_log2"] = t.map_log2
    work = Path(tempfile.mkdtemp(prefix=f"rh-gen-{track}-{seed}-"))
    specs = []
    try:
        with World.create(work / "gen", server_config(sc), found_company=False) as w:
            companies = build_start(w, work / "start.sav", t.agents)
            region_maps = {r: region_map_for(w, t.agents, r) for r in rotations}
            ind_tiles = [(int(i["x"]), int(i["y"])) for i in w.call("industries")]
        for r in rotations:
            for arm in arms:
                plan = TaskPlan(track, seed, r, arm, horizon_days)
                tdir = Path(out) / plan.task_id
                tdir.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(work / "start.sav", tdir / "start.sav")
                market = MarketConfig(arm=arm, seed=seed, start_balances=dict(start_balances or {}),
                                      quarter_days=t.quarter_days, tender=dict(tender or {}))
                rm = region_maps[r]
                extra = {"track": track, "seed": seed, "rotation": r, "arm": arm,
                         "region_types": {str(k): v for k, v in rm.types.items()},
                         "region_industries": {str(k): sum(region_of(rm, x, y) == k for x, y in ind_tiles)
                                               for k in sorted(rm.regions)}}
                specs.append(write_task(tdir, plan.task_id, companies, horizon_days or t.horizon_days,
                                        t.runoff_days, scenario=sc,
                                        split=split, regions=rm, market=market, extra_objective=extra,
                                        title=f"Waybill ({track} track): a shared world with a contract market"))
        return specs
    finally:
        shutil.rmtree(work, ignore_errors=True)


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    if argv[:1] == ["track"]:
        return track_main(argv[1:])
    opts = {"--agents": "3", "--split": "train", "--horizon": "730", "--runoff": "0", "--map-log2": "",
            "--start-cash": ""}
    pos = []
    it = iter(argv)
    for a in it:
        if a in opts:
            opts[a] = next(it)
        else:
            pos.append(a)
    if len(pos) != 2:
        print(__doc__, file=sys.stderr)
        return 2
    spec = gen_waybill(int(pos[0]), Path(pos[1]), n=int(opts["--agents"]), split=opts["--split"],
                       horizon_days=int(opts["--horizon"]), runoff_days=int(opts["--runoff"]),
                       map_log2=int(opts["--map-log2"]) if opts["--map-log2"] else None,
                       start_cash=int(opts["--start-cash"]) if opts["--start-cash"] else None)
    print(spec.task_id, [c["name"] for c in spec.objective["companies"]])
    return 0


def track_main(argv: list[str]) -> int:
    """python -m railhead.multi.gen track <short|decade> <first_seed> <count> <out_dir>
    [--rotations cycle|all] [--arms enforced,cheap_talk] [--start-balances 0=50000,1=0] [--horizon DAYS]"""
    opts = {"--rotations": "cycle", "--arms": ",".join(ARMS), "--start-balances": "", "--horizon": ""}
    pos = []
    it = iter(argv)
    for a in it:
        if a in opts:
            opts[a] = next(it)
        else:
            pos.append(a)
    if len(pos) != 4 or pos[0] not in TRACKS:
        print(track_main.__doc__, file=sys.stderr)
        return 2
    track, first, count, out = pos[0], int(pos[1]), int(pos[2]), Path(pos[3])
    seeds = list(range(first, first + count))
    arms = tuple(a for a in opts["--arms"].split(",") if a)
    sb = {int(k): int(v) for k, v in (kv.split("=") for kv in opts["--start-balances"].split(",") if kv)}
    plans = plan_track(track, seeds, opts["--rotations"], arms)
    for seed in seeds:
        rots = sorted({p.rotation for p in plans if p.seed == seed})
        for spec in gen_track(track, seed, out, rotations=rots, arms=arms, start_balances=sb,
                              horizon_days=int(opts["--horizon"]) if opts["--horizon"] else None):
            print(spec.task_id)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
