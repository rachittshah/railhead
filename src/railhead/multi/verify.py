"""Verifier for a Waybill game: the net worth of each company from the sealed start and the v3 log only
(docs/WAYBILL.md, section 5).

  1. integrity: start.sav must match the sealed hash (else tamper=1)
  2. replay a private copy of the log (``replay_multi``) with the player-action whitelist and the task
     horizon (an illegal log => rejected=1). A divergence is attributed by a second replay, as in
     ``railhead.verify``: replays that agree with each other but not with the log => rejected=1;
     replays that disagree => valid=0
  3. the world runs on to the horizon (if the log ends before it) and through the run-off
  4. the net worth (cash minus loan) of each company at the horizon and after the run-off

    python -m railhead.multi.verify <task_dir> <start.sav> <actions.jsonl> <out_dir>

writes ``<out_dir>/scores.json``.
"""

from __future__ import annotations

import json
import shutil
import sys
import tempfile
from pathlib import Path

from railhead.multi.log import TICKS_PER_DAY
from railhead.multi.replay import horizon_tick, replay_multi
from railhead.replay import ReplayRejected, default_whitelist
from railhead.seal import check_start
from railhead.task import TaskSpec
from railhead.verify import _log_at_fault


def verify_multi(task_dir: Path, start_sav: Path, log: Path, workdir: Path) -> dict:
    task_dir, workdir = Path(task_dir), Path(workdir)
    spec = TaskSpec.load(task_dir)
    base: dict = {"valid": 1, "tamper": 0, "rejected": 0, "companies": []}
    if not check_start(task_dir, start_sav):
        return base | {"tamper": 1}
    workdir.mkdir(parents=True, exist_ok=True)
    if not Path(log).is_file():
        return base | {"rejected": 1}
    snap = workdir / "actions.jsonl"
    shutil.copyfile(log, snap)

    def run(sub: str, keep_open: bool, max_ticks: int | None = None):
        return replay_multi(start_sav, snap, workdir / sub, allowed=default_whitelist() or set(),
                            keep_open=keep_open, max_ticks=max_ticks, horizon_days=spec.horizon_days)

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
        if end is not None and sw.ticks < end:
            sw.advance(end - sw.ticks)
        at_horizon = sw.net_worth()
        if spec.runoff_days > 0:
            sw.advance(spec.runoff_days * TICKS_PER_DAY)
        final = sw.net_worth()
        comps = []
        for c in res.companies:
            cid = int(c["id"])
            comps.append({"id": cid, "name": c["name"], "slot": int(c["slot"]),
                          "net_worth_log_end": replay_nw.get(cid, 0), "net_worth_horizon": at_horizon[cid],
                          "net_worth": final[cid]})
        return base | {"companies": comps, "acts": res.acts, "failed_acts": res.failed_acts, "steps": res.steps,
                       "divergences": len(res.divergences), "valid": 0 if res.divergences else 1,
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
