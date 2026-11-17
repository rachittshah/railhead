"""The Waybill wait barrier: when to step, by how much, whom to wake, and the straggler rule. Pure Python."""

from __future__ import annotations

import pytest

from railhead.multi.barrier import Barrier, Observation, Waiter, fired
from railhead.multi.log import TICKS_PER_DAY

D = TICKS_PER_DAY


class Clock:
    def __init__(self) -> None:
        self.t = 1000.0

    def __call__(self) -> float:
        return self.t


def test_no_step_until_every_live_company_waits():
    b = Barrier([0, 1, 2])
    assert not b.ready(0)
    b.add(Waiter(0, target=10 * D))
    b.add(Waiter(1, target=3 * D))
    assert not b.ready(0)
    b.add(Waiter(2, target=5))
    assert b.ready(0)


def test_step_to_the_earliest_target_and_wake_only_the_due():
    b = Barrier([0, 1, 2])
    b.add(Waiter(0, target=10 * D))
    b.add(Waiter(1, target=3 * D))
    b.add(Waiter(2, target=3 * D))
    n = b.next_ticks(0)
    assert n == 3 * D
    assert b.wake(n) == [(1, "elapsed"), (2, "elapsed")]
    assert set(b.waiting) == {0}
    assert not b.ready(n)


def test_wake_conditions_cap_the_step_to_one_chunk():
    b = Barrier([0, 1], chunk_ticks=7 * D)
    b.add(Waiter(0, target=100 * D, until=["new_month"], month=1))
    b.add(Waiter(1, target=100 * D))
    assert b.next_ticks(0) == 7 * D
    assert b.wake(7 * D, Observation(month=1)) == []
    assert b.wake(14 * D, Observation(month=2)) == [(0, "new_month")]
    assert set(b.waiting) == {1}


def test_steps_never_pass_the_horizon_and_everyone_wakes_there():
    b = Barrier([0, 1], horizon_tick=20 * D)
    b.add(Waiter(0, target=20 * D))
    b.add(Waiter(1, target=20 * D))
    b.waiting[1].target = 30 * D  # a target past the horizon (the coordinator caps them; be safe anyway)
    assert b.next_ticks(15 * D) == 5 * D
    assert b.wake(20 * D) == [(0, "horizon"), (1, "horizon")]
    b.add(Waiter(0, target=21 * D))
    b.add(Waiter(1, target=21 * D))
    assert not b.ready(20 * D)


def test_a_submitted_company_leaves_the_barrier():
    b = Barrier([0, 1, 2])
    b.add(Waiter(0, target=D))
    b.add(Waiter(2, target=D))
    b.submit(1)
    assert b.ready(0)
    with pytest.raises(ValueError):
        b.add(Waiter(1, target=D))
    b.submit(0)
    b.submit(2)
    assert not b.ready(0)  # nobody left: the world stops


def test_double_wait_is_refused():
    b = Barrier([0])
    b.add(Waiter(0, target=D))
    with pytest.raises(ValueError):
        b.add(Waiter(0, target=2 * D))


@pytest.mark.parametrize(("until", "obs", "expect"), [
    (["cash_below:5000"], Observation(cash={0: 4999}), "cash_below"),
    (["cash_below:5000"], Observation(cash={0: 5000, 1: 0}), None),  # another company's cash does not count
    (["company_in_trouble"], Observation(events=[{"k": "company_in_trouble", "company": 1}]), None),
    (["company_in_trouble"], Observation(events=[{"k": "company_in_trouble", "company": 0}]), "company_in_trouble"),
    (["vehicle_crashed"], Observation(events=[{"k": "vehicle_crashed", "vehicle": 99}]), None),
    (["vehicle_crashed"], Observation(events=[{"k": "vehicle_crashed", "vehicle": 7}]), "vehicle_crashed"),
    (["station_first_vehicle"], Observation(events=[{"k": "station_first_vehicle", "station": 3}]),
     "station_first_vehicle"),
    (["station_first_vehicle"], Observation(events=[{"k": "station_first_vehicle", "station": 4}]), None),
    (["industry_close"], Observation(events=[{"k": "industry_close", "industry": 12}]), "industry_close"),
    (["new_month"], Observation(month=5), None),
    (["new_month"], Observation(month=6), "new_month"),
    (["new_month", "cash_below:1"], Observation(month=6, cash={0: 0}), "new_month"),  # the first in the list
])
def test_wake_conditions_are_scoped_to_the_waiting_company(until, obs, expect):
    w = Waiter(0, target=100 * D, until=until, month=5, vehicles=frozenset({7}), stations=frozenset({3}))
    assert fired(w, obs) == expect


def test_a_straggler_is_overdue_after_its_turn_budget():
    clock = Clock()
    b = Barrier([0, 1, 2], clock=clock)
    b.add(Waiter(0, target=D))
    clock.t += 30
    assert b.overdue(60) == []
    assert b.overdue(None) == []
    clock.t += 31
    assert b.overdue(60) == [1, 2]  # company 0 waits: its turn is over
    b.add(Waiter(1, target=D, auto=True))
    b.add(Waiter(2, target=D))
    assert b.wake(b.next_ticks(0)) == [(0, "elapsed"), (1, "elapsed"), (2, "elapsed")]
    assert b.overdue(60) == []  # a wake starts a new turn
    clock.t += 61
    assert b.overdue(60) == [0, 1, 2]
