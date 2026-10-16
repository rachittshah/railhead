"""Generate certified tasks: start state + objective + oracle log + certificates.

    railhead gen <family> <split> <seed_from> <seed_to> [--out tasks] [--jobs N]

families: deliver | deliver_bid | impossible | tycoon | simops
Every kept task has: a solvability certificate (oracle replay reward), the do-nothing reward, and a
target calibrated from what the scripted oracle actually achieved on that exact map.
"""

from __future__ import annotations

import json
import math
import os
import random
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
IMPOSSIBLE_SEED_OFFSET = 9000  # impossible tasks hide among deliver ids: deliver-<split>-9xxx


def public_scenario(sc: dict) -> dict:
    """Scenario fields the agent may see (variant/family would reveal e.g. an impossible task)."""
    return {k: v for k, v in sc.items() if k not in ("variant", "family")}


def noisy_targets(task: str, d: int) -> tuple[int, int]:
    """Contract quantity and bid cap from the oracle delivery d, with per-task noise so the agent
    cannot back out d from the instruction (deterministic in the task id)."""
    rng = random.Random(f"railhead-targets:{task}")
    q = max(20, int(round(d * rng.uniform(0.5, 0.7), -1)))
    return q, max(q + 10, int(round(q * rng.uniform(2.0, 3.0), -1)))


