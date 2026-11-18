"""Task tracks: sizes, paired arms, and region-type rotation across seeds (no engine)."""

from __future__ import annotations

import pytest

from railhead.multi.gen import TRACKS, plan_track


def test_track_sizes():
    s, d = TRACKS["short"], TRACKS["decade"]
    assert (s.agents, 2 ** s.map_log2, s.horizon_days) == (4, 256, 730)
    assert (d.agents, 2 ** d.map_log2, d.horizon_days) == (8, 512, 3650) and d.runoff_days > 0


def test_cycle_rotation_gives_each_slot_each_region_type_and_pairs_the_arms():
    plans = plan_track("short", [10, 11, 12, 13])
    assert len(plans) == 8
    by_seed = {}
    for p in plans:
        by_seed.setdefault(p.seed, set()).add((p.rotation, p.arm))
    for rs in by_seed.values():  # paired: both arms with the same rotation
        assert {a for _, a in rs} == {"enforced", "cheap_talk"} and len({r for r, _ in rs}) == 1
    n = TRACKS["short"].agents
    for slot in range(n):
        assert {(slot + p.rotation) % n for p in plans} == set(range(n))
    assert plans[0].task_id == "waybill-short-s0010-r0-enforced"
    assert len({p.task_id for p in plans}) == len(plans)


def test_all_rotations_and_explicit_rotations():
    plans = plan_track("decade", [5], rotations="all", arms=("enforced",))
    assert sorted(p.rotation for p in plans) == list(range(8))
    assert [p.rotation for p in plan_track("short", [1, 2], rotations=[3, 6])][::2] == [3, 2]
    with pytest.raises(ValueError):
        plan_track("short", [1, 2], rotations=[1])
    with pytest.raises(ValueError):
        plan_track("short", [1], arms=("honest",))


def test_a_horizon_override_shows_in_the_task_id():
    from railhead.multi.gen import TaskPlan

    assert TaskPlan("short", 3, 1, "enforced").task_id == "waybill-short-s0003-r1-enforced"
    assert TaskPlan("short", 3, 1, "enforced", 60).task_id == "waybill-short-s0003-r1-enforced-h60"
