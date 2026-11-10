"""Reward-integrity attacks (docs/VERIFICATION.md §5), run against a fake engine behind the real World.

Each ``test_attack_*`` performs one forgery or abuse and asserts the secure outcome. The fake engine
is deterministic (checksum = f(ticks, applied actions, meta digest)), so the real logging code in
``World`` writes honest logs and the real ``replay``/``verify`` check them, without an OpenTTD process.
"""

from __future__ import annotations

import itertools
import json
from types import SimpleNamespace

import pytest

from railhead import daemon as daemon_mod
from railhead.actionlog import ActionLog, read_log
from railhead.bridge import BridgeError
from railhead.task import TaskSpec
from railhead.verify import verify
from railhead.world import World

T0, D0 = 740, 1000  # start tick and date of the fake start state
_noise = itertools.count(1)


class FakeEngine:
    """Bridge stand-in: one deterministic game. ``noisy`` adds a per-instance term to the checksum."""

    def __init__(self, noisy: bool = False, apply_then_timeout: set[str] | None = None) -> None:
        self.ticks = T0
        self.applied: list[str] = []
        self.digest = 0
        self.noise = next(_noise) if noisy else 0
        self.apply_then_timeout = apply_then_timeout or set()
        self.sent: list[tuple[str, dict, float]] = []
        self.stepped = 0

    # the Bridge interface (both spellings: World may use either)
    def call(self, method: str, timeout: float = 30.0, **args):
        return self.request(method, args, timeout=timeout)

    def request(self, method: str, args: dict | None = None, timeout: float = 30.0):
        args = dict(args or {})
        self.sent.append((method, args, timeout))
        if method == "state":
            return {"ticks": self.ticks, "date": D0 + (self.ticks - T0) // 74, "company": 0, "ymd": "1950-01-01"}
        if method == "checksum":
            return {"h": self.ticks * 1000003 + 7 * len(self.applied) + self.digest + self.noise, "ticks": self.ticks}
        if method == "note":
            self.digest = (self.digest * 31 + int(args["d"])) % (1 << 40) or 1
            return {"noted": True}
        if method == "step":
            n = args["ticks"] if args.get("ticks") is not None else args["days"] * 74
            if "step" in self.apply_then_timeout:
                self.ticks += n
                raise TimeoutError("bridge call 'step' timed out")
            self.ticks += n
            self.stepped += n
            return {"ticks": self.ticks, "date": D0 + (self.ticks - T0) // 74, "days": n // 74, "reason": "elapsed"}
        if method == "monitor_read":
            return [{"id": 0, "total": self.delivered()}]
        if method == "companies":
            return [{"id": 0, "money": 1000 + len(self.applied), "loan": 0}]
        if method in ("company", "vehicles", "stations"):
            return {} if method == "company" else []
        if args.get("engine") == 99:
            raise BridgeError("ERR_FAKE", "refused")
        if method in self.apply_then_timeout or timeout < 1:
            self.applied.append(method)
            raise TimeoutError(f"bridge call {method!r} timed out after {timeout}s")
        self.applied.append(method)
        return {"vehicle": len(self.applied)}

    def delivered(self) -> int:
        return len(self.applied) * max(0, self.ticks - T0) // 74


def fake_world(engine: FakeEngine | None = None) -> World:
    w = World.__new__(World)
    w.bridge = engine or FakeEngine()
    w.admin = SimpleNamespace(close=lambda: None, welcome=SimpleNamespace(map_x=64, map_y=64))
    w.server = SimpleNamespace(stop=lambda: None, proc=None, game_port=0)
    w.log = None
    return w


@pytest.fixture
def engines(monkeypatch):
    """Every World.load returns a fresh fake world; the list collects their engines."""
    made: list[FakeEngine] = []
    state = {"noisy": False}

    def load(cls, workdir, savegame, config=None, speed=0):
        e = FakeEngine(noisy=state["noisy"])
        made.append(e)
        return fake_world(e)

    monkeypatch.setattr(World, "load", classmethod(load))
    made_ns = SimpleNamespace(list=made, state=state)
    return made_ns


@pytest.fixture
def bid_task(tmp_path):
    tdir = tmp_path / "task"
    tdir.mkdir()
    spec = TaskSpec(task_id="bid-demo", family="deliver", horizon_days=100, runoff_days=10,
                    objective={"bid": True, "quantity": 100, "bid_scale": 150, "monitor": 0, "bid_deadline_days": 30},
                    secret={"feasible": True})
    spec.save(tdir)
    (tdir / "start.sav").write_bytes(b"fake start state")
    return tdir


def honest_log(path, version=None):
    """A daemon-style log written by the real World: promise 10, buy one vehicle, wait 50 days."""
    w = fake_world()
    log = ActionLog(path)
    w.attach_log(log)
    if version is not None:  # rewrite the header version (an old log)
        log.close()
        lines = path.read_text().splitlines()
        hdr = json.loads(lines[0]) | {"v": version}
        path.write_text(json.dumps(hdr) + "\n")
        log = ActionLog(path)
        w.log, w.log_version = log, version
    w.note("promise", quantity=10)
    w.act("vehicle_buy", depot_tile=5, engine=1)
    w.wait(days=50)
    log.close()
    return path


def _records(path):
    return [r.raw for r in read_log(path)]


def _write(path, recs):
    path.write_text("".join(json.dumps(r, sort_keys=True) + "\n" for r in recs))
    return path


def _verify(task, log, tmp_path, **kw):
    return verify(task, task / "start.sav", log, tmp_path / "work", **kw)


# ------------------------------------------------------------------------------ honest play
def test_honest_log_verifies_and_scores(bid_task, engines, tmp_path):
    out = _verify(bid_task, honest_log(tmp_path / "a.jsonl"), tmp_path)
    assert out["valid"] == 1 and out["rejected"] == 0 and out["divergences"] == 0
    assert out["bid"] == 10.0 and out["delivered"] == 100  # 1 vehicle x 100 days (no actions after the log)
    assert 0 < out["reward"] < 0.6


# ------------------------------------------------------------------------------ finding 1: hindsight bid
def test_attack_hindsight_promise_appended_at_tick_zero_is_rejected(bid_task, engines, tmp_path):
    log = honest_log(tmp_path / "a.jsonl")
    recs = _records(log)
    recs.append({"kind": "meta", "seq": len(recs), "ticks": 0, "m": "promise", "a": {"quantity": 100}})
    out = _verify(bid_task, _write(log, recs), tmp_path)
    assert out["rejected"] == 1 and out["reward"] == 0.0


def test_attack_promise_appended_at_the_current_tick_after_the_deadline_is_ignored(bid_task, engines, tmp_path):
    log = honest_log(tmp_path / "a.jsonl")
    recs = _records(log)
    recs.append({"kind": "meta", "seq": len(recs), "ticks": recs[-1]["end_ticks"], "m": "promise",
                 "a": {"quantity": 100}})
    out = _verify(bid_task, _write(log, recs), tmp_path)
    assert out["rejected"] == 0 and out["bid"] == 10.0


def test_attack_promise_inserted_before_the_wait_is_rejected(bid_task, engines, tmp_path):
    """Insert a promise at the start tick and renumber seq: the wait checksum binds every meta record."""
    log = honest_log(tmp_path / "a.jsonl")
    recs = _records(log)
    forged = {"kind": "meta", "ticks": T0, "m": "promise", "a": {"quantity": 100}}
    recs = recs[:2] + [forged] + recs[2:]
    for i, r in enumerate(recs[1:], 1):
        r["seq"] = i
    out = _verify(bid_task, _write(log, recs), tmp_path)
    assert out["rejected"] == 1 and out["reward"] == 0.0 and out["valid"] == 1


def test_v1_log_in_a_header_tick_frame_is_honest(bid_task, engines, tmp_path):
    """Generator abstain/bid oracles: v1, header ticks 0, records at tick 0 while the start is later."""
    log = _write(tmp_path / "o.jsonl", [{"kind": "header", "v": 1, "ticks": 0},
                                        {"kind": "meta", "seq": 0, "ticks": 0, "m": "promise", "a": {"quantity": 10}}])
    out = _verify(bid_task, log, tmp_path)
    assert out["rejected"] == 0 and out["bid"] == 10.0


def test_attack_tick_frame_shift_cannot_move_a_late_promise_before_the_deadline(bid_task, engines, tmp_path):
    """v1 header at tick 0: a promise after a 40-day wait has a raw tick below the deadline. The deadline
    must use the replayed tick."""
    w = fake_world()
    log = ActionLog(tmp_path / "a.jsonl")
    w.attach_log(log)
    w.act("vehicle_buy", depot_tile=5, engine=1)
    w.wait(days=40)
    log.close()
    recs = _records(tmp_path / "a.jsonl")
    recs[0]["v"] = 1
    for r in recs:
        r["ticks"] -= T0
        if "end_ticks" in r:
            r["end_ticks"] -= T0
    recs.append({"kind": "meta", "seq": len(recs), "ticks": recs[-1]["end_ticks"], "m": "promise", "a": {"quantity": 40}})
    out = _verify(bid_task, _write(tmp_path / "a.jsonl", recs), tmp_path)
    assert out["rejected"] == 0 and out["bid"] == 0.0  # the promise is after the deadline


def test_attack_v2_log_cannot_use_a_shifted_tick_frame(bid_task, engines, tmp_path):
    log = honest_log(tmp_path / "a.jsonl")
    recs = _records(log)
    for r in recs:
        r["ticks"] -= T0
        if "end_ticks" in r:
            r["end_ticks"] -= T0
    assert _verify(bid_task, _write(log, recs), tmp_path)["rejected"] == 1


@pytest.mark.parametrize("seq", [None, 7, 2, "3"])
def test_attack_out_of_sequence_record_is_rejected(bid_task, engines, tmp_path, seq):
    log = honest_log(tmp_path / "a.jsonl")
    recs = _records(log)
    rec = {"kind": "meta", "ticks": recs[-1]["end_ticks"], "m": "flag", "a": {"note": "x"}}
    if seq is not None:
        rec["seq"] = seq
    recs.append(rec)
    assert _verify(bid_task, _write(log, recs), tmp_path)["rejected"] == 1


def test_attack_unknown_meta_decision_is_rejected(bid_task, engines, tmp_path):
    log = honest_log(tmp_path / "a.jsonl")
    recs = _records(log)
    recs.append({"kind": "meta", "seq": len(recs), "ticks": recs[-1]["end_ticks"], "m": "reward", "a": {}})
    assert _verify(bid_task, _write(log, recs), tmp_path)["rejected"] == 1


# ------------------------------------------------------------------------------ finding 2: dropout on demand
def test_attack_edit_one_checksum_is_rejected_not_dropped(bid_task, engines, tmp_path):
    log = honest_log(tmp_path / "a.jsonl")
    recs = _records(log)
    recs[-1]["checksum"]["h"] += 1
    out = _verify(bid_task, _write(log, recs), tmp_path)
    assert out["valid"] == 1 and out["rejected"] == 1 and out["reward"] == 0.0


def test_attack_divergence_costs_at_most_a_partial_second_replay(bid_task, engines, tmp_path):
    """A forged checksum early in a long log must not double the verify time (Harbor time-out = dropout)."""
    w = fake_world()
    log = ActionLog(tmp_path / "a.jsonl")
    w.attach_log(log)
    w.wait(days=5)
    w.act("vehicle_buy", depot_tile=5, engine=1)
    w.wait(days=90)
    log.close()
    recs = _records(tmp_path / "a.jsonl")
    recs[1]["checksum"]["h"] += 1  # the first wait
    out = _verify(bid_task, _write(tmp_path / "a.jsonl", recs), tmp_path)
    assert out["rejected"] == 1 and out["valid"] == 1
    first, second = engines.list
    assert second.stepped == 5 * 74 < first.stepped


def test_attack_strip_all_checksums_is_rejected(bid_task, engines, tmp_path):
    log = honest_log(tmp_path / "a.jsonl")
    recs = [{k: v for k, v in r.items() if not (r["kind"] == "wait" and k == "checksum")} for r in _records(log)]
    out = _verify(bid_task, _write(log, recs), tmp_path)
    assert out["rejected"] == 1 and out["reward"] == 0.0


def test_attack_old_log_version_does_not_skip_the_checksum_rule(bid_task, engines, tmp_path):
    log = honest_log(tmp_path / "a.jsonl", version=1)
    recs = [{k: v for k, v in r.items() if not (r["kind"] == "wait" and k == "checksum")} for r in _records(log)]
    out = _verify(bid_task, _write(log, recs), tmp_path)
    assert out["rejected"] == 1


def test_attack_old_log_version_is_refused_when_the_verifier_needs_v2(bid_task, engines, tmp_path):
    log = honest_log(tmp_path / "a.jsonl", version=1)
    assert _verify(bid_task, log, tmp_path, min_version=2)["rejected"] == 1
    assert _verify(bid_task, log, tmp_path / "b", min_version=1)["rejected"] == 0


@pytest.mark.parametrize("v", [0, 3, "2", None, True])
def test_attack_bad_header_version_is_rejected(bid_task, engines, tmp_path, v):
    log = honest_log(tmp_path / "a.jsonl")
    recs = _records(log)
    recs[0]["v"] = v
    assert _verify(bid_task, _write(log, recs), tmp_path)["rejected"] == 1


def test_header_only_log_is_no_play_at_any_version(bid_task, engines, tmp_path):
    log = _write(tmp_path / "empty.jsonl", [{"kind": "header", "v": 1, "ticks": 0}])
    out = _verify(bid_task, log, tmp_path, min_version=2)
    assert out["valid"] == 1 and out["rejected"] == 0 and out["acts"] == 0


def test_engine_nondeterminism_is_still_an_infrastructure_fault(bid_task, engines, tmp_path):
    log = honest_log(tmp_path / "a.jsonl")
    engines.state["noisy"] = True  # every replay engine now computes a different checksum
    out = _verify(bid_task, log, tmp_path)
    assert out["valid"] == 0 and out["rejected"] == 0
    assert len(engines.list) == 2  # attribution needed a second replay


# ------------------------------------------------------------------------------ finding 6: verifier stall
def test_attack_hundred_year_wait_is_rejected_without_simulating_it(bid_task, engines, tmp_path):
    log = honest_log(tmp_path / "a.jsonl")
    recs = _records(log)
    recs[-1]["end_ticks"] = recs[-1]["ticks"] + 36500 * 74
    out = _verify(bid_task, _write(log, recs), tmp_path)
    assert out["rejected"] == 1
    assert max(e.stepped for e in engines.list) <= (100 + 1) * 74


# ------------------------------------------------------------------------------ finding 8: act at the horizon
def test_attack_act_at_the_horizon_date_is_rejected(bid_task, engines, tmp_path):
    w = fake_world()
    log = ActionLog(tmp_path / "a.jsonl")
    w.attach_log(log)
    w.wait(days=100)
    w.act("vehicle_buy", depot_tile=5, engine=1)  # the daemon refuses this (days_left == 0)
    log.close()
    out = _verify(bid_task, tmp_path / "a.jsonl", tmp_path)
    assert out["rejected"] == 1


def test_submit_at_the_horizon_is_honest(bid_task, engines, tmp_path):
    w = fake_world()
    log = ActionLog(tmp_path / "a.jsonl")
    w.attach_log(log)
    w.wait(days=100)
    w.note("submit", confirm="yes")
    log.close()
    out = _verify(bid_task, tmp_path / "a.jsonl", tmp_path)
    assert out["rejected"] == 0 and out["submitted"] == 1


# ------------------------------------------------------------------------------ findings 3, 4: World
def test_attack_timeout_argument_cannot_shorten_the_action_timeout(tmp_path):
    eng = FakeEngine()
    w = fake_world(eng)
    w.attach_log(ActionLog(tmp_path / "a.jsonl"))
    w.act("vehicle_buy", timeout=0.001, depot_tile=5, engine=1)
    method, args, timeout = eng.sent[-1]
    assert method == "vehicle_buy" and timeout >= 60 and args["timeout"] == 0.001
    assert [r.raw["m"] for r in read_log(tmp_path / "a.jsonl") if r.kind == "act"] == ["vehicle_buy"]


def test_attack_applied_but_unanswered_action_is_a_world_fault_not_silence(tmp_path):
    from railhead.world import WorldFault

    eng = FakeEngine(apply_then_timeout={"vehicle_buy"})
    w = fake_world(eng)
    w.attach_log(ActionLog(tmp_path / "a.jsonl"))
    with pytest.raises(WorldFault):
        w.act("vehicle_buy", depot_tile=5, engine=1)
    assert not [r for r in read_log(tmp_path / "a.jsonl") if r.kind == "act"]


def test_wait_that_times_out_is_a_world_fault_and_is_not_logged(tmp_path):
    from railhead.world import WorldFault

    w = fake_world(FakeEngine(apply_then_timeout={"step"}))
    w.attach_log(ActionLog(tmp_path / "a.jsonl"))
    with pytest.raises(WorldFault):
        w.wait(days=3650)
    assert not [r for r in read_log(tmp_path / "a.jsonl") if r.kind == "wait"]


def test_long_wait_gets_a_timeout_that_grows_with_its_length():
    eng = FakeEngine()
    fake_world(eng).step(days=3650)
    assert eng.sent[-1][0] == "step" and eng.sent[-1][2] > 3650


def test_refused_action_is_logged_and_raised(tmp_path):
    w = fake_world()
    w.attach_log(ActionLog(tmp_path / "a.jsonl"))
    with pytest.raises(BridgeError):
        w.act("vehicle_buy", depot_tile=5, engine=99)
    acts = [r.raw for r in read_log(tmp_path / "a.jsonl") if r.kind == "act"]
    assert acts[-1]["ok"] is False and acts[-1]["code"] == "ERR_FAKE"


# ------------------------------------------------------------------------------ daemon
@pytest.fixture
def daemon(tmp_path, monkeypatch, bid_task):
    """A Railheadd on the fake engine; ``make()`` boots one more daemon on the same state dir."""
    spec = TaskSpec.load(bid_task)
    spec.limits = {"max_forks": 2, "max_fork_days": 40}
    spec.save(bid_task)
    monkeypatch.setattr(daemon_mod, "task_dir", lambda task_id: bid_task)
    worlds: list[World] = []

    def load_world(self):
        w = fake_world()
        w.fork = lambda workdir, reseed=None: fake_world()
        log_path = self.run_dir / "actions.jsonl"
        recovering = log_path.exists() and log_path.stat().st_size > 0
        w.attach_log(ActionLog(log_path), header=not recovering)
        worlds.append(w)
        return w

    monkeypatch.setattr(daemon_mod.Railheadd, "_load_world", load_world)

    def make():
        return daemon_mod.Railheadd("bid-demo", tmp_path / "state")

    d = make()
    d.make, d.worlds = make, worlds
    return d


def test_attack_rh_do_with_a_timeout_argument_is_refused_before_sending(daemon):
    eng = daemon.world.bridge
    n = len(eng.sent)
    with pytest.raises(ValueError, match="timeout"):
        daemon.handle({"op": "act", "m": "vehicle_buy", "a": {"timeout": 0.001, "depot_tile": 5, "engine": 1}})
    assert not [s for s in eng.sent[n:] if s[0] == "vehicle_buy"]


def test_daemon_sends_canonical_action_arguments(daemon):
    daemon.handle({"op": "act", "m": "order_append", "a": {"vehicle": 1, "tile": "3,2"}})
    method, args, _ = daemon.world.bridge.sent[-1]
    assert method == "order_append" and args == {"vehicle": 1, "tile": 2 * 64 + 3, "flags": []}


def test_world_fault_rebuilds_the_world_from_the_log(daemon):
    first = daemon.world
    first.bridge.apply_then_timeout = {"vehicle_buy"}
    with pytest.raises(daemon_mod.OpError) as e:
        daemon.handle({"op": "act", "m": "vehicle_buy", "a": {"depot_tile": 5, "engine": 1}})
    assert e.value.code == "ERR_INFRA"
    assert daemon.world is not first and daemon.world.bridge.applied == []


def test_attack_restart_does_not_reset_the_fork_budget(daemon):
    daemon.handle({"op": "fork_create", "name": "a"})
    daemon.handle({"op": "fork_wait", "name": "a", "days": 30})
    assert daemon.info()["fork_days_left"] == 10
    again = daemon.make()  # kill -9 and restart
    assert again.info()["fork_days_left"] == 10
    assert again.forks_created == 1
    again.handle({"op": "fork_create", "name": "b"})
    with pytest.raises(daemon_mod.OpError) as e:
        again.handle({"op": "fork_wait", "name": "b", "days": 11})
    assert e.value.code == "ERR_FORK_BUDGET"


@pytest.mark.parametrize("until", [["cash_below:abc"], ["cash_below"], ["new_month:5"], ["no_such_event"],
                                   "vehicle_crashed", [3], ["cash_below:1e3"]])
def test_attack_bad_wake_condition_is_refused_at_the_daemon(daemon, until):
    n = len(daemon.world.bridge.sent)
    with pytest.raises((daemon_mod.OpError, ValueError)):
        daemon.handle({"op": "wait", "days": 5, "until": until})
    assert not [s for s in daemon.world.bridge.sent[n:] if s[0] == "step"]


def test_good_wake_conditions_pass_the_daemon(daemon):
    out = daemon.handle({"op": "wait", "days": 5, "until": ["cash_below:-500", "new_month", "vehicle_crashed"]})
    assert out["reason"] == "elapsed"


def test_daemon_log_is_a_strict_v2_log(daemon, bid_task, engines, tmp_path):
    daemon.handle({"op": "meta", "m": "promise", "a": {"quantity": 10}})
    daemon.handle({"op": "act", "m": "vehicle_buy", "a": {"depot_tile": 5, "engine": 1}})
    daemon.handle({"op": "wait", "days": 40})
    log = daemon.run_dir / "actions.jsonl"
    assert _records(log)[0]["v"] == 2
    out = _verify(bid_task, log, tmp_path, min_version=2)
    assert out["valid"] == 1 and out["rejected"] == 0 and out["bid"] == 10.0
