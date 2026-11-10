"""Proof-of-play verifier (DESIGN.md M1 + M2).

reward = f(sealed start state, the agent's action log) — nothing the agent did to the live game counts.

  1. integrity: start.sav must match the sealed hash (else tamper=1, reward 0)
  2. replay a private copy of the log with the player-action whitelist, capped at the horizon
     (illegal log => rejected=1, reward 0). On a replay/live mismatch, replay again: two replays
     that agree with each other but not with the log => the log is false (rejected=1); two replays
     that disagree => engine nondeterminism (valid=0, an infra problem, never silently scored)
  3. the world keeps running without the agent until the task horizon
  4. read the objective ledger (engine cargo monitors) at the horizon
  5. run-off: N more game-days with the agent frozen; net worth afterwards
  6. family reward in [0,1] + flat numeric diagnostics -> reward.json

    python -m railhead.verify <task_dir> <start.sav> <actions.jsonl> <out_dir> [--credit] [--min-log-version=N]

The command line needs a v2 log by default (``actionlog.LOG_VERSION``): the verifier, not the log,
sets the minimum. ``--min-log-version=1`` re-verifies old (v1) agent logs; the Python function
``verify`` defaults to 1 for the generator and for ``railhead recheck`` of the tracked v1 oracle logs.
"""

from __future__ import annotations

import json
import os
import shutil
import sys
import tempfile
import time
from pathlib import Path

from railhead import rewards
from railhead.actionlog import LOG_VERSION, ActionLog, read_log
from railhead.replay import ReplayRejected, default_whitelist, replay
from railhead.seal import check_start
from railhead.task import TaskSpec

TICKS_PER_DAY = 74
# Turn credit stops starting new turns after this many seconds of verify time (Harbor verifier: 900 s).
CREDIT_BUDGET_S = 600.0


def _meta(log: Path, name: str) -> list[dict]:
    try:
        return [r.raw for r in read_log(log) if r.kind == "meta" and r.raw.get("m") == name]
    except (ValueError, OSError):
        return []


def _net_worth(world) -> int:
    me = world.state()["company"]
    for c in world.call("companies"):
        if c["id"] == me:
            return int(c["money"]) - int(c["loan"])
    return 0


def _monitor_total(world, mid: int | None) -> int:
    if mid is None:
        return 0
    for m in world.call("monitor_read"):
        if m["id"] == mid:
            return int(m["total"])
    return 0


def score(spec: TaskSpec, world, log: Path, start_date: int, start_ticks: int = 0,
          metas: list[dict] | None = None) -> dict:
    """Run the world to the horizon and through the run-off, then compute the family reward.

    ``metas``: the replayed meta records (``ReplayResult.metas``, ticks in the world's frame). Without
    them the meta records are read from ``log`` as written (turn credit only)."""
    def meta(name: str) -> list[dict]:
        if metas is None:
            return _meta(log, name)
        return [m for m in metas if m.get("m") == name]

    obj = spec.full_objective
    end_date = start_date + spec.horizon_days
    today = world.state()["date"]
    if today < end_date:
        world.step(days=end_date - today)
    out: dict[str, float] = {}
    mids = obj.get("monitors") or ([obj["monitor"]] if "monitor" in obj else [])
    at_horizon = sum(_monitor_total(world, m) for m in mids)
    out["delivered"] = at_horizon
    out["net_worth_horizon"] = _net_worth(world)
    if spec.runoff_days > 0:
        world.step(days=spec.runoff_days)
    after_runoff = sum(_monitor_total(world, m) for m in mids)
    out["runoff_delivered"] = after_runoff - at_horizon
    out["net_worth"] = _net_worth(world)
    abstained = bool(meta("abstain"))
    out["abstained"] = int(abstained)
    out["submitted"] = int(bool(meta("submit")))

    fam = spec.family
    if fam in ("deliver", "passengers"):
        feasible = bool(obj.get("feasible", True))
        out["quantity"] = obj.get("quantity", 0)
        if not feasible:
            r = rewards.abstain(abstained, feasible=False)
        elif obj.get("bid"):
            deadline = start_ticks + int(obj.get("bid_deadline_days", 30)) * TICKS_PER_DAY
            # int only, as the daemon writes it (not bool, float or NaN; the replay rejects the others)
            bids = [m["a"].get("quantity") for m in meta("promise")
                    if type(m["a"].get("quantity")) is int and int(m.get("ticks", 0)) <= deadline]
            b = float(bids[-1]) if bids else 0.0
            out["bid"] = b
            r = 0.0 if abstained else rewards.bid(at_horizon, b, scale=float(obj.get("bid_scale", obj["quantity"])))
        else:
            r = 0.0 if abstained else rewards.deliver(at_horizon, float(obj["quantity"]))
    elif fam == "simops":
        r = rewards.simops(out["runoff_delivered"], float(obj["k0"]), float(obj["kstar"]))
    elif fam == "tycoon":
        score = rewards.tycoon_log if obj.get("score") == "log50" else rewards.tycoon
        r = score(out["net_worth"], float(obj["w_idle"]), float(obj["w_base"]))
        out["gain_vs_baseline"] = (out["net_worth"] - float(obj["w_idle"])) / max(float(obj["w_base"]) - float(obj["w_idle"]), 1.0)
    else:
        raise ValueError(f"unknown task family {fam!r}")
    out["reward"] = float(r)
    return out


