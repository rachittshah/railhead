"""Generate certified tasks: start state + objective + oracle log + certificates.

    railhead gen <family> <split> <seed_from> <seed_to> [--out tasks] [--jobs N]

families: deliver | deliver_bid | impossible | tycoon | simops
Every kept task has: a solvability certificate (oracle replay reward), the do-nothing reward, and a
target calibrated from what the scripted oracle actually achieved on that exact map.
"""

from __future__ import annotations

import json
import math
import shutil
import sys
import tempfile
from concurrent.futures import ProcessPoolExecutor, as_completed
from pathlib import Path

from railhead.actionlog import ActionLog
from railhead.gen.families import pick_deliver_pair
from railhead.gen.scenario import sample_scenario, server_config, task_id
from railhead.oracle.road import OracleFailed, RoadOracle
from railhead.task import TaskSpec
from railhead.verify import verify
from railhead.world import World

DELIVER_HORIZON = 300
TYCOON_HORIZON = 365


class Rejected(Exception):
    pass


def _empty_log(path: Path) -> Path:
    log = ActionLog(path)
    log.write({"kind": "header", "v": 1, "ticks": 0})
    log.close()
    return path


def _write(tdir: Path, spec: TaskSpec) -> None:
    spec.save(tdir)


def _cargo_label(world: World, cargo: int) -> str:
    for c in world.call("cargos"):
        if int(c["id"]) == cargo:
            return str(c.get("name") or c.get("label"))
    return f"cargo {cargo}"


def _oracle_play(start: Path, work: Path, log_path: Path, src: dict, dst: dict, cargo: int) -> None:
    with World.load(work / "oracle", start) as w:
        w.attach_log(ActionLog(log_path))
        RoadOracle(w).deliver(src, dst, cargo)
        w.note("submit")


def _prepend_meta(log_path: Path, m: str, a: dict) -> None:
    lines = log_path.read_text().splitlines()
    hdr, rest = lines[0], lines[1:]
    meta = json.dumps({"kind": "meta", "seq": 0, "ticks": json.loads(hdr).get("ticks", 0), "m": m, "a": a},
                      separators=(",", ":"), sort_keys=True)
    out = [hdr, meta]
    for i, line in enumerate(rest, start=1):
        rec = json.loads(line)
        rec["seq"] = i
        out.append(json.dumps(rec, separators=(",", ":"), sort_keys=True))
    log_path.write_text("\n".join(out) + "\n")


