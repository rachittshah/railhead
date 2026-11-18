"""Log v3 (Waybill): record schema and ordering rules of the merged multi-company log. Pure Python."""

from __future__ import annotations

import pytest

from railhead.multi.log import (
    AUTO_WAIT_DAYS,
    TICKS_PER_DAY,
    LogRejected,
    LogState,
    meta_digest_v3,
    wait_target,
)

D = TICKS_PER_DAY
HEADER = {"kind": "header", "v": 3, "ticks": 100, "checksum": {"h": 1},
          "companies": [{"id": 0, "name": "A", "slot": 0}, {"id": 1, "name": "B", "slot": 1},
                        {"id": 2, "name": "C", "slot": 2}]}
CK = {"h": 7}


class Log:
    """Builds records with contiguous seq, like ActionLog, and feeds them to a LogState."""

    def __init__(self, horizon_tick: int | None = 100 + 365 * D, header: dict | None = None) -> None:
        self.state = LogState(dict(header or HEADER), horizon_tick=horizon_tick)
        self.seq = 1

    def feed(self, **r):
        r.setdefault("ticks", self.state.now)
        r["seq"] = self.seq
        self.state.check(r)
        self.seq += 1
        return r

    def act(self, c, m="road_line", ok=True):
        return self.feed(kind="act", company=c, m=m, a={"x": 1}, ok=ok)

    def wait(self, c, days=None, ticks=None, until=None, auto=False, target=None):
        a = {"days": days} if days is not None else {"ticks": ticks}
        if until:
            a["until"] = until
        t = target if target is not None else wait_target(self.state.now, a, self.state.horizon_tick)
        r = {"kind": "wait_req", "company": c, "a": a, "target": t}
        if auto:
            r["auto"] = True
        return self.feed(**r)

    def step(self, end, wakers):
        return self.feed(kind="step", end_ticks=end, checksum=CK,
                         wakers=[{"company": c, "reason": why} for c, why in wakers])


def test_an_honest_interleaving_is_accepted_and_the_clock_moves_only_on_steps():
    log = Log()
    log.act(0)
    log.act(1)
    log.wait(0, days=10)
    log.act(2)  # acts of companies that do not wait keep log order at the same tick
    log.wait(1, days=3)
    log.wait(2, ticks=5)
    assert log.state.now == 100
    log.step(105, [(2, "elapsed")])
    assert log.state.now == 105 and set(log.state.waiting) == {0, 1}
    log.act(2)
    log.wait(2, days=1)
    log.step(105 + D, [(2, "elapsed")])
    log.wait(2, days=30)
    log.step(100 + 3 * D, [(1, "elapsed")])
    log.act(1)
    log.wait(1, days=30)
    log.step(100 + 10 * D, [(0, "elapsed")])
    assert set(log.state.waiting) == {1, 2}


def test_the_header_must_be_v3_with_companies():
    with pytest.raises(LogRejected):
        LogState(dict(HEADER, v=2))
    with pytest.raises(LogRejected):
        LogState(dict(HEADER, companies=[]))
    with pytest.raises(LogRejected):
        LogState(dict(HEADER, companies=[{"id": 0, "name": "A", "slot": 0}, {"id": 0, "name": "B", "slot": 1}]))
    with pytest.raises(LogRejected):
        LogState({"kind": "act", "v": 3})


def test_seq_must_be_contiguous_and_ticks_must_match_the_clock():
    log = Log()
    with pytest.raises(LogRejected, match="out of sequence"):
        log.state.check({"kind": "act", "seq": 2, "ticks": 100, "company": 0, "m": "x", "a": {}})
    with pytest.raises(LogRejected, match="clock"):
        log.feed(kind="act", ticks=99, company=0, m="x", a={})


def test_unknown_company_and_unknown_kind_are_rejected():
    log = Log()
    with pytest.raises(LogRejected, match="not in the header"):
        log.act(9)
    with pytest.raises(LogRejected, match="not in the header"):
        log.feed(kind="act", company=True, m="x", a={})
    with pytest.raises(LogRejected, match="unknown record kind"):
        log.feed(kind="wait", company=0)


def test_a_waiting_company_cannot_act_meta_or_wait_again():
    log = Log()
    log.wait(0, days=5)
    for bad in (lambda: log.act(0), lambda: log.feed(kind="meta", company=0, m="flag", a={}),
                lambda: log.wait(0, days=1)):
        with pytest.raises(LogRejected, match="waiting"):
            bad()


def test_a_step_needs_every_live_company_to_wait():
    log = Log()
    log.wait(0, days=5)
    log.wait(1, days=5)
    with pytest.raises(LogRejected, match="lockstep"):
        log.step(105, [])


def test_a_step_cannot_pass_the_earliest_target():
    log = Log()
    for c, d in ((0, 5), (1, 2), (2, 9)):
        log.wait(c, days=d)
    with pytest.raises(LogRejected, match="earliest"):
        log.step(100 + 3 * D, [(1, "elapsed")])


def test_a_step_must_advance_and_carry_a_checksum():
    log = Log()
    for c in range(3):
        log.wait(c, days=1)
    with pytest.raises(LogRejected, match="advance"):
        log.step(100, [])
    with pytest.raises(LogRejected, match="checksum"):
        log.feed(kind="step", end_ticks=100 + D, checksum={}, wakers=[])


