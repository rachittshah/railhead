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
