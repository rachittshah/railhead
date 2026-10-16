"""Proof-of-play verifier: replay the agent's log from the sealed start, then score from the engine ledger."""

import json

import pytest

from railhead.actionlog import ActionLog
from railhead.seal import seal_task
from railhead.task import TaskSpec
from railhead.verify import verify
from railhead.world import World
from tests.integration.conftest import make_config

pytestmark = pytest.mark.integration


@pytest.fixture
def deliver_task(tmp_path):
    """A deliver task whose start state carries a delivery monitor on some accepting industry."""
    tdir = tmp_path / "task"
    tdir.mkdir()
    with World.create(tmp_path / "gen", make_config(seed=31)) as w:
        dest = next(i for i in w.call("industries") if i["accepts"])
        cargo = dest["accepts"][0]
        mid = w.call("monitor_add", kind="industry_delivery", cargo=cargo, target=dest["id"])
        w.save(tdir / "start.sav")
    spec = TaskSpec(task_id="v-demo", family="deliver", horizon_days=60, runoff_days=30,
                    objective={"cargo": cargo, "dest": dest["id"], "quantity": 100, "monitor": mid,
                               "feasible": True})
    (tdir / "task.json").write_text(json.dumps(spec.to_json()))
    seal_task(tdir)
    return tdir


def _log(tmp_path, records):
    path = tmp_path / "actions.jsonl"
    log = ActionLog(path)
    for r in records:
        log.write(r)
    log.close()
    return path


def test_doing_nothing_scores_zero_but_is_valid(deliver_task, tmp_path):
    log = _log(tmp_path, [{"kind": "header", "v": 1, "ticks": 0}])
    out = verify(deliver_task, deliver_task / "start.sav", log, tmp_path / "v")
    assert out["valid"] == 1
    assert out["reward"] == 0.0
    assert out["delivered"] == 0
    assert out["tamper"] == 0


def test_tampered_start_state_scores_zero_and_is_flagged(deliver_task, tmp_path):
    (deliver_task / "start.sav").write_bytes((deliver_task / "start.sav").read_bytes() + b"x")
    log = _log(tmp_path, [{"kind": "header", "v": 1, "ticks": 0}])
    out = verify(deliver_task, deliver_task / "start.sav", log, tmp_path / "v")
    assert out["tamper"] == 1 and out["reward"] == 0.0


def test_non_player_command_in_log_is_rejected(deliver_task, tmp_path):
    log = _log(tmp_path, [
        {"kind": "header", "v": 1, "ticks": 0},
        {"kind": "act", "ticks": 0, "m": "monitor_add", "a": {}, "ok": True},
    ])
    out = verify(deliver_task, deliver_task / "start.sav", log, tmp_path / "v")
    assert out["rejected"] == 1 and out["reward"] == 0.0 and out["valid"] == 1


def test_reward_json_is_flat_numeric(deliver_task, tmp_path):
    log = _log(tmp_path, [{"kind": "header", "v": 1, "ticks": 0}])
    out = verify(deliver_task, deliver_task / "start.sav", log, tmp_path / "v")
    assert all(isinstance(v, (int, float)) and not isinstance(v, bool) for v in out.values())
