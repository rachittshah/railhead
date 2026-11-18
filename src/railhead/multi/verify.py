"""Verifier for a Waybill game: the net worth of each company from the sealed start and the v3 log only
(docs/WAYBILL.md, section 5).

  1. integrity: start.sav, task.json (companies, market config) and regions.json must match the sealed
     hashes (else tamper=1)
  2. the companies of the log header must be the companies of the task (id, slot, name), else rejected=1
  3. replay a private copy of the log (``replay_multi``) with the player-action whitelist and the task
     horizon (an illegal log => rejected=1). A divergence is attributed by a second replay, as in
     ``railhead.verify``: replays that agree with each other but not with the log => rejected=1;
     replays that disagree => valid=0
  4. the world runs on to the horizon (if the log ends before it) and through the run-off
  5. the net worth (cash minus loan) of each company at the horizon and after the run-off

    python -m railhead.multi.verify <task_dir> <start.sav> <actions.jsonl> <out_dir>

writes ``<out_dir>/scores.json``.
"""

from __future__ import annotations

import json
import shutil
import sys
import tempfile
from pathlib import Path

from railhead.actionlog import read_log
from railhead.multi.log import TICKS_PER_DAY
from railhead.multi.market import MarketConfig
from railhead.multi.regions import REGIONS_FILE, RegionMap
from railhead.multi.replay import horizon_tick, replay_multi
from railhead.replay import ReplayRejected, default_whitelist
from railhead.seal import check_sealed_file, check_start
from railhead.task import TaskSpec
from railhead.verify import _log_at_fault


def _norm(companies: list[dict]) -> list[tuple]:
    try:
        return sorted((int(c["id"]), int(c["slot"]), str(c["name"])) for c in companies)
    except (KeyError, TypeError, ValueError):
        return [("invalid",)]


def verify_multi(task_dir: Path, start_sav: Path, log: Path, workdir: Path) -> dict:
    task_dir, workdir = Path(task_dir), Path(workdir)
    spec = TaskSpec.load(task_dir)
    base: dict = {"valid": 1, "tamper": 0, "rejected": 0, "companies": []}
    if not check_start(task_dir, start_sav) or not check_sealed_file(task_dir, REGIONS_FILE, "regions_json") \
            or not check_sealed_file(task_dir, "task.json", "task_json"):
        return base | {"tamper": 1}
    regions = RegionMap.for_task(task_dir)
    market_cfg = MarketConfig.from_objective(spec.objective)
    workdir.mkdir(parents=True, exist_ok=True)
    if not Path(log).is_file():
        return base | {"rejected": 1}
    snap = workdir / "actions.jsonl"
    shutil.copyfile(log, snap)

    def run(sub: str, keep_open: bool, max_ticks: int | None = None):
        return replay_multi(start_sav, snap, workdir / sub, allowed=default_whitelist() or set(),
                            keep_open=keep_open, max_ticks=max_ticks, horizon_days=spec.horizon_days,
                            regions=regions, market_cfg=market_cfg)

    # The task, not the log, says which company is which slot: a header that relabels them is false.
    try:
        header = next(iter(read_log(snap))).raw
    except (StopIteration, ValueError):
        return base | {"rejected": 1}
    if _norm(header.get("companies") or []) != _norm(spec.objective.get("companies") or []):
        return base | {"rejected": 1}
    try:
        res = run("replay", keep_open=True)
    except (ReplayRejected, ValueError, KeyError, TypeError):
        return base | {"rejected": 1}
    sw = res.world
    assert sw is not None
    try:
        if res.divergences and _log_at_fault(res, run):
            return base | {"rejected": 1, "divergences": len(res.divergences)}
        end = horizon_tick(res.start_ticks, spec.horizon_days)
        replay_nw = dict(res.net_worth)
        mk = res.market
        while end is not None and sw.ticks < end:  # the market keeps settling until the horizon
            stop = mk.next_forced(sw.ticks) if mk is not None else None
            sw.advance(min(end, stop) - sw.ticks if stop is not None else end - sw.ticks)
            if mk is not None:
                if mk.book.active:
                    mk.book.sample(sw.ticks, sw.monitor_rows())
                mk.tick_records(sw.ticks, sw.industries)
                mk.book.ensure(mk.ex, sw.ticks, sw.monitor_add)
        at_horizon = sw.net_worth()
        ledger = {int(c["id"]): mk.balance(int(c["id"])) for c in res.companies} if mk is not None else {}
        if spec.runoff_days > 0:
            sw.advance(spec.runoff_days * TICKS_PER_DAY)
        final = sw.net_worth()
        comps = []
        for c in res.companies:
            cid = int(c["id"])
            row = {"id": cid, "name": c["name"], "slot": int(c["slot"]),
                   "net_worth_log_end": replay_nw.get(cid, 0), "net_worth_horizon": at_horizon[cid],
                   "net_worth": final[cid]}
            if mk is not None:  # score = in-game net worth + ledger balance
                row |= {"ledger_balance": ledger[cid], "score": final[cid] + ledger[cid]}
            comps.append(row)
        extra = {"market": mk.state()} if mk is not None else {}
        return base | extra | {"companies": comps, "acts": res.acts, "failed_acts": res.failed_acts, "steps": res.steps,
                       "divergences": len(res.divergences), "valid": 0 if res.divergences else 1,
                       "ticks_log_end": res.end_ticks, "checksum_log_end": res.final_checksum,
                       "end_ticks": sw.ticks}
    finally:
        sw.close()


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    if len(argv) != 4:
        print(__doc__, file=sys.stderr)
        return 2
    task_dir, start_sav, log, out_dir = map(Path, argv)
    out_dir.mkdir(parents=True, exist_ok=True)
    work = Path(tempfile.mkdtemp(prefix="rh-wb-verify-"))
    try:
        try:
            out = verify_multi(task_dir, start_sav, log, work)
        except Exception as e:  # noqa: BLE001 - an infra failure still writes a (masked) score file
            out = {"valid": 0, "tamper": 0, "rejected": 0, "error": 1, "companies": []}
            print(f"verifier error: {type(e).__name__}: {e}", file=sys.stderr)
        (out_dir / "scores.json").write_text(json.dumps(out, indent=1))
        print(json.dumps(out))
    finally:
        shutil.rmtree(work, ignore_errors=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
