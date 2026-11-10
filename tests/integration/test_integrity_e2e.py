"""Reward-integrity attacks end to end: a real bid task, a real engine, the real GameScript and verifier.

The honest log replays the task's oracle on the sealed start state with a log attached, but promises
half of what the oracle promised (a cautious agent), then waits past the bid deadline, as an agent
through the daemon does. A hindsight promise of the true delivery would raise its reward. Each attack
edits a copy of that log the way a root agent can, and the verifier must refuse it.
"""

import json
from pathlib import Path

import pytest

from railhead.actionlog import ActionLog, read_log
from railhead.bridge import BridgeError
from railhead.replay import replay
from railhead.verify import verify
from railhead.world import World

pytestmark = pytest.mark.integration

TASK = Path(__file__).resolve().parents[2] / "tasks" / "deliver_bid-dev-1a1af5"


@pytest.fixture(scope="module")
def honest(tmp_path_factory):
    if not (TASK / "oracle.jsonl").exists():
        pytest.skip(f"{TASK.name} is not in the catalog")
    tmp = tmp_path_factory.mktemp("bid")
    log_path = tmp / "actions.jsonl"
    log = ActionLog(log_path)
    with World.load(tmp / "live", TASK / "start.sav") as w:
        w.attach_log(log)
        for rec in read_log(TASK / "oracle.jsonl"):
            if rec.kind == "meta":
                a = dict(rec.raw.get("a") or {})
                if rec.raw["m"] == "promise":
                    a["quantity"] //= 2
                w.note(rec.raw["m"], **a)
            elif rec.kind == "act":
                try:
                    w.act(rec.raw["m"], **(rec.raw.get("a") or {}))
                except BridgeError:
                    pass
        w.wait(days=40)  # past the 30-day bid deadline: the agent now sees how deliveries go
        w.wait(days=20)
    log.close()
    out = verify(TASK, TASK / "start.sav", log_path, tmp / "v", min_version=2)
    return log_path, out


def _forge(honest_log: Path, tmp_path: Path, edit) -> Path:
    recs = [r.raw for r in read_log(honest_log)]
    recs = edit(recs)
    path = tmp_path / "forged.jsonl"
    path.write_text("".join(json.dumps(r, sort_keys=True) + "\n" for r in recs))
    return path


def _verify(log: Path, tmp_path: Path) -> dict:
    return verify(TASK, TASK / "start.sav", log, tmp_path / "v", min_version=2)


def test_honest_agent_log_verifies_under_the_strict_rules(honest):
    _, out = honest
    assert out["valid"] == 1 and out["rejected"] == 0 and out["divergences"] == 0
    assert out["waits"] == 2 and out["bid"] > 0 and out["delivered"] > out["bid"]


def test_attack_hindsight_promise_at_tick_zero_is_rejected(honest, tmp_path):
    log, out = honest

    def edit(recs):
        return recs + [{"kind": "meta", "seq": len(recs), "ticks": 0, "m": "promise",
                        "a": {"quantity": out["delivered"]}}]

    forged = _verify(_forge(log, tmp_path, edit), tmp_path)
    assert forged["rejected"] == 1 and forged["reward"] == 0.0


def test_attack_hindsight_promise_inserted_before_the_waits_is_rejected(honest, tmp_path):
    """Seq renumbered and tick correct: only the GameScript meta digest in the wait checksum catches it."""
    log, out = honest

    def edit(recs):
        first_wait = next(i for i, r in enumerate(recs) if r["kind"] == "wait")
        forged = {"kind": "meta", "ticks": recs[first_wait]["ticks"], "m": "promise",
                  "a": {"quantity": out["delivered"]}}
        recs = recs[:first_wait] + [forged] + recs[first_wait:]
        for i, r in enumerate(recs[1:], 1):
            r["seq"] = i
        return recs

    forged = _verify(_forge(log, tmp_path, edit), tmp_path)
    assert forged["rejected"] == 1 and forged["valid"] == 1 and forged["reward"] == 0.0


def test_attack_edit_one_checksum_is_rejected_not_dropped(honest, tmp_path):
    log, _ = honest

    def edit(recs):
        w = next(r for r in recs if r["kind"] == "wait")
        w["checksum"]["h"] += 1
        return recs

    forged = _verify(_forge(log, tmp_path, edit), tmp_path)
    assert forged["valid"] == 1 and forged["rejected"] == 1 and forged["reward"] == 0.0


def test_attack_strip_every_checksum_is_rejected(honest, tmp_path):
    log, _ = honest

    def edit(recs):
        for r in recs:
            if r["kind"] == "wait":
                r.pop("checksum")
        return recs

    forged = _verify(_forge(log, tmp_path, edit), tmp_path)
    assert forged["rejected"] == 1 and forged["reward"] == 0.0


# ---------------------------------------------------------------------- the bridge and the GameScript
def test_attack_timeout_argument_does_not_desync_the_live_world(world, tmp_path):
    log = ActionLog(tmp_path / "actions.jsonl")
    world.attach_log(log)
    me = world.state()["company"]
    # Before: `timeout` set the bridge wait to 0 s, the call raised TimeoutError (unlogged) and the
    # GameScript still renamed the company. Now `timeout` reaches the GameScript as an argument, which
    # refuses it; the refusal is logged and the live world equals the log.
    with pytest.raises(BridgeError):
        world.act("company_rename", timeout=0, name="Timeout Probe Co")
    world.wait(days=2)
    log.close()
    acts = [r.raw for r in read_log(tmp_path / "actions.jsonl") if r.kind == "act"]
    assert [(a["m"], a["ok"], a["code"]) for a in acts] == [("company_rename", False, "ERR_BAD_ARG")]
    assert next(c for c in world.call("companies") if c["id"] == me)["name"] != "Timeout Probe Co"


@pytest.mark.parametrize("until", [["cash_below:abc"], ["cash_below"], ["new_month:3"], ["cash_below:12x"]])
def test_attack_bad_wake_condition_does_not_break_the_gamescript(world, until):
    t0 = world.state()["ticks"]
    with pytest.raises(BridgeError) as e:
        world.step(days=1, until=until)
    assert e.value.code == "ERR_BAD_ARG"
    st = world.state()
    assert not st["stepping"] and st["ticks"] == t0
    assert world.step(days=1)["reason"] == "elapsed"  # not ERR_BUSY, and the script is alive


def test_meta_digest_changes_the_checksum_only_when_sent(world):
    h0 = world.call("checksum")["h"]
    assert world.call("checksum")["h"] == h0
    world.call("note", d=12345)
    h1 = world.call("checksum")["h"]
    assert h1 != h0
    with pytest.raises(BridgeError):
        world.call("note", d="x")


def test_v1_log_replays_without_meta_digests(tmp_path):
    """A v1 oracle log (meta records passive) still replays with the same final checksum as before."""
    oracle = TASK / "oracle.jsonl"
    if not oracle.exists():
        pytest.skip(f"{TASK.name} is not in the catalog")
    assert next(read_log(oracle)).raw["v"] == 1
    res = replay(TASK / "start.sav", oracle, tmp_path / "r", min_version=1, horizon_days=300)
    assert res.valid and res.acts > 0
