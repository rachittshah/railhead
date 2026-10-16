"""World: boot a server, create + bind the model's company, run fast, save/load deterministically."""

import pytest

from railhead.server import ServerConfig
from railhead.world import World

pytestmark = pytest.mark.integration


def test_world_creates_and_binds_a_company(tmp_path):
    with World.create(tmp_path / "w", ServerConfig(seed=3)) as w:
        st = w.state()
        assert st["company"] >= 0
        assert st["paused"] is True
        companies = w.call("companies")
        assert [c["id"] for c in companies] == [st["company"]]
        assert companies[0]["money"] > 0


def test_world_runs_at_unlimited_speed(tmp_path):
    with World.create(tmp_path / "w", ServerConfig(seed=3)) as w:
        assert w.admin.rcon("gamespeed") == ["Game speed: 0"]


def test_save_then_load_resumes_identical_state(tmp_path):
    with World.create(tmp_path / "a", ServerConfig(seed=5)) as w:
        w.step(days=10)
        save = w.save(tmp_path / "snap.sav")
        before = w.state()
    with World.load(tmp_path / "b", save) as w2:
        after = w2.state()
        assert after["company"] == before["company"]
        assert after["ticks"] == before["ticks"]
        assert after["date"] == before["date"]
        assert after["paused"] is True


def test_loaded_worlds_evolve_identically(tmp_path):
    with World.create(tmp_path / "a", ServerConfig(seed=9)) as w:
        save = w.save(tmp_path / "start.sav")
    results = []
    for name in ("x", "y"):
        with World.load(tmp_path / name, save) as w:
            w.step(days=200)
            results.append(w.call("checksum"))
    assert results[0] == results[1]
