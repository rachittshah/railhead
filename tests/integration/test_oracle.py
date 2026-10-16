"""The scripted oracle plays through player actions only, and its log replays to a credited delivery."""

import json

import pytest

from railhead.actionlog import ActionLog
from railhead.gen.families import pick_deliver_pair
from railhead.oracle.road import RoadOracle
from railhead.task import TaskSpec
from railhead.verify import verify
from railhead.world import World
from tests.integration.conftest import make_config

pytestmark = pytest.mark.integration


def test_road_oracle_delivers_and_replay_verifier_credits_it(tmp_path):
    tdir = tmp_path / "task"
    tdir.mkdir()
    with World.create(tmp_path / "gen", make_config(seed=3)) as w:
        w.step(days=31)
        pair = pick_deliver_pair(w)
        assert pair is not None
        src, dst, cargo = pair
        mid = w.call("monitor_add", kind="industry_delivery", cargo=cargo, target=dst["id"])
        start = w.save(tdir / "start.sav")
    spec = TaskSpec(task_id="o-demo", family="deliver", horizon_days=240, runoff_days=0,
                    objective={"cargo": cargo, "source": src["id"], "dest": dst["id"], "quantity": 1,
                               "monitor": mid, "feasible": True})
    (tdir / "task.json").write_text(json.dumps(spec.to_json()))

    log_path = tmp_path / "oracle.jsonl"
    with World.load(tmp_path / "play", start) as w:
        w.attach_log(ActionLog(log_path))
        net = RoadOracle(w).deliver(src, dst, cargo)
        assert net.vehicles
        w.wait(days=240)
        live = w.call("monitor_read")[0]["total"]
    assert live > 0, "trucks delivered nothing in 240 days"

    out = verify(tdir, start, log_path, tmp_path / "v")
    assert out["valid"] == 1 and out["divergences"] == 0
    assert out["delivered"] == live
    assert out["reward"] == 1.0
