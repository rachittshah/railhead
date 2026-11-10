"""Anchor plumbing end to end, without any download: the bundled RailheadIdle AI plays as the "anchor"."""

import json

import pytest

from railhead import anchors
from railhead.engine import REPO_DIR
from railhead.task import TaskSpec

pytestmark = pytest.mark.integration

TASK = REPO_DIR / "tasks" / "arena-dev-0100"


def test_idle_ai_as_anchor_for_30_days(tmp_path):
    res = anchors.run_anchor(TASK, "RailheadIdle", seed=1, workdir=tmp_path / "w", cache=tmp_path / "cache",
                             horizon_days=30, runoff_days=0, chunk_days=10)
    assert res["status"] == "ok", res
    assert res["ai_company"] is not None and res["ai_company"] != res["model_company"]
    p = res["parity"]
    assert p["model"]["date"] == p["ai"]["date"]
    assert p["model"]["max_loan"] == p["ai"]["max_loan"]
    assert [s["day"] for s in res["samples"]] == [0, 10, 20, 30]
    assert res["mandate"] is res["final"] or res["mandate"] == res["final"]
    assert res["final"]["vehicles"] == 0 and res["final"]["fleet"] == {m: 0 for m in anchors.MODES}
    # The idle AI pays only loan interest: its net worth moves by less than a month of interest on max loan.
    assert -5_000 < res["final"]["net_worth"] <= 0
    assert res["settings"]["script.script_max_opcode_till_suspend"] is not None
    row = anchors.make_row(TaskSpec.load(TASK), res, info={})
    assert row["status"] == "anchor" and row["player"] == "anchor:RailheadIdle"
    json.dumps(row)