def test_wakers_must_be_waiting_and_due_waiters_must_wake():
    log = Log()
    for c, d in ((0, 1), (1, 1), (2, 4)):
        log.wait(c, days=d)
    with pytest.raises(LogRejected, match="did not wake"):
        log.step(100 + D, [(0, "elapsed")])
    log2 = Log()
    for c, d in ((0, 1), (1, 1), (2, 4)):
        log2.wait(c, days=d)
    with pytest.raises(LogRejected, match="cannot wake"):  # not due and no until: no early wake
        log2.step(100 + D, [(0, "elapsed"), (1, "elapsed"), (2, "elapsed")])
    log3 = Log()
    log3.wait(0, days=1)
    log3.wait(1, days=1)
    with pytest.raises(LogRejected, match="lockstep"):
        log3.step(100 + D, [(0, "elapsed"), (1, "elapsed"), (2, "elapsed")])


def test_an_early_wake_needs_the_condition_in_the_waiters_until_list():
    log = Log()
    log.wait(0, days=1)
    log.wait(1, days=30, until=["cash_below:5000", "vehicle_crashed"])
    log.wait(2, days=30)
    log.step(100 + D, [(0, "elapsed"), (1, "cash_below")])
    assert set(log.state.waiting) == {2}
    log.act(1)
    log.wait(1, days=30, until=["new_month"])
    log.wait(0, days=30)
    with pytest.raises(LogRejected, match="cannot wake"):
        log.step(100 + 2 * D, [(1, "vehicle_crashed")])


def test_wait_targets_are_capped_at_the_horizon_and_must_be_exact():
    log = Log(horizon_tick=100 + 10 * D)
    assert wait_target(100, {"days": 30}, 100 + 10 * D) == 100 + 10 * D
    with pytest.raises(LogRejected, match="target"):
        log.wait(0, days=30, target=100 + 30 * D)
    log.wait(0, days=30)
    log.wait(1, days=30)
    log.wait(2, days=2)
    log.step(100 + 2 * D, [(2, "elapsed")])
    log.wait(2, days=30)
    with pytest.raises(LogRejected, match="cannot wake"):
        log.step(100 + 10 * D, [(0, "elapsed"), (1, "elapsed"), (2, "elapsed")])


def test_at_the_horizon_every_waiter_wakes_and_nothing_more_happens():
    log = Log(horizon_tick=100 + 10 * D)
    for c in range(3):
        log.wait(c, days=30)
    log.step(100 + 10 * D, [(0, "horizon"), (1, "horizon"), (2, "horizon")])
    with pytest.raises(LogRejected, match="horizon"):
        log.act(0)
    with pytest.raises(LogRejected, match="horizon"):
        log.wait(0, days=1)
    log.feed(kind="meta", company=0, m="flag", a={"note": "x"})  # meta decisions stay allowed


def test_bad_wait_arguments_are_rejected():
    log = Log()
    for a in ({}, {"days": 1, "ticks": 5}, {"days": 0}, {"days": True}, {"days": 1, "until": ["nope"]},
              {"days": 1, "until": "new_month"}, {"days": 1, "x": 2}):
        with pytest.raises(LogRejected):
            log.feed(kind="wait_req", company=0, a=a, target=200)
    with pytest.raises(LogRejected, match="auto"):
        log.feed(kind="wait_req", company=0, a={"days": 1}, target=100 + D, auto="yes")


def test_an_auto_wait_counts_as_a_wait():
    log = Log()
    log.wait(0, days=AUTO_WAIT_DAYS, auto=True)
    assert log.state.waiting[0]["auto"] is True
    log.wait(1, days=1)
    log.wait(2, days=1)
    log.step(100 + D, [(1, "elapsed"), (2, "elapsed")])


def test_a_submitted_company_leaves_the_lockstep_and_cannot_act():
    log = Log()
    log.feed(kind="meta", company=1, m="submit", a={"confirm": "yes"})
    with pytest.raises(LogRejected, match="submitted"):
        log.act(1)
    log.wait(0, days=1)
    log.wait(2, days=1)
    log.step(100 + D, [(0, "elapsed"), (2, "elapsed")])  # the world steps on without company 1
    assert log.state.live == {0, 2}


def test_no_step_when_every_company_has_submitted():
    log = Log()
    for c in range(3):
        log.feed(kind="meta", company=c, m="submit", a={"confirm": "yes"})
    with pytest.raises(LogRejected, match="no live company"):
        log.step(100 + D, [])


def test_the_meta_digest_binds_the_company():
    a = {"confirm": "yes"}
    assert meta_digest_v3(0, "submit", a) != meta_digest_v3(1, "submit", a)
    assert meta_digest_v3(0, "submit", a) == meta_digest_v3(0, "submit", dict(a))
    assert 0 < meta_digest_v3(2, "flag", {"note": "x"}) < 2 ** 31


def test_the_single_agent_replay_rejects_a_v3_log(tmp_path):
    """The version check runs before the engine starts: a v3 log never reaches the single-agent verifier."""
    import json

    from railhead.replay import ReplayRejected, replay

    log = tmp_path / "a.jsonl"
    log.write_text(json.dumps(HEADER) + "\n")
    with pytest.raises(ReplayRejected, match="unknown log version"):
        replay(tmp_path / "start.sav", log, tmp_path / "w")
