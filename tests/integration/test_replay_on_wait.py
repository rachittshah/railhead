"""replay(on_wait=...): one callback per replayed wait, at the wait's end tick.

Generated oracle logs hold only acts and metas (the verifier runs the horizon itself), so the test
re-plays a small tycoon oracle on its sealed start save with a log attached and then adds logged
waits. The result is a real log with checksummed waits that starts from the oracle's actions.
"""

from pathlib import Path

import pytest

from railhead.actionlog import ActionLog, read_log
from railhead.bridge import BridgeError
from railhead.replay import replay
from railhead.world import World

pytestmark = pytest.mark.integration

TASK = Path(__file__).resolve().parents[2] / "tasks" / "tycoon-train-0155"


@pytest.fixture
def log_with_waits(tmp_path):
    if not (TASK / "oracle.jsonl").exists():
        pytest.skip(f"{TASK.name} is not in the catalog")
    log_path = tmp_path / "actions.jsonl"
    log = ActionLog(log_path)
    with World.load(tmp_path / "live", TASK / "start.sav") as w:
        w.attach_log(log)
        for rec in read_log(TASK / "oracle.jsonl"):
            if rec.kind == "act":
                try:
                    w.act(rec.raw["m"], **(rec.raw.get("a") or {}))
                except BridgeError:
                    pass
        w.wait(days=20)
        w.wait(days=45, until=["company_bankrupt"])
        w.wait(ticks=500)
    log.close()
    return log_path


def waits(log_path):
    return [r.raw for r in read_log(log_path) if r.kind == "wait"]


def test_on_wait_runs_once_per_wait_at_its_end_tick(log_with_waits, tmp_path):
    calls = []

    def on_wait(world, r):
        calls.append((world.state()["ticks"], r["seq"]))

    result = replay(TASK / "start.sav", log_with_waits, tmp_path / "replay", on_wait=on_wait)
    ws = waits(log_with_waits)
    assert len(ws) == 3
    assert calls == [(r["end_ticks"], r["seq"]) for r in ws]
    assert result.waits == len(ws)
    assert result.valid and result.divergences == []


def test_on_wait_skips_a_wait_that_max_ticks_truncates(log_with_waits, tmp_path):
    ws = waits(log_with_waits)
    last = ws[-1]
    calls = []
    replay(TASK / "start.sav", log_with_waits, tmp_path / "replay", max_ticks=last["end_ticks"] - 100,
           on_wait=lambda world, r: calls.append((world.state()["ticks"], r["seq"])))
    assert calls == [(r["end_ticks"], r["seq"]) for r in ws[:-1]]


def test_replay_without_on_wait_is_unchanged(log_with_waits, tmp_path):
    result = replay(TASK / "start.sav", log_with_waits, tmp_path / "replay")
    assert result.valid and result.waits == 3
