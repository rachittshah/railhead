"""Red-team attacks on the hardened verifier (docs/VERIFICATION.md §5), on the fake engine of test_integrity.

Threat model M1 (log tamperer): the agent can read and rewrite the log and the state files under
``run/`` before verification and can call ``rh`` in any way, but it cannot run its own engine and cannot
touch the verifier process. Each ``test_attack_*`` asserts the secure outcome. A test marked
``xfail(strict=True)`` is an open hole whose fix needs a design decision (the reason names it).
"""

# ruff: noqa: F811 - fixtures imported from test_integrity are redefined as parameters
from __future__ import annotations

import json
import math
import os

import pytest

from railhead import daemon as daemon_mod
from railhead import verify as verify_mod
from railhead.actionlog import ActionLog
from railhead.task import TaskSpec
from tests.unit.test_integrity import (  # noqa: F401 - fixtures are used by name
    T0,
    _records,
    _verify,
    _write,
    bid_task,
    engines,
    fake_world,
    honest_log,
)


def _cli(task, log, out_dir, *flags):
    """The Harbor path: ``python -m railhead.verify`` (main), which turns any exception into error=1."""
    verify_mod.main([str(task), str(task / "start.sav"), str(log), str(out_dir), *flags])
    return json.loads((out_dir / "reward.json").read_text())


def _play(path, steps):
    """A daemon-style v2 log written by the real World on the fake engine."""
    w = fake_world()
    log = ActionLog(path)
    w.attach_log(log)
    for kind, *args in steps:
        if kind == "act":
            w.act("vehicle_buy", depot_tile=5, engine=1)
        elif kind == "wait":
            w.wait(days=args[0])
        else:
            w.note(kind, **args[0])
    log.close()
    return path


@pytest.fixture
def real_daemon(tmp_path, monkeypatch, bid_task, engines):
    """Railheadd with its real boot and recovery (replay of run/actions.jsonl) on fake engines."""
    monkeypatch.setattr(daemon_mod, "task_dir", lambda task_id: bid_task)

    def make():
        return daemon_mod.Railheadd("bid-demo", tmp_path / "state")

    return make


# ------------------------------------------------------------------ R1: log content -> dropout (valid=0)
def test_attack_deeply_nested_record_is_rejected_not_dropped(bid_task, engines, tmp_path):
    """One record nested 100 000 deep: json.loads raises RecursionError, which is not a ValueError."""
    log = honest_log(tmp_path / "a.jsonl")
    deep = '{"kind":"flag","x":' + "[" * 100_000 + "]" * 100_000 + "}\n"
    log.write_text(log.read_text() + deep)
    out = _cli(bid_task, log, tmp_path / "out")
    assert out["valid"] == 1 and out["rejected"] == 1 and out["reward"] == 0.0


def test_attack_log_replaced_by_a_directory_is_rejected_not_dropped(bid_task, engines, tmp_path):
    """`test -s` is true for a directory, so test.sh passes it on; copyfile then raises an OSError."""
    d = tmp_path / "actions.jsonl"
    d.mkdir()
    (d / "x").write_text("x")
    out = _cli(bid_task, d, tmp_path / "out")
    assert out["valid"] == 1 and out["rejected"] == 1 and out["reward"] == 0.0


# ------------------------------------------------------------------ R2: meta arguments the replay does not check
def test_attack_huge_promise_through_rh_is_refused_by_the_daemon(real_daemon):
    """`rh promise quantity=1000...0` (400 digits) is an int, so the daemon logged it; float() of it in the
    verifier raised OverflowError, an internal error: valid=0, the game is dropped."""
    d = real_daemon()
    with pytest.raises(daemon_mod.OpError):
        d.handle({"op": "meta", "m": "promise", "a": {"quantity": 10 ** 400}})


def test_attack_huge_promise_in_the_log_is_not_a_dropout(bid_task, engines, tmp_path):
    log = _play(tmp_path / "a.jsonl", [("promise", {"quantity": 10 ** 400}), ("act",), ("wait", 50)])
    out = _cli(bid_task, log, tmp_path / "out")
    assert out["valid"] == 1 and out["rejected"] == 1 and out["reward"] == 0.0