def verify(task_dir: Path, start_sav: Path, log: Path, workdir: Path, credit: bool = False,
           min_version: int = 1) -> dict:
    task_dir, workdir = Path(task_dir), Path(workdir)
    spec = TaskSpec.load(task_dir)
    base = {"reward": 0.0, "valid": 1, "tamper": 0, "rejected": 0}
    if not check_start(task_dir, start_sav):
        return base | {"tamper": 1}
    # One private copy: the replay, the second replay, the meta decisions and the credit all read it.
    workdir.mkdir(parents=True, exist_ok=True)
    if not Path(log).is_file():  # a directory, device or FIFO in place of the log: log fault, not infra
        return base | {"rejected": 1}
    snap = workdir / "actions.jsonl"
    shutil.copyfile(log, snap)
    log = snap

    def run(sub: str, keep_open: bool, max_ticks: int | None = None):
        return replay(start_sav, log, workdir / sub, allowed=default_whitelist() or set(), keep_open=keep_open,
                      max_ticks=max_ticks, min_version=min_version, horizon_days=spec.horizon_days)

    try:
        res = run("replay", keep_open=True)
    except (ReplayRejected, ValueError, KeyError, TypeError):
        return base | {"rejected": 1}
    world = res.world
    assert world is not None
    try:
        if res.divergences and _log_at_fault(res, run):
            return base | {"rejected": 1, "divergences": len(res.divergences)}
        # Horizon: the agent's time ends at start_date + horizon_days, whatever the log claims.
        start_date, start_ticks = res.start_date, res.start_ticks
        if world.state()["date"] > start_date + spec.horizon_days:
            return base | {"rejected": 1}
        out = base | score(spec, world, log, start_date, start_ticks, metas=res.metas)
        out.update(acts=res.acts, failed_acts=res.failed_acts, waits=res.waits,
                   divergences=len(res.divergences), valid=0 if res.divergences else 1)
    finally:
        world.close()
    if credit:
        out["credit_turns"] = len(turn_credit(spec, start_sav, log, workdir, out["reward"]))
    return out


def _log_at_fault(first, run) -> bool:
    """Attribute a live/replay divergence. Replay once more, up to the first divergent check point
    only (so a forged divergence costs at most one extra partial replay). If both replays computed
    the same check points up to and including that one, the engine is deterministic and the log
    carries an outcome this play cannot give: a forged or edited log. If they differ, the engine is
    not deterministic: an infra fault."""
    d = first.divergences[0]
    n = next(i for i, cp in enumerate(first.checkpoints) if cp[:2] == d["key"]) + 1
    second = run("replay2", keep_open=False, max_ticks=d["stop"])
    return second.checkpoints[:n] == first.checkpoints[:n]


def _start_point(start_sav: Path, workdir: Path) -> tuple[int, int]:
    from railhead.world import World

    with World.load(workdir / "probe", start_sav) as w:
        st = w.state()
        return int(st["date"]), int(st["ticks"])


