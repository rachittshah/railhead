"""Waybill cinema: a verified Waybill run as a video and stills (docs/WAYBILL.md, "Cinema").

    railhead waybill-cinema <run_dir> [<run_dir_2>] --out DIR [--task DIR [--task2 DIR]]
                            [--sample-only | --render-only] [--resample] [--grid-days 2]
                            [--theme dark,light] [--seconds 60] [--cuts main,split,social,square,stills]
                            [--4k] [--no-quotes] [--still-only DATE] [--engine-shots]

Two stages, like ``railhead timelapse``:

1. **Sample** (needs the engine; :mod:`railhead.cinema.sample`). Each run's merged log is replayed from the
   sealed start with the Waybill replay itself and read on a fine tick grid; every number that
   ``result.json`` also has must agree, else nothing is rendered.
2. **Render** (matplotlib + ffmpeg, no engine; :mod:`railhead.cinema.scene`). With two runs of the same
   seed (two arms), the main cut is the split screen, synchronised by game date. ``--engine-shots`` first
   takes real OpenTTD close-ups of key sites (:mod:`railhead.cinema.shots`, needs the GUI build and a
   display) and shows them as inserts with the captions.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path


def _opt(argv: list[str], name: str, default: str | None = None) -> str | None:
    return argv[argv.index(name) + 1] if name in argv else default


def _positional(argv: list[str]) -> list[str]:
    takes = {"--out", "--task", "--task2", "--grid-days", "--theme", "--seconds", "--cuts", "--still-only"}
    out, skip = [], False
    for a in argv:
        if skip:
            skip = False
        elif a in takes:
            skip = True
        elif not a.startswith("--"):
            out.append(a)
    return out


def main(argv: list[str]) -> int:
    runs = _positional(argv)
    if not runs or len(runs) > 2 or "--out" not in argv:
        print(__doc__, file=sys.stderr)
        return 2
    out = Path(_opt(argv, "--out"))
    tasks = [_opt(argv, "--task"), _opt(argv, "--task2")]
    rc = 0
    arms: list[str] = []
    if "--render-only" not in argv:
        from railhead.cinema.sample import CinemaMismatch, cached, sample_run

        for i, run in enumerate(runs):
            task = Path(tasks[i]) if tasks[i] else None
            hit = None if "--resample" in argv else cached(Path(run), out)
            if hit:
                print(json.dumps({"run": Path(run).name, "arm": hit, "cached": True}), flush=True)
                arms.append(hit)
                continue
            try:
                doc = sample_run(Path(run), out, task, grid_days=int(_opt(argv, "--grid-days", "2")))
            except CinemaMismatch as e:
                print(f"waybill-cinema: {e}", file=sys.stderr)
                return 1
            arms.append(doc["arm"])
            print(json.dumps({"run": Path(run).name, "arm": doc["arm"], "ok": doc["check"]["ok"], **doc["cost"]}),
                  flush=True)
    else:
        from railhead.cinema.sample import arm_of

        arms = [arm_of(Path(r)) for r in runs]
    if "--engine-shots" in argv:  # real OpenTTD close-ups from the sample stage's saves (needs the GUI build)
        from railhead.cinema.shots import make_shots

        for arm in arms:
            print(json.dumps({"arm": arm, "shots": [x["name"] for x in make_shots(out, arm)]}), flush=True)
    if "--sample-only" not in argv:
        from railhead.cinema.scene import render_all

        made = render_all(out, arms, themes=(_opt(argv, "--theme", "dark,light") or "").split(","),
                          seconds=float(_opt(argv, "--seconds", "60")),
                          cuts=(_opt(argv, "--cuts", "main,split,social,square,stills") or "").split(","),
                          master_4k="--4k" in argv, quotes="--no-quotes" not in argv,
                          still_only=_opt(argv, "--still-only"))
        for p in made:
            print(p)
    return rc
