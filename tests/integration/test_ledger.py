"""Objective ledger: engine-native cargo monitors accumulated by the GS and persisted in savegames."""

import pytest

from railhead.world import World

pytestmark = pytest.mark.integration


def _first_mine(world):
    return next(i for i in world.call("industries") if i["produces"])


def test_monitor_add_and_read_starts_at_zero(world):
    ind = _first_mine(world)
    cargo = ind["produces"][0]["cargo"]
    mid = world.call("monitor_add", kind="industry_pickup", cargo=cargo, target=ind["id"])
    world.step(days=40)
    rows = world.call("monitor_read")
    assert rows == [{"id": mid, "kind": "industry_pickup", "cargo": cargo, "target": ind["id"],
                     "company": world.company, "total": 0}]


def test_monitor_kind_and_target_are_validated(world):
    from railhead.bridge import BridgeError

    with pytest.raises(BridgeError) as ei:
        world.call("monitor_add", kind="bogus", cargo=0, target=0)
    assert ei.value.code == "ERR_BAD_ARG"


def test_monitors_survive_save_and_load(world, tmp_path):
    ind = _first_mine(world)
    world.call("monitor_add", kind="industry_pickup", cargo=ind["produces"][0]["cargo"], target=ind["id"])
    save = world.save(tmp_path / "m.sav")
    with World.load(tmp_path / "loaded", save) as w2:
        assert len(w2.call("monitor_read")) == 1
