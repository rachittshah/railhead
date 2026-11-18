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


def test_the_short_track_has_a_run_off_and_a_cross_region_minimum():
    s = TRACKS["short"]
    assert s.runoff_days == 365 and s.min_cross_only == 5 and TRACKS["decade"].runoff_days == 365


def test_the_rotation_schedule_balances_vendors_over_region_types():
    from railhead.multi.gen import rotation_schedule

    roster = ["claude", "codex", "claude", "codex"]
    s = rotation_schedule("short", list(range(100, 130)), roster)  # 30 seeds
    assert s["balanced"] and s["balance"]["claude"] == {"0": 15, "1": 15, "2": 15, "3": 15}
    assert [g["rotation"] for g in s["games"][:5]] == [0, 1, 2, 3, 0]
    assert s["games"][1]["seats"][3]["region_type"] == 0
    seat0 = rotation_schedule("short", list(range(30)), ["a", "b", "c", "d"])  # one seat per player: 8,8,7,7
    assert not seat0["balanced"] and seat0["balance"]["a"] == {"0": 8, "1": 8, "2": 7, "3": 7}
    assert TRACKS["decade"].min_cross_only == 10