def _turns(log: Path) -> list[list[int]]:
    """Group consecutive act records (by seq) into turns."""
    turns: list[list[int]] = []
    cur: list[int] = []
    for r in read_log(log):
        if r.kind == "act":
            cur.append(int(r.raw["seq"]))
        elif cur:
            turns.append(cur)
            cur = []
    if cur:
        turns.append(cur)
    return turns


def turn_credit(spec: TaskSpec, start_sav: Path, log: Path, workdir: Path, full_reward: float,
                deadline: float | None = None) -> list[dict]:
    """Leave-one-turn-out counterfactual credit by exact replay (DESIGN.md M5). With ``deadline`` (a
    ``time.monotonic()`` value) it stops before a turn that starts after it: the result is then partial."""
    credits = []
    records = [r.raw for r in read_log(log)]
    d0, t0 = _start_point(start_sav, workdir)  # the same for every ablation: boot the probe engine once
    for k, turn in enumerate(_turns(log)):
        if deadline is not None and time.monotonic() > deadline:
            break
        drop = set(turn)
        tmp = Path(tempfile.mkdtemp(dir=workdir))
        ablated = tmp / "ablated.jsonl"
        al = ActionLog(ablated)
        for rec in records:
            if rec.get("kind") == "act" and rec.get("seq") in drop:
                continue
            al.write({k2: v for k2, v in rec.items() if k2 != "seq"})
        al.close()
        res = replay(start_sav, ablated, tmp / "w", allowed=default_whitelist() or set(), keep_open=True)
        try:
            r = score(spec, res.world, ablated, d0, t0)["reward"]
        finally:
            res.world.close()
            shutil.rmtree(tmp, ignore_errors=True)
        credits.append({"turn": k, "seqs": turn, "reward_without": r, "credit": full_reward - r})
    (workdir / "credit.json").write_text(json.dumps(credits, indent=1))
    return credits


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    credit = "--credit" in argv
    min_version = LOG_VERSION
    for a in argv:
        if a.startswith("--min-log-version="):
            min_version = int(a.split("=", 1)[1])
    argv = [a for a in argv if a != "--credit" and not a.startswith("--min-log-version=")]
    if len(argv) != 4:
        print(__doc__, file=sys.stderr)
        return 2
    task_dir, start_sav, log, out_dir = map(Path, argv)
    out_dir.mkdir(parents=True, exist_ok=True)
    work = Path(tempfile.mkdtemp(prefix="rh-verify-"))
    t0 = time.monotonic()
    try:
        try:
            out = verify(task_dir, start_sav, log, work, min_version=min_version)
        except Exception as e:  # noqa: BLE001 - infra failure must still produce a (masked) reward file
            out = {"reward": 0.0, "valid": 0, "tamper": 0, "rejected": 0, "error": 1}
            print(f"verifier error: {type(e).__name__}: {e}", file=sys.stderr)
        # The reward files first: turn credit replays the log once per turn, so it can be slow, and a
        # failure or a Harbor time-out in it must not cost the reward (a dropout on demand).
        (out_dir / "reward.json").write_text(json.dumps(out))
        (out_dir / "reward.txt").write_text(f"{out['reward']}\n")
        print(json.dumps(out))
        if credit and out.get("valid") == 1 and not out.get("rejected") and not out.get("tamper"):
            try:
                # work/actions.jsonl: the private copy of the log that verify() scored
                credits = turn_credit(TaskSpec.load(task_dir), start_sav, work / "actions.jsonl", work, out["reward"],
                                      deadline=t0 + CREDIT_BUDGET_S)
                out["credit_turns"] = len(credits)
                tmp = out_dir / "reward.json.tmp"  # atomic: a kill here must not leave a torn reward.json
                tmp.write_text(json.dumps(out))
                os.replace(tmp, out_dir / "reward.json")
                shutil.copy(work / "credit.json", out_dir / "credit.json")
            except Exception as e:  # noqa: BLE001 - credit is a diagnostic; the reward stands
                print(f"credit error: {type(e).__name__}: {e}", file=sys.stderr)
    finally:
        shutil.rmtree(work, ignore_errors=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