# ---------------------------------------------------------------------- deliver family (+variants)
def gen_deliver(seed: int, split: str, out: Path, variant: str = "deliver") -> TaskSpec:
    sc = sample_scenario(seed, split, variant)
    tid = task_id(variant, split, seed)
    tdir = out / tid
    work = Path(tempfile.mkdtemp(prefix=f"rh-gen-{tid}-"))
    try:
        tdir.mkdir(parents=True, exist_ok=True)
        with World.create(work / "gen", server_config(sc)) as w:
            w.step(days=31)  # one month of history so production figures are real
            pair = pick_deliver_pair(w)
            if pair is None:
                raise Rejected("no source/destination pair at road distance")
            src, dst, cargo = pair
            label = _cargo_label(w, cargo)
            m_dst = w.call("monitor_add", kind="industry_delivery", cargo=cargo, target=dst["id"])
            m_src = w.call("monitor_add", kind="industry_pickup", cargo=cargo, target=src["id"])
            total_prod = sum(int(p.get("last_month", 0)) for i in w.call("industries")
                             for p in i.get("produces", []) if int(p["cargo"]) == cargo)
            w.save(tdir / "start.sav")
        start = tdir / "start.sav"
        objective = {"cargo": cargo, "cargo_name": label, "source": src["id"], "source_name": src["name"],
                     "source_xy": [src["x"], src["y"]], "dest": dst["id"], "dest_name": dst["name"],
                     "dest_xy": [dst["x"], dst["y"]], "monitor": m_dst, "monitors_aux": [m_src],
                     "quantity": 1}
        spec = TaskSpec(task_id=tid, family="deliver", horizon_days=DELIVER_HORIZON, runoff_days=0,
                        limits={"max_forks": 2, "max_fork_days": 180}, objective=objective,
                        scenario=sc | {"variant": variant}, split=split)
        _write(tdir, spec)
        oracle_log = tdir / "oracle.jsonl"
        if variant == "impossible":
            months = math.ceil(DELIVER_HORIZON / 30)
            q = int(max(1000, 5 * max(total_prod, 1) * months))
            q = int(math.ceil(q / 1000.0) * 1000)
            objective.update(quantity=q)
            spec.secret = {"feasible": False, "map_production_per_month": total_prod,
                           "upper_bound_basis": "5x total map production over the horizon"}
            _empty_log(oracle_log)
            _prepend_meta(oracle_log, "abstain", {"reason": f"total {label} production on the map is ~{total_prod}/month; "
                                                             f"{q} units in {DELIVER_HORIZON} days is impossible"})
        else:
            try:
                _oracle_play(start, work, oracle_log, src, dst, cargo)
            except OracleFailed as e:
                raise Rejected(f"oracle failed: {e}") from None
            probe = verify(tdir, start, oracle_log, work / "probe")
            d = int(probe["delivered"])
            if probe["valid"] != 1 or d < 40:
                raise Rejected(f"oracle delivered too little ({d}) or invalid replay")
            q = max(20, int(round(0.6 * d, -1)))
            objective.update(quantity=q)
            spec.secret = {"feasible": True, "oracle_delivered": d}
            if variant == "deliver_bid":
                scale = int(round(1.5 * d, -1))
                objective.update(bid=True, bid_scale=scale, bid_deadline_days=30)
                _prepend_meta(oracle_log, "promise", {"quantity": d})
        spec.objective = objective
        spec.title, spec.briefing = deliver_text(spec)
        _write(tdir, spec)
        certify(spec, tdir, work)
        return spec
    except Exception:
        shutil.rmtree(tdir, ignore_errors=True)
        raise
    finally:
        shutil.rmtree(work, ignore_errors=True)


def deliver_text(spec: TaskSpec) -> tuple[str, str]:
    o = spec.objective
    sx, sy = o["source_xy"]
    dx, dy = o["dest_xy"]
    title = f"Deliver {o['cargo_name']} to {o['dest_name']}"
    lines = [
        f"You run a young transport company. A customer needs **{o['cargo_name']}** delivered to "
        f"**{o['dest_name']}** (industry id {o['dest']}, near tile {dx},{dy}).",
        f"A likely supplier is **{o['source_name']}** (industry id {o['source']}, near tile {sx},{sy}); any "
        "source counts, only what your company delivers to that industry is counted.",
        f"**Contract:** deliver at least **{o['quantity']:,} units** within the horizon.",
    ]
    if o.get("bid"):
        lines.append(
            f"**Promise:** within the first {o['bid_deadline_days']} game-days, commit to a delivery quantity with "
            f"`rh promise quantity=N` (the last promise before the deadline counts; max {o['bid_scale']:,}). "
            "You are paid for min(delivered, promised) and fined for any shortfall, so promise what you can really "
            "deliver: over- and under-promising both cost you.")
    lines.append("If you conclude the contract cannot be met by anyone, say so with `rh abstain reason=\"...\"`; "
                 "abstaining on a feasible contract scores zero.")
    lines.append("The reward is the fraction of the contract delivered (capped at 1), measured by the engine.")
    return title, "\n\n".join(lines)


