"""SimOps: fault-injected network repair ("SWE-smith for simulations", docs/research/02 §7.6).

A scripted operator builds a healthy truck route and runs it until it is established; then a
seeded set of faults is injected with ordinary player actions; that broken state is the sealed
start. The agent must diagnose and repair it. Reward = run-off deliveries normalised between the
do-nothing counterfactual (k0) and the oracle repair (k*) — computed by exact replay.
"""

from __future__ import annotations

import random
import shutil
import tempfile
from pathlib import Path

from railhead.actionlog import ActionLog
from railhead.bridge import BridgeError
from railhead.gen.build import Rejected, _empty_log, _write, certify
from railhead.gen.families import pick_deliver_pair
from railhead.gen.scenario import sample_scenario, server_config, task_id
from railhead.oracle.road import OracleFailed, RoadOracle
from railhead.task import TaskSpec
from railhead.verify import verify
from railhead.world import World

FAULTS = ("road_gap", "orders_cleared", "fleet_stopped", "pickup_removed")
HORIZON, RUNOFF, WARMUP = 60, 365, 120


def inject(w: World, o: RoadOracle, net, fault: str) -> list[tuple[str, dict]]:
    """Apply a fault with player actions; return the oracle's repair as (method, args) pairs."""
    idx = o.idx
    fix: list[tuple[str, dict]] = []
    if fault == "fleet_stopped":
        for v in net.vehicles:
            w.call("vehicle_stop", vehicle=v)
            fix.append(("vehicle_start", {"vehicle": v}))
    elif fault == "orders_cleared":
        for v in net.vehicles:
            w.call("order_clear", vehicle=v)
            fix += [("order_append", {"vehicle": v, "tile": idx(net.pickup), "flags": ["full_load_any"]}),
                    ("order_append", {"vehicle": v, "tile": idx(net.drop), "flags": ["unload", "no_load"]})]
    elif fault == "road_gap":
        inner = net.path[2:-2]
        order = sorted(inner, key=lambda t: abs(inner.index(t) - len(inner) // 2))  # middle first
        mid = None
        for _attempt in range(20):
            for t in order:
                try:
                    w.call("land_demolish", tile=idx(t))
                    mid = t
                    break
                except BridgeError as e:
                    if e.code != "ERR_VEHICLE_IN_THE_WAY":
                        raise
            if mid is not None:
                break
            w.step(ticks=3)  # every candidate tile had a truck on it
        if mid is None:
            raise Rejected("could not open a road gap")
        i = net.path.index(mid)
        a, b = net.path[i - 1], net.path[i + 1]
        fix.append(("road_line", {"from_tile": idx(a), "to_tile": idx(mid)}))
        fix.append(("road_line", {"from_tile": idx(mid), "to_tile": idx(b)}))
    elif fault == "pickup_removed":
        for _attempt in range(30):  # a truck is usually loading there; wait for a gap
            try:
                w.call("land_demolish", tile=idx(net.pickup))
                break
            except BridgeError as e:
                if e.code != "ERR_VEHICLE_IN_THE_WAY":
                    raise
                w.step(ticks=10)
        else:
            raise Rejected("pickup stop never clear of vehicles")
        fix.append(("road_stop", {"tile": idx(net.pickup), "front_tile": idx(net.path[1]), "kind": "truck"}))
        fix.append(("road_line", {"from_tile": idx(net.path[1]), "to_tile": idx(net.pickup)}))
        for v in net.vehicles:  # orders to a demolished station are dropped by the engine
            fix += [("order_clear", {"vehicle": v}),
                    ("order_append", {"vehicle": v, "tile": idx(net.pickup), "flags": ["full_load_any"]}),
                    ("order_append", {"vehicle": v, "tile": idx(net.drop), "flags": ["unload", "no_load"]})]
    else:
        raise ValueError(fault)
    return fix


def gen_simops(seed: int, split: str, out: Path) -> TaskSpec:
    sc = sample_scenario(seed, split, "simops")
    rng = random.Random(sc["generation_seed"])
    faults = rng.sample(FAULTS, k=1 if rng.random() < 0.6 else 2)
    tid = task_id("simops", split, seed)
    tdir = out / tid
    work = Path(tempfile.mkdtemp(prefix=f"rh-gen-{tid}-"))
    try:
        tdir.mkdir(parents=True, exist_ok=True)
        with World.create(work / "gen", server_config(sc)) as w:
            w.step(days=31)  # one month of history so production figures are real
            pair = pick_deliver_pair(w)
            if pair is None:
                raise Rejected("no route to operate")
            src, dst, cargo = pair
            mid = w.call("monitor_add", kind="industry_delivery", cargo=cargo, target=dst["id"])
            o = RoadOracle(w)  # no log attached: the operator's build is history, not the agent's play
            try:
                net = o.deliver(src, dst, cargo)
            except OracleFailed as e:
                raise Rejected(f"operator failed: {e}") from None
            w.step(days=WARMUP)
            fix: list[tuple[str, dict]] = []
            for f in faults:
                fix += inject(w, o, net, f)
            w.save(tdir / "start.sav")
        start = tdir / "start.sav"
        spec = TaskSpec(
            task_id=tid, family="simops", horizon_days=HORIZON, runoff_days=RUNOFF,
            limits={"max_forks": 2, "max_fork_days": 120},
            objective={"monitor": mid, "cargo": cargo, "dest": dst["id"], "dest_name": dst["name"],
                       "source": src["id"], "source_name": src["name"]},
            scenario=sc, split=split, secret={"faults": faults, "k0": 0, "kstar": 1})
        _write(tdir, spec)
        oracle_log = tdir / "oracle.jsonl"
        with World.load(work / "fix", start) as w:
            w.attach_log(ActionLog(oracle_log))
            for m, a in fix:
                try:
                    w.act(m, **a)
                except Exception:  # noqa: BLE001 - a redundant repair step may legitimately fail
                    pass
            w.note("submit")
        k0 = verify(tdir, start, _empty_log(work / "idle.jsonl"), work / "k0")["runoff_delivered"]
        ks = verify(tdir, start, oracle_log, work / "ks")["runoff_delivered"]
        if ks < max(40, 1.5 * k0 + 10):
            raise Rejected(f"repair not clearly better (k0={k0}, k*={ks})")
        spec.secret = {"faults": faults, "k0": int(k0), "kstar": int(ks)}
        spec.title = f"Fix the {dst['name']} supply route"
        spec.briefing = (
            f"You have just taken over a transport company. Its truck route carrying cargo to **{dst['name']}** "
            f"(industry id {dst['id']}) from **{src['name']}** (industry id {src['id']}) used to work but deliveries "
            "have collapsed. Diagnose what is wrong and repair it.\n\n"
            f"You have {HORIZON} game-days. Then the company runs **{RUNOFF} days without you**, and the reward is the "
            "cargo it delivers to that industry during the run-off, scaled so that leaving the fault in place scores 0 "
            "and a correct repair scores 1. Your vehicles, stations and orders are all inspectable with `rh`.")
        _write(tdir, spec)
        certify(spec, tdir, work)
        return spec
    except Exception:
        shutil.rmtree(tdir, ignore_errors=True)
        raise
    finally:
        shutil.rmtree(work, ignore_errors=True)