def gen_deliver(seed: int, split: str, out: Path, variant: str = "deliver") -> TaskSpec:
    sc = sample_scenario(seed, split, variant)
    if variant == "impossible":
        tid = task_id("deliver", split, IMPOSSIBLE_SEED_OFFSET + seed)
    else:
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
                        scenario=public_scenario(sc), split=split)
        _write(tdir, spec)
        oracle_log = tdir / "oracle.jsonl"
        if variant == "impossible":
            months = math.ceil(DELIVER_HORIZON / 30)
            q = int(max(1000, 5 * max(total_prod, 1) * months))
            q = int(math.ceil(q / 1000.0) * 1000)
            objective.update(quantity=q)
            spec.secret = {"feasible": False, "variant": variant, "map_production_per_month": total_prod,
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
            q, scale = noisy_targets(tid, d)
            objective.update(quantity=q)
            spec.secret = {"feasible": True, "variant": variant, "oracle_delivered": d}
            if variant == "deliver_bid":
                objective.update(bid=True, bid_scale=scale, bid_deadline_days=30)
                _prepend_meta(oracle_log, "promise", {"quantity": min(d, scale)})
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
def gen_arena(seed: int, split: str, out: Path) -> TaskSpec:
    """Money-and-scale arena: 5 game years on a 256x256 map, 1-year run-off. Same scoring as tycoon."""
    return gen_tycoon(seed, split, out, family="arena", horizon=1825, runoff=365, map_log2=8)


def gen_tycoon(seed: int, split: str, out: Path, family: str = "tycoon", horizon: int = TYCOON_HORIZON,
               runoff: int = 730, map_log2: int | None = None) -> TaskSpec:
    sc = sample_scenario(seed, split, family)
    if map_log2:
        sc["map_log2"] = map_log2
    tid = task_id(family, split, seed)
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
        spec = TaskSpec(task_id=tid, family="tycoon", horizon_days=horizon, runoff_days=runoff,
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
        if family == "arena":
            spec.objective["score"] = "log50"  # unsaturated scale score: strong models beat the baseline 10x+
        spec.title = "Build a transport empire" if family == "tycoon" else "Arena: make money and scale"
        spec.briefing = (
            f"You run a new transport company with a {horizon}-day mandate. Build whatever network you like "
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
    "arena": gen_arena,
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
    # Run through the package module so worker processes and simops share one Rejected class.
    from railhead.gen import build as _pkg

    raise SystemExit(_pkg.main())


def harden_catalog(tasks_dir: Path) -> dict:
    """Apply the leak fixes to an existing catalog: rename impossible tasks into the deliver id space,
    scrub public scenario fields, re-derive noisy targets, rewrite briefings, re-certify bid tasks."""
    tasks_dir = Path(tasks_dir)
    stats = {"renamed": 0, "retargeted": 0, "recertified": 0}
    for tdir in sorted(p for p in tasks_dir.iterdir() if (p / "task.json").exists()):
        spec = TaskSpec.load(tdir)
        sc = spec.scenario
        variant = sc.get("variant") or spec.secret.get("variant") or sc.get("family") or spec.family
        if spec.family != "deliver":
            spec.scenario = public_scenario(sc)
            _write(tdir, spec)
            continue
        if variant == "impossible" and spec.task_id.startswith("impossible-"):
            seed = int(spec.task_id.rsplit("-", 1)[1])
            new_id = task_id("deliver", spec.split, IMPOSSIBLE_SEED_OFFSET + seed)
            new_dir = tasks_dir / new_id
            tdir.rename(new_dir)
            tdir, spec.task_id = new_dir, new_id
            stats["renamed"] += 1
        spec.scenario = public_scenario(sc)
        spec.secret = {**spec.secret, "variant": variant}
        d = spec.secret.get("oracle_delivered")
        if d and variant in ("deliver", "deliver_bid"):
            q, scale = noisy_targets(spec.task_id, int(d))
            spec.objective["quantity"] = q
            stats["retargeted"] += 1
            if variant == "deliver_bid":
                spec.objective["bid_scale"] = scale
                lines = (tdir / "oracle.jsonl").read_text().splitlines()
                out = []
                for line in lines:
                    rec = json.loads(line)
                    if rec.get("kind") == "meta" and rec.get("m") == "promise":
                        rec["a"] = {"quantity": min(int(d), scale)}
                    out.append(json.dumps(rec, separators=(",", ":"), sort_keys=True))
                (tdir / "oracle.jsonl").write_text("\n".join(out) + "\n")
        spec.title, spec.briefing = deliver_text(spec)
        _write(tdir, spec)
        if variant == "deliver_bid":
            work = Path(tempfile.mkdtemp(prefix="rh-harden-"))
            try:
                certify(spec, tdir, work)
                stats["recertified"] += 1
            finally:
                shutil.rmtree(work, ignore_errors=True)
    return stats


OPAQUE_FAMILIES = ("deliver", "deliver_bid", "simops")  # ids must not reveal the generator seed


def _salt() -> str:
    """Local, never-published salt for opaque task ids (~/.railhead/id_salt)."""
    p = Path.home() / ".railhead" / "id_salt"
    if not p.exists():
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(os.urandom(16).hex())
        p.chmod(0o600)
    return p.read_text().strip()


def opaque_catalog(tasks_dir: Path) -> dict:
    """Rename seed-revealing task ids to salted opaque ones and move map seeds into secret.json.

    The generator is public and deterministic, so `simops-train-0042` would let anyone recompute
    the injected faults (or a bid task's oracle delivery) offline. Idempotent."""
    import hashlib

    tasks_dir, salt = Path(tasks_dir), _salt()
    stats = {"renamed": 0, "scrubbed": 0}
    for tdir in sorted(p for p in tasks_dir.iterdir() if (p / "task.json").exists()):
        spec = TaskSpec.load(tdir)
        changed = False
        for k in ("seed", "generation_seed"):
            if k in spec.scenario:
                spec.secret.setdefault("scenario_private", {})[k] = spec.scenario.pop(k)
                changed = True
        prefix, split, tail = spec.task_id.rsplit("-", 2)[0], spec.split, spec.task_id.rsplit("-", 1)[1]
        if prefix in OPAQUE_FAMILIES and tail.isdigit():
            spec.secret["source_task_id"] = spec.task_id
            h = hashlib.sha256(f"{salt}:{spec.task_id}".encode()).hexdigest()[:6]
            new_id = f"{prefix}-{split}-{h}"
            new_dir = tasks_dir / new_id
            assert not new_dir.exists(), new_id
            tdir.rename(new_dir)
            tdir, spec.task_id = new_dir, new_id
            stats["renamed"] += 1
            changed = True
        if changed:
            _write(tdir, spec)
            stats["scrubbed"] += 1
    return stats
