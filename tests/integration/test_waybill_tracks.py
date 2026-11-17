"""The short track from one seed: one world, paired arms and two rotations share one start.sav; the
regions are balanced and sealed."""

from __future__ import annotations

import json

import pytest

from railhead.multi.gen import TRACKS, gen_track
from railhead.multi.regions import RegionMap
from railhead.multi.verify import verify_multi
from railhead.seal import file_sha256

pytestmark = [pytest.mark.integration, pytest.mark.timeout(900)]


def test_short_track_paired_arms_and_rotations(tmp_path):
    specs = gen_track("short", 21, tmp_path, rotations=[1, 2], start_balances={0: 10_000})
    assert len(specs) == 4
    dirs = {s.task_id: tmp_path / s.task_id for s in specs}
    assert {file_sha256(d / "start.sav") for d in dirs.values()} == {file_sha256(next(iter(dirs.values())) /
                                                                                 "start.sav")}
    n = TRACKS["short"].agents
    maps = {}
    for d in dirs.values():
        obj = json.loads((d / "task.json").read_text())["objective"]
        assert len(obj["companies"]) == n and obj["market"]["arm"] == obj["arm"] and obj["track"] == "short"
        assert obj["market"]["start_balances"] == {"0": 10_000}
        rm = RegionMap.load(d / "regions.json")
        assert (rm.map_x, rm.map_y) == (256, 256) and len(rm.regions) == n
        assert rm.types == {s: (s + obj["rotation"]) % n for s in range(n)}
        maps[(obj["rotation"], obj["arm"])] = rm.to_json()
        assert "regions_json" in json.loads((d / "sealed.json").read_text())
    assert maps[(1, "enforced")] == maps[(1, "cheap_talk")] and maps[(1, "enforced")] != maps[(2, "enforced")]
    # a changed regions.json is tamper
    d = dirs[specs[0].task_id]
    rm = RegionMap.load(d / "regions.json")
    rm.regions[0] = [[1, 1, 254, 254]]
    rm.save(d / "regions.json")
    (tmp_path / "empty.jsonl").write_text("")
    assert verify_multi(d, d / "start.sav", tmp_path / "empty.jsonl", tmp_path / "v")["tamper"] == 1