@pytest.mark.parametrize("a", [{"quantity": math.nan}, {"quantity": 12.5}, {"quantity": True}, {"quantity": "100"},
                               {"quantity": 10, "pad": 1}, {}])
def test_attack_promise_the_daemon_cannot_write_is_rejected(bid_task, engines, tmp_path, a):
    """The agent buys, then appends a promise after its last act and before any wait (so no checksum binds
    it). With quantity NaN, rewards.bid paid as if the agent had bid its exact delivery: 0.5 + D/(2*scale)."""
    log = _play(tmp_path / "a.jsonl", [("act",)])
    recs = _records(log)
    recs.append({"kind": "meta", "seq": len(recs), "ticks": T0, "m": "promise", "a": a})
    out = _verify(bid_task, _write(log, recs), tmp_path)
    assert out["rejected"] == 1 and out["reward"] == 0.0


def test_honest_v1_submit_without_confirm_still_verifies(bid_task, engines, tmp_path):
    """1,379 tracked oracle logs have `submit` with no arguments: the replay must not require confirm."""
    log = _write(tmp_path / "o.jsonl", [{"kind": "header", "v": 1, "ticks": 0},
                                        {"kind": "meta", "seq": 0, "ticks": 0, "m": "submit", "a": {}}])
    out = _verify(bid_task, log, tmp_path)
    assert out["rejected"] == 0 and out["submitted"] == 1


# ------------------------------------------------------------------ R3: run-dir files the verifier step trusts
@pytest.fixture
def no_signals(monkeypatch):
    """Record os.kill instead of sending signals (an unfixed daemon would SIGKILL pytest)."""
    sent: list[tuple[int, int]] = []

    def kill(pid, sig):
        if sig != 0:
            sent.append((pid, sig))

    monkeypatch.setattr(daemon_mod.os, "kill", kill)
    clock = iter(range(10 ** 6))
    monkeypatch.setattr(daemon_mod.time, "monotonic", lambda: float(next(clock)))
    monkeypatch.setattr(daemon_mod.time, "sleep", lambda s: None)
    return sent


def test_attack_children_file_cannot_make_railhead_down_kill_the_verifier(tmp_path, no_signals):
    """test.sh runs `railhead down`, which SIGKILLs every pid in run-dir children.json. The agent lists
    test.sh (here: this process and its parent): test.sh dies and writes no reward file."""
    state = tmp_path / "state"
    state.mkdir()
    (state / "children.json").write_text(json.dumps([os.getpid(), os.getppid(), 1]))
    daemon_mod._kill_stale_children(state)
    assert no_signals == []


def test_attack_daemon_pid_file_cannot_make_railhead_down_kill_the_verifier(tmp_path, no_signals, monkeypatch):
    """`railhead down` SIGKILLs the pid in daemon.pid if it is still alive after 15 s."""
    state = tmp_path / "state"
    state.mkdir()
    (state / "daemon.pid").write_text(str(os.getppid()))
    monkeypatch.setattr(daemon_mod, "Client", lambda *a, **k: (_ for _ in ()).throw(OSError("no socket")))
    daemon_mod.shutdown(state)
    assert no_signals == []


@pytest.mark.parametrize("name", ["up.lock", "daemon.pid", "children.json"])
def test_attack_fifo_in_the_state_dir_cannot_hang_railhead_down(tmp_path, monkeypatch, name):
    """`mkfifo /var/lib/railhead/up.lock` (no root, no process left behind): `railhead down` in test.sh
    opens it and blocks for ever, the verifier never starts, and Harbor's time-out leaves no reward file."""
    import threading

    state = tmp_path / "state"
    state.mkdir()
    os.mkfifo(state / name)
    monkeypatch.setattr(daemon_mod, "Client", lambda *a, **k: (_ for _ in ()).throw(OSError("no socket")))
    t = threading.Thread(target=daemon_mod.shutdown, args=(state,), daemon=True)
    t.start()
    t.join(timeout=10)
    hung = t.is_alive()
    if hung:  # release the blocked open so the thread can end
        fd = os.open(state / name, os.O_RDWR | os.O_NONBLOCK)
        os.close(fd)
        t.join(timeout=5)
    assert not hung


