"""`rh` end to end, exactly as a model in a terminal uses it (daemon + CLI + formatters + log)."""

import json

import pytest

from railhead.cli import main as rh
from railhead.daemon import ensure_up, shutdown
from railhead.task import TaskSpec
from railhead.world import World
from tests.integration.conftest import make_config

pytestmark = pytest.mark.integration


@pytest.fixture
def task_env(tmp_path, monkeypatch):
    tasks, state = tmp_path / "tasks", tmp_path / "state"
    tdir = tasks / "cli-demo"
    tdir.mkdir(parents=True)
    with World.create(tmp_path / "gen", make_config(seed=3)) as w:
        w.step(days=31)
        w.save(tdir / "start.sav")
    TaskSpec(task_id="cli-demo", family="tycoon", horizon_days=90, runoff_days=30, title="CLI demo",
             briefing="Make money.", objective={"w_idle": 0, "w_base": 1}).save(tdir)
    monkeypatch.setenv("RAILHEAD_TASKS", str(tasks))
    monkeypatch.setenv("RAILHEAD_STATE", str(state))
    monkeypatch.setenv("RAILHEAD_TASK", "cli-demo")
    ensure_up("cli-demo")
    yield state
    shutdown(state)


def run(capsys, *args):
    code = rh(list(args))
    out = capsys.readouterr()
    return code, out.out, out.err


def test_status_is_a_compact_text_dashboard(task_env, capsys):
    code, out, _ = run(capsys, "status")
    assert code == 0
    assert "days left 90" in out
    assert len(out.encode()) < 4096


def test_map_renders_ascii_with_legend(task_env, capsys):
    code, out, _ = run(capsys, "map", "x=0", "y=0", "w=30", "h=12")
    assert code == 0 and "legend" in out.lower()


def test_build_action_with_xy_tiles_is_logged_and_costs_money(task_env, capsys):
    code, out, _ = run(capsys, "industries", "--json")
    ind = json.loads(out)[0]
    x, y = ind["x"], ind["y"]
    # find a buildable flat tile near the industry and drag a short road there (dry run first)
    code, out, _ = run(capsys, "tiles", f"x={max(1, x - 8)}", f"y={max(1, y - 8)}", "w=16", "h=16", "--json")
    rows = json.loads(out)
    spot = next((r["x"] + i, r["y"]) for r in rows for i, k in enumerate(r["kind"])
                if k == "." and r["slope"][i] == "0" and r["build"][i] == "1"
                and r["kind"][i + 1: i + 4] == "..." and r["slope"][i + 1: i + 4] == "000")
    a, b = f"{spot[0]},{spot[1]}", f"{spot[0] + 3},{spot[1]}"
    code, out, err = run(capsys, "road", "line", f"from_tile={a}", f"to_tile={b}", "dry_run=true", "--json")
    assert code == 0, err
    assert json.loads(out)["cost"] > 0
    code, out, err = run(capsys, "road", "line", f"from_tile={a}", f"to_tile={b}", "--json")
    assert code == 0, err
    code, out, _ = run(capsys, "history", "--json")
    acts = [r for r in json.loads(out) if r["kind"] == "act"]
    assert acts and acts[-1]["m"] == "road_line" and acts[-1]["ok"] is True


def test_wait_advances_and_errors_are_typed(task_env, capsys):
    code, out, _ = run(capsys, "wait", "days=10", "--json")
    assert code == 0 and json.loads(out)["days"] == 10
    code, _, err = run(capsys, "vehicle", "start", "vehicle=99999")
    assert code == 1 and err.startswith("ERR_")
