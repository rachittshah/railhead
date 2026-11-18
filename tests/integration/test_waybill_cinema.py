"""Waybill cinema on the engine: three stub agents play a short enforced game through the arena runner
(A grants B a licence inside A's region, B accepts, C sends a message); the cinema replays the merged log
with sampling, must reproduce result.json exactly, and renders frames and a still from the samples."""

from __future__ import annotations

import itertools
import json
from pathlib import Path

import pytest

from railhead.multi import arena as wa
from railhead.multi.gen import gen_track

pytestmark = [pytest.mark.integration, pytest.mark.timeout(1800)]

HORIZON = 40


def scripts(d: Path, task: Path) -> list[str]:
    regions = json.loads((task / "regions.json").read_text())["regions"]
    x0, y0, x1, y1 = regions["0"][0]
    rect = f"{x0 + 2},{y0 + 2},{min(x1, x0 + 8)},{min(y1, y0 + 8)}"
    waits = []
    for _ in range(HORIZON // 5 + 2):
        waits += ["rh wait days=5", "@sleep 0.3"]
    a = ["rh info", (f"rh offer kind=licence to=B grantor=me grantee=B rect={rect} window_start=+0d deadline=+30d "
         "fee=1000 expires_tick=+20d"), *waits]
    b = ["rh wait days=2 until=offer_received", "rh accept contract_id=C1", *waits]
    c = ['rh msg to=A text="hello from C, happy to trade later on."', *waits]
    out = []
    for name, lines in (("a", a), ("b", b), ("c", c)):
        p = d / f"{name}.txt"
        p.write_text("\n".join(lines) + "\n")
        out.append(f"stub:{p}")
    return out


def test_cinema_samples_a_verified_run_and_renders_frames(tmp_path):
    pytest.importorskip("matplotlib")
    import matplotlib.image as mpimg

    from railhead.cinema import main as cinema_main
    from railhead.cinema.sample import CinemaMismatch, cached, sample_run
    from railhead.cinema.scene import Arm, Scene, captions_for, load_arms, schedule_for

    tasks = tmp_path / "tasks"
    specs = gen_track("short", 7, tasks, rotations=[0], arms=("enforced",), horizon_days=HORIZON)
    task = tasks / specs[0].task_id
    run = tmp_path / "run"
    res = wa.Runner.create(task, run, scripts(tmp_path, task), turn_s=120, poll_s=0.3, grace_s=10).run()
    assert res["verify"]["clean"] and res["contracts"]["signed"] == 1, res["contracts"]

    out = tmp_path / "cinema"
    doc = sample_run(run, out)
    assert doc["check"]["ok"], doc["check"]
    assert cached(run, out) == "enforced"
    ss = doc["samples"]
    assert [s["t"] for s in ss] == sorted(s["t"] for s in ss) and ss[-1]["t"] == res["verify"]["end_ticks"]
    assert {s["phase"] for s in ss} >= {"play", "runoff"}
    grid = doc["grid_days"] * 74
    gaps = [b["t"] - a["t"] for a, b in itertools.pairwise(ss)]
    assert max(gaps) <= grid  # never more than one grid step between two samples
    assert ss[-1]["ex"]["signed"] == res["contracts"]["signed"]
    assert any(lc["status"] for lc in doc["exchange"]["contracts"].values())

    # a result.json that disagrees is refused before anything is rendered
    bad = tmp_path / "bad"
    bad.mkdir()
    for f in ("run.json", "result.json"):
        (bad / f).write_text((run / f).read_text())
    (bad / "coord" / "run").mkdir(parents=True)
    (bad / "coord" / "run" / "actions.jsonl").write_text((run / "coord" / "run" / "actions.jsonl").read_text())
    r = json.loads((bad / "result.json").read_text())
    r["companies"][0]["score"] += 1
    (bad / "result.json").write_text(json.dumps(r))
    with pytest.raises(CinemaMismatch):
        sample_run(bad, tmp_path / "cinema-bad")

    arms = load_arms(out, ["enforced"])
    a = arms[0]
    assert isinstance(a, Arm) and [e.kind for e in a.events if e.kind == "licence_signed"]
    sched = schedule_for(arms, seconds=4, hold_start=0.3, hold_end=0.3)
    caps = captions_for(arms, sched, quotes=True, hold_s=1.0)
    scene = Scene(arms, "main", "dark", 1920, 1080, 1.0, sched, caps, out / "base" / "minimap.png")
    try:
        for f in (0, len(sched) // 2, len(sched) - 1):
            assert bytes(scene.frame(f))
    finally:
        scene.close()
    end_ymd = ss[-1]["date"]
    assert cinema_main([str(run), "--out", str(out), "--render-only", "--theme", "light", "--cuts", "main",
                        "--still-only", end_ymd]) == 0
    still = out / "stills" / f"cinema-enforced-light-at-{end_ymd}.png"
    assert mpimg.imread(still).shape[:2] == (1080, 1920)