def test_attack_symlinked_lock_cannot_make_railhead_down_truncate_a_file(tmp_path, monkeypatch):
    """`ln -s <a verifier module> /var/lib/railhead/up.lock`: test.sh checks the hashes, then `railhead down`
    opens up.lock for writing, which truncates the module. The verifier then fails: no reward (a dropout)."""
    state = tmp_path / "state"
    state.mkdir()
    victim = tmp_path / "verify.py"
    victim.write_text("print('trusted code')\n")
    (state / "up.lock").symlink_to(victim)
    monkeypatch.setattr(daemon_mod, "Client", lambda *a, **k: (_ for _ in ()).throw(OSError("no socket")))
    daemon_mod.shutdown(state)
    assert victim.read_text() == "print('trusted code')\n"


@pytest.mark.xfail(strict=True, reason="decision: the fork budget is a run-dir file, not part of the log. "
                   "Deleting run/forks.json and sending {'op':'shutdown'} on the socket (no kill needed) resets "
                   "it. Fix: log fork use as meta records (digested in v2) and rebuild the budget from the log.")
def test_attack_deleting_the_fork_budget_file_does_not_reset_it(real_daemon):
    d = real_daemon()
    d.fork_days_used = 30  # what fork_wait charges (test_attack_restart_does_not_reset_the_fork_budget)
    d._save_budget()
    d.close()
    (d.run_dir / "forks.json").unlink()
    again = real_daemon()
    assert again.fork_days_used == 30


# ------------------------------------------------------------------ R4: hindsight without a private engine
@pytest.mark.xfail(strict=True, reason="decision: nothing outside the container binds the log length. The "
                   "agent cuts its log (here: to the header, or test.sh's fallback when the log is deleted) after "
                   "it saw a bad result. Bid family: no promise pays 0.5, more than a failed promise. Fix: commit "
                   "the log head outside the container, or query the live daemon for its head hash in test.sh "
                   "before `railhead down`.")
def test_attack_cutting_the_log_before_a_failed_promise_does_not_pay(bid_task, engines, tmp_path):
    log = _play(tmp_path / "a.jsonl", [("promise", {"quantity": 150}), ("wait", 50)])
    honest = _verify(bid_task, log, tmp_path / "h")
    assert honest["rejected"] == 0 and honest["reward"] == 0.0  # promised 150, delivered 0
    cut = _write(tmp_path / "cut.jsonl", _records(log)[:1])
    assert _verify(bid_task, cut, tmp_path / "c")["reward"] <= honest["reward"]


@pytest.mark.xfail(strict=True, reason="decision: the daemon recovers by replay of whatever run/actions.jsonl "
                   "holds. The agent waits, sees the result, shuts the daemon down through the socket, cuts the "
                   "log and runs `rh status`: the world is back at the cut. This is the private-engine attack of "
                   "section 6 with the task's own daemon. Fix: a head hash kept where the agent cannot write "
                   "(separate user or outside the container).")
def test_attack_rewind_by_cutting_the_log_and_restarting_the_daemon(real_daemon):
    d = real_daemon()
    d.handle({"op": "act", "m": "vehicle_buy", "a": {"depot_tile": 5, "engine": 1}})
    d.handle({"op": "wait", "days": 60})  # the agent sees 60 days of the true future
    seen = d.world.state()["ticks"]
    d.close()
    log = d.run_dir / "actions.jsonl"
    log.write_text(log.read_text().splitlines(keepends=True)[0])  # back to the header
    again = real_daemon()
    assert again.world.state()["ticks"] == seen  # the secure daemon does not go back in time


