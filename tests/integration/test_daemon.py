"""railheadd: owns the world for one task; the agent reaches it only through a unix socket."""

import json
import os
import signal

import pytest

from railhead.client import Client, DaemonError
from railhead.daemon import ensure_up, shutdown
from railhead.task import TaskSpec
from railhead.world import World
from tests.integration.conftest import make_config

pytestmark = pytest.mark.integration


@pytest.fixture
def env(tmp_path, monkeypatch):
    tasks, state = tmp_path / "tasks", tmp_path / "state"
    tdir = tasks / "t-demo"
    tdir.mkdir(parents=True)
    with World.create(tmp_path / "gen", make_config(seed=21)) as w:
        w.save(tdir / "start.sav")
    spec = TaskSpec(task_id="t-demo", family="tycoon", horizon_days=120, runoff_days=30,
                    limits={"max_forks": 1, "max_fork_days": 100}, objective={}, scenario={"seed": 21})
    (tdir / "task.json").write_text(json.dumps(spec.to_json()))
    monkeypatch.setenv("RAILHEAD_TASKS", str(tasks))
    monkeypatch.setenv("RAILHEAD_STATE", str(state))
    yield state
    shutdown(state)


def test_up_is_idempotent_and_serves_status(env):
    pid1 = ensure_up("t-demo")
    pid2 = ensure_up("t-demo")
    assert pid1 == pid2
    info = Client(env).request("info")
    assert info["task_id"] == "t-demo"
    assert info["days_left"] == 120


def test_actions_outside_the_whitelist_are_refused(env):
    ensure_up("t-demo")
    with pytest.raises(DaemonError) as ei:
        Client(env).request("act", m="monitor_add", a={})
    assert ei.value.code == "ERR_NOT_A_PLAYER_ACTION"


def test_wait_is_capped_at_the_horizon(env):
    ensure_up("t-demo")
    c = Client(env)
    out = c.request("wait", days=500)
    assert out["days"] == 120
    with pytest.raises(DaemonError) as ei:
        c.request("wait", days=1)
    assert ei.value.code == "ERR_HORIZON"


def test_daemon_recovers_exact_state_after_being_killed(env):
    pid = ensure_up("t-demo")
    c = Client(env)
    c.request("wait", days=33)
    before = c.request("query", m="checksum")
    os.kill(pid, signal.SIGKILL)
    ensure_up("t-demo")
    after = Client(env).request("query", m="checksum")
    assert after == before


def test_fork_budget_is_enforced(env):
    ensure_up("t-demo")
    c = Client(env)
    c.request("fork_create", name="f1")
    out = c.request("fork_wait", name="f1", days=60)
    assert out["fork_days_left"] == 40
    with pytest.raises(DaemonError) as ei:
        c.request("fork_wait", name="f1", days=60)
    assert ei.value.code == "ERR_FORK_BUDGET"
    with pytest.raises(DaemonError) as ei:
        c.request("fork_create", name="f2")
    assert ei.value.code == "ERR_FORK_LIMIT"
    c.request("fork_drop", name="f1")
    # the real world did not move
    assert c.request("info")["days_left"] == 120


def test_meta_decisions_reject_unknown_arguments_and_submit_needs_confirmation(env):
    ensure_up("t-demo")
    c = Client(env)
    for m, a in [("submit", {}), ("submit", {"vtype": "road"}), ("flag", {"vtype": "road"}),
                 ("promise", {"qty": 5}), ("forecast", {"bogus": 1})]:
        with pytest.raises(DaemonError) as ei:
            c.request("meta", m=m, a=a)
        assert ei.value.code == "ERR_BAD_ARG", (m, a)
    assert c.request("info")["submitted"] is False
    c.request("meta", m="promise", a={"quantity": 100})
    c.request("meta", m="flag", a={"note": "station rating looks odd"})
    c.request("meta", m="submit", a={"confirm": "yes"})
    assert c.request("info")["submitted"] is True


def test_wait_rejects_unknown_arguments(env):
    ensure_up("t-demo")
    with pytest.raises(DaemonError) as ei:
        Client(env).request("wait", days=5, vtype="road")
    assert ei.value.code == "ERR_BAD_ARG"


def test_concurrent_ensure_up_and_restart_never_run_two_daemons(env):
    import threading

    pids: list[int] = []
    ts = [threading.Thread(target=lambda: pids.append(ensure_up("t-demo"))) for _ in range(4)]
    for t in ts:
        t.start()
    for t in ts:
        t.join()
    assert len(set(pids)) == 1
    c = Client(env)
    c.request("wait", days=3)
    before = c.request("query", m="checksum")
    # restart while other clients race to bring it back up
    shutdown(env)
    pids.clear()
    ts = [threading.Thread(target=lambda: pids.append(ensure_up("t-demo"))) for _ in range(4)]
    for t in ts:
        t.start()
    for t in ts:
        t.join()
    assert len(set(pids)) == 1
    assert Client(env).request("query", m="checksum") == before


def test_shutdown_racing_a_client_restart_leaves_a_healthy_daemon(env):
    import threading
    import time as _t

    ensure_up("t-demo")
    Client(env).request("wait", days=2)
    for _ in range(3):
        stopper = threading.Thread(target=shutdown, args=(env,))
        stopper.start()
        _t.sleep(0.05)
        pid = ensure_up("t-demo")  # a player's rh call arrives mid-shutdown
        stopper.join()
        pid = ensure_up("t-demo")
        assert Client(env).request("query", m="state")["paused"] is True, pid