# ---------------------------------------------------------------------- tycoon
def gen_tycoon(seed: int, split: str, out: Path) -> TaskSpec:
    sc = sample_scenario(seed, split, "tycoon")
    tid = task_id("tycoon", split, seed)
    tdir = out / tid
    work = Path(tempfile.mkdtemp(prefix=f"rh-gen-{tid}-"))
    try:
        tdir.mkdir(parents=True, exist_ok=True)
        with World.create(work / "gen", server_config(sc)) as w:
            w.step(days=31)  # one month of history so production figures are real
            pair = pick_deliver_pair(w, dmin=25, dmax=60, revenue=True)
            if pair is None:
                raise Rejected("no profitable starter route")
            w.save(tdir / "start.sav")
        start = tdir / "start.sav"
        spec = TaskSpec(task_id=tid, family="tycoon", horizon_days=TYCOON_HORIZON, runoff_days=730,
                        limits={"max_forks": 2, "max_fork_days": 365},
                        objective={"w_idle": 0, "w_base": 1}, scenario=sc, split=split)
        _write(tdir, spec)
        idle = verify(tdir, start, _empty_log(work / "idle.jsonl"), work / "idle")
        oracle_log = tdir / "oracle.jsonl"
        try:
            _oracle_play(start, work, oracle_log, *pair)
        except OracleFailed as e:
            raise Rejected(f"baseline failed: {e}") from None
        base = verify(tdir, start, oracle_log, work / "base")
        if base["valid"] != 1 or base["net_worth"] <= idle["net_worth"]:
            raise Rejected("baseline does not beat doing nothing")
        spec.objective = {"w_idle": int(idle["net_worth"]), "w_base": int(base["net_worth"])}
        spec.title = "Build a transport empire"
        spec.briefing = (
            f"You run a new transport company with a {TYCOON_HORIZON}-day mandate. Build whatever network you like "
            "(road, rail, air, water). When the mandate ends, your company keeps running **without you** for "
            f"{spec.runoff_days} more days; the reward is your **net worth (cash minus loan) after that run-off**, "
            f"scaled so that doing nothing scores 0, a simple scripted truck route scores 0.5 "
            f"(net worth £{spec.objective['w_base']:,}), and doubling its gain scores 1. "
            "Asset stripping and loan games do not help: only sustainable profit survives the run-off.")
        _write(tdir, spec)
        certify(spec, tdir, work)
        return spec
    except Exception:
        shutil.rmtree(tdir, ignore_errors=True)
        raise
    finally:
        shutil.rmtree(work, ignore_errors=True)


# ---------------------------------------------------------------------- certificates
def certify(spec: TaskSpec, tdir: Path, work: Path) -> None:
    start = tdir / "start.sav"
    idle = verify(tdir, start, _empty_log(work / "cert-idle.jsonl"), work / "cert-idle")
    orc = verify(tdir, start, tdir / "oracle.jsonl", work / "cert-oracle")
    spec.limits = dict(spec.limits)
    certs = {"idle_reward": idle["reward"], "oracle_reward": orc["reward"], "oracle_valid": orc["valid"]}
    (tdir / "certificates.json").write_text(json.dumps(certs, indent=1))
    if orc["valid"] != 1:
        raise Rejected("oracle replay not valid")
    if orc["reward"] <= idle["reward"]:
        raise Rejected("oracle does not beat doing nothing")


GENERATORS = {
    "deliver": lambda s, sp, o: gen_deliver(s, sp, o, "deliver"),
    "deliver_bid": lambda s, sp, o: gen_deliver(s, sp, o, "deliver_bid"),
    "impossible": lambda s, sp, o: gen_deliver(s, sp, o, "impossible"),
    "tycoon": gen_tycoon,
}


def _one(family: str, seed: int, split: str, out: str) -> tuple[int, str]:
    if family == "simops" and "simops" not in GENERATORS:  # workers re-import this module (spawn)
        from railhead.gen.simops import gen_simops

        GENERATORS["simops"] = gen_simops
    try:
        spec = GENERATORS[family](seed, split, Path(out))
        return seed, f"ok {spec.task_id}"
    except Rejected as e:
        return seed, f"rejected: {e}"
    except Exception as e:  # noqa: BLE001
        return seed, f"error: {type(e).__name__}: {e}"


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    out, jobs, rest = Path("tasks"), 4, []
    it = iter(argv)
    for a in it:
        if a == "--out":
            out = Path(next(it))
        elif a == "--jobs":
            jobs = int(next(it))
        else:
            rest.append(a)
    family, split, lo, hi = rest[0], rest[1], int(rest[2]), int(rest[3])
    out.mkdir(parents=True, exist_ok=True)
    with ProcessPoolExecutor(max_workers=jobs) as ex:
        futs = [ex.submit(_one, family, s, split, str(out)) for s in range(lo, hi)]
        for f in as_completed(futs):
            seed, msg = f.result()
            print(f"{family}/{split}/{seed}: {msg}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