@pytest.mark.xfail(strict=True, reason="decision: abstain has no deadline and the daemon accepts meta records "
                   "after the horizon and after submit. On an infeasible contract the agent can try, fail, and "
                   "abstain on the last day for reward 1. Fix: give abstain the bid deadline (or refuse meta after "
                   "the horizon), then regenerate the 183 abstain oracles that use it.")
def test_attack_abstain_after_the_horizon_does_not_pay(real_daemon, bid_task):
    spec = TaskSpec.load(bid_task)
    spec.secret = {"feasible": False}
    spec.save(bid_task)
    d = real_daemon()
    d.handle({"op": "wait", "days": 100})
    d.handle({"op": "meta", "m": "submit", "a": {"confirm": "yes"}})
    try:
        d.handle({"op": "meta", "m": "abstain", "a": {"reason": "too late"}})
    except daemon_mod.OpError:
        pass
    out = _verify(bid_task, d.run_dir / "actions.jsonl", d.run_dir.parent / "v", min_version=2)
    assert out["abstained"] == 0


# ------------------------------------------------------------------ R5: verifier cost
@pytest.mark.xfail(strict=True, reason="decision: no size bound on the log. A Harbor verifier time-out writes no "
                   "reward file (a dropout). Honest logs reach 186 KB (11 Arena 2 games); a cap of a few MB, or a "
                   "time budget that ends in rejected=1, needs an owner decision.")
def test_attack_oversized_log_is_rejected_before_replay(bid_task, engines, tmp_path):
    log = honest_log(tmp_path / "a.jsonl")
    recs = _records(log)
    tick = recs[-1]["end_ticks"]
    extra = [{"kind": "meta", "seq": len(recs) + i, "ticks": tick, "m": "flag", "a": {"note": "x" * 100}}
             for i in range(120_000)]  # about 17 MB
    _write(log, recs + extra)
    out = _verify(bid_task, log, tmp_path)
    assert out["rejected"] == 1 and len(engines.list[0].sent) < 1000


def test_attack_credit_failure_does_not_drop_the_reward(bid_task, engines, tmp_path, monkeypatch):
    """With RAILHEAD_CREDIT=1 the verifier replays the log once per turn, and only then writes reward.json.
    Many short turns make that exceed the Harbor time-out (no reward file); any failure in it gave error=1."""
    log = honest_log(tmp_path / "a.jsonl")
    plain = _cli(bid_task, log, tmp_path / "plain")

    def boom(*a, **k):
        raise TimeoutError("credit ran out of time")

    monkeypatch.setattr(verify_mod, "turn_credit", boom)
    out = _cli(bid_task, log, tmp_path / "out", "--credit")
    assert out["valid"] == 1 and out["reward"] == plain["reward"]


# ------------------------------------------------------------------ honest side
def test_honest_daemon_killed_mid_write_recovers(real_daemon):
    """kill -9 during a log write leaves a torn last line. The daemon must still boot (it replays the log),
    and the record it was writing did not happen."""
    d = real_daemon()
    d.handle({"op": "act", "m": "vehicle_buy", "a": {"depot_tile": 5, "engine": 1}})
    d.handle({"op": "wait", "days": 10})
    d.close()
    log = d.run_dir / "actions.jsonl"
    log.write_text(log.read_text() + '{"kind":"act","m":"vehicle_bu')
    again = real_daemon()
    again.handle({"op": "wait", "days": 10})
    again.close()
    out = _verify(again.tdir, log, log.parent / "v", min_version=2)
    assert out["rejected"] == 0 and out["valid"] == 1 and out["waits"] == 2


def test_turn_credit_stops_at_its_deadline(bid_task, engines, tmp_path):
    """The credit step must end inside the Harbor verifier time-out whatever the number of turns."""
    log = honest_log(tmp_path / "a.jsonl")
    spec = TaskSpec.load(bid_task)
    assert len(verify_mod.turn_credit(spec, bid_task / "start.sav", log, tmp_path, 0.5)) == 1
    assert verify_mod.turn_credit(spec, bid_task / "start.sav", log, tmp_path, 0.5, deadline=0.0) == []
