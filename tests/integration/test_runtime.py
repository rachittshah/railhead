"""Runtime mechanisms: event-driven waits, forks (exact and reseeded), action log + replay."""

import pytest

from railhead.world import World

pytestmark = pytest.mark.integration


# ------------------------------------------------------------------ event-driven time
def test_wait_until_condition_returns_early_with_reason(world):
    money = world.call("companies")[0]["money"]
    out = world.step(days=200, until=[f"cash_below:{money + 1}"])
    assert out["reason"] == "cash_below"
    assert out["paused"] is True
    assert out["days"] < 200


def test_wait_without_matching_event_runs_full_length(world):
    d0 = world.state()["date"]
    out = world.step(days=30, until=["company_bankrupt"])
    assert out["reason"] == "elapsed"
    assert out["date"] == d0 + 30


def test_unknown_until_kind_is_rejected(world):
    from railhead.bridge import BridgeError

    with pytest.raises(BridgeError) as ei:
        world.step(days=5, until=["no_such_event"])
    assert ei.value.code == "ERR_BAD_ARG"


# ------------------------------------------------------------------ forks
def test_exact_fork_evolves_like_its_loaded_twin(world, tmp_path):
    a = world.fork(tmp_path / "fa")
    b = world.fork(tmp_path / "fb")
    try:
        a.step(days=150)
        b.step(days=150)
        assert a.call("checksum") == b.call("checksum")
    finally:
        a.close()
        b.close()


def test_exact_fork_matches_the_parent_that_kept_running(world, tmp_path):
    child = world.fork(tmp_path / "fc")
    try:
        world.step(days=150)
        child.step(days=150)
        assert child.call("checksum") == world.call("checksum")
    finally:
        child.close()


def test_reseeded_fork_samples_a_different_future(world, tmp_path):
    a = world.fork(tmp_path / "ra", reseed=1)
    b = world.fork(tmp_path / "rb", reseed=2)
    try:
        a.step(days=400)
        b.step(days=400)
        assert a.call("checksum")["h"] != b.call("checksum")["h"]
    finally:
        a.close()
        b.close()


# ------------------------------------------------------------------ action log + replay
def test_action_log_records_waits_and_replay_reproduces_state(world, tmp_path):
    from railhead.actionlog import ActionLog
    from railhead.replay import replay

    start = world.save(tmp_path / "start.sav")
    log = ActionLog(tmp_path / "actions.jsonl")
    world.attach_log(log)
    world.wait(days=20)
    world.wait(days=45, until=["company_bankrupt"])
    live = world.call("checksum")
    log.close()

    result = replay(start, tmp_path / "actions.jsonl", tmp_path / "replay")
    assert result.valid
    assert result.final_checksum == live
    assert result.divergences == []
